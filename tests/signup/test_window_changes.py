"""`/signup open` on the change queue: the window opens only once its message is up (#439, slice
6b, C68); and the window's close, by command, timer, restart or turning signup off (C69–C71),
whose league is described where its tests begin.

Today the command posts the information message with its Sign Up button itself, then records the
window open, moves the season on and writes its audit row, all off the queue. On the queue it asks
`signup.open`: its check holds the command's refusals, the bot's permissions on the signup channel
included; its `post_info` job posts the message, and that job's record opens the window, keeps the
button's id, moves the season and writes the audit in the same save as its mark (decision 11).
A refused post stops the queue with signups still closed, and a Discard leaves them closed. The
last "signups closed" notice is deleted by a `delete_closed` job after the post.

The league: server 1 with its log and interaction channels, its base role (555001) and driver
role (555002) set and on the server, one availability slot, the signup channel (620) set, and
season 1 waiting for its window. A league manager (77) runs `/signup open` with no tracks and no
close time.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.module_service import ModuleService
from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.signup.services.signup_module_service import SignupModuleService
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    league_double,
    member_interaction,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    tier_member,
    updated_reply,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_BUTTON,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    setup_league,
    signing_up,
)
from tests.support.season_league import SIGNUP_CHANNEL as LEAGUE_SIGNUP_CHANNEL
from tests.support.undecorate import undecorate

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
#: The change `/signup open` asks.
OPEN_KIND = "signup.open"
SIGNUP_CHANNEL = 620
BASE_ROLE = 555001
DRIVER_ROLE = 555002
#: The "signups closed" notice the last close left standing in the signup channel.
CLOSED_NOTICE = 9001
#: The permissions the bot needs on the signup channel to post the Sign Up button.
NEEDED = ("**View Channel**", "**Send Messages**", "**Embed Links**")



def _role(role_id: int) -> MagicMock:
    """A role on the server that the bot can grant."""
    role = MagicMock()
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    return role


class _League:
    def __init__(self, db_path: str, bot: Any, guild: Any, events: list[Any]) -> None:
        self.db_path = db_path
        self.bot = bot
        self.guild = guild
        self.events = events
        self.signup = guild.get_channel(SIGNUP_CHANNEL)

    async def config(self) -> dict[str, Any]:
        async with get_connection(self.db_path) as db:
            cursor = await db.execute("SELECT * FROM signup_module_config WHERE id = 1")
            return dict(await cursor.fetchone())

    async def stage(self) -> str:
        async with get_connection(self.db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons")
            return str((await cursor.fetchone())["stage"])

    async def audits(self) -> list[dict[str, Any]]:
        async with get_connection(self.db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM audit_entries WHERE change_type = 'SIGNUP_OPEN'"
            )
            return [dict(row) for row in await cursor.fetchall()]

    def posts(self) -> list[Any]:
        """The messages the bot posted in the signup channel, in order."""
        return [self.signup.messages[mid] for kind, cid, mid in self.events
                if kind == "send" and cid == SIGNUP_CHANNEL and mid in self.signup.messages]


async def _league(tmp_path: Any, *, closed_notice: bool = False,
                  perms: dict[str, bool] | None = None) -> _League:
    """The league of the module docstring; *closed_notice* leaves the last close's notice
    standing; *perms* overrides the bot's permissions on the signup channel (all granted)."""
    db_path = os.path.join(str(tmp_path), "window.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET base_role_id = ?, driver_role_id = ?",
                         (BASE_ROLE, DRIVER_ROLE))
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id, signups_open, "
            "signup_closed_message_id) VALUES (1, ?, 0, ?)",
            (SIGNUP_CHANNEL, CLOSED_NOTICE if closed_notice else None),
        )
        await db.execute("INSERT INTO signup_availability_slots (day_of_week, time_hhmm) "
                         "VALUES (1, '19:00')")
        await db.execute("INSERT INTO seasons (start_date, status, season_number, stage) "
                         "VALUES ('2026-11-01', 'SETUP', 1, 'WAITING')")
        await db.commit()

    events: list[Any] = []
    signup = channel(SIGNUP_CHANNEL, events)
    signup.mention = "#signups"
    if closed_notice:
        signup.seed(CLOSED_NOTICE, "🔒 Signups are now closed.")
    granted = {"view_channel": True, "send_messages": True, "embed_links": True,
               **(perms or {})}
    signup.permissions_for = MagicMock(return_value=MagicMock(**granted))
    roles = {BASE_ROLE: _role(BASE_ROLE), DRIVER_ROLE: _role(DRIVER_ROLE)}
    guild = MagicMock()
    guild.id = 1
    guild.get_channel = MagicMock(side_effect=lambda cid: signup if cid == SIGNUP_CHANNEL
                                  else None)
    guild.get_role = MagicMock(side_effect=roles.get)
    guild.me = MagicMock()
    signup.guild = guild

    bot = league_double(db_path)
    bot.config_service = ConfigService(db_path)
    bot.module_service = ModuleService(db_path)
    await bot.module_service.set_signup_enabled(True)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.scheduler_service = MagicMock()
    bot.get_guild = MagicMock(return_value=guild)
    known = {SIGNUP_CHANNEL: signup, bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=known.get)
    attach_queue(bot, db_path, now=Clock(NOW))
    return _League(db_path, bot, guild, events)


