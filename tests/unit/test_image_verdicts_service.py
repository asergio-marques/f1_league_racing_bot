"""Verdict resolution — T013, T015, T019.

Written against specs/043-verdicts-image-generation/data-model.md and Constitution XIV.7
and XIV.16. Pure: no Discord, no database, no rasteriser.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_verdict_service import (  # noqa: E402
    VerdictDrawing,
    VerdictKind,
    resolve_mentions,
    sanction_text,
    stage_label,
)


def _drawing(**overrides) -> VerdictDrawing:
    values = dict(
        kind=VerdictKind.PENALTY,
        division_name="Pro Division",
        round_number=7,
        driver_name="Ada Lovelace",
        penalty="5 seconds added",
        description="Contact at turn four.",
        justification="Video evidence reviewed.",
        session_name="Feature Race",
        team_name="Red Bull",
    )
    values.update(overrides)
    return VerdictDrawing(**values)


# ── T013: the three kinds and their stage strings ─────────────────────────


def test_there_are_exactly_three_kinds_of_verdict():
    assert {kind.name for kind in VerdictKind} == {
        "PENALTY",
        "APPEAL",
        "ATTENDANCE_SANCTION",
    }


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (VerdictKind.PENALTY, "Post-Race Penalty"),
        (VerdictKind.APPEAL, "Appeal"),
        (VerdictKind.ATTENDANCE_SANCTION, "Attendance Sanction"),
    ],
)
def test_the_stage_is_fixed_text_per_kind(kind, expected):
    assert stage_label(kind) == expected
    assert _drawing(kind=kind).stage == expected


def test_only_an_attendance_sanction_names_no_session_and_no_team():
    for kind in (VerdictKind.PENALTY, VerdictKind.APPEAL):
        drawing = _drawing(kind=kind)
        assert drawing.names_a_session
        assert drawing.names_a_team

    sanction = _drawing(
        kind=VerdictKind.ATTENDANCE_SANCTION, session_name=None, team_name=None
    )
    assert not sanction.names_a_session
    assert not sanction.names_a_team


def test_one_template_serves_all_three_kinds():
    """They differ in the value of two fields, not in any field (XIV.10, v4.8.0)."""
    assert {_drawing(kind=kind).template_key for kind in VerdictKind} == {
        "verdicts_template"
    }


# ── T019: the sanction rendering is the text path's ───────────────────────


def test_the_sanction_rendering_is_the_announcement_service_s():
    """XIV.7's one rendering: the graphic calls it and holds none of its own."""
    from services import verdict_announcement_service  # noqa: PLC0415

    assert sanction_text.__module__ != verdict_announcement_service.__name__
    assert sanction_text("TIME_PENALTY", 5) == verdict_announcement_service.describe_penalty(
        "TIME_PENALTY", 5
    )


@pytest.mark.parametrize(
    ("penalty_type", "seconds", "expected"),
    [
        ("TIME_PENALTY", 5, "5 seconds added"),
        ("TIME_PENALTY", 12, "12 seconds added"),
        ("TIME_PENALTY", -3, "3 seconds removed"),
        ("DSQ", None, "Disqualified"),
        ("TIME_PENALTY", None, "Disqualified"),
    ],
)
def test_a_positive_magnitude_adds_and_a_negative_one_removes(
    penalty_type, seconds, expected
):
    """Confirmed by the author on 2026-08-14: positive adds, negative removes."""
    assert sanction_text(penalty_type, seconds) == expected


# ── T015: a mention inside free text (XIV.16, v4.8.0) ─────────────────────


NAMES = {"123": "Ada Lovelace", "456": "Grace Hopper", "789": "Pro Division"}


def _resolver(user_id: str) -> str:
    return NAMES.get(user_id, user_id)


def test_a_bare_mention_becomes_the_name():
    assert resolve_mentions("<@123> was penalised.", _resolver) == (
        "Ada Lovelace was penalised."
    )


@pytest.mark.parametrize("form", ["<@123>", "<@!123>", "<@&123>"])
def test_every_mention_form_is_resolved(form):
    assert resolve_mentions(f"{form} reached the limit.", _resolver) == (
        "Ada Lovelace reached the limit."
    )


def test_several_mentions_in_one_string_are_all_resolved():
    assert resolve_mentions("<@123> and <@456> collided.", _resolver) == (
        "Ada Lovelace and Grace Hopper collided."
    )


def test_a_mention_followed_by_its_own_name_in_brackets_yields_the_name_once():
    """The shape the attendance module actually composes: `<@id> (display name)`.

    A naive substitution reads "Ada Lovelace (Ada Lovelace)", which is what this pins
    against — the parenthesised copy is the textual message's, not the value's.
    """
    composed = "<@123> (Ada Lovelace) has reached the 12 attendance point limit."
    assert resolve_mentions(composed, _resolver) == (
        "Ada Lovelace has reached the 12 attendance point limit."
    )


def test_a_bracketed_name_that_is_not_the_mention_s_own_is_kept():
    composed = "<@123> (the reserve) took the seat."
    assert resolve_mentions(composed, _resolver) == "Ada Lovelace (the reserve) took the seat."


def test_text_holding_no_mention_is_returned_unchanged():
    assert resolve_mentions("Nothing to resolve here.", _resolver) == (
        "Nothing to resolve here."
    )


def test_an_unresolvable_mention_falls_back_to_the_resolver_s_answer():
    assert resolve_mentions("<@999> did something.", _resolver) == "999 did something."


def test_an_empty_value_is_safe():
    assert resolve_mentions("", _resolver) == ""
    assert resolve_mentions(None, _resolver) == ""
