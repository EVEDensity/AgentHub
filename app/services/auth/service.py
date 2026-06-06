from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import DEFAULT_USER_ID
from app.db.init_db import now
from app.db.session import afetch_one, aexecute
from app.services.secret_service import _SECRET

security = HTTPBearer(auto_error=False)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or uuid.uuid4().hex
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _, salt, expected = password_hash.split("$", 2)
    except ValueError:
        return False
    actual = hash_password(password, salt).split("$", 2)[2]
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class AuthService:
    @staticmethod
    def create_access_token(user: dict, expires_in: int = 60 * 60 * 8) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"sub": user["id"], "name": user["name"], "role": user["role"], "exp": int(time.time()) + expires_in}
        signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
        signature = hmac.new(_SECRET, signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_b64(signature)}"

    @staticmethod
    def decode_access_token(token: str) -> dict:
        try:
            header_b64, payload_b64, signature_b64 = token.split(".")
            signing_input = f"{header_b64}.{payload_b64}"
            expected = _b64(hmac.new(_SECRET, signing_input.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(signature_b64, expected):
                raise ValueError("bad signature")
            payload = json.loads(_unb64(payload_b64))
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Invalid token") from exc
        if payload.get("exp", 0) < int(time.time()):
            raise HTTPException(status_code=401, detail="Token expired")
        return payload

    @staticmethod
    async def create_user(name: str, password: str, role: str = "developer") -> dict:
        if not name.strip() or not password:
            raise HTTPException(status_code=400, detail="name and password are required")
        user_id = uuid.uuid4().hex
        existing = await afetch_one("SELECT id FROM users WHERE name=$1", name)
        if existing:
            raise HTTPException(status_code=409, detail="User already exists")
        await aexecute(
            "INSERT INTO users(id,name,role,password_hash,created_at) VALUES($1,$2,$3,$4,$5)",
            user_id, name, role, hash_password(password), now(),
        )
        return {"id": user_id, "name": name, "role": role}

    @staticmethod
    async def authenticate_user(name: str, password: str) -> dict:
        user = await afetch_one("SELECT id,name,role,password_hash,created_at FROM users WHERE name=$1", name)
        if not user or not verify_password(password, user.get("password_hash") or ""):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        return {"id": user["id"], "name": user["name"], "role": user["role"], "created_at": user["created_at"]}

    @staticmethod
    async def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
        if credentials and credentials.scheme.lower() == 'bearer':
            payload = AuthService.decode_access_token(credentials.credentials)
            user = await afetch_one('SELECT id,name,role,created_at FROM users WHERE id=$1', payload['sub'])
            if user:
                return user
        raise HTTPException(status_code=401, detail='Authentication required')

    @staticmethod
    def require_admin(user: dict) -> None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin permission required")

    @staticmethod
    async def websocket_user(token: str | None) -> dict:
        if token:
            payload = AuthService.decode_access_token(token)
            user = await afetch_one('SELECT id,name,role,created_at FROM users WHERE id=$1', payload['sub'])
            if user:
                return user
        raise HTTPException(status_code=401, detail='Authentication required')

    @staticmethod
    def write_audit(user_id: str, agent_id: str, action: str, risk_level: str, decision: str, payload: dict) -> str:
        """Write an audit log entry (best-effort, non-fatal, fire-and-forget).

        Schedules the actual DB write as a background task so callers are
        never blocked by audit logging.  Returns the audit_id immediately.
        """
        import logging
        _log = logging.getLogger("agenthub.auth")
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        audit_id = str(uuid.uuid4())
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                _write_audit_async(
                    audit_id, user_id, agent_id, action, risk_level,
                    decision, content, _log,
                )
            )
        except RuntimeError:
            # No running event loop — execute synchronously in a new loop
            try:
                asyncio.run(
                    _write_audit_async(
                        audit_id, user_id, agent_id, action, risk_level,
                        decision, content, _log,
                    )
                )
            except Exception:
                pass
        return audit_id


# ── Module-level aliases (backward compatibility) ──────────────────
# Older code (e.g. app/api/artifacts.py) imports these as module-level
# names. Mirror what app/services/auth_service.py already does so both
# import styles keep working.
get_current_user = AuthService.get_current_user
require_admin = AuthService.require_admin
websocket_user = AuthService.websocket_user
write_audit = AuthService.write_audit
create_user = AuthService.create_user
authenticate_user = AuthService.authenticate_user
create_access_token = AuthService.create_access_token
decode_access_token = AuthService.decode_access_token


async def _write_audit_async(
    audit_id: str,
    user_id: str,
    agent_id: str,
    action: str,
    risk_level: str,
    decision: str,
    content: str,
    _log,
) -> None:
    """Internal async helper for write_audit."""
    try:
        await aexecute(
            "INSERT INTO audit_log(id,user_id,agent_id,action,risk_level,decision,content_hash,payload_json,timestamp) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
            audit_id, user_id, agent_id, action, risk_level, decision,
            hashlib.sha256(content.encode()).hexdigest(), content, now(),
        )
    except Exception:
        _log.warning("write_audit failed for user=%s action=%s (non-fatal)", user_id, action, exc_info=True)
