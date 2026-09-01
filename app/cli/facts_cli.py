"""`agenthub facts` command handlers (ADR-0107 items 3-4).

Thin CLI shell over the flat key-scoped store in `project_facts.py`:
list/set/get/remove with `SECTION.KEY` addressing. The storage module
owns parsing, overwrite semantics, and gated injection.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.cli.project_facts import (
    load_facts,
    memory_file_path,
    remove_fact,
    set_fact,
)
from app.cli.runtime import EXIT_INFRA_ERROR, EXIT_OK, state_dir


def _split_fact_name(name: str) -> tuple[str, str] | None:
    if "." not in name:
        return None
    section, _, key = name.partition(".")
    section = section.strip()
    key = key.strip()
    if not section or not key:
        return None
    return section, key


def cmd_facts(args: argparse.Namespace, cwd: Path) -> int:
    directory = state_dir(cwd)
    path = memory_file_path(directory)
    command = getattr(args, "facts_command", None) or "list"
    if command == "list":
        facts = load_facts(path)
        if not facts:
            print(f"no facts yet — add one with "
                  f"`agenthub facts set SECTION.KEY \"value\"`")
            return EXIT_OK
        for fact in facts:
            print(f"{fact.dotted}: {fact.value}")
        return EXIT_OK
    parsed = _split_fact_name(args.name)
    if parsed is None:
        print(
            "error: fact name must be SECTION.KEY (e.g. python.interpreter)",
            file=sys.stderr,
        )
        return EXIT_INFRA_ERROR
    section, key = parsed
    if command == "set":
        directory.mkdir(parents=True, exist_ok=True)
        outcome = set_fact(path, section, key, args.value)
        if outcome == "unchanged":
            print(f"unchanged: {args.name} already holds that value")
        else:
            print(f"{outcome}: {args.name} = {args.value}")
        return EXIT_OK
    if command == "get":
        for fact in load_facts(path):
            if fact.section == section and fact.key == key:
                print(fact.value)
                return EXIT_OK
        print(f"fact not found: {args.name}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    if command == "remove":
        if remove_fact(path, section, key):
            print(f"removed: {args.name}")
            return EXIT_OK
        print(f"fact not found: {args.name}", file=sys.stderr)
        return EXIT_INFRA_ERROR
    parser_error = f"unknown facts command: {command}"
    print(parser_error, file=sys.stderr)
    return EXIT_INFRA_ERROR
