"""Unit tests for the results drawing resolution — T016.

Covers :func:`resolve_drawing` alone: the values a graphic carries, decided with no template
and no Discord in view. The projection onto a template is covered by
``test_image_results_fill.py``.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.points_config import SessionType
from models.session_result import (
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
)
from services.image_results_service import (
    QUALIFYING_TEMPLATE_KEY,
    RACE_TEMPLATE_KEY,
    ResultsDataError,
    resolve_drawing,
    status_label,
    template_key_for,
)


def _qual(position: int, user_id: int, *, best_lap: str | None = "1:23.456", tyre="Soft"):
    return QualifyingSessionResult(
        id=position,
        session_result_id=1,
        driver_user_id=user_id,
        team_role_id=900 + position,
        finishing_position=position,
        outcome=OutcomeModifier.CLASSIFIED,
        tyre=tyre,
        best_lap=best_lap,
        points_awarded=0,
    )


def _race(
    position: int,
    user_id: int,
    *,
    base_time_ms: int | None = 5025678,
    fastest_lap: str | None = "1:23.456",
    fl_bonus: int = 0,
    ingame_ms: int = 0,
):
    return RaceSessionResult(
        id=position,
        session_result_id=1,
        driver_user_id=user_id,
        team_role_id=900 + position,
        finishing_position=position,
        outcome=OutcomeModifier.CLASSIFIED,
        base_time_ms=base_time_ms,
        laps_behind=None,
        ingame_time_penalties_ms=ingame_ms,
        postrace_time_penalties_ms=0,
        appeal_time_penalties_ms=0,
        fastest_lap=fastest_lap,
        fastest_lap_bonus=fl_bonus,
        points_awarded=25,
    )


def _resolve(rows, session_type=SessionType.FEATURE_QUALIFYING, **overrides):
    kwargs = dict(
        session_type=session_type,
        is_sprint=False,
        result_status="FINAL",
        division_name="Division One",
        round_number=3,
        race_name="British Grand Prix",
        driver_rows=rows,
        points_map={row.driver_user_id: 25 for row in rows},
        driver_names={row.driver_user_id: f"Driver {row.driver_user_id}" for row in rows},
        team_names={row.team_role_id: f"Team {row.team_role_id}" for row in rows},
    )
    kwargs.update(overrides)
    return resolve_drawing(**kwargs)


# ── Which template draws what ─────────────────────────────────────────────


def test_the_session_kind_selects_the_template():
    assert template_key_for(SessionType.SPRINT_QUALIFYING) == QUALIFYING_TEMPLATE_KEY
    assert template_key_for(SessionType.FEATURE_QUALIFYING) == QUALIFYING_TEMPLATE_KEY
    assert template_key_for(SessionType.SPRINT_RACE) == RACE_TEMPLATE_KEY
    assert template_key_for(SessionType.FEATURE_RACE) == RACE_TEMPLATE_KEY


def test_the_session_name_drops_the_feature_prefix_off_a_sprint_round():
    sprint = _resolve([_qual(1, 10)], is_sprint=True)
    other = _resolve([_qual(1, 10)], is_sprint=False)
    assert sprint.session_name == "Feature Qualifying"
    assert other.session_name == "Qualifying"


# ── The lifecycle label and the two phase closures ────────────────────────


def test_status_label_matches_the_text_the_message_carries():
    assert status_label("PROVISIONAL") == "Provisional Results"
    assert status_label("POST_RACE_PENALTY") == "Post-Race Penalty Results"
    assert status_label("FINAL") == "Final Results"


@pytest.mark.parametrize(
    "status,penalty_closed,appeal_closed",
    [
        ("PROVISIONAL", False, False),
        ("POST_RACE_PENALTY", True, False),
        ("FINAL", True, True),
    ],
)
def test_the_phase_closures_follow_the_result_status(status, penalty_closed, appeal_closed):
    drawing = _resolve([_qual(1, 10)], result_status=status)
    assert drawing.penalty_phase_closed is penalty_closed
    assert drawing.appeal_phase_closed is appeal_closed


def test_provisional_empties_both_sanction_cells_on_every_row():
    drawing = _resolve([_qual(1, 10), _qual(2, 11)], result_status="PROVISIONAL")
    assert all(entry.postrace_penalty is None for entry in drawing.entries)
    assert all(entry.appeal_penalty is None for entry in drawing.entries)


def test_a_closed_phase_that_applied_nothing_carries_a_dash():
    drawing = _resolve([_qual(1, 10)], result_status="FINAL")
    assert drawing.entries[0].postrace_penalty == "—"
    assert drawing.entries[0].appeal_penalty == "—"


def test_a_closed_penalty_phase_with_an_open_appeal_resolves_one_and_empties_the_other():
    drawing = _resolve([_qual(1, 10)], result_status="POST_RACE_PENALTY")
    assert drawing.entries[0].postrace_penalty == "—"
    assert drawing.entries[0].appeal_penalty is None


def test_a_disqualification_lands_in_the_phase_that_applied_it():
    drawing = _resolve([_qual(1, 10)], dsq_phase_map={1: "APPEAL"})
    assert drawing.entries[0].appeal_penalty == "DSQ"
    assert drawing.entries[0].postrace_penalty == "—"


# ── The ordinal ───────────────────────────────────────────────────────────


def test_entries_are_ordinalled_from_one_in_classification_order():
    drawing = _resolve([_qual(2, 11), _qual(1, 10), _qual(3, 12)])
    assert [entry.ordinal for entry in drawing.entries] == [1, 2, 3]
    assert [entry.driver_name for entry in drawing.entries] == [
        "Driver 10",
        "Driver 11",
        "Driver 12",
    ]


# ── Names ─────────────────────────────────────────────────────────────────


def test_the_team_name_falls_back_to_the_role_where_the_division_holds_no_team():
    drawing = _resolve([_qual(1, 10)], team_names={})
    assert drawing.entries[0].team_name == "Role 901"


def test_a_driver_with_no_resolvable_name_is_fatal():
    """A mandatory field whose value cannot be determined aborts the render (XIV.3)."""
    with pytest.raises(ResultsDataError, match="no name could be resolved"):
        _resolve([_qual(1, 10)], driver_names={})


def test_a_session_recording_no_entry_at_all_is_fatal():
    with pytest.raises(ResultsDataError, match="records no entry at all"):
        _resolve([])


# ── The fastest-lap block ─────────────────────────────────────────────────


def test_the_block_names_the_holder_and_their_lap():
    drawing = _resolve(
        [_race(1, 10), _race(2, 11, fl_bonus=1, fastest_lap="1:21.000")],
        session_type=SessionType.FEATURE_RACE,
    )
    assert drawing.fastest_lap is not None
    assert drawing.fastest_lap.driver_name == "Driver 11"
    assert drawing.fastest_lap.lap_time == "1:21.000"
    assert [entry.holds_fastest_lap for entry in drawing.entries] == [False, True]


def test_there_is_no_block_where_the_session_conferred_no_bonus():
    drawing = _resolve([_race(1, 10)], session_type=SessionType.FEATURE_RACE)
    assert drawing.fastest_lap is None
    assert not any(entry.holds_fastest_lap for entry in drawing.entries)


def test_a_qualifying_drawing_never_carries_a_block():
    drawing = _resolve([_qual(1, 10)])
    assert drawing.fastest_lap is None
    assert drawing.is_qualifying


# ── Cells that come from the shared builders ──────────────────────────────


def test_the_in_game_penalty_is_never_empty():
    """A dash where the game applied none — the one race cell that is never emptied."""
    drawing = _resolve([_race(1, 10)], session_type=SessionType.FEATURE_RACE)
    assert drawing.entries[0].ingame_penalty == "—"


def test_the_in_game_penalty_keeps_its_fraction():
    drawing = _resolve(
        [_race(1, 10, ingame_ms=5500)], session_type=SessionType.FEATURE_RACE
    )
    assert drawing.entries[0].ingame_penalty == "+5.500s"


def test_the_reference_entry_carries_no_gap():
    drawing = _resolve([_qual(1, 10), _qual(2, 11, best_lap="1:24.456")])
    assert drawing.entries[0].gap is None
    assert drawing.entries[1].gap == "+1.000"


def test_a_tyre_that_was_never_recorded_is_none():
    drawing = _resolve([_qual(1, 10, tyre=None)])
    assert drawing.entries[0].tyre is None


# ── Nationality ───────────────────────────────────────────────────────────


def test_a_recorded_nationality_reaches_the_entry():
    drawing = _resolve([_qual(1, 10)], nationalities={10: "British"})
    assert drawing.entries[0].nationality == "British"


def test_nationality_collection_switched_off_is_carried_on_the_drawing():
    drawing = _resolve([_qual(1, 10)], nationality_collected=False)
    assert drawing.nationality_collected is False
    assert drawing.entries[0].nationality is None
