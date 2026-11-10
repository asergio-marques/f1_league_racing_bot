"""The season review's three workflows, run as core services rather than on the season cog.

The approval, the mid-season placements confirmation and the configuration confirmation each run
every gate before they ask their change of the queue. These pin that none of that work reaches
into `SeasonCog` any more, and that the press judges time on the queue's clock, as the change it
asks does, rather than on the host's.

The leagues are `tests/support/season_league.py`'s: season 3 with Pro and Am, rounds 1 to 4 falling
30, 60, 90 and 120 days after "now", Admin (77) pressing the review's button. The queue's clock is
`league.clock`, which a test moves on; the host's clock is left where it is.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.support.change_queue import change_rows, run_queue
from tests.support.season_league import (
    CONFIGURATION_CONFIRM_KIND,
    PLACEMENTS_CONFIRM_KIND,
    approval_changes,
    configuring_league,
    placing_league,
    post_config_review,
    post_mid_season_review,
    post_review,
    press_approve,
    reply,
    season_league,
)

#: Every workflow and gate the review's buttons run on `SeasonCog` today: work the press must not
#: reach on the cog. All but `_approval_windows` move into core's services; `_approval_windows`
#: stays for the review command, the workflow reading the windows through `read_round_windows`.
_COG_WORK = (
    "_do_approve", "_offer_backup_before_approving", "_record_backup_question_ended",
    "_do_confirm_mid_season_placements", "_do_confirm_configuration",
    "_points_ordering_problems", "_missing_points_config_problems", "_no_points_config_attached",
    "_team_name_problems", "_ask_drawing", "_lineup_problems", "_image_configuration_faults",
    "_mid_season_configuration_faults", "_placement_confirmation_faults",
    "_division_channel_faults", "_season_has_divisions", "_configuration_faults",
    "_approval_windows",
)

PASSED_DATES = "❌ Season cannot be approved — its calendar holds dates that have already gone by:"
EXPIRED = (
    "⏱️ This review expired before the season could be approved. Run "
    "`/season placements-review` again. **Nothing has been approved.**"
)


async def _asked(league: Any, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


async def _approval(league: Any) -> list[dict[str, Any]]:
    return await approval_changes(league)


async def _mid_season(league: Any) -> list[dict[str, Any]]:
    return await _asked(league, PLACEMENTS_CONFIRM_KIND)


async def _configuration(league: Any) -> list[dict[str, Any]]:
    return await _asked(league, CONFIGURATION_CONFIRM_KIND)


@pytest.mark.parametrize("build, post, asked", [
    pytest.param(season_league, post_review, _approval, id="approval"),
    pytest.param(placing_league, post_mid_season_review, _mid_season, id="mid-season placements"),
    pytest.param(configuring_league, post_config_review, _configuration, id="configuration"),
])
async def test_each_review_button_runs_its_workflow_with_nothing_of_the_season_cog(
    tmp_path, monkeypatch, build, post, asked,
):
    """Season 3's review stands, nothing wrong with the season. Every workflow and gate method is
    taken off `SeasonCog`, and the bot finds no season cog loaded. Admin presses the review's
    button: the press raises nothing and asks its one change of the queue."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    league = await build(tmp_path, monkeypatch=monkeypatch)
    review = await post(league)
    for name in _COG_WORK:
        monkeypatch.delattr(SeasonCog, name, raising=False)
    league.bot.get_cog = MagicMock(return_value=None)

    await press_approve(league, review)

    assert league.errors == []
    assert len(await asked(league)) == 1


async def test_the_configuration_change_s_check_needs_no_season_cog(tmp_path, monkeypatch):
    """Admin presses ✅ Confirm configuration on season 3's review, the signup module off. Before
    the queue runs, the bot loses its season cog and no season cog can be built. The queue runs:
    the change's check still reads the configuration's faults, finds none, and the season moves
    on to placements."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    league = await configuring_league(tmp_path, monkeypatch=monkeypatch)
    await press_approve(league, await post_config_review(league))
    assert league.errors == []

    def _no_cog(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the configuration's check built a season cog")

    league.bot.get_cog = MagicMock(return_value=None)
    monkeypatch.setattr(SeasonCog, "__init__", _no_cog)
    await run_queue(league.bot)

    assert (await league.season())["stage"] == "PLACEMENTS"


async def test_the_approval_judges_the_dates_on_the_queue_s_clock(tmp_path, monkeypatch):
    """The queue's clock is 31 days on from the host's, so round 1 of each division, 30 days out
    by the host's clock, has gone by on the queue's. Admin presses ✅ Approve: the refusal is the
    dates gate's, nothing is asked of the queue, and every time the dates are judged, at the press
    and in the change's check alike, "now" is the queue's clock. Until now the press judged them
    on the host's clock, let the season through, and only the change's check refused it."""
    from leaguebot.core.services import approval_checks

    league = await season_league(tmp_path, monkeypatch=monkeypatch)
    league.clock.advance(days=31)
    judged_at: list[Any] = []
    real = approval_checks.date_refusal

    def _recorded(*args: Any, **kwargs: Any) -> Any:
        judged_at.append(kwargs["now"])
        return real(*args, **kwargs)

    monkeypatch.setattr(approval_checks, "date_refusal", _recorded)

    press = await press_approve(league)

    assert league.errors == []
    assert PASSED_DATES in reply(press)
    assert await approval_changes(league) == []
    assert judged_at
    assert all(now == league.clock.now for now in judged_at), judged_at


async def test_the_backup_question_s_time_left_is_read_from_the_queue_s_clock(
    tmp_path, monkeypatch,
):
    """Test mode is on and season 3's review was posted on the host's clock, with five minutes to
    stand. The queue's clock is six minutes on. Admin presses ✅ Approve: by the queue's clock the
    review has expired, so the backup question is never put, Admin is told the review expired,
    and nothing is asked of the queue. Until now the time left was read from the host's clock,
    which still gave the question five minutes."""
    league = await season_league(tmp_path, monkeypatch=monkeypatch, test_mode=True)
    league.clock.advance(minutes=6)

    press = await press_approve(league, backup="save")

    assert league.errors == []
    assert EXPIRED in reply(press)
    assert league.backup_answers == []
    assert await approval_changes(league) == []
