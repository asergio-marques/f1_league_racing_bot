"""`/bot hub-channel`: pointing the hub at a channel, and moving it (issue #279).

**A channel does one job.** The hub is held to the rule every channel command is: a channel
already set as anything else is refused, naming what holds it, and nothing is written.

**Setting it is the whole of standing it up.** The permissions are set and the panel posted in
the same change, so a league never has a hub channel without a panel, or a panel members
cannot see.

**Moving it stands the old one down.** The old panel is deleted and the permissions the bot set
there cleared, as moving the signup channel does; otherwise the old channel keeps a live panel
and a read-only lock nobody asked it to keep.

**What fails after the write is reported, not undone.** The channel chosen is still the right
one; a permission Discord refuses is repaired in Discord.

**It is a core `Setting` on the change queue** (#283), as the league's two roles beside it are
(`test_league_role_commands.py`): the command answers with the queue's acknowledgement, and the
queue writes the hub channel, its panel's id cleared and its audit entry in one save, stamped
with the queue's clock. The old hub stood down, the new hub's permissions and the panel's refresh
follow the save as the setting's own steps, and what they report is carried in the setting's one
line. The tests run the bot the entry point builds, with the real queue and "now" pinned, and run
the queue.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import hub_service
from tests.support.change_queue import (
    PAST_THE_STOP,
    Clock,
    acknowledgement,
    change_rows,
    http_error,
    league_bot,
    league_queue,
    member,
    retry_job,
    run_queue,
    step_rows,
    stop_the_league_lane,
    stopped_job,
)
from tests.support.settings_league import interaction as member_asks
from tests.support.settings_league import last_said, unruled
from tests.support.undecorate import undecorate

SERVER_ID = 27902
LOG_CHANNEL = 101
HUB, OLD_HUB = 7920, 7921
OLD_PANEL = 7930
NEW_PANEL = 7931
#: A base role the league set, since deleted from the server.
GONE_ROLE = 4444
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The manager running the command, as a log line names them, the mention wrapped so it
#: notifies nobody.
NAMED = "Manager (`<@77>`)"

#: What the manager is told once the hub is set, before any fault after the save is added.
SET = f"✅ **Hub channel** set to <#{HUB}>. Its panel follows on the change queue."


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    """No option is offered on the panel. Core's About registers itself when first imported,
    which the bot's build does, so it is imported before the registry is emptied: otherwise the
    first test of a session to build the bot would find it registered."""
    import leaguebot.core.services.about_service  # noqa: F401

    monkeypatch.setattr(hub_service, "_OPTIONS", {})


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "hub_command.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id) "
            "VALUES (?, 900, 100, ?, 902)",
            (SERVER_ID, LOG_CHANNEL),
        )
        await db.commit()
    return path


async def _configure(db_path, **columns):
    async with get_connection(db_path) as db:
        for column, value in columns.items():
            await db.execute(f"UPDATE server_configs SET {column} = ?", (value,))
        await db.commit()


async def _stored(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT hub_channel_id, hub_message_id FROM server_configs")
        return dict(await cursor.fetchone())


async def _audit(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, change_type, old_value, new_value, timestamp FROM audit_entries"
        )
        return [dict(r) for r in await cursor.fetchall()]


def _channel(channel_id, *, manage=True, send_raises=None, edit_raises=None):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(
        return_value=MagicMock(manage_channels=manage, manage_roles=manage)
    )
    channel.edit = AsyncMock(side_effect=edit_raises)
    channel.send = AsyncMock(side_effect=send_raises, return_value=MagicMock(id=NEW_PANEL))
    partial = MagicMock()
    partial.edit = AsyncMock()
    partial.delete = AsyncMock()
    # A message's link is text: a refused deletion keeps it on the stopped job, as JSON.
    partial.jump_url = f"https://discord.test/channels/{SERVER_ID}/{channel_id}/message"
    channel.get_partial_message = MagicMock(return_value=partial)
    channel._partial = partial
    return channel


async def _setup(db_path, *channels):
    """The command's cog on the league's bot (`league_bot`), its configuration read from the
    database, carrying the league's change queue, which posts and deletes the hub's panels when
    it is run; the guild and the bot find *channels* and the log channel, and every role but
    `GONE_ROLE`. The interaction is the manager's (77), recording every word they are told."""
    bot = await league_bot(db_path, real_config=True)
    by_id = {c.id: c for c in channels}
    by_id[LOG_CHANNEL] = bot.log_channel
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    guild.get_role = MagicMock(
        side_effect=lambda rid: None if rid == GONE_ROLE else MagicMock(spec=discord.Role)
    )
    guild.default_role = MagicMock(spec=discord.Role)
    guild.me = MagicMock()

    bot.get_guild = MagicMock(return_value=guild)
    bot.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: by_id[cid])
    league_queue(bot, db_path, now=Clock(NOW))
    cog = BotCog(bot)

    asked = member_asks(bot, "bot hub-channel", user=member(77, "Manager", "Manager#0001"))
    asked.guild = guild
    asked.guild_id = SERVER_ID
    return cog, asked


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


