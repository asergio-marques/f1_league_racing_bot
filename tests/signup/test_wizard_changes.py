"""A driver's signup channel on the change queue: pressing Sign Up (#439, slice 6b, C75) and
withdrawing (C76).

Today Sign Up creates the driver's signup channel, saves the wizard, moves the driver to Pending
Signup Completion and posts the welcome itself, off the queue, and Cancel Signup ends the signup
and posts its closing notice the same way. On the queue Sign Up asks `signup.start`, a league
change run whole on the league lane (a press outside every conversation counts as a command,
decision 31): `delete_old_channel` where an old channel stands, `create_channel` (whose record
saves the wizard row), `start`, `arm_inactivity` and `post_welcome`. Its check refuses the driver
where a close waits on the queue (Q28), and where the bot may not manage channels or the category
has no room, naming the cause (decision 10). Cancel Signup asks `signup.end` on the league lane,
which changes the driver's status and asks back `signup.closing` on the driver's lane (Q-B), so a
notice Discord refuses stops that lane alone.

The league: `setup_league` at Signups, the signup module on and its window open in the signup
channel (620), which sits in the category "Signups" (630) with room to spare and the bot allowed
to manage channels there. Kimi (105) presses Sign Up in it; a channel the bot creates is given the
id 6500 and records what it is sent.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    http_error,
    member_interaction,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    SIGNING_UP,
    SIGNUP_CHANNEL,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    setup_league,
    signing_up,
)

#: The changes Sign Up and Cancel Signup ask, and the one the league asks back of the lane.
START_KIND = "signup.start"
END_KIND = "signup.end"
CLOSING_KIND = "signup.closing"
#: The timers the wizard arms: its 24 hours of inactivity, and the held channel's deletion.
INACTIVITY_TIMER = "signup_inactivity"
DELETE_TIMER = "signup_channel_delete"
#: The category the signup channel sits in, and the channel a Sign Up creates.
CATEGORY = 630
CATEGORY_NAME = "Signups"
NEW_CHANNEL = 6500
#: Kimi's signup channel from an earlier signup, and the one he is filling in now.
OLD_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
KIMI_LANE = f"channel:{KIMI_CHANNEL}"

NO_START_CHANGE = (
    "#439: Sign Up creates the driver's signup channel, saves the wizard and posts the welcome "
    "itself off the queue, with no signup.start change"
)
NO_END_CHANGE = (
    "#439: Cancel Signup ends the signup and posts its closing notice itself off the queue, with "
    "no signup.end change on the league lane asking signup.closing back of the driver's lane"
)


async def _league(tmp_path: Any, *, manage_channels: bool = True,
                  category_size: int = 3) -> SeasonLeague:
    """`setup_league` at Signups with the window open; the signup channel in the category
    "Signups", holding *category_size* channels, the bot allowed to manage channels there or
    not; each channel the bot creates recorded, its creations in `league.created`."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    category = MagicMock(spec=discord.CategoryChannel)
    category.id = CATEGORY
    category.name = CATEGORY_NAME
    category.channels = [MagicMock() for _ in range(category_size)]
    allowed = MagicMock(manage_channels=manage_channels)
    category.permissions_for = MagicMock(return_value=allowed)
    league.guild.me.guild_permissions = allowed
    league.channel(SIGNUP_CHANNEL).category = category
    league.created = []
    league.order = []
    ids = iter(range(NEW_CHANNEL, NEW_CHANNEL + 10))

    async def _create(*_args: Any, **_kwargs: Any) -> Any:
        cid = next(ids)
        league.order.append(f"create {cid}")
        league.created.append(cid)
        return _held(league, cid)

    league.guild.create_text_channel = AsyncMock(side_effect=_create)
    category.create_text_channel = league.guild.create_text_channel
    # Today's handlers off the queue, reached by the presses until the changes replace them.
    league.bot.wizard_service.start_wizard = AsyncMock(return_value=None)
    return league


def _held(league: SeasonLeague, cid: int) -> Any:
    """Signup channel *cid* on the server, recording what it is sent, its permission edits and
    its deletion (in `league.order`)."""
    made = league._recording(channel(cid, league.events))
    made.guild = league.guild
    made.set_permissions = AsyncMock()

    async def _delete(*_args: Any, **_kwargs: Any) -> None:
        league.order.append(f"delete {cid}")
        league.channels.pop(cid, None)

    made.delete = AsyncMock(side_effect=_delete)
    league.channels[cid] = made
    return made


