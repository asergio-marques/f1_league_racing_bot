"""Turning Results & Standings off mid-season, carried out on the change queue (#439, defect 8).

`/module disable results` used to commit the flag, then erase the season over many calls to
Discord, and only then close the rounds waiting on results. A stop between the first and the last
left rounds nothing could ever close, and a season that could never be completed. The switch-off
is now one change on the queue: its first job erases the season's rows, drops the flag and closes
the rounds in one save, keeping the ids of every message to take down; one job for each message
or channel then takes it down; a closing job counts what went. The hub refresh and the season's
wind-down are changes of their own, asked for in the switch-off's save.

A job that fails stops the queue until it is cleared (owner, 2026-10-02): the bot tries it again
after 1, 5, 10, 15, 30 and 60 minutes, then only Retry moves it on, and a league admin's Discard
drops it. A removal discarded is named to the league with its link and not counted.

These run the real change types the builder registers, through the confirmation the league
presses, on a database built by the migrations, with "now" pinned. Discord is a fake server whose
channels record what was deleted from them. Everything of the queue is imported inside a test or
a helper, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService
from tests.support.change_queue import (
    MEMBER_ID,
    SERVER_ID,
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    league_double,
    maybe_await,
    member_interaction,
    queued_log_lines,
    restart_queue,
    run_queue,
    seed_server,
    stopped_job,
    updated_reply,
)
from tests.support.teams import seed_team_instances

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
WHAT = "`/module disable results`"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
SUCCESS = "✅ Results & Standings module disabled."
NOTHING_CHANGED = "Nothing was changed: Results & Standings is still on."
COMPLETABLE = "the season can still be completed"
#: The rest of that sentence: some of the season's messages may remain, for removal by hand.
MAY_REMAIN = "may still be posted — delete them by hand"
#: The stop line's opening and the line after the hour, as the queue writes them.
STOPPED_AT = "❌ The queue is stopped at job #"
HOUR_LINE = (
    "❌ Job #{id} ({job}) still fails after an hour. The bot has stopped trying on its own: "
    "press Retry on its notice, or Discard."
)

RESULTS_CHANNEL_ID = 9001
STANDINGS_CHANNEL_ID = 9002
VERDICTS_CHANNEL_ID = 9003
AMEND_CHANNEL_ID = 9100


def _link(channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{SERVER_ID}/{channel_id}/{message_id}"


def _acknowledges(text: str) -> bool:
    """Whether *text* is the admin's acknowledgement: the switch-off under way, to be updated when
    it is done. The job number it also names is pinned in `test_change_queue.py`."""
    return (
        text.startswith("⏳ Turning Results & Standings off")
        and "This message will be updated when it is done" in text
        and "the log channel will say so." in text
    )


# ---------------------------------------------------------------------------
# The league's server
# ---------------------------------------------------------------------------


class _FakeChannel:
    """A channel recording each message deleted from it, and each attempt at one.

    An id in `refuse` will not delete, as for a bot that has lost its permissions there. A message
    deleted is gone: fetching it again raises `NotFound`, as Discord does.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.deleted_messages: list[int] = []
        self.attempts: list[int] = []
        self.deleted = False
        self.refuse: set[int] = set()

    async def fetch_message(self, message_id: int) -> Any:
        if message_id in self.deleted_messages:
            raise discord.NotFound(MagicMock(status=404), "Unknown Message")
        message = MagicMock()
        message.id = message_id

        async def _delete() -> None:
            self.attempts.append(message_id)
            if message_id in self.refuse:
                raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")
            self.deleted_messages.append(message_id)

        message.delete = _delete
        return message

    def get_partial_message(self, message_id: int) -> Any:
        message = MagicMock()
        message.id = message_id

        async def _delete() -> None:
            await (await self.fetch_message(message_id)).delete()

        message.delete = _delete
        return message

    def history(self, **_kwargs: Any) -> Any:
        async def _empty():
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    async def delete(self, reason: str | None = None) -> None:
        self.deleted = True


