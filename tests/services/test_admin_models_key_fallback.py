"""Desktop Model API Key fallback for admin model creation (P1-a).

The desktop shell injects ``AGENTHUB_DESKTOP_MODEL_API_KEY`` into the local
Mission Control process from the OS credential store. ``resolve_model_api_key``
must prefer the request-provided key and only fall back to the desktop key
when the request omits one. No database involved — pure resolution contract.
"""

from __future__ import annotations

from app.api.admin.models import resolve_model_api_key


def test_request_key_wins_over_desktop_env(monkeypatch):
    monkeypatch.setenv("AGENTHUB_DESKTOP_MODEL_API_KEY", "desktop-secret")
    assert resolve_model_api_key("request-key") == "request-key"


def test_desktop_env_fills_missing_request_key(monkeypatch):
    monkeypatch.setenv("AGENTHUB_DESKTOP_MODEL_API_KEY", "desktop-secret")
    assert resolve_model_api_key("") == "desktop-secret"


def test_explicit_whitespace_is_not_treated_as_missing(monkeypatch):
    monkeypatch.setenv("AGENTHUB_DESKTOP_MODEL_API_KEY", "desktop-secret")
    # An explicit value always wins verbatim; whitespace trimming is the
    # caller's concern (the desktop injection side filters blank keys).
    assert resolve_model_api_key("   ") == "   "


def test_missing_everywhere_resolves_empty(monkeypatch):
    monkeypatch.delenv("AGENTHUB_DESKTOP_MODEL_API_KEY", raising=False)
    assert resolve_model_api_key("") == ""
