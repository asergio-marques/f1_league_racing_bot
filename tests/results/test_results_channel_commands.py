"""`/results channel …`: setting the channels a division's results are posted to.

Results' own commands, under results' own group. They sat under core's `/division` until #462
moved them, and only their names changed. `/results channel results` and `/results channel
standings` refuse alike; `/results channel verdicts` keeps words of its own, and refuses a channel
the bot cannot post in before it reads anything else.

**A channel serves one purpose across the whole server** (decided 2026-09-06). Two settings
sharing one channel interleave two kinds of posting, and several posting paths edit or delete
their last message by an id stored against the channel — so sharing is how one output comes to
delete another's. The guard runs **before** the write, so a refusal leaves the configuration
exactly as it stood.

**Re-running a command with the value it already holds is refused in its own words.** It is not
a collision with something else, and reporting it as one would send a manager looking for a
conflict that does not exist. `test_re_setting_the_same_channel_is_refused_as_unchanged` and
`test_a_channel_doing_another_job_is_refused_as_a_clash` sit either side of that distinction —
it is the whole reason `same_setting` is threaded through to the refusal text, and a reader
collapsing the two messages would lose it.

**The same-value case used to be written and only then reported as unchanged.** The guard
running first is what fixed that, and `test_a_refused_assignment_writes_nothing` holds it
against a later reader who moves the check after the upsert for tidiness. The refusal's own
rule is pinned in `test_channel_registry.py`.

**Each is a results `Setting` on the change queue** (#283), as `/weather channel` is
(`tests/weather/test_weather_channel_command.py`): the command answers with the queue's
acknowledgement, and the queue writes the division's channel and its audit entry in one save,
stamped with the queue's clock, then updates the acknowledgement to the reply and writes the
line. The module, the season, the division and (verdicts) the bot's access to the channel are
judged by the command, which must find the division to ask for its change; the channel's use, and
the value already held, by the setting's check at the ask, in the same words. The check is made
again as the change starts, so results switched off, the division deleted, the season ended, the
channel claimed or (verdicts) the bot's access to it lost while the change waits is refused then,
in the ask's words; a division renamed meanwhile is still the division asked for, and gets its
channel. The tests run the bot the entry point builds, with the real queue and "now" pinned, and
run the queue.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.cogs.results_cog import ResultsCog
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
from tests.support.settings_league import last_said, unruled
from tests.support.undecorate import undecorate

SEASON_ID = 1
DIVISION_ID = 11
OTHER_DIVISION_ID = 12
CHANNEL_ID = 770001
OTHER_CHANNEL_ID = 770002
ACTOR_ID = 77
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The manager running the command, as a log line names them, the mention wrapped so it
#: notifies nobody.
NAMED = f"Manager (`<@{ACTOR_ID}>`)"


#: Each command: its method on the cog, the kind of change it asks of the queue, the column of
#: `division_results_config` it sets, and the label its reply opens with.
COMMANDS = {
    "results": ("channel_results", "results.setting.results_channel", "results_channel_id", "Results"),
    "standings": ("channel_standings", "results.setting.standings_channel", "standings_channel_id", "Standings"),
    "verdicts": ("channel_verdicts", "results.setting.verdicts_channel", "penalty_channel_id", "Verdicts"),
}

_OFF = "❌ The Results & Standings module is not enabled."
_NO_SEASON = (
    "❌ No season is live. A division's channels belong to the season being built or raced — "
    "start one with `/season setup`."
)
_NO_ACCESS = "❌ Cannot access that channel. Ensure the bot has permission to post there."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _not_found(which: str, name: str = "Division 1") -> str:
    """The command's own refusal of a division the live season does not hold."""
    if which == "verdicts":
        return f'❌ Division "{name}" not found.'
    return f"❌ Division **{name}** not found in the current season."


def _clashing(which: str) -> str:
    """The setting of Division 2 that holds the channel in a clash: its standings channel, or its
    results channel where the command is standings'."""
    return "results" if which == "standings" else "standings"


def _clash(which: str, channel_id: int = CHANNEL_ID) -> str:
    """The refusal of a channel already doing Division 2's `_clashing` job."""
    return (
        f"❌ <#{channel_id}> is already the {_clashing(which)} channel for **Division 2**. A "
        f"channel does one job — pick one that is not in use, or clear the other setting first."
    )


