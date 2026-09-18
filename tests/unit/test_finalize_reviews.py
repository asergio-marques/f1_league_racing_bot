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
so a recovered finalisation cannot grant one twice.

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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
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


def _patches(*, apply_result=None, announce_error=None, attendance_errors=None):
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
            "services.results_post_service.delete_and_repost_final_results", new=AsyncMock()
        ),
        "subsequent": patch(
            "services.results_post_service.repost_subsequent_standings", new=AsyncMock()
        ),
        "banner": patch(
            "services.verdict_announcement_service.banner_for_round", new=MagicMock()
        ),
        "penalty_announce": patch(
            "services.verdict_announcement_service.post_penalty_announcements",
            new=AsyncMock(side_effect=announce_error),
        ),
        "appeal_announce": patch(
            "services.verdict_announcement_service.post_appeal_announcements",
            new=AsyncMock(side_effect=announce_error),
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
            new=AsyncMock(side_effect=attendance_errors.get("sanctions")),
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
    return "\n".join(str(c.args[1]) for c in state.bot.output_router.post_log.await_args_list)


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
    """The penalty verdicts are already published by the time this runs."""
    db_path = await _make_db(tmp_path, name=f"att_fail_{failing}")
    state = _state(db_path, attendance_enabled=True)

    stubs = await _run(
        finalize_penalty_review, state, attendance_errors={failing: RuntimeError("boom")}
    )

    for step in ("record", "distribute", "sheet", "sanctions"):
        stubs[step].assert_awaited_once()
    assert state.appeals_prompt_message_id == 9900


async def test_without_a_guild_nothing_is_posted_but_points_are_distributed(tmp_path):
    db_path = await _make_db(tmp_path, name="att_noguild")

    stubs = await _run(
        finalize_penalty_review,
        _state(db_path, attendance_enabled=True),
        _interaction(guild=False),
    )

    stubs["distribute"].assert_awaited_once()
    stubs["sheet"].assert_not_awaited()
    stubs["sanctions"].assert_not_awaited()


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
