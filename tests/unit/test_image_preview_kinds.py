"""The `/images test` kind classification (046).

`PREVIEW_KINDS` is the one table three separate rules are read from — which parameters a
command requires, whether a bare server may draw the kind, and what format its round must
carry. A wrong entry is not a visible bug: it is a preview quietly refusing where it should
draw, or drawing where it should refuse. These tests pin every column.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    PREVIEW_KINDS,
    ROSTER_DRAWING_KINDS,
)

#: The eleven, written out rather than derived, so that adding a kind to the constant
#: without deciding its three columns fails here.
EXPECTED_KINDS = {
    "calendar",
    "lineup",
    "results",
    "standings",
    "attendance",
    "verdict",
    "rsvp",
    "weather-p1",
    "weather-p2",
    "weather-p3",
    "weather-mystery",
}


def test_all_eleven_kinds_are_classified():
    assert set(PREVIEW_KINDS) == EXPECTED_KINDS


def test_every_kind_declares_every_column():
    for kind, spec in PREVIEW_KINDS.items():
        assert set(spec) == {"needs_round", "draws_roster", "format_demanded"}, kind
        assert isinstance(spec["needs_round"], bool), kind
        assert isinstance(spec["draws_roster"], bool), kind
        assert spec["format_demanded"] in (None, True, False), kind


def test_only_calendar_and_lineup_take_no_round():
    takes_no_round = {k for k, s in PREVIEW_KINDS.items() if not s["needs_round"]}
    assert takes_no_round == {"calendar", "lineup"}


def test_exactly_five_kinds_draw_a_roster():
    """The six-versus-five split FR-012 turns on.

    `rsvp` draws no roster and `verdict` does, which is the pair a reading of feature 045
    gets wrong — its FR-011 names neither.
    """
    assert ROSTER_DRAWING_KINDS == {
        "lineup",
        "results",
        "standings",
        "attendance",
        "verdict",
    }
    assert "rsvp" not in ROSTER_DRAWING_KINDS


def test_the_six_roster_free_kinds_are_the_ones_a_bare_server_draws():
    roster_free = set(PREVIEW_KINDS) - ROSTER_DRAWING_KINDS
    assert roster_free == {
        "calendar",
        "rsvp",
        "weather-p1",
        "weather-p2",
        "weather-p3",
        "weather-mystery",
    }


def test_only_the_weather_kinds_demand_a_format():
    demanded = {k: s["format_demanded"] for k, s in PREVIEW_KINDS.items()}
    assert demanded["weather-p1"] is False
    assert demanded["weather-p2"] is False
    assert demanded["weather-p3"] is False
    assert demanded["weather-mystery"] is True
    for kind in EXPECTED_KINDS - {
        "weather-p1",
        "weather-p2",
        "weather-p3",
        "weather-mystery",
    }:
        assert demanded[kind] is None, kind


def test_roster_drawing_kinds_is_derived_from_the_table():
    """Not written out twice. The two cannot drift because one is built from the other."""
    assert ROSTER_DRAWING_KINDS == frozenset(
        k for k, s in PREVIEW_KINDS.items() if s["draws_roster"]
    )


@pytest.mark.parametrize(
    "kind, builder_name",
    [
        ("calendar", "build_calendar_preview"),
        ("lineup", "build_lineup_preview"),
        ("results", "build_results_preview"),
        ("standings", "build_standings_preview"),
        ("attendance", "build_attendance_preview"),
        ("verdict", "build_verdict_preview"),
        ("rsvp", "build_rsvp_preview"),
    ],
)
def test_draws_roster_matches_what_the_builder_actually_reads(kind, builder_name):
    """The column is checked against the source of the builder, not against prose.

    This is the guard that would have caught the `rsvp` / `verdict` mistake at the time it
    was made. A builder that stops reading the roster, or starts, fails here rather than
    silently changing which servers can draw it.
    """
    from services import image_preview_service

    source = inspect.getsource(getattr(image_preview_service, builder_name))

    # Two builders reach the roster through helpers rather than directly. Both helpers
    # exist for no other purpose — `_racing_drivers` filters `context.drivers` and
    # `_driver_maps` indexes them — so naming them here follows the read rather than
    # widening the check.
    roster_reads = ("context.drivers", "context.teams", "_racing_drivers", "_driver_maps")
    reads_roster = any(token in source for token in roster_reads)

    assert reads_roster is PREVIEW_KINDS[kind]["draws_roster"], (
        f"{builder_name} {'reads' if reads_roster else 'does not read'} the roster, "
        f"but PREVIEW_KINDS says draws_roster="
        f"{PREVIEW_KINDS[kind]['draws_roster']}"
    )
