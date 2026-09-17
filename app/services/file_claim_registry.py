"""Workspace-level file-claim registry for multi-Agent conflict detection.

Problem: when two parallel Agents both write ``src/models/user.py`` the
result is whichever write lands last.  Git catches this only at commit
time, which is way too late — by then both Agents have already burned
LLM budget and produced half-working changes.

Solution: a *workspace-scoped claim registry* that the orchestrator and
the runner tool layer both consult before any write lands.  The registry
maps glob patterns to the Agent + WorkUnit that claimed them, and emits
a conflict when two concurrent claims would overlap.

Design rules
------------

1. **Claims are on file *paths*, not on files.**  An Agent that writes
   ``src/api/**`` claims the glob; a second Agent that wants
   ``src/api/v1/missions.py`` must either (a) narrow its claim to just
   that file or (b) wait for the first Agent to finish.

2. **Claims are scoped to the workspace.**  Two missions that touch
   different workspaces never collide — each registry instance is
   workspace-local.

3. **Claims have a lease.**  Every claim expires after a configurable
   ``claim_lease_seconds`` (default 120) unless the owning Agent renews
   it.  Dead Agents auto-release their claims.

4. **Conflict is detected at claim time, not at git time.**  The
   orchestrator calls :meth:`FileClaimRegistry.check_conflicts` *before*
   launching a node; the runner's file tools call it before each write.
   In both cases the call is fast (in-memory dict + glob fnmatch).

5. **Claims are released when the node finishes.**  SUCCEEDED, FAILED,
   or SKIPPED — any terminal state releases the claim.  FAILED claims
   are kept in a short "recently-failed" buffer so the next Agent sees
   the error context.

Usage (orchestrator side)::

    registry = FileClaimRegistry(workspace_id="team-frontend")
    conflicts = registry.check_conflicts(
        node_id="backend",
        agent_id="dev",
        file_claims=("src/api/**", "src/models/*.py"),
    )
    if conflicts:
        # Either ask the user, narrow the claim, or queue the node
        pass

Usage (tool side — inside file_ops)::

    await registry.claim(
        workspace_id="team-frontend",
        owner=f"{agent_id}:{work_unit_id}",
        patterns=("src/models/user.py",),
    )
    # ... do the write ...
    await registry.release(owner=...)

The registry is intentionally in-memory.  For persistence across runner
restarts, a thin :class:`FileClaimRegistry` subclass could serialize to
the existing SessionEvent stream — the lease mechanism already handles
the "reboot mid-run" case.
"""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


# ── Conflict shape ───────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimConflict:
    """Two claims collide on at least one file path.

    ``overlap_patterns`` is the list of *glob pairs* that collided —
    not resolved file paths.  That keeps the conflict reason readable
    even when the actual matching files are not yet known.

    Example::

        ClaimConflict(
            owner_a=("dev","wu-backend-42"),   # glob "src/api/**"
            owner_b=("dev","wu-integration-7"),# glob "src/api/v1/missions.py"
            overlap_patterns=("src/api/**", "src/api/v1/missions.py"),
            suggestion="Narrow one claim to just the file you need",
        )
    """

    owner_a: tuple[str, str]  # (agent_id, work_unit_id)
    owner_b: tuple[str, str]
    overlap_patterns: tuple[str, str]
    suggestion: str = "Narrow one claim or queue the later Agent"


# ── Active claim ──────────────────────────────────────────────────────


@dataclass
class _ActiveClaim:
    agent_id: str
    work_unit_id: str
    patterns: tuple[str, ...]
    claimed_at: float
    lease_until: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return time.monotonic() > self.lease_until

    @property
    def owner_key(self) -> tuple[str, str]:
        return (self.agent_id, self.work_unit_id)


# ── Registry ─────────────────────────────────────────────────────────