def _league(db_path: str, *, guild: bool = True) -> Any:
    """The bot: a real router, services and queue on *db_path*, and a fake server.

    `bot.channels` holds the server's channels; `bot.broken` names channels whose lookup raises
    an error that is not Discord's; `bot.clock` is the queue's "now".
    """
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.services.module_service import ModuleService

    bot = league_double(db_path)
    bot.user.id = 77
    bot.module_service = ModuleService(db_path)
    bot.season_service = SeasonService(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.channels = {
        cid: _FakeChannel(cid)
        for cid in (RESULTS_CHANNEL_ID, STANDINGS_CHANNEL_ID, VERDICTS_CHANNEL_ID,
                    AMEND_CHANNEL_ID, 8000, 8001)
    }
    bot.broken = set()

    def _get_channel(cid: int) -> Any:
        if cid in bot.broken:
            raise RuntimeError("the bot's own fault")
        return bot.channels.get(cid)

    server = MagicMock()
    server.id = SERVER_ID
    server.get_channel = MagicMock(side_effect=_get_channel)
    bot.server = server
    bot.get_guild = MagicMock(
        side_effect=lambda sid: server if guild and sid == SERVER_ID else None
    )
    bot.clock = Clock(NOW)
    attach_queue(bot, db_path, now=bot.clock)
    return bot


@pytest.fixture(autouse=True)
def _quiet_follow_ons(monkeypatch):
    """The hub has no panel to refresh here, unless a test says otherwise."""
    from leaguebot.core.services import hub_service

    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(return_value=None))


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _seed(tmp_path, *, rounds=("AWAITING_RESULTS",), stage: str = "ONGOING",
                attendance: bool = False, results_data: bool = True) -> SimpleNamespace:
    """A running season of one division, one round per status, each raced round's results posted.

    Round *i* has its results message 1000+i in the results channel, its standings messages
    2000+i and 3000+i in the standings channel, and an open submission channel 8000+i.
    """
    os.makedirs(str(tmp_path), exist_ok=True)
    db_path = os.path.join(str(tmp_path), "results_off.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (1, ?)", (int(attendance),)
        )
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-01-01', 'ACTIVE', 1, ?)",
            (stage,),
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier, status) "
            "VALUES (?, 'Division 1', 555, 1, 'ACTIVE')",
            (season_id,),
        )
        division_id = cur.lastrowid
        await seed_team_instances(db, division_id, 7)
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, RESULTS_CHANNEL_ID, STANDINGS_CHANNEL_ID),
        )
        if attendance:
            await db.execute(
                "INSERT INTO attendance_division_config "
                "(division_id, rsvp_channel_id, attendance_channel_id) VALUES (?, '71', '72')",
                (division_id,),
            )
        round_ids: list[int] = []
        for number, status in enumerate(rounds, start=1):
            cur = await db.execute(
                "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, "
                "format, status) VALUES (?, ?, 'Bahrain', ?, 'NORMAL', ?)",
                (division_id, number, f"2026-0{number}-01T12:00:00", status),
            )
            round_ids.append(cur.lastrowid)
        if results_data:
            for index, round_id in enumerate(round_ids):
                await _seed_results(db, round_id, division_id, index)
        await db.commit()
    return SimpleNamespace(db_path=db_path, division_id=division_id, round_ids=round_ids,
                           season_id=season_id)


async def _seed_results(db, round_id: int, division_id: int, index: int) -> None:
    cur = await db.execute(
        "INSERT INTO session_results "
        "(round_id, division_id, session_type, status, config_name, results_message_id) "
        "VALUES (?, ?, 'RACE', 'ACTIVE', '100%', ?)",
        (round_id, division_id, 1000 + index),
    )
    cur = await db.execute(
        "INSERT INTO race_session_results "
        "(session_result_id, driver_user_id, team_instance_id, finishing_position) "
        "VALUES (?, 42, 7, 1)",
        (cur.lastrowid,),
    )
    race_result_id = cur.lastrowid
    await db.execute(
        "INSERT INTO penalty_records (race_result_id, penalty_type, description, justification, "
        "applied_by, applied_at) VALUES (?, 'TIME', '5s', 'contact', 'steward', "
        "'2026-01-02T00:00:00')",
        (race_result_id,),
    )
    await db.execute(
        "INSERT INTO appeal_records (race_result_id, penalty_type, description, justification, "
        "submitted_by, submitted_at) VALUES (?, 'TIME', '5s', 'appealed', 'driver', "
        "'2026-01-03T00:00:00')",
        (race_result_id,),
    )
    await db.execute(
        "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
        "standing_position, total_points, standings_message_id, "
        "constructor_standings_message_id) VALUES (?, ?, 42, 1, 25, ?, ?)",
        (round_id, division_id, 2000 + index, 3000 + index),
    )
    await db.execute(
        "INSERT INTO team_standings_snapshots (round_id, division_id, team_instance_id, "
        "standing_position, total_points) VALUES (?, ?, 7, 1, 25)",
        (round_id, division_id),
    )
    await db.execute(
        "INSERT INTO round_submission_channels (round_id, channel_id, created_at, closed) "
        "VALUES (?, ?, '2026-01-01T00:00:00', 0)",
        (round_id, 8000 + index),
    )


async def _announce_verdicts(db_path: str) -> None:
    """The seeded penalty announced as message 5001 and the appeal as 6001."""
    async with get_connection(db_path) as db:
        for table, message_id in (("penalty_records", 5001), ("appeal_records", 6001)):
            await db.execute(
                f"UPDATE {table} SET announcement_message_id = ?, "  # noqa: S608
                "announcement_message_ids = ?, announcement_channel_id = ?",
                (str(message_id), json.dumps([message_id]), str(VERDICTS_CHANNEL_ID)),
            )
        await db.commit()


