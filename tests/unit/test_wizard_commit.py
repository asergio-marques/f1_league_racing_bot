"""`commit_wizard` — turning a driver's answers into the league's signup record.

Issue #208. This is the moment the wizard's draft becomes the thing a manager reviews and a
season is seeded from, and it was unexecuted.

**Every answer must survive the crossing, and an unanswered one must arrive as its empty
form.** The draft is a loose dictionary and the record is a dataclass with a field per
question, so the mapping between them is written out by hand — twelve lines, each of which
could name the wrong key without any other test noticing. `test_every_answer_reaches_the_record`
drives a full draft through and reads all of it back. Its counterpart,
`test_an_unanswered_question_arrives_as_its_empty_form`, covers a draft missing every optional
key: the list-valued fields must arrive as `[]` and the dictionary as `{}`, never as `None`,
because everything downstream iterates them.

**The draft is cleared and the wizard parked.** Leaving the answers behind would let a later
advance commit them a second time, and the league would see two signups for one driver.

**The driver loses write permission at this point**, because the channel now holds an admin
review panel rather than a question. A driver who could still type would be answering nothing.

**A correction is distinguished from a first submission only in the log.** The record and the
transition are identical either way; what differs is what the league reads in its log, which
is how a manager tells "this driver has just signed up" from "this driver has fixed the thing
I asked about". The distinction is drawn from the driver's *prior* state, read before anything
is written — read afterwards it would always say the same thing.
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
CHANNEL_ID = 99

#: A complete set of answers, as the wizard would leave them.
FULL_DRAFT = {
    "discord_username": "lewis",
    "server_display_name": "Lewis Hamilton",
    "nationality": "British",
    "platform": "Steam",
    "platform_id": "lh44",
    "availability_slot_ids": ["Mon_19_00", "Fri_21_00"],
    "driver_type": "Full-Time Driver",
    "preferred_teams": ["Alpha", "Beta"],
    "preferred_teammate": "George",
    "lap_times": {"1": "1:23.456"},
    "notes": "See you there",
}


def _wizard(draft: dict | None = None):
    from models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=WizardState.COLLECTING_NOTES,
        signup_channel_id=CHANNEL_ID,
        config_snapshot=ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=["1"],
            slots=[],
        ),
        draft_answers=dict(FULL_DRAFT if draft is None else draft),
        current_lap_track_index=1,
        last_activity_at=None,
    )


@pytest.fixture
def committer():
    from services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    saved: list = []

    svc._get_track_name_map = AsyncMock(return_value={"1": "Silverstone"})  # type: ignore[method-assign]
    svc._format_review_panel = MagicMock(return_value="panel")  # type: ignore[method-assign]
    svc._revoke_driver_write = AsyncMock(return_value=None)  # type: ignore[method-assign]
    svc._cancel_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]

    async def _save_record(record):
        saved.append(record)

    signup_svc = MagicMock()
    signup_svc.get_wizard = AsyncMock(return_value=_wizard())
    signup_svc.save_wizard = AsyncMock(return_value=None)
    signup_svc.save_record = AsyncMock(side_effect=_save_record)
    signup_svc.get_record = AsyncMock(return_value=SimpleNamespace(id=9))

    driver_service = MagicMock()
    driver_service.get_profile = AsyncMock(return_value=None)
    driver_service.transition = AsyncMock(return_value=None)

    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.driver_service = driver_service
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot

    svc._output_router = MagicMock()
    svc._output_router.post_log = AsyncMock(return_value=None)

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)
    member = MagicMock()
    member.display_name = "Lewis Hamilton"
    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=channel)
    guild.get_member = MagicMock(return_value=member)

    return SimpleNamespace(
        svc=svc,
        saved=saved,
        guild=guild,
        channel=channel,
        signup_svc=signup_svc,
        driver_service=driver_service,
    )


async def _commit(ctx):
    await ctx.svc.commit_wizard(SERVER_ID, DRIVER_ID, ctx.guild)


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


async def test_every_answer_reaches_the_record(committer):
    """Twelve hand-written field assignments, any of which could name the wrong key."""
    await _commit(committer)

    record = committer.saved[0]
    assert record.discord_username == "lewis"
    assert record.server_display_name == "Lewis Hamilton"
    assert record.nationality == "British"
    assert record.platform == "Steam"
    assert record.platform_id == "lh44"
    assert record.availability_slot_ids == ["Mon_19_00", "Fri_21_00"]
    assert record.driver_type == "Full-Time Driver"
    assert record.preferred_teams == ["Alpha", "Beta"]
    assert record.preferred_teammate == "George"
    assert record.lap_times == {"1": "1:23.456"}
    assert record.notes == "See you there"
    assert record.signup_channel_id == CHANNEL_ID


async def test_an_unanswered_question_arrives_as_its_empty_form(committer):
    """A league requiring no nationality, no tracks and no teams leaves those keys absent.
    The collections must arrive empty rather than `None` — everything downstream iterates
    them, and a `None` would raise where it is read rather than where it was written."""
    committer.signup_svc.get_wizard = AsyncMock(return_value=_wizard({}))

    await _commit(committer)

    record = committer.saved[0]
    assert record.availability_slot_ids == []
    assert record.preferred_teams == []
    assert record.lap_times == {}
    assert record.nationality is None
    assert record.notes is None


async def test_a_committed_signup_awaits_admin_approval(committer):
    from models.driver_profile import DriverState

    await _commit(committer)

    committer.driver_service.transition.assert_awaited_once()
    assert committer.driver_service.transition.await_args.args[2] == (
        DriverState.PENDING_ADMIN_APPROVAL
    )


async def test_a_driver_with_no_wizard_commits_nothing(committer):
    committer.signup_svc.get_wizard = AsyncMock(return_value=None)

    await _commit(committer)

    assert committer.saved == []
    committer.driver_service.transition.assert_not_awaited()


# ---------------------------------------------------------------------------
# Parking the wizard
# ---------------------------------------------------------------------------


async def test_the_wizard_is_parked_and_its_draft_cleared(committer):
    """Left populated, a later advance would commit the same answers again and the league
    would see two signups for one driver."""
    from models.signup_module import WizardState

    wizard = _wizard()
    committer.signup_svc.get_wizard = AsyncMock(return_value=wizard)

    await _commit(committer)

    assert wizard.wizard_state == WizardState.UNENGAGED
    assert wizard.draft_answers == {}
    committer.signup_svc.save_wizard.assert_awaited_once()


async def test_the_record_is_saved_before_the_draft_is_cleared(committer):
    """The record is built from the draft, so clearing first would save an empty one."""
    await _commit(committer)

    assert committer.saved[0].platform == "Steam"


async def test_the_driver_loses_write_permission(committer):
    """The channel now holds a review panel rather than a question; a driver who could
    still type would be answering nothing."""
    await _commit(committer)

    committer.svc._revoke_driver_write.assert_awaited_once()


async def test_the_inactivity_timeout_is_cancelled(committer):
    """The driver is waiting on a manager now, and cannot be timed out for it."""
    await _commit(committer)

    committer.svc._cancel_inactivity_job.assert_awaited_once()


# ---------------------------------------------------------------------------
# The review panel
# ---------------------------------------------------------------------------


async def test_the_review_panel_is_posted_to_the_signup_channel(committer):
    await _commit(committer)

    committer.channel.send.assert_awaited_once()
    assert committer.channel.send.await_args.args[0] == "panel"
    assert committer.channel.send.await_args.kwargs["view"] is not None


async def test_a_missing_channel_still_commits_the_record(committer):
    """A channel deleted by hand must not cost the driver their signup."""
    committer.guild.get_channel = MagicMock(return_value=None)

    await _commit(committer)

    assert committer.saved[0].platform == "Steam"
    committer.channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Correction versus first submission
# ---------------------------------------------------------------------------


async def test_a_first_submission_is_logged_as_submitted(committer):
    await _commit(committer)

    assert "Submitted" in committer.svc._output_router.post_log.await_args.args[1]


async def test_a_correction_is_logged_as_a_correction(committer):
    """How a manager tells "this driver has just signed up" from "this driver has fixed
    the thing I asked about"."""
    from models.driver_profile import DriverState

    committer.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.PENDING_DRIVER_CORRECTION)
    )

    await _commit(committer)

    assert "Correction submitted" in committer.svc._output_router.post_log.await_args.args[1]


