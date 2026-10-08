"""Cancelling a division on the change queue (#439, slice 4b).

`/division cancel` checks its confirmation word, the season and the name at the press, then asks
the change queue for the division's cancellation; its being already cancelled is the change type's
check, run when it is asked and again when it runs, in today's words. The timed work of every
round of the division is removed first; one save records the division and every round of it whose
results have not been entered cancelled, each with the status it was cancelled from, and moves the
season on where it was the last; then each enabled module's notice, each round's check-in call
taken down and the calendar's repost follow as jobs of their own; then one closing line. A round
whose results submission stands open is cancelled with the rest, as today. A second cancellation
of the division is refused at once, naming the job.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`ongoing_league`, with "now" pinned. The change type is unbuilt until the build, so each test that
needs it is marked to fail until then: today the command cancels on the spot. The press's refusals,
and a division cancelled with a round's submission open, are what today's command already does,
and pass as they stand.
"""
from __future__ import annotations

import importlib
from contextlib import ExitStack
import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    AM,
    CALL_MESSAGES,
    DIVISION_CANCEL_KIND,
    DIVISIONS,
    LEWIS,
    PRO,
    ROUND_CANCEL_KIND,
    SEASON_ID,
    SUBMISSION_CHANNEL,
    accept_session,
    cancel_division,
    cancel_round,
    cancellation_changes,
    ongoing_league,
    open_submission,
    profile_id,
    reply,
    round_id,
)

PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
R3, R4 = round_id(PRO, 3), round_id(PRO, 4)
CANCELLED = "✅ Division **Pro** cancelled."
SUCCESS = "Admin (`<@77>`) | /division cancel | Success"
REFUSAL = "⛔ `/division cancel` refused for Admin (`<@77>`) — "
ACK = (
    "⏳ Cancelling **Pro**. This message will be updated when it is done; if it takes longer, the "
    "log channel will say so. It begins with job #"
)
UNARM_DISCARDED = (
    "Nothing was cancelled: **Pro** stands as it was. Run `/division cancel` again."
)
SAVE_DISCARDED = (
    "Nothing was cancelled, but the rounds of **Pro** no longer have their timed work: run "
    "`/division cancel` again."
)
CHECKIN_R3 = (
    "\n  check-in, Pro, Round 3 (Silverstone):"
    "\n    accepted: `<@101>`"
    "\n    tentative: none"
    "\n    declined: none"
    "\n    no answer: `<@102>`"
)
CHECKIN_R4 = (
    "\n  check-in, Pro, Round 4 (Silverstone):"
    "\n    accepted: none"
    "\n    tentative: none"
    "\n    declined: `<@101>`"
    "\n    no answer: none"
)
#: Round 4's check-in call, seeded by `_second_call`.
R4_CALL = (7101, 7102, 7103)
WIND_DOWN = "season.wind_down"
#: Every channel a cancellation of Pro posts in: its check-in, forecast, results and calendar.
_PRO_POSTS = (PRO_CH.checkin, PRO_CH.forecast, PRO_CH.results, PRO_CH.calendar)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _change(league: Any) -> dict[str, Any]:
    """The one cancellation of a division asked of the queue."""
    rows = [row for row in await cancellation_changes(league)
            if row["kind"] == DIVISION_CANCEL_KIND]
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    return await step_rows(league.db_path, (await _change(league))["id"])


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the cancellation's job called *name* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the cancellation's job {name} never finished: {await _jobs(league)}")


async def _done(league: Any) -> None:
    """Run the queue, and see the cancellation finished on it."""
    await run_queue(league.bot)
    assert (await _change(league))["state"] == "DONE"


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


async def _statuses(league: Any) -> dict[int, str]:
    rows = await league.rows("SELECT id, status FROM rounds WHERE division_id = ?", PRO)
    return {row["id"]: row["status"] for row in rows}


async def _division(league: Any, division_id: int = PRO) -> str:
    return (await league.rows("SELECT status FROM divisions WHERE id = ?", division_id))[0][
        "status"
    ]


