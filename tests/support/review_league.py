"""A league whose round is in its penalty review, for the tests of a round's review on the change
queue (#439, slice 2).

`review_league(tmp_path, ...)` builds division 11 (Pro) of season 1 on a database built by the
migrations, its round 3 at Silverstone with Lewis (101) and Max (102) in its Feature Race, and a bot
double with a real change queue and the real change types (`register_change_types`), "now" pinned.

The Discord side is a fake channel per channel id (`channel`), recording every send, delete and
channel deletion in one list of events, in order, each carrying the league's server as its `guild`;
image generation is off. Attendance is reached
through the hook the builder hands the change types, `bot.attendance_after_review`, here an
`AttendanceDouble` that checks the switch as the real hook does, records each call, and writes its
record on the save it is handed into a table of the test's own (`attendance_recorded`).

Everything of the change types is imported inside a function, so a test file using this module
still collects while they are unbuilt.
"""
from __future__ import annotations

import itertools
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import StagedPardon
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    http_error,
    league_double,
    member_interaction,
    register,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
)
from tests.support.teams import seed_team_instances

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
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

_ids = itertools.count(9000)


# ---------------------------------------------------------------------------
# The Discord side
# ---------------------------------------------------------------------------


def channel(channel_id: int, events: list[tuple[str, int, int]]) -> Any:
    """A text channel holding its messages by id, recording each send and delete in *events*.

    `send_fails`, an exception, refuses every send; `fail_when(content, kwargs)`, where it returns
    True, refuses that send alone; `delete_fails` refuses the channel's own deletion, recorded as
    a `delete_channel` event. `messages` maps an id to the message standing, with its
    `content` and `view`; `seed(id, content)` places one as though posted earlier.
    """
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.name = f"channel-{channel_id}"
    channel.messages = {}
    channel.send_fails = None
    channel.fail_when = None
    channel.delete_fails = None

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

    async def _delete_channel(*_args: Any, **_kwargs: Any) -> None:
        if channel.delete_fails is not None:
            raise channel.delete_fails
        events.append(("delete_channel", channel_id, channel_id))

    channel.seed = seed
    channel.delete = AsyncMock(side_effect=_delete_channel)
    channel.send = AsyncMock(side_effect=_send)
    channel.fetch_message = AsyncMock(side_effect=_fetch)
    channel.get_partial_message = MagicMock(side_effect=_partial)
    return channel


def is_appeals_prompt(_content: str, kwargs: dict[str, Any]) -> bool:
    return type(kwargs.get("view")).__name__ == "AppealsReviewView"


class AttendanceDouble:
    """The attendance hook the builder hands the change types, as a double.

    Every method does nothing while the league's attendance is off, as the real hook does.
    `candidates` are the drivers over a threshold; one whose profile is in `applied` is no longer
    owed. `record_fails`, `sheet_fails` (a list, one per failing try), `apply_fails` and
    `announce_fails` (by profile) and `lineup_fails` (a list, one per failing try) make a call
    raise.
    """

    def __init__(self, league: "ReviewLeague") -> None:
        self.league = league
        self.calls: list[tuple[Any, ...]] = []
        self.candidates: list[dict[str, Any]] = []
        self.applied: set[int] = set()
        self.record_fails: Exception | None = None
        self.sheet_fails: list[Exception] = []
        self.apply_fails: dict[int, Exception] = {}
        self.announce_fails: dict[int, Exception] = {}
        self.lineup_fails: list[Exception] = []

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
        profile = candidate["driver_profile_id"]
        self.calls.append(("announce_sanction", profile))
        failure = self.announce_fails.pop(profile, None)
        if failure is not None:
            raise failure

    async def refresh_lineup(self, division_id: int) -> None:
        if not self.league.attendance_on:
            return
        self.calls.append(("refresh_lineup", division_id))
        if self.lineup_fails:
            raise self.lineup_fails.pop(0)

    async def sync_hint(self, division_id: int, round_id: int) -> str:
        return "Repair the cause, then run `/attendance sync division:Pro round:3`."


def candidate(profile: int, driver: int, sanction: str = "AUTORESERVE",
               other_divisions: tuple[int, ...] = ()) -> dict[str, Any]:
    return {"driver_profile_id": profile, "driver_user_id": driver, "sanction": sanction,
            "other_divisions": list(other_divisions)}


