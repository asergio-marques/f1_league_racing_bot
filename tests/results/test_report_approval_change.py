"""Approving a round's reports through the change queue: `results.reports.approve` (#439, slice 2).

`results/services/report_approval_change.py` carries stage one of a round's penalty review as a
change: `names` resolves the drivers' display names, `apply` saves everything the approval writes
in one save (the penalties and their records, the points, the round's attendance, the standings
snapshots of this round and every later one, and the status), then the republication under
"Post-Race Penalty Results", the take-down of the prompt and the approval message, one
`announce_verdict` per penalty, the appeals prompt, the attendance sheet and the sanction jobs, and
`close`, which writes `PENALTY_REVIEW_APPROVED`.

These tests drive the real change types the builder registers (`register_change_types`) on a real
queue, on a database built by the migrations, with "now" pinned. The approval is asked for as the
review's Approve control asks it: `bot.change_queue.ask("results.reports.approve", payload, ...)`,
the payload carrying the staged penalties and pardons as plain data (`to_payload`).

Attendance is reached through the hook the builder hands the change types,
`bot.attendance_after_review` (`AttendanceAfterReview`), here a double that checks the switch as the
real one does, records each call, and writes its record on the save it is handed into a table of
the test's own (`attendance_recorded`), so that the record is seen to land in the approval's save.

The Discord side is a fake channel per channel id, recording every send and delete in one list of
events, in order. Image generation is off.

Everything of the change type is imported inside a test, so this file collects while it is unbuilt.
What `test_repost_subsequent_standings.py::test_the_snapshots_are_recomputed_first` pinned is held
here by `test_the_snapshots_of_this_round_and_every_later_one_are_saved_with_the_penalties`.
"""
from __future__ import annotations

import itertools
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import StagedPardon
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    league_double,
    member_interaction,
    register,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    tier_member,
    updated_reply,
)
from tests.support.teams import seed_team_instances

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
KIND = "results.reports.approve"
SEASON_ID = 1
DIVISION_ID = 11
OTHER_DIVISION_ID = 12
ROUND_ID = 21
LATER_ROUND_ID = 22
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701
SUBMISSION_CHANNEL = 702
VERDICTS_CHANNEL = 704
AMEND_CHANNEL = 705
OLD_RESULTS = 8800
OLD_STANDINGS = 8801
PROMPT = 8900
APPROVAL = 8901
LEWIS, MAX = 101, 102
LEWIS_PROFILE, MAX_PROFILE = 31, 32
NOT_BUILT = "#439: the report approval is not yet a change on the queue"

_ids = itertools.count(9000)


# ---------------------------------------------------------------------------
# The Discord side
# ---------------------------------------------------------------------------


def _channel(channel_id: int, events: list[tuple[str, int, int]]) -> Any:
    """A text channel holding its messages by id, recording each send and delete in *events*.

    `send_fails`, an exception, refuses every send; `fail_when(content, kwargs)`, where it returns
    True, refuses that send alone. `messages` maps an id to the message standing, with its
    `content` and `view`; `seed(id, content)` places one as though posted earlier.
    """
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.name = f"channel-{channel_id}"
    channel.messages = {}
    channel.send_fails = None
    channel.fail_when = None

    def _message(message_id: int, content: str, view: Any = None) -> Any:
        message = MagicMock()
        message.id = message_id
        message.content = content
        message.view = view
        message.channel = channel
        message.jump_url = f"https://discord.test/{channel_id}/{message_id}"

        async def _edit(**changes: Any) -> Any:
            if message_id not in channel.messages:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            for name in ("content", "view"):
                if name in changes:
                    setattr(message, name, changes[name])
            events.append(("edit", channel_id, message_id))
            return message

        async def _delete(*_args: Any, **_kwargs: Any) -> None:
            if message_id not in channel.messages:
                raise http_error(discord.NotFound, status=404, text="Unknown Message")
            del channel.messages[message_id]
            events.append(("delete", channel_id, message_id))

        message.edit = AsyncMock(side_effect=_edit)
        message.delete = AsyncMock(side_effect=_delete)
        return message

    def seed(message_id: int, content: str = "earlier posting") -> None:
        channel.messages[message_id] = _message(message_id, content)

    async def _send(content: str = "", **kwargs: Any) -> Any:
        if channel.send_fails is not None:
            raise channel.send_fails
        if channel.fail_when is not None and channel.fail_when(content, kwargs):
            raise http_error(status=403, text="Missing Access")
        message = _message(next(_ids), content, kwargs.get("view"))
        channel.messages[message.id] = message
        events.append(("send", channel_id, message.id))
        return message

    async def _fetch(message_id: int) -> Any:
        if message_id not in channel.messages:
            raise http_error(discord.NotFound, status=404, text="Unknown Message")
        return channel.messages[message_id]

    def _partial(message_id: int) -> Any:
        return channel.messages.get(message_id) or _message(message_id, "")

    channel.seed = seed
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    channel.get_partial_message = MagicMock(side_effect=_partial)
    return channel


