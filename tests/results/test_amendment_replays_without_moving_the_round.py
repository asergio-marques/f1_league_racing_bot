"""An amendment replays a round's review stages without moving the round (#345).

The replay reuses the report and appeal *screens* wholesale — the same views, the same staging.
What it must **not** reuse is the part of each first-pass finalisation that moves the round on or
publishes as it goes, because an amended round has already been everywhere it is going:

- report review sets `AWAITING_APPEAL_VERDICTS`, reposts the round, announces its penalties and
  runs the attendance pipeline;
- appeal review sets `FINAL`, refreshes the division's status, and may wind the season down.

Replaying those against a settled round would send it backwards through states it left weeks
ago, could finish a division or wind down a season a second time, and would publish a round
half-amended — which an amendment abandoned part-way could then not take back.

So each finaliser hands an amendment to a function of its own before doing anything else:
`_approve_amendment_reports` and `_approve_amendment_appeals`. These tests pin that routing,
and pin what the two amendment functions leave out.

**Asserted against the parsed source rather than by driving the finalisers.** Each first-pass
finaliser is a hundred-odd lines reaching through Discord, the database, the attendance module
and the image renderer; a test that ran one end to end would be pinning those rather than this.
`test_finalize_reviews.py` drives the amendment functions themselves and counts rows. This is
the technique `test_batch_notice_call_sites.py` uses, for the same reason.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "src" / "leaguebot"
MODULE = "results/services/result_submission_service.py"


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


def _first_if(node: ast.AST) -> ast.If:
    """The first `if` statement of the function's own body, imports and docstring aside."""
    for statement in node.body:
        if isinstance(statement, ast.If):
            return statement
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        raise AssertionError(
            f"{node.name} does something before routing an amendment: {ast.unparse(statement)}"
        )
    raise AssertionError(f"{node.name} has no top-level if")


# ── The routing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "finaliser, amendment_fn",
    [
        ("finalize_penalty_review", "_approve_amendment_reports"),
        ("finalize_appeals_review", "_approve_amendment_appeals"),
    ],
)
def test_a_finaliser_hands_an_amendment_away_before_doing_anything(finaliser, amendment_fn):
    """Before deferring, before reading a row: an amendment runs none of the first pass."""
    guard = _first_if(_function(MODULE, finaliser))
    body = "\n".join(ast.unparse(statement) for statement in guard.body)

    assert "is_amendment" in ast.unparse(guard.test)
    assert amendment_fn in body
    assert isinstance(guard.body[-1], ast.Return)


@pytest.mark.parametrize("finaliser", ["finalize_penalty_review", "finalize_appeals_review"])
def test_the_first_pass_carries_no_amendment_branches_of_its_own(finaliser):
    """Threading the amendment through the first pass is what made it unreadable, and what let
    a publishing step slip through unguarded. The routing at the top is the only mention."""
    source = _code(_function(MODULE, finaliser))

    assert source.count("is_amendment") == 1


# ── The report stage ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        "delete_and_repost_final_results",
        "repost_subsequent_standings",
        "post_penalty_announcements",
        "record_attendance_from_results",
        "cascade_attendance_from_round",
        "post_attendance_sheet",
        "enforce_attendance_sanctions",
        "UPDATE rounds",
    ],
)
def test_the_amendments_report_stage_publishes_and_moves_nothing(call):
    """Nothing is published until the last stage, and the attendance is the last stage's.

    Anything posted here would stay posted if the amendment were then reverted — the revert
    restores the classification and the verdicts, not the channels or the attendance record.
    """
    assert call not in _code(_function(MODULE, "_approve_amendment_reports"))


def test_the_report_stage_clears_the_sessions_records_before_re_applying():
    """`apply_penalties` only inserts, and adds to the stored penalty columns. Replaying a
    round's reports over records still there duplicated every one of them.
    `test_finalize_reviews.py` counts the rows; this pins the order."""
    source = _code(_function(MODULE, "_approve_amendment_reports"))

    assert source.index("_clear_round_verdict_records") < source.index("apply_penalties")


def test_the_report_stage_is_claimed_before_it_writes():
    source = _code(_function(MODULE, "_approve_amendment_reports"))

    assert source.index("_claim_amendment") < source.index("_clear_round_verdict_records")


# ── The appeal stage ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        "RoundStatus.FINAL",
        "refresh_division_status",
        "wind_down_ongoing",
        "delete_and_repost_final_results",
        "post_appeal_announcements",
    ],
)
def test_the_amendments_appeal_stage_never_moves_the_round(call):
    """FINAL, the division refresh and the season wind-down travel together, and an amended
    round has had all three. Its appeals are announced by the rebuild, in order, not here."""
    assert call not in _code(_function(MODULE, "_approve_amendment_appeals"))


def test_the_division_is_rebuilt_when_the_appeal_stage_is_approved():
    """**The amendment's single rebuild, and its last act.** Rebuilding earlier would publish a
    classification whose reports and appeals are still the old round's."""
    source = _code(_function(MODULE, "_approve_amendment_appeals"))

    assert "replay_division_channels" in source
    assert "_repost_attendance_after_amendment" in source
    assert "take_down_superseded_announcements" in source


def test_the_snapshot_is_released_before_the_rebuild_begins():
    """Once anything is published the round must not be reverted from under it."""
    source = _code(_function(MODULE, "_approve_amendment_appeals"))

    assert source.index("_release_amendment") < source.index("replay_division_channels")


# ── The first pass's appeal review ────────────────────────────────────────


@pytest.mark.parametrize("moving_call", ["refresh_division_status", "wind_down_ongoing"])
def test_a_settled_round_is_not_moved_on_by_the_first_pass(moving_call):
    """A view rebuilt by restart recovery cannot carry `is_amendment`, so the round's own status
    guards the three moving calls — each appearing once, inside that guard."""
    node = _function(MODULE, "finalize_appeals_review")
    guards = [
        child for child in ast.walk(node)
        if isinstance(child, ast.If) and "_round_is_final" in ast.unparse(child.test)
    ]
    guarded = "\n".join(ast.unparse(s) for guard in guards for s in guard.body)

    assert ast.unparse(node).count(moving_call) == guarded.count(moving_call) == 1


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
    """Defaulting to False is what keeps every existing caller a first pass."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

    state = PenaltyReviewState(
        round_id=1, division_id=1, submission_channel_id=1,
        session_types_present=[], db_path=":memory:", bot=None,
    )

    assert state.is_amendment is False
    assert state.reports_approved is False
