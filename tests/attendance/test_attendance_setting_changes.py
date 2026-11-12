"""The eight `/attendance config` settings as changes on the queue: the rules a setting is judged by.

Each of the eight is a `Setting` on the change queue (#285), as the attendance channels are
(`test_attendance_channel_commands.py`): its check judges a request at the ask and again as the
change starts, on the database as the changes ahead of it left it. Three rules compare a setting
with something else, and the cog once judged them alone, at the ask, from a configuration read
before the save: the timing invariant (the first notice before the last, the last before the
deadline), auto-sack and auto-reserve never both on, and the three timings fixed once a season's
placements are confirmed. A change ahead on the queue, or a season confirmed meanwhile, could
break them unseen.

The three rules' tests ask each change with no interaction, so no command judges it: the change
is asked while the rule holds, the rule is broken while it waits, and the queue is run. The change
is refused as it starts, in the words the command always used, and nothing is saved: the setting
stands as it was and no audit entry is written.

The rest run each command as a manager types it, and pin what a league sees of the eight, which
the queue leaves as it was: the manager is acknowledged at once and nothing is written until the
queue runs the change; the change then writes the value, its `ATTENDANCE_CONFIG_SET` entry and its
log line in one save, stamped with the queue's clock, and the acknowledgement is updated to the
reply each command always gave; a value already held writes nothing and says so; a fault in the
save saves none of the three and the lane goes on; and every refusal keeps its words, its one line
and saves nothing. All run the bot the entry point builds, with the real queue and "now" pinned.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.core.utils.log_lines import refusal_line
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    change_rows,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    step_rows,
)
from tests.support.season_league import set_module_flag
from tests.support.settings_league import audit_rows, fault_in, last_said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.undecorate import undecorate

ACTOR_ID = 77
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The manager asking, as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Manager (`<@{ACTOR_ID}>`)"

#: Each setting's command, by the kind of change it asks of the queue. Each change carries its
#: value as `{"value": ...}`, a threshold's 0 carried as None, as the command stores it.
KINDS = {
    "rsvp-notice": "attendance.setting.rsvp_notice",
    "rsvp-last-notice": "attendance.setting.rsvp_last_notice",
    "rsvp-deadline": "attendance.setting.rsvp_deadline",
    "no-rsvp-penalty": "attendance.setting.no_rsvp_penalty",
    "absent-penalty": "attendance.setting.absent_penalty",
    "no-show-penalty": "attendance.setting.no_show_penalty",
    "autosack": "attendance.setting.autosack",
    "autoreserve": "attendance.setting.autoreserve",
}

#: Why each test fails until the eight settings are changes on the queue whose check holds the rule.
NOT_IN_THE_SERVICE = (
    "#285: the eight settings are not yet changes on the queue, and the rule is judged by the "
    "cog alone, at the ask"
)

#: Why each test of a setting's run through the queue fails until the eight are changes on it.
NOT_ON_THE_QUEUE = (
    "#285: the eight settings are not yet changes on the queue: the command defers and saves "
    "through set_setting itself"
)


async def _league(tmp_path, **config: Any):
    """The league's bot: attendance on, its configuration the packaged defaults (notice 5 days,
    last notice 24 hours, deadline 2 hours, no threshold) bar *config*, no season confirmed, and
    the real queue with "now" pinned."""
    db_path = os.path.join(str(tmp_path), "attendance_settings.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    await set_module_flag(db_path, "attendance", True)
    await _write(db_path, **config)
    bot = await league_bot(db_path)
    league_queue(bot, db_path, now=Clock(NOW))
    return bot, db_path


async def _write(db_path: str, **config: Any) -> None:
    """Set attendance's settings as a change ahead on the queue would leave them."""
    async with get_connection(db_path) as db:
        for column, value in config.items():
            await db.execute(f"UPDATE attendance_config SET {column} = ?", (value,))  # noqa: S608
        await db.commit()