async def _round_audits(league: Any) -> list[str]:
    rows = await league.rows(
        "SELECT old_value FROM audit_entries WHERE change_type = 'round.status' "
        "AND division_id = ? ORDER BY id", PRO,
    )
    return [row["old_value"] for row in rows]


async def _awaiting_results(league: Any, rid: int) -> None:
    await league.write(
        "UPDATE rounds SET status = 'AWAITING_RESULTS', scheduled_at = ? WHERE id = ?",
        (league.clock.now - timedelta(hours=2)).isoformat(), rid,
    )


async def _am_finished(league: Any) -> None:
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)


async def _second_call(league: Any) -> None:
    """Round 4's check-in call stands too, as messages 7101 to 7103, Lewis having declined."""
    call, notice, distribution = R4_CALL
    await league.write(
        "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
        "posted_at, last_notice_msg_id, distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        R4, PRO, str(call), str(PRO_CH.checkin), league.clock.now.isoformat(), str(notice),
        str(distribution),
    )
    await league.write(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
        "rsvp_status) VALUES (?, ?, ?, 'DECLINED')",
        R4, PRO, profile_id(LEWIS),
    )
    for message_id in R4_CALL:
        league.channel(PRO_CH.checkin).seed(message_id, "Round 4 check-in")


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if SUCCESS in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if line.startswith("⛔ ")]


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch *name* wherever core keeps it, and the change type's own reference to it."""
    stack = ExitStack()
    for module_name in ("leaguebot.core.services.season_service",
                        "leaguebot.core.services.season_lifecycle_service",
                        "leaguebot.core.services.cancellation_changes"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _failing(name: str) -> ExitStack:
    """*name* raises, as a fault of the database would."""
    return _everywhere(name, AsyncMock(side_effect=RuntimeError("disk I/O error")))


async def _asked(league: Any, name: str = "Pro", **kwargs: Any) -> Any:
    interaction = await cancel_division(league, name, **kwargs)
    assert not league.errors, league.errors
    return interaction


# ── The defects ─────────────────────────────────────────────────────────────────────


async def test_a_stop_after_the_division_is_cancelled_tells_it_and_reposts_its_calendar_on_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path, weather=True, results=True, attendance=True)
    await _asked(league)
    await _run_through(league, "apply")
    assert [job["name"] for job in await _jobs(league) if job["done_at"]] == ["unarm", "apply"]

    await league.restart()
    await run_queue(league.bot)

    checkin = league.texts(PRO_CH.checkin)
    assert len(checkin) == 1 and checkin[0].startswith("<@&801>\n📢 **Division Cancelled: Pro**")
    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert len(league.texts(PRO_CH.forecast)) == 1
    assert len(league.texts(PRO_CH.results)) == 1
    calendar = league.texts(PRO_CH.calendar)
    assert len(calendar) == 1 and calendar[0].count("— Cancelled") == 2
    lines = _success_lines(league)
    assert len(lines) == 1 and CHECKIN_R3 in lines[0]


async def test_a_stop_after_the_timed_work_is_removed_cancels_the_division_on_restart(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)
    await _run_through(league, "unarm")
    assert await _division(league) == "ACTIVE"

    await league.restart()
    await run_queue(league.bot)

    assert await _division(league) == "CANCELLED"
    assert (await _statuses(league))[R3] == "CANCELLED"
    assert (await _statuses(league))[R4] == "CANCELLED"


