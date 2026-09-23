"""`split_validation` and `_rows_of_kind` — reading `validate_submission_block`'s result (#228).

The validator returns either its errors or its rows, and parses every row of a session as the
one kind the session is. These say so to the type check, and raise by name rather than silently
drop a row of the other kind.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.result_submission_service import (
    ParsedQualifyingRow,
    ParsedRaceRow,
    _rows_of_kind,
    split_validation,
)


def _qualifying_row() -> ParsedQualifyingRow:
    row = MagicMock(spec=ParsedQualifyingRow)
    row.__class__ = ParsedQualifyingRow
    return row


def _race_row() -> ParsedRaceRow:
    row = MagicMock(spec=ParsedRaceRow)
    row.__class__ = ParsedRaceRow
    return row


def test_errors_come_back_as_errors_and_no_rows():
    assert split_validation(["Row 1: bad", "Row 2: worse"]) == (["Row 1: bad", "Row 2: worse"], [])


def test_rows_come_back_as_rows_and_no_errors():
    rows = [_qualifying_row(), _qualifying_row()]
    assert split_validation(rows) == ([], rows)


def test_rows_of_one_kind_are_handed_back_whole():
    rows = [_race_row(), _race_row()]
    assert _rows_of_kind(rows, ParsedRaceRow) == rows


def test_a_row_of_the_other_kind_is_raised_by_name_not_dropped():
    with pytest.raises(RuntimeError, match="held a row of another kind"):
        _rows_of_kind([_race_row(), _qualifying_row()], ParsedRaceRow)
