"""The results submission channel — creating it, closing it, and asking about its state.

Issue #208. `result_submission_service.py` is the largest file in the results module. This file
takes its channel lifecycle: the transient channel a round's results are typed into, which every
button of the penalty and appeals reviews then lives inside.

**It is opened to both tiers of the league** (issue #116). Every review button is in here, so a
league admin who does not also hold the interaction role would be shut out of a round they are
entitled to judge — on the half of the bot that is entirely button-driven, where nobody would
notice until a season was running. `test_both_league_tiers_are_admitted` is the regression test.

**`@everyone` is denied.** The channel carries unpublished results and staged penalties; a
division reading its own penalties before the verdicts are posted is the thing the review
process exists to prevent.

**Any previous row for the round is deleted before the new one is written.** A closed submission
or a row orphaned by a restart would otherwise collide with the UNIQUE constraint, and the
channel would be created in Discord and then fail to be recorded — leaving a channel nobody
could close. `test_a_previous_row_is_replaced_rather_than_colliding` holds it.

**Closing marks the database first and deletes the channel second.** A channel deleted while
still recorded as open would leave the round unable to move on, with a submission the bot
believes is in progress and no channel to run it in. Marking first means a failure to delete
costs an untidy channel rather than a stuck round.

**The name carries the season, division and round**, because a league runs several of these at
once and they are only distinguishable by name in the channel list.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    _make_slug,
    close_submission_channel,
    create_submission_channel,
    is_channel_in_penalty_review,
    is_submission_open,
)

SERVER_ID = 11708
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
CHANNEL_ID = 771001
CMD_CHANNEL_ID = 100


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, round_status: str = "AWAITING_RESULTS") -> str:
    db_path = os.path.join(str(tmp_path), "submission.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) "
            "VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.commit()
    return db_path


async def _seed_channel_row(
    db_path: str,
    *,
    channel_id: int = CHANNEL_ID,
    closed: int = 0,
    in_penalty_review: int = 0,
    resubmitting: int = 0,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, created_at, closed, in_penalty_review, resubmitting) "
            "VALUES (?, ?, '2026-06-01T00:00:00+00:00', ?, ?, ?)",
            (ROUND_ID, channel_id, closed, in_penalty_review, resubmitting),
        )
        await db.commit()


def _guild(*, category=..., has_me: bool = True):
    guild = MagicMock()
    created = MagicMock()
    created.id = CHANNEL_ID
    guild.create_text_channel = AsyncMock(return_value=created)
    guild.default_role = MagicMock(name="everyone")
    guild.me = MagicMock(name="bot") if has_me else None

    cmd_channel = MagicMock()
    cmd_channel.category = MagicMock(name="category") if category is ... else category
    guild.get_channel = MagicMock(return_value=cmd_channel)
    guild._cmd_channel = cmd_channel
    return guild


async def _create(db_path: str, guild, *, interaction_role=None, league_admin_role=None):
    return await create_submission_channel(
        guild,
        "Division 1",
        3,
        5,
        ROUND_ID,
        db_path,
        bot_cmd_channel_id=CMD_CHANNEL_ID,
        interaction_role=interaction_role,
        league_admin_role=league_admin_role,
    )


async def _rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT channel_id, closed FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# The name
# ---------------------------------------------------------------------------


def test_the_channel_name_carries_the_season_division_and_round():
    """A league runs several of these at once and they are only distinguishable by name in
    the channel list."""
    assert _make_slug("Division 1")


@pytest.mark.parametrize(
    "division,expected_in_name",
    [("Division 1", "division-1"), ("Premier League", "premier-league")],
)
async def test_the_division_is_slugged_into_the_name(tmp_path, division, expected_in_name):
    db_path = await _make_db(tmp_path)
    guild = _guild()

    await create_submission_channel(
        guild, division, 3, 5, ROUND_ID, db_path, bot_cmd_channel_id=CMD_CHANNEL_ID
    )

    name = guild.create_text_channel.await_args.kwargs["name"]
    assert name.startswith("S3-")
    assert name.endswith("-R5-results")
    assert expected_in_name in name


# ---------------------------------------------------------------------------
# Who can see it
# ---------------------------------------------------------------------------


async def test_everyone_is_denied(tmp_path):
    """The channel carries unpublished results and staged penalties — a division reading
    its own penalties before the verdicts are posted is what the review exists to prevent."""
    db_path = await _make_db(tmp_path)
    guild = _guild()

    await _create(db_path, guild)

    overwrites = guild.create_text_channel.await_args.kwargs["overwrites"]
    assert overwrites[guild.default_role].read_messages is False


async def test_both_league_tiers_are_admitted(tmp_path):
    """Issue #116. Every review button lives in here, so an admin without the interaction
    role would be shut out of a round they are entitled to judge."""
    db_path = await _make_db(tmp_path)
    guild = _guild()
    manager_role = MagicMock(name="manager")
    league_admin_role = MagicMock(name="admin")

    await _create(db_path, guild, interaction_role=manager_role, league_admin_role=league_admin_role)

    overwrites = guild.create_text_channel.await_args.kwargs["overwrites"]
    assert overwrites[manager_role].read_messages is True
    assert overwrites[league_admin_role].read_messages is True


async def test_the_bot_may_manage_its_own_messages(tmp_path):
    """It edits the review prompt on every staged penalty and deletes it at the end."""
    db_path = await _make_db(tmp_path)
    guild = _guild()

    await _create(db_path, guild)

    overwrites = guild.create_text_channel.await_args.kwargs["overwrites"]
    assert overwrites[guild.me].manage_messages is True


async def test_a_league_with_only_one_role_configured_still_gets_a_channel(tmp_path):
    """A league that has not set its admin role yet must not be blocked from running a
    round."""
    db_path = await _make_db(tmp_path)
    guild = _guild()
    manager_role = MagicMock(name="manager")

    await _create(db_path, guild, interaction_role=manager_role, league_admin_role=None)

    overwrites = guild.create_text_channel.await_args.kwargs["overwrites"]
    assert manager_role in overwrites


async def test_a_guild_the_bot_is_not_a_member_of_still_gets_a_channel(tmp_path):
    """`guild.me` can be absent before the cache fills; the channel is still made and the
    bot's own access comes from its permissions rather than an overwrite."""
    db_path = await _make_db(tmp_path)
    guild = _guild(has_me=False)

    await _create(db_path, guild)

    guild.create_text_channel.assert_awaited_once()


