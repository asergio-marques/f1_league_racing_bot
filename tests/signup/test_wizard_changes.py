"""A driver's signup channel on the change queue: pressing Sign Up (#439, slice 6b, C75),
withdrawing (C76), the signup's expiry (C77), a rejection (C78), the held channel's deletion
(C79) and a member leaving mid-signup (C80).

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

The expiry, asked by the 24-hour timer or the restart, is the league's whole (decision 31): its
`signup.end` returns the driver, posts the notice, locks the channel and arms its deletion on the
league lane, and is not due where an answer typed before the 24 hours ran out is queued (Q22). A
reason typed after Reject asks `signup.end` on the league lane through the press's stored
interaction, which asks back `signup.closing`, deleting the reason, on the driver's lane. The
24-hour deletion of a held channel runs on the driver's own lane (R2(d)); `/driver reassign`'s
deletion of a replaced account's channel is a command's, on the league lane. A member leaving
asks `signup.member_left` on the league lane (an event counts as a command, A27), which returns
the driver and deletes the channel; a turn of his already stopped on his lane stays stopped
(decision 24) until Retry finds it no longer due.

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
    discard_job,
    http_error,
    member_interaction,
    refuse_the_job,
    retry_job,
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
    # The league's default teams, which the wizard's team buttons offer (the Reserve team apart).
    from leaguebot.core.services.team_service import TeamService

    league.bot.team_service = TeamService(league.db_path)
    await league.bot.team_service.add_default_team("FER", full_name="Scuderia Ferrari")
    await league.bot.team_service.add_default_team("MCL", full_name="McLaren Racing")
    # Today's handlers off the queue, reached by the presses until the changes replace them.
    league.bot.wizard_service.start_wizard = AsyncMock(return_value=None)
    league.bot.wizard_service.withdraw = AsyncMock(return_value=None)
    return league


def _held(league: SeasonLeague, cid: int) -> Any:
    """Signup channel *cid* on the server, recording what it is sent, its permission edits and
    its deletion (in `league.order`); a message typed in it (`typed_message`) is kept in
    `posted` until deleted."""
    made = league._recording(channel(cid, league.events))
    made.guild = league.guild
    made.posted = []
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
    """Kimi presses Sign Up in the signup channel; gives his press. The view is handed the change
    queue when it is built (A23), never reaching it through the bot."""
    from leaguebot.signup.services.signup_views import SignupButtonView

    press = _driver(league)
    await SignupButtonView(league.bot.change_queue).signup_button.callback(press)
    return press


async def _press_cancel(league: SeasonLeague) -> Any:
    """Kimi presses Cancel Signup in his signup channel; gives his press. The view is handed his
    account and the change queue when it is built (A23), never reaching the queue through the
    bot."""
    from leaguebot.signup.services.signup_views import WithdrawButtonView

    press = _driver(league, cid=KIMI_CHANNEL)
    view = WithdrawButtonView(str(SIGNING_UP), league.bot.change_queue)
    await view.withdraw_button.callback(press)
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


async def test_a_member_who_leaves_before_their_channel_is_made_is_dropped_not_stopped(tmp_path):
    """Kimi presses Sign Up and leaves the server before the queue reaches his signup. The change
    is found no longer due and dropped: no channel is created, he is not moved, and the league's
    lane is not stopped."""
    league = await _league(tmp_path)

    await _press_sign_up(league)
    league.absent.add(SIGNING_UP)
    await run_queue(league.bot)

    assert league.created == []
    assert await stopped_job(league.db_path) is None
    assert await _driver_state(league) != "PENDING_SIGNUP_COMPLETION"


async def test_a_discarded_channel_creation_leaves_the_driver_free_to_press_sign_up_again(
    tmp_path,
):
    """Discord refuses to create Kimi's channel and an admin discards the job. His status is not
    moved (no `start`), no 24 hours are armed and no welcome is posted; he is told to press Sign
    Up again, and pressing it again, once Discord allows, starts his signup."""
    league = await _league(tmp_path)
    create = league.guild.create_text_channel
    league.guild.create_text_channel = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    league.channel(SIGNUP_CHANNEL).category.create_text_channel = league.guild.create_text_channel

    press = await _press_sign_up(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "create_channel"
    await discard_job(league.bot)

    assert await _driver_state(league) != "PENDING_SIGNUP_COMPLETION"
    assert _armed(league, INACTIVITY_TIMER) == []
    assert "Press Sign Up again" in _answered(press)

    league.guild.create_text_channel = create
    league.channel(SIGNUP_CHANNEL).category.create_text_channel = create
    again = await _press_sign_up(league)
    await run_queue(league.bot)

    assert "already have a signup in progress" not in _answered(again)
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert (await _wizard(league)) is not None


async def test_a_discarded_start_posts_no_welcome_and_arms_no_24_hours(tmp_path):
    """Kimi presses Sign Up; his channel (6500) is made, but the save that starts his signup
    fails (the driver profiles refuse the write) and an admin discards that job. The jobs after it
    find they are no longer due (core :130): no 24 hours are armed and neither the welcome nor
    the first question is posted in his channel; he is told his signup was not started and to
    press Sign Up again."""
    league = await _league(tmp_path)
    for event in ("INSERT", "UPDATE"):
        await league.write(
            f"CREATE TRIGGER start_refused_{event.lower()} BEFORE {event} ON driver_profiles "
            "BEGIN SELECT RAISE(ABORT, 'the signup could not be started'); END"
        )

    press = await _press_sign_up(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "start"
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert league.created == [NEW_CHANNEL]
    assert _armed(league, INACTIVITY_TIMER) == []
    assert league.channel(NEW_CHANNEL).texts == []
    assert "Your signup was not started" in _answered(press)


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


async def test_sign_up_while_a_close_waits_on_the_queue_is_refused_as_closing(tmp_path):
    """The close time has come and the timer has asked the queue to close the window, which has
    not yet run. Kimi presses Sign Up: he is told, plainly, that signups are being closed and a
    new signup cannot start; no `signup.start` change is asked, and when the queue runs no signup
    channel is created and he is not signing up."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended

    league = await _league(tmp_path)
    await close_signups_unattended(league.bot, cause="timer")

    press = await _press_sign_up(league)

    assert "⛔ Signups are being closed, so a new signup cannot start." in _answered(press)
    assert await _changes_of(league, START_KIND) == []
    await run_queue(league.bot)
    assert league.created == []
    assert await _driver_state(league) in (None, "NOT_SIGNED_UP")


