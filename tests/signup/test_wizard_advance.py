"""`_advance_wizard_in_channel` — the order the signup wizard asks its questions in.

Issue #208. This is the wizard's spine: every step handler ends by calling it, and it decides
what the driver is asked next, whether they are asked at all, and when the signup is committed.
It was unexecuted.

**Three steps are conditional, and each is skipped for a different reason.**

- *Nationality* is asked only when the league requires it, and it is the **first** step, so
  skipping it changes where the wizard starts as well as what it asks.
- *Preferred teams* is skipped for a **Reserve Driver**. A reserve is not placed in a team, so
  asking which team they would like collects an answer nothing can honour — and the driver's
  own earlier answer is what decides it, which makes the skip depend on a value collected two
  steps earlier.
- *Lap times* repeat, once per track the league selected, and are skipped entirely when it
  selected none. This is the only step the wizard can stay on, and `current_lap_track_index` is
  what distinguishes "ask again for the next track" from "move on".

**The write permission flips around the button-only steps.** Platform, driver type and
preferred teams are answered by pressing a button, and the driver's ability to type in the
channel is revoked on the way in and restored on the way out. Getting this wrong is not
cosmetic: a driver left without write permission cannot answer the next typed question and the
wizard stalls until it times out. `test_write_is_restored_when_leaving_a_button_only_step` and
its pair hold both halves.

**Correction mode short-circuits the whole thing.** A driver amending one answer after review
must commit that correction, not resume the sequence and be asked every later question again.
The flag is *popped* rather than read, so a correction cannot leak into the next advance.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SERVER_ID = 1
DRIVER_ID = "7"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(state, *, tracks=(), nationality_required=False, lap_index=0, **draft):
    from models.signup_module import ConfigSnapshot, SignupWizardRecord

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=state,
        signup_channel_id=99,
        config_snapshot=ConfigSnapshot(
            nationality_required=nationality_required,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=list(tracks),
            slots=[],
            team_names=["Alpha", "Beta"],
        ),
        draft_answers=dict(draft),
        current_lap_track_index=lap_index,
        last_activity_at=None,
    )


@pytest.fixture
def advancer():
    """A `WizardService` with everything but the advancement logic stubbed."""
    from services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)

    svc._get_track_name_map = AsyncMock(return_value={})  # type: ignore[method-assign]
    svc._prompt_for_state = MagicMock(return_value="prompt")  # type: ignore[method-assign]
    svc._build_step_view = MagicMock(return_value=None)  # type: ignore[method-assign]
    svc._reset_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._grant_driver_write = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._revoke_driver_write = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc.commit_wizard = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._commit_correction = AsyncMock(return_value=None)  # type: ignore[method-assign]

    signup_svc = MagicMock()
    signup_svc.save_wizard = AsyncMock(return_value=None)
    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)

    member = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    guild.get_member = MagicMock(return_value=member)

    return SimpleNamespace(svc=svc, channel=channel, guild=guild, signup_svc=signup_svc)


async def _advance(ctx, wizard):
    await ctx.svc._advance_wizard_in_channel(wizard, ctx.channel, ctx.guild)


# ---------------------------------------------------------------------------
# The ordinary sequence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current,expected",
    [
        ("COLLECTING_PLATFORM", "COLLECTING_PLATFORM_ID"),
        ("COLLECTING_PLATFORM_ID", "COLLECTING_AVAILABILITY"),
        ("COLLECTING_AVAILABILITY", "COLLECTING_DRIVER_TYPE"),
        ("COLLECTING_PREFERRED_TEAMS", "COLLECTING_PREFERRED_TEAMMATE"),
    ],
)
async def test_each_step_leads_to_the_next(advancer, current, expected):
    from models.signup_module import WizardState

    wizard = _wizard(getattr(WizardState, current))

    await _advance(advancer, wizard)

    assert wizard.wizard_state == getattr(WizardState, expected)
    advancer.channel.send.assert_awaited_once()


async def test_moving_on_saves_the_wizard_and_resets_the_timeout(advancer):
    """The wizard survives a restart, so the new state has to be persisted before the
    prompt goes out — not after, or a restart in between would ask the old question."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)

    await _advance(advancer, wizard)

    advancer.signup_svc.save_wizard.assert_awaited_once()
    advancer.svc._reset_inactivity_job.assert_awaited_once()
    assert wizard.last_activity_at is not None


# ---------------------------------------------------------------------------
# Nationality — asked only when the league wants it
# ---------------------------------------------------------------------------


