from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from fastapi import Depends, Header, HTTPException, Query, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import DEFAULT_USER_ID
from app.db.init_db import now
from app.db.session import get_connection, one_row
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


def create_access_token(user: dict, expires_in: int = 60 * 60 * 8) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user["id"], "name": user["name"], "role": user["role"], "exp": int(time.time()) + expires_in}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(_SECRET, signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


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


def create_user(name: str, password: str, role: str = "developer") -> dict:
    if not name.strip() or not password:
        raise HTTPException(status_code=400, detail="name and password are required")
    user_id = uuid.uuid4().hex
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User already exists")
        conn.execute("INSERT INTO users(id,name,role,password_hash,created_at) VALUES(?,?,?,?,?)", (user_id, name, role, hash_password(password), now()))
    return {"id": user_id, "name": name, "role": role}


def authenticate_user(name: str, password: str) -> dict:
    user = one_row("SELECT id,name,role,password_hash,created_at FROM users WHERE name=?", (name,))
    if not user or not verify_password(password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"id": user["id"], "name": user["name"], "role": user["role"], "created_at": user["created_at"]}


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials and credentials.scheme.lower() == 'bearer':
        payload = decode_access_token(credentials.credentials)
        user = one_row('SELECT id,name,role,created_at FROM users WHERE id=?', (payload['sub'],))
        if user:
            return user
    raise HTTPException(status_code=401, detail='Authentication required')

def require_admin(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")


def websocket_user(token: str | None) -> dict:
    if token:
        payload = decode_access_token(token)
        user = one_row('SELECT id,name,role,created_at FROM users WHERE id=?', (payload['sub'],))
        if user:
            return user
    raise HTTPException(status_code=401, detail='Authentication required')

def write_audit(user_id: str, agent_id: str, action: str, risk_level: str, decision: str, payload: dict) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    audit_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log(id,user_id,agent_id,action,risk_level,decision,content_hash,payload_json,timestamp) VALUES(?,?,?,?,?,?,?,?,?)",
            (audit_id, user_id, agent_id, action, risk_level, decision, hashlib.sha256(content.encode()).hexdigest(), content, now()),
        )
    return audit_id
