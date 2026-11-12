"""Results' "called off" note is taken down when results is switched off (#455, #284).

Where a round or a division is called off, or a round ends with every session entered as not
held, results posts its note in the division's results channel ("📢 **Round 3 Cancelled: Pro**
… No results will be posted for it."). `/module disable results` takes down every message results
posted in the live season, and the note is one of them: its id is kept where it is posted, and the
switch-off's purge reads it and deletes it, as it does the verdicts and banners.

The league is `ongoing_league`'s, results on: Pro's round 3 not yet run, its results channel
`PRO_RESULTS`. Each case calls round 3 (or Pro) off as the league would, runs the queue, then
presses the confirmation `/module disable results` shows, as admin 77, and runs the queue again.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.support.change_queue import (
    MANAGER_ROLE_ID,
    member_interaction,
    run_queue,
    stopped_job,
    tier_member,
    typed_message,
)
from tests.support.season_league import (
    ADMIN_ID,
    DIVISIONS,
    PRO,
    SUBMISSION_CHANNEL,
    SeasonLeague,
    accept_session,
    cancel_division,
    cancel_round,
    ongoing_league,
    open_submission,
    round_id,
)

PRO_RESULTS = DIVISIONS[PRO][3].results
ROUND_3 = round_id(PRO, 3)

NOTE_NOT_KEPT = "#284: results' called-off note keeps no message id, so the switch-off leaves it up"


@pytest.fixture(autouse=True)
def _no_panel_to_refresh(monkeypatch):
    """The hub has no panel here; turning results off refreshes it as a change of its own."""
    from leaguebot.core.services.modules import hub_service

    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(return_value=None))


async def _round_called_off(league: SeasonLeague) -> None:
    """`/round cancel` for Pro's round 3."""
    await cancel_round(league, "Pro", 3)


async def _division_called_off(league: SeasonLeague) -> None:
    """`/division cancel` for Pro."""
    await cancel_division(league, "Pro")


async def _round_ended_not_held(league: SeasonLeague) -> None:
    """Round 3 awaits results, its Feature Qualifying entered as cancelled; the admin, a league
    manager, types CANCELLED for the Feature Race in its submission channel, and the round ends
    not held."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    league.roles_held[ADMIN_ID] = {MANAGER_ROLE_ID}
    await league.write("UPDATE rounds SET status = 'AWAITING_RESULTS' WHERE id = ?", ROUND_3)
    await open_submission(league, ROUND_3)
    submission = league.channel(SUBMISSION_CHANNEL)
    if not isinstance(getattr(submission, "posted", None), list):
        submission.posted = []
    await accept_session(league, ROUND_3, session="FEATURE_QUALIFYING", status="CANCELLED")
    message = typed_message(
        league.channel(SUBMISSION_CHANNEL), league._member(ADMIN_ID), "CANCELLED",
        created_at=league.clock.now,
    )
    await ResultsCog(league.bot).on_message(message)


def _notes(league: SeasonLeague) -> list[int]:
    """The ids of the "called off" notes standing in Pro's results channel."""
    channel = league.channel(PRO_RESULTS)
    return [mid for mid, message in channel.messages.items()
            if (message.content or "").startswith("📢")]


async def _switch_results_off(league: SeasonLeague) -> None:
    """Press the confirmation `/module disable results` showed, as admin 77, and run the queue."""
    from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = league.bot
    view = _ConfirmDisableResultsView(cog, ADMIN_ID, cascade_attendance=False)
    interaction = member_interaction(
        league.bot, user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    interaction.guild = league.guild
    await type(view).confirm(view, interaction, view.confirm)
    await run_queue(league.bot)


@pytest.mark.parametrize(
    "call_off",
    [
        pytest.param(_round_called_off, id="round called off"),
        pytest.param(_division_called_off, id="division called off"),
        pytest.param(_round_ended_not_held, id="round ended not held"),
    ],
)
@pytest.mark.xfail(strict=True, reason=NOTE_NOT_KEPT)
async def test_turning_results_off_takes_down_the_called_off_note(tmp_path, call_off):
    """Round 3 (or Pro) is called off, and results posts its note in Pro's results channel. Then
    results is switched off: the queue runs to the end and the note is gone from the channel."""
    league = await ongoing_league(tmp_path)
    await call_off(league)
    await run_queue(league.bot)
    assert await stopped_job(league.db_path) is None
    [note] = _notes(league)

    await _switch_results_off(league)

    assert await stopped_job(league.db_path) is None
    assert note not in league.channel(PRO_RESULTS).messages
