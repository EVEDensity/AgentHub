# Desktop Runner composition

This package composes desktop authentication, model context, scoped built-in
tools, Harness budgets/checkpoints, claim supervision and independent artifact
verification. Durable state changes go through Mission Control.

`tool_approval.py` translates the resolved workspace permission policy into
explicit grants for the canonical Harness tool gateway. Suggest mode denies
workspace mutation; edit mode permits bounded workspace edits; auto mode also
permits the built-in code executor. Built-in handlers retain path and execution
limits. Shell acceptance commands keep their existing declared-command channel.
Arbitrary remote, network and MCP side effects receive no implicit grant.
Child desktop Harness runs inherit the same permission policy. A factory without
an injected policy cannot approve side effects.

Verified by `tests/services/test_desktop_tool_approval.py` and
`tests/services/test_desktop_local_runner.py`.
