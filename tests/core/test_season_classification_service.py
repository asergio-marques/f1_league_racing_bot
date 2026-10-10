"""The two classifications a season is bracketed by (2026-09-08).

The opening one is posted when a season is approved, the final one when it completes.
Both are the standings and attendance sheets a league already reads every round, under a
different heading and with no message text — see
``leaguebot.core.models.classification_occasion.ClassificationOccasion``.

What matters here is the orchestration: which sheets are posted for which division, and,
for the opening one, that each division's posting raises where it cannot be made. What the sheets *say* is covered by
``test_image_standings_service`` and ``test_attendance_sheet_posting``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services import season_classification_service as service

pytestmark = pytest.mark.asyncio


def _bot(path):
    """A bot carrying a real db path — the name resolution reads it."""
    bot = MagicMock()
    bot.db_path = path
    return bot


def _guild():
    guild = MagicMock()
    guild.id = 1
    guild.get_channel.return_value = MagicMock()
    return guild


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "classification.db")
    await run_migrations(path)
    return path


async def _seed(path, *, server_id=1, divisions=("Div A",)):
    """A season, its divisions, one round each, and a seated driver per division."""
    ids: list[int] = []
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 1, 2, 3)",
            (server_id,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cur.lastrowid
        for index, name in enumerate(divisions):
            cur = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, "
                "forecast_channel_id) VALUES (?, ?, ?, ?)",
                (season_id, name, 10 + index, 20 + index),
            )
            division_id = cur.lastrowid
            ids.append(division_id)
            await db.execute(
                "INSERT INTO division_results_config "
                "(division_id, results_channel_id, standings_channel_id) "
                "VALUES (?, ?, ?)",
                (division_id, 800 + index, 900 + index),
            )
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
                "VALUES (?, 1, 'NORMAL', '2026-02-01T18:00:00')",
                (division_id,),
            )
            cur = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ACTIVE')",
                (500 + index,),
            )
            profile_id = cur.lastrowid
            cur = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, 'Apex Racing', 'Apex Racing', 2, 0)",
                (division_id,),
            )
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, "
                "driver_profile_id) VALUES (?, 1, ?)",
                (cur.lastrowid, profile_id),
            )
        await db.commit()
    return season_id, ids


# ── The opening classification ────────────────────────────────────────────
#
# One division at a time, each a job of the season's approval on the change queue (#439,
# slice 4a): the standings and the sheet are two functions, each reading the division's
# channel and its first round as it runs, and each raising where it cannot post, for the
# queue to stop on. The standings channel is read from `division_results_config`, where
# `/division results-channel` keeps it; the approval used to look for it on a division read
# that never carries it, so no opening standings were ever posted.


def _opening_posts(standings=None, attendance=None):
    """The opening standings, through whichever of results' two posting functions it uses,
    and the sheet. Both standings doubles are the one mock, so a test reads either."""
    standings = standings or AsyncMock(return_value=[])
    return (
        patch("leaguebot.results.services.results_post_service.post_standings", standings),
        patch("leaguebot.results.services.results_post_service.produce_standings", standings),
        patch(
            "leaguebot.attendance.services.attendance_service.post_attendance_sheet",
            attendance or AsyncMock(return_value=None),
        ),
    )


async def _first_round_id(path, division_id: int) -> int:
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT id FROM rounds WHERE division_id = ? AND round_number = 1", (division_id,)
        )
        return (await cursor.fetchone())["id"]


async def test_the_opening_posts_both_sheets_for_every_division(db_path):
    _season_id, division_ids = await _seed(db_path, divisions=("Div A", "Div B"))
    standings, attendance = AsyncMock(return_value=[]), AsyncMock()

    first, second, third = _opening_posts(standings, attendance)
    with first, second, third:
        for division_id in division_ids:
            await service.post_opening_standings(
                _bot(db_path), _guild(), db_path, division_id, as_text=False
            )
            await service.post_opening_sheet(
                _bot(db_path), _guild(), db_path, division_id, as_text=False
            )

    assert standings.await_count == 2
    assert attendance.await_count == 2
    assert all(
        call.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
        for call in standings.await_args_list + attendance.await_args_list
    )


async def test_the_opening_is_drawn_against_the_divisions_first_round(db_path):
    """Not because it stands after it — the grid of rounds is read from the calendar."""
    _season_id, division_ids = await _seed(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00')",
            (division_ids[0],),
        )
        await db.commit()
    opener = await _first_round_id(db_path, division_ids[0])
    standings, attendance = AsyncMock(return_value=[]), AsyncMock()

    first, second, third = _opening_posts(standings, attendance)
    with first, second, third:
        await service.post_opening_standings(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )
        await service.post_opening_sheet(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )

    assert standings.await_args.args[2] == opener
    assert attendance.await_args.args[3] == opener


async def test_the_opening_carries_no_lifecycle_label(db_path):
    _season_id, division_ids = await _seed(db_path)
    standings = AsyncMock(return_value=[])

    first, second, third = _opening_posts(standings)
    with first, second, third:
        await service.post_opening_standings(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )

    assert standings.await_args.args[10] == ""


async def test_a_division_with_no_rounds_is_skipped(db_path):
    _season_id, division_ids = await _seed(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rounds WHERE division_id = ?", (division_ids[0],))
        await db.commit()
    standings, attendance = AsyncMock(return_value=[]), AsyncMock()

    first, second, third = _opening_posts(standings, attendance)
    with first, second, third:
        await service.post_opening_standings(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )
        await service.post_opening_sheet(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )

    assert standings.await_count == 0
    assert attendance.await_count == 0


async def test_the_opening_standings_post_reads_the_division_s_standings_channel(db_path):
    """Div A's standings channel (900) is set with `/division results-channel`, in the results
    module's own settings: the opening classification is posted there, both championships."""
    _season_id, division_ids = await _seed(db_path)
    guild = _guild()
    standings_channel = MagicMock()
    guild.get_channel.side_effect = lambda channel_id: (
        standings_channel if channel_id == 900 else None
    )
    standings = AsyncMock(return_value=[])

    first, second, third = _opening_posts(standings)
    with first, second, third:
        await service.post_opening_standings(
            _bot(db_path), guild, db_path, division_ids[0], as_text=False
        )

    standings.assert_awaited_once()
    args = standings.await_args.args
    assert args[5] is standings_channel
    assert args[6], "the drivers' championship"
    assert args[7] is not None, "the constructors' championship"
    assert standings.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING


