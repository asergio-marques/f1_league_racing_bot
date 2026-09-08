"""Unit tests for ClassificationOccasion — the three moments a classification is published.

Covers:
  1. The phrase each occasion draws.
  2. The round number being required by AFTER_ROUND and ignored by the other two.
  3. Both behavioural properties' truth tables, which are deliberately not the same.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.classification_occasion import ClassificationOccasion


def test_each_occasion_draws_its_own_phrase():
    assert ClassificationOccasion.SEASON_OPENING.label() == "Opening Classification"
    assert ClassificationOccasion.AFTER_ROUND.label(10) == "After Round 10"
    assert ClassificationOccasion.SEASON_FINAL.label() == "Final Classification"


def test_the_round_number_is_ignored_where_it_does_not_apply():
    """A caller holding a round need not decide whether to pass it."""
    assert ClassificationOccasion.SEASON_OPENING.label(4) == "Opening Classification"
    assert ClassificationOccasion.SEASON_FINAL.label(4) == "Final Classification"


def test_a_round_number_may_be_a_string():
    assert ClassificationOccasion.AFTER_ROUND.label("7") == "After Round 7"


def test_after_round_refuses_to_name_a_round_it_was_not_given():
    with pytest.raises(ValueError):
        ClassificationOccasion.AFTER_ROUND.label()


def test_only_a_round_posting_names_a_round():
    assert ClassificationOccasion.AFTER_ROUND.names_a_round is True
    assert ClassificationOccasion.SEASON_OPENING.names_a_round is False
    assert ClassificationOccasion.SEASON_FINAL.names_a_round is False


def test_only_the_final_sheet_declines_the_live_slot():
    """Not the same truth table as `names_a_round` — the opening sheet parts company here.

    It takes the slot so round one replaces it; the final sheet stands beside the last
    round's rather than replacing it.
    """
    assert ClassificationOccasion.SEASON_OPENING.takes_the_live_slot is True
    assert ClassificationOccasion.AFTER_ROUND.takes_the_live_slot is True
    assert ClassificationOccasion.SEASON_FINAL.takes_the_live_slot is False
