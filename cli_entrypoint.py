"""Frozen Windows entry point for the AgentHub developer CLI.

PyInstaller target for ``python -m app.cli`` (north-star M3 / I-2: the
npm ``@agenthub/cli`` distribution ships this as a single onefile
binary with zero runtime dependencies). The mission-control subprocess
is booted by the frozen binary re-invoking itself with the hidden
``_serve`` subcommand — see ``app.cli.runtime.server_command``.
"""

import sys

if sys.argv[1:] in (["--help"], ["-h"]):
    from app.cli.fast_help import print_root_help

    print_root_help()
    raise SystemExit(0)

from app.cli.main import main

if __name__ == "__main__":
    main()
