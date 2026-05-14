from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import HTTPException

from app.config import WORKSPACE_REPO_PATH


class GitService:
    def __init__(self, repo_path: Path = WORKSPACE_REPO_PATH) -> None:
        self.repo_path = repo_path

    def _run(self, args: list[str]) -> str:
        try:
            completed = subprocess.run(["git", *args], cwd=self.repo_path, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail="Git is not installed or not in PATH") from exc
        if completed.returncode != 0:
            raise HTTPException(status_code=400, detail=completed.stderr.strip() or completed.stdout.strip())
        return completed.stdout.strip()

    def ensure_repo(self) -> None:
        if not (self.repo_path / ".git").exists():
            self._run(["init"])

    def _ensure_identity(self) -> None:
        try:
            self._run(["config", "user.email"])
        except HTTPException:
            self._run(["config", "user.email", "agenthub@example.local"])
        try:
            self._run(["config", "user.name"])
        except HTTPException:
            self._run(["config", "user.name", "AgentHub"])

    def current_branch(self) -> str:
        self.ensure_repo()
        try:
            return self._run(["branch", "--show-current"]) or "main"
        except HTTPException:
            return "main"

    def create_branch(self, branch_name: str) -> dict:
        self.ensure_repo()
        branches = self._run(["branch", "--list", branch_name])
        if branches:
            raise HTTPException(status_code=400, detail="分支已存在")
        self._run(["checkout", "-b", branch_name])
        return {"status": "success", "branch": branch_name}

    def commit(self, message: str, paths: list[str] | None = None) -> dict:
        self.ensure_repo()
        self._ensure_identity()
        self._run(["add", *(paths or ["."])])
        status = self._run(["status", "--porcelain"])
        if not status:
            return {"status": "success", "commit_hash": "", "message": "无文件变更"}
        self._run(["commit", "-m", message])
        commit_hash = self._run(["rev-parse", "HEAD"])
        return {"status": "success", "commit_hash": commit_hash}

    def diff(self) -> dict:
        self.ensure_repo()
        return {"diff": self._run(["diff", "--", "."])}

    def status(self) -> dict:
        self.ensure_repo()
        return {"branch": self.current_branch(), "status": self._run(["status", "--short"])}


git_service = GitService()
