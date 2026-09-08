"""The two classifications a season is bracketed by (2026-09-08).

The opening one is posted when a season is approved, the final one when it completes.
Both are the standings and attendance sheets a league already reads every round, under a
different heading and with no message text — see
``models.classification_occasion.ClassificationOccasion``.

What matters here is the orchestration: which sheets are posted for which division, and
that one division's failure never stops the next. What the sheets *say* is covered by
``test_image_standings_service`` and ``test_attendance_sheet_posting``.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.classification_occasion import ClassificationOccasion
from services import season_classification_service as service

pytestmark = pytest.mark.asyncio


def _division(division_id: int, name: str, channel_id=900):
    division = MagicMock()
    division.id = division_id
    division.name = name
    division.standings_channel_id = channel_id
    return division


def _round(round_id: int, number: int):
    rnd = MagicMock()
    rnd.id = round_id
    rnd.round_number = number
    rnd.track_name = "Silverstone Circuit"
    return rnd


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
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'ACTIVE', 1)",
            (server_id,),
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
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ACTIVE')",
                (server_id, 500 + index),
            )
            profile_id = cur.lastrowid
            cur = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Apex Racing', 2, 0)",
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
            "services.results_post_service.post_standings",
            standings or AsyncMock(return_value=None),
        ),
        patch(
            "services.attendance_service.post_attendance_sheet",
            attendance or AsyncMock(return_value=None),
        ),
    )


# ── The opening classification ────────────────────────────────────────────


async def test_the_opening_posts_both_sheets_for_every_division(db_path):
    _season_id, division_ids = await _seed(db_path, divisions=("Div A", "Div B"))
    divisions = [_division(division_ids[0], "Div A"), _division(division_ids[1], "Div B")]
    div_rounds = {division_ids[0]: [_round(1, 1)], division_ids[1]: [_round(2, 1)]}
    standings, attendance = AsyncMock(), AsyncMock()

    with _patched(standings, attendance)[0], _patched(standings, attendance)[1]:
        problems = await service.post_opening_classifications(
            _bot(db_path), _guild(), db_path, divisions, div_rounds
        )

    assert problems == []
    assert standings.await_count == 2
    assert attendance.await_count == 2
    assert all(
        call.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
        for call in standings.await_args_list + attendance.await_args_list
    )


async def test_the_opening_is_drawn_against_the_divisions_first_round(db_path):
    """Not because it stands after it — the grid of rounds is read from the calendar."""
    _season_id, division_ids = await _seed(db_path)
    divisions = [_division(division_ids[0], "Div A")]
    div_rounds = {division_ids[0]: [_round(41, 1), _round(42, 2)]}
    standings = AsyncMock()

    with _patched(standings)[0], _patched(standings)[1]:
        await service.post_opening_classifications(
            _bot(db_path), _guild(), db_path, divisions, div_rounds
        )

    assert standings.await_args.args[2] == 41


async def test_the_opening_carries_no_lifecycle_label(db_path):
    _season_id, division_ids = await _seed(db_path)
    standings = AsyncMock()

    with _patched(standings)[0], _patched(standings)[1]:
        await service.post_opening_classifications(
            _bot(db_path),
            _guild(),
            db_path,
            [_division(division_ids[0], "Div A")],
            {division_ids[0]: [_round(41, 1)]},
        )

    assert standings.await_args.args[10] == ""


async def test_one_divisions_failure_does_not_stop_the_next(db_path):
    """Approval is far too consequential to be failed by a picture."""
    _season_id, division_ids = await _seed(db_path, divisions=("Div A", "Div B"))
    divisions = [_division(division_ids[0], "Div A"), _division(division_ids[1], "Div B")]
    div_rounds = {division_ids[0]: [_round(1, 1)], division_ids[1]: [_round(2, 1)]}
    standings = AsyncMock(side_effect=[RuntimeError("the template is at fault"), None])
    attendance = AsyncMock()

    with _patched(standings, attendance)[0], _patched(standings, attendance)[1]:
        problems = await service.post_opening_classifications(
            _bot(db_path), _guild(), db_path, divisions, div_rounds
        )

    assert standings.await_count == 2, "the second division is still posted"
    assert attendance.await_count == 2
    assert len(problems) == 1
    assert "Div A standings" in problems[0]


async def test_a_division_with_no_rounds_is_skipped(db_path):
    _season_id, division_ids = await _seed(db_path)
    standings = AsyncMock()

    with _patched(standings)[0], _patched(standings)[1]:
        problems = await service.post_opening_classifications(
            _bot(db_path), _guild(), db_path, [_division(division_ids[0], "Div A")], {}
        )

    assert problems == []
    assert standings.await_count == 0


async def test_no_guild_posts_nothing(db_path):
    standings = AsyncMock()
    with _patched(standings)[0], _patched(standings)[1]:
        assert await service.post_opening_classifications(
            _bot(db_path), None, db_path, [_division(1, "Div A")], {}
        ) == []
    assert standings.await_count == 0


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
