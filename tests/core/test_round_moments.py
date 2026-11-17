"""A round's stored moment, read back.

When each of a round's timed events falls is each module's to say, in the leads it hands core
(`round_leads_read`): those are pinned where the modules answer them, in `test_entry_point.py`.

A round's moment is stored in one form, ISO 8601 with its UTC offset, and read back by one reader,
`read_scheduled_at`, which hands an aware UTC moment and refuses a stored value with no offset.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

#: Pro's round, on 14 June 2026 at 14:00 UTC.
MOMENT = datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("2026-06-14T14:00:00+00:00", id="in UTC"),
        pytest.param("2026-06-14T16:00:00+02:00", id="with another offset"),
    ],
)
def test_a_stored_round_moment_is_read_as_an_aware_utc_moment(stored):
    """Pro's round is stored at 14:00 UTC on 14 June 2026, written with its offset. The one reader
    of a round's stored moment hands back 14:00 UTC, aware, whatever offset it was written in."""
    from leaguebot.core.models.round import read_scheduled_at

    moment = read_scheduled_at(stored)

    assert moment == MOMENT
    assert moment.utcoffset() == timedelta(0)


def test_a_stored_round_moment_without_its_offset_is_refused():
    """A round's moment stored as "2026-06-14T14:00:00", with no offset, is a form no writer
    produces: the one reader refuses it with a ValueError rather than guess its zone."""
    from leaguebot.core.models.round import read_scheduled_at

    with pytest.raises(ValueError):
        read_scheduled_at("2026-06-14T14:00:00")
