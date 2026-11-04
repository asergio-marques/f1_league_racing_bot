"""The final classification a season ends with (2026-09-08).

The final one is posted when a season completes: the standings and attendance sheet a league
already reads every round, under a different heading and with no message text — see
``leaguebot.core.models.classification_occasion.ClassificationOccasion``. The opening one, posted
when a season is approved, is results' and attendance's own, and tested with them.

What matters here is the orchestration: which sheets are posted for which division. What the
sheets *say* is covered by ``test_image_standings_service`` and ``test_attendance_sheet_posting``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services import season_classification_service as service
from tests.support.change_queue import in_a_job
from tests.support.season_classification import bot_on as _bot
from tests.support.season_classification import classification_posts as _opening_posts
from tests.support.season_classification import guild_double as _guild
from tests.support.season_classification import seed_season as _seed

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "classification.db")
    await run_migrations(path)
    return path


# ── The final classification ──────────────────────────────────────────────


async def test_the_final_is_drawn_against_the_last_round_with_results(db_path):
    """The final sheet *is* that round's classification, restated under its own heading. Div A's
    round 2 is its last round with results, as the completion's first save finds it: its final
    standings and final sheet are each drawn against round 2, as the final classification."""
    _season_id, division_ids = await _seed(db_path)
    second = await _second_round_with_results(db_path, division_ids[0])

    standings, attendance = AsyncMock(return_value=[]), AsyncMock(return_value=None)
    first, middle, last = _opening_posts(standings, attendance)
    inputs = _final_inputs(second, division_ids[0])
    with first, middle, last, inputs[0], inputs[1]:
        await service.post_final_standings(
            _bot(db_path), _guild(), db_path, division_ids[0], second, as_text=False
        )
        await service.post_final_sheet(
            _bot(db_path), _guild(), db_path, division_ids[0], second, as_text=False
        )

    assert standings.await_args.args[2] == second
    assert standings.await_args.args[3] == 2
    assert standings.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    assert attendance.await_args.args[3] == second
    assert (
        attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    )


# ── The final classification, one division at a time (#439, slice 5) ──────
#
# Each division's final standings and final sheet are jobs of the season's completion on the
# change queue, each raising where it cannot post, as the opening's are. The final standings
# are posted beside the last round's (results spec :561, image spec :1025: "Neither posting
# replaces a standings message nor has its ID recorded").


async def _second_round_with_results(path, division_id: int, *, message_id=None) -> int:
    """Round 2 of the division, with an accepted session and Lewis (500) leading its standings,
    the standings message *message_id* recorded against it."""
    async with get_connection(path) as db:
        cur = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00')",
            (division_id,),
        )
        round_id = cur.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points, standings_message_id, standings_message_ids) "
            "VALUES (?, ?, 500, 1, 25, ?, ?)",
            (
                round_id,
                division_id,
                message_id,
                None if message_id is None else f"[{message_id}]",
            ),
        )
        await db.commit()
    return round_id


async def _recorded_standings_ids(path, round_id: int) -> list:
    async with get_connection(path) as db:
        rows = await (
            await db.execute(
                "SELECT standings_message_id FROM driver_standings_snapshots "
                "WHERE round_id = ? ORDER BY id",
                (round_id,),
            )
        ).fetchall()
    return [row["standings_message_id"] for row in rows]


def _lewis(round_id: int, division_id: int):
    from leaguebot.results.models.standings_snapshot import DriverStandingsSnapshot

    return DriverStandingsSnapshot(
        id=0, round_id=round_id, division_id=division_id, driver_user_id=500,
        standing_position=1, total_points=25, finish_counts={}, first_finish_rounds={},
    )


def _final_inputs(round_id: int, division_id: int):
    """The drivers and teams of the round, as the final standings read them."""
    return (
        patch(
            "leaguebot.results.services.results_post_service.driver_standings_for_display",
            AsyncMock(return_value=[_lewis(round_id, division_id)]),
        ),
        patch(
            "leaguebot.results.services.standings_service.compute_team_standings",
            AsyncMock(return_value=[]),
        ),
    )


async def test_the_final_standings_stand_beside_the_last_round_s_and_replace_nothing(db_path):
    """Div A's round 2 has its standings posted as message 9100, recorded against it. The final
    standings are posted as text: a new message is sent beside it, and 9100 is neither edited
    nor deleted, its recorded id unchanged. Today the text path edits 9100 in place and records
    the final table against round 2 (`results_post_service.py:870`, `:902`)."""
    _season_id, division_ids = await _seed(db_path)
    division_id = division_ids[0]
    round_id = await _second_round_with_results(db_path, division_id, message_id=9100)
    old_message = MagicMock()
    old_message.edit = AsyncMock()
    old_message.delete = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=9200))
    channel.fetch_message = AsyncMock(return_value=old_message)
    channel.delete_messages = AsyncMock()
    guild = _guild()
    guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 900 else None

    first, second = _final_inputs(round_id, division_id)
    with first, second:
        with in_a_job():
            await service.post_final_standings(
                _bot(db_path), guild, db_path, division_id, round_id, as_text=True
            )

    channel.send.assert_awaited()
    old_message.edit.assert_not_awaited()
    old_message.delete.assert_not_awaited()
    channel.delete_messages.assert_not_awaited()
    assert await _recorded_standings_ids(db_path, round_id) == [9100]


async def test_final_standings_whose_channel_was_deleted_raise(db_path):
    """Div A's standings channel (900) has been deleted from the server: the job raises for the
    queue to stop on. Today the division is passed over in silence
    (`season_classification_service.py:278-279`)."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    _season_id, division_ids = await _seed(db_path)
    round_id = await _second_round_with_results(db_path, division_ids[0])
    guild = _guild()
    guild.get_channel.return_value = None
    standings = AsyncMock(return_value=[])

    first, second, third = _opening_posts(standings)
    inputs = _final_inputs(round_id, division_ids[0])
    with first, second, third, inputs[0], inputs[1], pytest.raises(StepFailedOnDiscord):
        await service.post_final_standings(
            _bot(db_path), guild, db_path, division_ids[0], round_id, as_text=False
        )

    standings.assert_not_awaited()