async def test_cancelling_the_last_division_moves_the_season_on_in_the_same_save(tmp_path):
    league = await ongoing_league(tmp_path)
    await _am_finished(league)
    await _asked(league)

    with _failing("advance_to_pending_completion_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    assert await _division(league) == "ACTIVE"
    assert (await _statuses(league))[R3] == "NOT_RUN"
    assert (await league.season())["stage"] == "ONGOING"

    await retry_job(league.bot, run=False)
    await _run_through(league, "apply")

    assert await _division(league) == "CANCELLED"
    assert (await league.season())["stage"] == "PENDING_COMPLETION"


async def test_each_round_called_off_is_audited_with_the_status_it_was_cancelled_from(tmp_path):
    league = await ongoing_league(tmp_path)
    await _awaiting_results(league, R4)
    await _asked(league)

    await _done(league)

    assert sorted(await _round_audits(league)) == ["AWAITING_RESULTS", "NOT_RUN"]


async def test_a_call_message_discord_will_not_delete_stops_the_queue(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1
    [stop] = [line for line in _log_lines(league) if "The queue is stopped at job #" in line]
    assert "check-in call" in stop and "round 3" in stop and "**Pro**" in stop
    assert "`/division cancel`" in stop
    assert "Admin (`<@77>`)" in stop

    league.undeletable.clear()
    await retry_job(league.bot)

    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    assert CANCELLED in reply(interaction)


async def test_the_admin_is_told_at_once_naming_the_job_and_the_reply_is_updated_with_the_outcome(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    interaction.response.defer.assert_not_awaited()
    assert await _division(league) == "ACTIVE"

    await _done(league)

    assert CANCELLED in reply(interaction)


async def test_a_second_cancel_of_the_division_is_refused_at_once_naming_the_job(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)
    with _failing("cancel_division_on"):
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    job = [j for j in await _jobs(league) if j["name"] == "apply"][0]["id"]

    second = await _asked(league)

    assert reply(second).startswith(f"⏳ **Pro** is being cancelled (job #{job})")
    assert len(_refusal_lines(league)) == 1
    assert len(await cancellation_changes(league)) == 1


# ── The checks ──────────────────────────────────────────────────────────────────────


async def _nothing(_league: Any) -> None:
    return None


async def _no_season(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'SETUP', stage = 'PLACEMENTS' WHERE id = ?",
                       SEASON_ID)


async def _pending_completion(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?", SEASON_ID)


async def _archived(league: Any) -> None:
    league.bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=SeasonImmutableError("Season 3 is archived and cannot be modified.")
    )


async def _already_cancelled(league: Any) -> None:
    await league.write("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", PRO)


ONLY_ONGOING = "❌ `/division cancel` is available only while the season is ongoing."

#: Each refusal at the press: what sets it up, the name typed, the confirmation word, and
#: today's reply.
_PRESS_REFUSALS = {
    "confirm": (_nothing, "Pro", "confirm",
                "❌ Type exactly `CONFIRM` in the `confirm` field to proceed."),
    "no season": (_no_season, "Pro", "CONFIRM", ONLY_ONGOING),
    "pending completion": (_pending_completion, "Pro", "CONFIRM", ONLY_ONGOING),
    "archived": (_archived, "Pro", "CONFIRM",
                 "❌ This season is archived (COMPLETED) and cannot be modified."),
    "unknown": (_nothing, "Nope", "CONFIRM", "❌ Division `Nope` not found."),
    "already cancelled": (_already_cancelled, "pro", "CONFIRM",
                          "❌ Division **Pro** is already cancelled."),
}


@pytest.mark.parametrize("case", sorted(_PRESS_REFUSALS))
async def test_the_command_is_refused_at_once_in_today_s_words(tmp_path, case):
    setup, name, word, text = _PRESS_REFUSALS[case]
    league = await ongoing_league(tmp_path, attendance=True)
    await setup(league)
    before = await _statuses(league)

    interaction = await _asked(league, name, confirm=word)

    assert reply(interaction) == text
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await cancellation_changes(league) == []
    await run_queue(league.bot)
    assert league.unarmed == []
    assert await _statuses(league) == before
    assert [league.texts(cid) for cid in _PRO_POSTS] == [[], [], [], []]


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_a_raced_round_keeps_its_results_and_its_status(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _done(league)

    assert await _statuses(league) == {
        round_id(PRO, 1): "FINAL", round_id(PRO, 2): "AWAITING_REPORT_VERDICTS",
        R3: "CANCELLED", R4: "CANCELLED",
    }
    assert await _division(league) == "CANCELLED"


async def test_each_round_called_off_has_its_call_taken_down_and_its_check_in_written(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    await _second_call(league)
    await _asked(league)

    await _done(league)

    taken = [job for job in await _jobs(league) if job["name"] == "take_down_call"]
    assert len(taken) == 2
    assert not (set(CALL_MESSAGES) | set(R4_CALL)) & set(league.channel(PRO_CH.checkin).messages)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE division_id = ?", PRO) == []
    line = _success_lines(league)[0]
    assert CHECKIN_R3 + CHECKIN_R4 in line


async def test_a_round_whose_call_was_never_posted_is_neither_taken_down_nor_written(tmp_path):
    """Pro's round 3 has its check-in call standing and round 4 has none. Cancelling Pro takes
    round 3's call down and writes its check-in beneath the line; round 4 gets no take-down job
    and no check-in written (attendance spec, Cancellation)."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _asked(league)

    await _done(league)

    assert [job["name"] for job in await _jobs(league)].count("take_down_call") == 1
    [line] = _success_lines(league)
    assert CHECKIN_R3 in line
    assert "check-in, Pro, Round 4" not in line


async def test_the_division_is_told_in_the_division_s_words(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, results=True, attendance=True)
    await _asked(league)

    await _done(league)

    assert league.texts(PRO_CH.checkin) == [
        "<@&801>\n📢 **Division Cancelled: Pro**\nThis division has been cancelled. There are no "
        "further check-ins to answer."
    ]
    assert league.texts(PRO_CH.forecast) == [
        "📢 **Division Cancelled: Pro**\nNo further weather forecasts will be posted for this "
        "division."
    ]
    assert league.texts(PRO_CH.results) == [
        "📢 **Division Cancelled: Pro**\nNo further results will be posted for this division."
    ]


async def test_the_jobs_run_in_today_s_order(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, results=True, attendance=True)
    await _second_call(league)
    await _asked(league)

    await _done(league)

    assert [job["name"] for job in await _jobs(league)] == [
        "unarm", "apply", "notify_checkin", "take_down_call", "take_down_call",
        "notify_forecast", "notify_results", "post_calendar", "close",
    ]
    deleted = [mid for kind, cid, mid in league.events
               if kind == "delete" and cid == PRO_CH.checkin]
    assert deleted == list(CALL_MESSAGES) + list(R4_CALL)
    sent = [cid for kind, cid, _mid in league.events if kind == "send"]
    assert sent == [PRO_CH.checkin, PRO_CH.forecast, PRO_CH.results, PRO_CH.calendar]


async def test_every_round_of_the_division_loses_its_timed_work(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _done(league)

    assert sorted(league.unarmed) == [round_id(PRO, n) for n in range(1, 5)]
    assert (await _jobs(league))[0]["name"] == "unarm"


async def test_the_wind_down_follows_as_a_change_of_its_own(tmp_path):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = ?", SEASON_ID)
    await _am_finished(league)
    await _asked(league)

    await _run_through(league, "apply")
    wind_downs = [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN]
    assert len(wind_downs) == 1

    await run_queue(league.bot)
    assert (await league.season())["stage"] == "PENDING_COMPLETION"


async def test_a_discarded_save_cancels_nothing(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    interaction = await _asked(league)
    with _failing("cancel_division_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await discard_job(league.bot)

    assert SAVE_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _division(league) == "ACTIVE"
    assert (await _statuses(league))[R3] == "NOT_RUN"
    assert _success_lines(league) == []
    assert [league.texts(cid) for cid in _PRO_POSTS] == [[], [], [], []]


async def test_a_discarded_unarming_cancels_nothing(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.scheduler_service.cancel_round = MagicMock(
        side_effect=RuntimeError("the job store is locked")
    )
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "unarm"
    await discard_job(league.bot)

    assert UNARM_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _division(league) == "ACTIVE"
    assert (await _statuses(league))[R3] == "NOT_RUN"
    assert (await _statuses(league))[R4] == "NOT_RUN"
    assert await _round_audits(league) == []
    assert _success_lines(league) == []
    assert [league.texts(cid) for cid in _PRO_POSTS] == [[], [], [], []]


async def test_a_round_s_cancellation_queued_first_runs_first_and_the_division_s_then_calls_off_the_rest(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    league.remove_channel(AM_CH.checkin)
    await cancel_round(league, "Am", 3)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"
    round_asked = await cancel_round(league, "Pro", 3)
    division_asked = await _asked(league)
    assert not _refusal_lines(league)

    league.restore_channel(AM_CH.checkin)
    await retry_job(league.bot)

    assert "✅ Round **3** in **Pro** cancelled." in reply(round_asked)
    assert CANCELLED in reply(division_asked)
    assert (await _statuses(league))[R3] == "CANCELLED"
    assert (await _statuses(league))[R4] == "CANCELLED"
    assert await _round_audits(league) == ["NOT_RUN", "NOT_RUN"]
    kinds = [row["kind"] for row in await change_rows(league.db_path)
             if row["kind"] in (ROUND_CANCEL_KIND, DIVISION_CANCEL_KIND)]
    assert kinds == [ROUND_CANCEL_KIND, ROUND_CANCEL_KIND, DIVISION_CANCEL_KIND]
    # Every cancellation went through; the wind-down it asked drops itself where not due.
    for row in await change_rows(league.db_path):
        if row["kind"] in (ROUND_CANCEL_KIND, DIVISION_CANCEL_KIND):
            assert row["state"] == "DONE"
        else:
            assert (row["kind"], row["state"]) in ((WIND_DOWN, "DONE"), (WIND_DOWN, "DROPPED"))


async def test_a_round_with_a_submission_open_is_cancelled_with_its_division_and_nothing_is_refused(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _awaiting_results(league, R3)
    await open_submission(league, R3)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert (await _statuses(league))[R3] == "CANCELLED"
    assert await _division(league) == "CANCELLED"
    assert _refusal_lines(league) == []
    assert CANCELLED in reply(interaction)


# ── A finished division (#439 slice 4b, F1) ────────────────────────────────────────

FINISHED = "❌ Division **Pro** has finished and cannot be cancelled."


async def _pro_finished(league: Any) -> None:
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", PRO)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", PRO)


async def test_a_finished_division_is_refused_at_once(tmp_path):
    """Every round of Pro has its results and Pro has finished, while Am still races. Asking to
    cancel Pro is refused at once: nothing is queued, one refusal line is written, and Pro and its
    rounds are left as they were."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _pro_finished(league)
    before = await _statuses(league)

    interaction = await _asked(league, "pro")

    assert reply(interaction) == FINISHED
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await cancellation_changes(league) == []
    await run_queue(league.bot)
    assert league.unarmed == []
    assert await _division(league) == "FINISHED"
    assert await _statuses(league) == before


async def test_a_division_that_finishes_while_its_cancellation_waits_is_refused_when_it_runs(
    tmp_path,
):
    """Pro's cancellation waits behind Am's round 3 cancellation, stopped at its check-in notice.
    Meanwhile Pro's last results come in and Pro finishes. When the queue goes on, Pro's
    cancellation is refused: the reply says so, Pro stays finished and its rounds untouched."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.remove_channel(AM_CH.checkin)
    await cancel_round(league, "Am", 3)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"
    interaction = await _asked(league)
    assert len(await cancellation_changes(league)) == 2
    await _pro_finished(league)
    before = await _statuses(league)

    league.restore_channel(AM_CH.checkin)
    await retry_job(league.bot)

    assert FINISHED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _division(league) == "FINISHED"
    assert await _statuses(league) == before
    assert not set(before) & set(league.unarmed)
    assert len(_refusal_lines(league)) == 1
    assert (await _change(league))["state"] == "REFUSED"


# ── While an amendment is applied (#439 slice 4b, F4) ──────────────────────────────

BEING_AMENDED = (
    "⏸️ A round of **Pro** is being amended, so its rounds cannot be cancelled until that is "
    "done. Try again in a moment."
)


async def test_a_division_cancel_asked_while_a_round_of_it_is_being_amended_is_refused_at_once(
    tmp_path,
):
    """A `/round amend` of a round of Pro has been confirmed and is being applied. Asking to
    cancel Pro meanwhile is refused at once: nothing is queued, one refusal line is written, and
    Pro and its rounds are left as they were."""
    league = await ongoing_league(tmp_path, attendance=True)
    before = await _statuses(league)

    with league.bot.amendment_service.applying(PRO):
        interaction = await _asked(league)

    assert reply(interaction) == BEING_AMENDED
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await cancellation_changes(league) == []
    await run_queue(league.bot)
    assert league.unarmed == []
    assert await _division(league) == "ACTIVE"
    assert await _statuses(league) == before


async def test_a_division_cancel_that_comes_to_run_while_a_round_of_it_is_being_amended_is_refused(
    tmp_path,
):
    """Pro's cancellation waits behind Am's round 3 cancellation, stopped at its check-in notice.
    When the queue goes on, a `/round amend` of a round of Pro is being applied: Pro's
    cancellation is refused when it runs, the reply says so, and Pro and its rounds stand."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.remove_channel(AM_CH.checkin)
    await cancel_round(league, "Am", 3)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"
    interaction = await _asked(league)
    before = await _statuses(league)

    with league.bot.amendment_service.applying(PRO):
        league.restore_channel(AM_CH.checkin)
        await retry_job(league.bot)

    assert BEING_AMENDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _division(league) == "ACTIVE"
    assert await _statuses(league) == before
    assert not set(before) & set(league.unarmed)
    assert len(_refusal_lines(league)) == 1
    assert (await _change(league))["state"] == "REFUSED"


async def test_a_division_that_finishes_after_its_check_is_refused_by_the_save_on_a_retry(
    tmp_path,
):
    """Pro's cancellation passed its check and removed the timed work, and its save failed, so the
    queue stopped at the save. Meanwhile Pro's last results come in and Pro finishes. On Retry the
    save itself refuses: the reply says Pro has finished, Pro stays finished, its rounds keep the
    statuses they had, one refusal line is written and no wind-down is asked."""
    league = await ongoing_league(tmp_path, attendance=True)
    interaction = await _asked(league)
    with _failing("cancel_division_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    await _pro_finished(league)
    before = await _statuses(league)

    await retry_job(league.bot)

    assert FINISHED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _division(league) == "FINISHED"
    assert await _statuses(league) == before
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert _success_lines(league) == []
    assert [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN] == []


# ── An open submission (#439 slice 4b, amendment A, A2) ─────────────────────────────

#: Why each test below fails until the build: today a division's cancellation cancels a round
#: whatever its open submission holds, and leaves the submission open.
SUBMISSION_XFAIL = (
    "#439: a division's cancellation does not yet refuse a round whose open submission has "
    "accepted results, nor close an empty one and delete its channel"
)
#: A second submission channel, for round 4.
R4_SUBMISSION = SUBMISSION_CHANNEL + 1


def _accepted(number: int, channel_id: int = SUBMISSION_CHANNEL) -> str:
    return (f"❌ Cannot cancel **Pro** — results have already been accepted in the submission "
            f"channel of round {number} (<#{channel_id}>), and cancelling would lose them.")


async def _closed(league: Any) -> dict[int, int]:
    rows = await league.rows("SELECT round_id, closed FROM round_submission_channels")
    return {row["round_id"]: row["closed"] for row in rows}


@pytest.mark.xfail(strict=True, reason=SUBMISSION_XFAIL)
@pytest.mark.parametrize("when", ["at ask", "at run"])
async def test_a_division_with_a_round_whose_submission_has_accepted_results_is_refused_naming_the_round(
    tmp_path, when,
):
    """Pro's round 3 waits for its results, and its open submission (channel 690) has accepted
    a session's results. `/division cancel Pro` asked then, or asked while the queue stands
    stopped and the session accepted while it waits: it is refused, naming round 3 and its
    channel, with one ⛔ line; Pro and its rounds are left as they were and its submission
    stays open."""
    league = await ongoing_league(tmp_path, attendance=True)

    async def _accepted_in_round_3() -> None:
        await _awaiting_results(league, R3)
        await open_submission(league, R3)
        await accept_session(league, R3)

    if when == "at ask":
        await _accepted_in_round_3()
        before = await _statuses(league)
        interaction = await _asked(league)
        assert reply(interaction) == _accepted(3)
        assert await cancellation_changes(league) == []
    else:
        league.remove_channel(AM_CH.checkin)
        await cancel_round(league, "Am", 3)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "notify_checkin"
        interaction = await _asked(league)
        await _accepted_in_round_3()
        before = await _statuses(league)
        league.restore_channel(AM_CH.checkin)
        await retry_job(league.bot)
        assert _accepted(3) in reply(interaction)
        assert CANCELLED not in reply(interaction)
        assert (await _change(league))["state"] == "REFUSED"

    assert len(_refusal_lines(league)) == 1
    assert await _division(league) == "ACTIVE"
    assert await _statuses(league) == before
    assert not set(before) & set(league.unarmed)
    assert await _closed(league) == {R3: 0}


@pytest.mark.xfail(strict=True, reason=SUBMISSION_XFAIL)
async def test_a_division_with_a_round_in_its_review_is_refused(tmp_path):
    """Pro's round 2 is in its penalty review: its results accepted, its submission channel
    (690) still open for the review. `/division cancel Pro` is refused until the review is
    finished, naming round 2 and its channel, with one ⛔ line; nothing is queued, and Pro,
    round 2 and its submission are left as they were."""
    league = await ongoing_league(tmp_path, attendance=True)
    r2 = round_id(PRO, 2)
    await open_submission(league, r2)
    await league.write(
        "UPDATE round_submission_channels SET in_penalty_review = 1 WHERE round_id = ?", r2
    )
    await accept_session(league, r2)
    before = await _statuses(league)

    interaction = await _asked(league)

    assert reply(interaction) == _accepted(2)
    assert len(_refusal_lines(league)) == 1
    assert await cancellation_changes(league) == []
    await run_queue(league.bot)
    assert await _division(league) == "ACTIVE"
    assert await _statuses(league) == before
    assert (await _statuses(league))[r2] == "AWAITING_REPORT_VERDICTS"
    assert await _closed(league) == {r2: 0}


@pytest.mark.xfail(strict=True, reason=SUBMISSION_XFAIL)
async def test_each_closed_submission_s_channel_is_deleted_in_round_order_first_after_the_save(
    tmp_path,
):
    """Pro's rounds 3 and 4 both wait for their results, each with an open submission that has
    accepted nothing (channels 690 and 691). `/division cancel Pro`, the queue run: both
    submissions are closed in the save, and the two jobs straight after it delete round 3's
    channel, then round 4's, before any notice is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    for rid, channel_id in ((R3, SUBMISSION_CHANNEL), (R4, R4_SUBMISSION)):
        await _awaiting_results(league, rid)
        await open_submission(league, rid, channel_id=channel_id)
    interaction = await _asked(league)

    await _done(league)

    assert await _closed(league) == {R3: 1, R4: 1}
    jobs = await _jobs(league)
    names = [job["name"] for job in jobs]
    after_save = names.index("apply") + 1
    assert names[after_save:after_save + 2] == ["delete_channel", "delete_channel"]
    assert [json.loads(job["payload"])["channel_id"]
            for job in jobs[after_save:after_save + 2]] == [SUBMISSION_CHANNEL, R4_SUBMISSION]
    deleted = [cid for kind, cid, _ in league.events if kind == "delete_channel"]
    assert deleted == [SUBMISSION_CHANNEL, R4_SUBMISSION]
    first_send = next(i for i, event in enumerate(league.events) if event[0] == "send")
    last_delete = max(i for i, event in enumerate(league.events) if event[0] == "delete_channel")
    assert last_delete < first_send
    assert CANCELLED in reply(interaction)
