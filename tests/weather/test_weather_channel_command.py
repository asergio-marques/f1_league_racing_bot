"""`/weather channel`: setting the channel a division's forecasts are posted to.

Weather's own command, under weather's own group. It sat under core's `/division` until #462
moved it, and only its name changed: it checks weather is on in its own words, then the live
season, then the division, then that the channel is free, and only then writes, records and
logs.

**A channel serves one purpose across the whole server** (decided 2026-09-06). Two settings
sharing one channel interleave two kinds of posting, and several posting paths edit or delete
their last message by an id stored against the channel, so sharing is how one output comes to
delete another's. The check runs **before** the write, so a refusal leaves the configuration
exactly as it stood.

**Re-running the command with the value it already holds is refused in its own words.** It is
not a collision with something else, and reporting it as one would send a manager looking for a
conflict that does not exist. `test_the_two_refusals_do_not_read_alike` holds the difference.

**Every refusal is recorded in the log channel** (#482; the core specification's "The record of
what changed"), as one "⛔" line naming `/weather channel`, the member and the reply's reason.

**It is a weather `Setting` on the change queue** (#283), as core's `/division lineup-channel`
is (`tests/core/test_division_core_channels.py`): the command answers with the queue's
acknowledgement, and the queue writes the division's forecast channel and its audit entry in one
save, stamped with the queue's clock, then updates the acknowledgement to the reply and writes
the line. The module, the season and the division are judged by the command, which must find the
division to ask for its change; the channel's use, and the value already held, by the setting's
check at the ask, in the same words. The check is made again as the change starts, so weather
switched off, the division deleted, the season ended or the channel claimed while the change
waits is refused then, in the ask's words; a division renamed meanwhile is still the division
asked for, and gets its channel. The tests run the bot the entry point builds, with the real
queue and "now" pinned, and run the queue.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.weather.cogs.weather_cog import WeatherCog
from tests.support.change_queue import (
    Clock,
    PAST_THE_STOP,
    acknowledgement,
    change_rows,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    step_rows,
    stop_the_league_lane,
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

#: The kind of change `/weather channel` asks of the queue.
KIND = "weather.setting.forecast_channel"

_OFF = "❌ The Weather module is not enabled."
_NO_SEASON = (
    "❌ No season is live. A division's channels belong to the season being built or raced — "
    "start one with `/season setup`."
)
_NOT_FOUND = "❌ Division **Division 1** not found in the current season."
_RESULTS_CLASH = (
    f"❌ <#{CHANNEL_ID}> is already the results channel for **Division 2**. A channel does one "
    f"job — pick one that is not in use, or clear the other setting first."
)
_ALREADY = (
    f"ℹ️ <#{CHANNEL_ID}> is already the weather forecast channel for **Division 1**. Nothing was "
    f"changed."
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, name: str = "weather_channel", *, status: str = "SETUP",
                   weather: bool = True) -> str:
    """The league's server set up, with weather on unless *weather* is False, and Season 1 in
    *status* with its Division 1 (id 11) and no forecast channel set."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "weather", weather)
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


async def _make_cog(db_path: str) -> WeatherCog:
    """The cog on the league's bot (`league_bot`), with the league's log channel and its real
    change queue, "now" pinned to `NOW`."""
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    cog = WeatherCog.__new__(WeatherCog)
    cog.bot = bot
    return cog


def _channel(channel_id: int = CHANNEL_ID, name: str = "forecasts"):
    """A text channel as Discord gives one, its mention the one Discord renders."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.mention = f"<#{channel_id}>"
    return channel


def _interaction(cog):
    """The manager's (77) interaction for `/weather channel`, recording every word they are
    told."""
    return member_asks(
        cog.bot, "weather channel", user=member(ACTOR_ID, "Manager", "Manager#0001")
    )


def _replied(interaction) -> str:
    """What the manager's reply reads now."""
    return last_said(interaction)


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


