"""In-process desktop local runner (R1) — public facade.

When ``AGENTHUB_DESKTOP_LOCAL_RUNNER=1`` the Mission Control process starts
a :class:`DesktopLocalRunnerController` (see
docs/internal/architecture/desktop-local-runner-plan.md §2):

- a :class:`RunnerWorker` polls workspace ``local-admin`` through the
  self-hosted HTTP API with an authenticated admin token, keeping the
  "the executor never owns state" boundary intact;
- claimed ``desktop.task`` WorkUnits execute through the function-calling
  Harness with the model configured in the admin model table and the fixed
  desktop file-tool whitelist;
- a derivation loop creates exactly one ``desktop.task`` root WorkUnit per
  RUNNING manual Mission so desktop-created Missions become claimable;
- an unattended verification loop discovers VERIFYING items through the
  Mission Control verifier API, checks the registered Artifact bytes and
  submits PASS/FAIL Evidence so deterministic missions finish on their own.

The whole controller is env-gated and defaults to off; production and
server deployments never construct it.

The implementation lives in the :mod:`app.services.runner` package; this
module stays as the historical import surface (tests and callers import
``from app.services.desktop_local_runner import ...`` unchanged).
"""

from __future__ import annotations

from app.services.runner import *  # noqa: F401,F403
from app.services.runner import __all__ as _runner_all

__all__ = list(_runner_all)