def _already(which: str, channel_id: int = CHANNEL_ID) -> str:
    """The refusal of the channel Division 1 already has for this very setting."""
    return (
        f"ℹ️ <#{channel_id}> is already the {which} channel for **Division 1**. Nothing was "
        f"changed."
    )


def _reply(which: str, verb: str, *, name: str = "Division 1", channel_id: int = CHANNEL_ID,
           channel_name: str | None = None) -> str:
    """The reply once the channel is saved, *verb* "set" or "updated". Verdicts' keeps its own
    words: the division unbolded and the channel by its name."""
    if which == "verdicts":
        return f"✅ Verdicts channel for {name} {verb} to #{channel_name or which}."
    return f"✅ {COMMANDS[which][3]} channel for **{name}** {verb} to <#{channel_id}>."


def _acknowledged(which: str, first_job: int) -> str:
    """The queue's acknowledgement of the command for Division 1 and channel 770001."""
    return (
        f"⏳ Setting the {which} channel for **Division 1** to <#{CHANNEL_ID}>. This message will "
        f"be updated when it is done; if it takes longer, the log channel will say so. It begins "
        f"with job #{first_job}."
    )


def _success_line(which: str, *, name: str = "Division 1", channel_name: str | None = None) -> str:
    return (
        f"{NAMED} | /results channel {which} | Success\n"
        f"  division: {name}\n"
        f"  channel: #{channel_name or which}"
    )


async def _make_db(tmp_path, name: str = "results_channels", *, status: str = "SETUP",
                   results: bool = True) -> str:
    """The league's server set up, with results on unless *results* is False, and Season 1 in
    *status* with its Division 1 (id 11) and none of its three results channels set."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    await seed_server(db_path)
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


async def _make_cog(db_path: str) -> ResultsCog:
    """The cog on the league's bot (`league_bot`), with the league's log channel and its real
    change queue, "now" pinned to `NOW`. `cog.channels` holds the channels the bot finds by id
    beside the league's own: `_channel` puts each there as it makes it."""
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    found = bot.get_channel
    bot.channels = {}
    bot.get_channel = MagicMock(
        side_effect=lambda channel_id: bot.channels.get(channel_id, found(channel_id))
    )
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _channel(cog, which: str, channel_id: int = CHANNEL_ID, name: str | None = None, *,
             may_post: bool = True):
    """A text channel as Discord gives one, named for the command unless *name* is given, its
    mention the one Discord renders, the bot allowed to post in it unless *may_post* is False,
    and found by the bot by its id."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name or which
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(return_value=SimpleNamespace(send_messages=may_post))
    cog.bot.channels[channel_id] = channel
    return channel


def _interaction(cog, which: str):
    """The manager's (77) interaction for `/results channel <which>`, recording every word they
    are told."""
    return member_asks(
        cog.bot, f"results channel {which}", user=member(ACTOR_ID, "Manager", "Manager#0001")
    )


def _replied(interaction) -> str:
    """What the manager's reply reads now."""
    return last_said(interaction)


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


def _refusal_line(which: str, reply: str) -> str:
    """The one line a refusal records: the member, the command, and the reply without its
    mark."""
    return (
        f"⛔ `/results channel {which}` refused for {NAMED} — {reply.split(' ', 1)[1]}"
    )


