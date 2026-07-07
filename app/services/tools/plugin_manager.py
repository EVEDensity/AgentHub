# ─────────────────────────────────────────────────────────────────────
# PluginManager — pluggy wrapper for the AgentHub tool system (P0.2 — Sprint 5)
# ─────────────────────────────────────────────────────────────────────
# Wraps pluggy.PluginManager to provide three plugin loading mechanisms:
#   1. Builtin plugins  — shipped in app/services/tools/plugins/
#   2. Entry points     — third-party packages declaring group "agenthub.plugins"
#   3. Path loading     — PLUGINS_PATH env var (single .py file or directory)
#
# Loading is lazy: call load_all() once at startup. The hook property
# exposes the pluggy hook relay so callers can invoke:
#   plugin_manager.hook.pre_tool_use(tool_name=..., arguments=..., context=...)
#
# See docs/plugin-development.md for the full plugin development guide.
# ─────────────────────────────────────────────────────────────────────
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pluggy

from .plugin_spec import HOOK_NAMESPACE, ToolHookSpecs

logger = logging.getLogger("agenthub.tools.plugins")


class PluginManager:
    """Wraps pluggy.PluginManager for the AgentHub tool system.

    The wrapper centralises plugin discovery and registration so application
    code only depends on this module rather than pluggy internals. The
    underlying pluggy.PluginManager is exposed via the ``pm`` attribute and
    the ``hook`` property for advanced use cases.
    """

    def __init__(self) -> None:
        self.pm = pluggy.PluginManager(HOOK_NAMESPACE)
        self.pm.add_hookspecs(ToolHookSpecs)
        self._loaded: bool = False

    # ── Loading ────────────────────────────────────────────────────────

    def load_builtin_plugins(self) -> None:
        """Register the builtin plugins shipped with AgentHub.

        Idempotent: re-registering an already-registered plugin name is a
        no-op (pluggy raises ValueError, which we swallow).
        """
        from .plugins.builtin_audit import AuditPlugin
        from .plugins.builtin_permission import PermissionPlugin
        from .plugins.builtin_sanitize import SanitizePlugin

        builtins = [
            ("builtin.audit", AuditPlugin()),
            ("builtin.permission", PermissionPlugin()),
            ("builtin.sanitize", SanitizePlugin()),
        ]
        for name, instance in builtins:
            try:
                self.pm.register(instance, name=name)
            except ValueError:
                # Already registered — ignore (idempotent reload)
                logger.debug("plugin_manager: %s already registered, skipping", name)
            except Exception as exc:
                logger.warning("plugin_manager: failed to register %s: %s", name, exc)

    def load_entry_points(self) -> int:
        """Load third-party plugins declaring entry point group ``agenthub.plugins``.

        Returns the number of plugins loaded. Returns 0 if no entry points
        are declared or if importlib.metadata is unavailable.
        """
        try:
            return self.pm.load_setuptools_entrypoints(HOOK_NAMESPACE + ".plugins")
        except Exception as exc:
            logger.warning("plugin_manager: entry_point loading failed: %s", exc)
            return 0

    def load_from_path(self, path: str) -> int:
        """Load plugin(s) from a filesystem path.

        ``path`` may be:
          - A single ``.py`` file containing one or more plugin classes
            (classes with at least one ``@hookimpl``-decorated method).
          - A directory: every ``*.py`` file in it is loaded.

        Each discovered plugin class is instantiated (no-arg constructor)
        and registered with name ``user.<ClassName>``. Returns the number
        of plugins registered.
        """
        p = Path(path)
        if not p.exists():
            logger.warning("plugin_manager: PLUGINS_PATH %s does not exist", path)
            return 0

        files: list[Path]
        if p.is_file() and p.suffix == ".py":
            files = [p]
        elif p.is_dir():
            files = sorted(p.glob("*.py"))
        else:
            logger.warning("plugin_manager: PLUGINS_PATH %s is not a .py file or directory", path)
            return 0

        count = 0
        for f in files:
            try:
                count += self._load_file(f)
            except Exception as exc:
                logger.warning("plugin_manager: failed to load %s: %s", f, exc)
        return count

    def load_all(self) -> None:
        """Convenience: load builtin → entry_points → PLUGINS_PATH.

        Idempotent: subsequent calls are no-ops. Safe to call at startup
        and again in tests.
        """
        if self._loaded:
            return
        self._loaded = True

        self.load_builtin_plugins()

        n_ep = self.load_entry_points()
        if n_ep:
            logger.info("plugin_manager: loaded %d entry_point plugin(s)", n_ep)

        env_path = os.getenv("PLUGINS_PATH")
        if env_path:
            n_path = self.load_from_path(env_path)
            if n_path:
                logger.info("plugin_manager: loaded %d plugin(s) from %s", n_path, env_path)

    # ── Introspection ──────────────────────────────────────────────────

    def list_plugins(self) -> dict[str, str]:
        """Return ``{registered_name: plugin_class_name}`` for all plugins.

        Used by the admin API to display loaded plugins.
        """
        result: dict[str, str] = {}
        for name, plugin in self.pm.list_name_plugin():
            result[name] = type(plugin).__name__
        return result

    def is_registered(self, name: str) -> bool:
        """Check whether a plugin with the given registered name is loaded."""
        return self.pm.has_plugin(name)

    # ── Hook access ────────────────────────────────────────────────────

    @property
    def hook(self):
        """The pluggy hook relay. Call hook methods with keyword args:

            plugin_manager.hook.pre_tool_use(
                tool_name="code_execute",
                arguments={"code": "print(1)"},
                context={"tenant_id": "t1", "user_id": "u1"},
            )

        pluggy collects results from all registered implementations into a
        list (LIFO order by default). ``None`` results are filtered out by
        pluggy only when ``firstresult=True`` is set on the hookspec; our
        hookspecs do not use ``firstresult`` so callers receive the full
        list and must inspect it themselves.
        """
        return self.pm.hook

    # ── Internal helpers ───────────────────────────────────────────────

    def _load_file(self, f: Path) -> int:
        """Load a single .py file as a module and register any plugin classes."""
        mod_name = f"user_plugin_{f.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, f)
        if spec is None or spec.loader is None:
            logger.warning("plugin_manager: cannot create module spec for %s", f)
            return 0
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

        count = 0
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            obj = getattr(mod, attr_name)
            if not isinstance(obj, type):
                continue
            # Skip the hookspec class itself and imported symbols
            if obj is ToolHookSpecs:
                continue
            # A plugin class has at least one method decorated with @hookimpl.
            # pluggy marks decorated methods with the `_pluggy_hooks` attribute
            # on the function object; we check the class's methods.
            if not self._is_plugin_class(obj):
                continue
            try:
                instance = obj()
                reg_name = f"user.{attr_name}"
                self.pm.register(instance, name=reg_name)
                count += 1
                logger.info("plugin_manager: registered %s from %s", reg_name, f)
            except Exception as exc:
                logger.warning("plugin_manager: failed to register %s: %s", attr_name, exc)
        return count

    # Method names defined in ToolHookSpecs. A class that defines (or
    # overrides) any of these is treated as a plugin candidate. This is
    # robust across pluggy versions (the internal marker attribute name
    # has changed between releases), and matches how pluggy itself
    # discovers hooks — by method name matching the hookspec.
    _HOOK_METHOD_NAMES = frozenset({
        "pre_tool_use", "post_tool_use", "register_tools", "tool_categories",
    })

    @classmethod
    def _is_plugin_class(cls, candidate: type) -> bool:
        """Heuristic: a plugin class overrides at least one hookspec method.

        We check ``candidate.__dict__`` (and its MRO) for methods whose
        names match the hookspec. The hookspec class itself is excluded
        by the caller before reaching here.
        """
        for name in cls._HOOK_METHOD_NAMES:
            # Walk the MRO so subclasses inheriting hookimpls are detected
            for klass in candidate.__mro__:
                if name in klass.__dict__:
                    method = klass.__dict__[name]
                    if callable(method):
                        return True
                    break
        return False


# ── Module-level singleton ──────────────────────────────────────────────
# Importing code uses ``from app.services.tools.plugin_manager import plugin_manager``.
# Call ``plugin_manager.load_all()`` once at application startup.
plugin_manager = PluginManager()
