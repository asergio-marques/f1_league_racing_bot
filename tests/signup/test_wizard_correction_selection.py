"""`select_correction_parameter` — sending a driver back to one question.

Issue #208. `tests/signup/test_correction_parameter_timeout.py` covers the window *lapsing*;
choosing a parameter within it was unexecuted. This is the step that turns "something is wrong
with your signup" into a specific question the driver can answer.

**It re-opens exactly one question, and the wizard must not run on from there.** The
`_is_correction` flag is what `_advance_wizard_in_channel` reads to commit instead of
continuing, so without it a driver asked to fix their platform would be walked through every
subsequent question again. `test_the_wizard_is_marked_as_a_correction` holds it; its absence
would be invisible here and only surface as a driver being re-interrogated.

**Two of the nine parameters need state reset beyond the wizard's step**, and both are the
kind of thing a reader trims:

- *Lap times* are collected one track at a time, so the track index goes back to zero. Left
  where it was, the driver would be asked only about the track after the last one they
  answered, or about none at all.
- *Preferred teams* is a sub-step loop with its own counter and an accumulating list. Both are
  cleared, so the driver picks afresh rather than appending to what the league already
  rejected.

**The write permission is set for the step being returned to, not merely restored.** A
correction can send a driver back to a button step or a typed one, and the permission has to
match: a driver returned to a typed question with typing revoked cannot answer at all, and the
correction stalls until the 24-hour timeout ends their signup.

**The reason is consumed here.** It was written down when the manager asked for changes, and
appears exactly once — in the prompt the driver reads. Popping it rather than reading it means
a second correction cannot carry the first one's reason.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99

#: Every parameter a manager may send back, and the step it re-opens.
PARAMETERS = [
    ("nationality", "COLLECTING_NATIONALITY"),
    ("platform", "COLLECTING_PLATFORM"),
    ("platform_id", "COLLECTING_PLATFORM_ID"),
    ("availability", "COLLECTING_AVAILABILITY"),
    ("driver_type", "COLLECTING_DRIVER_TYPE"),
    ("preferred_teams", "COLLECTING_PREFERRED_TEAMS"),
    ("preferred_teammate", "COLLECTING_PREFERRED_TEAMMATE"),
    ("lap_times", "COLLECTING_LAP_TIME"),
    ("notes", "COLLECTING_NOTES"),
]


def _wizard(**draft):
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=WizardState.UNENGAGED,
        signup_channel_id=CHANNEL_ID,
        config_snapshot=ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=["1", "2"],
            slots=[],
            team_names=["Alpha", "Beta"],
        ),
        draft_answers=dict(draft),
        current_lap_track_index=2,
        last_activity_at=None,
    )


@pytest.fixture
def correction():
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}

    svc._arm_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._grant_driver_write = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._revoke_driver_write = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._get_track_name_map = AsyncMock(return_value={})  # type: ignore[method-assign]
    svc._prompt_for_state = MagicMock(return_value="prompt")  # type: ignore[method-assign]
    svc._build_step_view = MagicMock(return_value=None)  # type: ignore[method-assign]

    signup_svc = MagicMock()
    signup_svc.get_wizard = AsyncMock(return_value=_wizard())
    signup_svc.save_wizard = AsyncMock(return_value=None)

    driver_service = MagicMock()
    driver_service.transition = AsyncMock(return_value=None)

    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.driver_service = driver_service
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot

    member = MagicMock()
    member.mention = f"<@{DRIVER_ID}>"
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=channel)
    guild.get_member = MagicMock(return_value=member)

    return SimpleNamespace(
        svc=svc,
        guild=guild,
        channel=channel,
        signup_svc=signup_svc,
        driver_service=driver_service,
    )


async def _select(ctx, parameter: str, wizard=None):
    if wizard is not None:
        ctx.signup_svc.get_wizard = AsyncMock(return_value=wizard)
    await ctx.svc.select_correction_parameter(
        DRIVER_ID, parameter, ctx.guild
    )


# ---------------------------------------------------------------------------
# The parameter map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parameter,state", PARAMETERS)
async def test_each_parameter_re_opens_its_own_step(correction, parameter, state):
    """Nine hand-written map entries. One pointing at the wrong step would send a driver
    back to a question the manager did not ask about."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard()

    await _select(correction, parameter, wizard)

    assert wizard.wizard_state == getattr(WizardState, state)


async def test_an_unknown_parameter_changes_nothing(correction):
    """A stale button from an older version of the bot."""
    wizard = _wizard()

    await _select(correction, "favourite_colour", wizard)

    correction.driver_service.transition.assert_not_awaited()
    correction.signup_svc.save_wizard.assert_not_awaited()


async def test_a_driver_with_no_wizard_is_left_alone(correction):
    correction.signup_svc.get_wizard = AsyncMock(return_value=None)

    await correction.svc.select_correction_parameter(
        DRIVER_ID, "platform", correction.guild
    )

    correction.driver_service.transition.assert_not_awaited()