async def _open(league: _League, close_time: str | None = None) -> Any:
    """League manager 77 runs `/signup open` with no tracks and, unless *close_time* is given, no
    close time."""
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=77))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name="signup open")
    await undecorate(SignupCog.signup_open)(cog, interaction, None, close_time)
    return interaction


async def _open_change(league: _League) -> dict[str, Any] | None:
    found = [row for row in await change_rows(league.db_path) if row["kind"] == OPEN_KIND]
    return found[0] if found else None


async def _jobs(league: _League) -> dict[str, dict[str, Any]]:
    change = await _open_change(league)
    assert change is not None, "no signup.open change was asked"
    return {job["name"]: job for job in await step_rows(league.db_path, change["id"])}


def _refuse_posts(league: _League) -> None:
    league.signup.send_fails = http_error(discord.Forbidden, status=403,
                                          text="Missing Permissions")


async def test_signup_open_is_acknowledged_and_posts_the_information_with_its_button(tmp_path):
    """Manager 77 runs `/signup open`. He is answered at once with an under-way line and nothing
    is posted yet. When the queue runs, one message is posted in the signup channel carrying the
    Sign Up button, and his acknowledgement is updated with a ✅ outcome."""
    league = await _league(tmp_path)

    interaction = await _open(league)

    assert acknowledgement(interaction).startswith("⏳")
    assert league.posts() == []

    await run_queue(league.bot)

    [posted] = league.posts()
    assert type(posted.view).__name__ == "SignupButtonView"
    assert updated_reply(interaction).startswith("✅")
    assert await stopped_job(league.db_path) is None


async def test_the_window_is_opened_in_the_record_of_the_post_that_shows_it(tmp_path):
    """Manager 77 runs `/signup open`. Until the queue runs, signups stay closed and the season
    waits. When it runs, the `post_info` job is done, and with it the window is recorded open,
    the Sign Up button's message id kept, the season moved to SIGNUPS and the SIGNUP_OPEN audit
    row written naming him."""
    league = await _league(tmp_path)

    await _open(league)

    assert not (await league.config())["signups_open"]
    assert await league.stage() == "WAITING"
    assert await league.audits() == []

    await run_queue(league.bot)

    assert (await _jobs(league))["post_info"]["done_at"]
    [posted] = league.posts()
    config = await league.config()
    assert config["signups_open"]
    assert config["signup_button_message_id"] == posted.id
    assert await league.stage() == "SIGNUPS"
    [audit] = await league.audits()
    assert str(audit["actor_id"]) == "77"


async def test_the_last_closed_notice_is_deleted_after_the_information_is_posted(tmp_path):
    """The last close's "signups closed" notice still stands in the signup channel. Manager 77
    runs `/signup open` and the queue runs: the information message is posted first, then the
    closed notice is deleted by a `delete_closed` job of its own, done."""
    league = await _league(tmp_path, closed_notice=True)

    await _open(league)
    await run_queue(league.bot)

    [posted] = league.posts()
    assert (await _jobs(league))["delete_closed"]["done_at"]
    sent = league.events.index(("send", SIGNUP_CHANNEL, posted.id))
    deleted = league.events.index(("delete", SIGNUP_CHANNEL, CLOSED_NOTICE))
    assert sent < deleted
    assert CLOSED_NOTICE not in league.signup.messages


async def test_signup_open_without_the_bot_s_permissions_is_refused_naming_them(tmp_path):
    """The bot may view the signup channel and send messages in it, but not embed links. Manager
    77 runs `/signup open`: he is refused at once, the reply naming View Channel, Send Messages
    and Embed Links; no change is asked, nothing is posted and signups stay closed."""
    league = await _league(tmp_path, perms={"embed_links": False})

    interaction = await _open(league)
    await run_queue(league.bot)

    reply = acknowledgement(interaction)
    assert reply.startswith("❌")
    assert all(name in reply for name in NEEDED)
    assert await _open_change(league) is None
    assert league.posts() == []
    assert not (await league.config())["signups_open"]