def _replied(interaction) -> str:
    """What the manager's reply reads now."""
    return last_said(interaction)


async def _run(cog, interaction, channel):
    """The manager runs `/bot hub-channel` for *channel*, and the queue is run."""
    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)
    await run_queue(cog.bot)


def _with_faults(*faults: str) -> str:
    """The setting's line where a step after its save reported *faults*."""
    return (
        f"{NAMED} | /bot hub-channel | Success, with faults\n  channel: <#{HUB}>"
        + "".join(f"\n  {fault}" for fault in faults)
    )


# ── Setting the hub ───────────────────────────────────────────────────────


async def test_the_hub_is_set_permissioned_and_given_its_panel(db_path):
    channel = _channel(HUB)
    cog, interaction = await _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert "overwrites" in channel.edit.await_args.kwargs
    assert "Nothing is offered here yet." in channel.send.await_args.args[0]


async def test_the_reply_says_the_hub_was_set_and_its_panel_follows_on_the_queue(db_path):
    """Core spec :135: a change asked of the queue is acknowledged at once, and its reply says
    what it did once it has run, the post following on the queue. The manager points the hub at
    7920: before the queue runs, they have the queue's acknowledgement, and nothing is written,
    no permission set and no panel posted; once the queue runs, the reply names the new hub
    channel and says its panel follows on the queue, and the panel is posted."""
    channel = _channel(HUB)
    cog, interaction = await _setup(db_path, channel)

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)

    assert acknowledgement(interaction).startswith(
        f"⏳ Setting **Hub channel** to <#{HUB}>. This message will be updated when it is "
        f"done; if it takes longer, the log channel will say so."
    )
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}
    channel.edit.assert_not_awaited()
    channel.send.assert_not_awaited()

    await run_queue(cog.bot)

    assert _replied(interaction) == SET
    channel.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_the_change_is_audited_and_logged(db_path):
    """The hub moves from 7921 to 7920. One audit entry names the old hub and the new, the
    manager who ran it, and the queue's clock, never the host's; the log channel takes today's
    line, word for word, and nothing else."""
    await _configure(db_path, hub_channel_id=OLD_HUB)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    (row,) = await _audit(db_path)
    assert row["change_type"] == "HUB_CHANNEL_SET"
    assert json.loads(row["old_value"]) == {"channel_id": OLD_HUB}
    assert json.loads(row["new_value"]) == {"channel_id": HUB}
    assert row["actor_id"] == 77
    assert row["timestamp"] == NOW.isoformat()
    assert _lines(cog) == [f"{NAMED} | /bot hub-channel | Success\n  channel: <#{HUB}>"]


