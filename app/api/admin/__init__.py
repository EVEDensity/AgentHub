"""Admin API package — modular admin endpoints with single-responsibility boundaries.

Each sub-module owns one functional domain:
  models       — LLM provider model configuration
  roles        — Role-to-model bindings
  chat_defaults — Default chat agent selection
  workflows    — Agent workflow/route CRUD
  users        — User listing
  audit        — Audit trail
  analytics    — Token usage statistics
"""

from app.api.admin.router import router

__all__ = ["router"]