async def test_a_refused_information_post_stops_the_queue_with_signups_still_closed(tmp_path):
    """Discord refuses every post in the signup channel. Manager 77 runs `/signup open` and the
    queue runs: it stops at `post_info`, with nothing posted, signups still closed, no button id
    kept, the season still waiting and no audit row written."""
    league = await _league(tmp_path)
    _refuse_posts(league)

    await _open(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "post_info"
    assert league.posts() == []
    config = await league.config()
    assert not config["signups_open"]
    assert config["signup_button_message_id"] is None
    assert await league.stage() == "WAITING"
    assert await league.audits() == []


async def test_a_discarded_information_post_leaves_signups_closed(tmp_path):
    """Discord refuses every post in the signup channel, so `/signup open`'s change stops at
    `post_info`. A league admin discards it: the queue is no longer stopped, signups stay closed
    with no button id kept, and the season still waits for its window."""
    league = await _league(tmp_path)
    _refuse_posts(league)
    await _open(league)
    await run_queue(league.bot)

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.posts() == []
    config = await league.config()
    assert not config["signups_open"]
    assert config["signup_button_message_id"] is None
    assert await league.stage() == "WAITING"


async def test_a_discarded_information_post_leaves_the_closed_notice_and_arms_no_close(tmp_path):
    """The last close's notice still stands. Manager 77 runs `/signup open` with a close time and
    Discord refuses every post in the signup channel, so the change stops at `post_info`; an admin
    discards it. The window was never opened on this change, so its later jobs find themselves no
    longer due: the "signups closed" notice stays standing and no close timer is armed, or it would
    close, at that time, a window opened afterwards with no close time."""
    league = await _league(tmp_path, closed_notice=True)
    _refuse_posts(league)
    await _open(league, close_time="2999-01-01T20:00:00")
    await run_queue(league.bot)

    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    jobs = await _jobs(league)
    assert jobs["arm_close"]["done_at"] and jobs["delete_closed"]["done_at"]
    assert CLOSED_NOTICE in league.signup.messages
    league.bot.scheduler_service.schedule_signup_close_timer.assert_not_called()
    assert not (await league.config())["signups_open"]


#: What a discarded arming of the auto-close says to do: `/signup close-time add` alone is
#: refused while a close time is saved (#439, slice 8, W2).
ARM_AGAIN = (
    "The auto-close was not scheduled: arm it again with `/signup close-time cancel` and then "
    "`/signup close-time add`; the bot also arms it when it next starts."
)


async def test_a_discarded_close_arming_names_the_commands_that_arm_it_again(tmp_path):
    """Manager 77 runs `/signup open` with a close time, and arming the close fails, so the
    change stops at `arm_close` with the information posted and signups open. A league admin
    discards it: the reply says to arm it again with `/signup close-time cancel` and then
    `/signup close-time add`, and that the bot arms it when it next starts, never to set it with
    `/signup close-time add` alone."""
    league = await _league(tmp_path)
    league.bot.scheduler_service.schedule_signup_close_timer.side_effect = RuntimeError(
        "the job store is locked"
    )
    interaction = await _open(league, close_time="2999-01-01T20:00:00")
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "arm_close"

    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await league.config())["signups_open"]
    assert ARM_AGAIN in updated_reply(interaction)
    assert "set it with `/signup close-time add`" not in updated_reply(interaction)


# ---------------------------------------------------------------------------
# Closing the window: `signup.close` and `module.off:signup` (C69–C71)
# ---------------------------------------------------------------------------
#
# Today `/signup close`, its Confirm Close button, the close timer, a restart past the close time
# and `/module disable signup` close the window through `execute_forced_close`, off the queue:
# each step that fails is named and the rest run on. On the queue each asks `signup.close` (or
# `module.off:signup`): `return_drivers` returns each driver still filling in the wizard to Not
# Signed Up, `delete_button` deletes the Sign Up message, `post_closed` posts the closed notice
# and, in its record, saves the window closed, the season's move and the audit in one save; then
# each returned driver's closing notice, lock and deletion a day on, every job on the league lane
# (decision 31). A failed step stops the queue, and a Discard names what was not done (Q26).
#
# The league is `tests.support.season_league`'s `setup_league`: season 7 at its Signups stage,
# signups open in the signup channel (620) with the Sign Up button (6201) standing. Kimi (105),
# and in some tests a second driver (107), are still filling in the wizard (Pending Signup
# Completion), each with their signup channel standing (6300 + their id).

#: The change a close asks, and the one turning the module off asks.
CLOSE_KIND = "signup.close"
MODULE_OFF_KIND = "module.off:signup"
#: The driver's lane change a last answer hands over (C83).
SUBMIT_KIND = "signup.submit"
#: A second driver still filling in the wizard.
OTHER = 107
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
KIMI_LANE = f"channel:{KIMI_CHANNEL}"
#: What the signup channel and a returned driver's channel are told.
CLOSED_TEXT = "🔒 Signups are now closed."
DRIVER_NOTICE = "🔒 Signups have closed."
#: The timer that deletes a held signup channel a day on.
DELETE_TIMER = "signup_channel_delete"


async def _close_league(tmp_path: Any, *drivers: int) -> SeasonLeague:
    """`setup_league` at Signups with the window open; each of *drivers* still filling in the
    wizard (Pending Signup Completion), their signup channel standing and recording what it is
    sent and its permission edits."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    for user_id in drivers:
        await signing_up(league, user_id, state="PENDING_SIGNUP_COMPLETION")
        cid = SIGNUP_CHANNEL_BASE + user_id
        held = league._recording(channel(cid, league.events))
        held.guild = league.guild
        held.set_permissions = AsyncMock()
        league.channels[cid] = held
    return league


def _manager(league: SeasonLeague) -> Any:
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=77))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name="signup close")
    return interaction


async def _signup_close(league: SeasonLeague) -> Any:
    """League manager 77 runs `/signup close`; gives his interaction."""
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = _manager(league)
    await undecorate(SignupCog.signup_close)(cog, interaction)
    return interaction


async def _confirm_close(league: SeasonLeague) -> Any:
    """Manager 77 runs `/signup close`, is shown the confirmation and presses Confirm Close;
    gives the press's interaction."""
    asked = await _signup_close(league)
    view = asked.response.send_message.await_args.kwargs["view"]
    press = _manager(league)
    await view.confirm.callback(press)
    return press


