"""The four render batches that carry a notice, and the one ordering rule between them.

`batch_notice` is tested on its own in `test_batch_notice.py`. What is left is whether
each slow batch is actually inside one, and whether the appeals batch closes its notice
before it deletes the channel the notice lives in — an ordering that is invisible to the
helper and would fail only in production, silently, as an ignored `NotFound`.

The wrapping is asserted against the parsed source rather than by driving four commands
to completion. Each of these functions reaches a hundred-odd lines through Discord, the
database and Inkscape; a test that ran them would be pinning those, not this, and would
be the kind of host-dependent test the project bans. What matters here is structural:
which statements sit inside the `async with`.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SRC = Path(__file__).resolve().parents[2] / "src"


# ── Reading the source ────────────────────────────────────────────────────


def _tree(relative: str) -> ast.Module:
    return ast.parse((SRC / relative).read_text(encoding="utf-8"))


def _function(relative: str, name: str) -> ast.AST:
    for node in ast.walk(_tree(relative)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {relative}")


def _notices(node: ast.AST) -> list[ast.AsyncWith]:
    """Every `async with batch_notice(...)` block inside *node*."""
    found = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.AsyncWith):
            continue
        for item in sub.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "batch_notice":
                found.append(sub)
    return found


def _calls_within(node: ast.AST) -> set[str]:
    """Names of every function called anywhere inside *node*."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# ── `/season review` ──────────────────────────────────────────────────────


def test_the_review_pre_render_is_wrapped():
    """The pre-render is the whole batch — the posting loop after it runs at message
    speed, and wrapping that instead would show the notice for the wrong stretch."""
    node = _function("cogs/season_cog.py", "season_review")
    blocks = _notices(node)

    assert len(blocks) == 1, "expected exactly one notice in /season review"
    assert "_prerender_review_images" in _calls_within(blocks[0])


def test_the_review_posting_loop_is_not_wrapped():
    node = _function("cogs/season_cog.py", "season_review")
    inside = _calls_within(_notices(node)[0])

    assert "_discard_prepared_review_images" not in inside, (
        "the notice covers the posting loop, so it stays up while the blocks are sent"
    )


# ── `/season approve` (the button) ────────────────────────────────────────


def test_the_approve_lineup_and_calendar_posting_is_wrapped():
    """Both draw inside their own loops, so one notice covers the pair."""
    node = _function("cogs/season_cog.py", "_do_approve")
    blocks = _notices(node)

    assert len(blocks) == 1, "expected exactly one notice in _do_approve"
    inside = _calls_within(blocks[0])
    assert "_refresh_lineup_post" in inside
    assert "post_division_calendar" in inside


def test_the_approve_notice_goes_to_the_interaction_channel():
    """The approve button is ephemeral, so the channel the review was read in is the
    only home it has."""
    node = _function("cogs/season_cog.py", "_do_approve")
    call = _notices(node)[0].items[0].context_expr

    target = call.args[0]
    assert isinstance(target, ast.Attribute) and target.attr == "channel"


# ── The results flow ──────────────────────────────────────────────────────


def test_the_penalty_batch_is_wrapped():
    node = _function("services/result_submission_service.py", "finalize_penalty_review")
    blocks = _notices(node)

    assert len(blocks) == 1
    inside = _calls_within(blocks[0])
    for expected in (
        "delete_and_repost_final_results",
        "repost_subsequent_standings",
        "post_penalty_announcements",
        "post_attendance_sheet",
        "enforce_attendance_sanctions",
    ):
        assert expected in inside, f"{expected} draws graphics outside the notice"


def test_the_appeals_batch_is_wrapped():
    node = _function("services/result_submission_service.py", "finalize_appeals_review")
    blocks = _notices(node)

    assert len(blocks) == 1
    inside = _calls_within(blocks[0])
    assert "delete_and_repost_final_results" in inside
    assert "repost_subsequent_standings" in inside
    assert "post_appeal_announcements" in inside


def test_the_appeals_notice_closes_before_the_channel_is_deleted():
    """The ordering rule. `close_submission_channel` deletes the channel the notice sits
    in; inside the block, the delete would race it and be swallowed as a `NotFound`,
    leaving the notice visible until Discord caught up."""
    node = _function("services/result_submission_service.py", "finalize_appeals_review")
    block = _notices(node)[0]

    assert "close_submission_channel" not in _calls_within(block), (
        "the channel is deleted inside the notice block"
    )
    assert "close_submission_channel" in _calls_within(node), (
        "the appeals flow no longer closes its channel — this test needs rewriting"
    )
    closes_at = min(
        sub.lineno
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and getattr(sub.func, "id", None) == "close_submission_channel"
    )
    assert block.end_lineno < closes_at, "the channel is closed before the notice ends"


def test_both_results_flow_notices_target_the_submission_channel():
    """Not the results, standings or verdicts channels the graphics land in: the person
    waiting is the steward who pressed the button, and they are in here."""
    for name in ("finalize_penalty_review", "finalize_appeals_review"):
        node = _function("services/result_submission_service.py", name)
        call = _notices(node)[0].items[0].context_expr
        assert isinstance(call.args[0], ast.Name), name
        assert call.args[0].id == "_notice_channel", name


# ── What the notices say ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "relative,function",
    [
        ("cogs/season_cog.py", "season_review"),
        ("cogs/season_cog.py", "_do_approve"),
        ("services/result_submission_service.py", "finalize_penalty_review"),
        ("services/result_submission_service.py", "finalize_appeals_review"),
    ],
)
def test_every_notice_carries_plain_text_for_a_league(relative, function):
    """The notice is deleted, so nothing in it survives — and a league reads it, so it
    names no channel ids, paths or internals."""
    call = _notices(_function(relative, function))[0].items[0].context_expr
    text = call.args[1].value

    assert isinstance(text, str) and text.strip()
    assert "one moment" in text.lower()
    for forbidden in ("_id", "None", "png", "svg", "division_id"):
        assert forbidden not in text