async def _confirm_a_season(db_path: str) -> None:
    """Season 1, its placements confirmed: ACTIVE, racing (stage ONGOING)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE', 'ONGOING')"
        )
        await db.commit()


async def _ask(bot, command: str, value: int | None) -> int | None:
    """Manager 77 asks `/attendance config <command>` for *value* through the queue, with no
    interaction: no command judges it."""
    return await bot.change_queue.ask(
        KINDS[command], {"value": value},
        actor=member(ACTOR_ID, "Manager", "Manager#0001"),
        what=f"`/attendance config {command}`",
    )


async def _setting(db_path: str, column: str) -> Any:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT {column} FROM attendance_config")  # noqa: S608
        row = await cursor.fetchone()
    return row[0]


async def _assert_refused(bot, db_path: str, command: str, reply: str, column: str,
                          held: Any) -> None:
    """The change ended refused, the refusal recorded in the command's words, the setting still
    *held*, and no audit entry written."""
    assert [row["state"] for row in await change_rows(db_path)] == ["REFUSED"]
    first_lines = [unruled(str(line)).split("\n")[0] for line in bot.log_channel.sent]
    assert refusal_line(NAMED, f"`/attendance config {command}`", reply.split(" ", 1)[1]) in (
        first_lines
    )
    assert await _setting(db_path, column) == held
    assert await audit_rows(db_path) == []


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_a_timing_that_breaks_the_invariant(tmp_path):
    """`/attendance config rsvp-deadline 12` is asked while the last notice stands at 24 hours,
    so the deadline falls before it; before it runs, the last notice is moved to 6 hours. It is
    refused as it starts, saying the last notice must be greater than the deadline, and the
    deadline stays at 2 hours."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "rsvp-deadline", 12) is not None
    await _write(db_path, rsvp_last_notice_hours=6)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "rsvp-deadline",
        "❌ `rsvp_last_notice_hours` (6h) must be greater than `rsvp_deadline_hours` (12h).",
        "rsvp_deadline_hours", 2,
    )


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_an_autosack_and_autoreserve_that_overlap(tmp_path):
    """`/attendance config autosack 3` is asked while auto-reserve is off; before it runs,
    auto-reserve is set to 2. It is refused as it starts, telling the manager to disable
    auto-reserve first, and auto-sack stays off."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "autosack", 3) is not None
    await _write(db_path, autoreserve_threshold=2)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "autosack",
        "❌ Cannot set auto-sack while auto-reserve is active. "
        "Disable auto-reserve first (`/attendance config autoreserve 0`).",
        "autosack_threshold", None,
    )


@pytest.mark.xfail(strict=True, reason=NOT_IN_THE_SERVICE)
async def test_set_setting_refuses_a_timing_change_once_a_season_is_confirmed(tmp_path):
    """`/attendance config rsvp-notice 7` is asked while no season is confirmed; before it runs,
    Season 1's placements are confirmed. It is refused as it starts, saying attendance's
    configuration cannot be changed once a season's placements are confirmed, and the notice
    stays at 5 days."""
    bot, db_path = await _league(tmp_path)

    assert await _ask(bot, "rsvp-notice", 7) is not None
    await _confirm_a_season(db_path)
    await run_queue(bot)

    await _assert_refused(
        bot, db_path, "rsvp-notice",
        "❌ Attendance configuration cannot be changed once a season's placements are confirmed.",
        "rsvp_notice_days", 5,
    )


# ---------------------------------------------------------------------------
# The eight commands, as a manager runs them
# ---------------------------------------------------------------------------


def _interaction(bot, command: str):
    """Manager 77's interaction for `/attendance config <command>`, recording every word they
    are told."""
    return member_asks(
        bot, f"attendance config {command}", user=member(ACTOR_ID, "Manager", "Manager#0001")
    )


async def _run(bot, command: str, value: int, *, run: bool = True):
    """Manager 77 runs `/attendance config <command> <value>`, then the queue is run unless
    *run* is False; the interaction is returned."""
    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = bot
    interaction = _interaction(bot, command)
    method = "config_" + command.replace("-", "_")
    await undecorate(getattr(AttendanceCog, method))(cog, interaction, value)
    if run:
        await run_queue(bot)
    return interaction


async def _config(db_path: str) -> dict[str, Any]:
    """Attendance's whole configuration row, as the database holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM attendance_config")
        return dict(await cursor.fetchone())


def _lines(bot) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in bot.log_channel.sent]