async def _changes_of(league: SeasonLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


async def _close_jobs(league: SeasonLeague, kind: str = CLOSE_KIND) -> list[dict[str, Any]]:
    [change] = await _changes_of(league, kind)
    return await step_rows(league.db_path, change["id"])


async def _window(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows("SELECT * FROM signup_module_config WHERE id = 1")
    return row


async def _season_stage(league: SeasonLeague) -> str:
    [row] = await league.rows("SELECT stage FROM seasons WHERE id = ?", SEASON_ID)
    return str(row["stage"])


async def _driver_state(league: SeasonLeague, user_id: int = SIGNING_UP) -> str:
    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(user_id)
    )
    return str(row["current_state"])


async def _close_audits(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT * FROM audit_entries WHERE change_type LIKE 'SIGNUP_%CLOSE'"
    )


def _closed_notices(league: SeasonLeague) -> list[Any]:
    signup = league.channel(LEAGUE_SIGNUP_CHANNEL)
    return [message for message in signup.messages.values() if message.content == CLOSED_TEXT]


def _told(league: SeasonLeague, cid: int = KIMI_CHANNEL) -> list[str]:
    return [text for text in league.channel(cid).texts if text.startswith(DRIVER_NOTICE)]


def _locked_in(league: SeasonLeague, cid: int = KIMI_CHANNEL) -> bool:
    return any(call.kwargs.get("send_messages") is False
               for call in league.channel(cid).set_permissions.call_args_list)


def _deletion_armed(league: SeasonLeague, user_id: int = SIGNING_UP) -> bool:
    return any(call.args[:2] == (DELETE_TIMER, str(user_id))
               for call in league.bot.scheduler_service.arm_once.call_args_list)


def _lane_of(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


async def test_signup_close_returns_drivers_deletes_the_button_posts_the_notice_and_tells_each_as_jobs(
    tmp_path,
):
    """Kimi is still filling in the wizard. Manager 77 runs `/signup close` and presses Confirm
    Close. He is answered at once with an under-way line, and nothing has changed yet: Kimi is
    still signing up and the Sign Up button stands. When the queue runs, the close's jobs are
    done in order — `return_drivers`, `delete_button`, `post_closed`, then Kimi's
    `signup_notice`, `close_signup` and `arm_deletion` — Kimi is Not Signed Up, the button is
    deleted, the closed notice stands in the signup channel, Kimi's channel is told signups have
    closed and locked, its deletion is armed, and the acknowledgement is updated with ✅."""
    league = await _close_league(tmp_path, SIGNING_UP)

    press = await _confirm_close(league)

    assert acknowledgement(press).startswith("⏳")
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert SIGNUP_BUTTON in league.channel(LEAGUE_SIGNUP_CHANNEL).messages

    await run_queue(league.bot)

    names = [job["name"] for job in await _close_jobs(league)]
    order = ["return_drivers", "delete_button", "post_closed", "signup_notice",
             "close_signup", "arm_deletion"]
    assert [name for name in names if name in order] == order
    assert all(job["done_at"] for job in await _close_jobs(league))
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert SIGNUP_BUTTON not in league.channel(LEAGUE_SIGNUP_CHANNEL).messages
    assert len(_closed_notices(league)) == 1
    assert len(_told(league)) == 1
    assert _locked_in(league)
    assert _deletion_armed(league)
    assert updated_reply(press).startswith("✅")
    assert await stopped_job(league.db_path) is None


async def test_a_close_s_window_and_season_move_are_saved_together(tmp_path):
    """Nobody is signing up. Manager 77 runs `/signup close`; until the queue runs the window
    stays open, the season at Signups and no close audited. When it runs, `post_closed`'s record
    saves, with its mark, the window closed with the notice's id, the season moved to Placements
    and the SIGNUP_CLOSE audit. In a second league whose signup channel refuses the notice, the
    queue stops at `post_closed` with the window still open and the season still at Signups:
    neither is saved without the other."""
    league = await _close_league(tmp_path / "posted")

    await _signup_close(league)

    assert (await _window(league))["signups_open"]
    assert await _season_stage(league) == "SIGNUPS"
    assert await _close_audits(league) == []

    await run_queue(league.bot)

    post_closed = [job for job in await _close_jobs(league) if job["name"] == "post_closed"]
    assert post_closed and post_closed[0]["done_at"]
    [notice] = _closed_notices(league)
    window = await _window(league)
    assert not window["signups_open"]
    assert window["signup_closed_message_id"] == notice.id
    assert await _season_stage(league) == "PLACEMENTS"
    assert [row["change_type"] for row in await _close_audits(league)] == ["SIGNUP_CLOSE"]

    refused = await _close_league(tmp_path / "refused")
    refused.channel(LEAGUE_SIGNUP_CHANNEL).fail_when = (
        lambda content, _kwargs: content == CLOSED_TEXT
    )

    await _signup_close(refused)
    await run_queue(refused.bot)

    job = await stopped_job(refused.db_path)
    assert job is not None and job["name"] == "post_closed"
    assert (await _window(refused))["signups_open"]
    assert await _season_stage(refused) == "SIGNUPS"
    assert await _close_audits(refused) == []


async def test_a_failed_close_step_stops_the_queue_and_a_discard_names_it(tmp_path):
    """Kimi is still filling in the wizard, and Discord refuses every post in his signup channel.
    Manager 77 confirms the close and the queue runs: Kimi is returned to Not Signed Up, the
    button is deleted and the closed notice posted, then the queue stops at Kimi's
    `signup_notice`, his channel neither told nor locked. A league admin discards it: the queue
    is no longer stopped, and the close's outcome or the log channel names Kimi as not told
    that signups had closed."""
    league = await _close_league(tmp_path, SIGNING_UP)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    press = await _confirm_close(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "signup_notice"
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert len(_closed_notices(league)) == 1
    assert _told(league) == []
    assert not _locked_in(league)

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    said = updated_reply(press) + "\n" + league.log()
    assert f"<@{SIGNING_UP}>" in said


#: What a discarded lock of a returned driver's signup channel says: its deletion a day on is
#: still armed (#439, slice 8, W3; owner Q-D2).
NOT_LOCKED = (
    f"<@{SIGNING_UP}>'s signup channel could not be locked: they can still type in it until it "
    "deletes itself a day on, or delete it by hand sooner."
)


@pytest.mark.xfail(strict=True, reason="#439: a discarded lock still says the channel will not "
                   "delete itself, though its deletion is armed")
async def test_a_discarded_lock_says_the_channel_stays_open_to_typing_until_it_deletes_itself(
    tmp_path,
):
    """Kimi is still filling in the wizard, and Discord refuses to change the permissions of his
    signup channel. Manager 77 confirms the close and the queue stops at Kimi's `close_signup`.
    A league admin discards it: the channel's deletion a day on is armed all the same, and the
    reply and the log channel say that Kimi can still type in it until it deletes itself a day
    on, or that it may be deleted by hand sooner, never that it will not delete itself."""
    league = await _close_league(tmp_path, SIGNING_UP)
    league.channel(KIMI_CHANNEL).set_permissions = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    press = await _confirm_close(league)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "close_signup"

    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _deletion_armed(league)
    said = updated_reply(press) + "\n" + league.log()
    assert NOT_LOCKED in said
    assert "will not delete itself" not in said


async def test_a_close_that_could_not_record_the_window_closed_names_the_close_time_command_first(
    tmp_path,
):
    """The window has a close time armed, and the timer's close runs: Discord refuses "Signups are
    now closed", so the queue stops at `post_closed`, and a league admin discards it. The window
    is not recorded closed and its close time stays armed, so `/signup close` would refuse: the
    line says to run `/signup close-time cancel` first, and so does the not-closed text a season's
    end writes. Since `/signup close` refuses too while a season's end or wind-down is in hand,
    the line says to let that finish first."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended
    from leaguebot.core.services.season_lifecycle_service import WINDOW_NOT_CLOSED

    league = await _close_league(tmp_path)
    await league.write("UPDATE signup_module_config SET close_at = ?", "2026-10-13T11:00:00+00:00")
    league.channel(LEAGUE_SIGNUP_CHANNEL).fail_when = lambda content, _kwargs: CLOSED_TEXT in str(content)

    await close_signups_unattended(league.bot, cause="timer")
    await run_queue(league.bot)
    await discard_job(league.bot)

    assert (await _window(league))["signups_open"]
    [line] = [each for each in league.log().split("\n⌛") if "Signups auto-close" in each] or [league.log()]
    assert "`/signup close`" in line and "`/signup close-time cancel`" in line
    assert line.index("`/signup close-time cancel`") > line.index("`/signup close`")
    assert "`/signup close-time cancel`" in WINDOW_NOT_CLOSED
    assert "season's end or wind-down is still on the queue, let it finish first" in line, line


async def test_a_confirm_after_the_window_changed_is_refused_when_it_runs(tmp_path):
    """Kimi is still filling in the wizard. Manager 77 runs `/signup close` and presses Confirm
    Close; before the queue reaches it, signups are reopened on another Sign Up message (7777).
    When the queue runs, the close is refused: his acknowledgement is updated saying signups
    were reopened, nothing is closed — the window stays open, Kimi is still signing up, his
    channel is not told — and the queue is not stopped."""
    league = await _close_league(tmp_path, SIGNING_UP)

    press = await _confirm_close(league)
    await league.write("UPDATE signup_module_config SET signup_button_message_id = 7777")
    await run_queue(league.bot)

    assert "reopened" in updated_reply(press)
    assert (await _window(league))["signups_open"]
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"
    assert _told(league) == []
    assert _closed_notices(league) == []
    assert await stopped_job(league.db_path) is None


async def test_the_close_timer_and_a_restart_past_the_close_time_ask_the_close(tmp_path):
    """Two leagues, each with Kimi still filling in the wizard: in one the close timer fires, in
    the other the bot starts after the close time (`close_signups_unattended` with the cause
    "timer", then "restart"). Each asks `signup.close` as the bot's own change and posts nothing
    itself: the window stays open until the queue runs. When it runs, the window is closed, the
    closed notice posted and Kimi returned to Not Signed Up. The close is audited once, by the
    system, as an automatic close, and one "🔒 Signups closed automatically at their set time"
    line is written, its head showing the set time (and, for the restart, that it came at
    start-up); it names no member and states beneath it the one driver returned (signup :67)."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended
    from tests.support.change_queue import queued_log_lines

    for cause in ("timer", "restart"):
        league = await _close_league(tmp_path / cause, SIGNING_UP)
        set_at = league.clock.now - timedelta(minutes=1)
        await league.write("UPDATE signup_module_config SET close_at = ?", set_at.isoformat())

        await close_signups_unattended(league.bot, cause=cause)

        [change] = await _changes_of(league, CLOSE_KIND)
        assert change["origin"] == "BOT", cause
        assert (await _window(league))["signups_open"], cause
        assert _closed_notices(league) == [], cause

        await run_queue(league.bot)

        assert not (await _window(league))["signups_open"], cause
        assert len(_closed_notices(league)) == 1, cause
        assert await _driver_state(league) == "NOT_SIGNED_UP", cause
        audits = await league.rows(
            "SELECT change_type, old_value, new_value, actor_name FROM audit_entries "
            "WHERE change_type LIKE 'SIGNUP_%CLOSE'"
        )
        assert [(row["change_type"], row["old_value"], row["new_value"], row["actor_name"])
                for row in audits] == [("SIGNUP_AUTO_CLOSE", "open", "closed", "system")], cause
        lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
        [line] = [line for line in lines
                  if "🔒 Signups closed automatically at their set time" in line]
        head, *beneath = line.splitlines()
        assert (set_at.isoformat() in head
                or f"<t:{int(set_at.timestamp())}" in head), (cause, head)
        assert ("start-up" in head) == (cause == "restart"), (cause, head)
        assert "<@" not in line, (cause, line)
        assert any("returned" in text and "1" in text for text in beneath), (cause, line)


#: The changes that end a season or wind it down to Pending completion, each of which closes the
#: window itself.
WIND_DOWN_KIND = "season.wind_down"
SEASON_ENDS = ("season.complete", WIND_DOWN_KIND)


async def _wind_down_due(league: SeasonLeague) -> None:
    """Season 7 as a wind-down finds it when it runs: signups open while it is raced (Ongoing,
    signups open), and both its divisions finished."""
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING_SIGNUPS' WHERE id = ?", SEASON_ID)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE season_id = ?", SEASON_ID)


async def _season_end_in_hand(league: SeasonLeague, kind: str, *, due: bool = True) -> int:
    """A *kind* change of season 7 in hand on the league's lane, asked after the close and stopped
    at its first job, its next try an hour away: it stays in hand while the close runs. A
    wind-down is one that would run, the season first put as it finds it (`_wind_down_due`),
    unless *due* is False. Gives the job's number."""
    import json

    if kind == WIND_DOWN_KIND and due:
        await _wind_down_due(league)
    later = league.clock.now + timedelta(hours=1)
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'BOT', 'QUEUED', 'ending the season')",
            (kind, kind, json.dumps({"season_id": SEASON_ID})),
        )
        cursor = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, tries, "
            "failing_since, last_failure, next_try_at) VALUES (?, 0, 'first', '{}', 1, ?, ?, ?)",
            (cursor.lastrowid, league.clock.now.isoformat(), "a fault", later.isoformat()),
        )
        job = cursor.lastrowid
        await db.commit()
    assert job is not None
    return job