async def test_an_opening_standings_channel_set_and_gone_raises(db_path):
    """Div A's standings channel (900) has been deleted from the server: the job raises for
    the queue to stop on, rather than passing over the division in silence."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    _season_id, division_ids = await _seed(db_path)
    guild = _guild()
    guild.get_channel.return_value = None
    standings = AsyncMock(return_value=[])

    first, second, third = _opening_posts(standings)
    with first, second, third, pytest.raises(StepFailedOnDiscord):
        await service.post_opening_standings(
            _bot(db_path), guild, db_path, division_ids[0], as_text=False
        )

    standings.assert_not_awaited()


async def test_the_opening_sheet_raises_where_it_cannot_be_posted(db_path):
    """Discord refuses Div A's opening attendance sheet: the sheet is asked for with
    `raise_on_failure`, and what it raises reaches the job, for the queue to stop on."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    _season_id, division_ids = await _seed(db_path)
    attendance = AsyncMock(side_effect=StepFailedOnDiscord("the sheet could not be posted"))

    first, second, third = _opening_posts(attendance=attendance)
    with first, second, third, pytest.raises(StepFailedOnDiscord):
        await service.post_opening_sheet(
            _bot(db_path), _guild(), db_path, division_ids[0], as_text=False
        )

    assert attendance.await_args.kwargs["raise_on_failure"] is True
    assert attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING



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

_XFAIL_FINAL = "#439: the final standings and the final sheet are not yet jobs of their own"


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


@pytest.mark.xfail(strict=True, reason=_XFAIL_FINAL)
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


@pytest.mark.xfail(strict=True, reason=_XFAIL_FINAL)
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
    ), patch("leaguebot.results.services.results_post_service.post_standings", recording):
        await service.post_final_standings(
            _bot(db_path), _guild(), db_path, division_id, round_id, as_text=False
        )

    produce.assert_awaited_once()
    assert produce.await_args.args[2] == round_id
    assert produce.await_args.kwargs["fresh"] is True
    assert produce.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    recording.assert_not_awaited()
    assert await _recorded_standings_ids(db_path, round_id) == [None]


@pytest.mark.xfail(strict=True, reason=_XFAIL_FINAL)
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
    posting is asked to raise, as the final classification, and as text."""
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
    assert attendance.await_args.kwargs["raise_on_failure"] is True
    assert attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    assert attendance.await_args.kwargs["as_text"] is True