def _is_appeals_prompt(_content: str, kwargs: dict[str, Any]) -> bool:
    return type(kwargs.get("view")).__name__ == "AppealsReviewView"


class _Attendance:
    """The attendance hook the builder hands the change types, as a double.

    Every method does nothing while the league's attendance is off, as the real hook does.
    `candidates` are the drivers over a threshold; one whose profile is in `applied` is no longer
    owed. `record_fails`, `sheet_fails` (a list, one per failing try) and `apply_fails` (by
    profile) make a call raise.
    """

    def __init__(self, league: "_League") -> None:
        self.league = league
        self.calls: list[tuple[Any, ...]] = []
        self.candidates: list[dict[str, Any]] = []
        self.applied: set[int] = set()
        self.record_fails: Exception | None = None
        self.sheet_fails: list[Exception] = []
        self.apply_fails: dict[int, Exception] = {}

    def _calls(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]

    async def record_on(self, db: Any, round_id: int, division_id: int, pardons: Any,
                        now: Any) -> None:
        if not self.league.attendance_on:
            return
        if self.record_fails is not None:
            raise self.record_fails
        await db.execute(
            "INSERT INTO attendance_recorded (round_id, pardons) VALUES (?, ?)",
            (round_id, len(list(pardons))),
        )
        self.calls.append(("record_on", round_id, division_id))

    async def rewrite_pardons_on(self, db: Any, round_id: int, pardons: Any, now: Any) -> None:
        self.calls.append(("rewrite_pardons_on", round_id))

    async def recalculate_on(self, db: Any, round_id: int, division_id: int) -> None:
        self.calls.append(("recalculate_on", round_id, division_id))

    async def post_sheet(self, round_id: int, division_id: int, *, sanctioned: Any,
                         as_text: bool) -> None:
        if not self.league.attendance_on:
            return
        self.calls.append(("post_sheet", division_id, as_text))
        if self.sheet_fails:
            raise self.sheet_fails.pop(0)

    async def sanction_candidates(self, round_id: int, division_id: int) -> list[dict[str, Any]]:
        if not self.league.attendance_on:
            return []
        return [dict(c) for c in self.candidates if c["driver_profile_id"] not in self.applied]

    async def apply_sanction(self, round_id: int, division_id: int, candidate: dict[str, Any],
                             actor: Any) -> None:
        if not self.league.attendance_on:
            return
        profile = candidate["driver_profile_id"]
        self.calls.append(("apply_sanction", profile))
        failure = self.apply_fails.pop(profile, None)
        if failure is not None:
            raise failure
        self.applied.add(profile)

    async def announce_sanction(self, round_id: int, division_id: int, candidate: dict[str, Any],
                                *, as_text: bool) -> None:
        if not self.league.attendance_on:
            return
        self.calls.append(("announce_sanction", candidate["driver_profile_id"]))

    async def refresh_lineup(self, division_id: int) -> None:
        if not self.league.attendance_on:
            return
        self.calls.append(("refresh_lineup", division_id))

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        return "Repair the cause, then run `/attendance sync division:Pro round:3`."


def _candidate(profile: int, driver: int, sanction: str = "AUTORESERVE",
               other_divisions: tuple[int, ...] = ()) -> dict[str, Any]:
    return {"driver_profile_id": profile, "driver_user_id": driver, "sanction": sanction,
            "other_divisions": list(other_divisions)}