async def test_the_final_standings_record_no_message_id(db_path):
    """The final standings of Div A, drawn against its round 2, go out as message 9200 through
    results' posting that records nothing, asked to post afresh as the final classification:
    no id is recorded against round 2."""
    from leaguebot.results.services.results_post_service import PostedTable

    _season_id, division_ids = await _seed(db_path)
    division_id = division_ids[0]
    round_id = await _second_round_with_results(db_path, division_id)
    produce = AsyncMock(return_value=[PostedTable([9200], [])])
    recording = AsyncMock()

    inputs = _final_inputs(round_id, division_id)
    with inputs[0], inputs[1], patch(
        "leaguebot.results.services.results_post_service.produce_standings", produce
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", recording, create=True,
    ):
        await service.post_final_standings(
            _bot(db_path), _guild(), db_path, division_id, round_id, as_text=False
        )

    produce.assert_awaited_once()
    assert produce.await_args.args[2] == round_id
    assert produce.await_args.kwargs["fresh"] is True
    assert produce.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    recording.assert_not_awaited()
    assert await _recorded_standings_ids(db_path, round_id) == [None]


async def test_a_part_posted_final_standings_is_kept_and_removed_by_the_next_try(db_path):
    """Div A's final standings are posted in part: message 9201 went out and the rest was
    refused. The job raises with 9201 to keep; the next try, handed it, removes 9201 from the
    standings channel before it posts both again."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    _season_id, division_ids = await _seed(db_path)
    division_id = division_ids[0]
    round_id = await _second_round_with_results(db_path, division_id)
    channel = MagicMock()
    guild = _guild()
    guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 900 else None
    refusal = RuntimeError("the constructors' table was refused")
    setattr(refusal, "left_standing", [9201])
    produce = AsyncMock(side_effect=[refusal, []])
    remove = AsyncMock(return_value=([], []))

    inputs = _final_inputs(round_id, division_id)
    with inputs[0], inputs[1], patch(
        "leaguebot.results.services.results_post_service.produce_standings", produce
    ), patch(
        "leaguebot.results.services.results_post_service.remove_part_posted_messages", remove
    ):
        with pytest.raises(StepFailedOnDiscord) as failed:
            await service.post_final_standings(
                _bot(db_path), guild, db_path, division_id, round_id, as_text=False
            )
        assert failed.value.result == {"new": [9201], "channel_id": 900}
        remove.assert_not_awaited()

        await service.post_final_standings(
            _bot(db_path), guild, db_path, division_id, round_id,
            as_text=True, kept=failed.value.result,
        )

    remove.assert_awaited_once_with(channel, [9201])
    assert produce.await_count == 2


async def test_a_final_standings_channel_never_set_posts_nothing(db_path):
    """Div A was never given a standings channel: its final standings post nothing and raise
    nothing."""
    _season_id, division_ids = await _seed(db_path)
    division_id = division_ids[0]
    round_id = await _second_round_with_results(db_path, division_id)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET standings_channel_id = NULL "
            "WHERE division_id = ?",
            (division_id,),
        )
        await db.commit()
    standings = AsyncMock(return_value=[])

    first, second, third = _opening_posts(standings)
    inputs = _final_inputs(round_id, division_id)
    with first, second, third, inputs[0], inputs[1]:
        await service.post_final_standings(
            _bot(db_path), _guild(), db_path, division_id, round_id, as_text=False
        )

    standings.assert_not_awaited()


async def test_the_final_sheet_is_asked_to_raise_and_posted_as_the_final_one(db_path):
    """Div A's final attendance sheet, drawn against its round 2 and retried as text: attendance's
    posting, which always raises, is asked for it as the final classification, and as text."""
    _season_id, division_ids = await _seed(db_path)
    division_id = division_ids[0]
    round_id = await _second_round_with_results(db_path, division_id)
    attendance = AsyncMock(return_value=None)

    with patch(
        "leaguebot.attendance.services.attendance_service.post_attendance_sheet", attendance
    ):
        await service.post_final_sheet(
            _bot(db_path), _guild(), db_path, division_id, round_id, as_text=True
        )

    attendance.assert_awaited_once()
    assert attendance.await_args.args[3] == round_id
    assert attendance.await_args.args[4] == division_id
    assert "raise_on_failure" not in attendance.await_args.kwargs
    assert attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    assert attendance.await_args.kwargs["as_text"] is True
