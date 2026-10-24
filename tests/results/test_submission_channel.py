"""The results submission channel — creating it, closing it, and asking about its state.

Issue #208. `result_submission_service.py` is the largest file in the results module. This file
takes its channel lifecycle: the transient channel a round's results are typed into, which every
button of the penalty and appeals reviews then lives inside.

**It is created by the queue** (#439, slice 6b, C45). At a round's start its timer asks the
queue for `results.submission.open`, whose `create_channel` job creates the channel; the cases
below drive that change on `ongoing_league`'s league, Pro's round 3 in season 3. That the channel
admits both tiers of the league (issue #116) is pinned beside the opening's other tests, in
`test_result_submission_wizard.py::test_the_channel_is_opened_to_both_league_roles`; that it stands
in the interaction channel's category and is recorded against the round, in
`test_submission_open_change.py`.

**`@everyone` is denied.** The channel carries unpublished results and staged penalties; a
division reading its own penalties before the verdicts are posted is the thing the review
process exists to prevent.

**Any previous row for the round gives way to the new one.** A closed submission or a row
orphaned by a restart would otherwise collide with the UNIQUE constraint, and the channel would
be created in Discord and then fail to be recorded — leaving a channel nobody could close.
`test_a_previous_row_is_replaced_rather_than_colliding` holds it.

**Closing marks the database first and deletes the channel second.** A channel deleted while
still recorded as open would leave the round unable to move on, with a submission the bot
believes is in progress and no channel to run it in. Marking first means a failure to delete
costs an untidy channel rather than a stuck round.

**The name carries the season, division and round**, because a league runs several of these at
once and they are only distinguishable by name in the channel list.
"""
from __future__ import annotations

import itertools
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.result_submission_service import (
    _make_slug,
    is_channel_in_penalty_review,
    is_submission_open,
)
from tests.support.change_queue import ADMIN_ROLE_ID, MANAGER_ROLE_ID, run_queue
from tests.support.review_league import channel
from tests.support.season_league import (
    PRO,
    SEASON_NUMBER,
    SeasonLeague,
    ongoing_league,
    round_id,
)

SERVER_ID = 11708
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
CHANNEL_ID = 771001
PRO_ROUND_3 = round_id(PRO, 3)

_created_ids = itertools.count(7700)


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


# ---------------------------------------------------------------------------
# Creating it, through the queue's opening of round 3's submission (C45)
# ---------------------------------------------------------------------------


async def _opening_league(tmp_path: Any) -> SeasonLeague:
    """`ongoing_league`'s league, results on, its server creating text channels: the ids of the
    channels created are kept in `league.created`. The interaction channel stands in a category
    of the server's."""
    league = await ongoing_league(tmp_path)
    league.created = []
    league.bot.interaction_channel.category = MagicMock(name="the interaction channel's category")

    async def _make(name: str, **kwargs: Any) -> Any:
        made = league._recording(channel(next(_created_ids), league.events))
        made.name = name
        made.category = kwargs.get("category")
        made.guild = league.guild
        league.channels[made.id] = made
        league.created.append(made.id)
        return made

    league.guild.create_text_channel = AsyncMock(side_effect=_make)
    return league


async def _open(league: SeasonLeague) -> None:
    """Pro's round 3 starts: its timer asks the queue to open its submission, as the bot, and
    the queue is run."""
    from leaguebot.core.models.change import ChangeOrigin

    await league.bot.change_queue.ask(
        "results.submission.open", {"round_id": PRO_ROUND_3},
        what="opening round 3's results submission", origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)


def _created_with(league: SeasonLeague) -> dict[str, Any]:
    """What the server was asked as round 3's channel was created."""
    league.guild.create_text_channel.assert_awaited_once()
    return dict(league.guild.create_text_channel.await_args.kwargs)


async def _pro_rows(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT channel_id, closed FROM round_submission_channels WHERE round_id = ?",
        PRO_ROUND_3,
    )


# ---------------------------------------------------------------------------
# The name
# ---------------------------------------------------------------------------


def test_the_channel_name_carries_the_season_division_and_round():
    """A league runs several of these at once and they are only distinguishable by name in
    the channel list."""
    assert _make_slug("Division 1")


async def test_the_division_is_slugged_into_the_name(tmp_path):
    """Season 3's Pro round 3 opens: its channel is named `S3-pro-R3-results`, the season,
    the division slugged and the round."""
    league = await _opening_league(tmp_path)

    await _open(league)

    assert _created_with(league)["name"] == f"S{SEASON_NUMBER}-pro-R3-results"


# ---------------------------------------------------------------------------
# Who can see it
# ---------------------------------------------------------------------------


async def test_everyone_is_denied(tmp_path):
    """The channel carries unpublished results and staged penalties — a division reading
    its own penalties before the verdicts are posted is what the review exists to prevent."""
    league = await _opening_league(tmp_path)

    await _open(league)

    overwrites = _created_with(league)["overwrites"]
    assert overwrites[league.guild.default_role].read_messages is False


async def test_the_bot_may_manage_its_own_messages(tmp_path):
    """It edits the review prompt on every staged penalty and deletes it at the end."""
    league = await _opening_league(tmp_path)

    await _open(league)

    overwrites = _created_with(league)["overwrites"]
    assert overwrites[league.guild.me].manage_messages is True


async def test_a_league_with_only_one_role_configured_still_gets_a_channel(tmp_path):
    """A league whose admin role cannot be found must not be blocked from running a round:
    the channel is created, admitting the league managers' role."""
    league = await _opening_league(tmp_path)
    league.roles_gone.add(ADMIN_ROLE_ID)

    await _open(league)

    admitted = {getattr(target, "id", None) for target in _created_with(league)["overwrites"]}
    assert MANAGER_ROLE_ID in admitted
    assert ADMIN_ROLE_ID not in admitted


async def test_a_guild_the_bot_is_not_a_member_of_still_gets_a_channel(tmp_path):
    """`guild.me` can be absent before the cache fills; the channel is still made and the
    bot's own access comes from its permissions rather than an overwrite."""
    league = await _opening_league(tmp_path)
    league.guild.me = None

    await _open(league)

    assert len(league.created) == 1
    assert None not in _created_with(league)["overwrites"]


# ---------------------------------------------------------------------------
# Where it goes
# ---------------------------------------------------------------------------


async def test_a_command_channel_with_no_category_still_works(tmp_path):
    """The interaction channel stands in no category: round 3's channel is created in none."""
    league = await _opening_league(tmp_path)
    league.bot.interaction_channel.category = None

    await _open(league)

    assert _created_with(league)["category"] is None


async def test_a_league_with_no_command_channel_configured_still_works(tmp_path):
    """The league has no interaction channel set: round 3's channel is still created, in no
    category."""
    league = await _opening_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE server_configs SET interaction_channel_id = NULL")
        await db.commit()

    await _open(league)

    assert _created_with(league)["category"] is None


# ---------------------------------------------------------------------------
# Recording it
# ---------------------------------------------------------------------------


async def test_a_previous_row_is_replaced_rather_than_colliding(tmp_path):
    """A closed submission or a row orphaned by a restart would hit the UNIQUE constraint
    — and the channel would already exist in Discord, leaving one nobody could close. Round 3
    holds a closed row for channel 999999; its opening leaves one open row, for the channel
    created."""
    league = await _opening_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
            "VALUES (?, 999999, '2026-06-01T00:00:00+00:00', 1)",
            (PRO_ROUND_3,),
        )
        await db.commit()

    await _open(league)

    [made] = league.created
    assert await _pro_rows(league) == [{"channel_id": made, "closed": 0}]


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
