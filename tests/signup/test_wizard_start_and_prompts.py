"""The questions a signup asks.

Issue #208. `_prompt_for_state` (the text of every question) and `_build_step_view` (the buttons
under each) were almost entirely untested; the jobs that post the next question use both.

**Every question says what step it is and how to answer.** The prompt text is what a driver
reads, so each state is tested for its step number and its instruction; a lap-time question
names the track by name rather than id, counts through the tracks, and asks for a screenshot
where the league wants one.

Pressing Sign Up is `signup.start` on the change queue (#439 slice 6b), pinned in
`tests/signup/test_wizard_changes.py`: the channel and who sees it, the configuration frozen
with no reserve team, the first question and the typing lock, the welcome, the started line,
and an abandoned signup's channel and timers. A correction's commit is pinned in
`tests/signup/test_wizard_commit.py`, and a closing notice, the lock and the channel's deletion
a day on in `tests/signup/test_closing_steps.py`: each is a job on the change queue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.models.signup_module import (
    AvailabilitySlot,
    ConfigSnapshot,
    SignupWizardRecord,
    WizardState,
)
from leaguebot.signup.services.wizard_service import WizardService

SERVER_ID = 14208
DRIVER = "4242"
OLD_CHANNEL = 7199


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _slot(seq, label, slot_id):
    return AvailabilitySlot(
        id=seq, slot_id=slot_id, slot_sequence_id=seq,
        day_of_week=1, time_hhmm="19:00", display_label=label,
    )


def _snapshot(*, nationality=False, tracks=(), time_type="TIME_TRIAL", image=False, slots=()):
    return ConfigSnapshot(
        nationality_required=nationality,
        time_type=time_type,
        time_image_required=image,
        selected_track_ids=list(tracks),
        slots=list(slots),
    )


def _wizard(state=WizardState.COLLECTING_PLATFORM, *, channel=OLD_CHANNEL, draft=None, index=0, snapshot=None):
    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER,
        wizard_state=state,
        signup_channel_id=channel,
        config_snapshot=snapshot or _snapshot(),
        draft_answers=dict(draft or {}),
        current_lap_track_index=index,
        last_activity_at=datetime.now(timezone.utc).isoformat(),
    )


def _service(
    *,
    existing=None,
    signup_cfg=True,
    snapshot=None,
    teams=None,
    record=None,
    module_enabled=True,
    signups_open=True,
):
    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}
    svc._scheduler = MagicMock()
    svc._scheduler._scheduler = MagicMock()
    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock()

    signup = MagicMock()
    signup.get_wizard = AsyncMock(return_value=existing)
    signup.get_config = AsyncMock(
        return_value=SimpleNamespace(
            signup_channel_id=600,
            signups_open=signups_open,
            signup_button_message_id=8800 if signups_open else None,
        )
        if signup_cfg
        else None
    )
    signup.get_window_state = AsyncMock(return_value=signup_cfg and signups_open)
    signup.capture_config_snapshot = AsyncMock(return_value=snapshot or _snapshot())
    signup.save_wizard = AsyncMock()
    signup.delete_wizard = AsyncMock()
    signup.get_record = AsyncMock(return_value=record)
    signup.save_record = AsyncMock()

    drivers = MagicMock()
    drivers.transition = AsyncMock()

    bot = MagicMock()
    bot.signup_module_service = signup
    bot.driver_service = drivers
    bot.module_service.is_signup_enabled = AsyncMock(return_value=module_enabled)
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(
            interaction_role_id=900, league_admin_role_id=901, test_mode_active=False
        )
    )
    bot.team_service.get_default_teams = AsyncMock(
        return_value=teams
        if teams is not None
        else [
            SimpleNamespace(name="Red", full_name="Red Racing", is_reserve=False),
            SimpleNamespace(name="Reserves", full_name="Reserves", is_reserve=True),
        ]
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot
    svc._get_track_name_map = AsyncMock(return_value={"27": "Silverstone"})
    return svc


# ---------------------------------------------------------------------------
# The questions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,step,instruction",
    [
        (WizardState.COLLECTING_NATIONALITY, "Step 1 — Nationality", "type `other`"),
        (WizardState.COLLECTING_PLATFORM, "Step 2 — Platform", "buttons below"),
        (WizardState.COLLECTING_PLATFORM_ID, "Step 3 — Platform ID", "gamertag"),
        (WizardState.COLLECTING_DRIVER_TYPE, "Step 5 — Driver Type", "buttons below"),
        (WizardState.COLLECTING_PREFERRED_TEAMS, "Step 6 — 1st Preferred Team", "No Preference"),
        (WizardState.COLLECTING_PREFERRED_TEAMMATE, "Step 7 — Preferred Teammate", "No Preference"),
        (WizardState.COLLECTING_NOTES, "Step 9 — Additional Notes", "max 50 chars"),
    ],
)
def test_each_question_names_its_step_and_how_to_answer(state, step, instruction):
    prompt = _service()._prompt_for_state(state, _snapshot())

    assert step in prompt
    assert instruction in prompt


def test_the_availability_question_lists_the_slots_by_their_numbers():
    """The driver types the ordinals; the durable slot id never reaches them (#126)."""
    snapshot = _snapshot(slots=[_slot(1, "Monday 19:00 UTC", "Mon_19_00"), _slot(2, "Friday 21:00 UTC", "Fri_21_00")])

    prompt = _service()._prompt_for_state(WizardState.COLLECTING_AVAILABILITY, snapshot)

    assert "`1` — Monday 19:00 UTC" in prompt
    assert "`2` — Friday 21:00 UTC" in prompt
    assert "Mon_19_00" not in prompt


def test_a_lap_time_question_names_the_track_and_counts_through_them():
    snapshot = _snapshot(tracks=["27", "11"])
    wizard = _wizard(WizardState.COLLECTING_LAP_TIME, index=0, snapshot=snapshot)

    prompt = _service()._prompt_for_state(
        WizardState.COLLECTING_LAP_TIME, snapshot, wizard, {"27": "Silverstone"}
    )

    assert "Time Trial (1/2: Silverstone)" in prompt
    assert "`M:ss.mmm`" in prompt


def test_an_unnamed_track_falls_back_to_its_id():
    snapshot = _snapshot(tracks=["27", "11"])
    wizard = _wizard(WizardState.COLLECTING_LAP_TIME, index=1, snapshot=snapshot)

    prompt = _service()._prompt_for_state(
        WizardState.COLLECTING_LAP_TIME, snapshot, wizard, {"27": "Silverstone"}
    )

    assert "(2/2: 11)" in prompt


def test_a_short_qualifying_league_is_asked_for_that():
    snapshot = _snapshot(tracks=["27"], time_type="SHORT_QUALIFICATION")

    prompt = _service()._prompt_for_state(
        WizardState.COLLECTING_LAP_TIME, snapshot, _wizard(snapshot=snapshot), {}
    )

    assert "Short Qualification" in prompt


def test_a_screenshot_is_asked_for_where_the_league_requires_one():
    with_image = _snapshot(tracks=["27"], image=True)
    without = _snapshot(tracks=["27"], image=False)
    svc = _service()

    assert "attach screenshot" in svc._prompt_for_state(
        WizardState.COLLECTING_LAP_TIME, with_image, _wizard(snapshot=with_image), {}
    )
    assert "attach screenshot" not in svc._prompt_for_state(
        WizardState.COLLECTING_LAP_TIME, without, _wizard(snapshot=without), {}
    )


def test_a_state_with_no_question_says_so_rather_than_raising():
    assert _service()._prompt_for_state(WizardState.UNENGAGED, _snapshot()) == "Ready for next step."


# ---------------------------------------------------------------------------
# The buttons under each question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state,view_name",
    [
        (WizardState.COLLECTING_PLATFORM, "PlatformButtonView"),
        (WizardState.COLLECTING_DRIVER_TYPE, "DriverTypeButtonView"),
        (WizardState.COLLECTING_PREFERRED_TEAMS, "PreferredTeamsButtonView"),
        (WizardState.COLLECTING_PREFERRED_TEAMMATE, "NoPreferenceTeammateView"),
        (WizardState.COLLECTING_NOTES, "NoNotesButtonView"),
        (WizardState.COLLECTING_PLATFORM_ID, "WithdrawButtonView"),
    ],
)
async def test_each_question_gets_its_own_buttons(state, view_name):
    """A typed step still offers a way out, which is what the withdraw view is."""
    view = _service()._build_step_view(state, DRIVER, ["Red"])

    assert type(view).__name__ == view_name
