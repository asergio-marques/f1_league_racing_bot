"""Core's own two division channel commands, `/division lineup-channel` and `calendar-channel`.

Issue #208. A division's other channels are its modules' and are set under each module's own
group, covered in that module's tests: `/weather channel` in `tests/weather/`, the three
`/results channel` commands in `tests/results/`, and the two `/attendance channel` commands in
`tests/attendance/`. These two are every season's whatever modules are on, so they stay core's.

**What both hold to** is what this file pins first: the season and the division must both exist,
the channel-does-one-job guard runs before any write, and the assignment is audited and logged.
Those are parametrised across both precisely so a divergence in one of them fails rather than
passes quietly. Neither is gated on a module, and neither defers: the command's first answer is
its response.

**Each is a core `Setting` on the change queue** (#283), as `/bot hub-channel` is
(`test_hub_channel_command.py`): the command answers with the queue's acknowledgement, and the
queue writes the division's channel and its audit entry in one save, stamped with the queue's
clock, then updates the acknowledgement to today's reply and writes today's line. The season and
the division are judged by the command, which must find the division to ask for its change; the
channel's use, and the value already held, are judged by the setting's check at the ask, in
today's words. The check is made again as the change starts, so a division deleted, a season
ended, or the channel claimed by something else while the change waits is refused then, in the
same words; a division renamed meanwhile is still the division asked for, and gets its channel.
The tests run the bot the entry point builds, with the real queue and "now" pinned, and run the
queue.

**Where they once differed by accident** (issue #212, which #208 pinned rather than fixed). They
wrote `old_value = ''` into the audit entry, so a reassignment could not be traced back. Both now
record the channel they replaced, as an integer, which
`test_every_channel_command_here_audits_its_ids_as_integers` pins.

**"Set" and "updated" are different words for a reason:** a manager who meant to assign a fresh
channel and is told it was *updated* has just moved an existing one, and that is worth noticing
before the next round posts somewhere unexpected. The word is chosen from the channel the save
replaces, so a change waiting behind another for the same division reads what that one left.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    PAST_THE_STOP,
    acknowledgement,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    step_rows,
    stop_the_league_lane,
    stopped_job,
)
from tests.support.settings_league import interaction as member_asks
from tests.support.settings_league import last_said, unruled
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

#: command attribute and audit change_type.
COMMANDS = {
    "lineup": ("division_lineup_channel", "SIGNUP_LINEUP_CHANNEL_SET"),
    "calendar": ("division_calendar_channel", "DIVISION_CALENDAR_CHANNEL_SET"),
}
ALL = sorted(COMMANDS)

#: The command's full name, as the log channel reads it.
COMMAND_NAMES = {"lineup": "division lineup-channel", "calendar": "division calendar-channel"}

#: The words the reply and the acknowledgement give each setting.
LABELS = {"lineup": "Lineup channel", "calendar": "Calendar channel"}

#: Why a test fails until the two commands are core `Setting`s asked of the change queue.


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, name: str = "module_channels", *, season: bool = True) -> str:
    """The league's server set up, and, with *season*, Season 1 being set up with its Division 1
    (id 11) and no channel set."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    if season:
        async with get_connection(db_path) as db:
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


async def _make_cog(db_path: str) -> SeasonCog:
    """The cog on the league's bot (`league_bot`), with the league's log channel and its real
    change queue, "now" pinned to `NOW`."""
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _channel(channel_id: int = CHANNEL_ID, name: str = "notices"):
    """A text channel as Discord gives one, its mention the one Discord renders."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"<#{channel_id}>"
    return channel


def _interaction(cog, which: str = "lineup"):
    """The manager's (77) interaction for the command, recording every word they are told."""
    return member_asks(
        cog.bot, COMMAND_NAMES[which], user=member(ACTOR_ID, "Manager", "Manager#0001")
    )


def _replied(interaction) -> str:
    """What the manager's reply reads now."""
    return last_said(interaction)


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


async def _ask(cog, which, interaction, *, name="Division 1", channel=None):
    """The manager runs the command, and nothing more: the queue is not run."""
    attr = COMMANDS[which][0]
    body = undecorate(getattr(SeasonCog, attr))
    return await body(cog, interaction, name, channel or _channel())


