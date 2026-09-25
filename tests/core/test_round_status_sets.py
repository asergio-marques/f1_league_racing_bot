"""The named sets of round states, and the rules each of them encodes.

Three frozensets in `core/models/round.py` carry rules that several modules read rather than
restate: which states hold a round open, which allow it to be cancelled, and which only the
results module can move it out of. Each is pinned here because the cost of getting one wrong
is not a failing test somewhere else — it is a season that cannot be completed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.models.round import (  # noqa: E402
    ROUND_AWAITING_RESULTS_MODULE,
    ROUND_CANCELLABLE,
    ROUND_TERMINAL,
    RoundStatus,
)


def test_the_terminal_states_are_the_two_that_hold_nothing_open() -> None:
    assert ROUND_TERMINAL == {"FINAL", "CANCELLED"}


def test_a_round_may_only_be_cancelled_before_its_results_are_entered() -> None:
    """Once results are in, the drivers have reports and appeals to lodge."""
    assert ROUND_CANCELLABLE == {"NOT_RUN", "AWAITING_RESULTS"}


def test_the_results_module_owns_the_three_awaiting_states() -> None:
    assert ROUND_AWAITING_RESULTS_MODULE == {
        "AWAITING_RESULTS",
        "AWAITING_REPORT_VERDICTS",
        "AWAITING_APPEAL_VERDICTS",
    }


def test_not_run_is_not_the_results_module_s_to_close() -> None:
    """It waits on the clock, not on results — issue #167.

    `run_result_submission_job` closes a NOT_RUN round as FINAL when its moment arrives with
    the module off, and the round still has its weather and its check-in to run first. Adding
    NOT_RUN here would have disabling the module end rounds that have not been raced.
    """
    assert RoundStatus.NOT_RUN.value not in ROUND_AWAITING_RESULTS_MODULE


def test_a_round_the_results_module_owns_is_never_already_done() -> None:
    """Disabling the module closes these rounds, so it must not reopen a settled one."""
    assert ROUND_AWAITING_RESULTS_MODULE.isdisjoint(ROUND_TERMINAL)


def test_every_named_state_is_a_real_round_status() -> None:
    known = {s.value for s in RoundStatus}
    for named in (ROUND_TERMINAL, ROUND_CANCELLABLE, ROUND_AWAITING_RESULTS_MODULE):
        assert named <= known


def test_the_three_sets_account_for_every_state_between_them() -> None:
    """A state in none of them is one no rule reaches — worth failing over."""
    covered = ROUND_TERMINAL | ROUND_CANCELLABLE | ROUND_AWAITING_RESULTS_MODULE
    assert covered == {s.value for s in RoundStatus}