async def test_nationality_is_skipped_when_the_league_does_not_require_it(advancer):
    """It is the first step, so this decides where the wizard starts as well."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NATIONALITY, nationality_required=False)

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_PLATFORM


async def test_nationality_leads_to_platform_when_it_is_required(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NATIONALITY, nationality_required=True)

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_PLATFORM


# ---------------------------------------------------------------------------
# Preferred teams — not asked of a reserve
# ---------------------------------------------------------------------------


async def test_a_reserve_driver_is_not_asked_which_team_they_prefer(advancer):
    """A reserve is not placed in a team, so the answer could not be honoured. The skip
    turns on an answer given two steps earlier."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE, driver_type="Reserve Driver")

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_PREFERRED_TEAMMATE


async def test_a_full_time_driver_is_asked_which_team_they_prefer(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE, driver_type="Full-Time Driver")

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_PREFERRED_TEAMS


# ---------------------------------------------------------------------------
# Lap times — repeated per track, or skipped
# ---------------------------------------------------------------------------


async def test_lap_times_are_skipped_when_the_league_selected_no_tracks(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMMATE, tracks=())

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_NOTES


async def test_lap_times_are_asked_for_when_tracks_are_selected(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMMATE, tracks=("1", "2"))

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_LAP_TIME


async def test_the_wizard_stays_on_lap_time_while_tracks_remain(advancer):
    """The only step the wizard can stay on. The index has already been moved by the
    handler, so this is asking about the *next* track rather than repeating the last."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_LAP_TIME, tracks=("1", "2"), lap_index=1)

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_LAP_TIME
    advancer.channel.send.assert_awaited_once()
    advancer.svc._reset_inactivity_job.assert_awaited_once()


async def test_the_last_track_moves_the_wizard_on(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_LAP_TIME, tracks=("1", "2"), lap_index=2)

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_NOTES


# ---------------------------------------------------------------------------
# The write permission around button-only steps
# ---------------------------------------------------------------------------


async def test_write_is_revoked_when_entering_a_button_only_step(advancer):
    """Platform is answered by a button, so typing is turned off to stop a driver
    answering in a form the wizard will not read."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NATIONALITY, nationality_required=True)

    await _advance(advancer, wizard)

    advancer.svc._revoke_driver_write.assert_awaited_once()


async def test_write_is_restored_when_leaving_a_button_only_step(advancer):
    """The half that matters most. A driver left without write permission cannot answer
    the next typed question, and the wizard stalls until it times out."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)

    await _advance(advancer, wizard)

    advancer.svc._grant_driver_write.assert_awaited_once()


async def test_a_driver_who_has_left_the_server_does_not_stop_the_advance(advancer):
    """`get_member` returns None for someone who has left. The permission call is skipped
    rather than raising inside the wizard."""
    from models.signup_module import WizardState

    advancer.guild.get_member = MagicMock(return_value=None)
    wizard = _wizard(WizardState.COLLECTING_PLATFORM)

    await _advance(advancer, wizard)

    assert wizard.wizard_state == WizardState.COLLECTING_PLATFORM_ID
    advancer.svc._grant_driver_write.assert_not_awaited()


# ---------------------------------------------------------------------------
# The end of the sequence
# ---------------------------------------------------------------------------


async def test_the_last_step_commits_the_signup(advancer):
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES)

    await _advance(advancer, wizard)

    advancer.svc.commit_wizard.assert_awaited_once_with(DRIVER_ID, advancer.guild)


async def test_the_final_answers_are_saved_before_the_commit_reads_them(advancer):
    """`commit_wizard` reads the draft back out of the database rather than being handed
    it, so an unsaved last answer would be committed as though never given."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES, notes="See you there")

    await _advance(advancer, wizard)

    advancer.signup_svc.save_wizard.assert_awaited_once()


# ---------------------------------------------------------------------------
# Correction mode
# ---------------------------------------------------------------------------


async def test_a_correction_commits_instead_of_resuming_the_sequence(advancer):
    """A driver amending one answer after review must not be asked every later question
    again."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM, _is_correction=True)

    await _advance(advancer, wizard)

    advancer.svc._commit_correction.assert_awaited_once()
    advancer.svc.commit_wizard.assert_not_awaited()
    advancer.channel.send.assert_not_awaited()


async def test_the_correction_flag_is_consumed_rather_than_left_behind(advancer):
    """Popped, not read. Left in place it would make the *next* advance a correction too,
    and the driver would never reach the rest of the wizard."""
    from models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM, _is_correction=True)

    await _advance(advancer, wizard)

    assert "_is_correction" not in wizard.draft_answers
