"""Confirming a round's penalty review, then its appeals review.

Issue #208. `finalize_penalty_review` and `finalize_appeals_review` were partly covered — the
paths with nothing staged ran, and the ones that actually change a championship did not.

**Penalties are recorded as staged before they are applied.** `staged_penalties` is written
first, and a second press — a double click, or a restart mid-finalisation recovered by
re-posting the prompt — finds it set and applies nothing. Applying twice would double every
time penalty on the round, silently, and the recovery path's warning to stewards exists because
of exactly this column. `test_penalties_already_applied_are_not_applied_again` is the one that
holds it.

**A settled round is never reopened.** The review views outlive the round: disabling the results
module closes every round still awaiting review, but a client already holding the message can
still press the button. Both status writes are guarded by the terminal states, so a stale press
cannot drag a FINAL or CANCELLED round back into an awaiting one (#167).

**The attendance pipeline runs only where attendance is enabled, and each step is independent.**
Attendance is recorded from the results, staged pardons are persisted, points distributed, the
sheet posted and sanctions enforced — and a failure in one must not stop the rest, because the
penalty verdicts are already published by the time it runs. Pardons are inserted idempotently,
so a recovered finalisation cannot grant one twice. A sanction that did not apply is told to the
approving manager and the log channel, with the `/attendance sync` that finishes it (#239).

**Approving appeals is what finishes a round, and so what finishes a division.** The round goes
to FINAL, the division's status is reconsidered — the last round's appeals are what lets
`/season complete` run (#154) — and the submission channel is closed. Upheld corrections are
recorded as appeal records and announced; an announcement that fails does not un-approve them.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.attendance_service import SanctionOutcome  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.penalty_wizard import PenaltyReviewState, StagedPardon  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    finalize_appeals_review,
    finalize_penalty_review,
)

SERVER_ID = 13008
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
STEWARD = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "finalize",
    round_status: str = "AWAITING_REPORT_VERDICTS",
    staged_json: str | None = None,
    attendance_row: bool = False,
):
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
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "staged_penalties) VALUES (?, 700, '2026-02-01T00:00:00+00:00', ?)",
            (ROUND_ID, staged_json),
        )
        if attendance_row:
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (31, '101', 'ASSIGNED')"
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (id, round_id, division_id, "
                "driver_profile_id, rsvp_status) VALUES (41, ?, ?, 31, 'NO_RSVP')",
                (ROUND_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


def _penalty(driver: int = 101) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=5,
        description="Corner cutting",
        justification="Turn 4, lap 12",
    )


def _state(db_path, *, staged=(), appeals=(), pardons=(), attendance_enabled=False):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.add_view = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    return PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=700,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=bot,
        staged=list(staged),
        staged_appeals=list(appeals),
        staged_pardons=list(pardons),
        round_number=3,
        division_name="Pro",
    )


def _interaction(*, guild=True):
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = STEWARD
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    if guild:
        channel = MagicMock()
        message = MagicMock()
        message.id = 9900
        channel.send = AsyncMock(return_value=message)
        interaction.guild = MagicMock()
        interaction.guild.get_channel = MagicMock(return_value=channel)
    else:
        interaction.guild = None
    return interaction


def _patches(
    *, apply_result=None, announce_error=None, attendance_errors=None, sanction_outcome=None,
    repost_faults=None, subsequent_faults=None, verdict_faults=None,
):
    """*repost_faults* are the lines the results cascade could not post (#237).

    Both repost functions return a list of faults rather than ``None``, so the stubs must
    too: the approvals now add what comes back to what they report.
    """
    attendance_errors = attendance_errors or {}
    return {
        "snapshot": patch(
            "services.result_submission_service._snapshot_staged_drivers",
            new=AsyncMock(return_value=[]),
        ),
        "recompute": patch(
            "services.result_submission_service._recompute_session_points", new=AsyncMock()
        ),
        "apply": patch(
            "services.penalty_service.apply_penalties",
            new=AsyncMock(return_value=apply_result if apply_result is not None else [{}]),
        ),
        "repost": patch(
            "services.results_post_service.delete_and_repost_final_results",
            new=AsyncMock(return_value=list(repost_faults or [])),
        ),
        "subsequent": patch(
            "services.results_post_service.repost_subsequent_standings",
            new=AsyncMock(return_value=list(subsequent_faults or [])),
        ),
        "banner": patch(
            "services.verdict_announcement_service.banner_for_round", new=MagicMock()
        ),
        # Both return the verdicts they could not announce, so the stubs must too (#237):
        # a bare AsyncMock returns a truthy MagicMock, which would report a fault on every
        # approval that announced perfectly well.
        "penalty_announce": patch(
            "services.verdict_announcement_service.post_penalty_announcements",
            new=AsyncMock(
                side_effect=announce_error, return_value=list(verdict_faults or [])
            ),
        ),
        "appeal_announce": patch(
            "services.verdict_announcement_service.post_appeal_announcements",
            new=AsyncMock(
                side_effect=announce_error, return_value=list(verdict_faults or [])
            ),
        ),
        "record": patch(
            "services.attendance_service.record_attendance_from_results",
            new=AsyncMock(side_effect=attendance_errors.get("record")),
        ),
        "distribute": patch(
            "services.attendance_service.distribute_attendance_points",
            new=AsyncMock(side_effect=attendance_errors.get("distribute")),
        ),
        "sheet": patch(
            "services.attendance_service.post_attendance_sheet",
            new=AsyncMock(side_effect=attendance_errors.get("sheet")),
        ),
        "sanctions": patch(
            "services.attendance_service.enforce_attendance_sanctions",
            new=AsyncMock(
                side_effect=attendance_errors.get("sanctions"),
                return_value=sanction_outcome or SanctionOutcome(),
            ),
        ),
        "appeals_view": patch("services.penalty_wizard.AppealsReviewView", new=MagicMock()),
        "appeals_prompt": patch(
            "services.penalty_wizard._render_appeals_prompt_content",
            new=AsyncMock(return_value="appeals prompt"),
        ),
        "close": patch(
            "services.result_submission_service.close_submission_channel", new=AsyncMock()
        ),
        "refresh": patch(
            "services.season_service.SeasonService.refresh_division_status", new=AsyncMock()
        ),
    }


async def _run(fn, state, interaction=None, **patch_kwargs):
    interaction = interaction or _interaction()
    patches = _patches(**patch_kwargs)
    started = {key: p.start() for key, p in patches.items()}
    try:
        await fn(interaction, state)
    finally:
        for p in patches.values():
            p.stop()
    return started


async def _round_status(db_path) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (ROUND_ID,))
        return (await cursor.fetchone())["status"]


async def _staged_column(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT staged_penalties FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        return (await cursor.fetchone())["staged_penalties"]


def _logged(state) -> str:
    return "\n".join(str(c.args[0]) for c in state.bot.output_router.post_log.await_args_list)


# ---------------------------------------------------------------------------
# The penalty review
# ---------------------------------------------------------------------------


async def test_staged_penalties_are_applied(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])

    stubs = await _run(finalize_penalty_review, state)

    stubs["apply"].assert_awaited_once()
    stubs["recompute"].assert_awaited_once()


async def test_the_penalties_are_recorded_before_they_are_applied(tmp_path):
    """So a second press, or a recovered finalisation, finds them already there."""
    db_path = await _make_db(tmp_path, name="finalize_recorded")
    state = _state(db_path, staged=[_penalty()])
    seen: dict = {}

    async def _record(*_a, **_k):
        seen["column"] = await _staged_column(db_path)
        return [{}]

    patches = _patches()
    patches["apply"] = patch(
        "services.penalty_service.apply_penalties", new=AsyncMock(side_effect=_record)
    )
    for p in patches.values():
        p.start()
    try:
        await finalize_penalty_review(_interaction(), state)
    finally:
        for p in patches.values():
            p.stop()

    recorded = json.loads(seen["column"])
    assert recorded[0]["driver_user_id"] == 101
    assert recorded[0]["penalty_seconds"] == 5


async def test_penalties_already_applied_are_not_applied_again(tmp_path):
    """Applying twice would double every time penalty on the round, silently."""
    db_path = await _make_db(tmp_path, name="finalize_twice", staged_json="[]")
    state = _state(db_path, staged=[_penalty()])

    stubs = await _run(finalize_penalty_review, state)

    stubs["apply"].assert_not_awaited()


async def test_the_round_moves_on_to_appeals(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_status")

    await _run(finalize_penalty_review, _state(db_path))

    assert await _round_status(db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.parametrize("status", ["FINAL", "CANCELLED"])
async def test_a_settled_round_is_not_reopened_by_a_stale_press(tmp_path, status):
    """#167: a client still holding the review message can press it after the round was
    closed, and an unguarded write would strand the season."""
    db_path = await _make_db(tmp_path, name=f"finalize_settled_{status}", round_status=status)

    await _run(finalize_penalty_review, _state(db_path))

    assert await _round_status(db_path) == status


async def test_the_results_are_reposted_as_post_race_penalty_results(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_repost")

    stubs = await _run(finalize_penalty_review, _state(db_path, staged=[_penalty()]))

    assert stubs["repost"].await_args.kwargs["label"] == "Post-Race Penalty Results"
    stubs["subsequent"].assert_awaited_once()


async def test_the_approval_is_logged_with_its_penalty_count(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_log")
    state = _state(db_path, staged=[_penalty(), _penalty(102)])

    await _run(finalize_penalty_review, state)

    logged = _logged(state)
    assert "PENALTY_REVIEW_APPROVED" in logged
    assert "penalties: 2" in logged


async def test_a_review_with_no_penalties_says_none(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_log_none")
    state = _state(db_path)

    await _run(finalize_penalty_review, state)

    assert "penalties: none" in _logged(state)


async def test_a_failing_audit_log_does_not_stop_the_review(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_logfail")
    state = _state(db_path)
    state.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log"))
    interaction = _interaction()

    await _run(finalize_penalty_review, state, interaction)

    assert state.appeals_prompt_message_id == 9900


async def test_applied_penalties_are_announced(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_announce")

    stubs = await _run(
        finalize_penalty_review, _state(db_path, staged=[_penalty()]), apply_result=[{"id": 1}]
    )

    stubs["penalty_announce"].assert_awaited_once()


async def test_a_failed_announcement_does_not_stop_the_review(tmp_path):
    """The penalties are applied and the results reposted by then."""
    db_path = await _make_db(tmp_path, name="finalize_announce_fail")
    state = _state(db_path, staged=[_penalty()])

    await _run(
        finalize_penalty_review,
        state,
        apply_result=[{"id": 1}],
        announce_error=RuntimeError("no verdicts channel"),
    )

    assert await _round_status(db_path) == "AWAITING_APPEAL_VERDICTS"
    assert state.appeals_prompt_message_id == 9900


async def test_the_appeals_prompt_is_posted_and_registered(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_prompt")
    state = _state(db_path)

    await _run(finalize_penalty_review, state)

    state.bot.add_view.assert_called_once()
    assert state.appeals_prompt_message_id == 9900


async def test_without_a_guild_the_round_still_moves_on(tmp_path):
    db_path = await _make_db(tmp_path, name="finalize_noguild")

    stubs = await _run(finalize_penalty_review, _state(db_path), _interaction(guild=False))

    stubs["repost"].assert_not_awaited()
    assert await _round_status(db_path) == "AWAITING_APPEAL_VERDICTS"


# ---------------------------------------------------------------------------
# The attendance pipeline inside it
# ---------------------------------------------------------------------------


async def test_the_attendance_pipeline_runs_where_attendance_is_on(tmp_path):
    db_path = await _make_db(tmp_path, name="att_on")

    stubs = await _run(finalize_penalty_review, _state(db_path, attendance_enabled=True))

    for step in ("record", "distribute", "sheet", "sanctions"):
        stubs[step].assert_awaited_once()


async def test_the_attendance_pipeline_does_not_run_where_attendance_is_off(tmp_path):
    db_path = await _make_db(tmp_path, name="att_off")

    stubs = await _run(finalize_penalty_review, _state(db_path, attendance_enabled=False))

    for step in ("record", "distribute", "sheet", "sanctions"):
        stubs[step].assert_not_awaited()


async def _later_rounds(db_path, statuses: dict[int, str]) -> None:
    """Rounds after the fixture's round 3, by number, with the ids 100 + the number."""
    async with get_connection(db_path) as db:
        for number, status in statuses.items():
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "status) VALUES (?, ?, ?, '2026-03-01T18:00:00+00:00', 'NORMAL', ?)",
                (100 + number, DIVISION_ID, number, status),
            )
        await db.commit()


