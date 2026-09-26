"""The four per-module division channel commands, and where they have diverged.

Issue #208. `/division weather-channel`, `results-channel`, `standings-channel` and
`verdicts-channel` share one body, `_set_division_channel`, covered by
`test_division_channel_assignment.py`. These five — `rsvp-channel`, `attendance-channel`,
`verdicts-channel`, `lineup-channel` and `calendar-channel` — do not. Each carries its own copy
of the same forty lines, and copies drift.

**What all four hold to** is what this file pins first: the season and the division must both
exist, the channel-does-one-job guard runs before any write, and the assignment is audited and
logged. Those are parametrised across all four precisely so a divergence in one of them fails
rather than passes quietly.

**Where they legitimately differ.** `rsvp` and `attendance` belong to the attendance module and
`verdicts` to the results module, and each refuses when its own module is switched off —
assigning a channel to a module that is not running would configure something no code reads.
`lineup` and `calendar` are core settings with no such gate. The three gated ones also defer
first, because their work reaches a service; the core pair reply directly. That difference is invisible until the refusal guard has to choose
between `response.send_message` and `followup.send`, which is why it is pinned here as well as
in the guard's own file.

**Where they once differed by accident** (issue #212, which #208 pinned rather than fixed).
`lineup` and `calendar` wrote `old_value = ''` into the audit entry, so a reassignment could not
be traced back, and the attendance pair wrote their channel ids as strings where every other
command writes integers. All five now record the channel they replaced, as an integer —
`test_the_core_pair_record_the_channel_they_replaced` and
`test_every_channel_command_here_audits_its_ids_as_integers`.

**"Set" and "updated" are different words for a reason:** a manager who meant to assign a fresh
channel and is told it was *updated* has just moved an existing one, and that is worth noticing
before the next round posts somewhere unexpected. The core pair always said "set", having never
read the old id; all five now choose the word from it.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.channel_registry_service import ChannelUse
from tests.support.undecorate import undecorate

SERVER_ID = 9808
SEASON_ID = 1
DIVISION_ID = 11
CHANNEL_ID = 780001
ACTOR_ID = 77

#: command attribute, audit change_type, and whether the attendance module gates it.
COMMANDS = {
    "rsvp": ("division_rsvp_channel", "RSVP_CHANNEL_SET", "attendance"),
    "attendance": ("division_attendance_channel", "ATTENDANCE_CHANNEL_SET", "attendance"),
    "verdicts": ("division_verdicts_channel", "VERDICTS_CHANNEL_SET", "results"),
    "lineup": ("division_lineup_channel", "SIGNUP_LINEUP_CHANNEL_SET", None),
    "calendar": ("division_calendar_channel", "DIVISION_CALENDAR_CHANNEL_SET", None),
}
ALL = sorted(COMMANDS)
GATED = sorted(k for k, v in COMMANDS.items() if v[2])
UNGATED = sorted(k for k, v in COMMANDS.items() if not v[2])


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, name: str = "module_channels") -> str:
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


def _division(name: str = "Division 1"):
    return SimpleNamespace(id=DIVISION_ID, name=name, tier=1)


def _make_cog(
    db_path: str,
    *,
    season=SimpleNamespace(id=SEASON_ID),
    divisions=None,
    attendance_enabled: bool = True,
    results_enabled: bool = True,
    old_config=None,
    old_penalty_channel=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.season_service.set_division_penalty_channel = AsyncMock(
        return_value=old_penalty_channel
    )
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_division_config = AsyncMock(return_value=old_config)
    bot.attendance_service.set_rsvp_channel = AsyncMock(return_value=None)
    bot.attendance_service.set_attendance_channel = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
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
    attr = COMMANDS[which][0]
    body = undecorate(getattr(SeasonCog, attr))
    return await body(cog, interaction, name, channel or _channel())


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id "
            "FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]


#: The division column each core command writes.
CORE_COLUMNS = {"lineup": "lineup_channel_id", "calendar": "calendar_channel_id"}


async def _seed_core_channel(db_path: str, which: str, channel_id: int) -> None:
    """Give the division a channel already, as a move finds it."""
    async with get_connection(db_path) as db:
        await db.execute(
            f"UPDATE divisions SET {CORE_COLUMNS[which]} = ? WHERE id = ?",
            (channel_id, DIVISION_ID),
        )
        await db.commit()


async def _channel_column(db_path: str, column: str):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM divisions WHERE id = ?", (DIVISION_ID,)
        )
        row = await cursor.fetchone()
        return row[column]


# ---------------------------------------------------------------------------
# What all four hold to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ALL)
async def test_the_assignment_is_audited(tmp_path, which):
    """A channel change nobody recorded cannot be explained afterwards, and these four are
    exactly the settings a league argues about when a post lands in the wrong place."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [COMMANDS[which][1]]
    assert rows[0]["division_id"] == DIVISION_ID
    assert rows[0]["actor_id"] == ACTOR_ID