async def _auto_close(league: SeasonLeague, cause: str) -> None:
    """The window's close time has passed and the bot asks its close, by the timer or at a
    restart (*cause*)."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended

    set_at = league.clock.now - timedelta(minutes=1)
    await league.write("UPDATE signup_module_config SET close_at = ?", set_at.isoformat())
    await close_signups_unattended(league.bot, cause=cause)


@pytest.mark.parametrize("kind", SEASON_ENDS)
async def test_the_bot_s_close_leaves_a_season_whose_end_is_in_hand_to_it(tmp_path, kind):
    """Two leagues, each with Kimi still filling in the wizard: in one the close timer fires, in
    the other the bot starts after the close time, and each asks the close. Before it runs, the
    season's completion (or its wind-down to Pending completion, the season raced with both its
    divisions finished) comes to be in hand on the queue, behind it and stopped. When the close
    comes up to run it leaves the season to that end (signup :56): it is dropped, the season stays
    at its stage, Kimi is not returned and not told,
    nothing is posted in the signup channel and no line of the close is written."""
    from tests.support.change_queue import queued_log_lines

    for cause in ("timer", "restart"):
        league = await _close_league(tmp_path / cause, SIGNING_UP)
        await _auto_close(league, cause)
        await _season_end_in_hand(league, kind)
        stage = await _season_stage(league)

        await run_queue(league.bot)

        [change] = await _changes_of(league, CLOSE_KIND)
        assert change["state"] == "DROPPED", cause
        assert await _season_stage(league) == stage, cause
        assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION", cause
        assert _told(league) == [], cause
        assert _closed_notices(league) == [], cause
        lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
        assert not [line for line in lines if "Signups closed automatically" in line], cause


@pytest.mark.parametrize(("kind", "due", "moved"), (
    ("season.complete", True, None), (WIND_DOWN_KIND, True, None),
    (WIND_DOWN_KIND, False, "PLACEMENTS"),
))
async def test_a_close_cut_off_after_returning_its_drivers_tells_them_and_leaves_the_season_to_its_end(
    tmp_path, kind, due, moved,
):
    """The close timer fires with Kimi still filling in the wizard, and the bot stops once the
    close has returned him to Not Signed Up, before it has told him. While it is down, the
    season's completion (or its wind-down, the season raced with both its divisions finished)
    comes to be in hand on the queue, stopped. When the bot starts again the close carries on:
    Kimi is told once and his channel locked, but the season is left to its end and stays at its
    stage (signup :56). A wind-down in hand while the season is still at Signups is one its check
    will drop, which closes nothing: it holds nothing back, and the close moves the season on to
    Placements."""
    league = await _close_league(tmp_path, SIGNING_UP)
    await _auto_close(league, "timer")
    for _ in range(10):
        if [job for job in await _close_jobs(league)
                if job["name"] == "return_drivers" and job["done_at"]]:
            break
        await run_queue(league.bot, steps=1)
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert _told(league) == []

    await _season_end_in_hand(league, kind, due=due)
    stage = await _season_stage(league)
    await league.restart()
    await run_queue(league.bot)

    assert len(_told(league)) == 1
    assert _locked_in(league)
    assert await _season_stage(league) == (moved or stage)


@pytest.mark.parametrize("kind", ("season.complete", "season.cancel", "season.abort",
                                  "season.wind_down"))
@pytest.mark.parametrize("asked", ("the command", "the command with drivers", "Confirm Close"))
async def test_a_member_s_close_while_the_season_s_end_is_in_hand_is_refused_naming_the_job(
    tmp_path, kind, asked,
):
    """The season's completion (cancellation, abort or wind-down) is in hand on the queue, stopped
    at its first job. Manager 77 runs `/signup close` with nobody signing up, or with Kimi still
    filling in the wizard, or is shown the confirmation before the end comes to be in hand and
    then presses Confirm Close. Each is refused at once (core, "Ending a season"): he is told the
    season's end is in hand and closes signups itself, naming its job and saying to press Retry
    or Discard on its notice; the refusal is one ⛔ line in the log channel; no close is asked,
    the window stays open and the season at its stage. A wind-down is one that would run, the
    season raced with both its divisions finished."""
    from tests.support.change_queue import queued_log_lines

    drivers = (SIGNING_UP,) if asked != "the command" else ()
    league = await _close_league(tmp_path, *drivers)
    if asked == "Confirm Close":
        shown = await _signup_close(league)
        job = await _season_end_in_hand(league, kind)
        view = shown.response.send_message.await_args.kwargs["view"]
        press = _manager(league)
        await view.confirm.callback(press)
    else:
        job = await _season_end_in_hand(league, kind)
        press = await _signup_close(league)

    reply = acknowledgement(press)
    assert "season's end is in hand" in reply and "closes signups itself" in reply, reply
    assert f"job #{job}" in reply and "Retry or Discard" in reply, reply
    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    assert len([line for line in lines if line.startswith("⛔")]) == 1, lines
    assert await _changes_of(league, CLOSE_KIND) == []
    stage = await _season_stage(league)
    await run_queue(league.bot)
    assert (await _window(league))["signups_open"]
    assert await _season_stage(league) == stage


async def test_a_member_s_close_of_a_closed_window_is_told_it_is_not_open(tmp_path):
    """Signups are closed and the season's completion is in hand on the queue. Manager 77 runs
    `/signup close`: he is told signups are not open, not that the season's end will close them."""
    league = await _close_league(tmp_path)
    await league.write("UPDATE signup_module_config SET signups_open = 0")
    await _season_end_in_hand(league, "season.complete")

    reply = acknowledgement(await _signup_close(league))

    assert "Signups are not currently open" in reply, reply
    assert "season's end is in hand" not in reply, reply


