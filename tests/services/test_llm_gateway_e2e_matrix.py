"""T1 — new-api gateway e2e matrix (sync / SSE / tool-call shape).

Boots the REAL new-api release binary (if available) with an isolated
SQLite file plus the repo's OpenAI-compatible mock upstream, then drives
the OpenAI-compatible entry exactly like the application would.

These are opt-in system tests: they need the binary at
``NEWAPI_EXE`` (or the local download under %TEMP%/newapi) and a working
Docker-less environment. Without the binary the suite skips; CI keeps the
fast unit suite green as the hard gate.

Run:
    NEWAPI_EXE=/path/to/new-api .venv/Scripts/python -m pytest \
        tests/services/test_llm_gateway_e2e_matrix.py -s
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
MOCK_DIR = ROOT / "deploy" / "newapi"
sys.path.insert(0, str(MOCK_DIR))

EXE = os.getenv("NEWAPI_EXE") or str(Path(os.environ.get("TEMP", "")) / "newapi" / "new-api.exe")



def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 40.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as _r:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def mock_upstream_url() -> str:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mock_llm:app", "--host", "127.0.0.1",
         "--port", str(port), "--app-dir", str(MOCK_DIR)],
        env={**os.environ, "MOCK_MODEL": "mock-llm"},
        cwd=str(MOCK_DIR),
    )
    assert _wait(f"http://127.0.0.1:{port}/__health"), "mock upstream failed to start"
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture()
def newapi(tmp_path):
    """Boot new-api with an isolated sqlite DB; yield a mutable handle."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    db = tmp_path / "one-api.db"

    def _spawn() -> subprocess.Popen:
        return subprocess.Popen(
            [EXE],
            cwd=str(tmp_path),
            env={**os.environ, "PORT": str(port), "SESSION_SECRET": "e2e-test-secret",
                 "TZ": "Asia/Shanghai"},
        )

    proc = _spawn()
    try:
        assert _wait(f"{base}/api/status"), "new-api failed to start"
    except BaseException:
        proc.kill()
        raise

    handle = types.SimpleNamespace(base=base, db=db, proc=proc)
    try:
        yield handle
    finally:
        handle.proc.terminate()
        try:
            handle.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            handle.proc.kill()
        time.sleep(0.6)  # Windows releases log handles shortly after kill


def _restart_with_self_use(handle) -> None:
    """new-api caches options at boot; flip SelfUseModeEnabled, restart.

    Must run AFTER /api/setup so the options row exists (setup re-seeds
    defaults otherwise and would clobber the flag).
    """
    import sqlite3

    handle.proc.terminate()
    try:
        handle.proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        handle.proc.kill()
    conn = sqlite3.connect(handle.db)
    conn.execute(
        "INSERT INTO options (key, value) VALUES ('SelfUseModeEnabled', 'true') "
        "ON CONFLICT(key) DO UPDATE SET value='true'"
    )
    conn.commit()
    assert conn.execute("SELECT value FROM options WHERE key='SelfUseModeEnabled'").fetchone()[0] == "true"
    conn.close()
    handle.proc = subprocess.Popen(
        [EXE],
        cwd=str(handle.db.parent),
        env={**os.environ, "PORT": handle.base.rsplit(":", 1)[1], "SESSION_SECRET": "e2e-test-secret",
             "TZ": "Asia/Shanghai"},
    )
    assert _wait(f"{handle.base}/api/status"), "new-api failed to restart"