def _refusal_line(reply: str) -> str:
    """The one line a refusal records: the member, the command, and the reply without its
    mark."""
    return f"⛔ `/weather channel` refused for {NAMED} — {reply.split(' ', 1)[1]}"


async def _ask(cog, interaction, *, name="Division 1", channel=None) -> None:
    """The manager runs the command, and nothing more: the queue is not run."""
    await undecorate(WeatherCog.channel)(cog, interaction, name, channel or _channel())


async def _run(cog, interaction, *, name="Division 1", channel=None) -> None:
    """The manager runs the command, and the queue is run."""
    await _ask(cog, interaction, name=name, channel=channel)
    await run_queue(cog.bot)


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id, actor_id, timestamp "
            "FROM audit_entries ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


async def _forecast_channel(db_path: str, division_id: int = DIVISION_ID):
    """The division's forecast channel as the database holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT forecast_channel_id FROM divisions WHERE id = ?", (division_id,)
        )
        row = await cursor.fetchone()
        return None if row is None else row["forecast_channel_id"]


async def _seed_forecast_channel(db_path: str, channel_id: int) -> None:
    """Give Division 1 a forecast channel already, as a move finds it."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?", (channel_id, DIVISION_ID)
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


def _spy(monkeypatch, service, method: str) -> AsyncMock:
    """*service*'s *method*, still answering as it does, its calls recorded."""
    spy = AsyncMock(wraps=getattr(service, method))
    monkeypatch.setattr(service, method, spy)
    return spy


# ---------------------------------------------------------------------------
# The refusal ladder
# ---------------------------------------------------------------------------


async def test_the_weather_channel_is_refused_while_weather_is_off(tmp_path, monkeypatch):
    """Weather is off; Season 1 is being set up, and its Division 1 has no forecast channel. The
    manager (id 77) runs `/weather channel` for #forecasts. Setting a forecast channel for a
    module that is not running configures something no code reads: the reply is the module's
    refusal word for word, the season is never looked up, Division 1 still has no forecast
    channel, no change is asked of the queue, nothing is audited, and the log channel takes the
    refusal as its only line."""
    db_path = await _make_db(tmp_path, weather=False)
    cog = await _make_cog(db_path)
    season_lookup = _spy(monkeypatch, cog.bot.season_service, "get_setup_or_active_season")
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert _replied(interaction) == _OFF
    season_lookup.assert_not_awaited()
    assert await _forecast_channel(db_path) is None
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(_OFF)]


async def test_the_live_season_is_the_one_whose_channels_are_set(tmp_path, monkeypatch):
    """The only season is Season 1, completed, with its Division 1. A division's channels belong
    to the season being built or raced; an archived season's no longer matter, and are never
    reached (issue #220). The manager runs `/weather channel` for Division 1 and #forecasts: the
    live season is looked up once, the latest season whatever its status never is, the reply is
    the no-season refusal word for word, the archived Division 1 keeps no forecast channel,
    nothing is audited, and the log channel takes the refusal as its only line."""
    db_path = await _make_db(tmp_path, status="COMPLETED")
    cog = await _make_cog(db_path)
    live = _spy(monkeypatch, cog.bot.season_service, "get_setup_or_active_season")
    monkeypatch.setattr(
        cog.bot.season_service, "get_season_for_server",
        AsyncMock(side_effect=AssertionError("the latest season, archived or not, is not the target")),
    )
    interaction = _interaction(cog)

    await _run(cog, interaction)

    live.assert_awaited_once()
    assert _replied(interaction) == _NO_SEASON
    assert await _forecast_channel(db_path) is None
    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(_NO_SEASON)]


async def test_an_unknown_division_is_refused_by_name(tmp_path):
    """Season 1 is being set up with Division 1 alone. The manager runs `/weather channel` for
    Division 9: the reply names Division 9 as not found, word for word, no change is asked of the
    queue, and Division 1 keeps no forecast channel."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction, name="Division 9")

    assert _replied(interaction) == "❌ Division **Division 9** not found in the current season."
    assert await change_rows(db_path) == []
    assert await _forecast_channel(db_path) is None


async def test_an_unknown_division_is_recorded_as_a_refusal(tmp_path):
    """A division the live season does not hold is refused, and the refusal recorded as the log
    channel's only line."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction, name="Division 9")

    assert _lines(cog) == [
        _refusal_line("❌ Division **Division 9** not found in the current season.")
    ]


