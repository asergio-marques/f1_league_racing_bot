"""Tests for verdict_announcement_service: the penalty wording, the repair hint and the round's
label. The sanction's card and its banner are posted by queued jobs (#439, slice 6b), pinned in
tests/attendance/test_attendance_sync.py."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.results.services.verdict_announcement_service import (
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


# ---------------------------------------------------------------------------
# A verdict that was not announced reaches the league (#237)
#
# The penalty is applied either way: the classification changes and the driver loses the
# places. The announcement is the only thing that tells them why, and every failure to post
# one used to end in the host's log file.
# ---------------------------------------------------------------------------


def test_the_repair_hint_says_a_verdict_cannot_be_announced_twice():
    """There is no re-announce command, and #189 records why one could not be built.

    **Attendance sanctions are no exception**, though `/attendance sync` re-runs them: a
    driver already sacked or already moved to Reserve is no longer a candidate, so a second
    run passes over them without a word. ``attendance_service._failure_reason`` states this,
    and an earlier draft of this hint promised the opposite — which would have had a manager
    run the sync, read `Success`, and believe the driver had been told.
    """
    from leaguebot.results.services.verdict_announcement_service import verdict_repair_hint

    hint = verdict_repair_hint()
    assert "cannot announce a verdict a second time" in hint
    assert "yourself" in hint
    assert "/attendance sync" not in hint


async def test_the_repair_hint_is_defined_exactly_once(tmp_path):
    """A second definition shadowed the first and would have raised `NameError` (#345).

    `verdict_repair_hint` was defined twice in this module. The later one took a `sanction`
    keyword and returned `_SANCTION_RETRY` for it — a name defined nowhere in the repository — so
    any caller passing `sanction=True` would have raised rather than returning a hint. No caller
    did, which is exactly why it sat there: the failure was latent, reachable only by the next
    person to use the parameter the signature advertised.

    Pinned by counting the definitions rather than by calling it, because calling the surviving
    one proves nothing about a shadow that would silently replace it.
    """
    import inspect

    from leaguebot.results.services import verdict_announcement_service as module

    source = inspect.getsource(module)
    assert source.count("def verdict_repair_hint") == 1
    assert "_SANCTION_RETRY" not in source


def test_a_round_label_names_the_round_as_a_league_numbers_it():
    from leaguebot.results.services.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace(round_number=3, division_name="Pro")) == "Round 3 (Pro)"


def test_a_round_label_without_a_round_number_says_the_round():
    from leaguebot.results.services.verdict_announcement_service import _round_label

    assert _round_label(SimpleNamespace()) == "The round"
    assert _round_label(SimpleNamespace(round_number="not a number")) == "The round"
