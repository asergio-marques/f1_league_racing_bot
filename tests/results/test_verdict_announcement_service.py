"""Tests for verdict_announcement_service: the penalty wording, the repair hint and the round's
label. The sanction's card and its banner are posted by queued jobs (#439, slice 6b), pinned in
tests/attendance/test_attendance_sync.py."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.results.services.review.verdict_announcement_service import (
    describe_penalty,
    translate_penalty,
)


# ---------------------------------------------------------------------------
# translate_penalty
# ---------------------------------------------------------------------------

class TestTranslatePenalty:
    @pytest.mark.parametrize(
        "typed, expected",
        [
            pytest.param("+5s", "5 seconds added", id="positive_with_sign_and_s"),
            pytest.param("5s", "5 seconds added", id="positive_no_sign_with_s"),
            pytest.param("5", "5 seconds added", id="positive_no_sign_no_s"),
            pytest.param("-3s", "3 seconds removed", id="negative_with_s"),
            pytest.param("-10", "10 seconds removed", id="negative_no_s"),
            pytest.param("DSQ", "Disqualified", id="dsq_uppercase"),
            pytest.param("dsq", "Disqualified", id="dsq_lowercase"),
            pytest.param("Dsq", "Disqualified", id="dsq_mixed_case"),
            pytest.param("  DSQ  ", "Disqualified", id="dsq_with_whitespace"),
            pytest.param("+30s", "30 seconds added", id="large_penalty"),
            pytest.param("-1s", "1 seconds removed", id="single_second_removed"),
        ],
    )
    def test_the_typed_value_is_worded(self, typed, expected):
        assert translate_penalty(typed) == expected

    @pytest.mark.parametrize("typed", ["NFA", "nfa", "  Nfa  "])
    def test_no_further_action_says_no_penalty_follows(self, typed):
        assert translate_penalty(typed) == NO_FURTHER_ACTION


#: What a cleared driver's verdict reads, in the announcement and on the graphic alike
#: (decided 2026-09-24, #138).
NO_FURTHER_ACTION = "No further action"


def test_no_further_action_is_described_as_no_penalty_and_never_as_a_disqualification():
    """It carries no seconds, as a disqualification does — and a record with no seconds was
    read as one, so it has to be recognised before that fallback is reached (#138)."""
    assert describe_penalty("NFA", None) == NO_FURTHER_ACTION


def test_a_disqualification_is_still_described_as_one():
    assert describe_penalty("DSQ", None) == "Disqualified"


def test_a_round_label_names_the_round_as_a_league_numbers_it():
    from leaguebot.results.services.review.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace(round_number=3, division_name="Pro")) == "Round 3 (Pro)"


def test_a_round_label_without_a_round_number_says_the_round():
    from leaguebot.results.services.review.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace()) == "The round"
    assert _round_label(SimpleNamespace(round_number="not a number")) == "The round"