async def _run(cog, which, interaction, *, name="Division 1", channel=None):
    """The manager runs the command, and the queue is run."""
    await _ask(cog, which, interaction, name=name, channel=channel)
    await run_queue(cog.bot)


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id, timestamp "
            "FROM audit_entries ORDER BY id",
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


async def _seed_other_division_lineup(db_path: str, channel_id: int) -> None:
    """Give Division 2 (id 12), of the same season, *channel_id* as its lineup channel."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 2', 2, 556)",
            (OTHER_DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = ? WHERE id = ?",
            (channel_id, OTHER_DIVISION_ID),
        )
        await db.commit()


async def _seed_other_division_results(db_path: str, channel_id: int) -> None:
    """Give Division 2 (id 12), of the same season, *channel_id* as its results channel, written
    where `/results channel results` writes it."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 2', 2, 556)",
            (OTHER_DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) VALUES (?, ?)",
            (OTHER_DIVISION_ID, channel_id),
        )
        await db.commit()


async def _channel_column(db_path: str, column: str):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {column} FROM divisions WHERE id = ?", (DIVISION_ID,)
        )
        row = await cursor.fetchone()
        return None if row is None else row[column]


#: The refusal a channel already Division 2's lineup channel is given, whichever command asks.
_IN_USE = (
    f"❌ <#{CHANNEL_ID}> is already the lineup channel for **Division 2**. A channel does one "
    f"job — pick one that is not in use, or clear the other setting first."
)
_NO_SEASON = (
    "❌ No season is live. A division's channels belong to the season being built or raced — "
    "start one with `/season setup`."
)


# ---------------------------------------------------------------------------
# What both hold to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ALL)
async def test_the_assignment_is_audited(tmp_path, which):
    """A channel change nobody recorded cannot be explained afterwards, and these are exactly
    the settings a league argues about when a post lands in the wrong place. The entry is
    stamped with the queue's clock, never the host's."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [COMMANDS[which][1]]
    assert rows[0]["division_id"] == DIVISION_ID
    assert rows[0]["actor_id"] == ACTOR_ID
    assert rows[0]["timestamp"] == NOW.isoformat()


@pytest.mark.parametrize("which", ALL)
async def test_the_new_channel_is_recorded_in_the_audit(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    new_value = json.loads((await _audit_rows(db_path))[0]["new_value"])
    assert str(new_value["channel_id"]) == str(CHANNEL_ID)


@pytest.mark.parametrize("which", ALL)
async def test_the_division_is_matched_regardless_of_case(tmp_path, which):
    """A manager typing a division name into a slash command is not copying it letter for
    letter, and refusing `division 1` for `Division 1` would be a puzzle."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, which, interaction, name="dIvIsIoN 1")

    assert "not found" not in _replied(interaction)
    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", ALL)
async def test_the_assignment_is_logged(tmp_path, which):
    """The log is where a league sees a manager's actions; a channel move that reaches only
    the audit table is invisible to everyone but a maintainer with a SQL prompt. The line is
    the log channel's only one, word for word as before the queue."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    assert _lines(cog) == [
        f"{NAMED} | /{COMMAND_NAMES[which]} | Success\n"
        f"  division: Division 1\n"
        f"  channel: #notices"
    ]


@pytest.mark.parametrize("which", ALL)
async def test_the_manager_is_told_which_channel_was_assigned(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, which, interaction)

    assert _replied(interaction) == (
        f"✅ {LABELS[which]} for **Division 1** set to <#{CHANNEL_ID}>."
    )


@pytest.mark.parametrize("which", ALL)
async def test_the_manager_is_acknowledged_before_anything_is_written(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has no channel. The manager (id 77) runs the
    command for #notices. They are answered at once with the queue's acknowledgement, naming the
    change's first job, and nothing is written until the queue runs the change: the division
    still has no channel and no audit entry stands."""
    from tests.support.change_queue import step_rows

    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, which, interaction)

    first_job = (await step_rows(db_path))[0]["id"]
    assert acknowledgement(interaction) == (
        f"⏳ Setting the {LABELS[which].lower()} for **Division 1** to <#{CHANNEL_ID}>. This "
        f"message will be updated when it is done; if it takes longer, the log channel will say "
        f"so. It begins with job #{first_job}."
    )
    assert await _channel_column(db_path, CORE_COLUMNS[which]) is None
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize("which", ALL)
async def test_a_fault_in_the_save_saves_neither_the_channel_nor_its_audit(
    tmp_path, which, monkeypatch
):
    """Season 1 is being set up, and its Division 1 has no channel. The manager runs the command
    for #notices, and the audit entry's write fails on the database as the queue runs the change.
    The channel is not saved either: Division 1 still has no channel and no audit entry stands.
    The manager is told in `setting_change`'s words that nothing was saved and to run it again;
    the log channel gets the one failure line and no success line; the queue is not stopped."""
    from leaguebot.core.services import change_queue

    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )
    interaction = _interaction(cog, which)
    command = f"/{COMMAND_NAMES[which]}"

    await _run(cog, which, interaction)

    assert await _channel_column(db_path, CORE_COLUMNS[which]) is None
    assert await _audit_rows(db_path) == []
    assert _replied(interaction) == (
        f"❌ `{command}` stopped on a fault in the bot, not on anything you entered, and did not "
        f"finish. Nothing was saved. Run `{command}` again to retry. The fault is recorded in the "
        f"log channel."
    )
    assert _lines(cog) == [
        f"❌ `{command}` failed for {NAMED} — OperationalError. The details are in the host's log."
    ]
    assert await stopped_job(db_path) is None


