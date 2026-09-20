"""Rebuilding everything a division's channels show, in the order a league reads them (#345).

The amendment replay calls this once its corrected round has been computed. Five stages run in
the order the specification states — results, standings, the attendance sheet, the report
verdicts, then the appeal verdicts — and each rebuilds the **whole division**, not the amended
round alone.

**Why the whole division.** A repost is a new message at the bottom of a channel. Amending round
1 of five and reposting only round 1 leaves the results channel reading 2, 3, 4, 5, 1. Reposting
every round in round order is the only thing that keeps the sequence a league reads matching the
sequence it raced.

**The attendance sheet is deliberately not here.** A division keeps one live sheet in one slot,
so there is no sequence to preserve and nothing to reorder: it is reposted once, against the
round the running totals now stand at. The caller does that. It is named in the docstring so the
whole order is readable in one place, and its absence from this function is intentional rather
than an omission.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.results_post_service import replay_division_channels  # noqa: E402

DIVISION_ID = 91
FROM_ROUND = 3


async def _replay(
    *,
    results="ok",
    standings="ok",
    verdict_faults=None,
    bot=MagicMock(),
    state_factory=lambda round_id: MagicMock(round_id=round_id),
):
    """Run the orchestrator, recording which stage ran when."""
    order: list[str] = []

    async def _results(*_a, **_kw):
        order.append("results")
        return results

    async def _standings(*_a, **_kw):
        order.append("standings")
        return standings

    async def _verdicts(*_a, **_kw):
        order.append("verdicts")
        return list(verdict_faults or [])

    with patch(
        "services.results_post_service.repost_results_for_division",
        new=AsyncMock(side_effect=_results),
    ), patch(
        "services.results_post_service.repost_standings_for_division",
        new=AsyncMock(side_effect=_standings),
    ), patch(
        "services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=_verdicts),
    ):
        faults = await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=bot, verdict_state_factory=state_factory,
        )
    return faults, order


async def test_the_stages_run_in_the_order_a_league_reads_them():
    """Results, then standings, then the verdicts — the order the specification states."""
    faults, order = await _replay()

    assert faults == []
    assert order == ["results", "standings", "verdicts"]


async def test_an_unreachable_results_channel_is_reported():
    """The rebuild carries on; what could not be posted is named rather than swallowed (#237)."""
    faults, order = await _replay(results="no_channel")

    assert any("results channel" in line for line in faults)
    assert order == ["results", "standings", "verdicts"]


async def test_an_unreachable_standings_channel_is_reported():
    faults, _ = await _replay(standings="no_channel")

    assert any("standings channel" in line for line in faults)


async def test_a_division_with_no_rounds_is_not_a_fault():
    """`no_rounds` is an empty division, not a failure to reach one."""
    faults, _ = await _replay(results="no_rounds", standings="no_rounds")

    assert faults == []


async def test_verdict_faults_are_carried_back():
    """A verdict that could not be taken down is the league's to know about."""
    faults, _ = await _replay(
        verdict_faults=["the verdict for <@101> has to be removed by hand"]
    )

    assert faults == ["the verdict for <@101> has to be removed by hand"]


async def test_the_same_fault_from_two_stages_is_said_once():
    """Two stages over one division word a missing channel identically.

    Saying it twice would read as two separate problems for a manager to chase.
    """
    faults, _ = await _replay(
        results="no_channel",
        verdict_faults=[
            "the division's results channel could not be reached, so its results were "
            "not reposted"
        ],
    )

    assert len(faults) == 1


async def test_the_verdicts_are_skipped_without_a_bot():
    """Nothing can be announced with no gateway, and the rest of the rebuild still runs.

    The database is correct either way; it is what a league can *see* that is outstanding.
    """
    _, order = await _replay(bot=None)

    assert order == ["results", "standings"]


async def test_the_verdicts_are_skipped_without_a_state_factory():
    """The announcement functions read the round and division from the state they are given."""
    _, order = await _replay(state_factory=None)

    assert order == ["results", "standings"]