async def test_a_member_s_close_names_the_job_of_an_end_still_doing_one(tmp_path):
    """The season's cancellation is in hand with every job done and only its own close left, and
    its completion is in hand too, stopped at its first job. Manager 77 runs `/signup close`: the
    refusal names the completion's job, the one end with a job still to do."""
    import json

    league = await _close_league(tmp_path)
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('season.cancel', 'season.cancel', ?, 'BOT', 'RUNNING', 'cancelling')",
            (json.dumps({"season_id": SEASON_ID}),),
        )
        await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, done_at) "
            "VALUES (?, 0, 'first', '{}', ?)",
            (cursor.lastrowid, league.clock.now.isoformat()),
        )
        await db.commit()
    job = await _season_end_in_hand(league, "season.complete")

    reply = acknowledgement(await _signup_close(league))

    assert f"job #{job}" in reply, reply


@pytest.mark.parametrize("asked", ("timer", "command"))
async def test_a_wind_down_its_check_will_drop_holds_no_close_back(tmp_path, asked):
    """Season 7 is raced with signups open (Ongoing, signups open) and two divisions, the first
    finished and the second not. The first division's appeals are approved, and the bot asks a
    wind-down in case it was the season's last (`appeals_approval_change`); its check will drop
    it, as the second division is not done. Behind it, the window's close time passes and the
    bot asks the close, or manager 77 runs `/signup close` with no close time set and nobody
    signing up. Nothing ending the season is in hand that would close the window: the member's
    close is not refused, the close goes ahead, the window is closed with its notice posted and
    the season moves on to Ongoing, and the wind-down is dropped."""
    from tests.support.change_queue import queued_log_lines

    async def wind_down_asked() -> None:
        # As the approval's save queues it, a follow-on of the bot's, unchecked until its turn.
        async with get_connection(league.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO queued_changes (kind, dedup_key, payload, origin, what) "
                "VALUES (?, ?, '{}', 'BOT', ?)",
                (WIND_DOWN_KIND, WIND_DOWN_KIND,
                 "Winding the season down after approving round 3's appeals"),
            )
            for position, name in enumerate(("wind_down", "turn_down")):
                await db.execute(
                    "INSERT INTO queued_change_steps (change_id, position, name, payload) "
                    "VALUES (?, ?, ?, '{}')",
                    (cursor.lastrowid, position, name),
                )
            await db.commit()

    league = await _close_league(tmp_path)
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING_SIGNUPS' WHERE id = ?", SEASON_ID)
    [first, *_others] = await league.rows(
        "SELECT id FROM divisions WHERE season_id = ? ORDER BY id", SEASON_ID
    )
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", first["id"])

    if asked == "timer":
        await _auto_close(league, "timer")
        await wind_down_asked()
    else:
        await wind_down_asked()
        press = await _signup_close(league)
        reply = acknowledgement(press)
        assert "season's end is in hand" not in reply, reply
    await run_queue(league.bot)

    [close] = await _changes_of(league, CLOSE_KIND)
    assert close["state"] == "DONE", close
    [ended] = await _changes_of(league, WIND_DOWN_KIND)
    assert ended["state"] == "DROPPED", ended
    assert not (await _window(league))["signups_open"]
    assert len(_closed_notices(league)) == 1
    assert await _season_stage(league) == "ONGOING"
    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    assert not [line for line in lines if line.startswith("⛔")], lines


