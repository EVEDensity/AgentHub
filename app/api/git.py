from __future__ import annotations

from fastapi import APIRouter, Depends

from app.schemas.common import GitBranchRequest, GitCommitRequest
from app.services.auth_service import get_current_user, write_audit
from app.services.git_service import git_service

router = APIRouter(prefix="/api/git", tags=["git"])


@router.post("/branch")
async def create_branch(data: GitBranchRequest, user: dict = Depends(get_current_user)) -> dict:
    result = git_service.create_branch(data.branchName)
    write_audit(user["id"], "GitService", "git_branch", "L2", "approve", data.model_dump())
    return result


@router.post("/commit")
async def commit(data: GitCommitRequest, user: dict = Depends(get_current_user)) -> dict:
    result = git_service.commit(data.message, data.paths)
    write_audit(user["id"], "GitService", "git_commit", "L2", "approve", data.model_dump())
    return result


@router.get("/diff")
async def diff() -> dict:
    return git_service.diff()


@router.get("/status")
async def status() -> dict:
    return git_service.status()