_AUTOSACK_SET = (
    "✅ Auto-sack threshold set to **8** point(s).\n"
    "A driver who reaches it is removed from **every seat in every division**, whichever "
    "division's points carried them over. To drop a driver to reserve only in the division where "
    "they missed rounds, use `/attendance config autoreserve` instead."
)

#: Each command's change from the packaged defaults (bar *start*): the command, the settings it
#: starts from, the value typed, the column, the value replaced, the value stored, the line's
#: detail and the reply. A threshold's 0 disables it and is stored as None; a penalty's 0 is a
#: value, stored as 0.
SUCCESSES = [
    ("rsvp-notice", {}, 7, "rsvp_notice_days", 5, 7,
     "RSVP notice: 7 day(s) (was 5 day(s))",
     "✅ RSVP notice set to **7** day(s) before the race."),
    ("rsvp-last-notice", {}, 12, "rsvp_last_notice_hours", 24, 12,
     "last RSVP reminder: 12 hour(s) (was 24 hour(s))",
     "✅ Last RSVP reminder set to **12** hour(s) before the race."),
    ("rsvp-last-notice", {}, 0, "rsvp_last_notice_hours", 24, 0,
     "last RSVP reminder: disabled (was 24 hour(s))",
     "✅ Last RSVP reminder **disabled** (set to 0)."),
    ("rsvp-deadline", {}, 3, "rsvp_deadline_hours", 2, 3,
     "RSVP deadline: 3 hour(s) (was 2 hour(s))",
     "✅ RSVP deadline set to **3** hour(s) before the race."),
    ("no-rsvp-penalty", {}, 2, "no_rsvp_penalty", 1, 2,
     "no-RSVP penalty: 2 point(s) (was 1 point(s))",
     "✅ No-RSVP penalty set to **2** point(s)."),
    ("absent-penalty", {}, 3, "absent_penalty", 1, 3,
     "absent penalty: 3 point(s) (was 1 point(s))",
     "✅ Absent penalty set to **3** point(s)."),
    ("no-show-penalty", {}, 0, "no_show_penalty", 1, 0,
     "no-show penalty: 0 point(s) (was 1 point(s))",
     "✅ No-show penalty set to **0** point(s)."),
    ("autosack", {}, 8, "autosack_threshold", None, 8,
     "auto-sack threshold: 8 point(s) (was disabled)", _AUTOSACK_SET),
    ("autosack", {"autosack_threshold": 8}, 0, "autosack_threshold", 8, None,
     "auto-sack threshold: disabled (was 8 point(s))", "✅ Auto-sack **disabled**."),
    ("autoreserve", {}, 5, "autoreserve_threshold", None, 5,
     "auto-reserve threshold: 5 point(s) (was disabled)",
     "✅ Auto-reserve threshold set to **5** point(s)."),
    ("autoreserve", {"autoreserve_threshold": 5}, 0, "autoreserve_threshold", 5, None,
     "auto-reserve threshold: disabled (was 5 point(s))", "✅ Auto-reserve **disabled**."),
]


@pytest.mark.xfail(strict=True, reason=NOT_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "command,start,value,column,old,new,detail,reply",
    SUCCESSES,
    ids=[f"{case[0]}-{case[2]}" for case in SUCCESSES],
)
async def test_each_setting_is_saved_through_the_queue_with_its_audit_entry_line_and_reply(
    tmp_path, command, start, value, column, old, new, detail, reply
):
    """Attendance is on, its settings the packaged defaults bar *start*, and no season is
    confirmed. Manager 77 runs the command for *value* and the queue is run. The change asked is
    the command's own kind; the configuration row is as it was but for the one column, now *new*;
    one `ATTENDANCE_CONFIG_SET` entry records the manager, no division, the column from *old* to
    *new* (None for a disabled threshold) and the queue's clock; the log channel's only line is
    the Success line with the value and the one it replaced; and the manager's reply reads, word
    for word, as the command always answered."""
    bot, db_path = await _league(tmp_path, **start)
    before = await _config(db_path)

    interaction = await _run(bot, command, value)

    assert [row["kind"] for row in await change_rows(db_path)] == [KINDS[command]]
    assert await _config(db_path) == {**before, column: new}
    [entry] = await audit_rows(db_path)
    assert (entry["change_type"], entry["actor_id"], entry["actor_name"]) == (
        "ATTENDANCE_CONFIG_SET", ACTOR_ID, "Manager#0001"
    )
    assert entry["division_id"] is None
    assert (entry["old_value"], entry["new_value"]) == ({column: old}, {column: new})
    assert entry["timestamp"] == NOW.isoformat()
    assert _lines(bot) == [f"{NAMED} | /attendance config {command} | Success\n  {detail}"]
    assert last_said(interaction) == reply


