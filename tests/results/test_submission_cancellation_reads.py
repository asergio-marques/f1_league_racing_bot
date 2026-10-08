"""What a cancellation reads of a round's results submission, and how it closes it (#439, slice 4b,
amendment A).

A round, its division or its season may be cancelled while the round's results submission stands
open only where the submission has accepted nothing: neither a session's results nor a session
entered as not held. Results reports each open submission among the rounds it is handed, with
whether it has accepted anything (`open_submissions`, and `open_submissions_on` on a save's
connection), and closes them on the save a cancellation hands it (`close_submissions_on`),
committing nothing.

Each reader and writer is imported inside its test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Pro's rounds 1 to 3, and the channel each one's submission stands in.
ROUND_ID, SECOND_ROUND_ID, THIRD_ROUND_ID = 31, 32, 33
CHANNELS = {ROUND_ID: 690, SECOND_ROUND_ID: 691, THIRD_ROUND_ID: 692}

XFAIL = "#439: results has no reader or closer of the open submissions a cancellation meets"


async def _make_db(tmp_path) -> str:
    """Pro's rounds 1 to 3 (ids 31 to 33), each awaiting its results, none with a submission."""
    db_path = os.path.join(str(tmp_path), "submission_reads.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 3, '2026-11-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 801)"
        )
        for number, round_id in enumerate(CHANNELS, start=1):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, 1, ?, 'NORMAL', 'Monza', ?, "
                "'AWAITING_RESULTS')",
                (round_id, number, f"2026-11-0{number}T18:00:00+00:00"),
            )
        await db.commit()
    return db_path


async def _submission(db_path: str, round_id: int, *, closed: bool = False) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
            "VALUES (?, ?, '2026-11-01T20:00:00+00:00', ?)",
            (round_id, CHANNELS[round_id], int(closed)),
        )
        await db.commit()


async def _session(db_path: str, round_id: int, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, submitted_by, submitted_at) VALUES (?, 1, 'FEATURE_RACE', ?, "
            "'Standard', 77, '2026-11-01T21:00:00+00:00')",
            (round_id, status),
        )
        await db.commit()


async def _closed(db, round_id: int) -> int:
    cursor = await db.execute(
        "SELECT closed FROM round_submission_channels WHERE round_id = ?", (round_id,)
    )
    return (await cursor.fetchone())[0]


def _read(found) -> list[tuple]:
    return [(each.round_id, each.channel_id, each.accepted) for each in found]


@pytest.mark.xfail(strict=True, reason=XFAIL)
@pytest.mark.parametrize(("case", "expected"), [
    pytest.param("nothing accepted", [(ROUND_ID, 690, False)], id="nothing accepted"),
    pytest.param("ACTIVE", [(ROUND_ID, 690, True)], id="a session's results accepted"),
    pytest.param("CANCELLED", [(ROUND_ID, 690, True)], id="a session entered as not held"),
    pytest.param("closed", [], id="the submission closed"),
])
async def test_an_open_submission_counts_as_accepted_once_any_session_is_saved(
    tmp_path, case, expected,
):
    """Round 1's submission (channel 690) stands open with nothing accepted, or with a session's
    results accepted, or with a session entered as not held; or it has been closed, a session
    accepted. Round 2's submission stands open too, but round 2 is not asked about, and round 3
    has none. Asked about rounds 1 and 3, both readers give round 1's submission alone, accepted
    once any session of it is saved, and nothing for a closed submission."""
    from leaguebot.results.services.result_submission_service import (
        open_submissions,
        open_submissions_on,
    )

    db_path = await _make_db(tmp_path)
    await _submission(db_path, ROUND_ID, closed=case == "closed")
    await _submission(db_path, SECOND_ROUND_ID)
    await _session(db_path, SECOND_ROUND_ID, "ACTIVE")
    if case in ("ACTIVE", "CANCELLED"):
        await _session(db_path, ROUND_ID, case)
    elif case == "closed":
        await _session(db_path, ROUND_ID, "ACTIVE")

    assert _read(await open_submissions(db_path, [ROUND_ID, THIRD_ROUND_ID])) == expected
    async with get_connection(db_path) as db:
        assert _read(await open_submissions_on(db, [ROUND_ID, THIRD_ROUND_ID])) == expected


@pytest.mark.xfail(strict=True, reason=XFAIL)
async def test_closing_the_submissions_writes_on_the_connection_handed_and_commits_nothing(
    tmp_path,
):
    """Rounds 1 and 2 each have an open submission (channels 690 and 691); round 3's (692) is
    closed already. Closing rounds 1 and 3 on a connection gives round 1 and its channel alone,
    marks round 1's submission closed on that connection and leaves round 2's open; rolled back,
    a fresh connection finds round 1's submission open again, for nothing was committed."""
    from leaguebot.results.services.result_submission_service import close_submissions_on

    db_path = await _make_db(tmp_path)
    await _submission(db_path, ROUND_ID)
    await _submission(db_path, SECOND_ROUND_ID)
    await _submission(db_path, THIRD_ROUND_ID, closed=True)

    async with get_connection(db_path) as db:
        closed = await close_submissions_on(db, [ROUND_ID, THIRD_ROUND_ID])
        assert [tuple(each) for each in closed] == [(ROUND_ID, 690)]
        assert await _closed(db, ROUND_ID) == 1
        assert await _closed(db, SECOND_ROUND_ID) == 0
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _closed(db, ROUND_ID) == 0
        assert await _closed(db, THIRD_ROUND_ID) == 1