class ReviewLeague:
    """The bot double with its queue and attendance hook, its channels, and what they saw."""

    def __init__(self, db_path: str, *, attendance: bool) -> None:
        self.db_path = db_path
        self.attendance_on = attendance
        self.events: list[tuple[str, int, int]] = []
        self.bot = league_double(db_path)
        self.channels = {
            cid: channel(cid, self.events)
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
        for each in self.channels.values():
            each.guild = guild  # as a real text channel carries its server
        self.bot.get_channel = MagicMock(side_effect=_get)
        self.bot.fetch_channel = AsyncMock(side_effect=_fetch)
        self.bot.get_guild = MagicMock(return_value=guild)
        self.bot.guilds = [guild]
        self.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
        self.bot.module_service.is_attendance_enabled = AsyncMock(
            side_effect=lambda: self.attendance_on
        )
        self.attendance = AttendanceDouble(self)
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


async def make_db(
    tmp_path: Any, *, attendance: bool, config_name: str | None, round_status: str,
    verdicts_channel: bool, other_division: bool, appeals_prompt: int | None,
) -> str:
    """Division 11 (Pro) of season 1. Its round 3 at Silverstone awaits its report verdicts, the
    review open in its submission channel with its prompt; Lewis (101) won its Feature Race and
    Max (102) came second, the results and the standings posted provisionally. Round 4 is final,
    its standings posted. Division 12 (Am) is another division of the season, where
    *other_division* is set.

    *round_status* is round 3's; the division has its verdicts channel where *verdicts_channel* is
    set; *appeals_prompt* is the appeals prompt the channel records, where one is given (a column
    #439 adds)."""
    db_path = os.path.join(str(tmp_path), "report_approval.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        divisions = [(DIVISION_ID, "Pro", 1)]
        if other_division:
            divisions.append((OTHER_DIVISION_ID, "Am", 2))
        for division_id, name, tier in divisions:
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name, tier),
            )
        await seed_team_instances(db, DIVISION_ID, 3001)
        for round_id, number, track, status in (
            (ROUND_ID, 3, "Silverstone", round_status),
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
            (DIVISION_ID, RESULTS_CHANNEL, STANDINGS_CHANNEL,
             str(VERDICTS_CHANNEL) if verdicts_channel else None),
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
        if appeals_prompt is not None:
            await db.execute(
                "UPDATE round_submission_channels SET appeals_prompt_message_id = ?",
                (appeals_prompt,),
            )
        await db.commit()
    return db_path


async def review_league(
    tmp_path: Any, *, attendance: bool = False, config_name: str | None = "Standard",
    round_status: str = "AWAITING_REPORT_VERDICTS", verdicts_channel: bool = True,
    other_division: bool = True, appeals_prompt: int | None = None,
) -> ReviewLeague:
    """The league of `make_db`, its bot built with a real queue and the real change types."""
    db_path = await make_db(
        tmp_path, attendance=attendance, config_name=config_name, round_status=round_status,
        verdicts_channel=verdicts_channel, other_division=other_division,
        appeals_prompt=appeals_prompt,
    )
    return ReviewLeague(db_path, attendance=attendance)


def penalty(driver: int, seconds: int = 5) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=seconds,
        description="Corner cutting",
        justification="Turn 4, lap 12",
    )


def pardon() -> StagedPardon:
    return StagedPardon(
        driver_user_id=MAX, driver_profile_id=MAX_PROFILE, attendance_id=41,
        pardon_type="NO_RSVP", justification="Told us in advance", grantor_id=77,
    )


async def run_until_done(league: ReviewLeague, job: str) -> None:
    """Run the queue one job at a time until *job* is done, as a stop just after it: the first
    job of that name in the change asked last, not one an earlier change has already finished,
    nor a later one of the same name in the same change (a restart "between two verdicts" stops
    after the first)."""
    for _ in range(40):
        rows = [row for row in await step_rows(league.db_path) if row["name"] == job]
        if rows:
            latest = max(row["change_id"] for row in rows)
            first = next(row for row in rows if row["change_id"] == latest)
            if first["done_at"] is not None:
                return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"{job} was never done")


async def stopped_at(league: ReviewLeague) -> str | None:
    job = await stopped_job(league.db_path)
    return None if job is None else job["name"]


async def block_queue(league: ReviewLeague) -> dict[str, Any]:
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
    assert await stopped_at(league) == "block"
    return holder


async def race_rows(db_path: str) -> dict[int, dict[str, Any]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, postrace_time_penalties_ms, appeal_time_penalties_ms, "
            "points_awarded FROM race_session_results"
        )
        return {row["driver_user_id"]: dict(row) for row in await cursor.fetchall()}


async def one(db_path: str, sql: str, *args: Any) -> Any:
    async with get_connection(db_path) as db:
        row = await (await db.execute(sql, args)).fetchone()
    return None if row is None else row[0]


async def penalty_records(db_path: str) -> list[dict[str, Any]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM penalty_records ORDER BY id")
        return [dict(row) for row in await cursor.fetchall()]


async def round_status(db_path: str) -> str:
    return await one(db_path, "SELECT status FROM rounds WHERE id = ?", ROUND_ID)


async def changes_of(db_path: str, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(db_path) if row["kind"] == kind]


def points_fail() -> Any:
    """Every session's points, recalculated, raise as a fault in the bot would."""
    return patch(
        "leaguebot.results.services.result_submission_service._apply_points_in_tx",
        new=AsyncMock(side_effect=RuntimeError("the points could not be calculated")),
    )


#: The written heading over a batch of round 3's verdicts, where image generation is off.
HEADING = "**Season 1 Pro Round 3**"


def verdict_headings(league: ReviewLeague) -> list[int]:
    """The messages sent to the verdicts channel that are the written heading, in order."""
    channel = league.channel(VERDICTS_CHANNEL)
    return [
        mid for mid in league.sent_to(VERDICTS_CHANNEL)
        if getattr(channel.messages.get(mid), "content", None) == HEADING
    ]


def appeals_prompts(league: ReviewLeague) -> list[int]:
    return [
        mid for mid, message in league.channel(SUBMISSION_CHANNEL).messages.items()
        if type(message.view).__name__ == "AppealsReviewView"
    ]