async def test_the_hub_and_its_audit_entry_are_one_save_before_any_step_after_it(db_path):
    """The hub is 7921, its panel 7930 standing; the manager points it at 7920, and the queue
    runs the change's first step alone. That one save has written the hub 7920, cleared the old
    panel's id, and written the audit entry, stamped with the queue's clock; nothing has yet
    touched Discord: neither hub's permissions have been edited, no panel posted, and no refresh
    asked of the queue."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = await _setup(db_path, old, new)

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, new)
    await run_queue(cog.bot, steps=1)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": None}
    (row,) = await _audit(db_path)
    assert json.loads(row["new_value"]) == {"channel_id": HUB}
    assert row["timestamp"] == NOW.isoformat()
    old.edit.assert_not_awaited()
    new.edit.assert_not_awaited()
    new.send.assert_not_awaited()
    assert [row["kind"] for row in await change_rows(db_path)] == ["core.setting.hub_channel_id"]


async def test_a_fault_in_the_save_saves_neither_the_hub_nor_its_audit_entry(
    db_path, monkeypatch
):
    """The hub is 7921, its panel 7930 standing; the manager points it at 7920, and the audit
    entry's write fails on the database as the queue runs the change. Neither the hub nor its
    panel's id is written, and no audit entry stands; neither hub is touched and no refresh is
    asked. The manager is told in `setting_change`'s words that nothing was saved and to run it
    again; the log channel takes the one failure line and no success line; the queue is not
    stopped."""
    import sqlite3

    from leaguebot.core.services import change_queue

    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert await _stored(db_path) == {"hub_channel_id": OLD_HUB, "hub_message_id": OLD_PANEL}
    assert await _audit(db_path) == []
    old.edit.assert_not_awaited()
    new.edit.assert_not_awaited()
    new.send.assert_not_awaited()
    assert [row["kind"] for row in await change_rows(db_path)] == ["core.setting.hub_channel_id"]
    assert _replied(interaction) == (
        "❌ `/bot hub-channel` stopped on a fault in the bot, not on anything you entered, and "
        "did not finish. Nothing was saved. Run `/bot hub-channel` again to retry. The fault is "
        "recorded in the log channel."
    )
    assert _lines(cog) == [
        f"❌ `/bot hub-channel` failed for {NAMED} — OperationalError. The details are in the "
        f"host's log."
    ]
    assert await stopped_job(db_path) is None


async def test_the_stand_down_the_permissions_and_the_refresh_follow_the_save_as_its_steps(
    db_path,
):
    """The hub is 7921, its panel 7930 standing; the manager points it at 7920. While the change
    waits on the queue, nothing is touched and it is the only change asked. Once the queue has
    run it: the old hub's overwrites are cleared, then the new hub's permissions set, and then a
    refresh of the panel is asked of the queue as the bot's own change, after the setting's, in
    today's words ("posting the hub panel", which the log channel's lines about it name) and on
    behalf of the manager who ran the command, naming the command and the old panel 7930 in 7921
    for it to take down once the new is up."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    order: list[str] = []
    old, new = _channel(OLD_HUB), _channel(HUB)
    old.edit.side_effect = lambda **kwargs: order.append("clear old")
    new.edit.side_effect = lambda **kwargs: order.append("set new")
    cog, interaction = await _setup(db_path, old, new)

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, new)
    assert order == []
    assert [row["kind"] for row in await change_rows(db_path)] == ["core.setting.hub_channel_id"]
    await run_queue(cog.bot)

    assert order == ["clear old", "set new"]
    setting, refresh = await change_rows(db_path)
    assert setting["kind"] == "core.setting.hub_channel_id"
    assert (refresh["kind"], refresh["origin"]) == (hub_service.HUB_REFRESH, "BOT")
    assert (refresh["what"], refresh["actor_id"]) == ("posting the hub panel", 77)
    payload = json.loads(refresh["payload"])
    assert "/bot hub-channel" in payload["command"]
    assert (payload["old_channel_id"], payload["old_message_id"]) == (OLD_HUB, OLD_PANEL)


#: Discord refusing an edit of a hub's permissions.
_REFUSED = http_error(discord.Forbidden, status=403, text="Missing Permissions")
_OLD_NOT_CLEARED = (
    f"The permissions on the old hub <#{OLD_HUB}> could not be cleared: {_REFUSED}"
)
_NEW_NOT_SET = f"The hub <#{HUB}>'s permissions could not be set: {_REFUSED}"
_BASE_ROLE_GONE = (
    f"The base role (id {GONE_ROLE}) is not in the server, so the hub <#{HUB}> is closed to "
    f"members until `/bot base-role` sets one."
)


