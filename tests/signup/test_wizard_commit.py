"""Turning a driver's answers into the league's signup record, as the last answer's turn does.

Issue #208. This is the moment the wizard's draft becomes the thing a manager reviews and a
season is seeded from.

On the change queue (#439, slice 6b, C83) the last answer is a `signup.answer` turn on the lane
of the driver's signup channel: it saves the complete draft there and hands `signup.submit` to
the league, which saves the signup record, moves the driver to Pending Admin Approval and clears
the draft, then asks back `signup.panel` on the driver's lane, posting the review panel. So each
test here gives the last answer as the driver does and reads what the queue leaves once it has
run.

**Every answer must survive the crossing, and an unanswered one must arrive as its empty
form.** The draft is a loose dictionary and the record has a field per question, so the mapping
between them is written out by hand. `test_every_answer_reaches_the_record` drives a full draft
through and reads all of it back. `test_an_unanswered_question_arrives_as_its_empty_form`
covers a draft missing every optional key: the list-valued fields must arrive as `[]` and the
dictionary as `{}`, never as `None`, because everything downstream iterates them.

**The draft is cleared and the wizard parked.** Leaving the answers behind would let a later
answer commit them a second time, and the league would see two signups for one driver.

**The driver loses write permission at this point**, because the channel now holds an admin
review panel rather than a question.

**A correction is distinguished from a first submission in the record it amends and in the
log**, drawn from the driver's state before anything is written.

The league: `setup_league` at Signups with the window open. Kimi (105) is part-way through
signing up (Pending Signup Completion), his signup channel (6405) standing and recording what it
is sent; his wizard at the step a test names, holding the answers it names.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    refuse_the_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    SEASON_ID,
    SIGNING_UP,
    SIGNUP_CHANNEL_BASE,
    SeasonLeague,
    setup_league,
    signing_up,
)

KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
INACTIVITY_TIMER = "signup_inactivity"

#: Every answer but the notes, as the wizard leaves them before its last question.
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
}


async def _league(tmp_path: Any, *, state: str = "COLLECTING_NOTES",
                  draft: dict[str, Any] | None = None,
                  driver_state: str = "PENDING_SIGNUP_COMPLETION") -> SeasonLeague:
    """`setup_league` at Signups, the window open; Kimi at *driver_state*, his wizard at *state*
    holding *draft* (`FULL_DRAFT` unless given), one track to time, saved in a save of the
    test's own; his signup channel standing. Today's wizard handlers off the queue are stand-ins
    that do nothing, so that only what the answer asks of the queue shows."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.signup.models.signup_module import (
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await signing_up(league, SIGNING_UP, state=driver_state)
    signups = SignupModuleService(league.db_path)
    async with get_connection(league.db_path) as db:
        await signups.save_wizard_on(db, SignupWizardRecord(
            id=-1,
            discord_user_id=str(SIGNING_UP),
            wizard_state=WizardState(state),
            signup_channel_id=KIMI_CHANNEL,
            config_snapshot=ConfigSnapshot(
                nationality_required=True,
                time_type="TIME_TRIAL",
                time_image_required=False,
                selected_track_ids=["1"],
                slots=[],
                team_names=["Alpha", "Beta"],
            ),
            draft_answers=dict(FULL_DRAFT if draft is None else draft),
            current_lap_track_index=1,
            last_activity_at=league.clock.now.isoformat(),
        ))
        await db.commit()
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    wizards = league.bot.wizard_service
    wizards.get_wizard_by_channel = AsyncMock(side_effect=signups.get_wizard_by_channel)
    for handler in ("handle_message", "handle_platform_button", "handle_no_notes"):
        setattr(wizards, handler, AsyncMock(return_value=None))
    return league


async def _correcting(tmp_path: Any, state: str, draft: dict[str, Any]) -> SeasonLeague:
    """A league manager asked Kimi to correct an answer: he is Pending Driver Correction, his
    signup already saved (platform Steam, ID kimi7), his wizard at *state* holding *draft*, which
    marks it as a correction."""
    league = await _league(tmp_path, state=state, draft=dict(draft, _is_correction=True),
                           driver_state="PENDING_DRIVER_CORRECTION")
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, nationality, platform, "
        "platform_id, driver_type, notes, signup_channel_id) "
        "VALUES (?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', NULL, ?)",
        SEASON_ID, str(SIGNING_UP), KIMI_CHANNEL,
    )
    return league