def _driver(league: SeasonLeague, user_id: int = SIGNING_UP, *, cid: int = SIGNUP_CHANNEL) -> Any:
    """A press by driver *user_id* in channel *cid*, not yet answered."""
    press = member_interaction(league.bot, user=league._member(user_id))
    press.guild = league.guild
    press.channel_id = cid
    press.channel = league.channel(cid)
    return press


async def _press_sign_up(league: SeasonLeague) -> Any:
    """Kimi presses Sign Up in the signup channel; gives his press."""
    from leaguebot.signup.cogs.signup_cog import SignupButtonView

    press = _driver(league)
    await SignupButtonView().signup_button.callback(press)
    return press


async def _press_cancel(league: SeasonLeague) -> Any:
    """Kimi presses Cancel Signup in his signup channel; gives his press."""
    from leaguebot.signup.cogs.signup_cog import WithdrawButtonView

    press = _driver(league, cid=KIMI_CHANNEL)
    await WithdrawButtonView(str(SIGNING_UP), league.bot).withdraw_button.callback(press)
    return press


async def _changes_of(league: SeasonLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


async def _job_names(league: SeasonLeague, kind: str) -> list[str]:
    [change] = await _changes_of(league, kind)
    return [job["name"] for job in await step_rows(league.db_path, change["id"])]


async def _wizard(league: SeasonLeague, user_id: int = SIGNING_UP) -> dict[str, Any] | None:
    rows = await league.rows(
        "SELECT * FROM signup_wizard_records WHERE discord_user_id = ?", str(user_id)
    )
    return rows[0] if rows else None


async def _driver_state(league: SeasonLeague, user_id: int = SIGNING_UP) -> str | None:
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(user_id)
    )
    return str(rows[0]["current_state"]) if rows else None


def _armed(league: SeasonLeague, timer: str) -> list[tuple[Any, ...]]:
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == timer]


def _answered(press: Any) -> str:
    """All the member was told: the acknowledgement, then the reply as updated."""
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


def _in_order(seen: list[str], wanted: list[str]) -> bool:
    """Whether *wanted* appear in *seen* in that order, others between them allowed."""
    rest = iter(seen)
    return all(name in rest for name in wanted)