async def test_an_amended_round_redistributes_every_later_round(tmp_path):
    """Issue #238. `/round results amend` re-runs this review for a round that may sit well
    behind the season's latest, and every later round's stored total was worked out from the
    figure the amendment has just changed. A round not yet finalised holds no total to
    correct and is left alone."""
    db_path = await _make_db(tmp_path, name="att_cascade")
    await _later_rounds(db_path, {4: "FINAL", 5: "AWAITING_APPEAL_VERDICTS", 6: "NOT_RUN"})

    stubs = await _run(finalize_penalty_review, _state(db_path, attendance_enabled=True))

    assert [c.args[1] for c in stubs["distribute"].await_args_list] == [ROUND_ID, 104, 105]


async def test_the_sheet_and_the_sanctions_follow_the_cascade_to_its_last_round(tmp_path):
    """Issue #238. Each round's stored total is the driver's total as at that round, so
    amending round 3 of ten leaves the division's current standing on the last round scored —
    and it is the current standing the sheet must show and the thresholds must be read
    from."""
    db_path = await _make_db(tmp_path, name="att_cascade_latest")
    await _later_rounds(db_path, {4: "FINAL", 5: "FINAL"})

    stubs = await _run(finalize_penalty_review, _state(db_path, attendance_enabled=True))

    assert stubs["sheet"].await_args.args[3] == 105
    assert stubs["sanctions"].await_args.args[3] == 105