async def _type(league: SeasonLeague, content: str) -> None:
    """Kimi types *content* in his signup channel, and the signup cog hears it."""
    from leaguebot.signup.cogs.signup_cog import SignupCog

    kimi = league._member(SIGNING_UP)
    kimi.bot = False
    message = typed_message(league.channel(KIMI_CHANNEL), kimi, content,
                            created_at=league.clock.now)
    message.guild = league.guild
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    await cog.on_message(message)


async def _press(league: SeasonLeague, view_class: str, label: str) -> None:
    """Kimi presses *label* on the step's buttons in his channel, the view handed his account
    and the change queue when it is built (A23)."""
    from leaguebot.signup.services import signup_views

    press = member_interaction(league.bot, user=league._member(SIGNING_UP))
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    view = getattr(signup_views, view_class)(str(SIGNING_UP), league.bot.change_queue)
    [button] = [child for child in view.children if getattr(child, "label", None) == label]
    await button.callback(press)


async def _record(league: SeasonLeague) -> Any:
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    return await SignupModuleService(league.db_path).get_record(str(SIGNING_UP))


async def _records(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT id FROM signup_records WHERE discord_user_id = ? ORDER BY id", str(SIGNING_UP)
    )


async def _driver_state(league: SeasonLeague) -> str:
    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return str(row["current_state"])


def _panels(league: SeasonLeague) -> list[Any]:
    """The review panels posted in Kimi's channel."""
    return [call for call in league.channel(KIMI_CHANNEL).send.call_args_list
            if type(call.kwargs.get("view")).__name__ == "AdminReviewView"]


def _signup_lines(league: SeasonLeague) -> list[str]:
    return [line for line in league.log().splitlines() if "| Signup |" in line]


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


async def test_every_answer_reaches_the_record(tmp_path):
    """Kimi has answered everything but his notes. He types "See you there"; once the queue runs
    his signup record holds every answer, his notes and his signup channel."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    record = await _record(league)
    assert record is not None
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
    assert record.signup_channel_id == KIMI_CHANNEL


async def test_an_unanswered_question_arrives_as_its_empty_form(tmp_path):
    """Kimi's wizard holds no answer at all when he types "no notes" at the last question. Once
    the queue runs his record's collections arrive empty rather than `None` — everything
    downstream iterates them — and his nationality and notes are unset."""
    league = await _league(tmp_path, draft={})

    await _type(league, "no notes")
    await run_queue(league.bot)

    record = await _record(league)
    assert record is not None
    assert record.availability_slot_ids == []
    assert record.preferred_teams == []
    assert record.lap_times == {}
    assert record.nationality is None
    assert record.notes is None


async def test_a_committed_signup_awaits_admin_approval(tmp_path):
    """Kimi types his notes; once the queue runs he is Pending Admin Approval."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


async def test_a_driver_with_no_wizard_commits_nothing(tmp_path):
    """Kimi types his notes, and before the turn comes his wizard is gone. Once the queue runs
    no signup is saved and he is still Pending Signup Completion: the turn is no longer due."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await league.write(
        "DELETE FROM signup_wizard_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    await run_queue(league.bot)

    assert [row["kind"] for row in await change_rows(league.db_path)] == ["signup.answer"]
    assert await _records(league) == []
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"


# ---------------------------------------------------------------------------
# Parking the wizard
# ---------------------------------------------------------------------------


async def test_the_wizard_is_parked_and_its_draft_cleared(tmp_path):
    """Left populated, a later answer would commit the same answers again and the league would
    see two signups for one driver. Kimi types his notes; once the queue runs his wizard is
    Unengaged and holds no answers."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    [wizard] = await league.rows(
        "SELECT wizard_state, draft_answers_json FROM signup_wizard_records "
        "WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert wizard["wizard_state"] == "UNENGAGED"
    assert json.loads(wizard["draft_answers_json"] or "{}") == {}


async def test_the_record_is_saved_before_the_draft_is_cleared(tmp_path):
    """The record is built from the draft, so clearing first would save an empty one. Once the
    queue runs Kimi's record holds his platform."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    record = await _record(league)
    assert record is not None and record.platform == "Steam"


async def test_the_driver_loses_write_permission(tmp_path):
    """The channel now holds a review panel rather than a question. Once the queue runs Kimi's
    typing in his channel is revoked."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert any(call.kwargs.get("send_messages") is False
               for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list)