@pytest.mark.xfail(strict=True, reason="#283: a division's lineup and calendar channels wait behind a stopped job")
@pytest.mark.parametrize("which", ALL)
async def test_the_channel_is_set_while_the_queue_is_stopped_on_another_job(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has no channel. The league's queue is stopped
    on another job, which failed on a fault in the bot. The manager (id 77) runs
    `/division lineup-channel`, or `/division calendar-channel`, for #notices (780001), the remedy
    a stop notice names for a division with no lineup channel. They are acknowledged at once and
    told the change runs at once, past the stop, and that the stopped job stays stopped; nothing
    is written as they ask. Once the queue runs, Division 1 has 780001, the manager is told the
    channel was set, and the other job is still stopped where it was."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    stopped = await stop_the_league_lane(cog.bot, db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, which, interaction)

    first_job = min(
        row["id"] for row in await step_rows(db_path) if row["change_id"] != stopped["change_id"]
    )
    assert acknowledgement(interaction) == (
        f"⏳ Setting the {LABELS[which].lower()} for **Division 1** to <#{CHANNEL_ID}>. This "
        f"message will be updated when it is done; if it takes longer, the log channel will say "
        f"so. It begins with job #{first_job}." + PAST_THE_STOP.format(job=stopped["id"])
    )
    assert await _channel_column(db_path, CORE_COLUMNS[which]) is None
    await run_queue(cog.bot)

    assert await _channel_column(db_path, CORE_COLUMNS[which]) == CHANNEL_ID
    assert _replied(interaction) == f"✅ {LABELS[which]} for **Division 1** set to <#{CHANNEL_ID}>."
    still = await stopped_job(db_path)
    assert still is not None
    assert (still["id"], still["change_id"]) == (stopped["id"], stopped["change_id"])


# ---------------------------------------------------------------------------
# What changes while the change waits
# ---------------------------------------------------------------------------


async def _delete_the_division(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM divisions WHERE id = ?", (DIVISION_ID,))
        await db.commit()


async def _end_the_season(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (SEASON_ID,))
        await db.commit()


async def _claim_the_channel(db_path: str) -> None:
    await _seed_other_division_lineup(db_path, CHANNEL_ID)


@pytest.mark.parametrize("which", ALL)
@pytest.mark.parametrize(
    "case,meanwhile,refused",
    [
        pytest.param(
            "division_deleted", _delete_the_division,
            "❌ Division **Division 1** not found in the current season.",
            id="division_deleted",
        ),
        pytest.param("season_ended", _end_the_season, _NO_SEASON, id="season_ended"),
        pytest.param("channel_claimed", _claim_the_channel, _IN_USE, id="channel_claimed"),
    ],
)
async def test_a_change_is_refused_as_it_starts_where_its_ask_no_longer_holds(
    tmp_path, which, case, meanwhile, refused
):
    """Season 1 is being set up, and its Division 1 has no channel. The manager (id 77) asks for
    #notices, and is acknowledged; before the change runs, as a change waiting ahead of it on the
    league's lane would, Division 1 is deleted, or Season 1 is completed, or #notices is made
    Division 2's lineup channel. The change judges again as it starts and is refused: Division 1's
    channel is not written, nothing is audited, the manager's reply becomes the refusal the
    command gives at the ask, word for word, and the log channel takes that refusal as its only
    line."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, which, interaction)
    assert acknowledgement(interaction).startswith("⏳ ")
    await meanwhile(db_path)
    await run_queue(cog.bot)

    assert _replied(interaction) == refused
    assert await _channel_column(db_path, CORE_COLUMNS[which]) is None
    assert await _audit_rows(db_path) == []
    reason = refused.split(" ", 1)[1]
    assert _lines(cog) == [f"⛔ `/{COMMAND_NAMES[which]}` refused for {NAMED} — {reason}"]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("which", ALL)
async def test_a_division_renamed_while_its_change_waits_still_gets_its_channel(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has no channel. The manager asks for #notices
    for Division 1; before the change runs, Division 1 is renamed Division One. The change is
    for the division asked for, whatever it is called by then: Division One (id 11) is given
    #notices and audited, and the manager's reply names the division as they typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, which, interaction)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET name = 'Division One' WHERE id = ?", (DIVISION_ID,)
        )
        await db.commit()
    await run_queue(cog.bot)

    assert await _channel_column(db_path, CORE_COLUMNS[which]) == CHANNEL_ID
    assert [row["division_id"] for row in await _audit_rows(db_path)] == [DIVISION_ID]
    assert _replied(interaction) == (
        f"✅ {LABELS[which]} for **Division 1** set to <#{CHANNEL_ID}>."
    )


@pytest.mark.parametrize("which", ALL)
async def test_two_changes_waiting_for_one_division_each_record_the_channel_they_replace(
    tmp_path, which
):
    """Season 1 is being set up, and its Division 1 has no channel. The manager asks for
    #notices, then, before the queue runs, for #schedule. Each change reads the channel it
    replaces as it saves: the first is told its channel was set and records none replaced, the
    second is told its channel was updated and records #notices replaced, and Division 1 is left
    with #schedule."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, second = _interaction(cog, which), _interaction(cog, which)

    await _ask(cog, which, first)
    await _ask(cog, which, second, channel=_channel(OTHER_CHANNEL_ID, "schedule"))
    await run_queue(cog.bot)

    assert _replied(first) == f"✅ {LABELS[which]} for **Division 1** set to <#{CHANNEL_ID}>."
    assert _replied(second) == (
        f"✅ {LABELS[which]} for **Division 1** updated to <#{OTHER_CHANNEL_ID}>."
    )
    assert [
        (json.loads(row["old_value"]), json.loads(row["new_value"]))
        for row in await _audit_rows(db_path)
    ] == [
        ({"channel_id": None}, {"channel_id": CHANNEL_ID}),
        ({"channel_id": CHANNEL_ID}, {"channel_id": OTHER_CHANNEL_ID}),
    ]
    assert await _channel_column(db_path, CORE_COLUMNS[which]) == OTHER_CHANNEL_ID


# ---------------------------------------------------------------------------
# Every season's, whatever modules are on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ALL)
async def test_a_core_channel_needs_no_module(tmp_path, which):
    """Lineups and the calendar are core settings — gating them on attendance would refuse
    a league that never enabled the module. Every module is off here."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    assert len(await _audit_rows(db_path)) == 1


@pytest.mark.parametrize("which", ALL)
async def test_the_core_pair_reply_directly(tmp_path, which):
    """Neither defers: its first answer is the response itself."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, which, interaction)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited()


@pytest.mark.parametrize(
    "which,column",
    [("lineup", "lineup_channel_id"), ("calendar", "calendar_channel_id")],
)
async def test_the_core_pair_write_their_own_column(tmp_path, which, column):
    """Nothing but a test stands between a mistyped column name and a setting that silently
    never takes."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    assert await _channel_column(db_path, column) == CHANNEL_ID


# ---------------------------------------------------------------------------
# Where the two have drifted — pinned as they stand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ALL)
async def test_a_first_assignment_and_a_move_are_worded_differently(tmp_path, which):
    """A manager who meant to assign a fresh channel and is told it was *updated* has just
    moved an existing one — worth noticing before the next round posts somewhere else. The
    core pair always said "set" until issue #212."""
    first_db = await _make_db(tmp_path, name=f"first_{which}")
    moved_db = await _make_db(tmp_path, name=f"moved_{which}")
    await _seed_core_channel(moved_db, which, 111)
    first_cog = await _make_cog(first_db)
    moved_cog = await _make_cog(moved_db)
    first = _interaction(first_cog, which)
    moved = _interaction(moved_cog, which)

    await _run(first_cog, which, first)
    await _run(moved_cog, which, moved)

    assert "set to" in _replied(first)
    assert "updated to" in _replied(moved)


@pytest.mark.parametrize("which", ALL)
async def test_a_first_core_assignment_records_no_previous_channel(tmp_path, which):
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    assert json.loads((await _audit_rows(db_path))[0]["old_value"]) == {"channel_id": None}


@pytest.mark.parametrize("which", ALL)
async def test_every_channel_command_here_audits_its_ids_as_integers(tmp_path, which):
    """Every channel command records the channel it replaced, and writes its ids as integers, so
    a reader querying the audit for a channel id need not know which command wrote the row. They
    once wrote an empty `old_value`, so a lineup or calendar channel that moved could not be
    traced back — against the rule that every change is recorded from what to what (issue
    #212)."""
    db_path = await _make_db(tmp_path, name=f"types_{which}")
    await _seed_core_channel(db_path, which, 111)
    cog = await _make_cog(db_path)

    await _run(cog, which, _interaction(cog, which))

    row = (await _audit_rows(db_path))[0]
    assert json.loads(row["old_value"])["channel_id"] == 111
    assert json.loads(row["new_value"])["channel_id"] == CHANNEL_ID


# ---------------------------------------------------------------------------
# Every refusal is recorded (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. Each reply stays word for word as today, the channel guard's
# two answers included: a channel doing another job, and a channel already doing this one.


@pytest.mark.parametrize("which", ALL)
@pytest.mark.parametrize(
    "case",
    [
        "no_season",
        "an_unknown_division",
        "a_channel_doing_another_job",
        "another_divisions_results_channel",
        "the_channel_already_set",
    ],
)
async def test_every_channel_command_refusal_is_recorded(tmp_path, which, case):
    """Division 1 of a season being set up, and a text channel #notices. The manager (id 77) runs
    /division lineup-channel or calendar-channel and is refused at the ask in five cases: with no
    season live, there being no division to hang the setting on; naming Division 9, which does not
    exist; giving #notices while it is already the lineup channel of Division 2, a channel serving
    one purpose across the whole server; giving #notices while it is already the results channel
    of Division 2, a setting of another kind and another module; and giving #notices while it is
    already this very channel of Division 1. The manager gets today's reply word for word and
    nothing else, the setting is not written and nothing is audited, and the log channel gets
    exactly one line, "⛔ `/division …-channel` refused for Manager (`<@77>`) — " and the reply's
    first line, without its mark."""
    db_path = await _make_db(tmp_path, season=case != "no_season")
    name = "Division 9" if case == "an_unknown_division" else "Division 1"
    if case == "a_channel_doing_another_job":
        await _seed_other_division_lineup(db_path, CHANNEL_ID)
    if case == "another_divisions_results_channel":
        await _seed_other_division_results(db_path, CHANNEL_ID)
    if case == "the_channel_already_set":
        await _seed_core_channel(db_path, which, CHANNEL_ID)
    reply = {
        "no_season": _NO_SEASON,
        "an_unknown_division": "❌ Division **Division 9** not found in the current season.",
        "a_channel_doing_another_job": _IN_USE,
        "another_divisions_results_channel": f"❌ <#{CHANNEL_ID}> is already the results channel "
        "for **Division 2**. A channel does one job — pick one that is not in use, or clear the "
        "other setting first.",
        "the_channel_already_set": f"ℹ️ <#{CHANNEL_ID}> is already the {which} channel for "
        "**Division 1**. Nothing was changed.",
    }[case]
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _run(cog, which, interaction, name=name)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    assert await _audit_rows(db_path) == []
    expected_column = CHANNEL_ID if case == "the_channel_already_set" else None
    assert await _channel_column(db_path, CORE_COLUMNS[which]) == expected_column
    reason = reply.split(" ", 1)[1]
    assert _lines(cog) == [
        f"⛔ `/{COMMAND_NAMES[which]}` refused for {NAMED} — {reason}"
    ]