async def test_disabling_signup_closes_the_window_and_switches_the_module_off_as_jobs(tmp_path):
    """Kimi is still filling in the wizard. Manager 77 runs `/module disable signup`. Until the
    queue runs, the module is still on and the window open. When it runs, the
    `module.off:signup` change closes the window as its jobs — Kimi returned, the button
    deleted, the closed notice posted, Kimi told — and its last job, `switch_off`, turns the
    module off and forgets the signup channel, after which the channel's overwrites are cleared
    and the one line is written."""
    from leaguebot.core.cogs.module_cog import ModuleCog

    league = await _close_league(tmp_path, SIGNING_UP)
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = league.bot
    interaction = _manager(league)
    interaction.command = MagicMock(qualified_name="module disable")

    await cog._disable_signup(interaction)

    assert await league.bot.module_service.is_signup_enabled()
    assert (await _window(league))["signups_open"]
    assert len(await _changes_of(league, MODULE_OFF_KIND)) == 1

    await run_queue(league.bot)

    jobs = await _close_jobs(league, MODULE_OFF_KIND)
    assert all(job["done_at"] for job in jobs)
    names = [job["name"] for job in jobs]
    assert names[-2:] == ["clear_overwrites", "off_line"]
    assert names.index("post_closed") < names.index("switch_off") < names.index("clear_overwrites")
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert SIGNUP_BUTTON not in league.channel(LEAGUE_SIGNUP_CHANNEL).messages
    assert len(_closed_notices(league)) == 1
    assert len(_told(league)) == 1
    assert not await league.bot.module_service.is_signup_enabled()
    assert await league.rows("SELECT * FROM signup_module_config") == []