# ---------------------------------------------------------------------------
# The correction itself
# ---------------------------------------------------------------------------


async def test_the_driver_is_asked_to_correct_rather_than_to_sign_up_again(correction):
    from leaguebot.core.models.driver_profile import DriverState

    await _select(correction, "platform")

    assert correction.driver_service.transition.await_args.args[1] == (
        DriverState.PENDING_DRIVER_CORRECTION
    )


async def test_the_wizard_is_marked_as_a_correction(correction):
    """`_advance_wizard_in_channel` reads this to commit instead of continuing. Without it
    a driver asked to fix one answer is walked through every later question again."""
    wizard = _wizard()

    await _select(correction, "platform", wizard)

    assert wizard.draft_answers["_is_correction"] is True


async def test_the_selection_window_is_cancelled(correction):
    """The manager has chosen, so the five-minute timeout must not still fire and release
    the driver from a correction they are part-way through."""
    task = MagicMock()
    correction.svc._correction_tasks[DRIVER_ID] = task

    await _select(correction, "platform")

    task.cancel.assert_called_once()
    assert DRIVER_ID not in correction.svc._correction_tasks


async def test_a_fresh_inactivity_deadline_is_armed(correction):
    """The driver now has 24 hours to answer, the same as any other step."""
    await _select(correction, "platform")

    correction.svc._arm_inactivity_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# The two parameters needing more than a step change
# ---------------------------------------------------------------------------


async def test_correcting_lap_times_starts_again_from_the_first_track(correction):
    """Left where it was, the driver would be asked about the track after the last one
    they answered — or, at the end of the list, about nothing at all."""
    wizard = _wizard()
    assert wizard.current_lap_track_index == 2

    await _select(correction, "lap_times", wizard)

    assert wizard.current_lap_track_index == 0


async def test_correcting_preferred_teams_clears_the_previous_picks(correction):
    """The loop appends. Without the reset the driver's new choices would be added to the
    ones the league had just rejected."""
    wizard = _wizard(preferred_teams=["Alpha", "Beta"], _pref_teams_step=2)

    await _select(correction, "preferred_teams", wizard)

    assert wizard.draft_answers["preferred_teams"] == []
    assert "_pref_teams_step" not in wizard.draft_answers


async def test_correcting_another_parameter_leaves_the_lap_index_alone(correction):
    """The reset is specific to lap times; a driver correcting their notes has not
    unanswered their lap times."""
    wizard = _wizard()

    await _select(correction, "notes", wizard)

    assert wizard.current_lap_track_index == 2


# ---------------------------------------------------------------------------
# The prompt, and the write permission
# ---------------------------------------------------------------------------


async def test_the_driver_is_prompted_in_their_channel_and_named(correction):
    await _select(correction, "platform_id")

    correction.channel.send.assert_awaited_once()
    sent = correction.channel.send.await_args.args[0]
    assert DRIVER_ID in sent
    assert "platform id" in sent


async def test_the_reason_the_manager_gave_reaches_the_driver(correction):
    """It is the whole point of asking rather than rejecting."""
    wizard = _wizard(_correction_reason="Your platform ID does not match your account")

    await _select(correction, "platform_id", wizard)

    assert "does not match your account" in correction.channel.send.await_args.args[0]


async def test_the_reason_is_consumed_so_a_later_correction_cannot_reuse_it(correction):
    """Popped, not read. A second correction carrying the first one's reason would tell
    the driver to fix something they had already fixed."""
    wizard = _wizard(_correction_reason="Wrong platform")

    await _select(correction, "platform_id", wizard)

    assert "_correction_reason" not in wizard.draft_answers


async def test_no_reason_gives_no_reason_line(correction):
    await _select(correction, "platform_id")

    assert "**Reason:**" not in correction.channel.send.await_args.args[0]


async def test_returning_to_a_button_step_revokes_typing(correction):
    await _select(correction, "platform")

    correction.svc._revoke_driver_write.assert_awaited_once()
    correction.svc._grant_driver_write.assert_not_awaited()


async def test_returning_to_a_typed_step_restores_typing(correction):
    """The case that matters. A driver returned to a typed question with typing revoked
    cannot answer at all, and the correction stalls until the timeout ends their signup."""
    await _select(correction, "notes")

    correction.svc._grant_driver_write.assert_awaited_once()
    correction.svc._revoke_driver_write.assert_not_awaited()


async def test_a_missing_channel_still_records_the_correction(correction):
    """The driver state and the wizard step are the league's record; the prompt is only
    how the driver learns of it."""
    wizard = _wizard()
    correction.guild.get_channel = MagicMock(return_value=None)

    await _select(correction, "platform", wizard)

    assert wizard.draft_answers["_is_correction"] is True
    correction.channel.send.assert_not_awaited()
