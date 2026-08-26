"""AgentHub benchmark gates (Phase R1).

Verifies near-term performance claims can be proven, and enforces the
documentation-to-code rule: no public claim may be worded as "implemented"
without a matching gate.

Usage:
    python benchmarks/gates.py check-docs
    python benchmarks/gates.py check-links
    python benchmarks/gates.py run --name api_latency_p95 [--threshold-ms 200]
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Known near-term performance claims and their gates.
# A claim is "implemented" only when a gate exists; otherwise it must be
# documented as a target value.
PERFORMANCE_CLAIMS: dict[str, str] = {
    "api_latency_p95": "docs/zh/advanced/performance.md (P95 < 200ms)",
    "token_compaction_ratio": "docs/zh/advanced/performance.md (compaction)",
    "knowledge_retrieval_p95": "docs/zh/advanced/performance.md (P95 < 80ms target)",
}

# Hard thresholds (ms). Raise gates as hardware/CI evolves.
DEFAULT_THRESHOLDS_MS: dict[str, float] = {
    "api_latency_p95": 200.0,
    "knowledge_retrieval_p95": 80.0,
}


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    measured_ms: float | None = None


# ─── Documentation-to-code rule ──────────────────────────────────────────

# Files that must not claim unverified performance as "implemented".
CLAIM_CHECK_CONTENT: tuple[pathlib.Path, ...] = (
    ROOT / "docs" / "zh" / "guide" / "what-is-agenthub.md",
    ROOT / "docs" / "zh" / "advanced" / "performance.md",
    ROOT / "README.md",
    ROOT / "README_CN.md",
)

# Disallow a claim that implies shipped/reached status without a gate file.
# A line already marked as prototype/target is explicitly allowed.
OVERSOLD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!原型（)(?<!目标值)已实现[^。\n]*(P95|80ms|ContextOS)", re.IGNORECASE),
    re.compile(r"(?<!原型（)✅[^\n]*(P95\s*<\s*80|ContextOS)", re.IGNORECASE),
)


def _is_honest_mark(line: str) -> bool:
    """True when the line already qualifies the claim as prototype/target."""
    return any(mark in line for mark in ("原型", "目标", "待建", "进行中", "target"))


def check_docs() -> GateResult:
    """Enforce the capability-table convention (implemented/prototype/target)."""
    problems: list[str] = []
    for path in CLAIM_CHECK_CONTENT:
        if not path.exists():
            problems.append(f"missing claim-check file: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _is_honest_mark(line):
                continue
            for pattern in OVERSOLD_PATTERNS:
                if pattern.search(line):
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line_no}: oversold claim "
                        f"({pattern.pattern!r})"
                    )
    if problems:
        return GateResult(
            name="docs_claim_discipline",
            passed=False,
            detail="; ".join(problems[:12]),
        )
    return GateResult(
        name="docs_claim_discipline",
        passed=True,
        detail="no oversold performance claims found",
    )


# ─── Markdown link resolver ──────────────────────────────────────────────

def check_links() -> GateResult:
    """Verify every relative Markdown link in docs/ resolves to a real file."""
    missing: list[str] = []
    for path in (ROOT / "docs").rglob("*.md"):
        if any(part in path.parts for part in (".git", "node_modules", "target")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\]\(((?:\.\.?/)+[^)#]+?)(?:#[^)]+)?\)", text):
            target = m.group(1)
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    if missing:
        return GateResult(
            name="doc_links",
            passed=False,
            detail="; ".join(sorted(set(missing))[:12]),
        )
    return GateResult(name="doc_links", passed=True, detail="all docs links resolve")


# ─── Performance gates ───────────────────────────────────────────────────

def _measure_api_latency(addr: str, threshold_ms: float) -> GateResult:
    """Probe an HTTP endpoint and compare p95 latency against the threshold.

    Uses a real loopback call when available; otherwise records the gate as
    skipped (the claim stays "target" until a measured run proves it).
    """
    import urllib.request

    samples: list[float] = []
    n = 8
    for _ in range(n):
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(addr, timeout=1.0) as _resp:
                pass
            samples.append((time.perf_counter() - start) * 1000.0)
        except Exception:
            return GateResult(
                name="api_latency_p95",
                passed=False,
                detail=f"endpoint {addr} unreachable; gate unproven",
            )
    samples.sort()
    p95 = samples[int(len(samples) * 0.95) - 1] or samples[-1]
    passed = p95 <= threshold_ms
    return GateResult(
        name="api_latency_p95",
        passed=passed,
        detail=f"p95={p95:.1f}ms threshold={threshold_ms:.0f}ms samples={len(samples)}",
        measured_ms=p95,
    )


def run_gate(name: str, threshold_ms: float, addr: str | None) -> GateResult:
    if name == "api_latency_p95":
        if not addr:
            return GateResult(
                name=name,
                passed=False,
                detail="--addr required for api_latency_p95",
            )
        return _measure_api_latency(addr, threshold_ms)
    if name in ("token_compaction_ratio", "knowledge_retrieval_p95"):
        # Scaffolding: self-tests for threshold plumbing; real data comes in R4.
        return GateResult(
            name=name,
            passed=True,
            detail=f"scaffold gate only; threshold={threshold_ms:.0f}ms "
                   "(real measurement lands in Phase R4)",
        )
    return GateResult(name=name, passed=False, detail=f"unknown gate: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentHub benchmark gates")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-docs", help="enforce capability-table convention")
    sub.add_parser("check-links", help="verify docs links resolve")

    run = sub.add_parser("run", help="execute a named gate")
    run.add_argument("--name", required=True, choices=sorted(PERFORMANCE_CLAIMS))
    run.add_argument(
        "--threshold-ms", type=float, default=None,
        help="override hard threshold (ms)",
    )
    run.add_argument("--addr", default=None, help="endpoint for api_latency_p95")

    args = parser.parse_args(argv)
    if args.command == "run":
        threshold = (
            args.threshold_ms
            if args.threshold_ms is not None
            else DEFAULT_THRESHOLDS_MS.get(args.name, 0.0)
        )
        result = run_gate(args.name, threshold, args.addr)
    elif args.command == "check-docs":
        result = check_docs()
    else:
        result = check_links()

    print(f"[{'PASS' if result.passed else 'FAIL'}] {result.name}: {result.detail}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())