@pytest.mark.parametrize(
    ("case", "named"),
    [
        pytest.param("no Manage Channels", "Manage Channels"),
        pytest.param("the category is full", CATEGORY_NAME),
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


async def test_a_discarded_deletion_of_the_earlier_channel_is_named_in_the_reply(tmp_path):
    """Kimi signed up before and withdrew, his old signup channel (6405) standing. He presses Sign
    Up, Discord refuses to delete the old channel and an admin discards that job. The new channel
    is created and his signup starts, and the reply says the earlier channel was left standing,
    which the bot will never delete now that his wizard's row names the new one."""
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
    old = _held(league, OLD_CHANNEL)
    old.delete = AsyncMock(side_effect=http_error(discord.Forbidden, status=403, text="Missing"))

    press = await _press_sign_up(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "delete_old_channel"
    await discard_job(league.bot)

    assert league.created == [NEW_CHANNEL]
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    reply = _answered(press)
    assert f"<#{NEW_CHANNEL}>" in reply
    assert "earlier signup could not be deleted and was left standing" in reply


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


async def test_sign_up_s_channel_is_seen_only_by_the_driver_and_both_staff_tiers(tmp_path):
    """Kimi presses Sign Up and the queue runs. His channel is named for him, ending
    "-signup", and is created hidden from everyone but Kimi, the league's interaction role and
    its league admin role, so a league admin without the interaction role still sees the signup
    they are entitled to approve (#116)."""
    league = await _league(tmp_path)
    [roles] = await league.rows(
        "SELECT interaction_role_id, league_admin_role_id FROM server_configs"
    )

    await _press_sign_up(league)
    await run_queue(league.bot)

    created = league.guild.create_text_channel.await_args
    assert str(created.args[0] if created.args else created.kwargs["name"]).endswith("-signup")
    overwrites = created.kwargs["overwrites"]
    assert overwrites[league.guild.default_role].view_channel is False
    kimi = [key for key in overwrites if getattr(key, "id", None) == SIGNING_UP]
    assert kimi and overwrites[kimi[0]].view_channel is True
    staff = {roles["interaction_role_id"], roles["league_admin_role_id"]} - {None}
    assert staff and staff <= {getattr(key, "id", None) for key in overwrites}


async def test_the_wizard_is_saved_with_the_configuration_as_it_stood_and_no_reserve_team(
    tmp_path,
):
    """Kimi presses Sign Up and the queue runs. His wizard's row holds the signup configuration
    frozen as it stood when his channel was created, its team buttons the league's full-time
    teams with no reserve team, and his draft already holding his username and server name.
    A manager changing the configuration afterwards leaves the saved snapshot as it was."""
    import json

    league = await _league(tmp_path)

    await _press_sign_up(league)
    await run_queue(league.bot)

    wizard = await _wizard(league)
    assert wizard is not None and int(wizard["signup_channel_id"]) == NEW_CHANNEL
    snapshot = json.loads(wizard["config_snapshot_json"])
    teams = snapshot.get("team_names") or []
    assert teams and not any("reserve" in str(team).lower() for team in teams)
    draft = json.loads(wizard["draft_answers_json"] or "{}")
    assert "discord_username" in draft and draft.get("server_display_name") == "Kimi"

    await league.write("UPDATE signup_module_settings SET nationality_required = 0")
    assert (await _wizard(league) or {}).get("config_snapshot_json") == wizard[
        "config_snapshot_json"
    ]


@pytest.mark.parametrize(
    ("nationality", "first"),
    [
        pytest.param(True, "Step 1 — Nationality", id="nationality required"),
        pytest.param(False, "Platform", id="nationality not required"),
    ],
)
async def test_the_first_question_is_nationality_only_where_required_and_a_button_step_locks_typing(
    tmp_path, nationality, first
):
    """The league requires each driver's nationality, or does not. Kimi presses Sign Up and the
    queue runs. His welcome, posted in his new channel, asks nationality first where it is
    required, his wizard at Collecting Nationality and his typing left open; otherwise it asks
    his platform first, his wizard at Collecting Platform, and his typing in the channel is
    taken away, so a stray message cannot be read as an answer to a button-only step."""
    league = await _league(tmp_path)
    # The settings row is saved, not updated: a league that never changed them has none.
    from leaguebot.signup.models.signup_module import SignupModuleSettings

    await league.bot.signup_module_service.save_settings(SignupModuleSettings(
        nationality_required=nationality, time_type="TIME_TRIAL", time_image_required=True,
    ))
    await league.write("UPDATE season_signup_config SET nationality_required = ?", int(nationality))

    await _press_sign_up(league)
    await run_queue(league.bot)

    welcome = "\n".join(league.channel(NEW_CHANNEL).texts)
    assert first in welcome
    wizard = await _wizard(league)
    wanted = "COLLECTING_NATIONALITY" if nationality else "COLLECTING_PLATFORM"
    assert wizard is not None and wizard["wizard_state"] == wanted
    locked = [call for call in league.channel(NEW_CHANNEL).set_permissions.await_args_list
              if getattr(call.args[0], "id", None) == SIGNING_UP
              and call.kwargs.get("send_messages") is False]
    assert bool(locked) is (not nationality)


async def test_sign_up_welcomes_the_driver_by_name_and_writes_its_started_line(tmp_path):
    """Kimi presses Sign Up and the queue runs. His welcome says "Welcome to the signup wizard,
    <@105>!", and the log channel carries the signup's "Signup | Started" line naming him."""
    league = await _league(tmp_path)

    await _press_sign_up(league)
    await run_queue(league.bot)

    assert f"Welcome to the signup wizard, <@{SIGNING_UP}>!" in "\n".join(
        league.channel(NEW_CHANNEL).texts
    )
    started = [line for line in league.log().splitlines() if "Signup | Started" in line]
    assert len(started) == 1 and f"<@{SIGNING_UP}>" in started[0]


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("signups closed", id="signups closed"),
        pytest.param("module disabled", id="module disabled"),
    ],
)
async def test_a_leftover_sign_up_button_is_refused_once_signups_are_closed(tmp_path, case):
    """A Sign Up button the close could not delete still stands. Signups are now closed, or the
    signup module is off, and Kimi presses it: he is told "⛔ Signups are closed.", one refusal
    line naming him is written, no `signup.start` change is asked, and when the queue runs no
    channel is created and he is not signing up."""
    league = await _league(tmp_path)
    if case == "signups closed":
        await league.write("UPDATE signup_module_config SET signups_open = 0")
    else:
        await league.write("UPDATE server_configs SET signup_module_enabled = 0")

    press = await _press_sign_up(league)

    assert "⛔ Signups are closed." in _answered(press)
    refusals = [line for line in league.log().splitlines() if line.startswith("⛔")]
    assert len(refusals) == 1 and f"<@{SIGNING_UP}>" in refusals[0]
    assert await _changes_of(league, START_KIND) == []
    await run_queue(league.bot)
    assert league.created == []
    assert await _driver_state(league) in (None, "NOT_SIGNED_UP")


async def test_signing_up_again_disarms_the_abandoned_wizard_s_timers(tmp_path):
    """Kimi abandoned an earlier signup: he is Not Signed Up, his old channel (6405) standing
    and named by his wizard's row. He presses Sign Up and the queue runs: the old wizard's 24
    hours of inactivity and its channel's deletion are disarmed, so neither fires into the
    signup he has just begun, and fresh 24 hours are armed for it."""
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

    disarmed = {call.args[:2] for call in league.bot.scheduler_service.disarm.call_args_list}
    assert {(INACTIVITY_TIMER, str(SIGNING_UP)), (DELETE_TIMER, str(SIGNING_UP))} <= disarmed
    assert _armed(league, INACTIVITY_TIMER)


async def test_an_abandoned_channel_already_gone_does_not_stop_sign_up(tmp_path):
    """Kimi abandoned an earlier signup whose channel (6405) was since deleted by hand, though
    his wizard's row still names it. He presses Sign Up and the queue runs: `delete_old_channel`
    completes, finding the channel gone, his new channel (6500) is created and the change is
    done, the league lane not stopped."""
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

    await _press_sign_up(league)
    await run_queue(league.bot)

    assert league.created == [NEW_CHANNEL]
    [change] = await _changes_of(league, START_KIND)
    assert change["state"] == "DONE"
    assert await stopped_job(league.db_path, "league") is None


# ---------------------------------------------------------------------------
# Cancel Signup (C76)
# ---------------------------------------------------------------------------


async def _mid_signup(tmp_path: Any) -> SeasonLeague:
    """`_league`, Kimi part-way through signing up (Pending Signup Completion), his signup
    channel (6405) standing and recording what it is sent and its permission edits."""
    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_SIGNUP_COMPLETION")
    _held(league, KIMI_CHANNEL)
    return league


def _told(league: SeasonLeague) -> list[str]:
    return [text for text in league.channel(KIMI_CHANNEL).texts if "withdrawn" in text.lower()]


def _locked(league: SeasonLeague) -> bool:
    return any(call.kwargs.get("send_messages") is False
               for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list)


async def test_a_withdrawal_ends_the_signup_tells_the_driver_and_locks_the_channel_as_jobs(
    tmp_path,
):
    """Kimi presses Cancel Signup part-way through signing up. At once he is answered privately
    that it is under way, and nothing is posted in his channel. When the queue runs he is Not
    Signed Up, his channel is told his signup was withdrawn, his typing there is revoked, and the
    channel's deletion is armed a day after the job ran."""
    league = await _mid_signup(tmp_path)

    press = await _press_cancel(league)

    assert acknowledgement(press).startswith("⏳")
    assert league.channel(KIMI_CHANNEL).texts == []

    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert len(_told(league)) == 1
    assert _locked(league)
    assert _armed(league, DELETE_TIMER) == [
        (DELETE_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]


async def test_a_refused_closing_notice_stops_the_queue(tmp_path):
    """Kimi presses Cancel Signup, and Discord refuses every post in his signup channel. When
    the queue runs he is Not Signed Up (the league's change is done), but the turns in his
    channel stop at `signup_notice`: no notice, his typing not revoked and no deletion armed.
    The league lane is not stopped."""
    league = await _mid_signup(tmp_path)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _press_cancel(league)
    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "signup_notice"
    assert await stopped_job(league.db_path, "league") is None
    assert _told(league) == []
    assert not _locked(league)
    assert _armed(league, DELETE_TIMER) == []


async def test_withdraw_changes_the_status_on_the_league_and_the_notice_comes_back_on_the_driver_s_lane(
    tmp_path,
):
    """Kimi presses Cancel Signup and the queue runs. The `signup.end` change that returns him to
    Not Signed Up runs on the league lane; it asks back a `signup.closing` change, asked after
    it, on the lane of his signup channel, which posts the notice there."""
    league = await _mid_signup(tmp_path)

    await _press_cancel(league)
    await run_queue(league.bot)

    [end] = await _changes_of(league, END_KIND)
    [closing] = await _changes_of(league, CLOSING_KIND)
    assert (_lane(end), _lane(closing)) == ("league", KIMI_LANE)
    assert end["id"] < closing["id"]
    assert (end["state"], closing["state"]) == ("DONE", "DONE")
    assert len(_told(league)) == 1


# ---------------------------------------------------------------------------
# The signup's expiry (C77)
# ---------------------------------------------------------------------------

#: A second driver signing up alongside Kimi, active an hour ago.
OTHER = SIGNING_UP + 10


async def _idle(league: SeasonLeague, user_id: int, since: Any) -> None:
    """Driver *user_id* part-way through signing up (Pending Signup Completion, asked their
    nationality), their signup channel standing, last active at *since*."""
    await signing_up(league, user_id, state="PENDING_SIGNUP_COMPLETION")
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NATIONALITY', "
        "last_activity_at = ? WHERE discord_user_id = ?",
        since.isoformat(), str(user_id),
    )
    _held(league, SIGNUP_CHANNEL_BASE + user_id)


def _timer_callback(league: SeasonLeague, timer: str) -> Any:
    """The callback the builder registered for *timer*."""
    [callback] = [call.args[1]
                  for call in league.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == timer]
    return callback


def _expired(league: SeasonLeague, user_id: int = SIGNING_UP) -> list[str]:
    return [text for text in league.channel(SIGNUP_CHANNEL_BASE + user_id).texts
            if "expired" in text.lower()]


def _wizards(league: SeasonLeague) -> Any:
    """The wizard service as the builder makes it, handed the change queue when built, over the
    league's real signup module service."""
    from leaguebot.signup.services.wizard_service import WizardService

    wizards = WizardService(
        league.db_path, league.bot.scheduler_service, league.bot.output_router,
        change_queue=league.bot.change_queue,
    )
    wizards.set_bot(league.bot)
    return wizards


async def test_an_expiry_asked_by_its_timer_ends_the_signup(tmp_path):
    """Kimi is part-way through signing up, last active 24 hours and a minute ago. His 24-hour
    timer fires: it asks the queue for `signup.end` as the bot, on the league lane, and nothing
    is posted yet. When the queue runs he is Not Signed Up, his channel is told his signup
    expired, his typing there is revoked, the channel's deletion is armed a day after the job
    ran, and the lapse is recorded in the log channel. No change is asked back of his lane: the
    expiry is the league's whole."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=24, minutes=1))

    await _timer_callback(league, INACTIVITY_TIMER)(str(SIGNING_UP))

    [end] = await _changes_of(league, END_KIND)
    assert (_lane(end), end["origin"]) == ("league", "BOT")
    assert _expired(league) == []

    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert len(_expired(league)) == 1
    assert _locked(league)
    assert _armed(league, DELETE_TIMER) == [
        (DELETE_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]
    assert await _changes_of(league, CLOSING_KIND) == []
    assert "lapsed" in league.log().lower()


async def test_ending_a_signup_cannot_be_discarded(tmp_path):
    """Kimi is correcting his signup (Pending Driver Correction), last active 24 hours and a
    minute ago. His 24-hour timer fires, but the save ending his signup fails, so the league lane
    stops at `end`. A league admin's Discard on it is refused, saying Kimi's signup would never
    end and the season's placements could not be confirmed until the bot restarts. He is still
    Pending Driver Correction, his channel is not told his signup expired, his typing there is
    not revoked, the channel's deletion is not armed, and the queue stays stopped there. Once the
    fault is lifted, a Retry ends it: he is Not Signed Up, told once, his typing revoked and the
    deletion armed a day on."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=24, minutes=1))
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_DRIVER_CORRECTION' "
        "WHERE discord_user_id = ?", str(SIGNING_UP),
    )
    await refuse_the_job(league.db_path, END_KIND, "end", True)

    await _timer_callback(league, INACTIVITY_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "end"

    refused = await discard_job(league.bot, lane="league")

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}>'s signup would never end, "
        "and the season's placements could not be confirmed until the bot restarts. Fix what "
        "stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert _expired(league) == []
    assert not _locked(league)
    assert _armed(league, DELETE_TIMER) == []

    await refuse_the_job(league.db_path, END_KIND, "end", False)
    await retry_job(league.bot, lane="league")

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert len(_expired(league)) == 1
    assert _locked(league)
    assert _armed(league, DELETE_TIMER) == [
        (DELETE_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]


# ---------------------------------------------------------------------------
# A signup whose channel is gone when its review panel is due (#439, slice 8, F1)
# ---------------------------------------------------------------------------

#: Kimi as the log channel shows him.
KIMI_NAMED = f"Kimi (`<@{SIGNING_UP}>`)"
#: Kimi as the panel's job names him, carried in the ending's payload; the log channel wraps the
#: mention when it writes the line.
KIMI_NAMED_IN_PAYLOAD = f"Kimi (<@{SIGNING_UP}>)"
#: The line the ending writes in the log channel.
CHANNEL_GONE_LINE = (
    f"{KIMI_NAMED} | Signup | Ended\n  channel: <#{KIMI_CHANNEL}>\n  Their signup channel was "
    "gone when their review panel was due, so their signup ended. They may press Sign Up again "
    "while signups are open."
)


async def _ask_channel_gone(league: SeasonLeague) -> int | None:
    """Ask the queue, as the bot, to end Kimi's signup because his signup channel (6405) was gone
    when his review panel was due, as the panel's job does; gives the change's id, or None where
    it was not due."""
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.signup.services.wizard_changes import CHANNEL_GONE

    return await league.bot.change_queue.ask(
        END_KIND,
        {"user_id": str(SIGNING_UP), "reason": CHANNEL_GONE, "channel_id": KIMI_CHANNEL,
         "named": KIMI_NAMED_IN_PAYLOAD},
        what=f"ending <@{SIGNING_UP}>'s signup, their signup channel being gone",
        origin=ChangeOrigin.BOT,
    )


async def _withdrew(league: SeasonLeague) -> None:
    await league.write(
        "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )


async def _left(league: SeasonLeague) -> None:
    await league.write(
        "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await league.write(
        "DELETE FROM signup_wizard_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )


async def _signed_up_again(league: SeasonLeague) -> None:
    await league.write(
        "UPDATE signup_wizard_records SET signup_channel_id = ? WHERE discord_user_id = ?",
        NEW_CHANNEL, str(SIGNING_UP),
    )


@pytest.mark.parametrize(
    ("moved_on", "state"),
    [(_withdrew, "NOT_SIGNED_UP"), (_left, "NOT_SIGNED_UP"),
     (_signed_up_again, "PENDING_ADMIN_APPROVAL")],
    ids=["withdrew", "left", "signed up again"],
)
async def test_a_driver_moved_on_before_their_gone_channel_is_found_is_not_ended_again(
    tmp_path, moved_on, state,
):
    """Kimi awaits approval, his signup channel 6405 deleted. Before the ending of his signup for
    the gone channel is asked, he has withdrawn, or left the server (his wizard's row gone with
    him), or signed up again and is awaiting approval with a new channel 6500. The ending is not
    due: nothing is saved, he stays where he is, and nothing is written to the log channel."""
    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    league.remove_channel(KIMI_CHANNEL)
    await moved_on(league)
    wizard = await _wizard(league)

    asked = await _ask_channel_gone(league)
    await run_queue(league.bot)

    assert asked is None
    assert await _changes_of(league, END_KIND) == []
    assert await _driver_state(league) == state
    assert await _wizard(league) == wizard
    assert "| Signup | Ended" not in league.log()


async def test_ending_a_signup_whose_channel_is_gone_cannot_be_discarded(tmp_path):
    """Kimi awaits approval, his signup channel 6405 deleted, and the ending of his signup for
    the gone channel is asked with his name already found, so its one job is `end`. Its save
    fails, stopping the league lane there. A league admin's Discard on it is refused, saying Kimi
    would be left awaiting approval with no signup channel and no review panel, and the season's
    placements could not be confirmed; he is still awaiting approval and the queue stays stopped.
    Once the fault is lifted, a Retry ends his signup: he is Not Signed Up, his wizard ended, and
    the log channel is told once, naming him and the channel, that his signup ended because his
    signup channel was gone."""
    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    league.remove_channel(KIMI_CHANNEL)
    await refuse_the_job(league.db_path, END_KIND, "end", True)

    assert await _ask_channel_gone(league) is not None
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "end"
    assert await _job_names(league, END_KIND) == ["end"]

    refused = await discard_job(league.bot, lane="league")

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}> would be left awaiting "
        "approval with no signup channel and no review panel, and the season's placements could "
        "not be confirmed. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"

    await refuse_the_job(league.db_path, END_KIND, "end", False)
    await retry_job(league.bot, lane="league")

    assert await stopped_job(league.db_path) is None
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert (await _wizard(league))["wizard_state"] == "UNENGAGED"
    assert league.log().count(CHANNEL_GONE_LINE) == 1


async def test_a_driver_whose_signup_ended_for_a_gone_channel_may_sign_up_again(tmp_path):
    """Kimi's signup ended because his signup channel 6405 was gone when his review panel was due;
    his wizard still names 6405. He presses Sign Up and the queue runs: the old channel is found
    gone, a new channel 6500 is created, he is part-way through signing up in it, and the league
    lane is not stopped."""
    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    league.remove_channel(KIMI_CHANNEL)
    await _ask_channel_gone(league)
    await run_queue(league.bot)
    assert await _driver_state(league) == "NOT_SIGNED_UP"

    await _press_sign_up(league)
    await run_queue(league.bot)

    assert league.created == [NEW_CHANNEL]
    [change] = await _changes_of(league, START_KIND)
    assert change["state"] == "DONE"
    assert await stopped_job(league.db_path, "league") is None
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert (await _wizard(league))["signup_channel_id"] == NEW_CHANNEL


#: A signup channel deleted while its signup is under way is not yet seen (#439, slice 8, G2).
CHANNEL_DELETED_UNBUILT = "#439: a signup channel deleted by hand does not yet end its signup"


@pytest.mark.xfail(strict=True, reason=CHANNEL_DELETED_UNBUILT)
@pytest.mark.parametrize("state", ["NOT_SIGNED_UP", "UNASSIGNED"],
                         ids=["not signed up", "unassigned"])
async def test_a_channel_gone_ending_is_not_due_once_the_driver_is_no_longer_signing_up(
    tmp_path, state,
):
    """Kimi's signup has ended: he is Not Signed Up (it expired) or Unassigned (he was approved),
    his wizard ended but still naming his signup channel 6405, which is then deleted. The ending
    of his signup for a deleted channel is asked, as the bot sees the channel go. It is not due:
    no change is taken up, he stays where he is, his wizard is untouched and nothing is written
    to the log channel."""
    from leaguebot.signup.services.wizard_changes import DELETED, ask_channel_gone

    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    await league.write(
        "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
        state, str(SIGNING_UP),
    )
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    league.remove_channel(KIMI_CHANNEL)
    wizard = await _wizard(league)

    asked = await ask_channel_gone(
        league.bot.change_queue, user_id=str(SIGNING_UP), channel_id=KIMI_CHANNEL,
        named=KIMI_NAMED_IN_PAYLOAD, found=DELETED,
    )
    await run_queue(league.bot)

    assert asked is None
    assert await _changes_of(league, END_KIND) == []
    assert await _driver_state(league) == state
    assert await _wizard(league) == wizard
    assert "| Signup | Ended" not in league.log()


async def test_an_answer_typed_before_the_24_hours_counts_though_it_runs_after(tmp_path):
    """Kimi was last active 23 hours and 59 minutes ago. He types his nationality, which waits
    its turn on his lane; two minutes later, his 24 hours run out and the timer asks for his
    signup's expiry. When the queue runs, after the 24 hours, the answer counts: he is still
    Pending Signup Completion, no expiry notice is posted, his channel is not locked and no
    deletion is armed."""
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from tests.support.change_queue import typed_message

    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=23, minutes=59))
    kimi = league._member(SIGNING_UP)
    kimi.bot = False
    answer = typed_message(league.channel(KIMI_CHANNEL), kimi, "British",
                           created_at=league.clock.now)
    answer.guild = league.guild
    league.bot.wizard_service.get_wizard_by_channel = AsyncMock(
        side_effect=league.bot.signup_module_service.get_wizard_by_channel
    )
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot

    await cog.on_message(answer)
    league.clock.advance(minutes=2)
    await _timer_callback(league, INACTIVITY_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert _expired(league) == []
    assert not _locked(league)
    assert _armed(league, DELETE_TIMER) == []


async def test_a_timer_that_fires_before_the_24_hours_since_the_last_answer_ends_nothing(
    tmp_path,
):
    """Kimi answered 23 hours ago, and a timer armed earlier fires now. The expiry it asks is
    found not due: when the queue runs he is still Pending Signup Completion, told nothing, his
    channel not locked and no deletion armed."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=23))

    await _timer_callback(league, INACTIVITY_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert _expired(league) == []
    assert not _locked(league)
    assert _armed(league, DELETE_TIMER) == []


async def test_a_restart_asks_the_expiry_of_each_lapsed_signup(tmp_path):
    """The bot starts again. Kimi was last active 25 hours ago, a second driver (115) an hour
    ago, both part-way through signing up. Recovering the wizards (the wizard service built with
    the change queue) asks the queue for Kimi's `signup.end` as the bot, on the league lane,
    before the queue starts, and asks nothing for the second driver. When the queue runs Kimi is
    Not Signed Up and told his signup expired; the second driver is still signing up."""
    from datetime import datetime, timezone

    league = await _league(tmp_path)
    host_now = datetime.now(timezone.utc)
    await _idle(league, SIGNING_UP, host_now - timedelta(hours=25))
    await _idle(league, OTHER, host_now - timedelta(hours=1))

    await _wizards(league).recover_wizards()

    [end] = await _changes_of(league, END_KIND)
    assert (_lane(end), end["origin"]) == ("league", "BOT")
    assert str(SIGNING_UP) in str(end["payload"])
    assert str(OTHER) not in str(end["payload"])

    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert len(_expired(league)) == 1
    assert await _driver_state(league, OTHER) == "PENDING_SIGNUP_COMPLETION"
    assert _expired(league, OTHER) == []


# ---------------------------------------------------------------------------
# A rejection (C78)
# ---------------------------------------------------------------------------

#: The league manager reviewing Kimi's signup, and the reason they type after Reject.
REVIEWER = 77
REASON = "Lap time not credible"


def _rejected(league: SeasonLeague) -> list[str]:
    return [text for text in league.channel(KIMI_CHANNEL).texts if "rejected" in text.lower()]


def _said_to(press: Any) -> str:
    """All the manager's stored press was used to tell them: its follow-ups and its edits."""
    return " ".join(
        str(call) for call in (press.followup.send.call_args_list
                               + press.edit_original_response.call_args_list)
    )


async def test_a_reject_reason_is_deleted_and_the_signup_ended_as_jobs(tmp_path):
    """Kimi's signup awaits approval, his signup channel (6405) standing. Manager 77 has pressed
    Reject on the review panel, which waits for the reason (the press deferred and parked as the
    button parks it), and types "Lap time not credible" in Kimi's channel. The reason is not
    deleted at once. A `signup.end` change is asked on the league lane through the stored press;
    when the queue runs Kimi is Not Signed Up, and it asks back a `signup.closing` change on his
    lane, whose `delete_reason` deletes the manager's message before `signup_notice` tells the
    channel the signup was rejected, giving the reason. His typing is revoked, the channel's
    deletion armed a day on, and the manager is told the signup was rejected."""
    from leaguebot.signup.cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog
    from tests.support.change_queue import typed_message

    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    _held(league, KIMI_CHANNEL)
    manager = league._member(REVIEWER)
    manager.bot = False
    press = member_interaction(league.bot, user=manager)
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    await press.response.defer(ephemeral=True)
    # The acknowledgement of a deferred press is its first follow-up, and editing that message is
    # editing the press's original response, as Discord has it.
    press.followup.send.return_value = MagicMock(edit=press.edit_original_response)
    _PENDING_REASONS[(KIMI_CHANNEL, REVIEWER)] = {
        "action": "reject",
        "discord_user_id": str(SIGNING_UP),
        "actor": manager,
        "guild": league.guild,
        "interaction": press,
        "followup": press.followup,
    }
    reason = typed_message(league.channel(KIMI_CHANNEL), manager, REASON,
                           created_at=league.clock.now)
    reason.guild = league.guild
    try:
        await AdminReviewCog(league.bot).on_message(reason)

        assert not reason.gone
        [end] = await _changes_of(league, END_KIND)
        assert _lane(end) == "league"

        await run_queue(league.bot)
    finally:
        _PENDING_REASONS.clear()

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    [closing] = await _changes_of(league, CLOSING_KIND)
    assert _lane(closing) == KIMI_LANE
    assert _in_order(await _job_names(league, CLOSING_KIND), ["delete_reason", "signup_notice"])
    assert reason.gone
    told = _rejected(league)
    assert len(told) == 1 and REASON in told[0]
    assert _locked(league)
    assert _armed(league, DELETE_TIMER) == [
        (DELETE_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))
    ]
    assert "rejected" in _said_to(press).lower()


