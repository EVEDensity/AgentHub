"""Best-effort, dependency-free symbol extraction.

Python uses the standard library AST. TypeScript, JavaScript and Go use
conservative declaration/import patterns until a Tree-sitter grammar is
configured. Invalid source produces an empty result rather than blocking file
tools or the CLI.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    line_start: int
    line_end: int
    qualified_name: str = ""
    signature: str = ""


@dataclass(frozen=True)
class Reference:
    name: str
    line: int
    column: int
    kind: str = "name"


@dataclass(frozen=True)
class ImportReference:
    module: str
    name: str
    line: int


@dataclass(frozen=True)
class ExtractionResult:
    language: str
    symbols: tuple[Symbol, ...] = ()
    references: tuple[Reference, ...] = ()
    imports: tuple[ImportReference, ...] = ()
    parse_error: str = ""


_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
}

_TS_DECLARATION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:function|class|interface|type|enum|const|let|var)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_TS_IMPORT = re.compile(
    r"^\s*import(?:\s+[^'\"]+?\s+from)?\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_GO_DECLARATION = re.compile(
    r"^\s*(?:func\s+(?:\([^)]*\)\s*)?|type\s+|var\s+|const\s+)([A-Za-z_][\w]*)",
    re.MULTILINE,
)
_GO_IMPORT = re.compile(r"^\s*\"([^\"]+)\"\s*$", re.MULTILINE)
_IDENTIFIER = re.compile(r"\b[A-Za-z_$][\w$]*\b")


def language_for_path(path: Path) -> str | None:
    return _LANGUAGES.get(path.suffix.lower())


def extract_symbols(path: Path, text: str | None = None) -> ExtractionResult:
    """Extract declarations, references and imports without retaining source."""
    language = language_for_path(path)
    if language is None:
        return ExtractionResult(language="unsupported")
    try:
        source = text if text is not None else path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ExtractionResult(language=language, parse_error=type(exc).__name__)
    if language == "python":
        return _extract_python(source)
    if language in {"typescript", "javascript"}:
        return _extract_script(source, language)
    return _extract_go(source)


def _extract_python(source: str) -> ExtractionResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return ExtractionResult(language="python", parse_error=f"SyntaxError:{exc.lineno}")

    symbols: list[Symbol] = []
    references: list[Reference] = []
    imports: list[ImportReference] = []
    parents: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _qualified(self, name: str) -> str:
            return ".".join((*parents, name))

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            symbols.append(Symbol(node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno), self._qualified(node.name)))
            parents.append(node.name)
            self.generic_visit(node)
            parents.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            args = ", ".join(arg.arg for arg in node.args.args)
            symbols.append(Symbol(node.name, "function", node.lineno, getattr(node, "end_lineno", node.lineno), self._qualified(node.name), f"({args})"))
            parents.append(node.name)
            self.generic_visit(node)
            parents.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                imports.append(ImportReference(alias.name, alias.asname or alias.name.split(".")[0], node.lineno))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            for alias in node.names:
                imports.append(ImportReference(module, alias.asname or alias.name, node.lineno))

        def visit_Name(self, node: ast.Name) -> None:
            references.append(Reference(node.id, node.lineno, node.col_offset, type(node.ctx).__name__.lower()))

    Visitor().visit(tree)
    return ExtractionResult("python", tuple(symbols), tuple(references), tuple(imports))


def _extract_script(source: str, language: str) -> ExtractionResult:
    symbols = [
        Symbol(match.group(1), "declaration", _line_for_offset(source, match.start()), _line_for_offset(source, match.end()), match.group(1))
        for match in _TS_DECLARATION.finditer(source)
    ]
    imports = [
        ImportReference(match.group(1), match.group(1).rsplit("/", 1)[-1], _line_for_offset(source, match.start()))
        for match in _TS_IMPORT.finditer(source)
    ]
    references = _identifier_references(source)
    return ExtractionResult(language, tuple(symbols), tuple(references), tuple(imports))


def _extract_go(source: str) -> ExtractionResult:
    symbols = [
        Symbol(match.group(1), "declaration", _line_for_offset(source, match.start()), _line_for_offset(source, match.end()), match.group(1))
        for match in _GO_DECLARATION.finditer(source)
    ]
    imports = [
        ImportReference(match.group(1), match.group(1).rsplit("/", 1)[-1], _line_for_offset(source, match.start()))
        for match in _GO_IMPORT.finditer(source)
    ]
    return ExtractionResult("go", tuple(symbols), tuple(_identifier_references(source)), tuple(imports))


def _identifier_references(source: str) -> list[Reference]:
    return [
        Reference(match.group(0), _line_for_offset(source, match.start()), match.start() - source.rfind("\n", 0, match.start()) - 1)
        for match in _IDENTIFIER.finditer(source)
    ]


def _line_for_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1

