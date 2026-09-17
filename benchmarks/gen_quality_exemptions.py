"""Generate/refresh ``benchmarks/quality_exemptions.json`` baselines (R4-4).

Scans the same python targets as the quality gates and records the CURRENT
offenders (file-size and per-function complexity) so CI can enforce
"new code must be clean; legacy may only shrink". Re-run this deliberately —
it is a ratchet refresh, not a fix: entries that disappear on the next scan
must be deleted by hand so the list only ever shrinks.

Usage:
    python benchmarks/gen_quality_exemptions.py        # writes the JSON
    python benchmarks/gen_quality_exemptions.py --stdout  # preview only
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.gates import (  # noqa: E402
    MAX_FILE_LINES,
    MAX_FUNCTION_COMPLEXITY,
    _iter_functions,
    _python_targets,
    cyclomatic_complexity,
)

EXEMPTIONS_PATH = ROOT / "benchmarks" / "quality_exemptions.json"


def collect() -> dict[str, dict[str, object]]:
    file_size: dict[str, int] = {}
    complexity: dict[str, int] = {}
    for path in _python_targets():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            lines = sum(1 for line in path.open("rb") if line.strip())
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        if lines > MAX_FILE_LINES:
            file_size[rel] = lines
        for qualname, node in _iter_functions(tree):
            cc = cyclomatic_complexity(node)
            if cc > MAX_FUNCTION_COMPLEXITY:
                complexity[f"{rel}:{qualname}"] = cc
    return {"file_size": file_size, "complexity": complexity}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdout", action="store_true", help="preview without writing")
    args = parser.parse_args()

    data = collect()
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    print(f"file_size exemptions={len(data['file_size'])} "
          f"complexity exemptions={len(data['complexity'])}")
    if args.stdout:
        print(payload)
        return 0
    EXEMPTIONS_PATH.write_text(payload, encoding="utf-8")
    print(f"wrote {EXEMPTIONS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