async def _closing_the_rounds_fails(db_path: str) -> None:
    """Make the closing of a round fail inside the switch-off's save: a trigger of the test's own."""
    await _set(
        db_path,
        "CREATE TRIGGER test_closing_fails BEFORE UPDATE OF status ON rounds "
        "WHEN NEW.status = 'FINAL' BEGIN SELECT RAISE(ABORT, 'closing failed'); END",
    )


async def _set(db_path: str, sql: str, params: tuple = ()) -> None:
    async with get_connection(db_path) as db:
        await db.execute(sql, params)
        await db.commit()


# ---------------------------------------------------------------------------
# Asking, and reading what happened
# ---------------------------------------------------------------------------


async def _confirm(bot: Any, *, cascade: bool = False, interaction: Any = None) -> Any:
    """Press the confirmation `/module disable results` showed, as the admin who asked."""
    from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    view = _ConfirmDisableResultsView(cog, MEMBER_ID, cascade_attendance=cascade)
    interaction = interaction or member_interaction(bot)
    await type(view).confirm(view, interaction, view.confirm)
    return interaction


async def _run_through_a_restart(bot: Any, *, steps: int) -> None:
    """Run *steps* steps, stop as the bot would, start again and run the rest."""
    await run_queue(bot, steps=steps)
    await restart_queue(bot)
    try:
        await run_queue(bot)
    finally:
        await maybe_await(bot.change_queue.stop())


async def _lines(bot: Any) -> list[str]:
    """Every log line written: those the log channel took, then those waiting on a retry."""
    return list(bot.log_channel.sent) + await queued_log_lines(bot.db_path)


async def _line_starting(bot: Any, head: str) -> str:
    found = [line for line in await _lines(bot) if line.startswith(head)]
    assert len(found) == 1, (head, await _lines(bot))
    return found[0]


async def _value(db_path: str, sql: str, params: tuple = ()) -> Any:
    async with get_connection(db_path) as db:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return None if row is None else row[0]


async def _flag(db_path: str) -> int:
    return await _value(db_path, "SELECT module_enabled FROM results_module_config WHERE id = 1")


async def _round_status(db_path: str, round_id: int) -> str:
    return await _value(db_path, "SELECT status FROM rounds WHERE id = ?", (round_id,))


async def _count(db_path: str, table: str) -> int:
    return await _value(db_path, f"SELECT COUNT(*) FROM {table}")  # noqa: S608


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT change_type FROM audit_entries ORDER BY id")
        return [row[0] for row in await cur.fetchall()]


async def _change(db_path: str, kind: str) -> dict[str, Any]:
    found = [row for row in await change_rows(db_path) if row["kind"] == kind]
    assert len(found) == 1, (kind, await change_rows(db_path))
    return found[0]


def _deleted(bot: Any, channel_id: int) -> list[int]:
    return sorted(bot.channels[channel_id].deleted_messages)


async def _stop_lines(bot: Any) -> list[str]:
    return [line for line in await _lines(bot) if line.startswith(STOPPED_AT)]


async def _discard_line(bot: Any, job_id: int) -> str:
    """The log line recording the admin's Discard of job #*job_id*, with what was not done."""
    return await _line_starting(bot, f"{NAMED} | Discard job #{job_id} | Discarded")


async def _stopped_change(db_path: str) -> dict[str, Any]:
    """The change whose job the queue is stopped at."""
    job = await stopped_job(db_path)
    assert job is not None, "the queue is not stopped"
    [change] = [row for row in await change_rows(db_path) if row["id"] == job["change_id"]]
    return change


# ---------------------------------------------------------------------------
# The switch-off, in one save — defect 8
# ---------------------------------------------------------------------------


async def test_a_stop_after_results_is_turned_off_leaves_the_season_completable(tmp_path):
    """Defect 8: a stop after the switch-off no longer strands rounds nothing can close."""
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot, steps=1)

    assert await _flag(seeded.db_path) == 0
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "FINAL"
    assert await _value(
        seeded.db_path, "SELECT status FROM divisions WHERE id = ?", (seeded.division_id,)
    ) == "FINISHED"
    assert _deleted(bot, RESULTS_CHANNEL_ID) == []

    await restart_queue(bot)
    try:
        await run_queue(bot)
    finally:
        await maybe_await(bot.change_queue.stop())

    assert _deleted(bot, RESULTS_CHANNEL_ID) == [1000]
    assert _deleted(bot, STANDINGS_CHANNEL_ID) == [2000, 3000]
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"


