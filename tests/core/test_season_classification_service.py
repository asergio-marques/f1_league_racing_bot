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

import discord
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


def _patched(standings=None, attendance=None):
    return (
        patch(
            "leaguebot.results.services.results_post_service.post_standings",
            standings or AsyncMock(return_value=None),
        ),
        patch(
            "leaguebot.attendance.services.attendance_service.post_attendance_sheet",
            attendance or AsyncMock(return_value=None),
        ),
    )


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


async def test_a_failed_opening_standings_post_leaves_one_copy_once_retried(db_path):
    """Div A's opening standings go out as one table per championship, as they do where the
    image flow ran and neither graphic drew. Discord accepts the drivers' table and refuses the
    teams', so the job fails; it is tried again as text with the channel accepting everything.
    Afterwards the channel holds one drivers' table and one teams' table, not a second drivers'
    copy beside the first (#439). Only the outcome is pinned: whether the first copy comes down
    with the failure or before the next try is the build's to choose."""
    from leaguebot.image.services.image_standings_post import (
        FELL_BACK,
        ChampionshipOutcome,
        StandingsPostOutcome,
    )
    from tests.support.review_league import channel

    _season_id, division_ids = await _seed(db_path)
    events: list[tuple[str, int, int]] = []
    standings_channel = channel(900, events)
    guild = _guild()
    guild.get_channel.side_effect = lambda channel_id: (
        standings_channel if channel_id == 900 else None
    )
    neither_drew = AsyncMock(return_value=StandingsPostOutcome(
        drivers=ChampionshipOutcome(FELL_BACK), constructors=ChampionshipOutcome(FELL_BACK),
    ))
    standings_channel.fail_when = lambda content, _kwargs: "**Team Standings**" in content

    with patch("leaguebot.image.services.image_standings_post.try_post", neither_drew):
        with pytest.raises(discord.HTTPException):
            await service.post_opening_standings(
                _bot(db_path), guild, db_path, division_ids[0], as_text=False
            )
        # The drivers' table was sent before the teams' was refused.
        assert neither_drew.await_count == 1
        assert [event[0] for event in events][:1] == ["send"]

        standings_channel.fail_when = None
        await service.post_opening_standings(
            _bot(db_path), guild, db_path, division_ids[0], as_text=True
        )

    standing = [message.content for message in standings_channel.messages.values()]
    assert sum(text.count("**Driver Standings**") for text in standing) == 1, standing
    assert sum(text.count("**Team Standings**") for text in standing) == 1, standing


# ── The final classification ──────────────────────────────────────────────


async def test_a_division_that_ran_no_round_publishes_no_final_classification(db_path):
    """There is no classification to publish."""
    season_id, _division_ids = await _seed(db_path)
    standings, attendance = AsyncMock(), AsyncMock()

    with _patched(standings, attendance)[0], _patched(standings, attendance)[1]:
        problems = await service.post_final_classifications(
            _bot(db_path), _guild(), db_path, season_id
        )

    assert problems == []
    assert standings.await_count == 0
    assert attendance.await_count == 0


async def test_the_final_is_drawn_against_the_last_round_with_results(db_path):
    """The final sheet *is* that round's classification, restated under its own heading."""
    season_id, division_ids = await _seed(db_path)
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00')",
            (division_ids[0],),
        )
        second = cur.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (second, division_ids[0]),
        )
        await db.commit()

    standings, attendance = AsyncMock(), AsyncMock()
    with _patched(standings, attendance)[0], _patched(standings, attendance)[1]:
        problems = await service.post_final_classifications(
            _bot(db_path), _guild(), db_path, season_id
        )

    assert problems == []
    assert standings.await_args.args[2] == second
    assert standings.await_args.args[3] == 2
    assert standings.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    assert attendance.await_args.args[3] == second
    assert (
        attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    )