async def test_a_failed_cascade_leaves_the_sheet_on_the_round_approved(tmp_path):
    """Nothing was written, so there is no later round to follow it to — and the sanctions
    are deferred in any case."""
    db_path = await _make_db(tmp_path, name="att_cascade_failed")
    await _later_rounds(db_path, {4: "FINAL"})

    stubs = await _run(
        finalize_penalty_review,
        _state(db_path, attendance_enabled=True),
        attendance_errors={"distribute": RuntimeError("boom")},
    )

    assert stubs["sheet"].await_args.args[3] == ROUND_ID
    stubs["sanctions"].assert_not_awaited()


async def test_rounds_before_the_amended_one_keep_their_totals(tmp_path):
    db_path = await _make_db(tmp_path, name="att_cascade_earlier")
    await _later_rounds(db_path, {1: "FINAL", 2: "FINAL"})

    stubs = await _run(finalize_penalty_review, _state(db_path, attendance_enabled=True))

    stubs["distribute"].assert_awaited_once()


async def test_staged_pardons_are_persisted(tmp_path):
    db_path = await _make_db(tmp_path, name="att_pardons", attendance_row=True)
    pardon = StagedPardon(
        driver_user_id=101,
        driver_profile_id=31,
        attendance_id=41,
        pardon_type="NO_RSVP",
        justification="Power cut",
        grantor_id=STEWARD,
    )

    await _run(
        finalize_penalty_review, _state(db_path, pardons=[pardon], attendance_enabled=True)
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT attendance_id, pardon_type, justification, granted_by "
            "FROM attendance_pardons"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [
            (41, "NO_RSVP", "Power cut", STEWARD)
        ]


async def test_a_pardon_is_not_granted_twice(tmp_path):
    """A recovered finalisation runs this again; the insert is idempotent."""
    db_path = await _make_db(tmp_path, name="att_pardons_twice", attendance_row=True)
    pardon = StagedPardon(101, 31, 41, "NO_RSVP", "Power cut", STEWARD)

    await _run(
        finalize_penalty_review, _state(db_path, pardons=[pardon], attendance_enabled=True)
    )
    await _run(
        finalize_penalty_review, _state(db_path, pardons=[pardon], attendance_enabled=True)
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM attendance_pardons")
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.parametrize("failing", ["record", "distribute", "sheet", "sanctions"])
async def test_one_failing_attendance_step_does_not_stop_the_others(tmp_path, failing):
    """The penalty verdicts are already published by the time this runs.

    **The sanctions are the one exception, and only for the two steps that write** (#237).
    A failed `record`, or a failed distribution — which since #238 carries the later rounds'
    totals with it — leaves `total_points_after` wrong rather than absent,
    so the candidate query cannot screen it out, and an autosack applied on it would take a
    driver's seat on a number the bot already knows is unsound. Those two defer the run to
    `/attendance sync`; everything else still carries on regardless.
    """
    db_path = await _make_db(tmp_path, name=f"att_fail_{failing}")
    state = _state(db_path, attendance_enabled=True)

    stubs = await _run(
        finalize_penalty_review, state, attendance_errors={failing: RuntimeError("boom")}
    )

    defers_the_sanctions = failing in ("record", "distribute")
    for step in ("record", "distribute", "sheet"):
        stubs[step].assert_awaited_once()
    if defers_the_sanctions:
        stubs["sanctions"].assert_not_awaited()
    else:
        stubs["sanctions"].assert_awaited_once()
    assert state.appeals_prompt_message_id == 9900


async def test_without_a_guild_nothing_is_posted_but_points_are_distributed(tmp_path):
    """The sanctions cannot run without the server — and that is reported, never skipped
    in silence (#239)."""
    db_path = await _make_db(tmp_path, name="att_noguild")
    state = _state(db_path, attendance_enabled=True)
    interaction = _interaction(guild=False)

    stubs = await _run(finalize_penalty_review, state, interaction)

    stubs["distribute"].assert_awaited_once()
    stubs["sheet"].assert_not_awaited()
    stubs["sanctions"].assert_not_awaited()
    assert "could not be reached" in interaction.followup.send.await_args.args[0]
    assert "ATTENDANCE_SANCTIONS | Incomplete" in _logged(state)


async def test_incomplete_sanctions_are_told_to_the_approving_manager(tmp_path):
    """#239. The approval used to report nothing of a sanction that did not apply. The run
    logs its own failures, so the manager is told here and the log is not told twice."""
    db_path = await _make_db(tmp_path, name="att_incomplete")
    state = _state(db_path, attendance_enabled=True)
    interaction = _interaction()
    outcome = SanctionOutcome(failed=[("<@5> (Five)", "autoreserve", "no Reserve team")])

    await _run(finalize_penalty_review, state, interaction, sanction_outcome=outcome)

    told = interaction.followup.send.await_args
    assert "<@5> (Five) — autoreserve: no Reserve team" in told.args[0]
    assert "/attendance sync" in told.args[0]
    assert told.kwargs["ephemeral"] is True
    assert "ATTENDANCE_SANCTIONS" not in _logged(state)


async def test_a_sanction_run_that_raises_reaches_the_log_channel(tmp_path):
    """#239. A run that fails outright never logged anything a league could read."""
    db_path = await _make_db(tmp_path, name="att_raises")
    state = _state(db_path, attendance_enabled=True)
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction,
        attendance_errors={"sanctions": RuntimeError("database is locked")},
    )

    assert "the sanctions could not be run: database is locked" in _logged(state)
    assert "/attendance sync" in interaction.followup.send.await_args.args[0]


async def test_a_clean_sanction_run_tells_the_manager_nothing_more(tmp_path):
    db_path = await _make_db(tmp_path, name="att_clean")
    interaction = _interaction()

    await _run(finalize_penalty_review, _state(db_path, attendance_enabled=True), interaction)

    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# The appeals review
# ---------------------------------------------------------------------------


async def test_approving_appeals_finishes_the_round(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_final", round_status="AWAITING_APPEAL_VERDICTS")

    await _run(finalize_appeals_review, _state(db_path))

    assert await _round_status(db_path) == "FINAL"


async def test_a_cancelled_round_is_not_raised_to_final(tmp_path):
    """By a view that outlived its cancellation."""
    db_path = await _make_db(tmp_path, name="appeals_cancelled", round_status="CANCELLED")

    await _run(finalize_appeals_review, _state(db_path))

    assert await _round_status(db_path) == "CANCELLED"


async def test_the_divisions_status_is_reconsidered(tmp_path):
    """#154: the last round's appeals are what finishes a division."""
    db_path = await _make_db(tmp_path, name="appeals_division", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(finalize_appeals_review, _state(db_path))

    stubs["refresh"].assert_awaited_once()


async def test_the_submission_channel_is_closed(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_close", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(finalize_appeals_review, _state(db_path))

    stubs["close"].assert_awaited_once()


async def test_the_results_are_reposted_as_final(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_repost", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(finalize_appeals_review, _state(db_path))

    assert stubs["repost"].await_args.kwargs["label"] == "Final Results"


async def test_upheld_corrections_are_applied_and_recorded(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_upheld", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(
        finalize_appeals_review,
        _state(db_path, appeals=[_penalty()]),
        apply_result=[{"race_result_id": None, "qual_result_id": None}],
    )

    stubs["apply"].assert_awaited_once()
    assert stubs["apply"].await_args.kwargs["_phase"] == "APPEAL"
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status, penalty_type, time_seconds, description, submitted_by "
            "FROM appeal_records"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [
            ("UPHELD", "TIME", 5, "Corner cutting", str(STEWARD))
        ]


async def test_upheld_corrections_are_announced(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_announce", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(
        finalize_appeals_review,
        _state(db_path, appeals=[_penalty()]),
        apply_result=[{"race_result_id": None, "qual_result_id": None}],
    )

    stubs["appeal_announce"].assert_awaited_once()
    records = stubs["appeal_announce"].await_args.args[2]
    assert records[0]["driver_user_id"] == 101


async def test_a_failed_appeal_announcement_does_not_un_approve_them(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_announce_fail", round_status="AWAITING_APPEAL_VERDICTS")

    stubs = await _run(
        finalize_appeals_review,
        _state(db_path, appeals=[_penalty()]),
        apply_result=[{"race_result_id": None, "qual_result_id": None}],
        announce_error=RuntimeError("no channel"),
    )

    assert await _round_status(db_path) == "FINAL"
    stubs["close"].assert_awaited_once()


async def test_an_appeals_review_with_no_corrections_applies_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_none", round_status="AWAITING_APPEAL_VERDICTS")
    state = _state(db_path)

    stubs = await _run(finalize_appeals_review, state)

    stubs["apply"].assert_not_awaited()
    stubs["appeal_announce"].assert_not_awaited()
    assert "corrections: none" in _logged(state)


async def test_a_failing_appeals_audit_does_not_stop_the_close(tmp_path):
    db_path = await _make_db(tmp_path, name="appeals_logfail", round_status="AWAITING_APPEAL_VERDICTS")
    state = _state(db_path)
    state.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log"))

    stubs = await _run(finalize_appeals_review, state)

    stubs["close"].assert_awaited_once()


# ---------------------------------------------------------------------------
# A repost that did not land reaches the league (#237)
#
# The cascade deletes each message before posting its replacement, so a channel that has
# gone missing leaves the round with nothing posted. It used to be swallowed entirely: the
# approval logged `| Success` and the manager was told the same.
# ---------------------------------------------------------------------------

FAULT = "**Alpha** — the results channel <#501> no longer exists."


async def test_a_repost_that_could_not_post_tells_the_manager(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction, repost_faults=[FAULT]
    )

    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert FAULT in said
    assert "/results rounds sync" in said


async def test_a_repost_that_could_not_post_reaches_the_log_channel(tmp_path):
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])

    await _run(finalize_penalty_review, state, repost_faults=[FAULT])

    logged = _logged(state)
    assert "RESULTS_REPOST | Incomplete" in logged
    assert FAULT in logged


async def test_the_approval_is_logged_as_incomplete_when_the_repost_failed(tmp_path):
    """The audit line says what the approval achieved, not what it attempted."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])

    await _run(finalize_penalty_review, state, repost_faults=[FAULT])

    assert "PENALTY_REVIEW_APPROVED | Incomplete" in _logged(state)


async def test_the_approval_is_logged_as_success_when_everything_posted(tmp_path):
    """The counterpart, so `| Incomplete` cannot be the answer to everything."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])

    await _run(finalize_penalty_review, state)

    logged = _logged(state)
    assert "PENALTY_REVIEW_APPROVED | Success" in logged
    assert "RESULTS_REPOST | Incomplete" not in logged


async def test_a_missing_guild_is_reported_rather_than_skipped(tmp_path):
    """`if guild:` used to skip both reposts without a word (#237)."""
    db_path = await _make_db(tmp_path)
    state = _state(db_path, staged=[_penalty()])

    await _run(finalize_penalty_review, state, _interaction(guild=False))

    logged = _logged(state)
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in logged
    assert "could not be reached" in logged


async def test_the_appeals_approval_reports_an_unpostable_repost(tmp_path):
    """The same, on the approval that takes a round to FINAL."""
    db_path = await _make_db(
        tmp_path, name="appeals_unpostable", round_status="AWAITING_APPEAL_VERDICTS"
    )
    state = _state(db_path, appeals=[_penalty()])
    interaction = _interaction()

    await _run(finalize_appeals_review, state, interaction, repost_faults=[FAULT])

    logged = _logged(state)
    assert "APPEALS_REVIEW_APPROVED | Incomplete" in logged
    assert FAULT in logged
    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert "/results standings sync" in said


async def test_a_fault_both_reposts_found_is_reported_once(tmp_path):
    """Both reposts run over the same division and word it identically (#237 review).

    The manager repairs one channel, so reading the same bullet twice is a false count of
    the problems in front of them.
    """
    db_path = await _make_db(tmp_path, name="dup_fault")
    state = _state(db_path, staged=[_penalty()])
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction,
        repost_faults=[FAULT], subsequent_faults=[FAULT],
    )

    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert said.count(FAULT) == 1
    assert _logged(state).count(FAULT) == 1


async def test_a_log_channel_that_refuses_still_tells_the_manager(tmp_path):
    """The report is two messages, and neither may take the other down with it.

    The log channel is the league's record and the reply is the manager's; a failure to
    write one must not swallow the other, or the approval goes back to being silent in
    exactly the way #237 is about.
    """
    db_path = await _make_db(tmp_path, name="log_refuses")
    state = _state(db_path, staged=[_penalty()])
    state.bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction, repost_faults=[FAULT]
    )

    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert FAULT in said


async def test_a_reply_that_fails_does_not_stop_the_approval(tmp_path):
    """The manager may have dismissed the interaction; the approval still stands."""
    db_path = await _make_db(tmp_path, name="reply_fails")
    state = _state(db_path, staged=[_penalty()])
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=RuntimeError("unknown webhook"))

    await _run(
        finalize_penalty_review, state, interaction, repost_faults=[FAULT]
    )

    assert "RESULTS_REPOST | Incomplete" in _logged(state)
    assert await _round_status(db_path) == "AWAITING_APPEAL_VERDICTS"


# ---------------------------------------------------------------------------
# A verdict that was not announced, and attendance that was not recorded (#237)
# ---------------------------------------------------------------------------

VERDICT_FAULT = "**Alpha** — the verdict for <@99> was not announced: Missing Permissions."


async def test_an_unannounced_verdict_reaches_the_manager_and_the_log(tmp_path):
    db_path = await _make_db(tmp_path, name="verdict_fault")
    state = _state(db_path, staged=[_penalty()])
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction, verdict_faults=[VERDICT_FAULT]
    )

    logged = _logged(state)
    assert "VERDICTS | Incomplete" in logged
    assert VERDICT_FAULT in logged
    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert VERDICT_FAULT in said