async def test_a_close_posts_the_drivers_notices_on_the_league_lane(tmp_path):
    """Kimi and a second driver are still filling in the wizard; Discord refuses posts in Kimi's
    signup channel alone. Manager 77 confirms the close and the queue runs: the close is one
    change on the league lane, no change is asked on either driver's lane, and it stops the
    league lane at Kimi's `signup_notice` — Kimi's own lane is not stopped. Which driver the
    close tells first is not pinned: nothing behind the stopped job runs."""
    league = await _close_league(tmp_path, SIGNING_UP, OTHER)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _confirm_close(league)
    await run_queue(league.bot)

    [close] = await _changes_of(league, CLOSE_KIND)
    assert _lane_of(close) == "league"
    assert all(_lane_of(row) == "league" for row in await change_rows(league.db_path))
    job = await stopped_job(league.db_path, "league")
    assert job is not None and job["name"] == "signup_notice"
    assert await stopped_job(league.db_path, KIMI_LANE) is None


async def test_the_close_wins_over_a_last_answer_handed_over_behind_it(tmp_path):
    """Kimi is still filling in the wizard. Manager 77 confirms the close; then Kimi's last
    answer hands his signup to the league (`signup.submit`) behind it. When the queue runs, the
    close is done and Kimi returned to Not Signed Up; his submission finds him returned and is
    dropped as no longer due, saving no signup record. Kimi learns of the close from the close's
    own notice in his channel, the only one he is sent."""
    from leaguebot.core.models.change import ChangeOrigin

    league = await _close_league(tmp_path, SIGNING_UP)
    # On his last question, a state of the wizard's own (the helper gives it the driver's).
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NOTES' "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )

    await _confirm_close(league)
    await league.bot.change_queue.ask(
        SUBMIT_KIND,
        {"user_id": str(SIGNING_UP), "channel_id": KIMI_CHANNEL},
        what=f"submitting the signup of <@{SIGNING_UP}>",
        origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)

    [close] = await _changes_of(league, CLOSE_KIND)
    [submit] = await _changes_of(league, SUBMIT_KIND)
    assert close["id"] < submit["id"]
    assert close["state"] == "DONE"
    assert submit["state"] == "DROPPED"
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    assert await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    ) == []
    assert len(_told(league)) == 1
