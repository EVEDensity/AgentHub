"""Create an isolated, empty evidence directory for one release run."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_ROOT = (ROOT / "artifacts" / "production").resolve()
VALID_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_id", help="release-specific directory name, e.g. release-v1.0.0")
    args = parser.parse_args()
    if not VALID_RELEASE.fullmatch(args.release_id):
        parser.error("release_id contains unsupported characters")
    target = (EVIDENCE_ROOT / args.release_id).resolve()
    try:
        target.relative_to(EVIDENCE_ROOT)
    except ValueError as exc:
        raise SystemExit("release directory must be inside artifacts/production") from exc
    if target == EVIDENCE_ROOT:
        raise SystemExit("refusing to remove the entire production evidence root")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
