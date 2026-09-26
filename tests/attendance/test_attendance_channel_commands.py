"""`/attendance channel rsvp` and `/attendance channel attendance`: a division's two attendance
channels.

Attendance's own commands, under attendance's own group. They sat under core's `/division` until
#462 moved them, and only their names changed. Each carries its own copy of the same forty lines,
and copies drift, so every test here is parametrised across both: a divergence in one fails
rather than passes quietly.

**What both hold to.** The attendance module must be on, and the command says so in its own
words; the bot must be able to post in the channel; the live season and the division must both
exist; the channel-does-one-job check runs before any write; and the assignment is audited and
logged. Both defer first, because their work reaches a service, so every reply after the module
check follows up.

**Where they once differed by accident** (issue #212). The pair wrote their channel ids into the
audit as strings, their table storing them as text, where every other channel command writes
integers. Both now record the channel they replaced, as an integer.

**"Set" and "updated" are different words for a reason:** a manager who meant to assign a fresh
channel and is told it was *updated* has just moved an existing one, and that is worth noticing
before the next round's call is posted somewhere unexpected.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.channel_registry_service import ChannelUse
from tests.support.undecorate import undecorate

SERVER_ID = 9809
SEASON_ID = 1
DIVISION_ID = 11
CHANNEL_ID = 780001
ACTOR_ID = 77

#: Each command's method, its audit change type, the setter it reaches, the name its reply
#: gives the channel, and the command a league types.
COMMANDS = {
    "rsvp": (
        "channel_rsvp", "RSVP_CHANNEL_SET", "set_rsvp_channel", "RSVP",
        "/attendance channel rsvp",
    ),
    "attendance": (
        "channel_attendance", "ATTENDANCE_CHANNEL_SET", "set_attendance_channel", "Attendance",
        "/attendance channel attendance",
    ),
}
BOTH = sorted(COMMANDS)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, name: str = "attendance_channels") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    season=SimpleNamespace(id=SEASON_ID),
    attendance_enabled: bool = True,
    results_enabled: bool = True,
    old_config=None,
) -> AttendanceCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Division 1", tier=1)]
    )
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_division_config = AsyncMock(return_value=old_config)
    bot.attendance_service.set_rsvp_channel = AsyncMock(return_value=None)
    bot.attendance_service.set_attendance_channel = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = bot
    return cog


def _channel(channel_id: int = CHANNEL_ID, name: str = "notices", *, may_post: bool = True):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"#{name}"
    channel.permissions_for = MagicMock(
        return_value=SimpleNamespace(send_messages=may_post)
    )
    return channel


def _interaction(*, guild=True):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock() if guild else None
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _run(cog, which, interaction, *, name="Division 1", channel=None):
    body = undecorate(getattr(AttendanceCog, COMMANDS[which][0]))
    return await body(cog, interaction, name, channel or _channel())


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id "
            "FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# The module, and the channel the bot must be able to post in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_a_disabled_module_refuses_the_assignment(tmp_path, which):
    """Assigning a channel to a module that is not running configures something no code
    reads, and the notices the manager is expecting would never arrive. Word for word: the
    attendance cog's own gate is worded differently, and the command keeps the words it had
    before it moved."""
    db_path = await _make_db(tmp_path, name=f"disabled_{which}")
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _interaction()

    await _run(cog, which, interaction)

    assert _replied(interaction) == "❌ The Attendance module is not enabled."
    interaction.response.defer.assert_not_awaited()
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", BOTH)
async def test_another_modules_state_does_not_refuse_the_assignment(tmp_path, which):
    """Each command checks its *own* module: a league that has switched results off must
    still be able to set its attendance channels."""
    db_path = await _make_db(tmp_path, name=f"othermodule_{which}")
    cog = _make_cog(db_path, results_enabled=False)

    await _run(cog, which, _interaction())

    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", BOTH)
async def test_a_channel_the_bot_cannot_post_in_is_refused(tmp_path, which):
    """Checked before the write rather than discovered at the first notice, when the round
    it was meant to announce has already started."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, channel=_channel(may_post=False))

    assert _replied(interaction) == (
        "❌ Cannot access that channel. Ensure the bot has permission to post there."
    )
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", BOTH)
async def test_a_command_outside_a_guild_is_refused(tmp_path, which):
    """`permissions_for(guild.me)` needs a guild, so the absence is answered rather than
    raising an `AttributeError` at a manager."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(guild=False)

    await _run(cog, which, interaction)

    assert "Cannot access that channel" in _replied(interaction)


@pytest.mark.parametrize("which", BOTH)
async def test_the_commands_defer_before_working(tmp_path, which):
    """Their work reaches a service and the database, which can outrun Discord's
    three-second response window."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# The season, the division, and a channel doing one job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_a_server_with_no_season_is_refused(tmp_path, which):
    """There is no division to hang the setting on, and the command would otherwise fail
    somewhere deeper with nothing a manager could act on."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _run(cog, which, interaction)

    assert "No season is live" in _replied(interaction)
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", BOTH)
async def test_an_unknown_division_is_refused_by_name(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, name="Division 9")

    assert _replied(interaction) == '❌ Division "Division 9" not found.'
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", BOTH)
async def test_the_division_is_matched_regardless_of_case(tmp_path, which):
    """A manager typing a division name into a slash command is not copying it letter for
    letter, and refusing `division 1` for `Division 1` would be a puzzle."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, name="dIvIsIoN 1")

    assert "not found" not in _replied(interaction)
    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", BOTH)
