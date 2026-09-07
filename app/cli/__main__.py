"""``python -m app.cli`` entry point with a dependency-free help path."""

import sys

if sys.argv[1:] in (["--help"], ["-h"]):
    from app.cli.fast_help import print_root_help

    print_root_help()
    raise SystemExit(0)

from app.cli.main import main

if __name__ == "__main__":
    main()