class _League:
    """The bot double with its queue and attendance hook, its channels, and what they saw."""

    def __init__(self, db_path: str, *, attendance: bool) -> None:
        self.db_path = db_path
        self.attendance_on = attendance
        self.events: list[tuple[str, int, int]] = []
        self.bot = league_double(db_path)
        self.channels = {
            cid: _channel(cid, self.events)
            for cid in (RESULTS_CHANNEL, STANDINGS_CHANNEL, SUBMISSION_CHANNEL, VERDICTS_CHANNEL,
                        AMEND_CHANNEL)
        }
        self.channels[RESULTS_CHANNEL].seed(OLD_RESULTS, "provisional results")
        self.channels[STANDINGS_CHANNEL].seed(OLD_STANDINGS, "provisional standings")
        self.channels[SUBMISSION_CHANNEL].seed(PROMPT, "penalty review")
        self.channels[SUBMISSION_CHANNEL].seed(APPROVAL, "approve these penalties?")
        known = {self.bot.log_channel.id: self.bot.log_channel,
                 self.bot.interaction_channel.id: self.bot.interaction_channel}

        def _get(cid: int) -> Any:
            return self.channels.get(cid) or known.get(cid)

        async def _fetch(cid: int) -> Any:
            found = _get(cid)
            if found is None:
                raise http_error(discord.NotFound, status=404, text="Unknown Channel")
            return found

        names = {LEWIS: "Lewis", MAX: "Max"}

        def _member(user_id: int) -> Any:
            if user_id not in names:
                return None
            person = MagicMock(spec=discord.Member)
            person.id = user_id
            person.display_name = names[user_id]
            person.mention = f"<@{user_id}>"
            return person

        guild = MagicMock(spec=discord.Guild)
        guild.id = 12408
        guild.get_channel = MagicMock(side_effect=_get)
        guild.get_member = MagicMock(side_effect=_member)
        guild.fetch_member = AsyncMock(side_effect=lambda uid: _member(uid))
        self.guild = guild
        self.bot.get_channel = MagicMock(side_effect=_get)
        self.bot.fetch_channel = AsyncMock(side_effect=_fetch)
        self.bot.get_guild = MagicMock(return_value=guild)
        self.bot.guilds = [guild]
        self.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
        self.bot.module_service.is_attendance_enabled = AsyncMock(
            side_effect=lambda: self.attendance_on
        )
        self.attendance = _Attendance(self)
        self.bot.attendance_after_review = self.attendance
        attach_queue(self.bot, db_path, now=NOW)

    def channel(self, cid: int) -> Any:
        return self.channels[cid]

    def sent_to(self, cid: int) -> list[int]:
        return [mid for kind, ch, mid in self.events if kind == "send" and ch == cid]

    def log(self) -> str:
        return "\n".join(self.bot.log_channel.sent)

    async def switch_attendance(self, on: bool) -> None:
        self.attendance_on = on
        async with get_connection(self.db_path) as db:
            await db.execute("UPDATE attendance_config SET module_enabled = ?", (int(on),))
            await db.commit()


async def _make_db(tmp_path: Any, *, attendance: bool, config_name: str | None) -> str:
    """Division 11 (Pro) of season 1. Its round 3 at Silverstone awaits its report verdicts, the
    review open in its submission channel with its prompt; Lewis (101) won its Feature Race and
    Max (102) came second, the results and the standings posted provisionally. Round 4 is final,
    its standings posted. Division 12 (Am) is another division of the season."""
    db_path = os.path.join(str(tmp_path), "report_approval.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for division_id, name, tier in ((DIVISION_ID, "Pro", 1), (OTHER_DIVISION_ID, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name, tier),
            )
        await seed_team_instances(db, DIVISION_ID, 3001)
        for round_id, number, track, status in (
            (ROUND_ID, 3, "Silverstone", "AWAITING_REPORT_VERDICTS"),
            (LATER_ROUND_ID, 4, "Spa", "FINAL"),
        ):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "track_name, status) VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', "
                "?, ?)",
                (round_id, DIVISION_ID, number, track, status),
            )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, reserves_in_standings, penalty_channel_id) "
            "VALUES (?, ?, ?, 1, ?)",
            (DIVISION_ID, RESULTS_CHANNEL, STANDINGS_CHANNEL, str(VERDICTS_CHANNEL)),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', ?, ?, ?)",
            (ROUND_ID, DIVISION_ID, config_name, OLD_RESULTS, f"[{OLD_RESULTS}]"),
        )
        for position, (profile, driver, points) in enumerate(
            ((LEWIS_PROFILE, LEWIS, 25), (MAX_PROFILE, MAX, 18)), start=1,
        ):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(driver)),
            )
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
                "driver_profile_id) VALUES (?, ?, 3001, ?, 'CLASSIFIED', ?, ?, ?)",
                (session.lastrowid, driver, position, 3_600_000 + position * 1000, points,
                 profile),
            )
        for round_id, message_id in ((ROUND_ID, OLD_STANDINGS), (LATER_ROUND_ID, None)):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id) "
                "VALUES (?, ?, ?, 1, 25, ?)",
                (round_id, DIVISION_ID, LEWIS, message_id),
            )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "in_penalty_review, results_posted, prompt_message_id) "
            "VALUES (?, ?, '2026-02-01T20:00:00+00:00', 1, 1, ?)",
            (ROUND_ID, SUBMISSION_CHANNEL, PROMPT),
        )
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (1, ?)",
            (int(attendance),),
        )
        await db.execute("CREATE TABLE attendance_recorded (round_id INTEGER, pardons INTEGER)")
        await db.commit()
    return db_path


