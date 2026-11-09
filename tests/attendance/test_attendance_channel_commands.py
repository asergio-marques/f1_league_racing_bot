"""`/attendance channel rsvp` and `/attendance channel attendance`: a division's two attendance
channels.

Attendance's own commands, under attendance's own group. They sat under core's `/division` until
#462 moved them, and only their names changed. Each carries its own copy of the same forty lines,
and copies drift, so every test here is parametrised across both: a divergence in one fails
rather than passes quietly.

**What both hold to.** The attendance module must be on, and the command says so in its own
words; the bot must be able to post in the channel; the live season and the division must both
exist; the channel-does-one-job check runs before any write; and the assignment is audited and
logged.

**Where they once differed by accident** (issue #212). The pair wrote their channel ids into the
audit as strings, their table storing them as text, where every other channel command writes
integers. Both now record the channel they replaced, as an integer.

**"Set" and "updated" are different words for a reason:** a manager who meant to assign a fresh
channel and is told it was *updated* has just moved an existing one, and that is worth noticing
before the next round's call is posted somewhere unexpected.

**Each is an attendance `Setting` on the change queue** (#283), as `/results channel verdicts` is
(`tests/results/test_results_channel_commands.py`): the command answers with the queue's
acknowledgement, nothing deferred, and the queue writes the division's channel and its audit entry
in one save, stamped with the queue's clock, then updates the acknowledgement to the reply and
writes the line. The module, the bot's access to the channel, the season and the division are
judged by the command, which must find the division to ask for its change; the channel's use, and
the value already held, by the setting's check at the ask, in the same words. The check is made
again as the change starts, so attendance switched off, the division deleted, the season ended,
the channel claimed or the bot's access to it lost while the change waits is refused then, in the
ask's words; a division renamed meanwhile is still the division asked for, and gets its channel.
Each overtakes a stopped job: a stop notice for a check-in call or a sheet with no channel tells
the league to set it here, then press Retry. The tests run the bot the entry point builds, with
the real queue and "now" pinned, and run the queue.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    change_rows,
    league_bot,
    league_queue,
    member,
    register,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
)
from tests.support.season_league import set_module_flag
from tests.support.settings_league import interaction as member_asks
from tests.support.settings_league import last_said, said, unruled
from tests.support.undecorate import undecorate

SEASON_ID = 1
DIVISION_ID = 11
OTHER_DIVISION_ID = 12
CHANNEL_ID = 780001
OTHER_CHANNEL_ID = 780002
ACTOR_ID = 77
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The manager running the command, as a log line names them, the mention wrapped so it
#: notifies nobody.
NAMED = f"Manager (`<@{ACTOR_ID}>`)"

#: Each command: its method on the cog, the kind of change it asks of the queue, the column of
#: `attendance_division_config` it sets, the name its reply gives the channel, its audit change
#: type, and the name the queue's acknowledgement gives the channel.
COMMANDS = {
    "rsvp": (
        "channel_rsvp", "attendance.setting.rsvp_channel", "rsvp_channel_id", "RSVP",
        "RSVP_CHANNEL_SET", "RSVP",
    ),
    "attendance": (
        "channel_attendance", "attendance.setting.attendance_channel", "attendance_channel_id",
        "Attendance", "ATTENDANCE_CHANNEL_SET", "attendance",
    ),
}
BOTH = sorted(COMMANDS)

#: The word a refusal gives each command's own setting, as a league reads it.
REFUSAL_LABELS = {"rsvp": "check-in call", "attendance": "attendance"}

_OFF = "❌ The Attendance module is not enabled."
_NO_SEASON = (
    "❌ No season is live. A division's channels belong to the season being built or raced — "
    "start one with `/season setup`."
)
_NO_ACCESS = "❌ Cannot access that channel. Ensure the bot has permission to post there."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _not_found(name: str = "Division 1") -> str:
    """The commands' own refusal of a division the live season does not hold."""
    return f'❌ Division "{name}" not found.'


def _clash(channel_id: int = CHANNEL_ID) -> str:
    """The refusal of a channel already Division 2's results channel."""
    return (
        f"❌ <#{channel_id}> is already the results channel for **Division 2**. A channel does "
        f"one job — pick one that is not in use, or clear the other setting first."
    )


def _already(which: str, channel_id: int = CHANNEL_ID) -> str:
    """The refusal of the channel Division 1 already has for this very setting."""
    return (
        f"ℹ️ <#{channel_id}> is already the {REFUSAL_LABELS[which]} channel for **Division 1**. "
        f"Nothing was changed."
    )


