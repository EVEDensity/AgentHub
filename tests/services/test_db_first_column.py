"""First-column extraction for ``aexecute_insert`` across both DB backends.

Regression guard for the desktop sqlite path: the sqlite wrapper returns
plain dicts while asyncpg returns positional Records, so ``row[0]`` raised
``KeyError: 0`` on every desktop INSERT ... RETURNING (e.g. saving a model
configuration from the shell).
"""

from __future__ import annotations

from app.db.session import _first_column


class _FakeRecord:
    """Mimics asyncpg Record positional access."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __getitem__(self, index: int) -> str:
        if index != 0:
            raise IndexError(index)
        return self._value


def test_dict_row_uses_first_value():
    assert _first_column({"id": 42, "name": "x"}) == "42"


def test_record_row_uses_positional_access():
    assert _first_column(_FakeRecord("7")) == "7"


def test_empty_row_returns_empty_string():
    assert _first_column(None) == ""
    assert _first_column({}) == ""
