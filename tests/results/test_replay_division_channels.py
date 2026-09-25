"""Rebuilding everything a division's channels show, in the order a league reads them (#345).

The amendment replay calls this once its corrected round has been computed. Five stages run in
the order the specification states — results, standings, the attendance sheet, the report
verdicts, then the appeal verdicts — and each rebuilds the **whole division**, not the amended
round alone.

**Why the whole division.** A repost is a new message at the bottom of a channel. Amending round
1 of five and reposting only round 1 leaves the results channel reading 2, 3, 4, 5, 1. Reposting
every round in round order is the only thing that keeps the sequence a league reads matching the
sequence it raced.

**The attendance sheet is not a sequence.** A division keeps one live sheet in one slot, so
there is nothing to reorder: it is reposted once, against the round the running totals now stand
at. The caller supplies that as a step, which runs between the standings and the verdicts so the
stages happen in the order the specification states.

**A stage that raises is a fault, not the end of the rebuild.** Each channel is its own, and a
results channel that refused a post has already been put back as it was by the time it raises.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.services.results_post_service import replay_division_channels  # noqa: E402

DIVISION_ID = 91
FROM_ROUND = 3


@pytest.fixture(autouse=True)
def _no_standing_banners():
    """The rebuild reads the division's standing banners before any stage runs (#345).

    These tests drive the orchestrator against a placeholder path and stub every stage; left
    unstubbed, that one read would open a real database at the placeholder — creating an empty
    `db.sqlite` in whatever directory the suite ran from. A test about the banners patches it
    itself, inside this.
    """
    with patch(
        "leaguebot.results.services.verdict_announcement_service.banners_from_round",
        new=AsyncMock(return_value=[]),
    ):
        yield


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
        if isinstance(results, Exception):
            raise results
        return results

    async def _standings(*_a, **_kw):
        order.append("standings")
        if isinstance(standings, Exception):
            raise standings
        return standings

    async def _verdicts(*_a, **_kw):
        order.append("verdicts")
        return list(verdict_faults or [])

    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(side_effect=_results),
    ), patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(side_effect=_standings),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=_verdicts),
    ):
        outcome = await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=bot, verdict_state_factory=state_factory,
        )
    return outcome.faults, order


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


async def test_the_attendance_sheet_lands_between_the_standings_and_the_verdicts():
    """The order the specification states, and the order a league reads them (#345).

    It used to run *after* this whole function, so a sanction's own verdict was announced after
    the report and appeal verdicts of later rounds — out of sequence in the one channel where
    sequence is the point.
    """
    order: list[str] = []

    async def _attendance() -> list[str]:
        order.append("attendance")
        return []

    async def _results(*_a, **_kw):
        order.append("results")
        return "ok"

    async def _standings(*_a, **_kw):
        order.append("standings")
        return "ok"

    async def _verdicts(*_a, **_kw):
        order.append("verdicts")
        return []

    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(side_effect=_results),
    ), patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(side_effect=_standings),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=_verdicts),
    ):
        await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=MagicMock(), verdict_state_factory=lambda r: MagicMock(round_id=r),
            attendance_step=_attendance,
        )

    assert order == ["results", "standings", "attendance", "verdicts"]


async def test_an_unrecognised_repost_status_is_reported():
    """`no_rounds` is an empty division; anything else is a failure and must not read as success."""
    faults, _ = await _replay(results="something_went_wrong")

    assert any("were not reposted" in line for line in faults)


async def test_a_stage_that_raises_is_reported_and_the_rest_still_run():
    """A results channel that refused a post must not cost the league its standings and its
    verdicts as well — and it used to raise straight out of the amendment's last stage, leaving
    the round committed, its channel open and nothing logged."""
    faults, order = await _replay(results=RuntimeError("Missing Permissions"))

    assert order == ["results", "standings", "verdicts"]
    assert any("Missing Permissions" in fault for fault in faults)
    assert any("left as they were" in fault for fault in faults)


async def test_a_standings_stage_that_raises_is_reported_too():
    faults, order = await _replay(standings=RuntimeError("Missing Access"))

    assert order == ["results", "standings", "verdicts"]
    assert any("Missing Access" in fault for fault in faults)


async def test_a_banner_posted_by_the_attendance_step_is_not_taken_down(tmp_path):
    """**The banners to remove are the ones standing before the rebuild began** (#345).

    The attendance step enforces the division's sanctions, and those head themselves. A capture
    taken inside the verdict republish — which runs after it — handed that fresh banner in as a
    superseded one and deleted it, leaving the sanctions it headed bare.
    """
    seen: dict = {}

    async def _banners(*_a, **_kw):
        seen["captured_before_attendance"] = "attendance" not in order_so_far
        return [(FROM_ROUND, "77", 4242)]

    order_so_far: list[str] = []

    async def _attendance() -> list[str]:
        order_so_far.append("attendance")
        return []

    async def _verdicts(*_a, **kwargs):
        seen["handed_in"] = kwargs.get("superseded_banners")
        return []

    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(return_value="ok"),
    ), patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(return_value="ok"),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.banners_from_round",
        new=AsyncMock(side_effect=_banners),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=_verdicts),
    ):
        await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=MagicMock(), verdict_state_factory=lambda round_id: MagicMock(),
            attendance_step=_attendance,
        )

    assert seen["captured_before_attendance"] is True
    assert seen["handed_in"] == [(FROM_ROUND, "77", 4242)]


async def test_the_rebuild_says_whether_the_verdicts_were_replaced():
    """**The caller has one decision that turns on it** (#345): the amendment takes its
    superseded announcements down only where replacements went up, a verdict deleted from a
    channel being in no channel at all. A fault line alone cannot say which stage failed."""
    async def _results(*_a, **_kw):
        return "ok"

    async def _standings(*_a, **_kw):
        return "ok"

    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(side_effect=_results),
    ), patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(side_effect=_standings),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.banners_from_round",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=RuntimeError("gateway closed")),
    ):
        outcome = await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=MagicMock(), verdict_state_factory=lambda round_id: MagicMock(),
        )

    assert outcome.rebuilt_rounds == frozenset()
    assert any("gateway closed" in fault for fault in outcome.faults)


async def _rebuilds_round_three(*_a, rebuilt=None, **_kw):
    rebuilt.append(3)
    return []


async def test_a_rebuild_that_announced_its_verdicts_says_so():
    """The counterpart: the rounds the republish rebuilt are carried back to the caller."""
    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(return_value="ok"),
    ), patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(return_value="ok"),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.banners_from_round",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.verdict_announcement_service.republish_verdicts_from_round",
        new=AsyncMock(side_effect=_rebuilds_round_three),
    ):
        outcome = await replay_division_channels(
            "db.sqlite", DIVISION_ID, FROM_ROUND, MagicMock(),
            bot=MagicMock(), verdict_state_factory=lambda round_id: MagicMock(),
        )

    assert outcome.rebuilt_rounds == frozenset({3})
    assert outcome.faults == []