async def _league(tmp_path: Any, *, attendance: bool = False,
                  config_name: str | None = "Standard") -> _League:
    db_path = await _make_db(tmp_path, attendance=attendance, config_name=config_name)
    return _League(db_path, attendance=attendance)


def _penalty(driver: int, seconds: int = 5) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=seconds,
        description="Corner cutting",
        justification="Turn 4, lap 12",
    )


def _pardon() -> StagedPardon:
    return StagedPardon(
        driver_user_id=MAX, driver_profile_id=MAX_PROFILE, attendance_id=41,
        pardon_type="NO_RSVP", justification="Told us in advance", grantor_id=77,
    )


def _payload(*, staged: Any = None, pardons: Any = (), prompt: int = PROMPT,
             approval: int | None = None) -> dict[str, Any]:
    staged = [_penalty(LEWIS), _penalty(MAX)] if staged is None else staged
    return {
        "round_id": ROUND_ID,
        "division_id": DIVISION_ID,
        "staged": [penalty.to_payload() for penalty in staged],
        "pardons": [pardon.to_payload() for pardon in pardons],
        "prompt_message_id": prompt,
        "approval_message_id": approval,
    }


async def _approve(league: _League, **payload: Any) -> Any:
    """Press Approve on round 3's review as the league manager Alex; the queue is not yet run.
    Gives Alex's interaction."""
    interaction = member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001"),
    )
    await league.bot.change_queue.ask(
        KIND, _payload(**payload), interaction=interaction,
        what="✅ Approve on round 3's penalty review",
    )
    return interaction


async def _run_until_done(league: _League, job: str) -> None:
    """Run the queue one job at a time until the first *job* is done, as a stop just after it."""
    for _ in range(40):
        rows = [row for row in await step_rows(league.db_path) if row["name"] == job]
        if rows and rows[0]["done_at"] is not None:
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"{job} was never done")


async def _stopped_at(league: _League) -> str | None:
    job = await stopped_job(league.db_path)
    return None if job is None else job["name"]


async def _blocker(league: _League) -> dict[str, Any]:
    """Ask for a change of the test's own whose one job fails while `holder["fail"]` is set, and
    run the queue, so that the queue is stopped at it. Gives the holder."""
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

    holder: dict[str, Any] = {"fail": True}

    async def act(_ctx: Any) -> Any:
        if holder["fail"]:
            raise StepFailedOnDiscord("Missing Access")
        return StepResult()

    async def check(_ctx: Any) -> Any:
        return Verdict.go()

    register(league.bot, ChangeType(
        kind="test.blocker",
        opening=(PlannedStep("block"),),
        steps={"block": Step("block", StepKind.ACT, act)},
        check=check,
        key=lambda _payload: "test.blocker",
        doing=lambda _payload: "Blocking the queue",
        outcome=lambda _ctx: "Unblocked.",
    ))
    await league.bot.change_queue.ask(
        "test.blocker", {}, interaction=member_interaction(league.bot), what="the blocker",
    )
    await run_queue(league.bot)
    assert await _stopped_at(league) == "block"
    return holder


async def _race(db_path: str) -> dict[int, dict[str, Any]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, postrace_time_penalties_ms, points_awarded "
            "FROM race_session_results"
        )
        return {row["driver_user_id"]: dict(row) for row in await cursor.fetchall()}


async def _one(db_path: str, sql: str, *args: Any) -> Any:
    async with get_connection(db_path) as db:
        row = await (await db.execute(sql, args)).fetchone()
    return None if row is None else row[0]


async def _penalty_records(db_path: str) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM penalty_records ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