async def test_a_division_is_matched_regardless_of_case(tmp_path):
    """A manager typing `dIvIsIoN 1` means Division 1: once the queue has run, Division 1 (id 11)
    has #forecasts as its forecast channel, and the reply and the log line name the division as
    the manager typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction, name="dIvIsIoN 1")

    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert _replied(interaction) == (
        f"✅ Weather forecast channel for **dIvIsIoN 1** set to <#{CHANNEL_ID}>."
    )
    assert any("\n  division: dIvIsIoN 1\n" in line for line in _lines(cog)), _lines(cog)


# ---------------------------------------------------------------------------
# A channel does one job
# ---------------------------------------------------------------------------


async def test_a_channel_doing_another_job_is_refused_as_a_clash(tmp_path):
    """#forecasts is already Division 2's results channel. Two settings sharing one channel
    interleave two kinds of posting, and the posting paths that edit or delete their last message
    would delete each other's. The manager runs `/weather channel` for Division 1 and #forecasts:
    the reply is the clash refusal word for word, and Division 1 keeps no forecast channel."""
    db_path = await _make_db(tmp_path)
    await _seed_other_division_results(db_path, CHANNEL_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert _replied(interaction) == _RESULTS_CLASH
    assert await _forecast_channel(db_path) is None


async def test_a_channel_doing_another_job_is_recorded_as_a_refusal(tmp_path):
    """The clash with another setting is refused and the refusal recorded, with the reply's own
    reason, as the log channel's only line; nothing is audited."""
    db_path = await _make_db(tmp_path)
    await _seed_other_division_results(db_path, CHANNEL_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(_RESULTS_CLASH)]


async def test_the_two_refusals_do_not_read_alike(tmp_path):
    """In one league #forecasts is already Division 1's forecast channel; in another it is
    Division 2's results channel. `same_setting` is threaded all the way to the refusal text for
    this reason alone; a reader collapsing the two messages would lose the distinction
    entirely."""
    same_db = await _make_db(tmp_path, name="same")
    await _seed_forecast_channel(same_db, CHANNEL_ID)
    clash_db = await _make_db(tmp_path, name="clash")
    await _seed_other_division_results(clash_db, CHANNEL_ID)
    same_cog, clash_cog = await _make_cog(same_db), await _make_cog(clash_db)
    same, clash = _interaction(same_cog), _interaction(clash_cog)

    await _run(same_cog, same)
    await _run(clash_cog, clash)

    assert _replied(same) != _replied(clash)


async def test_a_refused_assignment_writes_nothing(tmp_path):
    """#forecasts is already Division 1's forecast channel, and the manager runs `/weather
    channel` for Division 1 and #forecasts again. The check runs before the write: the
    same-value case used to be written and only then reported as unchanged — this holds the
    order against a later tidy-up. It is refused as unchanged, word for word, not as a collision
    with something else: telling a manager it clashes would send them looking for a conflict
    that does not exist. No change is asked of the queue, nothing is audited, Division 1 keeps
    #forecasts, and the log channel takes the refusal, with the reply's own reason, as its only
    line."""
    db_path = await _make_db(tmp_path)
    await _seed_forecast_channel(db_path, CHANNEL_ID)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert _replied(interaction) == _ALREADY
    assert await change_rows(db_path) == []
    assert await _audit(db_path) == []
    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert _lines(cog) == [_refusal_line(_ALREADY)]


# ---------------------------------------------------------------------------
# The assignment
# ---------------------------------------------------------------------------


