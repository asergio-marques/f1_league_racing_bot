"""An amendment replays a round's review stages without moving the round (#345).

The replay reuses the report and appeal reviews wholesale — the same views, the same staging,
the same application of penalties. What it must **not** reuse is the part of each finalisation
that moves the round on, because an amended round has already been everywhere it is going:

- report review sets `AWAITING_APPEAL_VERDICTS`;
- appeal review sets `FINAL`, refreshes the division's status, and may wind the season down.

Replaying those against a settled round would send it backwards through states it left weeks
ago, and could finish a division or wind down a season a second time. `wind_down_ongoing` in
particular is not something to run again for no reason.

**Asserted against the parsed source rather than by driving the finalisers.** Each is a
hundred-odd lines reaching through Discord, the database, the attendance module and the image
renderer; a test that ran one end to end would be pinning those rather than this, and the guard
is structural anyway — it is *which statements sit inside the `if`* that matters. This is the
technique `test_batch_notice_call_sites.py` uses, for the same reason.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SRC = Path(__file__).resolve().parents[2] / "src"
MODULE = "services/result_submission_service.py"


def _function(relative: str, name: str) -> ast.AST:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {relative}")


def _amendment_guards(node: ast.AST) -> list[ast.If]:
    """Every `if not ...is_amendment...:` block in *node*."""
    found = []
    for child in ast.walk(node):
        if isinstance(child, ast.If) and "is_amendment" in ast.dump(child.test):
            found.append(child)
    return found


def _guarded_source(node: ast.AST, guard: ast.If) -> str:
    return "\n".join(ast.unparse(statement) for statement in guard.body)


# ── The report review ─────────────────────────────────────────────────────


def test_the_report_review_guards_its_forward_transition():
    node = _function(MODULE, "finalize_penalty_review")

    guards = _amendment_guards(node)

    assert guards, "finalize_penalty_review has no is_amendment guard"
    assert any(
        "AWAITING_APPEAL_VERDICTS" in _guarded_source(node, guard) for guard in guards
    )


def test_the_report_review_still_applies_its_penalties_on_an_amendment():
    """The stages are replayed, not skipped — only the round's movement is withheld.

    A guard that swallowed the penalty application would make an amendment a no-op with a
    confirmation screen.
    """
    node = _function(MODULE, "finalize_penalty_review")
    guarded = "\n".join(
        _guarded_source(node, guard) for guard in _amendment_guards(node)
    )

    assert "apply_penalties" not in guarded
    assert "apply_penalties" in ast.unparse(node)


# ── The appeal review ─────────────────────────────────────────────────────


def test_the_appeal_review_guards_the_three_things_that_move_a_round():
    """FINAL, the division refresh, and the season wind-down travel together."""
    node = _function(MODULE, "finalize_appeals_review")
    guarded = "\n".join(
        _guarded_source(node, guard) for guard in _amendment_guards(node)
    )

    assert "RoundStatus.FINAL" in guarded
    assert "refresh_division_status" in guarded
    assert "wind_down_ongoing" in guarded


@pytest.mark.parametrize(
    "moving_call", ["refresh_division_status", "wind_down_ongoing"]
)
def test_nothing_that_moves_a_round_runs_outside_the_guard(moving_call):
    """Each appears once, and that once is inside the guard.

    A second, unguarded call would defeat the guard entirely while leaving every assertion
    above still passing.
    """
    node = _function(MODULE, "finalize_appeals_review")
    guarded = "\n".join(
        _guarded_source(node, guard) for guard in _amendment_guards(node)
    )

    assert ast.unparse(node).count(moving_call) == guarded.count(moving_call)


def test_the_appeal_review_still_reposts_on_an_amendment():
    """The results and standings are exactly what an amendment is for."""
    node = _function(MODULE, "finalize_appeals_review")
    guarded = "\n".join(
        _guarded_source(node, guard) for guard in _amendment_guards(node)
    )

    assert "delete_and_repost_final_results" not in guarded


# ── The flag itself ───────────────────────────────────────────────────────


def test_the_state_carries_the_flag_and_defaults_to_a_first_pass():
    """Defaulting to False is what keeps every existing caller a first pass.

    A default of True would silently stop the ordinary review moving rounds at all.
    """
    from services.penalty_wizard import PenaltyReviewState

    state = PenaltyReviewState(
        round_id=1, division_id=1, submission_channel_id=1,
        session_types_present=[], db_path=":memory:", bot=None,
    )

    assert state.is_amendment is False


def test_the_appeal_stage_is_labelled_for_an_amendment():
    """Stage three reaches the amendment channel by the same code path as a first pass.

    `finalize_penalty_review` posts the appeals screen to `submission_channel_id`, which is the
    amendment channel in that case — so the stage needs no separate posting, only a heading
    saying where the manager is.
    """
    node = _function(MODULE, "finalize_penalty_review")
    source = ast.unparse(node)

    assert "Stage 3 of 3" in source
    assert "is_amendment" in source


# ── The one rebuild, at the end ───────────────────────────────────────────


def test_the_division_is_rebuilt_when_the_appeal_stage_is_approved():
    """**The amendment's single rebuild, and its last act** (#345).

    Rebuilding at stage one would publish a classification whose reports and appeals are still
    the old round's, repost every round of the division to do it, and then repeat the whole
    thing minutes later when the appeals stage is approved. The rebuild belongs here, once,
    after every decision is in.
    """
    node = _function(MODULE, "finalize_appeals_review")
    source = ast.unparse(node)

    assert "replay_division_channels" in source
    assert "_repost_attendance_after_amendment" in source


def test_stage_one_posts_its_round_and_does_not_rebuild_the_division():
    """`amend_session_result` writes and posts; it does not cascade the channels."""
    node = _function(MODULE, "amend_session_result")
    source = ast.unparse(node)

    assert "repost_round_results" in source
    assert "replay_division_channels" not in source


def test_a_first_pass_reposts_its_round_and_not_the_whole_division():
    """Sending every round through the division-wide rebuild would repost the entire
    championship at the end of every ordinary race weekend.

    The two live in different branches of the same `if`: an amendment reorders the division,
    a first pass replaces its own round's messages and leaves the rest alone.
    """
    node = _function(MODULE, "finalize_appeals_review")
    source = ast.unparse(node)

    assert "delete_and_repost_final_results" in source
    assert "repost_subsequent_standings" in source


def test_the_two_rebuilds_are_alternatives_not_both():
    """One `if/elif/else`, so a round is rebuilt one way or the other and never twice."""
    node = _function(MODULE, "finalize_appeals_review")

    branching = [
        child for child in ast.walk(node)
        if isinstance(child, ast.If)
        and "replay_division_channels" in ast.unparse(child)
        and "delete_and_repost_final_results" in ast.unparse(child)
    ]

    assert branching, "the two rebuilds are not alternatives of one branch"