def _reply(which: str, verb: str, *, name: str = "Division 1",
           channel_id: int = CHANNEL_ID) -> str:
    """The reply once the channel is saved, *verb* "set" or "updated": the division as typed,
    unbolded, and the channel by its mention."""
    return f"✅ {COMMANDS[which][3]} channel for {name} {verb} to <#{channel_id}>."


def _acknowledged(which: str, first_job: int) -> str:
    """The queue's acknowledgement of the command for Division 1 and channel 780001."""
    return (
        f"⏳ Setting the {COMMANDS[which][5]} channel for **Division 1** to <#{CHANNEL_ID}>. This "
        f"message will be updated when it is done; if it takes longer, the log channel will say "
        f"so. It begins with job #{first_job}."
    )


def _success_line(which: str, *, name: str = "Division 1", channel_name: str = "notices") -> str:
    return (
        f"{NAMED} | /attendance channel {which} | Success\n"
        f"  division: {name}\n"
        f"  channel: #{channel_name}"
    )


def _refusal_line(which: str, reply: str) -> str:
    """The one line a refusal records: the member, the command, and the reply without its
    mark."""
    return f"⛔ `/attendance channel {which}` refused for {NAMED} — {reply.split(' ', 1)[1]}"


async def _make_db(tmp_path, name: str = "attendance_channels", *, status: str = "SETUP",
                   attendance: bool = True, results: bool = True) -> str:
    """The league's server set up, with attendance on unless *attendance* is False and results
    on unless *results* is False, and Season 1 in *status* with its Division 1 (id 11) and
    neither of its attendance channels set."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "attendance", attendance)
    await set_module_flag(db_path, "results", results)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


async def _make_cog(db_path: str) -> AttendanceCog:
    """The cog on the league's bot (`league_bot`), with the league's log channel and its real
    change queue, "now" pinned to `NOW`. `cog.bot.channels` holds the channels the bot finds by
    id beside the league's own: `_channel` puts each there as it makes it."""
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    found = bot.get_channel
    bot.channels = {}
    bot.get_channel = MagicMock(
        side_effect=lambda channel_id: bot.channels.get(channel_id, found(channel_id))
    )
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = bot
    return cog


def _channel(cog, channel_id: int = CHANNEL_ID, name: str = "notices", *,
             may_post: bool = True):
    """A text channel as Discord gives one, its mention the one Discord renders, the bot allowed
    to post in it unless *may_post* is False, and found by the bot by its id."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(return_value=SimpleNamespace(send_messages=may_post))
    cog.bot.channels[channel_id] = channel
    return channel


def _interaction(cog, which: str):
    """The manager's (77) interaction for `/attendance channel <which>`, recording every word
    they are told."""
    return member_asks(
        cog.bot, f"attendance channel {which}",
        user=member(ACTOR_ID, "Manager", "Manager#0001"),
    )


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


async def _ask(cog, interaction, which: str, *, name="Division 1", channel=None) -> None:
    """The manager runs the command, and nothing more: the queue is not run."""
    await undecorate(getattr(AttendanceCog, COMMANDS[which][0]))(
        cog, interaction, name, channel or _channel(cog)
    )


async def _run(cog, interaction, which: str, *, name="Division 1", channel=None) -> None:
    """The manager runs the command, and the queue is run."""
    await _ask(cog, interaction, which, name=name, channel=channel)
    await run_queue(cog.bot)


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id, timestamp "
            "FROM audit_entries ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _channels(db_path: str, division_id: int = DIVISION_ID) -> dict[str, int | None]:
    """The division's two attendance channels as the database holds them, by command, each an
    integer; none set where it has no row."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_channel_id, attendance_channel_id "
            "FROM attendance_division_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
    held = {}
    for which, spec in COMMANDS.items():
        value = None if row is None else row[spec[2]]
        held[which] = None if value is None else int(value)
    return held


def _none_set() -> dict[str, int | None]:
    return {which: None for which in COMMANDS}


async def _seed(db_path: str, which: str, channel_id: int) -> None:
    """Give Division 1 *channel_id* as its *which* channel already, as text, as the table holds
    it."""
    column = COMMANDS[which][2]
    async with get_connection(db_path) as db:
        await db.execute(
            f"INSERT INTO attendance_division_config (division_id, {column}) VALUES (?, ?) "  # noqa: S608
            f"ON CONFLICT(division_id) DO UPDATE SET {column} = excluded.{column}",
            (DIVISION_ID, str(channel_id)),
        )
        await db.commit()