@pytest.mark.parametrize(
    "old_refuses,new_refuses,base_role_gone,faults",
    [
        pytest.param(True, False, False, (_OLD_NOT_CLEARED,), id="old-hub-refused"),
        pytest.param(False, True, False, (_NEW_NOT_SET,), id="new-hub-refused"),
        pytest.param(True, True, False, (_OLD_NOT_CLEARED, _NEW_NOT_SET), id="both-refused"),
        pytest.param(False, False, True, (_BASE_ROLE_GONE,), id="base-role-gone"),
    ],
)
async def test_a_fault_after_the_save_leaves_the_hub_saved_and_is_reported_in_today_s_words(
    db_path, old_refuses, new_refuses, base_role_gone, faults
):
    """The hub is 7921, its panel 7930 standing; the manager points it at 7920, and once the hub
    is saved Discord refuses to clear the old hub's overwrites (*old-hub-refused*), to set the new
    hub's (*new-hub-refused*), or both; or the league's base role has been deleted from the server,
    which keeps the hub closed (*base-role-gone*). The hub stays set to 7920 with its audit entry,
    the panel is still posted there and the old one taken down, and the queue is not stopped. The
    reply adds each fault beneath today's, marked ⚠️, and the log channel takes the one line,
    "Success, with faults", with the channel and each fault beneath it, in order."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    if base_role_gone:
        await _configure(db_path, base_role_id=GONE_ROLE)
    old = _channel(OLD_HUB, edit_raises=_REFUSED if old_refuses else None)
    new = _channel(HUB, edit_raises=_REFUSED if new_refuses else None)
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert [row["change_type"] for row in await _audit(db_path)] == ["HUB_CHANNEL_SET"]
    new.send.assert_awaited_once()
    old._partial.delete.assert_awaited_once()
    assert await stopped_job(db_path) is None
    assert _replied(interaction) == SET + "".join(f"\n⚠️ {fault}" for fault in faults)
    assert _lines(cog) == [_with_faults(*faults)]


async def test_a_step_after_the_save_that_raises_leaves_the_hub_saved_and_records_no_success(
    db_path,
):
    """The hub is 7921, its panel 7930 standing; the manager points it at 7920, and once the hub
    is saved, setting the new hub's permissions raises a fault of the bot's own, not a refusal
    of Discord's. The hub stays set to 7920 with its audit entry, the panel's refresh still
    follows and posts the new panel, and the queue is not stopped. The reply adds that the hub's
    permissions could not be updated, in the words the role settings use; the log channel takes
    the queue's failure line alone, no success line."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB, edit_raises=RuntimeError("x"))
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert [row["change_type"] for row in await _audit(db_path)] == ["HUB_CHANNEL_SET"]
    new.send.assert_awaited_once()
    assert await stopped_job(db_path) is None
    assert _replied(interaction) == (
        f"{SET}\n⚠️ The setting is saved, but the hub's permissions could not be updated."
    )
    assert _lines(cog) == [
        f"❌ `/bot hub-channel` failed for {NAMED} — RuntimeError. The details are in the "
        f"host's log."
    ]


@pytest.mark.parametrize("manage", [True, False], ids=["bot-manages", "bot-cannot-manage"])
async def test_a_channel_doing_another_job_is_refused(db_path, manage):
    """The log channel is the bot's; a panel there would sink under its lines. Refused at the
    ask in today's words: nothing is written, nothing touched, and nothing asked of the queue.
    Where the bot also lacks Manage Channel and Manage Permissions there
    (*bot-cannot-manage*), the channel's job is still the refusal given: it is checked first."""
    log_channel = _channel(LOG_CHANNEL, manage=manage)
    cog, interaction = await _setup(db_path, log_channel)

    await _run(cog, interaction, log_channel)

    assert _replied(interaction) == (
        f"❌ <#{LOG_CHANNEL}> is already the bot log channel. A channel does one job — pick one "
        f"that is not in use, or clear the other setting first."
    )
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}
    log_channel.edit.assert_not_awaited()
    log_channel.send.assert_not_awaited()
    assert await change_rows(db_path) == []


@pytest.mark.parametrize("manage", [True, False], ids=["bot-manages", "bot-cannot-manage"])
async def test_setting_the_hub_it_already_is_changes_nothing(db_path, manage):
    """Core spec :91, one panel in the hub, and the refusal of a channel's own value. The hub is
    7920, its panel 7930 standing: running the command for 7920 again is refused at the ask in
    today's words, before anything is written, so the panel 7930 is the one panel left
    standing — none posted beside it, none deleted, and nothing asked of the queue. Where the
    bot has also lost Manage Channel and Manage Permissions there (*bot-cannot-manage*), the
    channel's own value is still the refusal given: it is checked first."""
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=OLD_PANEL)
    channel = _channel(HUB, manage=manage)
    cog, interaction = await _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert _replied(interaction) == f"ℹ️ <#{HUB}> is already the hub channel. Nothing was changed."
    channel.send.assert_not_awaited()
    channel._partial.delete.assert_not_awaited()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": OLD_PANEL}
    assert await change_rows(db_path) == []