class FileClaimRegistry:
    """Workspace-scoped in-memory registry of file-path claims.

    One instance per workspace.  Thread-safe — every public method is
    guarded by an asyncio-friendly lock.
    """

    def __init__(
        self,
        *,
        workspace_id: str,
        claim_lease_seconds: float = 120.0,
        max_recent_failed: int = 8,
    ) -> None:
        self._workspace_id = workspace_id
        self._lease = claim_lease_seconds
        self._max_recent_failed = max_recent_failed
        self._claims: dict[tuple[str, str], _ActiveClaim] = {}
        self._recent_failed: list[tuple[tuple[str, str], tuple[str, ...]]] = []

    # ── Public: claim / renew / release ──────────────────────────────

    async def claim(
        self,
        *,
        agent_id: str,
        work_unit_id: str,
        patterns: Iterable[str],
        allow_same_agent_overlap: bool = True,
    ) -> tuple[bool, list[ClaimConflict]]:
        """Try to acquire claims for ``patterns``.

        Returns ``(ok, conflicts)``.  When ``ok=False`` the caller must
        decide whether to narrow the claim, wait, or ask the user.
        When ``allow_same_agent_overlap=True`` (default), two claims
        from the *same* (agent, work_unit) pair do not collide — an
        Agent writing multiple files in sequence is not a conflict.
        """
        self._evict_expired()
        new_claim = _ActiveClaim(
            agent_id=agent_id,
            work_unit_id=work_unit_id,
            patterns=tuple(sorted(set(patterns))),
            claimed_at=time.monotonic(),
            lease_until=time.monotonic() + self._lease,
        )
        conflicts = self._find_collisions(new_claim, allow_same_agent_overlap)
        if conflicts:
            return False, conflicts
        self._claims[new_claim.owner_key] = new_claim
        return True, []

    async def renew(self, *, agent_id: str, work_unit_id: str) -> bool:
        """Extend an existing claim's lease.  Returns False if no claim."""
        key = (agent_id, work_unit_id)
        claim = self._claims.get(key)
        if claim is None:
            return False
        claim.lease_until = time.monotonic() + self._lease
        return True

    async def release(self, *, agent_id: str, work_unit_id: str, failed: bool = False) -> None:
        """Release claims for this owner.

        When ``failed=True`` the patterns are kept in a short "recently
        failed" buffer so the next Agent can see the failure context.
        """
        key = (agent_id, work_unit_id)
        claim = self._claims.pop(key, None)
        if claim is None:
            return
        if failed:
            self._recent_failed.append((key, claim.patterns))
            if len(self._recent_failed) > self._max_recent_failed:
                self._recent_failed = self._recent_failed[-self._max_recent_failed :]

    # ── Conflict pre-flight (called *before* launch) ────────────────

    async def check_conflicts(
        self,
        *,
        node_id: str,
        agent_id: str,
        work_unit_id: str,
        file_claims: Iterable[str],
    ) -> list[ClaimConflict]:
        """Dry-run: would these patterns collide with existing claims?

        Returns the list of conflicts (empty → safe to claim).  This is
        what the orchestrator calls *before* launching a parallel node.
        """
        self._evict_expired()
        tentative = _ActiveClaim(
            agent_id=agent_id,
            work_unit_id=work_unit_id,
            patterns=tuple(sorted(set(file_claims))),
            claimed_at=time.monotonic(),
            lease_until=time.monotonic() + self._lease,
            metadata={"node_id": node_id},
        )
        return self._find_collisions(tentative, allow_same_agent_overlap=True)

    # ── Inspectors (for dashboard / audit) ──────────────────────────

    def active_claims(self) -> list[dict[str, Any]]:
        self._evict_expired()
        return [
            {
                "agent_id": c.agent_id,
                "work_unit_id": c.work_unit_id,
                "patterns": list(c.patterns),
                "lease_remaining_s": max(0.0, c.lease_until - time.monotonic()),
            }
            for c in self._claims.values()
        ]

    def recent_failures(self) -> list[dict[str, Any]]:
        return [
            {"owner": list(owner), "patterns": list(patterns)}
            for owner, patterns in self._recent_failed
        ]

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    # ── Internals ───────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        expired = [k for k, c in self._claims.items() if c.is_expired]
        for k in expired:
            self._claims.pop(k, None)

    def _find_collisions(
        self,
        new_claim: _ActiveClaim,
        allow_same_agent_overlap: bool,
    ) -> list[ClaimConflict]:
        conflicts: list[ClaimConflict] = []
        for existing in self._claims.values():
            if existing.is_expired:
                continue
            if allow_same_agent_overlap and existing.owner_key == new_claim.owner_key:
                continue
            overlap = _colliding_patterns(new_claim.patterns, existing.patterns)
            if overlap:
                conflicts.append(
                    ClaimConflict(
                        owner_a=new_claim.owner_key,
                        owner_b=existing.owner_key,
                        overlap_patterns=overlap,
                        suggestion=(
                            "Narrow one claim to just the file glob you need, "
                            "or queue the later Agent behind the earlier one."
                        ),
                    )
                )
        return conflicts


# ── Pattern collision detection ───────────────────────────────────────


def _colliding_patterns(
    a_patterns: tuple[str, ...],
    b_patterns: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    """Return every pair ``(a, b)`` where the two globs *could* match
    the same file path.

    Two globs collide when either ``a`` matches ``b`` or ``b`` matches
    ``a`` or they are identical.  We use fnmatch on the *other* pattern
    because globs can contain wildcards on either side — ``"src/**"``
    collides with ``"src/api/v1/missions.py"`` and vice versa.
    """
    collisions: list[tuple[str, str]] = []
    for a in a_patterns:
        for b in b_patterns:
            if a == b:
                collisions.append((a, b))
                continue
            a_wild = any(c in a for c in "*?[")
            b_wild = any(c in b for c in "*?[")
            # No wildcards → exact equality (handled above)
            if not a_wild and not b_wild:
                continue
            if a_wild and fnmatch.fnmatch(b, a):
                collisions.append((a, b))
                continue
            if b_wild and fnmatch.fnmatch(a, b):
                collisions.append((a, b))
    return tuple(collisions)


__all__ = [
    "ClaimConflict",
    "FileClaimRegistry",
]