async def test_the_verdict_report_says_it_cannot_be_announced_again(tmp_path):
    """Telling a manager to re-run something would leave them believing it finished."""
    db_path = await _make_db(tmp_path, name="verdict_hint")
    state = _state(db_path, staged=[_penalty()])
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction, verdict_faults=[VERDICT_FAULT]
    )

    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert "cannot announce a verdict a second time" in said


async def test_an_approval_that_announced_everything_reports_no_verdict_fault(tmp_path):
    """The counterpart — the stub returning an empty list must stay silent."""
    db_path = await _make_db(tmp_path, name="verdict_clean")
    state = _state(db_path, staged=[_penalty()])

    await _run(finalize_penalty_review, state)

    assert "VERDICTS | Incomplete" not in _logged(state)


async def test_an_appeal_verdict_that_was_not_announced_is_reported(tmp_path):
    db_path = await _make_db(
        tmp_path, name="appeal_verdict", round_status="AWAITING_APPEAL_VERDICTS"
    )
    state = _state(db_path, appeals=[_penalty()])

    await _run(finalize_appeals_review, state, verdict_faults=[VERDICT_FAULT])

    assert "VERDICTS | Incomplete" in _logged(state)


@pytest.mark.parametrize("failing", ["record", "distribute"])
async def test_attendance_that_was_not_recorded_is_reported(tmp_path, failing):
    """The two steps that write to the database, which leave the record wrong (#237).

    Reported under a heading of their own rather than with the sanctions: that report skips
    the log channel when the sanctions run has already written its own entry.
    """
    db_path = await _make_db(tmp_path, name=f"att_{failing}", attendance_row=True)
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    interaction = _interaction()

    await _run(
        finalize_penalty_review, state, interaction,
        attendance_errors={failing: RuntimeError("disk is full")},
    )

    logged = _logged(state)
    assert "ATTENDANCE_RECORD | Incomplete" in logged
    assert "disk is full" in logged
    said = "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)
    assert "/attendance sync" in said