async def test_the_inactivity_timeout_is_cancelled(tmp_path):
    """Kimi is waiting on a manager now, and cannot be timed out for it. Once the queue runs his
    24-hour inactivity timer is disarmed."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert any(call.args[:2] == (INACTIVITY_TIMER, str(SIGNING_UP))
               for call in league.bot.scheduler_service.disarm.call_args_list)


# ---------------------------------------------------------------------------
# The review panel
# ---------------------------------------------------------------------------


async def test_the_review_panel_is_posted_to_the_signup_channel(tmp_path):
    """Once the queue runs one review panel, with its buttons, is posted in Kimi's channel."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert len(_panels(league)) == 1


async def test_the_review_panel_notifies_no_group(tmp_path):
    """It quotes the driver's own answers, and the bot's permission to mention everybody must
    not carry a group mention in one of them (#362). The panel posted allows users alone."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    [panel] = _panels(league)
    allowed = panel.kwargs["allowed_mentions"]
    assert (allowed.everyone, allowed.roles, allowed.users) == (False, False, True)


async def test_the_review_panel_s_post_cannot_be_discarded(tmp_path):
    """Kimi types his notes and his signup is saved, but Discord refuses the review panel in his
    channel, so the queue stops at `post_panel` on his channel's lane. A league admin's Discard on
    it is refused, saying Kimi would wait for approval with no review panel and the season's
    placements could not be confirmed. The lane stays stopped there and no panel is posted. Once
    Discord takes posts again, a Retry posts the one review panel."""
    league = await _league(tmp_path)
    lane = f"channel:{KIMI_CHANNEL}"
    league.channel(KIMI_CHANNEL).fail_when = lambda _content, kwargs: kwargs.get("view") is not None

    await _type(league, "See you there")
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, lane)
    assert stopped is not None and stopped["name"] == "post_panel"

    refused = await discard_job(league.bot, lane=lane)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}> would wait for approval with "
        "no review panel, and the season's placements could not be confirmed. Fix what stopped "
        "it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, lane))["id"] == stopped["id"]
    posted = league.channel(KIMI_CHANNEL).messages.values
    assert not [m for m in posted() if type(m.view).__name__ == "AdminReviewView"]

    league.channel(KIMI_CHANNEL).fail_when = None
    await retry_job(league.bot, lane=lane)

    assert await stopped_job(league.db_path) is None
    assert len([m for m in posted() if type(m.view).__name__ == "AdminReviewView"]) == 1


#: The ending of a signup whose channel is gone when its panel is due is not yet built (#439,
#: slice 8, F1).
CHANNEL_GONE_UNBUILT = "#439: a signup whose channel is gone when its panel is due is not yet ended"
#: The panel's job does not yet look past the cache for its channel (#439, slice 8, F1).
PANEL_LOOKUP_UNBUILT = "#439: a review panel whose channel the cache does not hold is passed over"
#: The line the ending writes in the log channel, naming Kimi and his gone channel.
CHANNEL_GONE_LINE = (
    f"Kimi (`<@{SIGNING_UP}>`) | Signup | Ended\n  channel: <#{KIMI_CHANNEL}>\n  Their signup "
    "channel was gone when their review panel was due, so their signup ended. They may press "
    "Sign Up again while signups are open."
)


def _admin_panels(chan: Any) -> list[Any]:
    """The review panels standing in *chan*."""
    return [m for m in chan.messages.values() if type(m.view).__name__ == "AdminReviewView"]


def _delete_channel(league: SeasonLeague) -> Any:
    """Kimi's channel 6405 is deleted from the server: neither the cache nor Discord's lookup
    finds it, and taking the ⏳ off a message in it finds the message gone. Gives the channel."""
    gone = league.channels.pop(KIMI_CHANNEL)
    message = MagicMock()
    message.remove_reaction = AsyncMock(side_effect=http_error(discord.NotFound, status=404))
    league.bot.get_partial_messageable.return_value.get_partial_message.return_value = message
    return gone


def _out_of_cache(league: SeasonLeague, *, refusal: BaseException | None = None) -> None:
    """Kimi's channel 6405 is missing from the bot's cache of the server; Discord's lookup of it
    finds it standing, or fails with *refusal* where one is given."""
    cached = league.guild.get_channel.side_effect
    fetched = league.guild.fetch_channel.side_effect
    league.guild.get_channel.side_effect = (
        lambda cid: None if int(cid) == KIMI_CHANNEL else cached(cid)
    )

    async def _fetch(cid: int) -> Any:
        if int(cid) == KIMI_CHANNEL and refusal is not None:
            raise refusal
        return await fetched(cid)

    league.guild.fetch_channel.side_effect = _fetch


@pytest.mark.xfail(strict=True, reason=CHANNEL_GONE_UNBUILT)
async def test_a_channel_gone_when_the_panel_is_due_ends_the_signup_once_it_is_saved(tmp_path):
    """A channel deleted by hand must not cost the driver their signup record, nor leave them
    awaiting a review that can never be posted. Kimi types his notes, and his channel 6405 is
    deleted before the turn's panel is posted. Once the queue runs his signup record is saved,
    but his signup has ended: he is Not Signed Up, his wizard Unengaged, and no 24-hour
    inactivity timer is armed for him. Nothing is sent to the gone channel, the queue is not
    stopped, and the ending is one `signup.end` change on the league lane, done, whose single job
    is `end`."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    gone = _delete_channel(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    record = await _record(league)
    assert record is not None and record.platform == "Steam"
    assert await _driver_state(league) == "NOT_SIGNED_UP"
    [wizard] = await league.rows(
        "SELECT wizard_state FROM signup_wizard_records WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    assert wizard["wizard_state"] == "UNENGAGED"
    assert not any(call.args[:2] == (INACTIVITY_TIMER, str(SIGNING_UP))
                   for call in league.bot.scheduler_service.arm_once.call_args_list)
    gone.send.assert_not_awaited()
    [ending] = [row for row in await change_rows(league.db_path) if row["kind"] == "signup.end"]
    assert (ending["state"], ending["lane"]) == ("DONE", "league")
    assert [job["name"] for job in await step_rows(league.db_path, ending["id"])] == ["end"]


@pytest.mark.xfail(strict=True, reason=CHANNEL_GONE_UNBUILT)
async def test_a_signup_ended_for_its_gone_channel_is_logged_naming_the_driver_and_the_channel(
    tmp_path,
):
    """Kimi types his notes, and his channel 6405 is deleted before the turn's panel is posted.
    Once the queue runs the log channel is told once, naming Kimi and the channel, that his
    signup channel was gone when his review panel was due, so his signup ended, and that he may
    press Sign Up again while signups are open."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    _delete_channel(league)
    await run_queue(league.bot)

    assert league.log().count(CHANNEL_GONE_LINE) == 1


@pytest.mark.xfail(strict=True, reason=PANEL_LOOKUP_UNBUILT)
async def test_a_server_out_of_cache_stops_the_panel_rather_than_passing_it_over(tmp_path):
    """Kimi types his notes, and when his panel is due the league's server is missing from the
    bot's cache, so his channel cannot be looked for at all. The queue stops at `post_panel` on
    his channel's lane: his signup is saved, he still awaits approval, no panel is posted and his
    signup is not ended. Once the server is back, a Retry posts the one review panel in his
    channel."""
    league = await _league(tmp_path)
    lane = f"channel:{KIMI_CHANNEL}"

    await _type(league, "See you there")
    league.bot.get_guild.return_value = None
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, lane)
    assert stopped is not None and stopped["name"] == "post_panel"
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert _admin_panels(league.channel(KIMI_CHANNEL)) == []
    assert not [row for row in await change_rows(league.db_path) if row["kind"] == "signup.end"]

    league.bot.get_guild.return_value = league.guild
    await retry_job(league.bot, lane=lane)

    assert await stopped_job(league.db_path) is None
    assert len(_admin_panels(league.channel(KIMI_CHANNEL))) == 1
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


@pytest.mark.xfail(strict=True, reason=PANEL_LOOKUP_UNBUILT)
@pytest.mark.parametrize(
    "refusal",
    [http_error(), http_error(discord.Forbidden, status=403, text="Missing Access")],
    ids=["server error", "forbidden"],
)
async def test_a_channel_the_lookup_cannot_confirm_gone_stops_the_panel(tmp_path, refusal):
    """Kimi types his notes, and when his panel is due his channel 6405 is missing from the bot's
    cache, and Discord's lookup of it fails with a server error, or is forbidden, so the bot
    cannot tell whether it is gone. The queue stops at `post_panel` on his channel's lane: he
    still awaits approval, nothing is posted and his signup is not ended."""
    league = await _league(tmp_path)
    lane = f"channel:{KIMI_CHANNEL}"
    held = league.channel(KIMI_CHANNEL)
    _out_of_cache(league, refusal=refusal)

    await _type(league, "See you there")
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, lane)
    assert stopped is not None and stopped["name"] == "post_panel"
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    held.send.assert_not_awaited()
    assert not [row for row in await change_rows(league.db_path) if row["kind"] == "signup.end"]


@pytest.mark.xfail(strict=True, reason=PANEL_LOOKUP_UNBUILT)
async def test_a_channel_missing_from_the_cache_but_found_by_the_lookup_gets_its_panel(tmp_path):
    """Kimi types his notes, and when his panel is due his channel 6405 is missing from the bot's
    cache, but Discord's lookup finds it standing. Once the queue runs the one review panel is
    posted in it, he awaits approval, his signup is not ended and the queue is not stopped."""
    league = await _league(tmp_path)
    _out_of_cache(league)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_admin_panels(league.channel(KIMI_CHANNEL))) == 1
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert not [row for row in await change_rows(league.db_path) if row["kind"] == "signup.end"]


# ---------------------------------------------------------------------------
# Correction versus first submission
# ---------------------------------------------------------------------------


async def test_a_first_submission_is_logged_as_submitted(tmp_path):
    """Kimi types his notes; once the queue runs the log channel holds a "Submitted" line."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert any(line.endswith("| Signup | Submitted") for line in _signup_lines(league))


async def test_a_correction_is_logged_as_a_correction(tmp_path):
    """How a manager tells "this driver has just signed up" from "this driver has fixed the
    thing I asked about". Kimi was asked to correct his nationality and types "German". Once the
    queue runs the signup lines are one, "Correction submitted", naming what was re-collected by
    its review button's label (owner, 2026-10-01, Gate 3)."""
    league = await _correcting(tmp_path, "COLLECTING_NATIONALITY", FULL_DRAFT)

    await _type(league, "German")
    await run_queue(league.bot)

    lines = _signup_lines(league)
    assert len(lines) == 1, lines
    assert lines[0].endswith("| Signup | Correction submitted: Nationality"), lines[0]


async def test_no_notes_on_the_first_pass_writes_only_submitted(tmp_path):
    """Kimi reaches the notes step on his first pass and presses No Notes. That ends the wizard,
    and the "Submitted" line is the press's one line: No Notes writes no step line of its own."""
    league = await _league(tmp_path)

    await _press(league, "NoNotesButtonView", "No Notes")
    await run_queue(league.bot)

    lines = _signup_lines(league)
    assert len(lines) == 1, lines
    assert lines[0].endswith("| Signup | Submitted"), lines[0]


async def test_a_button_answer_that_ends_a_correction_writes_only_correction_submitted(tmp_path):
    """Kimi was asked to correct his platform and presses EA. That press ends the correction:
    once the queue runs it has written one line, "Correction submitted", and no "Platform: EA"
    beside it."""
    league = await _correcting(tmp_path, "COLLECTING_PLATFORM", FULL_DRAFT)

    await _press(league, "PlatformButtonView", "EA")
    await run_queue(league.bot)

    lines = _signup_lines(league)
    assert len(lines) == 1, lines
    assert "| Signup | Correction submitted" in lines[0], lines[0]
    assert "Platform: EA" not in lines[0]


COMMIT_UNDISCARDABLE = "#439: committing a signup may still be discarded"


@pytest.mark.xfail(strict=True, reason=COMMIT_UNDISCARDABLE)
async def test_a_correction_s_commit_cannot_be_discarded(tmp_path):
    """A league manager asked Kimi to correct his nationality, and he types "German". The save
    that commits his signup on the league lane fails, so the queue stops at `commit`. A league
    admin's Discard on it is refused, saying Kimi's signup would never be submitted and, while he
    corrects it, the season's placements could not be confirmed until the bot restarts. Kimi is
    still Pending Driver Correction, his signup still says British and the queue stays stopped
    there. Once the fault is lifted, a Retry commits it: Kimi awaits approval and his signup
    says German."""
    league = await _correcting(tmp_path, "COLLECTING_NATIONALITY", FULL_DRAFT)
    await refuse_the_job(league.db_path, "signup.submit", "commit", True)

    await _type(league, "German")
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "commit"

    refused = await discard_job(league.bot, lane="league")

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}>'s signup would never be "
        "submitted, and while they correct it the season's placements could not be confirmed "
        "until the bot restarts. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert (await _record(league)).nationality == "British"

    await refuse_the_job(league.db_path, "signup.submit", "commit", False)
    await retry_job(league.bot, lane="league")

    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert (await _record(league)).nationality == "German"


