"""An amendment replays a round's review stages without moving the round (#345, #439).

The replay reuses the report and appeal *screens* wholesale — the same views, the same staging.
What it must **not** reuse is the part of each first-pass approval that moves the round on or
publishes as it goes, because an amended round has already been everywhere it is going:

- the report approval sets `AWAITING_APPEAL_VERDICTS`, reposts the round, announces its penalties
  and runs the attendance jobs;
- the appeals approval sets `FINAL`, refreshes the division's status, and may wind the season down.

Replaying those against a settled round would send it backwards through states it left weeks
ago, could finish a division or wind down a season a second time, and would publish a round
half-amended — which an amendment abandoned part-way could then not take back.

So an amendment's two stages are changes of their own kinds on the queue (#439, slice 2),
`results.amendment.reports.approve` and `results.amendment.appeals.approve`, carried out by
`results/services/amendment_stage_changes.py`, and the first pass's change types carry no
amendment branch. These tests pin that split, and pin what the two stages' jobs leave out.

**Asserted against the change types' jobs and the parsed source rather than by running them.**
`test_amendment_stage_changes.py` runs the stages through the queue and counts rows; this pins
which jobs each stage can run at all, and what its saves never write. The change types are read
from a queue the real builder (`register_change_types`) has registered them on, as the bot
registers them. This is the technique `test_batch_notice_call_sites.py` used, for the same reason.
"""
from __future__ import annotations

import ast
import functools
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tests.support.change_queue import attach_queue, league_double


SRC = Path(__file__).resolve().parents[2] / "src" / "leaguebot"
MODULE = "results/services/result_submission_service.py"
AMENDMENT = "results/services/amendment_stage_changes.py"
REPORTS = "results.amendment.reports.approve"
APPEALS = "results.amendment.appeals.approve"

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _function(relative: str, name: str) -> ast.AST:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {relative}")


def _code(node: ast.AST) -> str:
    """The function's source without its docstring, which names what it avoids in prose."""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return "\n".join(ast.unparse(statement) for statement in body)


def _module_code(relative: str) -> str:
    """A module's code with every docstring taken out, so that prose naming what it avoids
    does not count as calling it."""
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list) and body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _change_type(tmp_path: Path, kind: str) -> Any:
    """The change type of *kind*, as the builder registers it on the bot's queue."""
    bot = league_double(str(tmp_path / "league.db"))
    queue = attach_queue(bot, str(tmp_path / "league.db"), now=NOW)
    return queue._type(kind)


def _job_source(change_type: Any, job: str) -> str:
    """The source of the function a job of *change_type* runs, unwrapped from any partial."""
    run = change_type.steps[job].run
    while isinstance(run, functools.partial):
        run = run.func
    return inspect.getsource(inspect.unwrap(run))


#: The jobs that publish, move a round on or run the attendance after a review.
PUBLISHING_JOBS = [
    "post_batch_notice",
    "post_session_results",
    "post_standings",
    "announce_verdict",
    "attendance_sheet",
    "plan_sanctions",
    "apply_sanction",
    "announce_sanction",
    "refresh_lineup",
]


# ── The split ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", [REPORTS, APPEALS])
async def test_each_stage_of_an_amendment_is_a_change_of_its_own_kind(tmp_path, kind):
    """An amendment runs none of the first pass: its stages are kinds of their own, never a
    branch inside the first pass's approvals."""
    change_type = _change_type(tmp_path, kind)

    assert change_type.kind == kind
    assert "apply" in change_type.steps
    assert "close" in change_type.steps


@pytest.mark.parametrize(
    "module",
    [
        "results/services/report_approval_change.py",
        "results/services/appeals_approval_change.py",
    ],
)
def test_the_first_pass_carries_no_amendment_branches_of_its_own(module):
    """Threading the amendment through the first pass is what made it unreadable, and what let
    a publishing step slip through unguarded. The first pass's change types never ask."""
    assert "is_amendment" not in _module_code(module)


# ── The report stage ──────────────────────────────────────────────────────


@pytest.mark.parametrize("job", PUBLISHING_JOBS)
async def test_the_amendments_report_stage_publishes_and_moves_nothing(tmp_path, job):
    """Nothing is published until the last stage, and the attendance is the last stage's.

    Anything posted here would stay posted if the amendment were then reverted — the revert
    restores the classification and the verdicts, not the channels or the attendance record.
    """
    assert job not in _change_type(tmp_path, REPORTS).steps


@pytest.mark.parametrize("call", ["UPDATE rounds", "set_round_status", "record_on"])
def test_neither_stage_moves_the_round_or_records_its_attendance_afresh(call):
    """The round's status is never written by an amendment, and its attendance is recalculated
    by the last stage rather than recorded as a first pass records it."""
    assert call not in _module_code(AMENDMENT)