async def test_a_free_channel_is_set(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager runs
    `/weather channel` for #forecasts, and the queue runs the change: Division 1's forecast
    channel is #forecasts, and the reply reads that it was set."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert _replied(interaction) == (
        f"✅ Weather forecast channel for **Division 1** set to <#{CHANNEL_ID}>."
    )


async def test_moving_a_channel_says_it_was_updated(tmp_path):
    """Division 1's forecasts already go to channel 111. A manager who meant a fresh assignment
    and is told it was *updated* has moved an existing one — worth noticing before the next
    forecast lands somewhere else (#212)."""
    db_path = await _make_db(tmp_path)
    await _seed_forecast_channel(db_path, 111)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert _replied(interaction) == (
        f"✅ Weather forecast channel for **Division 1** updated to <#{CHANNEL_ID}>."
    )


async def test_the_assignment_is_audited_against_its_division(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager (id 77)
    runs `/weather channel` for #forecasts. The change is asked of the queue as weather's
    forecast-channel setting, and the record is the one the command wrote while core held it:
    one `DIVISION_CHANNEL_SET` entry, with its `channel_type`, against Division 1 and the
    manager, from no channel to #forecasts, so the audit of a channel's history reads the same
    across the move. It is stamped with the queue's clock, never the host's."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog))

    assert [row["kind"] for row in await change_rows(db_path)] == [KIND]
    [entry] = await _audit(db_path)
    assert entry["change_type"] == "DIVISION_CHANNEL_SET"
    assert entry["division_id"] == DIVISION_ID
    assert entry["actor_id"] == ACTOR_ID
    assert json.loads(entry["old_value"]) == {"channel_type": "weather", "channel_id": None}
    assert json.loads(entry["new_value"]) == {"channel_type": "weather", "channel_id": CHANNEL_ID}
    assert entry["timestamp"] == NOW.isoformat()


async def test_the_audit_records_the_channel_being_replaced(tmp_path):
    """Division 1's forecasts already go to channel 660000. A manager re-pointing them needs the
    old channel recoverable from the log; without it there is no way back to what it was."""
    db_path = await _make_db(tmp_path)
    await _seed_forecast_channel(db_path, 660000)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog))

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["channel_id"] == 660000


