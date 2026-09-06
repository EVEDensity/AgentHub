"""Deterministic project identity discovery for model and CLI contexts."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_TECH_MANIFESTS = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "pom.xml": "Java",
    "Gemfile": "Ruby",
}


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _safe_remote(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
    return re.sub(r"//[^@/]+@", "//", value)


def _project_name(root: Path) -> str:
    package = root / "package.json"
    if package.is_file():
        try:
            value = json.loads(package.read_text(encoding="utf-8")).get("name")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (OSError, ValueError, TypeError):
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            value = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("name")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (OSError, ValueError, TypeError):
            pass
    return root.name or "workspace"


def _readme_summary(root: Path, *, max_chars: int = 500) -> str:
    for name in ("README.md", "README_CN.md", "README.rst", "README.txt"):
        path = root / name
        if not path.is_file():
            continue
        try:
            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        except (OSError, UnicodeError):
            continue
        meaningful = [line for line in lines if line and not line.startswith("<!--")]
        if meaningful and meaningful[0].startswith("#"):
            meaningful = meaningful[1:]
        summary = re.sub(r"[`*_]", "", " ".join(meaningful[:3])).strip()
        if summary:
            return summary[:max_chars]
    return ""


@dataclass(frozen=True)
class ProjectManifest:
    name: str
    workspace_root: Path
    git_branch: str = ""
    git_remote: str = ""
    readme_summary: str = ""
    tech_stack: tuple[str, ...] = ()
    instruction_files: tuple[str, ...] = ()

    @classmethod
    def discover(cls, workspace_root: Path, *, instruction_files=()) -> "ProjectManifest":
        root = workspace_root.resolve()
        stack = tuple(value for filename, value in _TECH_MANIFESTS.items() if (root / filename).is_file())
        return cls(
            name=_project_name(root), workspace_root=root,
            git_branch=_git(root, "branch", "--show-current"),
            git_remote=_safe_remote(_git(root, "config", "--get", "remote.origin.url")),
            readme_summary=_readme_summary(root),
            tech_stack=stack,
            instruction_files=tuple(str(Path(path).resolve()) for path in instruction_files),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "workspaceRoot": str(self.workspace_root),
            "gitBranch": self.git_branch or None, "gitRemote": self.git_remote or None,
            "readmeSummary": self.readme_summary, "techStack": list(self.tech_stack),
            "instructionFiles": list(self.instruction_files),
        }

    def to_prompt(self, *, provider: str = "", model: str = "") -> str:
        return (
            "【AgentHub 项目身份清单（程序自动发现，优先于模型猜测）】\n"
            f"项目名称: {self.name}\n工作区根目录: {self.workspace_root}\n"
            f"Git 分支: {self.git_branch or '非 Git 仓库或未检出分支'}\n"
            f"Git 远程: {self.git_remote or '未配置 origin'}\n"
            f"技术栈: {', '.join(self.tech_stack) or '未识别'}\n"
            f"项目说明: {self.readme_summary or '未读取到 README 摘要'}\n"
            f"项目指令文件: {', '.join(self.instruction_files) or '无'}\n"
            f"当前 CLI: AgentHub Developer CLI\n当前模型: {provider or '未知 provider'}/{model or '未知 model'}\n"
            "身份和项目事实必须以此清单或工具结果为准；不确定时调用 project_inspect、"
            "file_read、git_status 或 git_diff，不得使用‘可能是’代替事实。\n"
        )


__all__ = ["ProjectManifest"]