# ---------------------------------------------------------------------------
# The held channel's deletion (C79)
# ---------------------------------------------------------------------------

DELETE_KIND = "signup.channel.delete"
#: Kimi's new account after a reassign, and the held signup channel it holds of its own.
NEW_ACCOUNT = SIGNING_UP + 1000
NEW_ACCOUNT_CHANNEL = 6600


async def _held_after_signup(league: SeasonLeague, user_id: int = SIGNING_UP,
                             cid: int = KIMI_CHANNEL) -> Any:
    """Driver *user_id*'s signup has ended (Not Signed Up), their signup channel *cid* held,
    read-only, its wizard's row naming it; gives the channel."""
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver) "
        "VALUES (?, 'NOT_SIGNED_UP', 0)",
        str(user_id),
    )
    await league.write(
        "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, signup_channel_id) "
        "VALUES (?, 'UNENGAGED', ?)",
        str(user_id), cid,
    )
    return _held(league, cid)


async def test_the_24_hour_deletion_is_a_job_and_a_refused_one_stops_the_queue(tmp_path):
    """Kimi's signup ended a day ago and his signup channel (6405) is held. Its 24-hour timer
    fires and asks `signup.channel.delete`; Discord refuses to delete the channel. The turns in
    his channel stop at `delete_channel`, the channel and his wizard's row standing. Once
    Discord allows it, Retry deletes the channel, and the job's record deletes the row."""
    from tests.support.change_queue import retry_job

    league = await _league(tmp_path)
    held = await _held_after_signup(league)
    refusal = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    held.delete = AsyncMock(side_effect=refusal)

    await _timer_callback(league, DELETE_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "delete_channel"
    assert await _wizard(league) is not None

    held.delete = AsyncMock(return_value=None)
    await retry_job(league.bot, lane=KIMI_LANE)

    held.delete.assert_awaited_once()
    assert await _wizard(league) is None
    assert await stopped_job(league.db_path, KIMI_LANE) is None


async def test_the_24_hour_deletion_runs_on_the_driver_s_lane_and_a_refusal_stops_only_it(
    tmp_path,
):
    """Kimi's signup channel (6405) is held. Its 24-hour timer fires: the `signup.channel.delete`
    change is asked as the bot on the lane of his channel, not the league's. Discord refuses the
    deletion: his lane stops, and the league lane does not."""
    league = await _league(tmp_path)
    held = await _held_after_signup(league)
    held.delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _timer_callback(league, DELETE_TIMER)(str(SIGNING_UP))

    [change] = await _changes_of(league, DELETE_KIND)
    assert (_lane(change), change["origin"]) == (KIMI_LANE, "BOT")

    await run_queue(league.bot)

    assert await stopped_job(league.db_path, KIMI_LANE) is not None
    assert await stopped_job(league.db_path, "league") is None
    assert league.channel(KIMI_CHANNEL) is held


async def test_driver_reassign_asks_the_held_channel_s_deletion(tmp_path):
    """Kimi's old account (105) holds his signup channel (6405); his new account (1105) holds a
    held channel of its own (6600). `/driver reassign` moves him to the new account, so the old
    account's channel is to go. Nothing is deleted at once: a `signup.channel.delete` change is
    asked on the league lane, the command's. When the queue runs channel 6405 is deleted and the
    old account's wizard row with it; the new account keeps channel 6600."""
    league = await _league(tmp_path)
    await _held_after_signup(league)
    await _held_after_signup(league, NEW_ACCOUNT, NEW_ACCOUNT_CHANNEL)

    await _wizards(league).move_held_channel(str(SIGNING_UP), str(NEW_ACCOUNT), league.guild)

    assert league.order == []
    [change] = await _changes_of(league, DELETE_KIND)
    assert _lane(change) == "league"

    await run_queue(league.bot)

    assert league.order == [f"delete {KIMI_CHANNEL}"]
    assert await _wizard(league) is None
    theirs = await _wizard(league, NEW_ACCOUNT)
    assert theirs is not None and int(theirs["signup_channel_id"]) == NEW_ACCOUNT_CHANNEL


# ---------------------------------------------------------------------------
# A member leaving mid-signup (C80)
# ---------------------------------------------------------------------------

MEMBER_LEFT_KIND = "signup.member_left"
#: A change of the test's own on Kimi's lane, standing for one of his turns.
KIMI_TURN = "test.kimi_turn"


async def _leave(league: SeasonLeague, user_id: int = SIGNING_UP) -> None:
    """Driver *user_id* leaves the league's server, and the signup cog's listener hears it.
    Today's wizard clean-up off the queue is a stand-in that does nothing, so that only what the
    listener asks of the queue shows."""
    from leaguebot.signup.cogs.signup_cog import SignupCog

    league.bot.wizard_service.handle_member_remove = AsyncMock(return_value=None)
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    await cog.on_member_remove(league._member(user_id))


async def _kimi_signing_up(db: Any, _payload: dict[str, Any]) -> bool:
    """The test turn's `conversation_open`: Kimi's wizard is still running."""
    cursor = await db.execute(
        "SELECT 1 FROM signup_wizard_records WHERE discord_user_id = ? "
        "AND wizard_state != 'UNENGAGED'",
        (str(SIGNING_UP),),
    )
    return await cursor.fetchone() is not None


def _stuck_turn(league: SeasonLeague) -> list[str]:
    """Register `KIMI_TURN`, a change of the test's own on Kimi's lane: one job posting in his
    channel, which Discord refuses, its conversation read from his wizard's row. Gives the list
    each try that reaches Discord is noted in."""
    from leaguebot.core.models.change import (
        PlannedStep,
        StepFailedOnDiscord,
        StepKind,
        StepResult,
        Verdict,
    )
    from leaguebot.core.services.change_queue import ChangeType, Step
    from tests.support.change_queue import register

    tries: list[str] = []

    async def act(_ctx: Any) -> StepResult:
        tries.append("post")
        raise StepFailedOnDiscord("Missing Access")

    async def check(_ctx: Any) -> Any:
        return Verdict.go()

    register(league.bot, ChangeType(
        kind=KIMI_TURN,
        opening=(PlannedStep("post"),),
        steps={"post": Step("post", StepKind.ACT, act)},
        check=check,
        key=lambda _payload: KIMI_TURN,
        doing=lambda _payload: "Posting Kimi's next question",
        outcome=lambda _ctx: "Posted.",
        lane=lambda _payload: KIMI_LANE,
        conversation_open=_kimi_signing_up,
    ))
    return tries


async def test_a_member_leaving_ends_their_signup_and_deletes_the_channel_as_jobs(tmp_path):
    """Kimi is part-way through signing up when he leaves the server. Nothing is deleted at once:
    the listener asks the queue for `signup.member_left` as the bot, on the league lane (a member
    leaving counts as a command, A27). When the queue runs he is Not Signed Up, his signup channel
    is deleted and his wizard's row with it, and the log channel records that he left, naming
    him."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now)

    await _leave(league)

    assert league.order == []
    [change] = await _changes_of(league, MEMBER_LEFT_KIND)
    assert (_lane(change), change["origin"]) == ("league", "BOT")

    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert league.order == [f"delete {KIMI_CHANNEL}"]
    assert await _wizard(league) is None
    assert any("left" in line.lower() and f"<@{SIGNING_UP}>" in line
               for line in league.bot.log_channel.sent)


async def test_a_member_leaving_whose_signup_record_cannot_be_read_stops_the_league_lane(
    tmp_path, monkeypatch
):
    """Kimi, part-way through signing up, leaves the server, and his signup record cannot be read
    for the line's name. The job that reads it stops the league lane, as any job's fault does,
    rather than write the line under his id: he is still Pending Signup Completion, his channel
    stands, and no line says he left."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now)
    monkeypatch.setattr(
        league.bot.signup_module_service, "get_record",
        AsyncMock(side_effect=RuntimeError("the record could not be read")),
    )

    await _leave(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "name_driver"
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert league.order == []
    assert not any("Driver left server" in line for line in league.bot.log_channel.sent)


async def test_a_member_leaving_while_the_queue_is_stopped_keeps_their_channel_until_it_clears(
    tmp_path,
):
    """The league lane is stopped at a job Discord refuses when Kimi, part-way through signing
    up, leaves the server. His leaving waits behind the stop: his signup channel stands, he is
    still Pending Signup Completion and his wizard's row is kept. Once Retry clears the stop, the
    queue runs on: his channel is deleted, he is Not Signed Up and his wizard's row is gone."""
    from tests.support.change_queue import retry_job
    from tests.support.review_league import block_queue

    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now)
    holder = await block_queue(league, lane="league")

    await _leave(league)
    await run_queue(league.bot)

    assert league.order == []
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert await _wizard(league) is not None

    holder["fail"] = False
    await retry_job(league.bot, lane="league")

    assert league.order == [f"delete {KIMI_CHANNEL}"]
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert await _wizard(league) is None


async def test_a_member_leaving_ends_the_conversation_its_stopped_turn_left_stopped(tmp_path):
    """One of Kimi's turns is stopped on his own lane, at a post in his signup channel Discord
    refuses, when he leaves the server. The league's `signup.member_left` runs regardless: he is
    Not Signed Up and his channel deleted, and the league lane is not stopped. His stopped turn
    stays stopped, the same job (decision 24, "Left stopped"). An admin's Retry on his lane then
    finds it no longer due: nothing more is tried on Discord and his lane is no longer stopped."""
    from tests.support.change_queue import retry_job

    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now)
    tries = _stuck_turn(league)
    await league.bot.change_queue.ask(
        KIMI_TURN, {}, interaction=member_interaction(league.bot), what="Kimi's turn",
    )
    await run_queue(league.bot)
    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "post"

    await _leave(league)
    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert league.order == [f"delete {KIMI_CHANNEL}"]
    assert await stopped_job(league.db_path, "league") is None
    still = await stopped_job(league.db_path, KIMI_LANE)
    assert still is not None and still["id"] == job["id"]

    tried = len(tries)
    await retry_job(league.bot, lane=KIMI_LANE)

    assert len(tries) == tried
    assert await stopped_job(league.db_path, KIMI_LANE) is None