#: One change for each command, the first of `SUCCESSES` for it.
FIRSTS = {case[0]: case for case in reversed(SUCCESSES)}


@pytest.mark.xfail(strict=True, reason=NOT_ON_THE_QUEUE)
@pytest.mark.parametrize("command", sorted(KINDS))
async def test_the_manager_is_acknowledged_before_anything_is_written(tmp_path, command):
    """Manager 77 runs the command and the queue is not run. They are answered at once with the
    queue's acknowledgement, naming the change's first job, and nothing is deferred; nothing is
    written until the queue runs the change: the configuration and the audit stand as they
    were."""
    bot, db_path = await _league(tmp_path)
    before = await _config(db_path)

    interaction = await _run(bot, command, FIRSTS[command][2], run=False)

    first_job = (await step_rows(db_path))[0]["id"]
    said = acknowledgement(interaction)
    assert said.startswith("⏳ ") and said.endswith(f"It begins with job #{first_job}.")
    interaction.response.defer.assert_not_awaited()
    assert await _config(db_path) == before
    assert await audit_rows(db_path) == []


#: Each command given the value the packaged defaults already hold: the command, the value
#: typed, and the line's detail, which names the setting and the value as the reply does.
UNCHANGED = [
    ("rsvp-notice", 5, "RSVP notice: 5 day(s)"),
    ("rsvp-last-notice", 24, "last RSVP reminder: 24 hour(s)"),
    ("rsvp-deadline", 2, "RSVP deadline: 2 hour(s)"),
    ("no-rsvp-penalty", 1, "no-RSVP penalty: 1 point(s)"),
    ("absent-penalty", 1, "absent penalty: 1 point(s)"),
    ("no-show-penalty", 1, "no-show penalty: 1 point(s)"),
    ("autosack", 0, "auto-sack threshold: disabled"),
    ("autoreserve", 0, "auto-reserve threshold: disabled"),
]


@pytest.mark.parametrize("command,value,detail", UNCHANGED, ids=[c[0] for c in UNCHANGED])
async def test_a_value_already_held_changes_nothing_and_says_so(tmp_path, command, value, detail):
    """Attendance is on with the packaged defaults, and manager 77 runs the command for the value
    the setting already holds (a threshold's 0 for one already disabled); the queue is run. The
    configuration stands as it was, no audit entry is written, the reply says nothing changed and
    names the value held, and the log channel's only line is the "Nothing changed" line."""
    bot, db_path = await _league(tmp_path)
    before = await _config(db_path)

    interaction = await _run(bot, command, value)

    name, shown = detail.split(": ", 1)
    assert await _config(db_path) == before
    assert await audit_rows(db_path) == []
    assert last_said(interaction) == f"ℹ️ Nothing changed: the {name} is already **{shown}**."
    assert _lines(bot) == [f"{NAMED} | /attendance config {command} | Nothing changed\n  {detail}"]


