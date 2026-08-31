"""Build a stack manifest for the desktop bootstrap downloader (M3).

Walks a directory tree (the packaged `local-services/` runtime stack),
hashes every file with sha256, and writes a schemaVersion=1 manifest
matching both the Python installer (`app/cli/stack_installer.py`) and
the Rust bootstrap module (`desktop/src-tauri/src/bootstrap.rs`).

Usage::

    python scripts/make_stack_manifest.py \
        --stack-dir desktop/local-services \
        --version 0.1.0 --commit <git-sha> \
        --out dist/stack-manifest.json

The manifest file paths are POSIX-style and relative to the stack
root, so the same manifest works for the release source layout and the
installed `stacks/<version>-<commit>/` layout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_SCHEMA_VERSION = 1
# Files that never belong in a downloaded runtime stack.
_EXCLUDED_NAMES = {".pinned", ".pinned.tmp", "stack-manifest.json", ".DS_Store", "Thumbs.db"}
_CHUNK = 1024 * 1024


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a stack manifest for the bootstrap downloader."
    )
    parser.add_argument(
        "--stack-dir",
        required=True,
        help="root directory of the runtime stack (e.g. packaged local-services)",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="stack version (e.g. 0.1.0)",
    )
    parser.add_argument(
        "--commit",
        default="",
        help="source commit sha pinned into the manifest (default: '')",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="output manifest path (parent directories are created)",
    )
    parser.add_argument(
        "--base-dir-name",
        default="local-services",
        help=(
            "prefix for manifest paths (default: local-services). Use '' "
            "to keep paths relative to --stack-dir itself."
        ),
    )
    return parser.parse_args(argv)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_entries(
    stack_dir: Path, base_dir_name: str
) -> list[dict[str, object]]:
    if not stack_dir.is_dir():
        raise SystemExit(f"error: stack directory not found: {stack_dir}")
    entries: list[dict[str, object]] = []
    for path in sorted(stack_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_NAMES:
            continue
        relative = path.relative_to(stack_dir).as_posix()
        manifest_path = (
            f"{base_dir_name}/{relative}" if base_dir_name else relative
        )
        entries.append(
            {
                "path": manifest_path,
                "sha256": _sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    if not entries:
        raise SystemExit(f"error: no stack files found under {stack_dir}")
    return entries


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    stack_dir = Path(args.stack_dir)
    entries = collect_entries(stack_dir, args.base_dir_name)
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "version": args.version,
        "commit": args.commit,
        "generatedAt": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "files": entries,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    total_bytes = sum(int(entry["size"]) for entry in entries)  # type: ignore[arg-type]
    print(f"manifest written: {out_path}")
    print(
        f"version={args.version} commit={args.commit or '-'} "
        f"files={len(entries)} total_bytes={total_bytes}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
