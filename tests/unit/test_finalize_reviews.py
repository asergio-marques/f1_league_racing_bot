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
from services.results_post_service import ReplayOutcome  # noqa: E402
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
        # The amendment's own rebuild (#345). Stubbed so the appeals finaliser can be driven
        # with `is_amendment=True` without reaching Discord or the attendance module.
        "replay": patch(
            "services.results_post_service.replay_division_channels",
            new=AsyncMock(return_value=ReplayOutcome([], frozenset({ROUND_ID}))),
        ),
        "amend_attendance": patch(
            "services.result_submission_service._repost_attendance_after_amendment",
            new=AsyncMock(return_value=[]),
        ),
        "cascade_standings": patch(
            "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
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


async def _run(fn, state, interaction=None, *, replay_override=False, **patch_kwargs):
    """*replay_override* leaves the replay unpatched so a caller can patch it themselves."""
    interaction = interaction or _interaction()
    patches = _patches(**patch_kwargs)
    if replay_override:
        patches.pop("replay", None)
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


# ---------------------------------------------------------------------------
# An amendment rewrites the round's decisions rather than adding to them (#345)
# ---------------------------------------------------------------------------
#
# These drive the finaliser with `apply_penalties` **real** and count rows, because that is the
# only thing that catches the defect they exist for. `apply_penalties` only ever inserts, and
# adds to the stored penalty columns; replaying a round's reports over records still in place
# duplicated every one of them, and doubled the sanction again on a second amendment. The
# structural assertions in `test_amendment_replays_without_moving_the_round.py` cannot see any
# of that — an earlier version of that file asserted the defect and called it correct.


async def _verdict_count(db_path, table: str = "penalty_records") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return (await cursor.fetchone())["n"]


async def _pardon_count(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM attendance_pardons")
        return (await cursor.fetchone())["n"]


async def _seed_driver_row(db_path, driver: int = 101) -> None:
    """A race result row for the penalised driver, which `apply_penalties` attaches to.

    The shared fixture seeds a session header and no driver rows — enough for every test that
    stubs `apply_penalties`, and not enough for one that lets it write.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
            (ROUND_ID, "FEATURE_RACE"),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (ROUND_ID, DIVISION_ID),
            )
            session_id = cursor.lastrowid
        else:
            session_id = row["id"]
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, ?, 3001, 1)",
            (session_id, driver),
        )
        await db.commit()


#: The deadline an open amendment carries in these tests: far enough off never to lapse.
_OPEN_DEADLINE = "2099-01-01T00:00:00+00:00"


async def _open_amendment(state) -> None:
    """Mark *state* an amendment, and give it the open amendment it would have in life.

    Every stage of an amendment first claims the amendment's deadline (#345); with no
    `round_amend_channels` row there is nothing to claim, and the stage refuses to run — which
    would let a test asserting that something did *not* happen pass without the stage having
    run at all.
    """
    state.is_amendment = True
    async with get_connection(state.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO round_amend_channels (round_id, channel_id, session_types, "
            "created_at, pre_amendment_state, expires_at) VALUES (?, 700, '[\"FEATURE_RACE\"]', "
            "'2026-02-02T00:00:00+00:00', '{}', ?)",
            (state.round_id, _OPEN_DEADLINE),
        )
        await db.commit()


async def _run_real_apply(fn, state, interaction=None, **patch_kwargs):
    """As `_run`, but with `apply_penalties` left real so its writes can be counted."""
    interaction = interaction or _interaction()
    patches = {k: v for k, v in _patches(**patch_kwargs).items() if k != "apply"}
    started = {key: p.start() for key, p in patches.items()}
    try:
        await fn(interaction, state)
    finally:
        for p in patches.values():
            p.stop()
    return started


async def test_an_amendment_does_not_duplicate_the_rounds_penalty_records(tmp_path):
    """**The defect the independent review found.**

    Stage two hydrates the round's existing reports into `state.staged` and approving re-applies
    them. With the old records still in place that left two rows for one incident — and a second
    amendment then hydrated both, applying twice the sanction the steward gave.
    """
    db_path = await _make_db(tmp_path, name="amend_no_dupe")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)
    first = await _verdict_count(db_path)

    # Replay it again, as a second amendment of the same round would.
    state_again = _state(db_path, staged=[_penalty()])
    await _open_amendment(state_again)
    await _run_real_apply(finalize_penalty_review, state_again)

    assert first == 1
    assert await _verdict_count(db_path) == 1


async def test_an_amendment_writes_the_reports_the_manager_approved(tmp_path):
    """The other half: the edits must actually land.

    The idempotence probe read the *first pass's* `staged_penalties` column, which an amendment
    neither owns nor resets — so on a round originally reviewed with penalties it read "already
    applied" and discarded every edit the manager had just made, silently.
    """
    db_path = await _make_db(tmp_path, name="amend_edits_land")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET staged_penalties = ? WHERE round_id = ?",
            ('[{"driver_user_id": 101}]', ROUND_ID),
        )
        await db.commit()
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    assert await _verdict_count(db_path) == 1


async def test_an_amendment_does_not_overwrite_the_first_passs_crash_guard(tmp_path):
    """That column belongs to the original review; an amendment must leave it as it found it."""
    db_path = await _make_db(tmp_path, name="amend_guard_intact")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET staged_penalties = ? WHERE round_id = ?",
            ('["original"]', ROUND_ID),
        )
        await db.commit()
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    assert await _staged_column(db_path) == '["original"]'


async def test_a_first_pass_still_records_and_applies_once(tmp_path):
    """The ordinary path is untouched — the guards must not have cost it its own behaviour."""
    db_path = await _make_db(tmp_path, name="first_pass_intact")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])

    await _run_real_apply(finalize_penalty_review, state)

    assert await _verdict_count(db_path) == 1
    assert await _staged_column(db_path) is not None


async def test_a_report_removed_in_stage_two_is_removed_from_the_record(tmp_path):
    """Delete-and-rewrite is what makes the stage editable at all.

    Approving with a report taken out has to leave it out; `INSERT OR IGNORE` semantics would
    have kept the row and made the Remove button decorative.
    """
    db_path = await _make_db(tmp_path, name="amend_removal")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    await _run_real_apply(finalize_penalty_review, state)
    assert await _verdict_count(db_path) == 1

    # The manager removes it and approves again.
    emptied = _state(db_path, staged=[])
    await _open_amendment(emptied)
    await _run_real_apply(finalize_penalty_review, emptied)

    assert await _verdict_count(db_path) == 0


async def test_an_amendment_does_not_post_the_attendance_sheet_itself(tmp_path):
    """The sheet and the sanctions belong to the final stage (#345).

    Running both posted two sheets — the first built on attended flags describing the round
    being replaced, because this stage's cascade does not rebuild them — and enforced the
    sanctions twice, so a driver could be sacked by a sheet the next stage was about to correct.
    """
    db_path = await _make_db(tmp_path, name="amend_no_sheet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    stubs = await _run(finalize_penalty_review, state)

    stubs["sheet"].assert_not_awaited()
    stubs["sanctions"].assert_not_awaited()


async def test_a_first_pass_still_posts_the_attendance_sheet(tmp_path):
    """The counterpart, so the guard cannot become "never post a sheet"."""
    db_path = await _make_db(tmp_path, name="first_pass_sheet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)

    stubs = await _run(finalize_penalty_review, state)

    stubs["sheet"].assert_awaited()


async def test_an_amendment_still_reaches_the_appeal_stage(tmp_path):
    """Leaving the report stage early must not strand the amendment.

    The classification is corrected and the reports approved; with no appeals prompt there is
    no route to the appeals, and none to the rebuild that follows them.
    """
    db_path = await _make_db(tmp_path, name="amend_reaches_appeals")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert state.appeals_prompt_message_id is not None


async def test_an_amendment_announces_each_appeal_verdict_once(tmp_path):
    """The rebuild announces the round's verdicts; announcing them again doubled them (#345).

    `replay_division_channels` re-announces every verdict of every round from the amended one
    forward — the appeals just written among them. Posting them a second time here gave the
    driver the same decision twice, and the second could not be removed: the superseded set was
    captured before either went up, so neither was in it.
    """
    db_path = await _make_db(tmp_path, name="amend_appeal_once")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    stubs = await _run(finalize_appeals_review, state)

    stubs["appeal_announce"].assert_not_awaited()


async def test_a_first_pass_still_announces_its_appeal_verdicts(tmp_path):
    """The counterpart: an ordinary round has no rebuild to announce them for it."""
    db_path = await _make_db(tmp_path, name="first_pass_appeal_announce")
    state = _state(db_path, appeals=[_penalty()])

    stubs = await _run(finalize_appeals_review, state)

    stubs["appeal_announce"].assert_awaited_once()


async def test_an_amendment_rebuilds_the_division_once_at_the_end(tmp_path):
    """The whole point of the third stage: every decision is in, so the channels go back in
    order — and the round-only repost a first pass uses is *not* also run."""
    db_path = await _make_db(tmp_path, name="amend_rebuild_once")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    stubs = await _run(finalize_appeals_review, state)

    stubs["replay"].assert_awaited_once()
    stubs["repost"].assert_not_awaited()
    # The attendance sheet is handed to the rebuild as a step rather than run after it, so that
    # it lands between the standings and the verdicts as the specification states (#345).
    assert stubs["replay"].await_args.kwargs["attendance_step"] is not None


async def test_a_first_pass_reposts_its_own_round_and_not_the_division(tmp_path):
    """Sending every round through the division-wide rebuild would repost the whole
    championship at the end of every ordinary race weekend."""
    db_path = await _make_db(tmp_path, name="first_pass_round_only")
    state = _state(db_path, appeals=[_penalty()])

    stubs = await _run(finalize_appeals_review, state)

    stubs["repost"].assert_awaited_once()
    stubs["replay"].assert_not_awaited()


async def test_an_amendment_does_not_double_an_unamended_sessions_penalties(tmp_path):
    """**The worst defect any review of this change found.**

    `apply_penalties` walks whatever session types the staged set names, and stage one re-inserts
    only the *amended* session's driver rows — at zero. A report hydrated from an unamended
    session was therefore added on top of the milliseconds already standing in that session's
    row: amend the feature race, and the sprint race's 5 s penalty silently became 10 s, taking
    the driver down the sprint classification and costing them points they were never penalised.
    Each further amendment added another 5 s.

    Fixed by scoping the replay to the session being amended, which is what the stage driver now
    puts in `session_types_present`.
    """
    db_path = await _make_db(tmp_path, name="amend_other_session")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position, postrace_time_penalties_ms) "
            "VALUES (?, 101, 3001, 1, 5000)",
            (other.lastrowid,),
        )
        await db.commit()

    # The staged set an amendment of the feature race produces: that session only.
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.postrace_time_penalties_ms AS ms FROM race_session_results r "
            "JOIN session_results sr ON sr.id = r.session_result_id "
            "WHERE sr.session_type = 'SPRINT_RACE'"
        )
        assert (await cursor.fetchone())["ms"] == 5000


async def test_the_other_sessions_verdict_records_survive_an_amendment(tmp_path):
    """Clearing the whole round would drop them, and nothing would write them back.

    The replay only re-approves the amended session's reports, so a record belonging to another
    session has no route back into the database once deleted.
    """
    db_path = await _make_db(tmp_path, name="amend_other_records")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (other.lastrowid,),
        )
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) VALUES (?, 'TIME', 5, "
            "'Sprint contact', 'At fault', '77', '2026-02-02T00:00:00+00:00')",
            (cursor.lastrowid,),
        )
        await db.commit()

    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM penalty_records WHERE description = 'Sprint contact'"
        )
        assert (await cursor.fetchone())["n"] == 1


async def test_the_deadline_is_cleared_before_the_rebuild_begins(tmp_path):
    """Or the sweep reverts the round from under a rebuild that is still posting (#345).

    A division-wide rebuild throttles a second between postings and renders graphics, so it can
    outlast the stage timeout. Approving the appeals is the commitment.
    """
    db_path = await _make_db(tmp_path, name="amend_deadline_cleared")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, "
            "created_at, pre_amendment_state, expires_at) "
            "VALUES (?, 700, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00+00:00', '{}', "
            "'2026-02-02T00:30:00+00:00')",
            (ROUND_ID,),
        )
        await db.commit()
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    seen: dict = {}

    async def _replay(*_a, **_kw):
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT expires_at FROM round_amend_channels WHERE round_id = ?", (ROUND_ID,)
            )
            row = await cursor.fetchone()
            seen["expires_at"] = row["expires_at"] if row else "gone"
        return ReplayOutcome([], frozenset({ROUND_ID}))

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(side_effect=_replay),
    ):
        await _run(finalize_appeals_review, state, replay_override=True)

    assert seen["expires_at"] is None


# ---------------------------------------------------------------------------
# The amendment's stages publish nothing before the last, and run once (#345)
# ---------------------------------------------------------------------------


async def _deadline(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT expires_at FROM round_amend_channels")
        row = await cursor.fetchone()
        return row["expires_at"] if row else "gone"


async def _postrace_ms(db_path, driver: int = 101) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT postrace_time_penalties_ms AS ms FROM race_session_results "
            "WHERE driver_user_id = ?",
            (driver,),
        )
        return (await cursor.fetchone())["ms"]


async def test_the_amendments_report_stage_publishes_nothing(tmp_path):
    """**Nothing is published until the last stage.** It reposted the round as Post-Race Penalty
    Results and announced every penalty — which an amendment reverted afterwards left standing,
    the revert restoring the round and not the channels."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_quiet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    stubs = await _run(finalize_penalty_review, state)

    stubs["repost"].assert_not_awaited()
    stubs["subsequent"].assert_not_awaited()
    stubs["penalty_announce"].assert_not_awaited()
    stubs["record"].assert_not_awaited()
    assert await _round_status(db_path) == "AWAITING_REPORT_VERDICTS"


async def test_the_report_stage_hands_its_deadline_back(tmp_path):
    """The claim is released once the stage is done, or the sweep would never revert an
    amendment abandoned at the appeals.

    **The same deadline, not a fresh one** (decided 2026-09-21): the half hour covers both review
    stages from the moment stage one wrote, and a league is expected to arrive prepared."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_rearmed")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert await _deadline(db_path) == _OPEN_DEADLINE


async def test_approving_the_report_stage_twice_applies_the_reports_once(tmp_path):
    """`apply_penalties` adds to the penalty columns, and stage one wrote them at zero. A second
    press cleared the records and applied every report again — on top of the milliseconds the
    first had already added — doubling the sanction with a single, correct-looking record."""
    db_path = await _make_db(tmp_path, name="amend_double_press")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)
    await _run_real_apply(finalize_penalty_review, state)

    assert await _verdict_count(db_path) == 1
    assert await _postrace_ms(db_path) == 5000


async def test_a_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    """Lapsed, cancelled, or already being approved by another press: nothing is written."""
    db_path = await _make_db(tmp_path, name="amend_not_open")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    state.is_amendment = True  # no open amendment to claim
    interaction = _interaction()

    await _run_real_apply(finalize_penalty_review, state, interaction)

    assert await _verdict_count(db_path) == 0
    assert "no longer open" in str(interaction.followup.send.await_args.args[0])


async def test_a_report_stage_that_fails_part_way_is_undone(tmp_path):
    """The records are cleared before the reports are written back, so a failure between the two
    would otherwise leave the session carrying none of its decisions."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_fails")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ) as close, patch(
        "services.penalty_service.apply_penalties",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        await _run_real_apply(finalize_penalty_review, state, interaction)

    revert.assert_awaited_once()
    close.assert_awaited_once()
    assert "AMEND_FAILED" in _logged(state)
    assert "put back as it was" in str(interaction.followup.send.await_args.args[0])
    assert state.appeals_prompt_message_id is None


async def test_a_kept_report_keeps_its_author_and_its_time(tmp_path):
    """**A verdict follows its driver with its justification, its author and its time.** The
    report stage writes the session's decisions out again, and stamping each with the admin who
    amended the round — at the moment they did — would lose the audit the amendment exists to
    keep."""
    db_path = await _make_db(tmp_path, name="amend_provenance")
    await _seed_driver_row(db_path)
    kept = _penalty()
    kept.decided_by = "4242"
    kept.decided_at = "2026-02-01T20:00:00"
    state = _state(db_path, staged=[kept])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT applied_by, applied_at FROM penalty_records")
        row = await cursor.fetchone()
    assert (row["applied_by"], row["applied_at"]) == ("4242", "2026-02-01T20:00:00")


async def test_a_report_added_during_the_amendment_names_who_approved_it(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_new_report")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT applied_by FROM penalty_records")
        assert (await cursor.fetchone())["applied_by"] == str(STEWARD)


async def test_the_appeal_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_appeals_not_open")
    state = _state(db_path, appeals=[_penalty()])
    state.is_amendment = True
    interaction = _interaction()

    stubs = await _run(finalize_appeals_review, state, interaction)

    stubs["apply"].assert_not_awaited()
    stubs["replay"].assert_not_awaited()
    stubs["close"].assert_not_awaited()


async def test_a_committed_amendment_is_logged_as_result_amended(tmp_path):
    """The README tells a league to look for `RESULT_AMENDED`; the three-stage rebuild had
    stopped writing it anywhere, so a completed amendment left no record of itself."""
    db_path = await _make_db(tmp_path, name="amend_logged")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    logged = _logged(state)
    assert "RESULT_AMENDED | Success" in logged
    assert "sessions: FEATURE_RACE" in logged
    assert "APPEALS_REVIEW_APPROVED" not in logged


async def test_what_the_rebuild_could_not_post_is_named_in_result_amended(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_logged_faults")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["the standings channel refused"], frozenset({ROUND_ID}))),
    ):
        await _run(finalize_appeals_review, state, interaction, replay_override=True)

    logged = _logged(state)
    assert "RESULT_AMENDED | Incomplete" in logged
    assert "the standings channel refused" in logged
    assert "the standings channel refused" in _replied_text(interaction)


def _replied_text(interaction) -> str:
    return "\n".join(
        str(c.args[0]) for c in interaction.followup.send.await_args_list if c.args
    )


async def test_the_amendment_rewrites_the_rounds_pardons_at_its_last_stage(tmp_path):
    """A pardon removed in the report stage is removed from the round; one kept keeps the time
    it was granted. Written at the last stage, after the snapshot is released, because the revert
    restores the classification and not the attendance record."""
    db_path = await _make_db(tmp_path, name="amend_pardons", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'NO_RSVP', 'Removed', '5', "
            "'2026-02-01T21:00:00')"
        )
        await db.commit()
    kept = StagedPardon(
        driver_user_id=101, driver_profile_id=31, attendance_id=41, pardon_type="ABSENT",
        justification="Old", grantor_id=5, granted_at="2026-02-01T21:00:00",
    )
    state = _state(db_path, pardons=[kept], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT pardon_type, granted_at FROM attendance_pardons")
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [("ABSENT", "2026-02-01T21:00:00")]


async def test_the_pardons_are_left_alone_with_attendance_off(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_pardons_off", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.commit()
    state = _state(db_path, pardons=[], attendance_enabled=False)
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    assert await _pardon_count(db_path) == 1


async def test_the_report_stage_leaves_the_pardons_to_the_last_stage(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_pardons_wait", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.commit()
    state = _state(db_path, staged=[_penalty()], pardons=[], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert await _pardon_count(db_path) == 1


async def test_a_kept_appeal_keeps_its_author_and_its_time(tmp_path):
    """Both rows an upheld appeal writes — its appeal record and the penalty row beside it."""
    db_path = await _make_db(tmp_path, name="amend_appeal_provenance")
    await _seed_driver_row(db_path)
    kept = _penalty()
    kept.decided_by = "4343"
    kept.decided_at = "2026-02-03T20:00:00"
    state = _state(db_path, appeals=[kept])
    await _open_amendment(state)

    await _run_real_apply(finalize_appeals_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT submitted_by, submitted_at FROM appeal_records")
        appeal = tuple(await cursor.fetchone())
        cursor = await db.execute("SELECT applied_by, applied_at FROM penalty_records")
        penalty = tuple(await cursor.fetchone())
    assert appeal == ("4343", "2026-02-03T20:00:00")
    assert penalty == ("4343", "2026-02-03T20:00:00")


async def test_an_amendment_whose_appeal_stage_cannot_open_is_undone(tmp_path):
    """There is no route to the last stage, so leaving it would strand the round until the sweep
    reverted it half an hour later with the manager told nothing."""
    db_path = await _make_db(tmp_path, name="amend_no_stage_three")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction(guild=False)  # no guild, so no channel to post the stage in

    with patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ) as close:
        await _run(finalize_penalty_review, state, interaction)

    revert.assert_awaited_once()
    close.assert_awaited_once()
    assert "AMEND_FAILED" in _logged(state)
    assert "could not be opened" in _logged(state)
    # The deadline was never handed back, so nothing else could act on the amendment while it
    # was being undone — the claim is the caller's to hold until it is done with it.
    assert await _deadline(db_path) is None


async def test_a_rebuild_that_raises_still_closes_the_amendment(tmp_path):
    """**Nothing after the snapshot is released may leave the amendment half-closed** (#345).

    The snapshot and the deadline are gone by then, so the sweep cannot reach the row and
    `cancel_amendment` refuses it — and the cog's duplicate check would refuse the manager a
    second attempt for as long as it stood. The fault is reported and the channel closed.
    """
    db_path = await _make_db(tmp_path, name="amend_rebuild_raises")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(side_effect=RuntimeError("gateway closed")),
    ):
        stubs = await _run(finalize_appeals_review, state, interaction, replay_override=True)

    stubs["close"].assert_awaited_once()
    logged = _logged(state)
    assert "RESULT_AMENDED | Incomplete" in logged
    assert "gateway closed" in logged


async def test_an_appeal_stage_that_raises_while_opening_is_undone_too(tmp_path):
    """Not only an unreachable channel: a send that fails, a prompt that will not render, a view
    that will not register. Any of them leaves the amendment with no route to its last stage,
    and the claim is still held — so it is undone here rather than left claimed for ever,
    invisible to the sweep and refused by Cancel."""
    db_path = await _make_db(tmp_path, name="amend_stage_three_raises")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "services.result_submission_service._post_appeals_prompt",
        new=AsyncMock(side_effect=RuntimeError("gateway closed")),
    ), patch(
        "services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ):
        await _run(finalize_penalty_review, state, interaction)

    revert.assert_awaited_once()
    assert "gateway closed" in _logged(state)
    assert await _deadline(db_path) is None


async def test_the_old_announcements_are_left_standing_where_nothing_replaced_them(tmp_path):
    """**A verdict deleted from a channel is in no channel at all.**

    The take-down follows the republish that replaced them, so a rebuild that never ran — for
    want of a guild, or because it raised — leaves them where they are. The results they belong
    to were not reposted either, so the league reads the round exactly as it did before, and the
    log says what could not be done (#345).
    """
    db_path = await _make_db(tmp_path, name="amend_takedown_skipped")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, _interaction(guild=False))

    taken_down.assert_not_awaited()


async def test_the_old_announcements_come_down_once_their_replacements_are_up(tmp_path):
    """Produce-then-destroy across the two stages: the rebuild announces the round's verdicts
    afresh, and only then are the announcements they replace removed."""
    db_path = await _make_db(tmp_path, name="amend_takedown_runs")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state)

    taken_down.assert_awaited_once()


async def test_the_old_announcements_stay_where_the_verdicts_were_not_re_announced(tmp_path):
    """The rebuild reports a verdict that could not be announced as a fault line rather than
    raising, so the fault list alone cannot say whether the amended round was rebuilt — which
    is why the rebuild names the rounds it did rebuild (#345)."""
    db_path = await _make_db(tmp_path, name="amend_verdicts_failed")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["the verdicts were not announced"], frozenset())),
    ), patch(
        "services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, replay_override=True)

    taken_down.assert_not_awaited()


async def test_another_rounds_rebuild_does_not_license_the_take_down(tmp_path):
    """The amended round is the one whose old announcements are at stake; a later round being
    rebuilt says nothing about whether its replacements went up."""
    db_path = await _make_db(tmp_path, name="amend_other_round_rebuilt")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["round 3 failed"], frozenset({ROUND_ID + 1}))),
    ), patch(
        "services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, replay_override=True)

    taken_down.assert_not_awaited()


async def test_announcements_kept_for_want_of_a_replacement_are_named_with_links(tmp_path):
    """Their ids live only on the amendment's row, which closes with the channel — so where they
    are kept, the league is told which, with a way to each, or nothing could ever find them."""
    db_path = await _make_db(tmp_path, name="amend_kept_named")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET superseded_announcements = ? WHERE round_id = ?",
            ('[{"anchor": 8101, "chunks": "[8101]", "channel_id": "6100", '
             '"driver_user_id": 101}]', ROUND_ID),
        )
        await db.commit()
    interaction = _interaction()
    interaction.guild.id = 555

    with patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["one verdict failed"], frozenset())),
    ):
        await _run(finalize_appeals_review, state, interaction, replay_override=True)

    logged = _logged(state)
    assert "left standing" in logged
    assert "https://discord.com/channels/555/6100/8101" in logged