async def _status(db_path: str) -> str:
    return await _one(db_path, "SELECT status FROM rounds WHERE id = ?", ROUND_ID)


async def _change(db_path: str, kind: str = KIND) -> list[dict[str, Any]]:
    return [row for row in await change_rows(db_path) if row["kind"] == kind]


def _points_fail() -> Any:
    """Every session's points, recalculated, raise as a fault in the bot would."""
    return patch(
        "leaguebot.results.services.result_submission_service._apply_points_in_tx",
        new=AsyncMock(side_effect=RuntimeError("the points could not be calculated")),
    )


def _appeals_prompts(league: _League) -> list[int]:
    return [
        mid for mid, message in league.channel(SUBMISSION_CHANNEL).messages.items()
        if type(message.view).__name__ == "AppealsReviewView"
    ]


# ---------------------------------------------------------------------------
# Defect 7: a stop part-way through
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stop_after_the_penalties_are_saved_finishes_the_approval_on_restart(tmp_path):
    league = await _league(tmp_path, attendance=True)
    await _approve(league)
    await _run_until_done(league, "apply")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    records = await _penalty_records(league.db_path)
    assert len(records) == 2
    announced = {int(record["announcement_message_id"]) for record in records}
    assert len(announced) == 2
    assert sorted(league.sent_to(VERDICTS_CHANNEL)) == sorted(announced)
    assert len(league.attendance._calls("record_on")) == 1
    assert len(_appeals_prompts(league)) == 1
    assert await _status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stop_before_the_save_leaves_nothing_applied_and_the_approval_runs_whole_on_restart(
    tmp_path,
):
    league = await _league(tmp_path)
    await _approve(league)
    await _run_until_done(league, "names")

    assert await _penalty_records(league.db_path) == []
    assert (await _race(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert len(await _penalty_records(league.db_path)) == 2
    race = await _race(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert race[MAX]["postrace_time_penalties_ms"] == 5000
    assert await _status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stop_part_way_through_the_reposts_finishes_them_and_announces_each_verdict_once(
    tmp_path,
):
    league = await _league(tmp_path)
    await _approve(league)
    await _run_until_done(league, "post_session_results")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    results = league.sent_to(RESULTS_CHANNEL)
    assert len(results) == 1
    assert set(league.channel(RESULTS_CHANNEL).messages) == set(results)
    assert len(league.sent_to(VERDICTS_CHANNEL)) == 2
    assert (await _race(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_penalties_and_attendance_are_saved_in_one_save(tmp_path):
    league = await _league(tmp_path, attendance=True)
    async with get_connection(league.db_path) as db:
        columns = [row["name"] for row in await (
            await db.execute("PRAGMA table_info(round_submission_channels)")
        ).fetchall()]
    assert "staged_penalties" not in columns

    league.attendance.record_fails = RuntimeError("the attendance could not be recorded")
    await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) == "apply"
    assert await _penalty_records(league.db_path) == []
    assert (await _race(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"

    league.attendance.record_fails = None
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    assert len(await _penalty_records(league.db_path)) == 2
    assert await _one(league.db_path, "SELECT COUNT(*) FROM attendance_recorded") == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_snapshots_of_this_round_and_every_later_one_are_saved_with_the_penalties(
    tmp_path,
):
    league = await _league(tmp_path)
    await _approve(league, staged=[_penalty(LEWIS, seconds=30)])
    await _run_until_done(league, "apply")

    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, driver_user_id, standing_position FROM driver_standings_snapshots "
            "ORDER BY round_id, standing_position"
        )
        rows = [tuple(row) for row in await cursor.fetchall()]
    for round_id in (ROUND_ID, LATER_ROUND_ID):
        leaders = [driver for rid, driver, position in rows if rid == round_id and position == 1]
        assert leaders == [MAX], f"round {round_id}'s standings were not recomputed in the save"


# ---------------------------------------------------------------------------
# Defect 3: points that cannot be recalculated
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_session_whose_points_cannot_be_recalculated_stops_the_queue_and_changes_nothing(
    tmp_path,
):
    league = await _league(tmp_path)
    with _points_fail():
        await _approve(league)
        await run_queue(league.bot)

    assert await _stopped_at(league) == "apply"
    assert await _penalty_records(league.db_path) == []
    race = await _race(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 0
    assert race[LEWIS]["points_awarded"] == 25
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert league.sent_to(RESULTS_CHANNEL) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_apply_says_nothing_was_changed_and_the_review_is_still_open(tmp_path):
    league = await _league(tmp_path)
    with _points_fail():
        interaction = await _approve(league)
        await run_queue(league.bot)
    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "Nothing was changed" in reply
    assert await _penalty_records(league.db_path) == []
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert await _one(
        league.db_path, "SELECT in_penalty_review FROM round_submission_channels"
    ) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_session_with_no_points_configuration_is_skipped_not_failed(tmp_path):
    league = await _league(tmp_path, config_name=None)
    with _points_fail() as scored:
        await _approve(league)
        await run_queue(league.bot)

    assert await _stopped_at(league) is None
    scored.assert_not_awaited()
    race = await _race(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert race[LEWIS]["points_awarded"] == 25
    assert await _status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


# ---------------------------------------------------------------------------
# Defect 4: the attendance sheet
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_attendance_sheet_discord_refuses_stops_the_queue_and_is_retried(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) == "attendance_sheet"
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    sheets = league.attendance._calls("post_sheet")
    assert [call[1] for call in sheets] == [DIVISION_ID, DIVISION_ID]
    assert sheets[-1][2] is True, "the retry did not post the sheet as text"
    assert "✅" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_sheet_is_named_with_attendance_sync_and_the_sanctions_still_run(
    tmp_path,
):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(MAX_PROFILE, MAX)]
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "attendance_sheet"

    await discard_job(league.bot)

    assert await _stopped_at(league) is None
    assert ("apply_sanction", MAX_PROFILE) in league.attendance.calls
    reply = updated_reply(interaction)
    assert "attendance sheet" in reply.lower()
    assert "/attendance sync" in reply
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_sheet_is_not_put_on_the_old_retry_queue(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) == "attendance_sheet"
    assert await _one(league.db_path, "SELECT COUNT(*) FROM pending_messages") == 0


# ---------------------------------------------------------------------------
# Defect 11: a repost Discord refuses
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_repost_discord_refuses_is_retried_and_the_approval_finishes_once_it_lands(
    tmp_path,
):
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(text="Discord is down")
    interaction = await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_session_results"
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages

    league.channel(RESULTS_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    assert (await _change(league.db_path))[0]["state"] == "DONE"
    assert "Round 3's reports are approved" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# Discards
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_verdict_is_named_incomplete_and_the_manager_is_told_to_post_it(
    tmp_path,
):
    league = await _league(tmp_path)
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    interaction = await _approve(league, staged=[_penalty(LEWIS)])
    await run_queue(league.bot)
    assert await _stopped_at(league) == "announce_verdict"

    await discard_job(league.bot)

    assert await _stopped_at(league) is None
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    reply = updated_reply(interaction)
    assert "Lewis" in reply or f"<@{LEWIS}>" in reply
    assert "verdict" in reply.lower()
    assert "post" in reply.lower()
    assert (await _penalty_records(league.db_path))[0]["announcement_message_id"] is None


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_apply_reopens_the_review_with_a_fresh_prompt(tmp_path):
    league = await _league(tmp_path)
    with _points_fail():
        interaction = await _approve(league)
        await run_queue(league.bot)
    await discard_job(league.bot)
    await run_queue(league.bot)

    reopened = await _change(league.db_path, "results.review.open")
    assert len(reopened) == 1
    assert reopened[0]["origin"] == "BOT"
    assert PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    fresh = await _one(league.db_path, "SELECT prompt_message_id FROM round_submission_channels")
    assert fresh != PROMPT and fresh in league.channel(SUBMISSION_CHANNEL).messages
    reply = updated_reply(interaction)
    assert "Nothing was changed" in reply
    assert "Approve" in reply

    await _approve(league, prompt=fresh)
    await run_queue(league.bot)
    assert await _status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert len(await _penalty_records(league.db_path)) == 2


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_appeals_prompt_is_posted_again_at_once(tmp_path):
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = _is_appeals_prompt
    await _approve(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_appeals_prompt"

    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert len(await _change(league.db_path, "results.appeals.open")) == 1
    prompts = _appeals_prompts(league)
    assert len(prompts) == 1
    assert await _one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]


# ---------------------------------------------------------------------------
# Sanctions, one job per driver
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_each_driver_over_a_threshold_is_sanctioned_and_announced_in_jobs_of_their_own(
    tmp_path,
):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(LEWIS_PROFILE, LEWIS),
                                    _candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    steps = await step_rows(league.db_path)
    assert len([row for row in steps if row["name"] == "apply_sanction"]) == 2
    assert len([row for row in steps if row["name"] == "announce_sanction"]) == 2
    assert [c for c in league.attendance.calls if c[0] == "apply_sanction"] == [
        ("apply_sanction", LEWIS_PROFILE), ("apply_sanction", MAX_PROFILE),
    ]
    assert len(league.attendance._calls("announce_sanction")) == 2
    assert league.attendance._calls("refresh_lineup") == [("refresh_lineup", DIVISION_ID)]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_sanction_that_does_not_apply_stops_the_queue(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: ValueError("Pro has no reserve team")}
    await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) == "apply_sanction"
    assert league.attendance._calls("announce_sanction") == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_sanction_is_named_with_attendance_sync_and_the_others_go_ahead(
    tmp_path,
):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(LEWIS_PROFILE, LEWIS),
                                    _candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {LEWIS_PROFILE: ValueError("Pro has no reserve team")}
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "apply_sanction"

    await discard_job(league.bot)

    assert await _stopped_at(league) is None
    assert MAX_PROFILE in league.attendance.applied
    assert LEWIS_PROFILE not in league.attendance.applied
    reply = updated_reply(interaction)
    assert "Lewis" in reply or f"<@{LEWIS}>" in reply
    assert "/attendance sync" in reply
    assert "/attendance sync" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_retried_sanction_run_applies_only_what_is_owed(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(LEWIS_PROFILE, LEWIS),
                                    _candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: ValueError("Discord refused the role change")}
    await _approve(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "apply_sanction"

    league.attendance.applied.add(MAX_PROFILE)  # `/attendance sync` sanctioned Max meanwhile
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    applied = [c[1] for c in league.attendance.calls if c[0] == "apply_sanction"]
    assert applied == [LEWIS_PROFILE, MAX_PROFILE], "a sanction no longer owed was applied again"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_sacked_driver_s_other_division_sheet_is_posted_again_as_a_job(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [
        _candidate(MAX_PROFILE, MAX, "AUTOSACK", other_divisions=(OTHER_DIVISION_ID,)),
    ]
    await _approve(league)
    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    sheets = [row for row in await step_rows(league.db_path) if row["name"] == "attendance_sheet"]
    assert len(sheets) == 3
    divisions = [call[1] for call in league.attendance._calls("post_sheet")]
    assert divisions[0] == DIVISION_ID
    assert sorted(divisions[1:]) == sorted([DIVISION_ID, OTHER_DIVISION_ID])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_approval_while_the_first_is_queued_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await _league(tmp_path)
    await _approve(league)
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    assert len(await _change(league.db_path)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_approval_while_the_first_is_running_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await _league(tmp_path)
    await _approve(league)
    await _run_until_done(league, "names")
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    await run_queue(league.bot)
    assert len(await _penalty_records(league.db_path)) == 2


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_approval_while_the_first_is_stopped_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await _league(tmp_path)
    with _points_fail():
        await _approve(league)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    assert len(await _change(league.db_path)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_every_review_control_refuses_while_the_approval_is_in_hand(tmp_path):
    from leaguebot.results.services.penalty_wizard import (
        _BEING_APPROVED,
        PenaltyReviewState,
        _review_moved_on,
    )

    league = await _league(tmp_path)
    state = PenaltyReviewState(
        round_id=ROUND_ID, division_id=DIVISION_ID, submission_channel_id=SUBMISSION_CHANNEL,
        session_types_present=[SessionType.FEATURE_RACE], db_path=league.db_path,
        bot=league.bot, prompt_message_id=PROMPT, round_number=3, division_name="Pro",
    )
    assert await _review_moved_on(state) is None

    with _points_fail():
        await _approve(league)
        assert await _review_moved_on(state) == _BEING_APPROVED
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    assert await _review_moved_on(state) == _BEING_APPROVED


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_approval_of_a_review_moved_on_is_refused_when_it_runs(tmp_path):
    league = await _league(tmp_path)
    holder = await _blocker(league)
    interaction = await _approve(league)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET prompt_message_id = 8950")
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    assert (await _change(league.db_path))[0]["state"] == "REFUSED"
    assert await _penalty_records(league.db_path) == []
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert "replaced by a newer one" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_reply_with_only_pardons_names_them_and_one_with_nothing_staged_says_so(tmp_path):
    league = await _league(tmp_path, attendance=True)
    pardoned = await _approve(league, staged=[], pardons=[_pardon()])
    await run_queue(league.bot)

    reply = updated_reply(pardoned)
    assert "Round 3's reports are approved (Pro)" in reply
    assert "pardon" in reply.lower()
    assert "0 penalties" not in reply

    (tmp_path / "nothing").mkdir()
    other = await _league(tmp_path / "nothing")
    nothing = await _approve(other, staged=[])
    await run_queue(other.bot)

    reply = updated_reply(nothing)
    assert "Round 3's reports are approved (Pro)" in reply
    assert "0 penalties" not in reply
    assert "nothing" in reply.lower() or "no penalties" in reply.lower()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_approval_queued_while_the_division_is_being_amended_is_refused_when_it_runs(
    tmp_path,
):
    league = await _league(tmp_path)
    holder = await _blocker(league)
    interaction = await _approve(league)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "expires_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-10-05T11:55:00+00:00', "
            "'2026-10-05T12:25:00+00:00')",
            (LATER_ROUND_ID, AMEND_CHANNEL),
        )
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot)

    assert (await _change(league.db_path))[0]["state"] == "REFUSED"
    assert await _penalty_records(league.db_path) == []
    assert await _status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert "being amended" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# What the manager and the log see
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_manager_is_told_the_approval_is_under_way_and_then_its_outcome(tmp_path):
    league = await _league(tmp_path)
    interaction = await _approve(league)

    first = acknowledgement(interaction)
    assert first.startswith("⏳")
    assert "job #" in first

    await run_queue(league.bot)
    reply = updated_reply(interaction)
    assert "✅ Round 3's reports are approved (Pro): 2 penalties applied." in reply
    assert "appeals review is posted below" in reply


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_approval_writes_penalty_review_approved_with_its_audit_body(tmp_path):
    league = await _league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)

    lines = [line for line in league.bot.log_channel.sent if "PENALTY_REVIEW_APPROVED" in line]
    assert len(lines) == 1
    line = lines[0]
    assert "PENALTY_REVIEW_APPROVED | Success" in line
    assert "Alex" in line
    assert "round: 3 (Pro)" in line
    assert "penalties: 2" in line
    assert "old=" in line and "new=" in line
    assert "AWAITING_APPEAL_VERDICTS" in line


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_prompt_and_approval_message_are_taken_down_after_the_reposts(tmp_path):
    league = await _league(tmp_path)
    await _approve(league, approval=APPROVAL)
    await run_queue(league.bot)

    submission = league.channel(SUBMISSION_CHANNEL).messages
    assert PROMPT not in submission and APPROVAL not in submission
    events = league.events
    posted = events.index(("send", RESULTS_CHANNEL, league.sent_to(RESULTS_CHANNEL)[0]))
    assert events.index(("delete", SUBMISSION_CHANNEL, PROMPT)) > posted
    assert events.index(("delete", SUBMISSION_CHANNEL, APPROVAL)) > posted


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_appeals_prompt_id_is_saved(tmp_path):
    league = await _league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)

    prompts = _appeals_prompts(league)
    assert len(prompts) == 1
    assert await _one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_attendance_switched_off_before_its_jobs_drops_them(tmp_path):
    league = await _league(tmp_path, attendance=True)
    league.attendance.candidates = [_candidate(MAX_PROFILE, MAX)]
    interaction = await _approve(league)
    await _run_until_done(league, "apply")
    assert len(league.attendance._calls("record_on")) == 1

    await league.switch_attendance(False)
    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert league.attendance._calls("post_sheet") == []
    assert league.attendance._calls("apply_sanction") == []
    assert (await _change(league.db_path))[0]["state"] == "DONE"
    assert "✅" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_save_awaits_nothing_but_its_connection(tmp_path):
    league = await _league(tmp_path)
    await _approve(league)
    await _run_until_done(league, "names")

    names = [row for row in await step_rows(league.db_path) if row["name"] == "names"][0]
    assert "Lewis" in str(names["result"]) and "Max" in str(names["result"])

    asked = AssertionError("the save asked Discord for a name")
    league.guild.get_member = MagicMock(side_effect=asked)
    league.guild.fetch_member = AsyncMock(side_effect=asked)
    await _run_until_done(league, "apply")

    assert await _stopped_at(league) is None
    assert len(await _penalty_records(league.db_path)) == 2