# ---------------------------------------------------------------------------
# What an ending leaves behind, carried over from the wizard's own lifecycle tests
# ---------------------------------------------------------------------------


async def test_a_withdrawal_disarms_the_signup_s_24_hours_and_writes_one_withdrawn_line(tmp_path):
    """Kimi presses Cancel Signup part-way through signing up and the queue runs. His 24-hour
    inactivity timer is disarmed, so it cannot later fire against a driver who has withdrawn,
    and the log channel holds exactly one line saying his signup was withdrawn, naming him."""
    league = await _mid_signup(tmp_path)

    await _press_cancel(league)
    await run_queue(league.bot)

    disarmed = [call.args for call in league.bot.scheduler_service.disarm.call_args_list]
    assert (INACTIVITY_TIMER, str(SIGNING_UP)) in disarmed
    withdrawn = [line for line in league.bot.log_channel.sent if "Withdrawn" in line]
    assert len(withdrawn) == 1
    assert f"<@{SIGNING_UP}>" in withdrawn[0]


async def test_a_signup_s_end_looks_the_driver_up_before_its_save_not_in_it(tmp_path, monkeypatch):
    """Kimi presses Cancel Signup and the queue runs. The driver is named by a job of his own
    (`name_driver`) before `end`, with the queue's write lock not held, so the save that ends the
    signup waits on no second connection (architecture.md, "The database"); the line it writes
    still names him."""
    from leaguebot.signup.services import wizard_changes

    league = await _mid_signup(tmp_path)
    held: list[bool] = []
    real = wizard_changes.name_of_member

    async def _spy(bot: Any, member_id: Any) -> str:
        held.append(league.bot.change_queue._writing.locked())
        return await real(bot, member_id)

    monkeypatch.setattr(wizard_changes, "name_of_member", _spy)

    await _press_cancel(league)
    await run_queue(league.bot)

    assert held == [False]
    assert _in_order(await _job_names(league, END_KIND), ["name_driver", "end"])
    withdrawn = [line for line in league.bot.log_channel.sent if "Withdrawn" in line]
    assert len(withdrawn) == 1 and f"<@{SIGNING_UP}>" in withdrawn[0]