async def test_turning_results_off_and_closing_its_rounds_land_in_one_save(tmp_path, monkeypatch):
    """The flag, the erase and the closing of the rounds stand or fall together.

    And the save holds the only connection open: nothing the switch-off reaches opens one of
    its own, down to the season's move to Pending completion.
    """
    seeded = await _seed(tmp_path / "failing")
    await _closing_the_rounds_fails(seeded.db_path)
    bot = _league(seeded.db_path)
    await _confirm(bot)
    await run_queue(bot)

    assert await _flag(seeded.db_path) == 1
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "AWAITING_RESULTS"
    assert await _count(seeded.db_path, "session_results") == 1
    assert await _count(seeded.db_path, "penalty_records") == 1
    assert "MODULE_DISABLE" not in await _audit_types(seeded.db_path)

    clean = await _seed(tmp_path / "clean")
    bot = _league(clean.db_path)
    await _confirm(bot)
    real_connect = aiosqlite.connect
    open_now = {"open": 0, "most": 0}

    class _Counted:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._connection = real_connect(*args, **kwargs)

        async def __aenter__(self) -> Any:
            db = await self._connection.__aenter__()
            open_now["open"] += 1
            open_now["most"] = max(open_now["most"], open_now["open"])
            return db

        async def __aexit__(self, *exc: Any) -> Any:
            open_now["open"] -= 1
            return await self._connection.__aexit__(*exc)

        def __await__(self):
            return self._connection.__await__()

    with monkeypatch.context() as patched:
        patched.setattr(aiosqlite, "connect", _Counted)
        await run_queue(bot, steps=1)

    assert await _flag(clean.db_path) == 0
    assert open_now["most"] == 1


async def test_a_fault_before_anything_is_saved_says_nothing_was_changed(tmp_path):
    """The switch-off's save failing stops the queue at it, with nothing saved: the stop names the
    fault, the admin's reply says the request is stopped at that job, and results is still on."""
    seeded = await _seed(tmp_path)
    await _closing_the_rounds_fails(seeded.db_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)
    await run_queue(bot)

    job = await stopped_job(seeded.db_path)
    assert job is not None and job["name"] == "switch_off"
    [stop] = await _stop_lines(bot)
    assert f"for {WHAT} ({NAMED}) failed (IntegrityError)." in stop
    reply = updated_reply(interaction)
    assert reply.startswith("❌") and f"job #{job['id']}" in reply
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "RUNNING"
    assert await _flag(seeded.db_path) == 1
    assert await _count(seeded.db_path, "session_results") == 1


async def test_a_discarded_switch_off_says_nothing_was_changed(tmp_path):
    """A league admin discarding the switch-off whose save failed leaves results on and the season
    untouched, and the admin's reply says nothing was changed. The stop notice and the Discard line
    name the job as turning Results & Standings off."""
    seeded = await _seed(tmp_path)
    await _closing_the_rounds_fails(seeded.db_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)
    await run_queue(bot)
    job = await stopped_job(seeded.db_path)
    [stop] = await _stop_lines(bot)
    assert stop.startswith(
        f"{STOPPED_AT}{job['id']}: turning Results & Standings off for {WHAT} ({NAMED}) failed"
    )

    await discard_job(bot)

    assert (
        f"not done: turning Results & Standings off for {WHAT}"
        in await _discard_line(bot, job["id"])
    )
    assert NOTHING_CHANGED in updated_reply(interaction)
    assert COMPLETABLE not in updated_reply(interaction)
    assert await stopped_job(seeded.db_path) is None
    assert await _flag(seeded.db_path) == 1
    assert await _count(seeded.db_path, "session_results") == 1
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "AWAITING_RESULTS"


# ---------------------------------------------------------------------------
# Taking the season's messages down
# ---------------------------------------------------------------------------


async def test_a_stop_part_way_through_the_take_down_finishes_it_on_restart_and_counts_each_message_once(  # noqa: E501
    tmp_path,
):
    seeded = await _seed(tmp_path)
    await _announce_verdicts(seeded.db_path)
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await _run_through_a_restart(bot, steps=3)

    assert bot.channels[RESULTS_CHANNEL_ID].deleted_messages == [1000]
    assert _deleted(bot, STANDINGS_CHANNEL_ID) == [2000, 3000]
    assert _deleted(bot, VERDICTS_CHANNEL_ID) == [5001, 6001]
    closing = await _line_starting(bot, f"{NAMED} | /module disable results | Messages removed")
    assert "results and standings messages removed: 3" in closing
    assert "verdicts removed: 2" in closing


async def test_every_message_is_taken_down_by_the_ids_saved_with_the_switch_off(tmp_path):
    """The rows naming the messages are gone with the switch-off; the steps keep their ids."""
    seeded = await _seed(tmp_path)
    await _announce_verdicts(seeded.db_path)
    async with get_connection(seeded.db_path) as db:
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at, "
            "heads_sanctions) VALUES (?, ?, '7001', '2026-01-02T00:00:00', 0)",
            (seeded.round_ids[0], str(VERDICTS_CHANNEL_ID)),
        )
        await db.commit()
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot, steps=1)
    for table in ("session_results", "penalty_records", "appeal_records",
                  "driver_standings_snapshots"):
        assert await _count(seeded.db_path, table) == 0, table
    await run_queue(bot)

    assert _deleted(bot, RESULTS_CHANNEL_ID) == [1000]
    assert _deleted(bot, STANDINGS_CHANNEL_ID) == [2000, 3000]
    assert _deleted(bot, VERDICTS_CHANNEL_ID) == [5001, 6001, 7001]
    assert await _count(seeded.db_path, "verdict_banner_messages") == 0