async def test_a_channel_the_bot_cannot_manage_is_refused_before_anything_is_written(db_path):
    """Refused at the ask in today's words: nothing is written and nothing asked of the queue."""
    channel = _channel(HUB, manage=False)
    cog, interaction = await _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert _replied(interaction) == (
        f"❌ The bot needs **Manage Channel** and **Manage Permissions** on <#{HUB}> to set who "
        f"may see the hub. Nothing was changed."
    )
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}
    assert await change_rows(db_path) == []


async def test_a_panel_that_cannot_be_posted_stops_the_queue_and_the_hub_is_kept(db_path):
    """The channel is the right one; the repair is in Discord's settings. Discord refuses the
    new panel: the hub stays set to the channel chosen, and the queue stops at the panel's
    refresh, for Retry once the bot may post there, rather than the fault being reported."""
    channel = _channel(HUB, send_raises=http_error(discord.Forbidden, status=403,
                                                   text="Missing Access"))
    cog, interaction = await _setup(db_path, channel)

    await _run(cog, interaction, channel)

    assert (await _stored(db_path))["hub_channel_id"] == HUB
    stopped = await stopped_job(db_path)
    assert stopped is not None
    (refresh,) = [row for row in await change_rows(db_path) if row["id"] == stopped["change_id"]]
    assert refresh["kind"] == hub_service.HUB_REFRESH


# ── Moving the hub ────────────────────────────────────────────────────────


async def test_moving_the_hub_deletes_the_old_panel_and_clears_the_old_channel(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    old.get_partial_message.assert_called_once_with(OLD_PANEL)
    old._partial.delete.assert_awaited_once()
    old.edit.assert_awaited_once_with(overwrites={})
    new.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_an_old_panel_already_deleted_does_not_stop_the_move(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    old._partial.delete = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "gone")
    )
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert _replied(interaction) == SET
    assert (await _stored(db_path))["hub_channel_id"] == HUB
    assert await stopped_job(db_path) is None


async def test_an_old_hub_channel_deleted_from_the_server_is_stepped_over(db_path):
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    new = _channel(HUB)
    cog, interaction = await _setup(db_path, new)

    await _run(cog, interaction, new)

    assert _replied(interaction) == SET
    new.send.assert_awaited_once()


async def test_moving_the_hub_posts_the_new_panel_before_deleting_the_old(db_path):
    """The hub moves from 7921, whose panel stands, to 7920. The new panel is posted first and
    the old one deleted after, as a job of its own, so the league is never left without one."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    order: list[str] = []
    new.send.side_effect = lambda *a, **k: order.append("post new") or MagicMock(id=NEW_PANEL)
    old._partial.delete.side_effect = lambda *a, **k: order.append("delete old")
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    assert order == ["post new", "delete old"]
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_the_old_panel_comes_down_when_an_earlier_refresh_already_posted_the_new(db_path):
    """The manager moves the hub from 7921 to 7920, and a module toggle's refresh is asked while
    the move waits on the queue. The toggle's refresh runs after the move is saved and before
    the move's own, and posts the new panel, so the move's own refresh only edits it; the old
    panel still comes down, as the new one is up either way, and the new is posted once."""
    from leaguebot.core.models.change import ChangeOrigin

    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    cog, interaction = await _setup(db_path, old, new)
    await undecorate(BotCog.handle_hub_channel)(cog, interaction, new)
    await cog.bot.change_queue.ask(
        hub_service.HUB_REFRESH, {"command": "/module enable weather"},
        what="refreshing the hub panel", origin=ChangeOrigin.BOT,
    )

    await run_queue(cog.bot)

    new.send.assert_awaited_once()
    old._partial.delete.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}


async def test_a_refused_deletion_of_the_old_panel_stops_the_queue(db_path):
    """Discord refuses to delete the panel in the old hub 7921: the new panel stands in 7920
    and is kept, and the queue stops at the old panel's deletion rather than reporting it."""
    await _configure(db_path, hub_channel_id=OLD_HUB, hub_message_id=OLD_PANEL)
    old, new = _channel(OLD_HUB), _channel(HUB)
    old._partial.delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )
    cog, interaction = await _setup(db_path, old, new)

    await _run(cog, interaction, new)

    new.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert await stopped_job(db_path) is not None