async def test_a_correction_amends_the_signup_it_was_asked_of(tmp_path):
    """Issue #220: signups are kept, never overwritten, so a correction must amend the record it
    was asked of, not be stored as a second signup. Kimi was asked to correct his platform and
    presses EA; once the queue runs his one signup record holds EA, and the fresh review panel
    posted allows users alone."""
    league = await _correcting(tmp_path, "COLLECTING_PLATFORM", FULL_DRAFT)
    before = await _records(league)

    await _press(league, "PlatformButtonView", "EA")
    await run_queue(league.bot)

    assert await _records(league) == before
    record = await _record(league)
    assert record is not None and record.platform == "EA"
    [panel] = _panels(league)
    allowed = panel.kwargs["allowed_mentions"]
    assert (allowed.everyone, allowed.roles, allowed.users) == (False, False, True)


async def test_a_correction_changes_only_the_corrected_answer_and_hands_the_signup_back(tmp_path):
    """A correction changes only what was corrected, and hands the signup straight back to the
    manager who asked for it. Kimi's saved signup is British, Steam, ID kimi7; he was asked to
    correct his platform ID and types "new_tag". Until the queue runs nothing is saved; once it
    has, his one signup holds ID new_tag beside British and Steam unchanged, he is Pending Admin
    Approval again, his wizard is parked with its answers cleared, and one fresh review panel
    is posted in his channel."""
    league = await _correcting(tmp_path, "COLLECTING_PLATFORM_ID", {})

    await _type(league, "new_tag")

    record = await _record(league)
    assert record is not None and record.platform_id == "kimi7"

    await run_queue(league.bot)

    record = await _record(league)
    assert record is not None
    assert (record.platform_id, record.nationality, record.platform) == (
        "new_tag", "British", "Steam"
    )
    assert len(await _records(league)) == 1
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    [wizard] = await league.rows(
        "SELECT wizard_state, draft_answers_json FROM signup_wizard_records "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    assert (wizard["wizard_state"], wizard["draft_answers_json"]) == ("UNENGAGED", "{}")
    assert len(_panels(league)) == 1


async def test_a_first_submission_is_a_new_signup(tmp_path):
    """Kimi has no signup saved yet; once his notes' turn has run he has exactly one."""
    league = await _league(tmp_path)
    assert await _records(league) == []

    await _type(league, "See you there")
    await run_queue(league.bot)

    assert len(await _records(league)) == 1


async def test_the_prior_state_is_read_before_anything_is_written(tmp_path):
    """Read after the move to Pending Admin Approval, Kimi's state would always look like a first
    submission, and his correction would be saved as a second signup. Kimi, Pending Driver
    Correction, types his corrected nationality; once the queue runs he still has one signup,
    now holding it, and is Pending Admin Approval."""
    league = await _correcting(tmp_path, "COLLECTING_NATIONALITY", FULL_DRAFT)

    await _type(league, "German")
    await run_queue(league.bot)

    assert len(await _records(league)) == 1
    record = await _record(league)
    assert record is not None and record.nationality == "German"
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


async def test_a_driver_who_has_left_is_logged_by_id(tmp_path):
    """The log must still name somebody once a driver has left: by mention alone, as every line
    names a member no longer on the server (core specification, "The record of what changed"),
    never by the raw id beside it. Kimi types his notes and leaves the server before the turn
    runs; once it has, the "Submitted" line names him by mention."""
    league = await _league(tmp_path)

    await _type(league, "See you there")
    league.absent.add(SIGNING_UP)
    await run_queue(league.bot)

    assert f"`<@{SIGNING_UP}>` | Signup | Submitted" in _signup_lines(league)  # the log's mentions are in backticks