async def test_a_removal_discord_refuses_stops_the_queue_and_once_discarded_is_linked_not_counted(
    tmp_path,
):
    """A removal Discord refuses stops the queue and is tried again a minute on, like any job (owner,
    2026-10-02, withdrawing "Try once"); once a league admin discards it, its message is linked for
    removal by hand and not counted. The stop notice and the Discard line name the job by what it
    removes and that message's link, so a manager knows what to fix before pressing Retry."""
    seeded = await _seed(tmp_path)
    await _announce_verdicts(seeded.db_path)
    bot = _league(seeded.db_path)
    bot.channels[VERDICTS_CHANNEL_ID].refuse = {5001}
    interaction = await _confirm(bot)

    await run_queue(bot)
    job = await stopped_job(seeded.db_path)
    assert job["name"] == "take_down"
    removal = f"removing the verdict ({_link(VERDICTS_CHANNEL_ID, 5001)})"
    [stop] = await _stop_lines(bot)
    assert stop.startswith(
        f"{STOPPED_AT}{job['id']}: {removal} for {WHAT} ({NAMED}) failed (Forbidden)."
    )
    bot.clock.advance(minutes=1)
    await run_queue(bot)
    assert bot.channels[VERDICTS_CHANNEL_ID].attempts.count(5001) == 2
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "RUNNING"

    await discard_job(bot)

    assert bot.channels[VERDICTS_CHANNEL_ID].attempts.count(5001) == 2
    assert f"not done: {removal} for {WHAT}" in await _discard_line(bot, job["id"])
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"
    reply = updated_reply(interaction)
    assert "1 verdict(s) removed" in reply
    assert (
        "⚠️ 1 message(s) could not be removed — delete them by hand:\n"
        + _link(VERDICTS_CHANNEL_ID, 5001)
    ) in reply


async def test_messages_left_standing_are_listed_in_the_reply_and_the_closing_line_with_their_links(  # noqa: E501
    tmp_path,
):
    """Two removals Discord refuses each stop the queue; once a league admin discards both, both
    messages are linked in the admin's reply and in the closing line."""
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    bot.channels[RESULTS_CHANNEL_ID].refuse = {1000}
    bot.channels[STANDINGS_CHANNEL_ID].refuse = {2000}
    interaction = await _confirm(bot)

    await run_queue(bot)
    await discard_job(bot)
    await discard_job(bot)

    links = {_link(RESULTS_CHANNEL_ID, 1000), _link(STANDINGS_CHANNEL_ID, 2000)}
    reply = updated_reply(interaction)
    head = "⚠️ 2 message(s) could not be removed — delete them by hand:\n"
    assert head in reply
    assert set(reply.split(head, 1)[1].splitlines()[:2]) == links
    closing = await _line_starting(bot, f"{NAMED} | /module disable results | Messages removed")
    tail = closing.split("\n  left standing, to delete by hand: 2\n", 1)[1]
    assert {line.strip() for line in tail.splitlines()[:2]} == links
    switch_off = await _line_starting(bot, f"{NAMED} | /module disable results | Success")
    assert "left standing" not in switch_off


async def test_the_closing_line_counts_what_was_removed(tmp_path):
    seeded = await _seed(tmp_path)
    await _announce_verdicts(seeded.db_path)
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot)

    closing = await _line_starting(bot, f"{NAMED} | /module disable results | Messages removed")
    assert closing.startswith(
        f"{NAMED} | /module disable results | Messages removed\n"
        "  results and standings messages removed: 3\n"
        "  verdicts removed: 2"
    )
    assert "left standing" not in closing
    switch_off = await _line_starting(bot, f"{NAMED} | /module disable results | Success")
    assert switch_off.startswith(
        f"{NAMED} | /module disable results | Success\n"
        "  season results deleted: 1 session results, 2 standings rows\n"
        "  rounds closed with no results: 1\n"
        "  messages to remove: 3 results and standings, 2 verdicts"
    )


async def test_a_fault_in_the_take_down_says_the_season_can_still_be_completed(tmp_path):
    """A removal failing on the bot's own fault stops the queue after the switch-off; once a league
    admin discards it, the reply says the season can still be completed and links its message."""
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    bot.broken = {RESULTS_CHANNEL_ID}
    interaction = await _confirm(bot)

    await run_queue(bot)

    assert (await stopped_job(seeded.db_path))["name"] == "take_down"
    [stop] = await _stop_lines(bot)
    assert "failed (RuntimeError)." in stop
    assert await _flag(seeded.db_path) == 0
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "FINAL"

    await discard_job(bot)

    reply = updated_reply(interaction)
    assert COMPLETABLE in reply
    assert _link(RESULTS_CHANNEL_ID, 1000) in reply
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"


