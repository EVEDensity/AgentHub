from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.db.init_db import now
from app.db.session import afetch_all, afetch_one, aexecute
from app.schemas.common import ChatTaskRequest
from app.services.auth_service import get_current_user
from app.services.auth.session_guard import (
    SessionAccess,
    SessionRole,
    check_session_access,
)
from app.services.agent_service import list_messages
from app.services.auto_name_prompt import build_auto_name_prompt, extract_local_title
from app.services.session_preferences import (
    PINNED_SESSIONS_SETTING_KEY,
    apply_session_pin_state,
    parse_pinned_session_ids,
    serialize_pinned_session_ids,
)
from app.services.task_state_machine import task_state_machine

from app.api.chat._helpers import (
    SessionCreateRequest,
    InviteRequest,
    RoleChangeRequest,
    is_generic_name,
    _call_llm_for_name,
    _extract_local_title,
    try_auto_name_session,
    GENERIC_SESSION_NAMES,
    logger,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/sessions")
async def sessions(user: dict = Depends(get_current_user)) -> list[dict]:
    """Return sessions the current user can access.

    Includes sessions where the user is a member (any role) plus
    public sessions that are visible to all authenticated users.
    """
    user_id = user["id"]
    rows = await afetch_all(
        """SELECT s.id, s.name, s.type, s.active,
                  s.created_at AS "createdAt",
                  s.is_pinned AS "isPinned",
                  s.last_message_at AS "lastMessageAt",
                  s.owner_id AS "ownerId",
                  s.visibility,
                  COALESCE(sm.role, 'viewer') AS "myRole",
                  (SELECT COUNT(*) FROM session_members WHERE session_id = s.id)::int AS "memberCount"
           FROM sessions s
           LEFT JOIN session_members sm ON s.id = sm.session_id AND sm.user_id = $1
           WHERE s.visibility = 'public'
              OR sm.user_id = $1
           ORDER BY CASE WHEN s.last_message_at != '' THEN s.last_message_at
                         ELSE s.created_at END DESC""",
        user_id,
    )
    pinned_row = await afetch_one(
        "SELECT value FROM user_settings WHERE user_id=$1 AND key=$2 LIMIT 1",
        user_id,
        PINNED_SESSIONS_SETTING_KEY,
    )
    pinned_ids = parse_pinned_session_ids((pinned_row or {}).get("value"))
    return apply_session_pin_state([dict(row) for row in rows], pinned_ids)


@router.post("/sessions")
async def create_session(
    data: SessionCreateRequest, user: dict = Depends(get_current_user)
) -> dict:
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    ts = now()
    name = data.name.strip() or "新建会话"
    visibility = data.visibility if data.visibility in ("private", "public") else "private"

    await aexecute(
        "INSERT INTO sessions(id,name,type,participants,active,created_at,owner_id,visibility) "
        "VALUES($1,$2,$3,$4,$5,$6,$7,$8)",
        session_id, name, "group", "[]", 1, ts, user["id"], visibility,
    )
    # Add creator as owner
    await aexecute(
        "INSERT INTO session_members(session_id,user_id,role,joined_at) VALUES($1,$2,$3,$4)",
        session_id, user["id"], "owner", ts,
    )
    return {
        "id": session_id, "name": name, "createdAt": ts,
        "active": 1, "type": "group", "ownerId": user["id"],
        "visibility": visibility, "myRole": "owner",
    }


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: str, user: dict = Depends(get_current_user)) -> list[dict]:
    access = await check_session_access(session_id, user)
    return await list_messages(session_id)


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    from pathlib import Path
    from app.utils.async_file import aexists, aisfile, aunlink, aglob_simple, aread_json, awrite_json

    # ── 0. Access control: only owner can delete ─────────────────────
    access = await check_session_access(session_id, user)
    if not access.can_manage:
        raise HTTPException(status_code=403, detail="Only the session owner can delete it")

    # ── 1. Get session name before deletion (needed for memory cleanup) ──
    session_name: str | None = None
    row = await afetch_all("SELECT name FROM sessions WHERE id=$1 LIMIT 1", session_id)
    if row and row[0].get("name"):
        session_name = row[0]["name"]

    # ── 2. Delete from PostgreSQL ─────────────────────────────────────
    await aexecute("DELETE FROM messages WHERE session_id=$1", session_id)
    await aexecute("DELETE FROM tasks WHERE session_id=$1", session_id)
    await aexecute("DELETE FROM session_members WHERE session_id=$1", session_id)
    await aexecute("DELETE FROM user_presence WHERE session_id=$1", session_id)
    deleted = await afetch_one("DELETE FROM sessions WHERE id=$1 RETURNING id", session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    # ── 3. Clean up memory artifacts ───────────────────────────────────
    from app.config import MEMORY_DIR
    from app.services.memory.models import sanitize_filename

    memory_base = Path(MEMORY_DIR)
    cleaned: list[str] = []

    # 3a. Delete session summary file
    sessions_dir = memory_base / "sessions"
    raw_fname = sanitize_filename(session_id)
    stem_fname = raw_fname[:-3] if raw_fname.endswith(".md") else raw_fname
    for fname in (raw_fname, stem_fname):
        if not fname:
            continue
        summary_path = sessions_dir / fname
        try:
            if await aexists(summary_path):
                await aunlink(summary_path)
                cleaned.append(f"session_summary/{summary_path.name}")
        except OSError:
            pass

    # 3b. Delete memory files named after the session
    if session_name:
        sanitized_name = sanitize_filename(session_name)
        for candidate in await aglob_simple(memory_base, f"{sanitized_name}*"):
            try:
                if await aisfile(candidate) and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                    await aunlink(candidate)
                    cleaned.append(f"memory/{candidate.name}")
            except OSError:
                pass

    # 3c. Also check for memory files named after the session ID itself
    sanitized_id = sanitize_filename(session_id)
    for candidate in await aglob_simple(memory_base, f"{sanitized_id}*"):
        try:
            if await aisfile(candidate) and candidate.name.endswith(".md") and candidate.name != "MEMORY.md":
                await aunlink(candidate)
                cleaned.append(f"memory/{candidate.name}")
        except OSError:
            pass

    # 3c-extra. 兜底清理：扫描所有 .md 文件，检查其 YAML header 的 session_id
    # 字段是否等于被删的 session_id（即使文件名与 name/id 都不匹配也能删除）。
    from app.utils.async_file import aiterdir, aread_text
    try:
        for md_file in await aiterdir(memory_base):
            if not md_file.name.endswith(".md") or md_file.name == "MEMORY.md":
                continue
            try:
                content = await aread_text(md_file)
            except OSError:
                continue
            if f"session_id: {session_id}" in content or f"session_id:{session_id}" in content:
                try:
                    await aunlink(md_file)
                    cleaned.append(f"memory_by_header/{md_file.name}")
                except OSError:
                    pass
    except OSError:
        pass

    # 3d. Clean extraction state cursor
    extraction_state_path = memory_base / ".extraction_state.json"
    try:
        if await aexists(extraction_state_path):
            state = await aread_json(extraction_state_path)
            if state.get("sessions", {}).pop(session_id, None):
                await awrite_json(extraction_state_path, state)
                cleaned.append("extraction_state_cursor")
    except (OSError, ValueError):
        pass

    # 3e. Clean session memory state
    session_state_path = sessions_dir / ".session_state.json"
    try:
        if await aexists(session_state_path):
            state = await aread_json(session_state_path)
            if state.get("sessions", {}).pop(session_id, None):
                await awrite_json(session_state_path, state)
                cleaned.append("session_memory_state")
    except (OSError, ValueError):
        pass

    # 3f. Rebuild MEMORY.md index to reflect deletions
    if cleaned:
        try:
            from app.services.memory.storage import MemoryStorage
            storage = MemoryStorage(memory_base)
            await storage.rebuild_index()
        # noqa: BLE001 - best-effort, never block main path
        except Exception:
            pass

    logger.info(
        "session deleted id=%s name=%s cleaned=[%s]",
        session_id, session_name, ", ".join(cleaned),
    )

    return {"status": "success", "sessionId": session_id, "cleaned": cleaned}


@router.put("/sessions/{session_id}")
async def rename_session(session_id: str, data: dict, user: dict = Depends(get_current_user)) -> dict:
    access = await check_session_access(session_id, user)

    # Handle visibility change (owner only)
    visibility = data.get("visibility")
    if visibility and visibility in ("private", "public"):
        if not access.can_manage:
            raise HTTPException(status_code=403, detail="Only the owner can change visibility")
        await aexecute(
            "UPDATE sessions SET visibility=$1 WHERE id=$2", visibility, session_id,
        )
        from app.services.auth.session_guard import audit_session_event
        await audit_session_event(
            session_id, user["id"], "visibility_changed",
            details=f"Changed to {visibility}",
        )
        return {"status": "success", "sessionId": session_id, "visibility": visibility}

    # Handle name change
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if not access.can_write:
        raise HTTPException(status_code=403, detail="No permission to rename this session")

    updated = await afetch_one(
        "UPDATE sessions SET name=$1 WHERE id=$2 RETURNING id", name, session_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "success", "sessionId": session_id, "name": name}


@router.put("/sessions/{session_id}/pin")
async def toggle_pin_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    access = await check_session_access(session_id, user)
    pinned_row = await afetch_one(
        "SELECT value FROM user_settings WHERE user_id=$1 AND key=$2 LIMIT 1",
        user["id"], PINNED_SESSIONS_SETTING_KEY,
    )
    pinned_ids = parse_pinned_session_ids((pinned_row or {}).get("value"))
    is_pinned = session_id not in pinned_ids
    if is_pinned:
        pinned_ids.add(session_id)
    else:
        pinned_ids.discard(session_id)
    await aexecute(
        "INSERT INTO user_settings (user_id, key, value, updated_at) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, key) DO UPDATE SET value=$3, updated_at=$4",
        user["id"], PINNED_SESSIONS_SETTING_KEY,
        serialize_pinned_session_ids(pinned_ids), now(),
    )
    return {"status": "success", "sessionId": session_id, "isPinned": 1 if is_pinned else 0}


# ── Multi-user membership endpoints ────────────────────────────────────


@router.get("/sessions/{session_id}/members")
async def list_members(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """List all members of a session. Any member can view the member list."""
    access = await check_session_access(session_id, user)
    rows = await afetch_all(
        """SELECT sm.user_id AS "userId", u.name AS "userName", u.role AS "userRole",
                  sm.role, sm.invited_by AS "invitedBy", sm.joined_at AS "joinedAt"
           FROM session_members sm
           JOIN users u ON sm.user_id = u.id
           WHERE sm.session_id = $1
           ORDER BY CASE sm.role WHEN 'owner' THEN 0 WHEN 'member' THEN 1 ELSE 2 END,
                    sm.joined_at ASC""",
        session_id,
    )
    # Attach online status from user_presence
    presence_rows = await afetch_all(
        "SELECT user_id, status FROM user_presence WHERE session_id=$1", session_id
    )
    presence_map = {p["user_id"]: p["status"] for p in presence_rows}

    result = []
    for r in rows:
        r["onlineStatus"] = presence_map.get(r["userId"], "offline")
        result.append(r)
    return {"members": result}


@router.post("/sessions/{session_id}/members")
async def invite_member(
    session_id: str, data: InviteRequest, user: dict = Depends(get_current_user)
) -> dict:
    """Invite a user to the session. Only owner can invite."""
    access = await check_session_access(session_id, user)
    if not access.can_invite:
        raise HTTPException(status_code=403, detail="Only the owner can invite members")

    # Resolve target user: prefer user_id, fall back to user_name lookup
    target_user_id = data.user_id.strip()
    if not target_user_id and data.user_name.strip():
        target = await afetch_one(
            "SELECT id, name FROM users WHERE name=$1", data.user_name.strip()
        )
        if target:
            target_user_id = target["id"]
    if not target_user_id:
        raise HTTPException(status_code=400, detail="user_id or userName is required")

    # Verify the target user exists
    target = await afetch_one("SELECT id, name FROM users WHERE id=$1", target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    role = data.role if data.role in ("member", "viewer") else "member"
    ts = now()
    await aexecute(
        "INSERT INTO session_members(session_id,user_id,role,invited_by,joined_at) "
        "VALUES($1,$2,$3,$4,$5) ON CONFLICT(session_id,user_id) DO UPDATE SET role=$3",
        session_id, target_user_id, role, user["id"], ts,
    )

    # If session is private, make sure the invited user can see it
    await aexecute(
        "UPDATE sessions SET visibility='private' WHERE id=$1 AND visibility='private'",
        session_id,
    )

    return {
        "status": "success",
        "sessionId": session_id,
        "userId": target_user_id,
        "userName": target["name"],
        "role": role,
        "joinedAt": ts,
    }


@router.put("/sessions/{session_id}/members/{target_user_id}")
async def change_member_role(
    session_id: str, target_user_id: str,
    data: RoleChangeRequest, user: dict = Depends(get_current_user),
) -> dict:
    """Change a member's role. Only owner can change roles."""
    access = await check_session_access(session_id, user)
    if not access.can_manage:
        raise HTTPException(status_code=403, detail="Only the owner can change roles")

    if data.role not in ("member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role (use 'member' or 'viewer')")

    # Cannot change owner's role
    existing = await afetch_one(
        "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")
    if existing["role"] == "owner":
        raise HTTPException(status_code=400, detail="Cannot change the owner's role")

    await aexecute(
        "UPDATE session_members SET role=$1 WHERE session_id=$2 AND user_id=$3",
        data.role, session_id, target_user_id,
    )
    return {"status": "success", "sessionId": session_id, "userId": target_user_id, "role": data.role}


@router.delete("/sessions/{session_id}/members/{target_user_id}")
async def remove_member(
    session_id: str, target_user_id: str, user: dict = Depends(get_current_user)
) -> dict:
    """Remove a member from the session. Owner can remove anyone.
    Members can remove themselves (leave the session)."""
    access = await check_session_access(session_id, user)

    is_self = target_user_id == user["id"]
    if not is_self and not access.can_manage:
        raise HTTPException(status_code=403, detail="Only the owner can remove other members")

    # Cannot remove the owner
    existing = await afetch_one(
        "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")
    if existing["role"] == "owner" and not is_self:
        raise HTTPException(status_code=400, detail="Cannot remove the owner. Transfer ownership first.")

    await aexecute(
        "DELETE FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    await aexecute(
        "DELETE FROM user_presence WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    return {"status": "success", "sessionId": session_id, "userId": target_user_id}


@router.post("/sessions/{session_id}/join")
async def join_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Join a public session as a viewer. Private sessions require an invitation."""
    sess = await afetch_one(
        "SELECT id, name, visibility FROM sessions WHERE id=$1", session_id
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check existing membership
    existing = await afetch_one(
        "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, user["id"],
    )
    if existing:
        return {"status": "already_member", "sessionId": session_id, "role": existing["role"]}

    if sess["visibility"] != "public":
        raise HTTPException(status_code=403, detail="This session is private. You need an invitation to join.")

    ts = now()
    await aexecute(
        "INSERT INTO session_members(session_id,user_id,role,joined_at) VALUES($1,$2,$3,$4)",
        session_id, user["id"], "viewer", ts,
    )
    return {"status": "success", "sessionId": session_id, "role": "viewer", "joinedAt": ts}


@router.post("/sessions/{session_id}/transfer")
async def transfer_ownership(
    session_id: str, data: dict, user: dict = Depends(get_current_user)
) -> dict:
    """Transfer session ownership to another member. Only the current owner can do this."""
    access = await check_session_access(session_id, user)
    if not access.can_manage:
        raise HTTPException(status_code=403, detail="Only the owner can transfer ownership")

    target_user_id = data.get("userId", "")
    if not target_user_id:
        raise HTTPException(status_code=400, detail="userId is required")

    # Verify target is a member
    target = await afetch_one(
        "SELECT role FROM session_members WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    if not target:
        raise HTTPException(status_code=404, detail="Target user is not a member of this session")

    ts = now()
    # Demote current owner to member
    await aexecute(
        "UPDATE session_members SET role='member' WHERE session_id=$1 AND user_id=$2",
        session_id, user["id"],
    )
    # Promote target to owner
    await aexecute(
        "UPDATE session_members SET role='owner' WHERE session_id=$1 AND user_id=$2",
        session_id, target_user_id,
    )
    # Update sessions table owner_id
    await aexecute(
        "UPDATE sessions SET owner_id=$1 WHERE id=$2", target_user_id, session_id,
    )
    return {"status": "success", "sessionId": session_id, "newOwnerId": target_user_id}



# ── Auto-name endpoint ──────────────────────────────────────────


@router.post("/sessions/{session_id}/auto-name")
async def auto_name_session(session_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Generate a session name automatically from conversation content."""
    access = await check_session_access(session_id, user)
    if not access.can_write:
        raise HTTPException(status_code=403, detail="No permission to rename this session")

    session = await afetch_one("SELECT id, name FROM sessions WHERE id=$1", session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = await list_messages(session_id)
    if not msgs or len(msgs) < 2:
        return {"status": "skipped", "reason": "Not enough messages", "sessionId": session_id}

    prompt = build_auto_name_prompt(msgs)
    if not prompt:
        return {"status": "skipped", "reason": "No message content", "sessionId": session_id}

    name = await _call_llm_for_name(prompt)
    if not name:
        # LLM failed — use local keyword extraction from first user message
        user_msgs = [m for m in msgs if m.get("sender") not in ("system", "agent", "orchestrator")]
        first_msg = (user_msgs[0].get("content") or "").strip() if user_msgs else ""
        if first_msg:
            name = extract_local_title(first_msg)
    if not name:
        return {"status": "skipped", "reason": "LLM call failed", "sessionId": session_id}

    await aexecute("UPDATE sessions SET name=$1 WHERE id=$2", name, session_id)
    return {"status": "success", "sessionId": session_id, "name": name}



@router.post("/tasks")
async def create_task(data: ChatTaskRequest, user: dict = Depends(get_current_user)) -> dict:
    if not data.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    access = await check_session_access(data.sessionId, user)
    if not access.can_write:
        raise HTTPException(status_code=403, detail="No permission to send messages in this session")
    return await task_state_machine.create_task(data.sessionId, data.message)


@router.get("/workflows")
async def list_workflows() -> list[dict]:
    rows = await afetch_all(
        "SELECT id,name,description,trigger_keywords FROM agent_routes WHERE active=1 ORDER BY is_default DESC, updated_at DESC"
    )
    for r in rows:
        import json
        r["triggerKeywords"] = json.loads(r.pop("trigger_keywords", "[]") or "[]")
        r["routeId"] = r.pop("id")
    return rows