"""Dependency-free root help used before importing the CLI runtime graph."""

ROOT_HELP = """usage: agenthub [-h] [-p [PRINT_MODE]]
                {init,run,exec,missions,search,replay,facts,review-pr,chat,tui,stacks,upgrade,doctor,completion} ...

AgentHub developer CLI - run auditable Missions through bounded agents,
sandboxed tools, and an independent verifier gate.

commands:
  init        initialize local non-secret configuration
  run         run one objective interactively
  exec        run one objective for CI (--json/--jsonl)
  chat        open the interactive AgentHub session
  tui         open the full-screen terminal UI
  missions    list local Mission history
  search      search Mission receipts
  replay      show Mission evidence and Artifacts
  facts       manage project facts
  review-pr   review a pull-request diff
  stacks      list installed runtime stacks
  upgrade     install and verify a runtime stack
  doctor      diagnose local readiness
  completion  print shell completion

options:
  -h, --help            show this help message and exit
  -p [PRINT_MODE], --print [PRINT_MODE]
                        run one prompt and exit

Use `agenthub <command> --help` for command-specific options.
"""


def print_root_help() -> None:
    print(ROOT_HELP, end="")


__all__ = ["ROOT_HELP", "print_root_help"]