async def test_a_discard_after_the_switch_off_says_the_season_can_still_be_completed(
    tmp_path, monkeypatch,
):
    """The closing job failing after every message is down stops the queue; once a league admin
    discards it, the admin's reply still says the season can still be completed and that some of
    its messages may remain for removal by hand, and counts what the removals did take down. The
    stop notice and the Discard line name the job as counting what was removed."""
    from leaguebot.results.services import results_off_change

    monkeypatch.setattr(
        results_off_change, "_forget_banners_on", AsyncMock(side_effect=RuntimeError("stuck"))
    )
    seeded = await _seed(tmp_path)
    await _announce_verdicts(seeded.db_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)
    await run_queue(bot)
    job = await stopped_job(seeded.db_path)
    assert job["name"] == "close"
    [stop] = await _stop_lines(bot)
    assert stop.startswith(
        f"{STOPPED_AT}{job['id']}: counting what was removed for {WHAT} ({NAMED}) failed "
        "(RuntimeError)."
    )

    await discard_job(bot)

    assert (
        f"not done: counting what was removed for {WHAT}" in await _discard_line(bot, job["id"])
    )

    reply = updated_reply(interaction)
    assert COMPLETABLE in reply
    assert MAY_REMAIN in reply
    assert NOTHING_CHANGED not in reply
    assert "3 results and standings message(s) and 2 verdict(s) removed" in reply
    assert await stopped_job(seeded.db_path) is None
    assert await _flag(seeded.db_path) == 0
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "FINAL"


# ---------------------------------------------------------------------------
# The follow-on changes
# ---------------------------------------------------------------------------


async def test_the_season_is_wound_down_as_a_change_of_its_own(tmp_path, monkeypatch):
    """A season with placements still open has the wind-down's Discord work left to do."""
    from leaguebot.core.services import season_lifecycle_service

    wind_down = AsyncMock(return_value=[])
    monkeypatch.setattr(season_lifecycle_service, "close_window_for_wind_down", wind_down)
    seeded = await _seed(tmp_path, stage="ONGOING_PLACEMENTS")
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot)

    change = await _change(seeded.db_path, "season.wind_down")
    assert change["state"] == "DONE"
    assert change["origin"] == "BOT"
    assert change["what"] == f"Winding the season down after {WHAT}"
    wind_down.assert_awaited_once()


async def test_a_season_that_cannot_be_wound_down_is_reported_and_the_switch_off_stands(
    tmp_path, monkeypatch,
):
    """The wind-down failing stops the queue at its job, named as winding the season down; the
    switch-off, done before it, stands, and the admin's reply is its success."""
    from leaguebot.core.services import season_lifecycle_service

    monkeypatch.setattr(
        season_lifecycle_service, "close_window_for_wind_down",
        AsyncMock(side_effect=RuntimeError("stuck")),
    )
    seeded = await _seed(tmp_path, stage="ONGOING_PLACEMENTS")
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)

    await run_queue(bot)

    [stop] = await _stop_lines(bot)
    assert "winding the season down" in stop
    assert "failed (RuntimeError)." in stop
    assert (await _stopped_change(seeded.db_path))["kind"] == "season.wind_down"
    assert not any("not done" in line for line in await _lines(bot))
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"
    assert await _flag(seeded.db_path) == 0
    assert await _round_status(seeded.db_path, seeded.round_ids[0]) == "FINAL"
    assert updated_reply(interaction).startswith(SUCCESS)


async def test_a_wind_down_discord_keeps_failing_says_only_retry_continues_after_the_hour(
    tmp_path, monkeypatch,
):
    """Discord failing the wind-down stops the queue: the bot tries it again 1, 5, 10, 15, 30 and
    60 minutes after the first failure, then writes one ❌ line saying only Retry continues, and
    tries it no more."""
    from leaguebot.core.services import season_lifecycle_service

    monkeypatch.setattr(
        season_lifecycle_service, "close_window_for_wind_down",
        AsyncMock(side_effect=http_error(discord.Forbidden, status=403, text="Missing Access")),
    )
    seeded = await _seed(tmp_path, stage="ONGOING_PLACEMENTS")
    bot = _league(seeded.db_path)
    await _confirm(bot)
    await run_queue(bot)

    def _after_the_hour(lines: list[str]) -> list[str]:
        return [line for line in lines if "still fails after an hour" in line]

    for minutes in (1, 5, 10, 15, 30):
        bot.clock.now = NOW + timedelta(minutes=minutes)
        await run_queue(bot)
        assert _after_the_hour(await _lines(bot)) == []
    bot.clock.now = NOW + timedelta(minutes=60)
    await run_queue(bot)

    job = await stopped_job(seeded.db_path)
    hour = HOUR_LINE.format(id=job["id"], job="winding the season down")
    assert [line.split("\n")[0] for line in _after_the_hour(await _lines(bot))] == [hour]
    assert job["next_try_at"] is None
    assert job["tries"] == 7
    bot.clock.now = NOW + timedelta(days=1)
    await run_queue(bot)
    assert (await stopped_job(seeded.db_path))["tries"] == 7
    assert (await _change(seeded.db_path, "season.wind_down"))["state"] == "RUNNING"
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"


