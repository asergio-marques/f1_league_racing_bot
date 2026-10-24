"""Whether a round's submission may take anything more, read on a save's own connection (#439,
slice 8, F2).

A session's save reads again, inside its own save, whether a cancellation has closed its round's
submission meanwhile, so `closed_by_cancellation_on(db, round_id)` reads on the connection it is
handed: a cancellation written in that save and not yet committed is seen. The league is
`ongoing_league`'s: Pro's round 3 awaits its results, its submission open.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection
from tests.support.season_league import PRO, SEASON_ID, ongoing_league, open_submission, round_id

R3 = round_id(PRO, 3)
CANCELLED = (
    "⛔ Round 3 has been cancelled, so its results can no longer be entered. Nothing was saved."
)
CLOSED = (
    "⛔ Round 3's results submission has been closed, so its results can no longer be entered. "
    "Nothing was saved."
)

_CASES = {
    "round cancelled": ("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", R3, CANCELLED),
    "division cancelled": ("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", PRO,
                           CANCELLED),
    "season cancelled": ("UPDATE seasons SET status = 'CANCELLED' WHERE id = ?", SEASON_ID,
                         CANCELLED),
    "submission closed": ("UPDATE round_submission_channels SET closed = 1 WHERE round_id = ?",
                          R3, CLOSED),
    "open": (None, None, None),
}


@pytest.mark.parametrize("case", list(_CASES))
async def test_closed_by_cancellation_on_reads_the_connection_handed(tmp_path, case):
    """Pro's round 3 awaits its results, its submission open. On one connection the round, its
    division or its season is recorded cancelled, or its submission closed, or nothing is
    written, and nothing is committed. Read on that same connection, the round is said cancelled,
    or its submission closed, in the wizard's words; with nothing written it is open. Read on
    another connection, the uncommitted write is not seen."""
    from leaguebot.results.services.result_submission_service import closed_by_cancellation_on

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE rounds SET status = 'AWAITING_RESULTS' WHERE id = ?", R3)
    await open_submission(league, R3)
    sql, key, expected = _CASES[case]

    async with get_connection(league.db_path) as db:
        if sql is not None:
            await db.execute(sql, (key,))
        assert await closed_by_cancellation_on(db, R3) == expected
        async with get_connection(league.db_path) as other:
            assert await closed_by_cancellation_on(other, R3) is None
        await db.rollback()