async def _claim(db_path: str) -> None:
    """Give channel 780001 to Division 2 (id 12) of the same season as its results channel."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 2', 2, 556)",
            (OTHER_DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) VALUES (?, ?)",
            (OTHER_DIVISION_ID, CHANNEL_ID),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Another module's state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_another_modules_state_does_not_refuse_the_assignment(tmp_path, which):
    """Results is off, attendance on. Each command checks its *own* module: a league that has
    switched results off must still be able to set its attendance channels, and one audit entry
    stands once the queue has run."""
    db_path = await _make_db(tmp_path, results=False)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, which), which)

    assert len(await _audit(db_path)) == 1


# ---------------------------------------------------------------------------
# The division as typed, and the channel it already has
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_the_division_is_matched_regardless_of_case(tmp_path, which):
    """A manager typing `dIvIsIoN 1` means Division 1: refusing it would be a puzzle. Once the
    queue has run, Division 1 has #notices as its channel, one audit entry stands, and the reply
    names the division as the manager typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, interaction, which, name="dIvIsIoN 1")

    assert await _channels(db_path) == {**_none_set(), which: CHANNEL_ID}
    assert len(await _audit(db_path)) == 1
    assert last_said(interaction) == _reply(which, "set", name="dIvIsIoN 1")


@pytest.mark.parametrize("which", BOTH)
async def test_re_setting_the_same_channel_is_refused_as_unchanged(tmp_path, which):
    """#notices is already Division 1's check-in call (or attendance) channel, and the manager
    runs the command for it again. Each command names its own setting to the check, so it is
    refused in its own words, not as a clash a manager would go looking for: the reply is the
    unchanged refusal word for word, no change is asked of the queue, nothing is audited,
    Division 1 keeps #notices, and the log channel takes the refusal as its only line."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, which, CHANNEL_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, interaction, which)

    assert last_said(interaction) == _already(which)
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _channels(db_path) == {**_none_set(), which: CHANNEL_ID}
    assert _lines(cog) == [_refusal_line(which, _already(which))]


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_each_command_writes_its_own_column(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The two
    commands sit next to each other and write neighbouring columns; one writing the wrong column
    would put check-in calls in the attendance channel. Once the queue has run, Division 1 has
    780001 in that command's column alone."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, which), which)

    assert await _channels(db_path) == {**_none_set(), which: CHANNEL_ID}


@pytest.mark.parametrize("which", BOTH)
async def test_the_assignment_is_audited(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    (id 77) runs `/attendance channel rsvp` (or `attendance`) for 780001. A channel change nobody
    recorded cannot be explained afterwards. The change is asked of the queue as that command's
    attendance setting, and the record is the one the command wrote while core held it: one
    `RSVP_CHANNEL_SET` (or `ATTENDANCE_CHANNEL_SET`) entry against Division 1 and the manager,
    from no channel to 780001, the ids as integers, stamped with the queue's clock, never the
    host's."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, which), which)

    assert [row["kind"] for row in await change_rows(db_path)] == [COMMANDS[which][1]]
    [entry] = await _audit(db_path)
    assert entry["change_type"] == COMMANDS[which][4]
    assert entry["division_id"] == DIVISION_ID
    assert entry["actor_id"] == ACTOR_ID
    assert json.loads(entry["old_value"]) == {"channel_id": None}
    assert json.loads(entry["new_value"]) == {"channel_id": CHANNEL_ID}
    assert entry["timestamp"] == NOW.isoformat()


@pytest.mark.parametrize("which", BOTH)
async def test_a_first_assignment_and_a_move_are_worded_differently(tmp_path, which):
    """In one league Division 1 has no such channel; in another it already has channel 111. A
    manager who meant a fresh assignment and is told it was *updated* has moved an existing one.
    The first is told the channel was set, the second that it was updated, word for word."""
    first_db = await _make_db(tmp_path, name="first")
    moved_db = await _make_db(tmp_path, name="moved")
    await _seed(moved_db, which, 111)
    first_cog, moved_cog = await _make_cog(first_db), await _make_cog(moved_db)
    first, moved = _interaction(first_cog, which), _interaction(moved_cog, which)

    await _run(first_cog, first, which)
    await _run(moved_cog, moved, which)

    assert last_said(first) == _reply(which, "set")
    assert last_said(moved) == _reply(which, "updated")


@pytest.mark.parametrize("which", BOTH)
async def test_the_channel_replaced_is_audited_as_an_integer(tmp_path, which):
    """Division 1 already has channel 111, held as text as the table holds it (#212). The audit
    entry records 111 replaced and 780001 set, each as an integer, which is what makes the audit
    answer "where were the notices going before?"."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, which, 111)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, which), which)

    row = (await _audit(db_path))[0]
    assert json.loads(row["old_value"])["channel_id"] == 111
    assert json.loads(row["new_value"])["channel_id"] == CHANNEL_ID


@pytest.mark.parametrize("which", BOTH)
async def test_the_log_line_names_the_command_a_league_now_types(tmp_path, which):
    """A log line names the member, the command and its outcome, with the division and the
    channel beneath it. It is the log channel's only line, word for word."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, which), which)

    assert _lines(cog) == [_success_line(which)]


# ---------------------------------------------------------------------------
# Every refusal is recorded in the log channel (#482)
# ---------------------------------------------------------------------------

REFUSAL_CASES = [
    ("module-disabled", _OFF),
    ("cannot-post", _NO_ACCESS),
    ("no-guild", _NO_ACCESS),
    ("no-season", _NO_SEASON),
    ("unknown-division", _not_found("Division 9")),
    ("in-use", _clash()),
]


@pytest.mark.parametrize("case,reply", REFUSAL_CASES, ids=[c[0] for c in REFUSAL_CASES])
@pytest.mark.parametrize("which", BOTH)
async def test_every_refusal_of_a_channel_command_is_recorded(tmp_path, which, case, reply):
    """A7: the manager runs `/attendance channel rsvp` (or `attendance`) for Division 1 and
    #notices and is turned away. Each refusal is answered once, in its words, asks no change of
    the queue, writes and audits nothing, and records one "⛔ … refused for Manager … — …" line
    naming the command and the reason.

    - **module-disabled**: attendance is off. A channel for a module that is not running
      configures something no code reads. The words are the command's own, not the attendance
      cog's gate, which is worded differently: the command keeps the words it had before it
      moved, and refuses before anything is deferred.
    - **cannot-post**: the bot may not post in #notices, checked before the write rather than
      discovered at the first notice, when the round it was meant to announce has started.
    - **no-guild**: the interaction carries no server. `permissions_for(guild.me)` needs one, so
      the absence is answered rather than raising an `AttributeError` at a manager.
    - **no-season**: the only season, Season 1, is completed, so there is no division to hang the
      setting on.
    - **unknown-division**: Season 1 has Division 1 alone and the manager names Division 9,
      refused by the name typed.
    - **in-use**: #notices is already Division 2's results channel. A channel serves one purpose
      across the whole server (decided 2026-09-06), checked before the write."""
    db_path = await _make_db(
        tmp_path, attendance=case != "module-disabled",
        status="COMPLETED" if case == "no-season" else "SETUP",
    )
    if case == "in-use":
        await _claim(db_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)
    if case == "no-guild":
        interaction.guild = None

    await _run(
        cog, interaction, which,
        name="Division 9" if case == "unknown-division" else "Division 1",
        channel=_channel(cog, may_post=case != "cannot-post"),
    )

    assert said(interaction) == [reply]
    if case == "module-disabled":
        interaction.response.defer.assert_not_awaited()
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _channels(db_path) == _none_set()
    assert _lines(cog) == [_refusal_line(which, reply)]


# ---------------------------------------------------------------------------
# Through the change queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", BOTH)
async def test_the_manager_is_acknowledged_before_anything_is_written(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    (id 77) runs `/attendance channel rsvp` (or `attendance`) for channel 780001. They are
    answered at once with the queue's acknowledgement, naming the change's first job, and
    nothing is deferred; nothing is written until the queue runs the change: Division 1 still
    has neither channel and no audit entry stands."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)

    first_job = (await step_rows(db_path))[0]["id"]
    assert acknowledgement(interaction) == _acknowledged(which, first_job)
    interaction.response.defer.assert_not_awaited()
    assert await _channels(db_path) == _none_set()
    assert await _audit(db_path) == []


@pytest.mark.parametrize("which", BOTH)
async def test_a_fault_in_the_save_saves_neither_the_channel_nor_its_audit(
    tmp_path, monkeypatch, which
):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    runs `/attendance channel rsvp` (or `attendance`) for channel 780001, and the audit entry's
    write fails on the database as the queue runs the change. The channel is not saved either:
    Division 1 still has neither channel and no audit entry stands. The manager is told in
    `setting_change`'s words that nothing was saved and to run it again; the log channel gets
    the one failure line and no success line; the queue is not stopped."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services import change_queue

    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )
    interaction = _interaction(cog, which)

    await _run(cog, interaction, which)

    assert await _channels(db_path) == _none_set()
    assert await _audit(db_path) == []
    assert last_said(interaction) == (
        f"❌ `/attendance channel {which}` stopped on a fault in the bot, not on anything you "
        f"entered, and did not finish. Nothing was saved. Run `/attendance channel {which}` "
        f"again to retry. The fault is recorded in the log channel."
    )
    assert _lines(cog) == [
        f"❌ `/attendance channel {which}` failed for {NAMED} — OperationalError. The details "
        f"are in the host's log."
    ]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("which", BOTH)
async def test_two_changes_waiting_for_one_division_each_record_the_channel_they_replace(
    tmp_path, which
):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    asks `/attendance channel rsvp` (or `attendance`) for channel 780001, then, before the queue
    runs, for channel 780002, and is acknowledged for each. Each change reads the channel it
    replaces as it saves: the first is told its channel was set and records none replaced, the
    second is told its channel was updated and records 780001 replaced, and Division 1 is left
    with 780002."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, second = _interaction(cog, which), _interaction(cog, which)

    await _ask(cog, first, which)
    await _ask(cog, second, which, channel=_channel(cog, OTHER_CHANNEL_ID, "second"))
    assert acknowledgement(first).startswith("⏳ ")
    assert acknowledgement(second).startswith("⏳ ")
    await run_queue(cog.bot)

    assert last_said(first) == _reply(which, "set")
    assert last_said(second) == _reply(which, "updated", channel_id=OTHER_CHANNEL_ID)
    replaced = [
        (json.loads(row["old_value"])["channel_id"], json.loads(row["new_value"])["channel_id"])
        for row in await _audit(db_path)
    ]
    assert replaced == [(None, CHANNEL_ID), (CHANNEL_ID, OTHER_CHANNEL_ID)]
    assert (await _channels(db_path))[which] == OTHER_CHANNEL_ID


@pytest.mark.parametrize("which", BOTH)
async def test_the_same_change_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    (id 77) asks `/attendance channel rsvp` (or `attendance`) for channel 780001, and is
    acknowledged; before the queue runs, with nothing else asked of the league since, they ask
    for it again. The second is refused in the queue's words, as a change already asked for and
    not yet started, and the log channel takes that refusal as a line; only the one change is
    queued. The queue runs it: Division 1 has 780001, one audit entry stands, and the first is
    told its channel was set."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, repeat = _interaction(cog, which), _interaction(cog, which)
    repeated = (
        f"⚠️ Setting the {COMMANDS[which][5]} channel for **Division 1** to <#{CHANNEL_ID}> has "
        f"already been asked for and has not started yet, so it was not asked for again."
    )

    await _ask(cog, first, which)
    await _ask(cog, repeat, which)

    assert acknowledgement(first).startswith("⏳ ")
    assert last_said(repeat) == repeated
    assert len(await change_rows(db_path)) == 1
    await run_queue(cog.bot)

    assert (await _channels(db_path))[which] == CHANNEL_ID
    assert len(await _audit(db_path)) == 1
    assert last_said(first) == _reply(which, "set")
    assert _lines(cog) == [_refusal_line(which, repeated), _success_line(which)]


@pytest.mark.parametrize("which", BOTH)
async def test_the_channel_is_set_while_the_queue_is_stopped_on_another_job(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The league's
    queue is stopped on another job, which failed on a fault in the bot. The manager (id 77) runs
    `/attendance channel rsvp` (or `attendance`) for channel 780001, the remedy a stop notice
    names for a check-in call or a sheet with no channel. They are acknowledged at once and told
    the change runs at once, past the stop, and that the stopped job stays stopped; nothing is
    written as they ask. Once the queue runs, Division 1 has 780001, the manager is told the
    channel was set, and the other job is still stopped where it was."""
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

    async def fails(_ctx) -> StepResult:
        raise RuntimeError("a fault in the bot")

    async def go(_ctx):
        return Verdict.go()

    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    register(cog.bot, ChangeType(
        kind="test.stopping",
        opening=(PlannedStep("fail"),),
        steps={"fail": Step("fail", StepKind.ACT, fails)},
        check=go,
        key=lambda _payload: "test.stopping",
        doing=lambda _payload: "Doing another job",
        outcome=lambda _ctx: "Done.",
    ))
    other = await cog.bot.change_queue.ask("test.stopping", {}, actor=member(), what="another job")
    await run_queue(cog.bot)
    stopped = await stopped_job(db_path)
    assert stopped is not None and stopped["change_id"] == other
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)

    first_job = min(
        row["id"] for row in await step_rows(db_path) if row["change_id"] != other
    )
    assert acknowledgement(interaction) == (
        f"{_acknowledged(which, first_job)} The queue is stopped at job #{stopped['id']}, so "
        f"this request runs at once, past the stop; that job stays stopped."
    )
    assert await _channels(db_path) == _none_set()
    await run_queue(cog.bot)

    assert (await _channels(db_path))[which] == CHANNEL_ID
    assert last_said(interaction) == _reply(which, "set")
    still = await stopped_job(db_path)
    assert still is not None
    assert (still["id"], still["change_id"]) == (stopped["id"], other)


