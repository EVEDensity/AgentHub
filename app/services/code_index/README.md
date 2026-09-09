# Code Index Service

This package is an optional acceleration layer for local repository discovery.
It stores only file hashes and symbol metadata in a workspace-local SQLite
database. Python files use the standard AST; TypeScript, JavaScript and Go use
bounded declaration/import extraction without requiring a grammar package.

Index creation and queries are fail-open: callers must use the existing
`file_search` and `file_glob` tools when the index is unavailable, stale, or
cannot parse a file. The index is not a source of business truth and never
stores source text, credentials, or tool output.
