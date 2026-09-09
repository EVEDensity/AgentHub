"""Installed console-script entry point with a dependency-light help path."""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch ``agenthub`` while keeping ``--help`` usable in a lean install."""
    if sys.argv[1:] in (["--help"], ["-h"]):
        from app.cli.fast_help import print_root_help

        print_root_help()
        raise SystemExit(0)

    from app.cli.main import main as cli_main

    cli_main()


__all__ = ["main"]