async def test_a_posting_step_that_fails_is_not_reported_as_a_record_failure(tmp_path):
    """The sheet is a picture of the record, not the record — its failure is a different kind."""
    db_path = await _make_db(tmp_path, name="att_sheet", attendance_row=True)
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)

    await _run(
        finalize_penalty_review, state,
        attendance_errors={"sheet": RuntimeError("no attendance channel")},
    )

    assert "ATTENDANCE_RECORD | Incomplete" not in _logged(state)


@pytest.mark.parametrize("failing", ["record", "distribute"])
async def test_the_sanctions_are_not_run_on_a_record_known_to_be_wrong(tmp_path, failing):
    """An autosack takes a driver's seat, so it is never applied on unsound totals (#237).

    The thresholds read `total_points_after`, which the two failing steps write. Where
    recording fails but distribution succeeds the column is populated and *wrong* — and can
    be wrong upward, a driver who attended having been scored absent — so the candidate
    query does not screen it out. `/attendance sync` repairs the record and applies whatever
    is owed, which is where the run belongs.
    """
    db_path = await _make_db(tmp_path, name=f"defer_{failing}", attendance_row=True)
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)

    stubs = await _run(
        finalize_penalty_review, state,
        attendance_errors={failing: RuntimeError("disk is full")},
    )

    stubs["sanctions"].assert_not_awaited()
    logged = _logged(state)
    assert "ATTENDANCE_RECORD | Incomplete" in logged
    assert "no driver was checked" in logged


async def test_the_sanctions_still_run_when_the_record_is_sound(tmp_path):
    """The counterpart — a failed *posting* must not defer them."""
    db_path = await _make_db(tmp_path, name="defer_none", attendance_row=True)
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)

    stubs = await _run(
        finalize_penalty_review, state,
        attendance_errors={"sheet": RuntimeError("no attendance channel")},
    )

    stubs["sanctions"].assert_awaited()