async def test_the_hub_is_refreshed_once_the_flag_is_down(tmp_path, monkeypatch):
    from leaguebot.core.services import hub_service

    seeded = await _seed(tmp_path)
    seen: list[int] = []

    async def _refresh(_bot: Any) -> None:
        seen.append(await _flag(seeded.db_path))

    monkeypatch.setattr(hub_service, "refresh_panel", _refresh)
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot)

    assert seen == [0]
    assert (await _change(seeded.db_path, "hub.refresh"))["state"] == "DONE"


async def test_a_hub_refresh_that_fails_names_the_refresh_not_the_switch_off(
    tmp_path, monkeypatch,
):
    """The hub refresh failing stops the queue at the refresh's own job, named as refreshing the
    hub panel; the switch-off, done before it, keeps its success."""
    from leaguebot.core.services import hub_service

    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(side_effect=RuntimeError("x")))
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)

    await run_queue(bot)

    [stop] = await _stop_lines(bot)
    assert "refreshing the hub panel" in stop
    assert "failed (RuntimeError)." in stop
    assert (await _stopped_change(seeded.db_path))["kind"] == "hub.refresh"
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"
    assert updated_reply(interaction).startswith(SUCCESS)


# ---------------------------------------------------------------------------
# Repeats and refusals
# ---------------------------------------------------------------------------


async def test_a_second_confirmation_before_the_first_starts_is_refused_as_a_repeat(tmp_path):
    seeded = await _seed(tmp_path, attendance=True)
    bot = _league(seeded.db_path)
    await _confirm(bot, cascade=True)

    second = await _confirm(bot, cascade=True)

    assert acknowledgement(second) == (
        "⚠️ Turning Results & Standings off has already been asked for and has not started "
        "yet, so it was not asked for again."
    )
    assert any(
        line.startswith(
            f"⛔ the “✅ Disable both” button refused for {NAMED} — Turning Results & "
            "Standings off has already been asked for and has not started yet, so it was not "
            "asked for again."
        )
        for line in await _lines(bot)
    )
    assert [row["kind"] for row in await change_rows(seeded.db_path)] == ["module.off:results"]


async def test_a_confirmation_after_results_was_turned_off_meanwhile_is_refused(tmp_path):
    """A warning left open while results was turned off by another press is refused at once."""
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    await _confirm(bot)
    await run_queue(bot)

    late = await _confirm(bot)

    assert acknowledgement(late) == "⚠️ Results & Standings module is already disabled."
    assert any(
        line.startswith(f"⛔ the “✅ Disable and delete the results” button refused for {NAMED}")
        for line in await _lines(bot)
    )
    kinds = [row["kind"] for row in await change_rows(seeded.db_path)]
    assert kinds.count("module.off:results") == 1


async def test_a_switch_off_asked_before_pending_completion_and_run_after_it_is_refused(tmp_path):
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)
    await _set(seeded.db_path, "UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?",
               (seeded.season_id,))

    await run_queue(bot)

    refusal = (
        "❌ No module can be disabled while the season is pending completion. "
        "Complete it with `/season complete` first."
    )
    assert updated_reply(interaction) == refusal
    assert any(line.startswith(f"⛔ {WHAT} refused for {NAMED} — ")
               for line in await _lines(bot))
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "REFUSED"
    assert await _flag(seeded.db_path) == 1
    assert await _count(seeded.db_path, "session_results") == 1


async def test_a_switch_off_asked_while_results_is_on_and_run_once_it_is_off_is_refused(tmp_path):
    """Two presses not repeats of each other, with and without the cascade, are both queued; the
    first turns results off, so the second is refused when it runs."""
    seeded = await _seed(tmp_path, attendance=True)
    bot = _league(seeded.db_path)
    first = await _confirm(bot, cascade=True)
    second = await _confirm(bot)

    await run_queue(bot)

    refusal = "⚠️ Results & Standings module is already disabled."
    assert _acknowledges(acknowledgement(second))
    assert updated_reply(second) == refusal
    assert f"⛔ {WHAT} refused for {NAMED} — Results & Standings module is already disabled." in (
        "\n".join(await _lines(bot))
    )
    assert updated_reply(first).startswith(SUCCESS)
    assert [row["state"] for row in await change_rows(seeded.db_path)
            if row["kind"] == "module.off:results"] == ["DONE", "REFUSED"]
    assert (await _audit_types(seeded.db_path)).count("MODULE_DISABLE") == 1


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