async def _ask(cog, interaction, which: str, *, name="Division 1", channel=None) -> None:
    """The manager runs the command, and nothing more: the queue is not run."""
    await undecorate(getattr(ResultsCog, COMMANDS[which][0]))(
        cog, interaction, name, channel or _channel(cog, which)
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
    """The division's three results channels as the database holds them, by command, each an
    integer; none set where it has no row."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
    held = {}
    for which, (_method, _kind, column, _label) in COMMANDS.items():
        value = None if row is None else row[column]
        held[which] = None if value is None else int(value)
    return held


def _none_set() -> dict[str, int | None]:
    return {which: None for which in COMMANDS}


async def _seed(db_path: str, which: str, channel_id: int, division_id: int = DIVISION_ID) -> None:
    """Give the division *channel_id* as its *which* channel already, where the command writes
    it, adding Division 2 (id 12) of the same season where it is the one named."""
    column = COMMANDS[which][2]
    async with get_connection(db_path) as db:
        if division_id == OTHER_DIVISION_ID:
            await db.execute(
                "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, 'Division 2', 2, 556)",
                (OTHER_DIVISION_ID, SEASON_ID),
            )
        await db.execute(
            f"INSERT INTO division_results_config (division_id, {column}) VALUES (?, ?) "  # noqa: S608
            f"ON CONFLICT(division_id) DO UPDATE SET {column} = excluded.{column}",
            (division_id, channel_id),
        )
        await db.commit()


async def _claim(db_path: str, which: str) -> None:
    """Give channel 770001 to Division 2 as its `_clashing` channel."""
    await _seed(db_path, _clashing(which), CHANNEL_ID, OTHER_DIVISION_ID)


def _spy(monkeypatch, service, method: str) -> AsyncMock:
    """*service*'s *method*, still answering as it does, its calls recorded."""
    spy = AsyncMock(wraps=getattr(service, method))
    monkeypatch.setattr(service, method, spy)
    return spy


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


async def test_the_live_season_is_the_one_whose_channels_are_set(tmp_path, monkeypatch):
    """The only season is Season 1, completed, with its Division 1. A division's channels belong
    to the season being built or raced; an archived season's no longer matter, and are never
    reached (issue #220). The manager runs `/results channel results` for Division 1 and
    #results: the live season is looked up once, the latest season whatever its status never is,
    the reply is the no-season refusal word for word, and the archived Division 1 keeps no results
    channel."""
    db_path = await _make_db(tmp_path, status="COMPLETED")
    cog = await _make_cog(db_path)
    live = _spy(monkeypatch, cog.bot.season_service, "get_setup_or_active_season")
    monkeypatch.setattr(
        cog.bot.season_service, "get_season_for_server",
        AsyncMock(side_effect=AssertionError("the latest season, archived or not, is not the target")),
    )
    interaction = _interaction(cog, "results")

    await _run(cog, interaction, "results")

    live.assert_awaited_once()
    assert _replied(interaction) == _NO_SEASON
    assert await _channels(db_path) == _none_set()


async def test_an_unknown_division_is_refused_by_name(tmp_path):
    """Season 1 is being set up with Division 1 alone. The manager runs `/results channel
    results` for Division 9: the reply names Division 9 as not found, word for word, no change is
    asked of the queue, and Division 1 keeps no results channel."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "results")

    await _run(cog, interaction, "results", name="Division 9")

    assert _replied(interaction) == _not_found("results", "Division 9")
    assert await change_rows(db_path) == []
    assert await _channels(db_path) == _none_set()


async def test_a_division_is_matched_regardless_of_case(tmp_path):
    """A manager typing `dIvIsIoN 1` for `/results channel results` means Division 1: once the
    queue has run, Division 1 (id 11) has #results as its results channel, and the reply and the
    log line name the division as the manager typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "results")

    await _run(cog, interaction, "results", name="dIvIsIoN 1")

    assert await _channels(db_path) == {**_none_set(), "results": CHANNEL_ID}
    assert _replied(interaction) == _reply("results", "set", name="dIvIsIoN 1")
    assert _lines(cog) == [_success_line("results", name="dIvIsIoN 1")]


# ---------------------------------------------------------------------------
# A channel does one job
# ---------------------------------------------------------------------------


async def test_a_channel_doing_another_job_is_refused_as_a_clash(tmp_path):
    """#results is already Division 2's standings channel. Two settings sharing one channel
    interleave two kinds of posting, and the posting paths that edit or delete their last message
    would delete each other's. The manager runs `/results channel results` for Division 1 and
    #results: the reply is the clash refusal word for word, and Division 1 keeps no results
    channel."""
    db_path = await _make_db(tmp_path)
    await _claim(db_path, "results")
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "results")

    await _run(cog, interaction, "results")

    assert _replied(interaction) == _clash("results")
    assert await _channels(db_path) == _none_set()