# ---------------------------------------------------------------------------
# Where it goes
# ---------------------------------------------------------------------------


async def test_the_channel_joins_the_command_channel_s_category(tmp_path):
    """So a league's submission channels appear beside the rest of the bot's, rather than
    at the bottom of the server."""
    db_path = await _make_db(tmp_path)
    guild = _guild()

    await _create(db_path, guild)

    assert (
        guild.create_text_channel.await_args.kwargs["category"]
        is guild._cmd_channel.category
    )


async def test_a_command_channel_with_no_category_still_works(tmp_path):
    db_path = await _make_db(tmp_path)
    guild = _guild(category=None)

    await _create(db_path, guild)

    assert guild.create_text_channel.await_args.kwargs["category"] is None


async def test_a_league_with_no_command_channel_configured_still_works(tmp_path):
    db_path = await _make_db(tmp_path)
    guild = _guild()

    await create_submission_channel(
        guild, "Division 1", 3, 5, ROUND_ID, db_path, bot_cmd_channel_id=None
    )

    assert guild.create_text_channel.await_args.kwargs["category"] is None


# ---------------------------------------------------------------------------
# Recording it
# ---------------------------------------------------------------------------


async def test_the_channel_is_recorded_against_the_round(tmp_path):
    db_path = await _make_db(tmp_path)

    await _create(db_path, _guild())

    rows = await _rows(db_path)
    assert rows == [{"channel_id": CHANNEL_ID, "closed": 0}]


async def test_a_previous_row_is_replaced_rather_than_colliding(tmp_path):
    """A closed submission or a row orphaned by a restart would hit the UNIQUE constraint
    — and the channel would already exist in Discord, leaving one nobody could close."""
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, channel_id=999999, closed=1)

    await _create(db_path, _guild())

    rows = await _rows(db_path)
    assert rows == [{"channel_id": CHANNEL_ID, "closed": 0}]