# ---------------------------------------------------------------------------
# Sign Up (C75)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_sign_up_is_acknowledged_and_creates_the_channel_and_welcome_as_jobs(tmp_path):
    """Kimi presses Sign Up while signups are open. At once he is answered privately that his
    signup is under way, and no channel is created yet. When the queue runs, the `signup.start`
    change's jobs `create_channel`, `start`, `arm_inactivity` and `post_welcome` are done in that
    order: his signup channel (6500) is created, he is Pending Signup Completion, his 24 hours of
    inactivity are armed from the job's time, and the welcome is posted in the new channel."""
    league = await _league(tmp_path)

    press = await _press_sign_up(league)

    assert acknowledgement(press).startswith("⏳")
    assert league.created == []

    await run_queue(league.bot)

    names = await _job_names(league, START_KIND)
    assert _in_order(names, ["create_channel", "start", "arm_inactivity", "post_welcome"])
    assert league.created == [NEW_CHANNEL]
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert _armed(league, INACTIVITY_TIMER) == [
        (INACTIVITY_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]
    assert league.channel(NEW_CHANNEL).texts != []


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_the_wizard_row_is_saved_with_the_channel_s_mark(tmp_path):
    """Kimi presses Sign Up and the queue runs. His wizard's row is saved naming the channel
    created (6500), in the save that marks `create_channel` done: once that job is done, the
    row stands."""
    league = await _league(tmp_path)

    await _press_sign_up(league)
    await run_queue(league.bot)

    [change] = await _changes_of(league, START_KIND)
    [created] = [job for job in await step_rows(league.db_path, change["id"])
                 if job["name"] == "create_channel"]
    assert created["done_at"]
    wizard = await _wizard(league)
    assert wizard is not None and int(wizard["signup_channel_id"]) == NEW_CHANNEL


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_a_created_channel_whose_save_failed_is_deleted_before_the_next_try(tmp_path):
    """Kimi presses Sign Up; Discord creates his channel (6500), but the save that would record
    it fails (the wizard's table refuses the insert). The queue stops at `create_channel`, no
    wizard row standing. Once the table takes inserts again, Retry first deletes channel 6500,
    then creates a second (6501), which his wizard's row names; only one channel is left."""
    from tests.support.change_queue import retry_job

    league = await _league(tmp_path)
    await league.write(
        "CREATE TRIGGER wizard_refused BEFORE INSERT ON signup_wizard_records "
        "BEGIN SELECT RAISE(ABORT, 'the wizard could not be saved'); END"
    )

    await _press_sign_up(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "create_channel"
    assert await _wizard(league) is None

    await league.write("DROP TRIGGER wizard_refused")
    await retry_job(league.bot, lane="league")

    assert league.order == [
        f"create {NEW_CHANNEL}", f"delete {NEW_CHANNEL}", f"create {NEW_CHANNEL + 1}"
    ]
    wizard = await _wizard(league)
    assert wizard is not None and int(wizard["signup_channel_id"]) == NEW_CHANNEL + 1


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_sign_up_while_a_close_waits_on_the_queue_is_refused_as_closing(tmp_path):
    """The close time has come and the timer has asked the queue to close the window, which has
    not yet run. Kimi presses Sign Up: he is told signups are closing, no `signup.start` change
    is asked, and when the queue runs no signup channel is created and he is not signing up."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended

    league = await _league(tmp_path)
    await close_signups_unattended(league.bot, cause="timer")

    press = await _press_sign_up(league)

    assert "closing" in _answered(press).lower()
    assert await _changes_of(league, START_KIND) == []
    await run_queue(league.bot)
    assert league.created == []
    assert await _driver_state(league) in (None, "NOT_SIGNED_UP")


@pytest.mark.parametrize(
    ("case", "named"),
    [
        pytest.param("no Manage Channels", "Manage Channels",
                     marks=pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)),
        pytest.param("the category is full", CATEGORY_NAME,
                     marks=pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)),
    ],
)
async def test_sign_up_without_manage_channels_or_room_is_refused_naming_the_cause(
    tmp_path, case, named
):
    """Kimi presses Sign Up, but the bot may not create his channel:

    - no Manage Channels: the bot lacks the permission in the signup channel's category;
    - the category is full: "Signups" already holds Discord's 50 channels.

    He is refused, the refusal naming the cause (the permission, or the category by name); no
    `signup.start` change is asked, and when the queue runs nothing is created and he is not
    signing up."""
    if case == "no Manage Channels":
        league = await _league(tmp_path, manage_channels=False)
    else:
        league = await _league(tmp_path, category_size=50)

    press = await _press_sign_up(league)

    assert named in _answered(press)
    assert await _changes_of(league, START_KIND) == []
    await run_queue(league.bot)
    assert league.created == []
    assert await _driver_state(league) in (None, "NOT_SIGNED_UP")


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_an_old_channel_is_deleted_before_the_new_one_is_created(tmp_path):
    """Kimi signed up before and withdrew: he is Not Signed Up, his old signup channel (6405)
    still standing and named by his wizard's row. He presses Sign Up and the queue runs: the
    old channel is deleted by `delete_old_channel` before the new one (6500) is created, and
    his wizard's row names the new one."""
    league = await _league(tmp_path)
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver) "
        "VALUES (?, 'NOT_SIGNED_UP', 0)",
        str(SIGNING_UP),
    )
    await league.write(
        "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, signup_channel_id) "
        "VALUES (?, 'UNENGAGED', ?)",
        str(SIGNING_UP), OLD_CHANNEL,
    )
    _held(league, OLD_CHANNEL)

    await _press_sign_up(league)
    await run_queue(league.bot)

    assert league.order == [f"delete {OLD_CHANNEL}", f"create {NEW_CHANNEL}"]
    assert _in_order(await _job_names(league, START_KIND),
                     ["delete_old_channel", "create_channel"])
    wizard = await _wizard(league)
    assert wizard is not None and int(wizard["signup_channel_id"]) == NEW_CHANNEL


@pytest.mark.xfail(strict=True, reason=NO_START_CHANGE)
async def test_sign_up_posts_its_welcome_on_the_league_lane(tmp_path):
    """Kimi presses Sign Up and the queue runs. The `signup.start` change, its welcome in his
    new channel included, runs on the league lane, not on the lane of the channel it creates."""
    league = await _league(tmp_path)

    await _press_sign_up(league)
    await run_queue(league.bot)

    [change] = await _changes_of(league, START_KIND)
    assert _lane(change) == "league"
    assert change["state"] == "DONE"
    assert "post_welcome" in await _job_names(league, START_KIND)
    assert league.channel(NEW_CHANNEL).texts != []
