"""The points-ordering rule itself, away from any database.

`ordering_violations` is the one statement of a rule three callers apply — the season's
approval gate, the mid-season amendment, and the warning a config edit gives back. Those
three have their own tests for *when* they ask; this file is the only one that pins *what
the answer is*, so the rule can be read in one place rather than inferred from three.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.utils.points_ordering import ordering_violations  # noqa: E402


def test_a_table_running_down_has_no_violations():
    assert ordering_violations([(1, 25), (2, 18), (3, 15)]) == []


def test_a_lower_position_worth_more_is_a_violation():
    """The fault the whole rule exists for: second place beating first."""
    assert ordering_violations([(1, 10), (2, 25)]) == [(1, 10, 2, 25)]


def test_two_positive_values_tying_is_a_violation():
    """Two positions cannot both be third. Scoring them equal is the silent failure."""
    assert ordering_violations([(1, 25), (2, 25)]) == [(1, 25, 2, 25)]


def test_trailing_zeros_are_the_normal_shape_of_a_table():
    """A table stops paying somewhere, and every position below is worth nothing."""
    assert ordering_violations([(1, 25), (2, 18), (3, 0), (4, 0), (5, 0)]) == []


def test_a_zero_following_a_zero_is_not_a_tie():
    assert ordering_violations([(1, 0), (2, 0)]) == []


def test_a_positive_value_after_a_zero_is_a_violation():
    """Nothing for fourth and points for fifth is out of order, not a gap."""
    assert ordering_violations([(1, 25), (2, 18), (3, 0), (4, 5)]) == [(3, 0, 4, 5)]


def test_entries_are_compared_in_position_order_not_the_order_given():
    """The callers read from SQL and from dicts; neither is asked to sort first."""
    assert ordering_violations([(3, 15), (1, 25), (2, 18)]) == []
    assert ordering_violations([(2, 25), (1, 10)]) == [(1, 10, 2, 25)]


def test_a_gap_is_compared_across_as_it_stands():
    """P1 and P5 with nothing between them are still compared to each other."""
    assert ordering_violations([(1, 10), (5, 20)]) == [(1, 10, 5, 20)]


def test_every_violation_in_a_table_is_reported_not_just_the_first():
    """A manager fixing one fault should not have to run the check again to find the next."""
    assert ordering_violations([(1, 10), (2, 20), (3, 30)]) == [
        (1, 10, 2, 20),
        (2, 20, 3, 30),
    ]


def test_a_single_entry_has_nothing_to_compare():
    assert ordering_violations([(1, 25)]) == []


def test_an_empty_table_has_no_violations():
    """A config that has been created and not yet filled in is not broken."""
    assert ordering_violations([]) == []