@pytest.mark.parametrize("which", ALL)
async def test_the_new_channel_is_recorded_in_the_audit(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    new_value = json.loads((await _audit_rows(db_path))[0]["new_value"])
    assert str(new_value["channel_id"]) == str(CHANNEL_ID)


@pytest.mark.parametrize("which", ALL)
async def test_a_server_with_no_season_is_refused(tmp_path, which):
    """There is no division to hang the setting on, and the command would otherwise fail
    somewhere deeper with nothing a manager could act on."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _run(cog, which, interaction)

    assert "No season is live" in _replied(interaction)
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", ALL)
async def test_an_unknown_division_is_refused_by_name(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, name="Division 9")

    assert "Division 9" in _replied(interaction)
    assert "not found" in _replied(interaction)
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", ALL)
async def test_the_division_is_matched_regardless_of_case(tmp_path, which):
    """A manager typing a division name into a slash command is not copying it letter for
    letter, and refusing `division 1` for `Division 1` would be a puzzle."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, name="dIvIsIoN 1")

    assert "not found" not in _replied(interaction)
    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", ALL)
async def test_a_channel_already_doing_another_job_is_refused(tmp_path, which, monkeypatch):
    """A channel serves one purpose across the whole server (decided 2026-09-06). The guard
    runs before the write, so a refusal leaves the configuration as it stood."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    async def _in_use(*_args, **_kwargs):
        return ChannelUse("results", "Division 2")

    monkeypatch.setattr("leaguebot.core.services.channel_registry_service.find_channel_use", _in_use)

    await _run(cog, which, interaction)

    assert await _audit_rows(db_path) == []
    assert "#notices" in _replied(interaction)


@pytest.mark.parametrize("which", ALL)
async def test_the_assignment_is_logged(tmp_path, which):
    """The log is where a league sees a manager's actions; a channel move that reaches only
    the audit table is invisible to everyone but a maintainer with a SQL prompt."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "Division 1" in logged
    assert "#notices" in logged
    assert "Manager" in logged


@pytest.mark.parametrize("which", ALL)
async def test_the_manager_is_told_which_channel_was_assigned(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction)

    replied = _replied(interaction)
    assert "#notices" in replied
    assert "Division 1" in replied


# ---------------------------------------------------------------------------
# The attendance pair: a module gate and a permission check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", GATED)
async def test_a_disabled_module_refuses_the_assignment(tmp_path, which):
    """Assigning a channel to a module that is not running configures something no code
    reads, and the notices the manager is expecting would never arrive. Each command
    checks its *own* module, so a league running attendance and not results must still be
    refused the verdicts channel."""
    module = COMMANDS[which][2]
    db_path = await _make_db(tmp_path, name=f"disabled_{which}")
    cog = _make_cog(
        db_path,
        attendance_enabled=(module != "attendance"),
        results_enabled=(module != "results"),
    )
    interaction = _interaction()

    await _run(cog, which, interaction)

    assert "not enabled" in _replied(interaction)
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", GATED)
async def test_another_modules_state_does_not_refuse_the_assignment(tmp_path, which):
    """The other half of the same rule: a league that has switched attendance off must
    still be able to set its verdicts channel."""
    module = COMMANDS[which][2]
    db_path = await _make_db(tmp_path, name=f"othermodule_{which}")
    cog = _make_cog(
        db_path,
        attendance_enabled=(module == "attendance"),
        results_enabled=(module == "results"),
    )

    await _run(cog, which, _interaction())

    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", UNGATED)
async def test_a_core_channel_needs_no_module(tmp_path, which):
    """Lineups and the calendar are core settings — gating them on attendance would refuse
    a league that never enabled the module."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, attendance_enabled=False)

    await _run(cog, which, _interaction())

    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", GATED)
async def test_a_channel_the_bot_cannot_post_in_is_refused(tmp_path, which):
    """Checked before the write rather than discovered at the first notice, when the round
    it was meant to announce has already started."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction, channel=_channel(may_post=False))

    assert "Cannot access that channel" in _replied(interaction)
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", GATED)
async def test_a_command_outside_a_guild_is_refused(tmp_path, which):
    """`permissions_for(guild.me)` needs a guild, so the absence is answered rather than
    raising an `AttributeError` at a manager."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(guild=False)

    await _run(cog, which, interaction)

    assert "Cannot access that channel" in _replied(interaction)


@pytest.mark.parametrize("which", GATED)
async def test_the_gated_commands_defer_before_working(tmp_path, which):
    """Their work reaches a service and the database, which can outrun Discord's
    three-second response window."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()


@pytest.mark.parametrize("which", UNGATED)
async def test_the_core_pair_reply_directly(tmp_path, which):
    """They write two rows and answer; deferring would be a second round trip for nothing."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _run(cog, which, interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited()


async def test_the_rsvp_channel_reaches_the_attendance_service(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, "rsvp", _interaction())

    cog.bot.attendance_service.set_rsvp_channel.assert_awaited_once_with(
        DIVISION_ID, CHANNEL_ID
    )
    cog.bot.attendance_service.set_attendance_channel.assert_not_awaited()


async def test_the_attendance_channel_reaches_the_attendance_service(tmp_path):
    """The two commands sit next to each other and write neighbouring columns; a copy-paste
    that left the wrong setter behind would put RSVP notices in the logging channel."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, "attendance", _interaction())

    cog.bot.attendance_service.set_attendance_channel.assert_awaited_once_with(
        DIVISION_ID, CHANNEL_ID
    )
    cog.bot.attendance_service.set_rsvp_channel.assert_not_awaited()


@pytest.mark.parametrize(
    "which,column",
    [("lineup", "lineup_channel_id"), ("calendar", "calendar_channel_id")],
)
async def test_the_core_pair_write_their_own_column(tmp_path, which, column):
    """Written with SQL here rather than through a service, so nothing but a test stands
    between a mistyped column name and a setting that silently never takes."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    assert await _channel_column(db_path, column) == CHANNEL_ID


# ---------------------------------------------------------------------------
# Where the four have drifted — pinned as they stand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ALL)
async def test_a_first_assignment_and_a_move_are_worded_differently(tmp_path, which):
    """A manager who meant to assign a fresh channel and is told it was *updated* has just
    moved an existing one — worth noticing before the next round posts somewhere else. The
    core pair always said "set" until issue #212."""
    first_db = await _make_db(tmp_path, name=f"first_{which}")
    moved_db = await _make_db(tmp_path, name=f"moved_{which}")
    if which in CORE_COLUMNS:
        await _seed_core_channel(moved_db, which, 111)
    first = _interaction()
    moved = _interaction()

    await _run(_make_cog(first_db, old_config=None), which, first)
    await _run(
        _make_cog(
            moved_db,
            old_config=SimpleNamespace(rsvp_channel_id="111", attendance_channel_id="111"),
            old_penalty_channel=111,
        ),
        which,
        moved,
    )

    assert "set to" in _replied(first)
    assert "updated to" in _replied(moved)


@pytest.mark.parametrize("which", GATED)
async def test_the_gated_commands_record_the_channel_they_replaced(tmp_path, which):
    """Which is what makes the audit answer "where were the notices going before?"."""
    db_path = await _make_db(tmp_path, name=f"replaced_{which}")
    cog = _make_cog(
        db_path,
        old_config=SimpleNamespace(rsvp_channel_id=111, attendance_channel_id=111),
        old_penalty_channel=111,
    )

    await _run(cog, which, _interaction())

    old = json.loads((await _audit_rows(db_path))[0]["old_value"])
    assert old["channel_id"] == 111


@pytest.mark.parametrize("which", UNGATED)
async def test_the_core_pair_record_the_channel_they_replaced(tmp_path, which):
    """They once wrote an empty `old_value`, so a lineup or calendar channel that moved could
    not be traced back — against the rule that every change is recorded from what to what
    (issue #212)."""
    db_path = await _make_db(tmp_path)
    await _seed_core_channel(db_path, which, 111)

    await _run(_make_cog(db_path), which, _interaction())

    old = json.loads((await _audit_rows(db_path))[0]["old_value"])
    assert old["channel_id"] == 111


@pytest.mark.parametrize("which", UNGATED)
async def test_a_first_core_assignment_records_no_previous_channel(tmp_path, which):
    db_path = await _make_db(tmp_path)

    await _run(_make_cog(db_path), which, _interaction())

    assert json.loads((await _audit_rows(db_path))[0]["old_value"]) == {"channel_id": None}


@pytest.mark.parametrize("which", ALL)
async def test_every_channel_command_here_audits_its_ids_as_integers(tmp_path, which):
    """The attendance pair once wrote both ids as strings, since their table stores them as
    text, where every other channel command writes integers — so a reader querying the audit
    for a channel id had to know which command wrote the row (issue #212). The old id is
    given here as each source really holds it: text for the attendance pair."""
    db_path = await _make_db(tmp_path, name=f"types_{which}")
    if which in CORE_COLUMNS:
        await _seed_core_channel(db_path, which, 111)
    cog = _make_cog(
        db_path,
        old_config=SimpleNamespace(rsvp_channel_id="111", attendance_channel_id="111"),
        old_penalty_channel=111,
    )

    await _run(cog, which, _interaction())

    row = (await _audit_rows(db_path))[0]
    assert json.loads(row["old_value"])["channel_id"] == 111
    assert json.loads(row["new_value"])["channel_id"] == CHANNEL_ID


# ---------------------------------------------------------------------------
# The log line names the command by its module's group (#462)
# ---------------------------------------------------------------------------

#: The command each module's channel answers to once it sits under that module's group.
MODULE_COMMANDS = {
    "verdicts": "/results channel verdicts",
    "rsvp": "/attendance channel rsvp",
    "attendance": "/attendance channel attendance",
}


@pytest.mark.parametrize(
    "which",
    [
        pytest.param(
            which,
            id=which,
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"#462: the log line still names /division {which}-channel, "
                f"not {MODULE_COMMANDS[which]}",
            ),
        )
        for which in GATED
    ],
)
async def test_the_log_line_names_the_command_a_league_now_types(tmp_path, which):
    """A log line names the member, the command and its outcome, with the division and the
    channel beneath it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _run(cog, which, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert logged.splitlines() == [
        f"Manager (<@{ACTOR_ID}>) | {MODULE_COMMANDS[which]} | Success",
        "  division: Division 1",
        "  channel: #notices",
    ]