async def test_a_channel_already_doing_another_job_is_refused(tmp_path, which, monkeypatch):
    """A channel serves one purpose across the whole server (decided 2026-09-06). The check
    runs before the write, so a refusal leaves the configuration as it stood, and follows up,
    the command having deferred."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    async def _in_use(*_args, **_kwargs):
        return ChannelUse("results", "Division 2")

    monkeypatch.setattr("leaguebot.core.services.channel_registry_service.find_channel_use", _in_use)

    await _run(cog, which, interaction)

    assert await _audit_rows(db_path) == []
    assert "#notices is already the results channel for **Division 2**" in _replied(interaction)
    getattr(cog.bot.attendance_service, COMMANDS[which][2]).assert_not_awaited()


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_each_command_reaches_its_own_setter(tmp_path, which):
    """The two commands sit next to each other and write neighbouring columns; a copy-paste
    that left the wrong setter behind would put RSVP notices in the logging channel."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    other = next(w for w in BOTH if w != which)
    getattr(cog.bot.attendance_service, COMMANDS[which][2]).assert_awaited_once_with(
        DIVISION_ID, CHANNEL_ID
    )
    getattr(cog.bot.attendance_service, COMMANDS[other][2]).assert_not_awaited()


@pytest.mark.parametrize("which", BOTH)
async def test_the_assignment_is_audited(tmp_path, which):
    """A channel change nobody recorded cannot be explained afterwards. The record is the one
    the command wrote while core held it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [COMMANDS[which][1]]
    assert rows[0]["division_id"] == DIVISION_ID
    assert rows[0]["actor_id"] == ACTOR_ID
    assert json.loads(rows[0]["old_value"]) == {"channel_id": None}
    assert json.loads(rows[0]["new_value"]) == {"channel_id": CHANNEL_ID}


@pytest.mark.parametrize("which", BOTH)
async def test_a_first_assignment_and_a_move_are_worded_differently(tmp_path, which):
    first_db = await _make_db(tmp_path, name=f"first_{which}")
    moved_db = await _make_db(tmp_path, name=f"moved_{which}")
    first = _interaction()
    moved = _interaction()

    await _run(_make_cog(first_db, old_config=None), which, first)
    await _run(
        _make_cog(
            moved_db,
            old_config=SimpleNamespace(rsvp_channel_id="111", attendance_channel_id="111"),
        ),
        which,
        moved,
    )

    label = COMMANDS[which][3]
    assert _replied(first) == f"✅ {label} channel for Division 1 set to #notices."
    assert _replied(moved) == f"✅ {label} channel for Division 1 updated to #notices."


@pytest.mark.parametrize("which", BOTH)
async def test_the_channel_replaced_is_audited_as_an_integer(tmp_path, which):
    """Which is what makes the audit answer "where were the notices going before?". The old
    id is given as the table really holds it, as text (#212)."""
    db_path = await _make_db(tmp_path, name=f"replaced_{which}")
    cog = _make_cog(
        db_path,
        old_config=SimpleNamespace(rsvp_channel_id="111", attendance_channel_id="111"),
    )

    await _run(cog, which, _interaction())

    row = (await _audit_rows(db_path))[0]
    assert json.loads(row["old_value"])["channel_id"] == 111
    assert json.loads(row["new_value"])["channel_id"] == CHANNEL_ID


@pytest.mark.parametrize("which", BOTH)
async def test_the_log_line_names_the_command_a_league_now_types(tmp_path, which):
    """A log line names the member, the command and its outcome, with the division and the
    channel beneath it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines() == [
        f"Manager (<@{ACTOR_ID}>) | {COMMANDS[which][4]} | Success",
        "  division: Division 1",
        "  channel: #notices",
    ]
