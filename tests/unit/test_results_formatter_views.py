"""The textual standings and the points-config summary.

Issue #208, finishing off `results_formatter.py`. Most of the module already has cover; what
was left is the textual rendering a league sees where the image module is off, and the summary
`/results config view` prints.

**The textual standings are not a fallback.** A league that never turns the image module on
reads these every round, and they are the whole championship table — so a driver missing from
them is a driver whose points nobody can see.

**"No standings available." is a message, not an accident.** An empty string would render as a
blank message, which Discord refuses to send at all, so the round would silently produce
nothing. The empty case is therefore tested for both championships.

**A points configuration is printed with its trailing zeros already collapsed**, by the caller —
the docstring says so. These tests pass pre-collapsed rows, because passing uncollapsed ones
would be testing a contract the function does not have and would pin the wrong behaviour into
place.

**The fastest-lap bonus prints its eligibility limit only when there is one.** A league that
awards the bonus to anyone would otherwise read "(top None eligible)", and the limit is exactly
the sort of optional field that reaches a league as the word `None` when nobody checked.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.results_formatter import (  # noqa: E402
    format_config_view,
    format_gap_to_leader,
    format_team_standings,
)


def _team(position: int, role_id: int, points: int):
    return SimpleNamespace(
        standing_position=position, team_role_id=role_id, total_points=points
    )


# ---------------------------------------------------------------------------
# Team standings
# ---------------------------------------------------------------------------


def test_every_team_is_listed_with_its_points():
    result = format_team_standings([_team(1, 5001, 43), _team(2, 5002, 21)])

    assert "<@&5001>" in result
    assert "43 pts" in result
    assert "<@&5002>" in result


def test_teams_are_ordered_by_standing_not_by_the_order_given():
    """The caller hands back whatever the query produced; the table is the championship
    order and must not depend on it."""
    result = format_team_standings([_team(3, 5003, 5), _team(1, 5001, 43), _team(2, 5002, 21)])

    lines = result.splitlines()
    assert lines[0].startswith("1.")
    assert lines[1].startswith("2.")
    assert lines[2].startswith("3.")


def test_no_teams_says_so_rather_than_sending_nothing():
    """An empty string renders as a blank message, which Discord refuses outright — the
    round would silently produce nothing at all."""
    assert format_team_standings([]) == "No standings available."


def test_a_team_on_no_points_is_still_listed():
    """They are in the championship; omitting them would make the table shorter than the
    grid and a manager would go looking for the missing team."""
    result = format_team_standings([_team(1, 5001, 0)])

    assert "0 pts" in result


# ---------------------------------------------------------------------------
# The gap to the leader
# ---------------------------------------------------------------------------


def test_the_leader_has_no_gap_drawn():
    """XIV.3: a field where a value does not apply is left empty rather than given a
    placeholder. The leader is not zero points behind themselves — there is no gap."""
    assert format_gap_to_leader(0, is_leader=True) == ""


def test_a_following_entry_shows_how_far_behind_it_is():
    """With a leading minus, because the table reads downwards from the leader."""
    assert format_gap_to_leader(25, is_leader=False) == "-25"


def test_an_entry_level_with_the_leader_still_shows_a_gap():
    """Level on points but not the leader — the gap is zero and is drawn, because the
    empty field means "leader" and this entry is not one."""
    assert format_gap_to_leader(0, is_leader=False) == "-0"


# ---------------------------------------------------------------------------
# The points config summary
# ---------------------------------------------------------------------------


def test_a_configuration_with_nothing_set_says_so():
    """A newly created config has every position at zero, and a manager checking it needs
    telling that rather than shown an empty block."""
    result = format_config_view("100%", {}, {})

    assert "no entries configured" in result
    assert "100%" in result


def test_each_session_type_is_its_own_section():
    """A league scores a sprint differently from a feature race, and reading them as one
    list would make the two indistinguishable."""
    result = format_config_view(
        "100%",
        {"Full Race": [("1", 25)], "Full Qualifying": [("1", 3)]},
        {},
    )

    assert "Full Race" in result
    assert "Full Qualifying" in result


def test_sessions_are_listed_in_a_stable_order():
    """Sorted, so the summary reads the same every time — a manager comparing two configs
    should not have to account for the order having moved."""
    first = format_config_view(
        "100%", {"B Session": [("1", 1)], "A Session": [("1", 2)]}, {}
    )
    second = format_config_view(
        "100%", {"A Session": [("1", 2)], "B Session": [("1", 1)]}, {}
    )

    assert first == second


def test_every_scoring_position_is_shown():
    result = format_config_view(
        "100%", {"Full Race": [("1", 25), ("2", 18), ("3", 15)]}, {}
    )

    for line in ("P1: 25 pts", "P2: 18 pts", "P3: 15 pts"):
        assert line in result


def test_a_collapsed_range_is_printed_as_given():
    """The caller collapses trailing zeros before passing, so a range arrives as a string
    like `11-20` and is printed rather than re-derived."""
    result = format_config_view("100%", {"Full Race": [("1", 25), ("11-20", 0)]}, {})

    assert "P11-20: 0 pts" in result


def test_the_fastest_lap_bonus_is_shown_with_its_limit():
    """A league awarding the bonus only to the top ten needs that visible — without it the
    summary would say the bonus is available to a driver who cannot earn it."""
    result = format_config_view(
        "100%", {"Full Race": [("1", 25)]}, {"Full Race": (1, 10)}
    )

    assert "FL bonus: 1 pts" in result
    assert "top 10 eligible" in result


def test_an_unlimited_fastest_lap_bonus_omits_the_limit():
    """`None` reaching a league as the word "None" reads as a bug, and the limit is
    exactly the optional field where that happens."""
    result = format_config_view(
        "100%", {"Full Race": [("1", 25)]}, {"Full Race": (1, None)}
    )

    assert "FL bonus: 1 pts" in result
    assert "None" not in result
    assert "eligible" not in result


def test_a_session_with_no_fastest_lap_bonus_has_no_bonus_line():
    """Qualifying has no fastest lap to award, so the line would be meaningless there."""
    result = format_config_view(
        "100%",
        {"Full Race": [("1", 25)], "Full Qualifying": [("1", 3)]},
        {"Full Race": (1, None)},
    )

    # Sections are sorted, so "Full Qualifying" precedes "Full Race" — the qualifying
    # section is what lies between the two headers, not everything after the first.
    qualifying_section = result.split("*Full Qualifying*")[1].split("*Full Race*")[0]
    assert "FL bonus" not in qualifying_section


def test_the_configuration_is_named_at_the_top():
    """A league can hold several, and the summary is often pasted somewhere without the
    command that produced it."""
    result = format_config_view("Sprint 50%", {"Full Race": [("1", 25)]}, {})

    assert result.startswith("**Sprint 50%**")