async def test_the_hub_is_set_while_the_queue_is_stopped_on_another_job(db_path):
    """The league's server is set up with no hub, and the league's queue is stopped on another
    job, which failed on a fault in the bot. The manager (77) points the hub at 7920 with
    `/bot hub-channel`, the remedy the hub panel's stop notice names. They are acknowledged at once
    and told the change runs at once, past the stop, and that the stopped job stays stopped;
    nothing is written as they ask. Once the queue runs, 7920 is the hub, its permissions are set
    and the manager is told it was set and its panel follows on the queue; the panel's refresh
    waits behind the stop, so no panel is posted yet, and the other job is still stopped where it
    was."""
    channel = _channel(HUB)
    cog, interaction = await _setup(db_path, channel)
    stopped = await stop_the_league_lane(cog.bot, db_path)

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)

    first_job = min(
        row["id"] for row in await step_rows(db_path) if row["change_id"] != stopped["change_id"]
    )
    assert acknowledgement(interaction) == (
        f"⏳ Setting **Hub channel** to <#{HUB}>. This message will be updated when it is done; "
        f"if it takes longer, the log channel will say so. It begins with job #{first_job}."
        + PAST_THE_STOP.format(job=stopped["id"])
    )
    assert await _stored(db_path) == {"hub_channel_id": None, "hub_message_id": None}
    await run_queue(cog.bot)

    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": None}
    assert _replied(interaction) == SET
    channel.edit.assert_awaited()
    channel.send.assert_not_awaited()
    still = await stopped_job(db_path)
    assert still is not None
    assert (still["id"], still["change_id"]) == (stopped["id"], stopped["change_id"])


async def test_a_stopped_hub_panel_lands_once_the_hub_is_set_and_retried(db_path):
    """Set the channel, then Retry: the remedy the stop notice leads a manager to, end to end.

    The hub is set to 7921, which has since been deleted from the server, and no panel stands.
    The bot's refresh of the hub panel stops the queue, its notice naming `/bot hub-channel`. The
    manager (77) runs `/bot hub-channel` for 7920 and is acknowledged; the queue runs. The
    setting does not wait behind the stopped refresh: it runs past it, saves 7920 as the hub,
    sets its permissions and tells the manager it was set, while the refresh still stops the
    league's lane and no panel is posted. The manager presses Retry: the stopped refresh posts
    the panel in 7920 and keeps its id, and the refresh the setting asked for runs after it and
    edits that panel in place, so one panel stands and nothing stops the queue."""
    from leaguebot.core.models.change import ChangeOrigin

    channel = _channel(HUB)
    cog, interaction = await _setup(db_path, channel)
    await _configure(db_path, hub_channel_id=OLD_HUB)
    await cog.bot.change_queue.ask(
        hub_service.HUB_REFRESH, {"command": "/module enable weather"},
        what="refreshing the hub panel", origin=ChangeOrigin.BOT,
    )
    await run_queue(cog.bot)
    assert await stopped_job(db_path) is not None
    assert any("`/bot hub-channel`" in line for line in _lines(cog))

    await undecorate(BotCog.handle_hub_channel)(cog, interaction, channel)
    assert acknowledgement(interaction).startswith("⏳ ")
    await run_queue(cog.bot)

    assert _replied(interaction) == SET
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": None}
    channel.edit.assert_awaited()
    channel.send.assert_not_awaited()
    assert await stopped_job(db_path) is not None, "setting the channel ran the job"
    await retry_job(cog.bot)

    channel.send.assert_awaited_once()
    assert await _stored(db_path) == {"hub_channel_id": HUB, "hub_message_id": NEW_PANEL}
    assert await stopped_job(db_path) is None


# ── Its tier ──────────────────────────────────────────────────────────────


def test_the_command_is_a_league_manager_s_in_the_interaction_channel():
    """Like every other channel command; it repairs nothing the guards read."""
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_MANAGER, TIER_ATTRIBUTE

    callback = BotCog.handle_hub_channel.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER
    assert not getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE, False)