async def test_the_weather_channel_is_logged_as_weather_channel(tmp_path):
    """A log line names the member, the command and its outcome, and the command is the one a
    league now types: `/weather channel`. It is the log channel's only line, word for word."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)

    await _run(cog, _interaction(cog))

    assert _lines(cog) == [
        f"{NAMED} | /weather channel | Success\n"
        "  division: Division 1\n"
        "  channel: #forecasts"
    ]


async def test_the_manager_is_acknowledged_before_anything_is_written(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager (id 77)
    runs `/weather channel` for #forecasts. They are answered at once with the queue's
    acknowledgement, naming the change's first job, and nothing is written until the queue runs
    the change: Division 1 still has no forecast channel and no audit entry stands."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _ask(cog, interaction)

    first_job = (await step_rows(db_path))[0]["id"]
    assert acknowledgement(interaction) == (
        f"⏳ Setting the weather forecast channel for **Division 1** to <#{CHANNEL_ID}>. This "
        f"message will be updated when it is done; if it takes longer, the log channel will say "
        f"so. It begins with job #{first_job}."
    )
    assert await _forecast_channel(db_path) is None
    assert await _audit(db_path) == []


async def test_the_channel_is_set_while_the_queue_is_stopped_on_another_job(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The league's queue
    is stopped on another job, which failed on a fault in the bot. The manager (id 77) runs
    `/weather channel` for #forecasts (770001), the remedy the forecast's stop notice names. They
    are acknowledged at once and told the change runs at once, past the stop, and that the stopped
    job stays stopped; nothing is written as they ask. Once the queue runs, Division 1's forecast
    channel is 770001, the manager is told it was set, and the other job is still stopped where it
    was."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    stopped = await stop_the_league_lane(cog.bot, db_path)
    interaction = _interaction(cog)

    await _ask(cog, interaction)

    first_job = min(
        row["id"] for row in await step_rows(db_path) if row["change_id"] != stopped["change_id"]
    )
    assert acknowledgement(interaction) == (
        f"⏳ Setting the weather forecast channel for **Division 1** to <#{CHANNEL_ID}>. This "
        f"message will be updated when it is done; if it takes longer, the log channel will say "
        f"so. It begins with job #{first_job}." + PAST_THE_STOP.format(job=stopped["id"])
    )
    assert await _forecast_channel(db_path) is None
    await run_queue(cog.bot)

    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert _replied(interaction) == (
        f"✅ Weather forecast channel for **Division 1** set to <#{CHANNEL_ID}>."
    )
    still = await stopped_job(db_path)
    assert still is not None
    assert (still["id"], still["change_id"]) == (stopped["id"], stopped["change_id"])


async def test_a_fault_in_the_save_saves_neither_the_channel_nor_its_audit(tmp_path, monkeypatch):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager runs
    `/weather channel` for #forecasts, and the audit entry's write fails on the database as the
    queue runs the change. The channel is not saved either: Division 1 still has no forecast
    channel and no audit entry stands. The manager is told in `setting_change`'s words that
    nothing was saved and to run it again; the log channel gets the one failure line and no
    success line; the queue is not stopped."""
    from leaguebot.core.services import change_queue

    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    monkeypatch.setattr(
        change_queue, "record_change_on",
        AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
    )
    interaction = _interaction(cog)

    await _run(cog, interaction)

    assert await _forecast_channel(db_path) is None
    assert await _audit(db_path) == []
    assert _replied(interaction) == (
        "❌ `/weather channel` stopped on a fault in the bot, not on anything you entered, and "
        "did not finish. Nothing was saved. Run `/weather channel` again to retry. The fault is "
        "recorded in the log channel."
    )
    assert _lines(cog) == [
        f"❌ `/weather channel` failed for {NAMED} — OperationalError. The details are in the "
        f"host's log."
    ]
    assert await stopped_job(db_path) is None


async def test_two_changes_waiting_for_one_division_each_record_the_channel_they_replace(
    tmp_path,
):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager asks for
    #forecasts, then, before the queue runs, for #weather, and is acknowledged for each. Each
    change reads the channel it replaces as it saves: the first is told its channel was set and
    records none replaced, the second is told its channel was updated and records #forecasts
    replaced, and Division 1 is left with #weather."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, second = _interaction(cog), _interaction(cog)

    await _ask(cog, first)
    await _ask(cog, second, channel=_channel(OTHER_CHANNEL_ID, "weather"))
    assert acknowledgement(first).startswith("⏳ ")
    assert acknowledgement(second).startswith("⏳ ")
    await run_queue(cog.bot)

    assert _replied(first) == (
        f"✅ Weather forecast channel for **Division 1** set to <#{CHANNEL_ID}>."
    )
    assert _replied(second) == (
        f"✅ Weather forecast channel for **Division 1** updated to <#{OTHER_CHANNEL_ID}>."
    )
    assert [
        (json.loads(row["old_value"]), json.loads(row["new_value"]))
        for row in await _audit(db_path)
    ] == [
        ({"channel_type": "weather", "channel_id": None},
         {"channel_type": "weather", "channel_id": CHANNEL_ID}),
        ({"channel_type": "weather", "channel_id": CHANNEL_ID},
         {"channel_type": "weather", "channel_id": OTHER_CHANNEL_ID}),
    ]
    assert await _forecast_channel(db_path) == OTHER_CHANNEL_ID