async def test_the_report_stage_clears_the_sessions_records_before_re_applying(tmp_path):
    """`apply_penalties_on` only inserts, and adds to the stored penalty columns. Replaying a
    round's reports over records still there duplicated every one of them.
    `test_amendment_stage_changes.py` counts the rows; this pins the order in the stage's save."""
    source = _job_source(_change_type(tmp_path, REPORTS), "apply")

    assert source.index("_clear_round_verdict_records_on") < source.index("apply_penalties_on")


async def test_the_report_stage_s_save_is_refused_where_the_amendment_is_no_longer_open(tmp_path):
    """The stage's save sets `reports_approved_at` only on an amendment still unclaimed
    (`expires_at IS NOT NULL`), so a stage approved after the sweep or Cancel took the amendment
    writes nothing; one change at a time stops two stages running together."""
    source = _job_source(_change_type(tmp_path, REPORTS), "apply")

    assert "reports_approved_at" in source
    assert "expires_at IS NOT NULL" in source
    assert "_claim_amendment" not in source


# ── The appeal stage ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    ["RoundStatus.FINAL", "refresh_division_status", "wind_down"],
)
def test_the_amendments_appeal_stage_never_moves_the_round(call):
    """FINAL, the division refresh and the season wind-down travel together, and an amended
    round has had all three. Its appeals are announced by the rebuild, in order."""
    assert call not in _module_code(AMENDMENT)


async def test_the_division_is_rebuilt_when_the_appeal_stage_is_approved(tmp_path):
    """**The amendment's single rebuild, and its last act.** Rebuilding earlier would publish a
    classification whose reports and appeals are still the old round's. Its save recalculates
    the standings and the attendance; its jobs republish the division between the notices."""
    change_type = _change_type(tmp_path, APPEALS)
    source = _job_source(change_type, "apply")

    assert "cascade_recompute_from_round_on" in source
    assert "recalculate_on" in source
    for job in ("post_batch_notice", "post_session_results", "post_standings",
                "attendance_sheet", "announce_verdict", "delete_batch_notice",
                "delete_message", "delete_channel"):
        assert job in change_type.steps, job


async def test_the_snapshot_is_released_in_the_save_before_the_rebuild_begins(tmp_path):
    """Once anything is published the round must not be reverted from under it: the release is
    in the stage's save, and no publishing job opens the change ahead of it."""
    change_type = _change_type(tmp_path, APPEALS)

    assert "_release_amendment" in _job_source(change_type, "apply")
    opening = [planned.name for planned in change_type.opening]
    assert not set(opening) & set(PUBLISHING_JOBS)
    assert "apply" in opening


# ── The first pass's appeal review ────────────────────────────────────────


@pytest.mark.parametrize(
    "moving_call",
    [
        "refresh_division_status_on",
        pytest.param("WIND_DOWN", marks=pytest.mark.xfail(
            strict=True, reason="#439 slice 5: the appeals approval names the wind-down by its kind",
        )),
    ],
)
async def test_a_settled_round_is_not_moved_on_by_the_first_pass(tmp_path, moving_call):
    """A review put back by restart recovery cannot carry `is_amendment`, so the appeals
    approval's check refuses a round no longer awaiting its appeal verdicts, and the moving calls
    appear in its save alone."""
    change_type = _change_type(tmp_path, "results.appeals.approve")
    module = _module_code("results/services/appeals_approval_change.py")

    assert "AWAITING_APPEAL_VERDICTS" in inspect.getsource(inspect.unwrap(change_type.check))
    assert moving_call in _job_source(change_type, "apply")
    assert module.count(moving_call) == 1


# ── Stage one ─────────────────────────────────────────────────────────────


def test_stage_one_publishes_nothing_at_all():
    """It records the corrected classification; every posting belongs to the final stage."""
    source = _code(_function(MODULE, "amend_round_results"))

    assert "replay_division_channels" not in source
    assert "repost_round_results" not in source
    assert "delete_and_repost_final_results" not in source
    # The championship is still recalculated — the database has to be right either way.
    assert "cascade_recompute_from_round" in source


def test_the_revert_publishes_nothing_either():
    """Nothing was posted before the snapshot was released, so a revert has nothing to repost —
    and reposting one round would only move it to the bottom of the channel, out of order."""
    source = _code(_function(MODULE, "revert_abandoned_amendment"))

    assert "repost" not in source
    assert "cascade_recompute_from_round" in source


# ── The flags themselves ──────────────────────────────────────────────────


def test_the_state_carries_the_flags_and_defaults_to_a_first_pass():
    """Defaulting to False is what keeps every existing caller a first pass. Whether an
    amendment's reports are approved is read from its row's `reports_approved_at` (#439)."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

    state = PenaltyReviewState(
        round_id=1, division_id=1, submission_channel_id=1,
        session_types_present=[], db_path=":memory:", bot=None,
    )

    assert state.is_amendment is False
