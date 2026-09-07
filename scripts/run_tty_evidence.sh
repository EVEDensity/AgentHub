#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$root/artifacts/production/tty"
release_id="${AGENTHUB_RELEASE_ID:-release-local}"
evidence_root="$root/artifacts/production/$release_id"
mkdir -p "$evidence_root/tty"

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "TTY evidence requires an interactive terminal" >&2
  exit 2
fi

for width in 40 80 120; do
  export AGENTHUB_CLI_TTY_WIDTH="$width"
  export AGENTHUB_PRODUCTION_EVIDENCE_DIR="$evidence_root"
  python "$root/scripts/cli_tty_evidence.py" \
    > "$evidence_root/tty/tty-${width}.json"
done