async def test_the_cascade_lands_in_the_switch_off_s_save(tmp_path):
    seeded = await _seed(tmp_path, attendance=True)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot, cascade=True)

    await run_queue(bot, steps=1)

    assert await _flag(seeded.db_path) == 0
    assert await _value(
        seeded.db_path, "SELECT module_enabled FROM attendance_config WHERE id = 1"
    ) == 0
    assert await _count(seeded.db_path, "attendance_division_config") == 0
    audits = await _audit_types(seeded.db_path)
    assert "MODULE_DISABLE" in audits
    assert "ATTENDANCE_MODULE_CASCADE_DISABLED" in audits
    assert any(line.startswith(f"{NAMED} | /module disable attendance | Success")
               for line in await _lines(bot))

    await run_queue(bot)
    assert updated_reply(interaction).startswith(
        f"{SUCCESS}\n✅ Attendance module disabled with it. Its per-division check-in and "
        "attendance channels have been cleared; its timings, penalties and thresholds are kept."
    )


async def test_a_cascade_with_attendance_already_off_turns_off_results_alone(tmp_path):
    seeded = await _seed(tmp_path, attendance=True)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot, cascade=True)
    await _set(seeded.db_path, "UPDATE attendance_config SET module_enabled = 0 WHERE id = 1")

    await run_queue(bot)

    assert await _flag(seeded.db_path) == 0
    assert "ATTENDANCE_MODULE_CASCADE_DISABLED" not in await _audit_types(seeded.db_path)
    assert not any("/module disable attendance" in line for line in await _lines(bot))
    reply = updated_reply(interaction)
    assert reply.startswith(SUCCESS)
    assert "Attendance module disabled with it" not in reply


# ---------------------------------------------------------------------------
# What the admin is told
# ---------------------------------------------------------------------------


async def test_the_admin_is_told_at_once_and_the_reply_is_updated_with_what_went(tmp_path):
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)

    assert _acknowledges(acknowledgement(interaction))
    interaction.response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_not_awaited()

    await run_queue(bot)

    assert updated_reply(interaction) == (
        f"{SUCCESS}\n🗑️ This season's results are gone: 1 session result(s) and 2 standings "
        "row(s) deleted, 3 results and standings message(s) and 0 verdict(s) removed, "
        "1 round(s) closed with no results.\nPoints configurations and division channels are kept."
    )


async def test_an_outcome_after_a_restart_is_left_to_the_log_channel(tmp_path):
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path)
    interaction = await _confirm(bot)

    await _run_through_a_restart(bot, steps=1)

    interaction.edit_original_response.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()
    await _line_starting(bot, f"{NAMED} | /module disable results | Success")
    await _line_starting(bot, f"{NAMED} | /module disable results | Messages removed")


async def test_a_server_out_of_cache_erases_the_rows_and_names_every_message_left_standing(
    tmp_path,
):
    """With the league's server out of the cache, each of the four removals (three messages and the
    round's submission channel) stops the queue; once a league admin discards each, the rows are
    erased and all three messages are linked."""
    seeded = await _seed(tmp_path)
    bot = _league(seeded.db_path, guild=False)
    interaction = await _confirm(bot)

    await run_queue(bot)
    discarded = 0
    while await stopped_job(seeded.db_path) is not None and discarded < 10:
        assert (await stopped_job(seeded.db_path))["name"] == "take_down"
        await discard_job(bot)
        discarded += 1

    assert discarded == 4
    assert await _count(seeded.db_path, "session_results") == 0
    assert await _count(seeded.db_path, "penalty_records") == 0
    reply = updated_reply(interaction)
    assert "0 results and standings message(s)" in reply
    head = "⚠️ 3 message(s) could not be removed — delete them by hand:\n"
    assert head in reply
    assert set(reply.split(head, 1)[1].splitlines()[:3]) == {
        _link(RESULTS_CHANNEL_ID, 1000),
        _link(STANDINGS_CHANNEL_ID, 2000),
        _link(STANDINGS_CHANNEL_ID, 3000),
    }
    closing = await _line_starting(bot, f"{NAMED} | /module disable results | Messages removed")
    assert "left standing, to delete by hand: 3" in closing
    assert (await _change(seeded.db_path, "module.off:results"))["state"] == "DONE"


async def test_an_open_amendment_is_forgotten_with_the_season_and_its_channel_taken_down(
    tmp_path,
):
    """An amendment left behind would name a session the switch-off erased (#345)."""
    seeded = await _seed(tmp_path, rounds=("FINAL",))
    await _set(
        seeded.db_path,
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
        "pre_amendment_state, expires_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', "
        "'2026-01-01T00:00:00', '{}', '2026-01-01T00:30:00')",
        (seeded.round_ids[0], AMEND_CHANNEL_ID),
    )
    bot = _league(seeded.db_path)
    await _confirm(bot)

    await run_queue(bot, steps=1)
    assert await _count(seeded.db_path, "round_amend_channels") == 0
    assert bot.channels[AMEND_CHANNEL_ID].deleted is False

    await run_queue(bot)
    assert bot.channels[AMEND_CHANNEL_ID].deleted is True
