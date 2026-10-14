"""`/signup open` on the change queue: the window opens only once its message is up (#439, slice
6b, C68).

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
from datetime import datetime, timezone
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

NO_OPEN_CHANGE = (
    "#439: /signup open posts its information message and records the window open itself, off "
    "the queue, with no signup.open change whose post_info job opens the window in its record"
)


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


async def _open(league: _League) -> Any:
    """League manager 77 runs `/signup open` with no tracks and no close time."""
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=77))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name="signup open")
    await undecorate(SignupCog.signup_open)(cog, interaction, None, None)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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


@pytest.mark.xfail(strict=True, reason=NO_OPEN_CHANGE)
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