# ---------------------------------------------------------------------------
# What changes while the change waits
# ---------------------------------------------------------------------------


async def _switch_attendance_off(cog, db_path: str) -> None:
    await set_module_flag(db_path, "attendance", False)


async def _delete_the_division(cog, db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM divisions WHERE id = ?", (DIVISION_ID,))
        await db.commit()


async def _end_the_season(cog, db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (SEASON_ID,))
        await db.commit()


async def _claim_the_channel(cog, db_path: str) -> None:
    await _claim(db_path)


async def _lose_access(cog, db_path: str) -> None:
    cog.bot.channels[CHANNEL_ID].permissions_for.return_value = SimpleNamespace(
        send_messages=False
    )


async def _delete_the_channel(cog, db_path: str) -> None:
    cog.bot.channels[CHANNEL_ID] = None


def _meanwhile_cases():
    """Each command, with each thing that may change while its change waits and the refusal it
    then meets, in the words the command gives at the ask."""
    cases = []
    for which in BOTH:
        cases += [
            pytest.param(which, _switch_attendance_off, _OFF,
                         id=f"{which}-attendance_switched_off"),
            pytest.param(which, _delete_the_division, _not_found(),
                         id=f"{which}-division_deleted"),
            pytest.param(which, _end_the_season, _NO_SEASON, id=f"{which}-season_ended"),
            pytest.param(which, _claim_the_channel, _clash(), id=f"{which}-channel_claimed"),
            pytest.param(which, _lose_access, _NO_ACCESS, id=f"{which}-access_lost"),
            pytest.param(which, _delete_the_channel, _NO_ACCESS, id=f"{which}-channel_deleted"),
        ]
    return cases


@pytest.mark.parametrize("which,meanwhile,refused", _meanwhile_cases())
async def test_a_change_is_refused_as_it_starts_where_its_ask_no_longer_holds(
    tmp_path, which, meanwhile, refused
):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    (id 77) asks `/attendance channel rsvp` (or `attendance`) for channel 780001, and is
    acknowledged; before the change runs, as a change waiting ahead of it on the league's lane
    would, attendance is switched off, or Division 1 is deleted, or Season 1 is completed, or
    780001 is made Division 2's results channel, or the bot loses its permission to post in
    780001, or the channel is deleted. The change judges again as it starts and is refused:
    Division 1's channel is not written, nothing is audited, the manager's reply becomes the
    refusal the command gives at the ask, word for word, the log channel takes that refusal as
    its only line, and the queue is not stopped."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)
    assert acknowledgement(interaction).startswith("⏳ ")
    await meanwhile(cog, db_path)
    await run_queue(cog.bot)

    assert last_said(interaction) == refused
    assert (await _channels(db_path))[which] is None
    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(which, refused)]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("which", BOTH)
async def test_a_division_renamed_while_its_change_waits_still_gets_its_channel(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has neither attendance channel. The manager
    asks `/attendance channel rsvp` (or `attendance`) for channel 780001 for Division 1, and is
    acknowledged; before the change runs, Division 1 is renamed Division One. The change is for
    the division asked for, whatever it is called by then: Division One (id 11) is given 780001
    and audited, and the manager's reply names the division as they typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)
    assert acknowledgement(interaction).startswith("⏳ ")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET name = 'Division One' WHERE id = ?", (DIVISION_ID,)
        )
        await db.commit()
    await run_queue(cog.bot)

    assert (await _channels(db_path))[which] == CHANNEL_ID
    assert [row["division_id"] for row in await _audit(db_path)] == [DIVISION_ID]
    assert last_said(interaction) == _reply(which, "set")