async def test_the_report_stage_rewrites_every_amended_session_and_no_other(tmp_path):
    """**The reports of the sessions an amendment re-entered are reviewed together** (#345,
    decided 2026-09-21), and approving rewrites exactly those — while a session it left alone
    keeps its records, which were never shown and so could not be written back."""
    db_path = await _make_db(tmp_path, name="amend_many_reports")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        rows = {}
        for session_type in ("FEATURE_QUALIFYING", "SPRINT_RACE"):
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, 'ACTIVE')",
                (ROUND_ID, DIVISION_ID, session_type),
            )
            table = (
                "qualifying_session_results" if session_type.endswith("QUALIFYING")
                else "race_session_results"
            )
            cursor = await db.execute(
                f"INSERT INTO {table} (session_result_id, driver_user_id, team_role_id, "
                "finishing_position) VALUES (?, 101, 3001, 1)",
                (session.lastrowid,),
            )
            rows[session_type] = cursor.lastrowid
        await db.execute(
            "INSERT INTO penalty_records (qual_result_id, penalty_type, description, "
            "justification, applied_by, applied_at) VALUES (?, 'DSQ', 'Quali', 'Old', '7', "
            "'2026-02-02T00:00:00')",
            (rows["FEATURE_QUALIFYING"],),
        )
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) VALUES (?, 'TIME', 5, "
            "'Sprint', 'Untouched', '7', '2026-02-02T00:00:00')",
            (rows["SPRINT_RACE"],),
        )
        await db.commit()

    # The feature qualifying and race are amended; their review holds one race report and
    # drops the qualifying one. The sprint race is not part of the amendment.
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT description FROM penalty_records ORDER BY id")
        assert [r[0] for r in await cursor.fetchall()] == ["Sprint", "Corner cutting"]
