"""Starting a signup, the questions it asks, a correction being committed, and the channel's end.

Issue #208. The signup wizard's step handlers, approval and recovery were already tested; what
a driver meets first was not. `start_wizard` (pressing Sign Up), `_prompt_for_state` (the text
of every question), `_commit_correction` (answering a manager's request for changes) and the two
channel-ending helpers were all almost entirely untested.

**Pressing Sign Up again starts cleanly.** A driver who abandoned a wizard and presses the button
again gets a fresh channel: the old one is deleted, and its inactivity job, channel-delete job
and any pending correction window are cancelled. Left behind, they would fire into the new
signup — the old timeout ending a wizard the driver is halfway through.

**The configuration is frozen when the signup starts.** The snapshot taken at the start is what
the driver is asked against, so a manager editing the tracks or slots mid-signup does not change
the questions under a driver already answering them. The team buttons come from the league's
full-time teams only; a reserve team is not a team a driver asks to join.

**Nationality is skipped unless the league requires it.** The wizard starts at nationality or
at platform accordingly, and a button-only first step locks the driver's typing until they
press a button, so a stray message cannot be read as an answer.

**Every question says what step it is and how to answer.** The prompt text is what a driver
reads, so each state is tested for its step number and its instruction; a lap-time question
names the track by name rather than id, counts through the tracks, and asks for a screenshot
where the league wants one.

**A correction changes only what was corrected, and hands the signup straight back.** The
driver's other answers stand, the driver returns to waiting for approval, the wizard is parked,
and a fresh review panel is posted — the manager who asked for the change is the one waiting
to see it.

**The channel is held, then deleted.** Ending a signup revokes the driver's write access, posts
why, and schedules deletion in 24 hours, so the driver can read what happened; the scheduled
deletion removes the channel and the wizard record together.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.models.driver_profile import DriverState  # noqa: E402
from leaguebot.signup.models.signup_module import (  # noqa: E402
    AvailabilitySlot,
    ConfigSnapshot,
    SignupRecord,
    SignupWizardRecord,
    WizardState,
)
from leaguebot.signup.services.wizard_service import WizardService  # noqa: E402

SERVER_ID = 14208
DRIVER = "4242"
NEW_CHANNEL = 7200
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


def _record():
    return SignupRecord(
        id=1, discord_user_id=DRIVER, discord_username="racer",
        server_display_name="Racer", nationality="British", platform="Steam",
        platform_id="racer_steam", availability_slot_ids=["Mon_19_00"], driver_type="FULL_TIME",
        preferred_teams=["Red"], preferred_teammate=None, lap_times={"27": "1:23.456"},
        notes=None, signup_channel_id=OLD_CHANNEL,
    )


def _service(*, existing=None, signup_cfg=True, snapshot=None, teams=None, record=None):
    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}
    svc._scheduler = MagicMock()
    svc._scheduler._scheduler = MagicMock()
    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock()

    signup = MagicMock()
    signup.get_wizard = AsyncMock(return_value=existing)
    signup.get_config = AsyncMock(
        return_value=SimpleNamespace(signup_channel_id=600) if signup_cfg else None
    )
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
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(interaction_role_id=900, league_admin_role_id=901)
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


def _guild(*, old_channel=None, new_channel=None):
    guild = MagicMock()
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: old_channel if cid == OLD_CHANNEL else (new_channel if cid == NEW_CHANNEL else None)
    )
    roles = {rid: MagicMock(id=rid) for rid in (900, 901)}
    guild.get_role = MagicMock(side_effect=lambda rid: roles.get(rid))
    created = new_channel or _channel(NEW_CHANNEL)
    guild.create_text_channel = AsyncMock(return_value=created)
    guild._created = created
    return guild


def _channel(cid):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = cid
    channel.send = AsyncMock()
    channel.delete = AsyncMock()
    channel.set_permissions = AsyncMock()
    return channel


def _interaction(guild):
    member = MagicMock(spec=discord.Member)
    member.id = int(DRIVER)
    member.name = "Racer.One"
    member.display_name = "Racer"
    member.mention = f"<@{DRIVER}>"
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = member
    return interaction


def _saved_wizard(svc) -> SignupWizardRecord:
    return svc._bot.signup_module_service.save_wizard.await_args.args[0]


# ---------------------------------------------------------------------------
# Pressing Sign Up
# ---------------------------------------------------------------------------


async def test_a_private_channel_is_created_for_the_driver(tmp_path):
    svc = _service()
    guild = _guild()

    channel = await svc.start_wizard(_interaction(guild))

    assert channel is guild._created
    name = guild.create_text_channel.await_args.args[0]
    assert name == "racer-one-signup"


async def test_the_channel_is_visible_to_the_driver_and_both_staff_tiers(tmp_path):
    """#116: a league admin without the interaction role must still see the signup they are
    entitled to approve."""
    svc = _service()
    guild = _guild()
    interaction = _interaction(guild)

    await svc.start_wizard(interaction)

    overwrites = guild.create_text_channel.await_args.kwargs["overwrites"]
    assert overwrites[guild.default_role].view_channel is False
    assert overwrites[interaction.user].view_channel is True
    roles = {getattr(k, "id", None) for k in overwrites}
    assert {900, 901} <= roles


async def test_an_unconfigured_module_starts_nothing(tmp_path):
    svc = _service(signup_cfg=False)
    guild = _guild()

    assert await svc.start_wizard(_interaction(guild)) is None

    guild.create_text_channel.assert_not_awaited()
    svc._bot.driver_service.transition.assert_not_awaited()


async def test_the_driver_becomes_pending_signup_completion(tmp_path):
    svc = _service()

    await svc.start_wizard(_interaction(_guild()))

    svc._bot.driver_service.transition.assert_awaited_once_with(
        DRIVER, DriverState.PENDING_SIGNUP_COMPLETION
    )


async def test_the_wizard_is_saved_with_the_frozen_configuration(tmp_path):
    """The questions are asked against the configuration as it stood at the start."""
    snapshot = _snapshot(tracks=["27"])
    svc = _service(snapshot=snapshot)

    await svc.start_wizard(_interaction(_guild()))

    wizard = _saved_wizard(svc)
    assert wizard.config_snapshot is snapshot
    assert wizard.signup_channel_id == NEW_CHANNEL
    assert wizard.draft_answers == {"discord_username": "Racer.One", "server_display_name": "Racer"}


async def test_the_team_buttons_leave_the_reserve_team_out(tmp_path):
    """A reserve team is not a team a driver asks to join."""
    svc = _service()

    await svc.start_wizard(_interaction(_guild()))

    assert _saved_wizard(svc).config_snapshot.team_names == ["Red Racing"]


async def test_nationality_is_asked_first_where_the_league_requires_it(tmp_path):
    svc = _service(snapshot=_snapshot(nationality=True))
    guild = _guild()

    await svc.start_wizard(_interaction(guild))

    assert _saved_wizard(svc).wizard_state == WizardState.COLLECTING_NATIONALITY
    assert "Step 1 — Nationality" in str(guild._created.send.await_args.args[0])
    guild._created.set_permissions.assert_not_awaited()


async def test_platform_is_asked_first_otherwise_and_typing_is_locked(tmp_path):
    """A button-only first step: a stray message must not be read as an answer."""
    svc = _service(snapshot=_snapshot(nationality=False))
    guild = _guild()
    interaction = _interaction(guild)

    await svc.start_wizard(interaction)

    assert _saved_wizard(svc).wizard_state == WizardState.COLLECTING_PLATFORM
    permissions = guild._created.set_permissions.await_args
    assert permissions.args[0] is interaction.user
    assert permissions.kwargs["send_messages"] is False


async def test_the_driver_is_welcomed_by_name(tmp_path):
    svc = _service()
    guild = _guild()

    await svc.start_wizard(_interaction(guild))

    assert f"Welcome to the signup wizard, <@{DRIVER}>!" in str(guild._created.send.await_args.args[0])


async def test_the_inactivity_timeout_is_armed_for_24_hours(tmp_path):
    svc = _service()
    before = datetime.now(timezone.utc)

    await svc.start_wizard(_interaction(_guild()))

    job = svc._scheduler._scheduler.add_job.call_args
    assert job.kwargs["id"] == f"wizard_inactivity_{DRIVER}"
    fire_at = job.kwargs["trigger"].run_date
    assert timedelta(hours=23, minutes=59) < fire_at - before < timedelta(hours=24, minutes=1)


async def test_starting_is_logged(tmp_path):
    svc = _service()

    await svc.start_wizard(_interaction(_guild()))

    assert "Signup | Started" in str(svc._output_router.post_log.await_args.args[0])


async def test_starting_again_deletes_the_abandoned_channel(tmp_path):
    old = _channel(OLD_CHANNEL)
    svc = _service(existing=_wizard())
    guild = _guild(old_channel=old)

    await svc.start_wizard(_interaction(guild))

    old.delete.assert_awaited_once()


async def test_starting_again_cancels_the_abandoned_wizards_jobs(tmp_path):
    """Left behind, the old timeout would end the wizard the driver is halfway through."""
    svc = _service(existing=_wizard())
    task = MagicMock()
    svc._correction_tasks[DRIVER] = task

    await svc.start_wizard(_interaction(_guild(old_channel=_channel(OLD_CHANNEL))))

    removed = {c.args[0] for c in svc._scheduler._scheduler.remove_job.call_args_list}
    assert f"wizard_inactivity_{DRIVER}" in removed
    assert f"wizard_channel_delete_{DRIVER}" in removed
    task.cancel.assert_called_once()


async def test_an_abandoned_channel_already_gone_does_not_stop_the_start(tmp_path):
    old = _channel(OLD_CHANNEL)
    old.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "gone"))
    svc = _service(existing=_wizard())
    guild = _guild(old_channel=old)

    assert await svc.start_wizard(_interaction(guild)) is guild._created


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


# ---------------------------------------------------------------------------
# Committing a correction
# ---------------------------------------------------------------------------


async def test_only_the_corrected_answer_changes(tmp_path):
    record = _record()
    svc = _service(record=record)
    guild = _guild(old_channel=_channel(OLD_CHANNEL))

    await svc._commit_correction(
        _wizard(WizardState.COLLECTING_PLATFORM_ID, draft={"platform_id": "new_tag"}), guild
    )

    saved = svc._bot.signup_module_service.save_record.await_args.args[0]
    assert saved.platform_id == "new_tag"
    assert (saved.nationality, saved.platform, saved.preferred_teams) == ("British", "Steam", ["Red"])


async def test_the_driver_goes_back_to_waiting_for_approval(tmp_path):
    svc = _service(record=_record())

    await svc._commit_correction(
        _wizard(draft={"nationality": "Irish"}), _guild(old_channel=_channel(OLD_CHANNEL))
    )

    svc._bot.driver_service.transition.assert_awaited_once_with(
        DRIVER, DriverState.PENDING_ADMIN_APPROVAL
    )


async def test_the_wizard_is_parked_and_its_answers_cleared(tmp_path):
    svc = _service(record=_record())
    wizard = _wizard(draft={"nationality": "Irish"})

    await svc._commit_correction(wizard, _guild(old_channel=_channel(OLD_CHANNEL)))

    assert wizard.wizard_state == WizardState.UNENGAGED
    assert wizard.draft_answers == {}
    svc._bot.signup_module_service.save_wizard.assert_awaited_once_with(wizard)


async def test_the_timeout_on_the_correction_is_cancelled(tmp_path):
    svc = _service(record=_record())

    await svc._commit_correction(_wizard(draft={"notes": "hi"}), _guild(old_channel=_channel(OLD_CHANNEL)))

    removed = {c.args[0] for c in svc._scheduler._scheduler.remove_job.call_args_list}
    assert f"wizard_inactivity_{DRIVER}" in removed


async def test_a_fresh_review_panel_is_posted(tmp_path):
    """The manager who asked for the change is the one waiting to see it."""
    channel = _channel(OLD_CHANNEL)
    svc = _service(record=_record())

    await svc._commit_correction(_wizard(draft={"platform_id": "new_tag"}), _guild(old_channel=channel))

    posted = channel.send.await_args
    assert "new_tag" in str(posted.args[0])
    assert type(posted.kwargs["view"]).__name__ == "AdminReviewView"


@pytest.mark.parametrize(
    "field,value",
    [
        ("nationality", "Irish"),
        ("platform", "Xbox"),
        ("availability_slot_ids", ["Fri_21_00"]),
        ("driver_type", "RESERVE"),
        ("preferred_teams", ["Blue"]),
        ("preferred_teammate", "friend"),
        ("lap_times", {"27": "1:22.000"}),
        ("notes", "late on Fridays"),
    ],
)
async def test_every_correctable_field_is_applied(tmp_path, field, value):
    svc = _service(record=_record())

    await svc._commit_correction(_wizard(draft={field: value}), _guild(old_channel=_channel(OLD_CHANNEL)))

    saved = svc._bot.signup_module_service.save_record.await_args.args[0]
    assert getattr(saved, field) == value


async def test_a_correction_with_no_signup_record_does_nothing(tmp_path):
    svc = _service(record=None)

    await svc._commit_correction(_wizard(draft={"notes": "hi"}), _guild())

    svc._bot.driver_service.transition.assert_not_awaited()
    svc._bot.signup_module_service.save_record.assert_not_awaited()


# ---------------------------------------------------------------------------
# The channel's end
# ---------------------------------------------------------------------------


async def test_ending_a_signup_holds_the_channel(tmp_path):
    """The driver can read why it ended, and cannot type into it."""
    channel = _channel(OLD_CHANNEL)
    svc = _service(existing=_wizard())
    guild = _guild(old_channel=channel)
    member = MagicMock()
    guild.get_member = MagicMock(return_value=member)

    await svc._trigger_channel_hold(DRIVER, guild, "Signups have closed.")

    assert channel.set_permissions.await_args.kwargs["send_messages"] is False
    channel.send.assert_awaited_once_with("Signups have closed.")
    job = svc._scheduler._scheduler.add_job.call_args
    assert job.kwargs["id"] == f"wizard_channel_delete_{DRIVER}"


async def test_a_held_channel_is_deleted_in_24_hours(tmp_path):
    svc = _service(existing=_wizard())
    guild = _guild(old_channel=_channel(OLD_CHANNEL))
    guild.get_member = MagicMock(return_value=None)
    before = datetime.now(timezone.utc)

    await svc._trigger_channel_hold(DRIVER, guild, "ended")

    fire_at = svc._scheduler._scheduler.add_job.call_args.kwargs["trigger"].run_date
    assert timedelta(hours=23, minutes=59) < fire_at - before < timedelta(hours=24, minutes=1)


async def test_a_notice_that_cannot_be_posted_still_schedules_deletion(tmp_path):
    channel = _channel(OLD_CHANNEL)
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
    svc = _service(existing=_wizard())
    guild = _guild(old_channel=channel)
    guild.get_member = MagicMock(return_value=None)

    await svc._trigger_channel_hold(DRIVER, guild, "ended")

    svc._scheduler._scheduler.add_job.assert_called_once()


async def test_no_wizard_means_no_hold(tmp_path):
    svc = _service(existing=None)
    guild = _guild()

    await svc._trigger_channel_hold(DRIVER, guild, "ended")

    svc._scheduler._scheduler.add_job.assert_not_called()


async def test_the_scheduled_deletion_removes_channel_and_wizard(tmp_path):
    channel = _channel(OLD_CHANNEL)
    svc = _service(existing=_wizard())
    svc._bot.get_guild = MagicMock(return_value=_guild(old_channel=channel))

    await svc._execute_channel_delete(DRIVER)

    channel.delete.assert_awaited_once()
    svc._bot.signup_module_service.delete_wizard.assert_awaited_once_with(DRIVER)


async def test_a_channel_that_will_not_delete_still_clears_the_wizard(tmp_path):
    channel = _channel(OLD_CHANNEL)
    channel.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))
    svc = _service(existing=_wizard())
    svc._bot.get_guild = MagicMock(return_value=_guild(old_channel=channel))

    await svc._execute_channel_delete(DRIVER)

    svc._bot.signup_module_service.delete_wizard.assert_awaited_once()


async def test_a_guild_the_bot_has_left_still_clears_the_wizard(tmp_path):
    svc = _service(existing=_wizard())
    svc._bot.get_guild = MagicMock(return_value=None)

    await svc._execute_channel_delete(DRIVER)

    svc._bot.signup_module_service.delete_wizard.assert_awaited_once()
