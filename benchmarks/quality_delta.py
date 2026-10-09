"""PR quality ratchet against the actual Git merge base, without blanket exemptions."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

DIRECTORIES = {"app", "services", "benchmarks", "tests", "scripts"}
DECISIONS = (ast.If, ast.For, ast.While, ast.AsyncFor, ast.IfExp, ast.Try,
             ast.ExceptHandler, ast.With, ast.AsyncWith, ast.Assert, ast.comprehension)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, encoding="utf-8",
                            capture_output=True, check=True)
    return result.stdout


def _lines(source: str) -> int:
    return sum(bool(line.strip()) and not line.lstrip().startswith("#")
               for line in source.splitlines())


def _complexity(node: ast.AST) -> int:
    score = 1
    children = list(ast.iter_child_nodes(node))
    while children:
        child = children.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(child, DECISIONS):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.Match):
            score += len(child.cases)
        children.extend(ast.iter_child_nodes(child))
    return score


class _FunctionMetrics(ast.NodeVisitor):
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.nodes: list[tuple[str, ast.AST]] = []
        self.binding: str | None = None
        self.lambda_counts: dict[str, int] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        name = self.prefix + node.name
        self.nodes.append((name, node))
        previous = self.prefix
        self.prefix = name + "."
        self.generic_visit(node)
        self.prefix = previous

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        scope = self.prefix + (self.binding + "." if self.binding else "")
        index = self.lambda_counts.get(scope, 0) + 1
        self.lambda_counts[scope] = index
        name = f"{scope}<lambda#{index}>"
        self.nodes.append((name, node))
        previous_prefix, previous_binding = self.prefix, self.binding
        self.prefix, self.binding = name + ".", None
        self.generic_visit(node)
        self.prefix, self.binding = previous_prefix, previous_binding

    def _visit_bound(self, node: ast.AST, targets: list[ast.AST]) -> None:
        previous = self.binding
        self.binding = ",".join(ast.unparse(target) for target in targets)
        self.generic_visit(node)
        self.binding = previous

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Lambda):
            self._visit_bound(node, node.targets)
        else:
            self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.value, ast.Lambda):
            self._visit_bound(node, [node.target])
        else:
            self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        if isinstance(node.value, ast.Lambda):
            self._visit_bound(node, [node.target])
        else:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        previous = self.prefix
        self.prefix += node.name + "."
        self.generic_visit(node)
        self.prefix = previous


def _function_nodes(tree: ast.AST, prefix: str = "") -> list[tuple[str, ast.AST]]:
    visitor = _FunctionMetrics(prefix)
    visitor.visit(tree)
    return visitor.nodes


def _functions(tree: ast.AST, prefix: str = "") -> dict[str, int]:
    values = {}
    for name, node in _function_nodes(tree, prefix):
        values[name] = max(values.get(name, 0), _complexity(node))
    return values


def _compare(path: str, source: str, baseline: str | None) -> list[str]:
    issues = []
    current_functions = _functions(ast.parse(source, filename=path))
    previous_functions = _functions(ast.parse(baseline, filename=path)) if baseline else {}
    line_limit = max(800, _lines(baseline)) if baseline is not None else 500
    if _lines(source) > line_limit:
        issues.append(f"{path}: effective lines={_lines(source)} > {line_limit}")
    for name, complexity in current_functions.items():
        previous = previous_functions.get(name)
        limit = max(20, previous) if previous is not None else 15
        if complexity > limit:
            issues.append(f"{path}:{name}: CC={complexity} > {limit}")
    return issues


def check_quality(root: Path, base_ref: str) -> dict:
    base = _git(root, "merge-base", "HEAD", base_ref).strip()
    paths = set(_git(root, "diff", "--name-only", "-z", "--diff-filter=ACMR", base, "--").split("\0"))
    paths.update(_git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0"))
    baseline_paths = set(_git(root, "ls-tree", "-r", "--name-only", "-z", base).split("\0"))
    issues = []
    checked = []
    for path in sorted(paths):
        if not path:
            continue
        file = root / path
        if Path(path).suffix != ".py" or Path(path).parts[0] not in DIRECTORIES:
            continue
        if not file.is_file():
            continue
        checked.append(path)
        baseline = _git(root, "show", f"{base}:{path}") if path in baseline_paths else None
        try:
            issues.extend(_compare(path, file.read_text(encoding="utf-8-sig"), baseline))
        except SyntaxError as exc:
            issues.append(f"{path}: syntax error at line {exc.lineno}: {exc.msg}")
    return {"base": base, "checked": checked, "issues": issues, "passed": not issues}


def run_quality(root: Path, base_ref: str, output: str | None = None) -> int:
    try:
        report = check_quality(root, base_ref)
    except (subprocess.CalledProcessError, OSError, UnicodeError) as exc:
        report = {"passed": False, "issues": [f"quality baseline unavailable: {exc}"]}
    if output:
        file = Path(output)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[{'PASS' if report['passed'] else 'FAIL'}] PR quality: "
          f"{len(report.get('checked', []))} files checked")
    for issue in report["issues"]:
        print(issue)
    return 0 if report["passed"] else 1