# ---------------------------------------------------------------------------
# Closing it
# ---------------------------------------------------------------------------


def _closing_guild(*, channel=..., error=None):
    guild = MagicMock()
    resolved = MagicMock() if channel is ... else channel
    if resolved is not None:
        resolved.delete = AsyncMock(side_effect=error)
    guild.get_channel = MagicMock(return_value=resolved)
    guild._channel = resolved
    return guild


async def test_closing_marks_the_round_and_deletes_the_channel(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path)
    guild = _closing_guild()

    await close_submission_channel(CHANNEL_ID, ROUND_ID, guild, db_path)

    assert await is_submission_open(db_path, ROUND_ID) is False
    guild._channel.delete.assert_awaited_once()


async def test_the_database_is_marked_before_the_channel_goes(tmp_path):
    """A channel deleted while still recorded as open leaves the round unable to move on —
    a submission the bot believes is in progress with no channel to run it in. Marking
    first means a failed delete costs an untidy channel rather than a stuck round."""
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path)
    guild = _closing_guild()
    open_when_deleted: list[bool] = []

    async def _delete(**kwargs):
        open_when_deleted.append(await is_submission_open(db_path, ROUND_ID))

    guild._channel.delete = AsyncMock(side_effect=_delete)

    await close_submission_channel(CHANNEL_ID, ROUND_ID, guild, db_path)

    assert open_when_deleted == [False]


async def test_a_channel_already_deleted_still_closes_the_round(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path)
    guild = _closing_guild(error=discord.NotFound(MagicMock(), "gone"))

    await close_submission_channel(CHANNEL_ID, ROUND_ID, guild, db_path)

    assert await is_submission_open(db_path, ROUND_ID) is False


async def test_a_channel_that_will_not_delete_is_logged_not_raised(tmp_path, caplog):
    """Raising here would abandon the round's finalisation part-way."""
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path)
    guild = _closing_guild(error=discord.HTTPException(MagicMock(), "forbidden"))

    with caplog.at_level("WARNING"):
        await close_submission_channel(CHANNEL_ID, ROUND_ID, guild, db_path)

    assert "could not delete channel" in caplog.text


async def test_a_channel_the_bot_cannot_see_still_closes_the_round(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path)

    await close_submission_channel(
        CHANNEL_ID, ROUND_ID, _closing_guild(channel=None), db_path
    )

    assert await is_submission_open(db_path, ROUND_ID) is False


# ---------------------------------------------------------------------------
# Asking about its state
# ---------------------------------------------------------------------------


async def test_a_round_with_no_channel_is_not_open(tmp_path):
    """Asked by `/round cancel` among others, so the absent case has to be an answer
    rather than a failure."""
    db_path = await _make_db(tmp_path)

    assert await is_submission_open(db_path, ROUND_ID) is False


async def test_a_round_with_an_open_channel_is_open(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=0)

    assert await is_submission_open(db_path, ROUND_ID) is True


async def test_a_channel_in_penalty_review_is_recognised(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=0, in_penalty_review=1)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID) is True


async def test_a_channel_not_yet_in_penalty_review_is_not(tmp_path):
    """Results are still being typed in; the review has not begun."""
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=0, in_penalty_review=0)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID) is False


async def test_pastes_are_not_deleted_while_resubmitting(tmp_path):
    """Issue #210. The round is still in review, but the manager has pressed Resubmit and is
    pasting its results again. Answering True here has the message guard delete every paste."""
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=0, in_penalty_review=1, resubmitting=1)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID) is False


async def test_a_closed_channel_is_not_in_penalty_review(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=1, in_penalty_review=1)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID) is False


async def test_a_finalised_round_is_not_in_penalty_review(tmp_path):
    """The round is done; a flag left set would keep the channel answering as though a
    review were still running."""
    db_path = await _make_db(tmp_path, round_status="FINAL")
    await _seed_channel_row(db_path, closed=0, in_penalty_review=1)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID) is False


async def test_another_channel_is_not_in_this_round_s_review(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_channel_row(db_path, closed=0, in_penalty_review=1)

    assert await is_channel_in_penalty_review(db_path, CHANNEL_ID + 1) is False
