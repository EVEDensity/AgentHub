"""Rule engine for T1-1 Subscribe/Rule Trigger.

Reads ``.agenthub/rules.yaml`` (optional, opt-in) and evaluates each rule
against incoming messages.  A rule that matches produces a ``RuleHit``
that the caller can persist as a ``rule.triggered`` session event and,
optionally, promote to a ``mission.created`` after the user's explicit
confirmation gate.

Schema (``rules.yaml``) — deliberately small:

```yaml
version: 1
rules:
  - id: auto-deploy-on-push
    description: Deploy when main gets pushed
    trigger:
      kind: keyword          # | regex | mention
      keywords: [deploy, 部署, release]
    target: devops
    action:
      kind: create_mission   # | reply_only
      objective_template: "触发规则 [{rule_id}]：{description}"
      require_confirmation: true   # default true (ADR gate)
```

Design notes (``multi-agent-collaboration.md`` §6 + §11):

- **Confirmation gate is the default.**  Rules are defensive — they ask
  first; only explicit trigger (``require_confirmation: false``, gated
  behind owner approval) goes straight to Mission creation.
- **Rules do not execute.**  They only produce hits.  The caller owns
  persistence, session events, and Mission creation — the engine is
  pure matching.
- **YAML lives next to the project, not the user.**  Each workspace keeps
  its own rules in ``.agenthub/rules.yaml``; there is no global rules
  registry.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RULES_FILE_NAME = "rules.yaml"
RULES_DIR_NAME = ".agenthub"


# ── Rule data model ───────────────────────────────────────────────


@dataclass(frozen=True)
class RuleTrigger:
    kind: str                         # "keyword" | "regex" | "mention"
    keywords: tuple[str, ...] = ()    # keyword kind: any match triggers
    pattern: str | None = None        # regex kind: full pattern
    mention: str | None = None        # mention kind: agent name

    def matches(self, message: str) -> bool:
        msg = message.strip().lower()
        if not msg:
            return False
        if self.kind == "keyword":
            return any(kw.lower() in msg for kw in self.keywords)
        if self.kind == "regex":
            assert self.pattern is not None
            return bool(re.search(self.pattern, message, re.IGNORECASE))
        if self.kind == "mention":
            assert self.mention is not None
            target = self.mention.lower()
            # Match "@agentname" or "agentname" as whole word
            return (
                f"@{target}" in msg
                or bool(re.search(rf"\b{re.escape(target)}\b", msg))
            )
        raise ValueError(f"unknown trigger kind: {self.kind!r}")


@dataclass(frozen=True)
class RuleAction:
    kind: str = "reply_only"                     # "reply_only" | "create_mission"
    target_agent: str | None = None              # which agent to route to
    objective_template: str | None = None        # create_mission: objective
    require_confirmation: bool = True            # always default-true (ADR)


@dataclass(frozen=True)
class AgentRule:
    id: str
    description: str
    trigger: RuleTrigger
    action: RuleAction


@dataclass(frozen=True)
class RuleHit:
    rule: AgentRule
    matched_text: str = ""    # substring that matched (best-effort)


# ── YAML loading ─────────────────────────────────────────────────


_RULE_TRIGGER_KINDS = frozenset({"keyword", "regex", "mention"})
_RULE_ACTION_KINDS = frozenset({"reply_only", "create_mission"})


class RuleSyntaxError(ValueError):
    """Raised when rules.yaml violates the declared schema."""


def rules_file_path(state_dir: Path) -> Path:
    return state_dir / RULES_FILE_NAME


def load_rules(text: str) -> list[AgentRule]:
    """Parse ``.agenthub/rules.yaml`` text into validated :class:`AgentRule` list.

    Returns an empty list when the file is missing or has no rules — the
    opt-in default of the whole feature.
    """
    if not text.strip():
        return []
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuleSyntaxError(f"rules.yaml is not valid YAML: {exc}") from exc

    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise RuleSyntaxError("rules.yaml must be a mapping at the top level")

    version = raw.get("version", 1)
    if version != 1:
        raise RuleSyntaxError(f"unsupported rules.yaml version: {version}")

    raw_rules = raw.get("rules") or []
    if not isinstance(raw_rules, list):
        raise RuleSyntaxError("'rules' must be a list")

    rules: list[AgentRule] = []
    seen_ids: set[str] = set()
    for idx, raw_rule in enumerate(raw_rules):
        try:
            rule = _parse_one_rule(raw_rule)
        except RuleSyntaxError as exc:
            raise RuleSyntaxError(f"rules[{idx}]: {exc}") from exc
        if rule.id in seen_ids:
            raise RuleSyntaxError(f"duplicate rule id: {rule.id!r}")
        seen_ids.add(rule.id)
        rules.append(rule)
    return rules


def _parse_one_rule(raw: Any) -> AgentRule:
    if not isinstance(raw, dict):
        raise RuleSyntaxError("rule must be a mapping")

    rule_id = raw.get("id")
    if not rule_id or not isinstance(rule_id, str):
        raise RuleSyntaxError("'id' is required and must be a non-empty string")

    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise RuleSyntaxError("'description' must be a string")

    trigger = raw.get("trigger")
    if not isinstance(trigger, dict):
        raise RuleSyntaxError("'trigger' is required and must be a mapping")

    rule_trigger = _parse_trigger(trigger)

    action_raw = raw.get("action") or {}
    if not isinstance(action_raw, dict):
        raise RuleSyntaxError("'action' must be a mapping when provided")
    rule_action = _parse_action(action_raw)

    return AgentRule(
        id=rule_id.strip(),
        description=description.strip(),
        trigger=rule_trigger,
        action=rule_action,
    )


def _parse_trigger(raw: dict[str, Any]) -> RuleTrigger:
    kind = raw.get("kind")
    if kind not in _RULE_TRIGGER_KINDS:
        raise RuleSyntaxError(
            f"'trigger.kind' must be one of {sorted(_RULE_TRIGGER_KINDS)}, got {kind!r}"
        )

    if kind == "keyword":
        kws = raw.get("keywords") or []
        if not isinstance(kws, list) or not all(isinstance(k, str) for k in kws):
            raise RuleSyntaxError("'trigger.keywords' must be a list of strings")
        if not kws:
            raise RuleSyntaxError("'trigger.keywords' must be non-empty for keyword kind")
        return RuleTrigger(kind="keyword", keywords=tuple(k.strip() for k in kws if k.strip()))

    if kind == "regex":
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise RuleSyntaxError("'trigger.pattern' is required for regex kind")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleSyntaxError(f"invalid regex pattern: {exc}") from exc
        return RuleTrigger(kind="regex", pattern=pattern)

    # mention
    mention = raw.get("mention")
    if not isinstance(mention, str) or not mention:
        raise RuleSyntaxError("'trigger.mention' is required for mention kind")
    return RuleTrigger(kind="mention", mention=mention.strip().lstrip("@"))


def _parse_action(raw: dict[str, Any]) -> RuleAction:
    kind = raw.get("kind", "reply_only")
    if kind not in _RULE_ACTION_KINDS:
        raise RuleSyntaxError(
            f"'action.kind' must be one of {sorted(_RULE_ACTION_KINDS)}, got {kind!r}"
        )

    target = raw.get("target_agent")
    if target is not None and not isinstance(target, str):
        raise RuleSyntaxError("'action.target_agent' must be a string")

    objective_template = raw.get("objective_template")
    if objective_template is not None and not isinstance(objective_template, str):
        raise RuleSyntaxError("'action.objective_template' must be a string")

    confirm = raw.get("require_confirmation", True)
    if not isinstance(confirm, bool):
        raise RuleSyntaxError("'action.require_confirmation' must be a boolean")

    return RuleAction(
        kind=kind,
        target_agent=target,
        objective_template=objective_template,
        require_confirmation=confirm,
    )


# ── Matching engine ──────────────────────────────────────────────


def evaluate_rules(
    rules: list[AgentRule],
    message: str,
    *,
    limit: int = 5,
) -> list[RuleHit]:
    """Return every rule whose trigger matches ``message`` (ordered by rule id).

    ``limit`` caps hits to avoid a flood when many rules share broad
    keywords; the default 5 matches the architectural preference of
    "specificity over coverage" for the subscription layer.
    """
    if not rules or not message.strip():
        return []
    hits: list[RuleHit] = []
    for rule in rules:
        if rule.trigger.matches(message):
            hits.append(RuleHit(rule=rule))
            if len(hits) >= limit:
                break
    return hits


# ── File-loading + hot-reload cache (T4) ────────────────────────


def load_rules_from_path(path: Path) -> list[AgentRule]:
    """Convenience: read YAML from *path* and parse into rules.

    Returns an empty list when the file does not exist (opt-in default).
    Raises :class:`RuleSyntaxError` when the YAML is malformed or
    violates the declared schema.  Raises :class:`OSError` on IO
    failures other than "file not found".
    """
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    return load_rules(text)


def discover_rules_file(workspace_root: Path | None = None) -> Path | None:
    """Search standard locations for a project ``rules.yaml``.

    Lookup order:
      1. ``<workspace_root>/.agenthub/rules.yaml``  (project-local — preferred)
      2. ``.agenthub/rules.yaml`` relative to cwd     (CLI fallback)

    Returns the first path that exists, or ``None`` if neither is
    present.  The returned path is always a fully-resolved file path;
    callers can safely call :func:`load_rules_from_path` on it.
    """
    candidates: list[Path] = []
    if workspace_root is not None:
        candidates.append(workspace_root / RULES_DIR_NAME / RULES_FILE_NAME)
    candidates.append(Path.cwd() / RULES_DIR_NAME / RULES_FILE_NAME)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    return None


class RulesCache:
    """Hot-reload wrapper around :func:`load_rules_from_path`.

    The cache reads rules on first access and remembers the file's
    ``st_mtime``.  Every subsequent :meth:`get_rules` call checks the
    mtime and re-parses only when the file has changed — this keeps
    the per-request cost at one stat() syscall (well under 1 ms) while
    still letting developers edit ``rules.yaml`` and see changes on
    the next request.

    Thread-safe: a single ``threading.Lock`` protects both the mtime
    check and the reload so concurrent requests never observe a
    half-updated rule list.

    Usage::

        cache = RulesCache(Path(".agenthub/rules.yaml"))
        rules, error = cache.get_rules()
        if error:
            print(f"rules.yaml has errors: {error}")
        for rule in rules:
            print(rule.id)
    """

    def __init__(self, path: Path):
        self._path: Path = path
        self._rules: list[AgentRule] = []
        self._error: str | None = None
        self._mtime: float = 0.0
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def last_error(self) -> str | None:
        """Error from the most recent reload attempt (or None)."""
        return self._error

    def get_rules(self) -> tuple[list[AgentRule], str | None]:
        """Return ``(rules, error)`` — always non-fatal.

        When the file is missing, returns ``[]`` with ``error=None``.
        When the file exists but is malformed, returns the last known
        good rule list (or ``[]`` on first load) and sets ``error`` to
        a human-readable message.  The successful reload clears the
        error automatically.
        """
        with self._lock:
            if not self._path.is_file():
                # File was removed since last load → reset to empty.
                if self._rules or self._mtime:
                    self._rules = []
                    self._mtime = 0.0
                    self._error = None
                return self._rules, self._error

            try:
                current_mtime = self._path.stat().st_mtime
            except OSError as exc:
                self._error = f"cannot stat rules.yaml: {exc}"
                return self._rules, self._error

            if current_mtime == self._mtime:
                return self._rules, self._error

            # File changed → reload.
            try:
                text = self._path.read_text(encoding="utf-8")
                new_rules = load_rules(text)
            except RuleSyntaxError as exc:
                self._error = str(exc)
                # Keep existing rules on parse failure — don't wipe
                # production rules because someone introduced a typo.
                return self._rules, self._error
            except OSError as exc:
                self._error = f"cannot read rules.yaml: {exc}"
                return self._rules, self._error

            self._rules = new_rules
            self._mtime = current_mtime
            self._error = None
            return self._rules, self._error

    def invalidate(self) -> None:
        """Force a full reload on the next :meth:`get_rules` call."""
        with self._lock:
            self._mtime = 0.0


# ── Per-process cache registry ────────────────────────────────────
#
# One ``RulesCache`` per absolute file path, memoised so we don't
# duplicate stat() watches across chat_mission requests.  The dict
# is guarded by its own lock — independent of each cache's internal
# lock — so concurrent lookups are safe.

_cache_registry: dict[Path, RulesCache] = {}
_registry_lock = threading.Lock()


def get_or_create_rules_cache(path: Path) -> RulesCache:
    """Return the shared :class:`RulesCache` for *path* (memoised)."""
    resolved = path.resolve()
    with _registry_lock:
        cache = _cache_registry.get(resolved)
        if cache is None:
            cache = RulesCache(resolved)
            _cache_registry[resolved] = cache
        return cache


def clear_rules_cache_registry() -> None:
    """Reset the registry — useful in tests or after workspace switch."""
    with _registry_lock:
        _cache_registry.clear()