async def test_re_setting_the_same_channel_is_refused_as_unchanged(tmp_path):
    """#results is already Division 1's results channel, and the manager runs `/results channel
    results` for Division 1 and #results again. Not a collision with something else: telling a
    manager it clashes would send them looking for a conflict that does not exist. The reply is
    the unchanged refusal word for word, no change is asked of the queue, nothing is audited,
    Division 1 keeps #results, and the log channel takes the refusal, with the reply's own reason,
    as its only line."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, "results", CHANNEL_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "results")

    await _run(cog, interaction, "results")

    assert _replied(interaction) == _already("results")
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _channels(db_path) == {**_none_set(), "results": CHANNEL_ID}
    assert _lines(cog) == [_refusal_line("results", _already("results"))]


async def test_the_two_refusals_do_not_read_alike(tmp_path):
    """In one league #results is already Division 1's results channel; in another it is Division
    2's standings channel. `same_setting` is threaded all the way to the refusal text for this
    reason alone; a reader collapsing the two messages would lose the distinction entirely."""
    same_db = await _make_db(tmp_path, name="same")
    await _seed(same_db, "results", CHANNEL_ID)
    clash_db = await _make_db(tmp_path, name="clash")
    await _claim(clash_db, "results")
    same_cog, clash_cog = await _make_cog(same_db), await _make_cog(clash_db)
    same, clash = _interaction(same_cog, "results"), _interaction(clash_cog, "results")

    await _run(same_cog, same, "results")
    await _run(clash_cog, clash, "results")

    assert _replied(same) != _replied(clash)


async def test_a_refused_assignment_writes_nothing(tmp_path):
    """#results is already Division 2's standings channel, and the manager runs `/results channel
    results` for Division 1 and #results. The guard runs before the upsert — the same-value case
    used to be written and only then reported as unchanged, and this holds the order against a
    later tidy-up: no change is asked of the queue, nothing is audited, Division 1 keeps no
    results channel, and the log channel takes the refusal as its only line."""
    db_path = await _make_db(tmp_path)
    await _claim(db_path, "results")
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, "results"), "results")

    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _channels(db_path) == _none_set()
    assert _lines(cog) == [_refusal_line("results", _clash("results"))]


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel_type", ["results", "standings"])
async def test_moving_a_channel_says_it_was_updated(tmp_path, channel_type):
    """Division 1's results (or standings) already go to channel 111. A manager who meant a fresh
    assignment and is told it was *updated* has moved an existing one — worth noticing before the
    next post lands somewhere else. These two always said "set to" (issue #212)."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, channel_type, 111)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, channel_type)

    await _run(cog, interaction, channel_type)

    assert _replied(interaction) == _reply(channel_type, "updated")


@pytest.mark.parametrize("channel_type", ["results", "standings"])
async def test_each_channel_type_writes_its_own_column(tmp_path, channel_type):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The
    manager runs `/results channel results` (or `standings`) for channel 770001. Two near-identical
    commands: one writing the wrong column would point a division's results at its standings
    channel, and only this would object. Division 1 has 770001 in that command's column alone,
    and the reply names that channel's label and reads that it was set."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, channel_type)

    await _run(cog, interaction, channel_type)

    assert await _channels(db_path) == {**_none_set(), channel_type: CHANNEL_ID}
    assert _replied(interaction) == _reply(channel_type, "set")


@pytest.mark.parametrize("channel_type", ["results", "standings"])
async def test_each_assignment_is_audited_against_its_division(tmp_path, channel_type):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    (id 77) runs `/results channel results` (or `standings`) for channel 770001. The change is
    asked of the queue as that command's results setting, and the record is the one the command
    wrote while core held it: one `DIVISION_CHANNEL_SET` entry, with its `channel_type`, against
    Division 1 and the manager, from no channel to 770001, stamped with the queue's clock, never
    the host's."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, channel_type), channel_type)

    assert [row["kind"] for row in await change_rows(db_path)] == [COMMANDS[channel_type][1]]
    [entry] = await _audit(db_path)
    assert entry["change_type"] == "DIVISION_CHANNEL_SET"
    assert entry["division_id"] == DIVISION_ID
    assert entry["actor_id"] == ACTOR_ID
    assert json.loads(entry["old_value"]) == {"channel_type": channel_type, "channel_id": None}
    assert json.loads(entry["new_value"]) == {
        "channel_type": channel_type, "channel_id": CHANNEL_ID
    }
    assert entry["timestamp"] == NOW.isoformat()


async def test_the_audit_records_the_channel_being_replaced(tmp_path):
    """Division 1's results already go to channel 660000. A manager re-pointing them needs the
    old channel recoverable from the log; without it there is no way back to what it was."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, "results", 660000)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, "results"), "results")

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["channel_id"] == 660000


@pytest.mark.parametrize("channel_type", ["results", "standings"])
async def test_the_results_channels_are_logged_under_results_channel(tmp_path, channel_type):
    """A log line names the member, the command and its outcome, and the command is the one a
    league now types: `/results channel results` (or `standings`). It is the log channel's only
    line, word for word."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, channel_type), channel_type)

    assert _lines(cog) == [_success_line(channel_type)]


# ---------------------------------------------------------------------------
# /results channel verdicts, which keeps words of its own
# ---------------------------------------------------------------------------


async def test_the_verdicts_channel_is_not_refused_while_attendance_is_off(tmp_path):
    """Attendance is off, results on. Each command checks its *own* module: a league that has
    switched attendance off must still be able to set its verdicts channel, and one audit entry
    stands once the queue has run."""
    db_path = await _make_db(tmp_path)
    await set_module_flag(db_path, "attendance", False)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, "verdicts"), "verdicts")

    assert len(await _audit(db_path)) == 1


async def test_the_verdicts_channel_outside_a_guild_is_refused(tmp_path):
    """The manager's interaction carries no server. `permissions_for(guild.me)` needs one, so the
    absence is answered rather than raising an `AttributeError` at a manager: the reply is the
    channel-access refusal, and no change is asked of the queue."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "verdicts")
    interaction.guild = None

    await _run(cog, interaction, "verdicts")

    assert _replied(interaction) == _NO_ACCESS
    assert await change_rows(db_path) == []


async def test_the_verdicts_division_is_matched_regardless_of_case(tmp_path):
    """A manager typing `dIvIsIoN 1` for `/results channel verdicts` means Division 1: once the
    queue has run, Division 1 (id 11) has channel 770001 as its verdicts channel."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, "verdicts"), "verdicts", name="dIvIsIoN 1")

    assert await _channels(db_path) == {**_none_set(), "verdicts": CHANNEL_ID}


async def test_a_verdicts_channel_already_doing_another_job_is_refused(tmp_path):
    """#verdicts (770001) is already Division 2's results channel. The check runs before the
    write: the reply names the clash, Division 1 keeps no verdicts channel, no change is asked of
    the queue, and nothing is audited."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, "results", CHANNEL_ID, OTHER_DIVISION_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "verdicts")

    await _run(cog, interaction, "verdicts")

    assert f"<#{CHANNEL_ID}> is already the results channel for **Division 2**" in _replied(
        interaction
    )
    assert await _channels(db_path) == _none_set()
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []


async def test_the_verdicts_channel_is_set_and_audited(tmp_path):
    """Season 1 is being set up, and its Division 1 has no verdicts channel. The manager (id 77)
    runs `/results channel verdicts` for #verdicts (770001). The change is asked of the queue as
    results' verdicts-channel setting; the reply keeps its own words; and the record is the one
    the command wrote while core held it: one `VERDICTS_CHANNEL_SET` entry against Division 1 and
    the manager, from no channel to 770001, the ids as integers, stamped with the queue's clock."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "verdicts")

    await _run(cog, interaction, "verdicts")

    assert [row["kind"] for row in await change_rows(db_path)] == [COMMANDS["verdicts"][1]]
    assert _replied(interaction) == "✅ Verdicts channel for Division 1 set to #verdicts."
    [entry] = await _audit(db_path)
    assert entry["change_type"] == "VERDICTS_CHANNEL_SET"
    assert entry["division_id"] == DIVISION_ID
    assert entry["actor_id"] == ACTOR_ID
    assert json.loads(entry["old_value"]) == {"channel_id": None}
    assert json.loads(entry["new_value"]) == {"channel_id": CHANNEL_ID}
    assert entry["timestamp"] == NOW.isoformat()


async def test_moving_the_verdicts_channel_says_it_was_updated(tmp_path):
    """Division 1's verdicts already go to channel 111. The reply reads that the channel was
    updated, and the audit entry records channel 111 replaced, as an integer, which is what makes
    the audit answer "where were the verdicts going before?"."""
    db_path = await _make_db(tmp_path)
    await _seed(db_path, "verdicts", 111)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, "verdicts")

    await _run(cog, interaction, "verdicts")

    assert _replied(interaction) == "✅ Verdicts channel for Division 1 updated to #verdicts."
    assert json.loads((await _audit(db_path))[0]["old_value"]) == {"channel_id": 111}


async def test_the_verdicts_channel_is_logged_under_results_channel_verdicts(tmp_path):
    """A log line names the member, the command and its outcome, with the division and the
    channel beneath it. It is the log channel's only line, word for word."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog, "verdicts"), "verdicts")

    assert _lines(cog) == [_success_line("verdicts")]


# ---------------------------------------------------------------------------
# Every refusal is recorded (#482)
# ---------------------------------------------------------------------------


def _refusal(case_id, command, reply, **setup):
    return pytest.param(command, reply, setup, id=case_id)


def _channel_refusals(setting: str):
    """The refusals of `/results channel <setting>`, each with the reply it gives today."""
    command = f"results channel {setting}"
    return [
        _refusal(f"{setting}-module-off", command, _OFF, results_off=True),
        _refusal(f"{setting}-no-season", command, _NO_SEASON, status="COMPLETED"),
        _refusal(
            f"{setting}-unknown-division", command, _not_found(setting, "Division 9"),
            name="Division 9",
        ),
        _refusal(f"{setting}-channel-doing-another-job", command, _clash(setting), claimed=True),
        _refusal(f"{setting}-already-set", command, _already(setting), held=True),
    ]


@pytest.mark.parametrize(
    ("command", "reply", "setup"),
    _channel_refusals("results")
    + _channel_refusals("standings")
    + _channel_refusals("verdicts")
    + [
        _refusal(
            "verdicts-channel-the-bot-cannot-post-in", "results channel verdicts", _NO_ACCESS,
            may_post=False,
        ),
    ],
)
async def test_every_channel_refusal_is_recorded(tmp_path, monkeypatch, command, reply, setup):
    """The manager runs `/results channel results`, `standings` or `verdicts` for Division 1
    and is turned away: the module is off, no season is live, the division is not in the
    season, the channel already does Division 2's other job or already does this one for
    Division 1, or (verdicts) the bot may not post in it. The manager is answered as today, no
    change is asked of the queue, nothing is written or audited, and exactly one line records the
    refusal and its reason.

    The module-off words are each command's own, not the results cog's gate, which is worded
    differently: the commands keep the words they had before they moved, and are refused before
    the season is read or anything deferred. A verdicts channel the bot cannot post in is
    checked before the season is read, rather than discovered at the first verdict. The
    verdicts command names its own setting to the check, so the channel a division's verdicts
    already go to is refused in its own words, not as a clash a manager would go looking for."""
    setting = command.rsplit(" ", 1)[1]
    db_path = await _make_db(
        tmp_path, status=setup.get("status", "SETUP"), results=not setup.get("results_off")
    )
    if setup.get("claimed"):
        await _claim(db_path, setting)
    if setup.get("held"):
        await _seed(db_path, setting, CHANNEL_ID)
    before = await _channels(db_path)
    cog = await _make_cog(db_path)
    season_lookup = _spy(monkeypatch, cog.bot.season_service, "get_setup_or_active_season")
    interaction = _interaction(cog, setting)
    channel = _channel(cog, setting, may_post=setup.get("may_post", True))

    await _run(cog, interaction, setting, name=setup.get("name", "Division 1"), channel=channel)

    assert _replied(interaction) == reply
    assert _lines(cog) == [_refusal_line(setting, reply)]
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _channels(db_path) == before
    if setup.get("results_off") or setup.get("may_post") is False:
        season_lookup.assert_not_awaited()
    if setup.get("results_off"):
        interaction.response.defer.assert_not_awaited()


# ---------------------------------------------------------------------------
# Through the change queue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_the_manager_is_acknowledged_before_anything_is_written(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    (id 77) runs `/results channel results`, `standings` or `verdicts` for channel 770001. They
    are answered at once with the queue's acknowledgement, naming the change's first job, and
    nothing is deferred, verdicts included; nothing is written until the queue runs the change:
    Division 1 still has none of its channels and no audit entry stands."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)

    first_job = (await step_rows(db_path))[0]["id"]
    assert acknowledgement(interaction) == _acknowledged(which, first_job)
    interaction.response.defer.assert_not_awaited()
    assert await _channels(db_path) == _none_set()
    assert await _audit(db_path) == []


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_a_fault_in_the_save_saves_neither_the_channel_nor_its_audit(
    tmp_path, monkeypatch, which
):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    runs `/results channel results`, `standings` or `verdicts` for channel 770001, and the audit
    entry's write fails on the database as the queue runs the change. The channel is not saved
    either: Division 1 still has none of its channels and no audit entry stands. The manager is
    told in `setting_change`'s words that nothing was saved and to run it again; the log channel
    gets the one failure line and no success line; the queue is not stopped."""
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
    assert _replied(interaction) == (
        f"❌ `/results channel {which}` stopped on a fault in the bot, not on anything you "
        f"entered, and did not finish. Nothing was saved. Run `/results channel {which}` again "
        f"to retry. The fault is recorded in the log channel."
    )
    assert _lines(cog) == [
        f"❌ `/results channel {which}` failed for {NAMED} — OperationalError. The details are "
        f"in the host's log."
    ]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_two_changes_waiting_for_one_division_each_record_the_channel_they_replace(
    tmp_path, which
):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    asks `/results channel results`, `standings` or `verdicts` for channel 770001, then, before the
    queue runs, for channel 770002, and is acknowledged for each. Each change reads the channel it
    replaces as it saves: the first is told its channel was set and records none replaced, the
    second is told its channel was updated and records 770001 replaced, and Division 1 is left
    with 770002."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, second = _interaction(cog, which), _interaction(cog, which)

    await _ask(cog, first, which)
    await _ask(cog, second, which, channel=_channel(cog, which, OTHER_CHANNEL_ID, "second"))
    assert acknowledgement(first).startswith("⏳ ")
    assert acknowledgement(second).startswith("⏳ ")
    await run_queue(cog.bot)

    assert _replied(first) == _reply(which, "set")
    assert _replied(second) == _reply(
        which, "updated", channel_id=OTHER_CHANNEL_ID, channel_name="second"
    )
    replaced = [
        (json.loads(row["old_value"])["channel_id"], json.loads(row["new_value"])["channel_id"])
        for row in await _audit(db_path)
    ]
    assert replaced == [(None, CHANNEL_ID), (CHANNEL_ID, OTHER_CHANNEL_ID)]
    assert (await _channels(db_path))[which] == OTHER_CHANNEL_ID


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_the_same_change_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    (id 77) asks `/results channel results`, `standings` or `verdicts` for channel 770001, and is
    acknowledged; before the queue runs, with nothing else asked of the league since, they ask for
    it again. The second is refused in the queue's words, as a change already asked for and not
    yet started, and the log channel takes that refusal as a line; only the one change is queued.
    The queue runs it: Division 1 has 770001, one audit entry stands, and the first is told its
    channel was set."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, repeat = _interaction(cog, which), _interaction(cog, which)
    repeated = (
        f"⚠️ Setting the {which} channel for **Division 1** to <#{CHANNEL_ID}> has already been "
        f"asked for and has not started yet, so it was not asked for again."
    )

    await _ask(cog, first, which)
    await _ask(cog, repeat, which)

    assert acknowledgement(first).startswith("⏳ ")
    assert _replied(repeat) == repeated
    assert len(await change_rows(db_path)) == 1
    await run_queue(cog.bot)

    assert (await _channels(db_path))[which] == CHANNEL_ID
    assert len(await _audit(db_path)) == 1
    assert _replied(first) == _reply(which, "set")
    assert _lines(cog) == [_refusal_line(which, repeated), _success_line(which)]


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_the_channel_is_set_while_the_queue_is_stopped_on_another_job(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The league's
    queue is stopped on another job, which failed on a fault in the bot. The manager (id 77) runs
    `/results channel results`, `standings` or `verdicts` for channel 770001, the remedy a stop
    notice names for a channel gone. They are acknowledged at once and told the change runs at
    once, past the stop, and that the stopped job stays stopped; nothing is written as they ask.
    Once the queue runs, Division 1 has 770001, the manager is told the channel was set, and the
    other job is still stopped where it was."""
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
    assert _replied(interaction) == _reply(which, "set")
    still = await stopped_job(db_path)
    assert still is not None
    assert (still["id"], still["change_id"]) == (stopped["id"], other)


# ---------------------------------------------------------------------------
# What changes while the change waits
# ---------------------------------------------------------------------------


async def _switch_results_off(cog, db_path: str, which: str) -> None:
    await set_module_flag(db_path, "results", False)


async def _delete_the_division(cog, db_path: str, which: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM divisions WHERE id = ?", (DIVISION_ID,))
        await db.commit()


async def _end_the_season(cog, db_path: str, which: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (SEASON_ID,))
        await db.commit()


async def _claim_the_channel(cog, db_path: str, which: str) -> None:
    await _claim(db_path, which)


async def _lose_access(cog, db_path: str, which: str) -> None:
    cog.bot.channels[CHANNEL_ID].permissions_for.return_value = SimpleNamespace(
        send_messages=False
    )


async def _delete_the_channel(cog, db_path: str, which: str) -> None:
    cog.bot.channels[CHANNEL_ID] = None


def _meanwhile_cases():
    """Each command, with each thing that may change while its change waits and the refusal it
    then meets, in the words the command gives at the ask."""
    cases = []
    for which in sorted(COMMANDS):
        cases += [
            pytest.param(which, _switch_results_off, _OFF, id=f"{which}-results_switched_off"),
            pytest.param(
                which, _delete_the_division, _not_found(which), id=f"{which}-division_deleted"
            ),
            pytest.param(which, _end_the_season, _NO_SEASON, id=f"{which}-season_ended"),
            pytest.param(which, _claim_the_channel, _clash(which), id=f"{which}-channel_claimed"),
        ]
    cases += [
        pytest.param("verdicts", _lose_access, _NO_ACCESS, id="verdicts-access_lost"),
        pytest.param("verdicts", _delete_the_channel, _NO_ACCESS, id="verdicts-channel_deleted"),
    ]
    return cases


@pytest.mark.parametrize("which,meanwhile,refused", _meanwhile_cases())
async def test_a_change_is_refused_as_it_starts_where_its_ask_no_longer_holds(
    tmp_path, which, meanwhile, refused
):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    (id 77) asks `/results channel results`, `standings` or `verdicts` for channel 770001, and is
    acknowledged; before the change runs, as a change waiting ahead of it on the league's lane
    would, results is switched off, or Division 1 is deleted, or Season 1 is completed, or 770001
    is made one of Division 2's channels, or (verdicts) the bot loses its permission to post in
    770001 or the channel is deleted. The change judges again as it starts and is refused:
    Division 1's channel is not written, nothing is audited, the manager's reply becomes the
    refusal the command gives at the ask, word for word, the log channel takes that refusal as its
    only line, and the queue is not stopped."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog, which)

    await _ask(cog, interaction, which)
    assert acknowledgement(interaction).startswith("⏳ ")
    await meanwhile(cog, db_path, which)
    await run_queue(cog.bot)

    assert _replied(interaction) == refused
    assert (await _channels(db_path))[which] is None
    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(which, refused)]
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize("which", sorted(COMMANDS))
async def test_a_division_renamed_while_its_change_waits_still_gets_its_channel(tmp_path, which):
    """Season 1 is being set up, and its Division 1 has none of its results channels. The manager
    asks `/results channel results`, `standings` or `verdicts` for channel 770001 for Division 1,
    and is acknowledged; before the change runs, Division 1 is renamed Division One. The change is
    for the division asked for, whatever it is called by then: Division One (id 11) is given
    770001 and audited, and the manager's reply names the division as they typed it."""
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
    assert _replied(interaction) == _reply(which, "set")
