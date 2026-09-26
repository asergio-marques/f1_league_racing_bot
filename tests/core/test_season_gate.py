"""The shared season-stage gate (issue #224).

`season_for_command` answers two questions for every command that changes a season: which
season, and whether that season stands in a stage where the command means anything. The tests
here pin the three things a later reader could quietly undo:

- **the default excludes Pending completion.** A command that names no stages is asking for
  the ordinary rule, and the ordinary rule is "a season still being built or raced". Widening
  the default to the whole of a live season would silently reopen every gate this issue closed.
- **a completed or cancelled season is never returned.** The helper reads the live season
  alone, so the archive is unreachable through it whatever stages a caller names.
- **the reply follows the interaction's state.** Some commands defer before their gate and
  some do not; answering the wrong way is a 404 and a refusal the manager never sees.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.models.season import SeasonStage, status_of_stage
from leaguebot.core.utils.season_gate import (
    BEFORE_PENDING_COMPLETION,
    LIVE_STAGES,
    PLACEMENT_STAGES,
    season_for_command,
    stage_label,
)


def _interaction(*, done: bool = False) -> MagicMock:
    interaction = MagicMock()
    interaction.response.is_done = MagicMock(return_value=done)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _service(season) -> MagicMock:
    service = MagicMock()
    service.get_setup_or_active_season = AsyncMock(return_value=season)
    return service


def _season(stage: SeasonStage | None):
    return SimpleNamespace(id=1, season_number=3, stage=stage)


def _said(interaction) -> str:
    """Whatever the gate replied, through whichever route it used."""
    for mock in (interaction.response.send_message, interaction.followup.send):
        if mock.await_args is not None:
            return mock.await_args.args[0]
    return ""


# ---------------------------------------------------------------------------
# The stage sets themselves
# ---------------------------------------------------------------------------

def test_live_stages_are_the_eight_of_setup_and_active():
    assert LIVE_STAGES == {
        SeasonStage.CONFIGURATION,
        SeasonStage.WAITING,
        SeasonStage.SIGNUPS,
        SeasonStage.PLACEMENTS,
        SeasonStage.ONGOING,
        SeasonStage.ONGOING_SIGNUPS,
        SeasonStage.ONGOING_PLACEMENTS,
        SeasonStage.PENDING_COMPLETION,
    }
    assert SeasonStage.COMPLETED not in LIVE_STAGES
    assert SeasonStage.CANCELLED not in LIVE_STAGES


def test_the_default_set_is_every_live_stage_but_pending_completion():
    """Decided 2026-09-20. Widening this reopens every gate issue #224 closed."""
    assert SeasonStage.PENDING_COMPLETION not in BEFORE_PENDING_COMPLETION
    assert BEFORE_PENDING_COMPLETION == LIVE_STAGES - {SeasonStage.PENDING_COMPLETION}


def test_placement_stages_are_the_two_in_which_drivers_are_placed():
    assert PLACEMENT_STAGES == {SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS}
    assert all(status_of_stage(s) is not None for s in PLACEMENT_STAGES)


# ---------------------------------------------------------------------------
# What the gate lets through, and what it turns away
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", sorted(BEFORE_PENDING_COMPLETION, key=lambda s: s.value))
async def test_a_season_in_any_other_live_stage_is_returned(stage):
    season = _season(stage)
    interaction = _interaction()

    assert await season_for_command(interaction, _service(season), "team role") is season
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


async def test_pending_completion_is_refused_by_default():
    interaction = _interaction()

    got = await season_for_command(
        interaction, _service(_season(SeasonStage.PENDING_COMPLETION)), "team role"
    )

    assert got is None
    said = _said(interaction)
    assert "/team role" in said
    assert "Every division of this season is done" in said
    # The refusal names the three things that are still open, so a manager is not left
    # guessing what to do next.
    assert "/season complete" in said


