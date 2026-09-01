"""Lightweight dependency injection container.

Replaces module-level global singletons with a central registry so that:

- Dependencies are explicit (no more ``from x import manager`` at module scope)
- Tests can substitute mock implementations
- Multi-process deployments can create isolated instances

Usage::

    from app.core.container import container

    manager = container.ws_manager()
    adapter_mgr = container.adapter_manager()
    cfg = container.settings()

Pattern
-------
Module-level global singletons still exist for **backward compatibility**
(``websocket_manager.manager``, ``adapter_manager.adapter_manager``), but
new code should prefer the container.  Over time, migrate all callers to
``container.xxx()``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.adapter_manager import AdapterManager


class AppContainer:
    """Central registry for application-wide service instances.

    All accessors are lazy — instances are created on first call and
    cached for the lifetime of the process.  This is intentionally NOT
    a full IoC framework; it's a simple service locator that solves the
    "global singleton" anti-pattern without introducing a heavy DI library.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._settings = None
        self._adapter_manager = None

    # ── Settings ────────────────────────────────────────────────────

    def settings(self):
        """Return the validated application settings (singleton)."""
        if self._settings is None:
            from app.core.config import get_settings
            self._settings = get_settings()
        return self._settings

    # ── Adapter manager ─────────────────────────────────────────────

    def adapter_manager(self):
        """Return the LLM adapter manager (singleton)."""
        if self._adapter_manager is None:
            from app.services.adapter_manager import AdapterManager
            self._adapter_manager = AdapterManager()
        return self._adapter_manager


# Global container instance (single per process — replace in tests)
container = AppContainer()