async def test_a_correction_amends_the_signup_it_was_asked_of(committer):
    """Issue #220: signups are kept, never overwritten, so a correction must name the record
    it amends or it would be stored as a second signup."""
    from models.driver_profile import DriverState

    committer.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.PENDING_DRIVER_CORRECTION)
    )

    await _commit(committer)

    assert committer.saved[0].id == 9


async def test_a_first_submission_is_a_new_signup(committer):
    await _commit(committer)

    assert committer.saved[0].id == -1


async def test_the_prior_state_is_read_before_anything_is_written(committer):
    """Read after the transition it would always say `PENDING_ADMIN_APPROVAL`, and every
    submission would log as a first one."""
    from models.driver_profile import DriverState

    committer.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.PENDING_DRIVER_CORRECTION)
    )

    await _commit(committer)

    committer.driver_service.get_profile.assert_awaited_once()
    assert "Correction submitted" in committer.svc._output_router.post_log.await_args.args[1]


async def test_a_driver_who_has_left_is_logged_by_id(committer):
    """`get_member` gives nothing for someone who has left, and the log must still name
    somebody — the raw id is worse than a name but far better than "None"."""
    committer.guild.get_member = MagicMock(return_value=None)

    await _commit(committer)

    assert DRIVER_ID in committer.svc._output_router.post_log.await_args.args[1]