async def test_an_expiry_for_a_driver_who_has_since_submitted_changes_nothing(tmp_path):
    """Kimi was last active 25 hours ago, but has since given his last answer: his signup awaits
    a manager's approval and his wizard is parked. His 24-hour timer fires and the queue runs.
    He is still Pending Admin Approval: no expiry notice is posted, his channel is not locked and
    no deletion is armed."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now - timedelta(hours=25))
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_ADMIN_APPROVAL' "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )

    await _timer_callback(league, INACTIVITY_TIMER)(str(SIGNING_UP))
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert _expired(league) == []
    assert not _locked(league)
    assert _armed(league, DELETE_TIMER) == []


@pytest.mark.parametrize("naive", [False, True], ids=["with a zone", "without a zone"])
async def test_a_restart_re_arms_each_open_signup_from_its_own_last_activity(tmp_path, naive):
    """The bot starts again. A driver (115) part-way through signing up was last active an hour
    ago, the time stored with its zone or, as in older rows, without one (read as UTC).
    Recovering the wizards (the wizard service built with the change queue) asks no expiry for
    him, and arms his 24-hour timer for 24 hours after his last activity, not 24 hours from the
    restart."""
    from datetime import datetime, timezone

    league = await _league(tmp_path)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    await _idle(league, OTHER, since)
    if naive:
        await league.write(
            "UPDATE signup_wizard_records SET last_activity_at = ? WHERE discord_user_id = ?",
            since.replace(tzinfo=None).isoformat(), str(OTHER),
        )

    await _wizards(league).recover_wizards()

    assert await _changes_of(league, END_KIND) == []
    assert _armed(league, INACTIVITY_TIMER) == [
        (INACTIVITY_TIMER, str(OTHER), since + timedelta(hours=24))
    ]


async def test_a_restart_asks_no_expiry_of_a_signup_already_ended_or_waiting_on_a_manager(
    tmp_path,
):
    """The bot starts again, a day and more after two signups stopped moving. Kimi's ended
    (withdrawn, lapsed or returned by a close): he is Not Signed Up, his wizard parked. The
    second driver (115) gave his last answer and waits on a manager: Pending Admin Approval, his
    wizard parked. Recovering the wizards asks no expiry of either and arms neither's 24-hour
    timer; when the queue runs nothing is posted in either channel."""
    from datetime import datetime, timezone

    league = await _league(tmp_path)
    since = datetime.now(timezone.utc) - timedelta(hours=30)
    for user_id, state in ((SIGNING_UP, "NOT_SIGNED_UP"), (OTHER, "PENDING_ADMIN_APPROVAL")):
        await _idle(league, user_id, since)
        await league.write(
            "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' "
            "WHERE discord_user_id = ?",
            str(user_id),
        )
        await league.write(
            "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
            state, str(user_id),
        )

    await _wizards(league).recover_wizards()
    await run_queue(league.bot)

    assert await _changes_of(league, END_KIND) == []
    assert _armed(league, INACTIVITY_TIMER) == []
    assert league.channel(KIMI_CHANNEL).texts == []
    assert league.channel(SIGNUP_CHANNEL_BASE + OTHER).texts == []


@pytest.mark.parametrize("state", ["PENDING_ADMIN_APPROVAL", "AWAITING_CORRECTION_PARAMETER"])
async def test_a_member_leaving_while_waiting_on_a_manager_is_cleaned_up_too(tmp_path, state):
    """Kimi has given his last answer, his wizard parked, and waits on a manager: his signup is
    awaiting approval, or a manager has asked him to correct an answer and he has not yet been
    told which. He leaves the server and the queue runs. He is Not Signed Up, his signup channel
    is deleted and his wizard's row with it, as for a driver part-way through the questions."""
    league = await _league(tmp_path)
    await _idle(league, SIGNING_UP, league.clock.now)
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await league.write(
        "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
        state, str(SIGNING_UP),
    )

    await _leave(league)
    await run_queue(league.bot)

    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert league.order == [f"delete {KIMI_CHANNEL}"]
    assert await _wizard(league) is None


@pytest.mark.parametrize("approved", [False, True], ids=["never signed up", "approved"])
async def test_a_member_leaving_with_no_signup_in_progress_is_left_alone(tmp_path, approved):
    """A member leaves the server with no signup in progress: Kimi, who never signed up, or a
    driver (106) whose signup was approved and who is Unassigned. The listener asks
    `signup.member_left` all the same; when the queue runs the change is dropped as no longer
    due. No driver state is written or changed, no channel is deleted, and no signup or wizard
    row is removed."""
    from tests.support.season_league import UNASSIGNED, approved_unplaced

    league = await _league(tmp_path)
    user_id = UNASSIGNED if approved else SIGNING_UP
    if approved:
        await approved_unplaced(league, user_id)
    signups = await league.rows("SELECT * FROM signup_records")

    await _leave(league, user_id)
    await run_queue(league.bot)

    [change] = await _changes_of(league, MEMBER_LEFT_KIND)
    assert change["state"] == "DROPPED"
    assert await _driver_state(league, user_id) == ("UNASSIGNED" if approved else None)
    assert league.order == []
    assert await league.rows("SELECT * FROM signup_records") == signups