async def test_pending_completion_is_allowed_where_the_caller_names_it():
    """`/results rounds amend` keeps acting on a season pending completion."""
    season = _season(SeasonStage.PENDING_COMPLETION)
    interaction = _interaction()

    got = await season_for_command(
        interaction, _service(season), "results rounds amend", stages=LIVE_STAGES
    )

    assert got is season
    interaction.response.send_message.assert_not_awaited()


async def test_no_live_season_is_refused_and_says_the_archive_is_never_changed():
    interaction = _interaction()

    got = await season_for_command(interaction, _service(None), "results rounds sync")

    assert got is None
    said = _said(interaction)
    assert "/results rounds sync" in said
    assert "archive" in said


async def test_a_completed_season_is_unreachable_whatever_stages_are_named():
    """The helper reads the live season alone, so the archive never reaches a caller."""
    service = _service(None)
    interaction = _interaction()

    got = await season_for_command(
        interaction,
        service,
        "results rounds amend",
        stages=LIVE_STAGES | {SeasonStage.COMPLETED, SeasonStage.CANCELLED},
    )

    assert got is None
    service.get_setup_or_active_season.assert_awaited_once()


async def test_a_narrower_stage_set_uses_the_callers_own_refusal():
    interaction = _interaction()

    got = await season_for_command(
        interaction,
        _service(_season(SeasonStage.ONGOING)),
        "driver reassign",
        stages=PLACEMENT_STAGES,
        refusal="⛔ Use `/driver move` instead.",
    )

    assert got is None
    assert _said(interaction) == "⛔ Use `/driver move` instead."


async def test_a_callers_refusal_covers_the_no_season_case_too():
    """A command limited to two stages says the same thing whether the season is elsewhere
    or absent. The default wording would send a manager off to start a season to no purpose.
    """
    interaction = _interaction()

    got = await season_for_command(
        interaction,
        _service(None),
        "driver reassign",
        stages=PLACEMENT_STAGES,
        refusal="\u26d4 Use `/driver move` instead.",
    )

    assert got is None
    assert _said(interaction) == "\u26d4 Use `/driver move` instead."


async def test_a_narrower_set_without_a_refusal_names_the_stage_the_season_stands_in():
    interaction = _interaction()

    got = await season_for_command(
        interaction,
        _service(_season(SeasonStage.ONGOING_SIGNUPS)),
        "driver reassign",
        stages=PLACEMENT_STAGES,
    )

    assert got is None
    assert "Ongoing, signups open" in _said(interaction)


# ---------------------------------------------------------------------------
# How it answers
# ---------------------------------------------------------------------------

async def test_a_command_that_has_not_deferred_is_answered_through_the_response():
    interaction = _interaction(done=False)

    await season_for_command(
        interaction, _service(_season(SeasonStage.PENDING_COMPLETION)), "team role"
    )

    interaction.response.send_message.assert_awaited_once()
    interaction.followup.send.assert_not_awaited()


async def test_a_command_that_has_deferred_is_answered_through_the_followup():
    interaction = _interaction(done=True)

    await season_for_command(
        interaction, _service(_season(SeasonStage.PENDING_COMPLETION)), "team role"
    )

    interaction.followup.send.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize("done", [False, True])
async def test_every_refusal_is_seen_by_the_runner_alone(done):
    interaction = _interaction(done=done)

    await season_for_command(interaction, _service(None), "team role")

    mock = interaction.followup.send if done else interaction.response.send_message
    assert mock.await_args.kwargs["ephemeral"] is True


# ---------------------------------------------------------------------------
# The labels
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", sorted(SeasonStage, key=lambda s: s.value))
def test_every_stage_is_named_as_the_specification_names_it(stage):
    label = stage_label(stage)
    assert label
    assert label != stage.value or stage.value.isalpha()


def test_the_two_ongoing_sub_stages_read_as_prose_not_as_identifiers():
    assert stage_label(SeasonStage.ONGOING_SIGNUPS) == "Ongoing, signups open"
    assert stage_label(SeasonStage.ONGOING_PLACEMENTS) == "Ongoing, placements"


def test_a_season_with_no_stage_at_all_still_gets_a_readable_label():
    assert stage_label(None) == "no stage"