@pytest.mark.xfail(strict=True, reason=NOT_ON_THE_QUEUE)
async def test_a_fault_in_a_setting_s_save_saves_nothing_and_the_lane_goes_on(tmp_path):
    """Manager 77 runs `/attendance config rsvp-notice 7`, and the audit entry's write faults as
    the queue runs it; `/attendance config absent-penalty 3` is asked behind it and the queue run
    again. The notice stays at 5 days, the manager is told nothing was saved and to run the command
    again, one failure line is written and no Success line for the notice; the penalty behind it
    is saved, with its one audit entry, and its reply is the command's own."""
    bot, db_path = await _league(tmp_path)

    asked = await _run(bot, "rsvp-notice", 7, run=False)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(bot, steps=1)
    behind = await _run(bot, "absent-penalty", 3)

    what = "`/attendance config rsvp-notice`"
    assert last_said(asked) == failure_reply(
        what, "Nothing was saved. Run `/attendance config rsvp-notice` again to retry."
    )
    assert await _setting(db_path, "rsvp_notice_days") == 5
    assert await _setting(db_path, "absent_penalty") == 3
    assert [entry["new_value"] for entry in await audit_rows(db_path)] == [{"absent_penalty": 3}]
    lines = _lines(bot)
    assert any(line.startswith(f"❌ {what} failed for {NAMED} — ") for line in lines)
    assert not any("RSVP notice: 7" in line for line in lines)
    assert last_said(behind) == "✅ Absent penalty set to **3** point(s)."


async def _attendance_off(db_path: str) -> None:
    await set_module_flag(db_path, "attendance", False)


#: Every way a command is refused: the command, the value typed, the settings it starts from,
#: what else stands (the module off, or a season confirmed), and the reason the reply and the
#: line must carry.
REFUSALS = [
    *[
        (command, FIRSTS[command][2], {}, _attendance_off, "The Attendance module is not enabled.")
        for command in sorted(KINDS)
    ],
    *[
        (command, value, {}, _confirm_a_season, "Attendance configuration cannot be changed")
        for command, value in [("rsvp-notice", 7), ("rsvp-last-notice", 12), ("rsvp-deadline", 3)]
    ],
    ("rsvp-notice", 0, {}, None, "`rsvp_notice_days` must be at least 1."),
    ("rsvp-last-notice", -1, {}, None, "cannot be negative"),
    ("rsvp-deadline", -1, {}, None, "cannot be negative"),
    ("no-rsvp-penalty", -1, {}, None, "Penalty points cannot be negative."),
    ("absent-penalty", -1, {}, None, "Penalty points cannot be negative."),
    ("no-show-penalty", -1, {}, None, "Penalty points cannot be negative."),
    ("autosack", -1, {}, None, "Threshold cannot be negative."),
    ("autoreserve", -1, {}, None, "Threshold cannot be negative."),
    ("rsvp-notice", 1, {}, None, "must be greater than `rsvp_last_notice_hours`"),
    ("rsvp-last-notice", 200, {}, None, "must be greater than `rsvp_last_notice_hours` (200h)"),
    ("rsvp-deadline", 30, {}, None, "must be greater than `rsvp_deadline_hours` (30h)"),
    ("autosack", 8, {"autoreserve_threshold": 5}, None,
     "Cannot set auto-sack while auto-reserve is active."),
    ("autoreserve", 5, {"autosack_threshold": 8}, None,
     "Cannot set auto-reserve while auto-sack is active."),
]


def _refusal_id(case) -> str:
    command, value, start, meanwhile, _reason = case
    state = meanwhile.__name__.strip("_") if meanwhile else "-".join(start) or "value"
    return f"{command}-{value}-{state}"


@pytest.mark.parametrize(
    "command,value,start,state,reason", REFUSALS, ids=[_refusal_id(c) for c in REFUSALS]
)
async def test_every_refusal_of_a_setting_is_recorded_and_saves_nothing(
    tmp_path, command, value, start, state, reason
):
    """Attendance is on with the packaged defaults bar *start*, and *state* stands (attendance
    switched off, or Season 1's placements confirmed); manager 77 runs the command for *value*
    and the queue is run. The reply is a refusal carrying *reason*; the log channel's only line
    is that refusal, in the reply's words; no change is queued, no audit entry written, and the
    configuration stands as it was."""
    bot, db_path = await _league(tmp_path, **start)
    if state is not None:
        await state(db_path)
    before = await _config(db_path)

    interaction = await _run(bot, command, value)

    reply = last_said(interaction)
    assert reply.startswith("❌ ") and reason in reply
    assert _lines(bot) == [
        refusal_line(NAMED, f"`/attendance config {command}`", reply.split(" ", 1)[1])
    ]
    assert await change_rows(db_path) == []
    assert await audit_rows(db_path) == []
    assert await _config(db_path) == before