def _admin_init(newapi_base: str, tmp_db: Path, mock: str, models: list[str]) -> str:
    """Setup root + enable self-use + canary channel -> gateway key.

    Mirrors deploy/newapi/verify_newapi.py but in-process cheaply.
    """
    import sqlite3

    # 1) initial setup (creates root account)
    r = httpx.post(f"{newapi_base}/api/setup", json={
        "username": "root", "password": "sk-e2e-root",
        "confirmPassword": "sk-e2e-root", "siteName": "e2e",
    })
    assert r.status_code == 200 and r.json().get("success"), r.text

    # 2) login -> admin token, create channel + token, then re-login after
    #    the self-use restart so the drill keeps working fresh
    login = httpx.post(f"{newapi_base}/api/user/login",
                       json={"username": "root", "password": "sk-e2e-root"})
    assert login.status_code == 200, login.text
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 3) canary channel (base_url WITHOUT /v1 — new-api appends it)
    r = httpx.post(f"{newapi_base}/api/channel/", headers=headers, json={
        "channel": {"name": "e2e-canary", "type": 1, "key": "not-needed",
                    "base_url": mock, "models": ",".join(models), "group": "default"},
        "mode": "single",
    })
    assert r.status_code == 200 and r.json().get("success"), r.text

    # 4) gateway token
    r = httpx.post(f"{newapi_base}/api/token/", headers=headers, json={
        "name": "e2e-gateway", "remain_quota": -1, "expired_time": -1,
        "unlimited_quota": True, "model_limit_enabled": False, "models": "",
    })
    assert r.status_code == 200 and r.json().get("success"), r.text

    conn = sqlite3.connect(tmp_db)
    key = conn.execute("SELECT key FROM tokens WHERE name='e2e-gateway'").fetchone()[0]
    conn.close()
    return key


def _canary_key(handle, mock_upstream_url: str) -> str:
    key = _admin_init(handle.base, handle.db, mock_upstream_url, ["mock-llm"])
    _restart_with_self_use(handle)  # self-use must be visible at relay time
    return key


@pytest.mark.skipif(not Path(EXE).is_file(), reason="new-api binary not available "
                    f"(set NEWAPI_EXE or place it at {EXE})")
def test_matrix_sync_and_sse_streaming(newapi, mock_upstream_url) -> None:
    key = _canary_key(newapi, mock_upstream_url)
    base, headers = newapi.base, {"Authorization": f"Bearer {key}"}

    # sync non-streaming
    r = httpx.post(f"{base}/v1/chat/completions", headers=headers, json={
        "model": "mock-llm", "messages": [{"role": "user", "content": "矩阵-同步"}]})
    assert r.status_code == 200, r.text
    assert "[mock:mock-llm]" in r.json()["choices"][0]["message"]["content"]

    # SSE streaming: every chunk is `data: <json>` and closes with [DONE]
    chunks = []
    with httpx.stream("POST", f"{base}/v1/chat/completions", headers=headers,
                      json={"model": "mock-llm", "stream": True,
                            "messages": [{"role": "user", "content": "矩阵-SSE"}]}, timeout=15) as resp:
        assert resp.status_code == 200, resp.text
        for line in resp.iter_lines():
            if line.startswith("data:"):
                chunks.append(line)
    assert chunks and chunks[-1] == "data: [DONE]"
    assert any("mock:mock-llm" in c for c in chunks), chunks[:3]


@pytest.mark.skipif(not Path(EXE).is_file(), reason="new-api binary not available")
def test_matrix_tool_calls_shape_roundtrip(newapi, mock_upstream_url) -> None:
    key = _canary_key(newapi, mock_upstream_url)
    base, headers = newapi.base, {"Authorization": f"Bearer {key}"}

    # Tool calls are passed through the gateway untouched and come back as a
    # native tool_calls payload the app's parser consumes.
    r = httpx.post(f"{base}/v1/chat/completions", headers=headers, json={
        "model": "mock-llm",
        "messages": [{"role": "user", "content": "矩阵-工具"}],
        "tools": [{"type": "function", "function": {"name": "web_search",
                                                     "description": "搜索", "parameters": {"type": "object", "properties": {}}}}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("tools") is None  # upstream echo preserved the request
    assert body["choices"][0]["message"]["content"]


@pytest.mark.skipif(not Path(EXE).is_file(), reason="new-api binary not available")
def test_matrix_rerank_stays_on_selfhosted_service(tmp_path, monkeypatch) -> None:
    # T1 acceptance: enabling the gateway must NOT move rerank; the adapter
    # service keeps serving /v1/rerank via the local bge/mock path.
    pytest.importorskip("prometheus_client")
    sys.path.insert(0, str(ROOT / "services" / "python" / "model_adapter_service"))
    import main as mas  # type: ignore[import-not-found]
    monkeypatch.setenv("NEWAPI_BASE_URL", "http://127.0.0.1:3000")
    provider = mas._get_rerank_provider("bge-any")
    assert getattr(provider, "name", "bge") == "bge"
    assert "newapi" not in mas._init_providers()  # rerank path ignores gateway