async def test_the_same_change_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager (id 77)
    asks for #forecasts, and is acknowledged; before the queue runs, with nothing else asked of
    the league since, they ask for #forecasts again. The second is refused in the queue's words,
    as a change already asked for and not yet started, and the log channel takes that refusal as
    a line; only the one change is queued. The queue runs it: Division 1 has #forecasts, one
    audit entry stands, and the first is told its channel was set."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    first, repeat = _interaction(cog), _interaction(cog)
    repeated = (
        f"⚠️ Setting the weather forecast channel for **Division 1** to <#{CHANNEL_ID}> has "
        f"already been asked for and has not started yet, so it was not asked for again."
    )

    await _ask(cog, first)
    await _ask(cog, repeat)

    assert acknowledgement(first).startswith("⏳ ")
    assert _replied(repeat) == repeated
    assert len(await change_rows(db_path)) == 1
    await run_queue(cog.bot)

    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert len(await _audit(db_path)) == 1
    assert _replied(first) == (
        f"✅ Weather forecast channel for **Division 1** set to <#{CHANNEL_ID}>."
    )
    assert _lines(cog) == [
        _refusal_line(repeated),
        f"{NAMED} | /weather channel | Success\n"
        "  division: Division 1\n"
        "  channel: #forecasts",
    ]


# ---------------------------------------------------------------------------
# What changes while the change waits
# ---------------------------------------------------------------------------


async def _switch_weather_off(db_path: str) -> None:
    await set_module_flag(db_path, "weather", False)


async def _delete_the_division(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM divisions WHERE id = ?", (DIVISION_ID,))
        await db.commit()


async def _end_the_season(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", (SEASON_ID,))
        await db.commit()


async def _claim_the_channel(db_path: str) -> None:
    await _seed_other_division_results(db_path, CHANNEL_ID)


@pytest.mark.parametrize(
    "meanwhile,refused",
    [
        pytest.param(_switch_weather_off, _OFF, id="weather_switched_off"),
        pytest.param(_delete_the_division, _NOT_FOUND, id="division_deleted"),
        pytest.param(_end_the_season, _NO_SEASON, id="season_ended"),
        pytest.param(_claim_the_channel, _RESULTS_CLASH, id="channel_claimed"),
    ],
)
async def test_a_change_is_refused_as_it_starts_where_its_ask_no_longer_holds(
    tmp_path, meanwhile, refused
):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager (id 77)
    asks for #forecasts, and is acknowledged; before the change runs, as a change waiting ahead
    of it on the league's lane would, weather is switched off, or Division 1 is deleted, or
    Season 1 is completed, or #forecasts is made Division 2's results channel. The change judges
    again as it starts and is refused: Division 1's forecast channel is not written, nothing is
    audited, the manager's reply becomes the refusal the command gives at the ask, word for word,
    the log channel takes that refusal as its only line, and the queue is not stopped."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _ask(cog, interaction)
    assert acknowledgement(interaction).startswith("⏳ ")
    await meanwhile(db_path)
    await run_queue(cog.bot)

    assert _replied(interaction) == refused
    assert await _forecast_channel(db_path) is None
    assert await _audit(db_path) == []
    assert _lines(cog) == [_refusal_line(refused)]
    assert await stopped_job(db_path) is None


async def test_a_division_renamed_while_its_change_waits_still_gets_its_channel(tmp_path):
    """Season 1 is being set up, and its Division 1 has no forecast channel. The manager asks for
    #forecasts for Division 1, and is acknowledged; before the change runs, Division 1 is renamed
    Division One. The change is for the division asked for, whatever it is called by then:
    Division One (id 11) is given #forecasts and audited, and the manager's reply names the
    division as they typed it."""
    db_path = await _make_db(tmp_path)
    cog = await _make_cog(db_path)
    interaction = _interaction(cog)

    await _ask(cog, interaction)
    assert acknowledgement(interaction).startswith("⏳ ")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET name = 'Division One' WHERE id = ?", (DIVISION_ID,)
        )
        await db.commit()
    await run_queue(cog.bot)

    assert await _forecast_channel(db_path) == CHANNEL_ID
    assert [row["division_id"] for row in await _audit(db_path)] == [DIVISION_ID]
    assert _replied(interaction) == (
        f"✅ Weather forecast channel for **Division 1** set to <#{CHANNEL_ID}>."
    )
