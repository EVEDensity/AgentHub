# Legacy import boundary

This package maps immutable legacy snapshots into Mission domain values. It
does not persist state, dispatch work, or synchronize Task and Mission lifecycles.
Callers supply workspace, Contract, actor, and explicit timezone for naive dates.
Legacy SUCCESS maps to VERIFYING: independent Evidence is still required before
Mission success. Unknown fields and statuses fail validation.

Verified by `tests/compat/test_legacy_tasks.py`.
