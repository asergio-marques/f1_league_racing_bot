"""The amendment paste takes the submission format, not two extra sanction columns (#345).

Amending a round used to require two further columns carrying the post-race and appeal
penalties, so that a re-inserted classification kept the sanctions already applied rather than
losing them. That was the best available answer while the amendment was a single paste.

The replay makes it wrong. `apply_penalties` **adds** to the stored penalty columns, and the
replay re-inserts the driver rows with those columns at zero before running the round's report
and appeal stages over them — so the staged verdicts reproduce the totals exactly. A paste that
also carried the sanctions would have each one applied twice.

It is also a worse answer on its own terms. A pasted time penalty produced no verdict record, so
it had no justification, no author and no announcement — an unaudited sanction indistinguishable
from a reviewed one. A pasted `DSQ` was worse still: the post-race and appeal disqualification
marks are drawn from the verdict tables alone, so it set the outcome and left the column blank.

So the columns are refused rather than ignored, and the refusal says what to do instead — an
admin working from a saved paste learns the format changed rather than watching a silent
mis-scoring.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    _RETIRED_SANCTION_COLUMNS,
    validate_submission_block,
)

DRIVER = 101
TEAM = 3001


def _qualifying(*, sanction_columns: bool) -> str:
    row = f"1, <@{DRIVER}>, <@&{TEAM}>, Soft, 1:23.456, -"
    return row + (", N/A, N/A" if sanction_columns else "")


def _race(*, sanction_columns: bool) -> str:
    row = f"1, <@{DRIVER}>, <@&{TEAM}>, 1:23:45.678, 1:23.456, N/A"
    return row + (", N/A, N/A" if sanction_columns else "")


def _validate(line: str, session_type: SessionType):
    return validate_submission_block(
        [line], session_type, {DRIVER}, {TEAM: TEAM}, None, {DRIVER: TEAM},
    )


def test_a_race_row_in_the_submission_format_is_accepted():
    """The format the amendment now takes, and the one a first submission has always taken."""
    result = _validate(_race(sanction_columns=False), SessionType.FEATURE_RACE)

    assert not isinstance(result[0], str), result
    assert result[0].driver_user_id == DRIVER


def test_a_qualifying_row_in_the_submission_format_is_accepted():
    result = _validate(_qualifying(sanction_columns=False), SessionType.FEATURE_QUALIFYING)

    assert not isinstance(result[0], str), result
    assert result[0].driver_user_id == DRIVER


def test_a_race_row_with_the_old_sanction_columns_is_refused():
    """Ignoring them would score the round twice over; the paste is rejected instead."""
    result = _validate(_race(sanction_columns=True), SessionType.FEATURE_RACE)

    assert isinstance(result[0], str)
    assert "got 8" in result[0]


def test_a_qualifying_row_with_the_old_sanction_columns_is_refused():
    result = _validate(_qualifying(sanction_columns=True), SessionType.FEATURE_QUALIFYING)

    assert isinstance(result[0], str)
    assert "got 8" in result[0]


def test_the_refusal_says_what_to_do_instead():
    """An admin working from a saved paste has to learn the format changed, and why.

    A bare field count would read as a typo and be re-pasted unchanged.
    """
    result = _validate(_race(sanction_columns=True), SessionType.FEATURE_RACE)

    assert "same format as a first submission" in result[0]
    assert "justification" in result[0]


def test_the_explanation_is_only_offered_for_the_old_width():
    """A row of some other wrong width is a typo, not the retired format.

    Naming the sanction columns there would send somebody looking for columns they never had.
    """
    short = _validate(f"1, <@{DRIVER}>, <@&{TEAM}>", SessionType.FEATURE_RACE)

    assert isinstance(short[0], str)
    assert "got 3" in short[0]
    assert _RETIRED_SANCTION_COLUMNS not in short[0]


def test_a_row_two_columns_too_wide_in_qualifying_also_explains():
    """Both formats lost the same pair, so both rows point at the same explanation."""
    result = _validate(_qualifying(sanction_columns=True), SessionType.FEATURE_QUALIFYING)

    assert _RETIRED_SANCTION_COLUMNS in result[0]


def test_a_driver_listed_twice_is_refused():
    """Reviewed as a suspected mis-pointing of verdicts; it does not reach that far (#345).

    `_repoint_verdicts` builds `{driver_user_id: row_id}` from the re-inserted rows, so a driver
    appearing twice would silently attach their verdict to whichever row was inserted last. The
    validation above refuses the paste first, on every path including the amendment's — so the
    mis-pointing is unreachable rather than merely unlikely.

    Pinned here because the two pieces of code are far apart: a future change to either could
    open the gap without anything obviously breaking.
    """
    rows = [
        f"1, <@{DRIVER}>, <@&{TEAM}>, 1:23:45.678, 1:23.456, N/A",
        f"2, <@{DRIVER}>, <@&{TEAM}>, +1.000, 1:24.000, N/A",
    ]

    result = validate_submission_block(
        rows, SessionType.FEATURE_RACE, {DRIVER}, {TEAM: TEAM}, None, {DRIVER: TEAM},
    )

    assert isinstance(result[0], str)
    assert "appears more than once" in result[0]
