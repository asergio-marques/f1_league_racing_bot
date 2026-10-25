"""A driver's answers in their signup channel on the change queue (#439, slice 6b, C81–C83).

Today a typed answer is judged, saved and followed by the next question at once, off the queue,
by the wizard service, and a wizard button does the same from its press. On the queue a typed
answer is a turn on the lane of the driver's signup channel: the signup cog marks it ⏳ at once
and asks `signup.answer`, whose jobs save the answer (or refuse it, recorded), post the next
question (granting or revoking typing in the same job) and clear the mark. A message typed while
an earlier answer still waits is turned away at once, with a note, and deleted. A wizard button
asks the same change with the press, acknowledged privately and updated. The last answer saves the
complete draft on the lane and hands `signup.submit` to the league, which saves the signup and
asks back `signup.panel` on the lane, posting the review panel (decision 22, Q-B). A driver's
turns write only their wizard's row (R3).

The league: `setup_league` at Signups with the window open. Kimi (105) is part-way through
signing up (Pending Signup Completion), his signup channel (6405) standing and recording what it
is sent, his wizard asking for his nationality unless a test says otherwise.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from tests.support.change_queue import (
    MARK,
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    typed_message,
    updated_reply,
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

#: The changes a driver's answers ask, and those the league runs and asks back for the last.
ANSWER_KIND = "signup.answer"
SUBMIT_KIND = "signup.submit"
PANEL_KIND = "signup.panel"
KIMI_CHANNEL = SIGNUP_CHANNEL_BASE + SIGNING_UP
KIMI_LANE = f"channel:{KIMI_CHANNEL}"
#: Every answer Kimi has given before his notes, his last question.
ANSWERED_TO_NOTES = {
    "discord_username": "kimi",
    "server_display_name": "Kimi",
    "nationality": "British",
    "platform": "Steam",
    "platform_id": "kimi7",
    "availability_slot_ids": [],
    "driver_type": "Full-Time Driver",
    "preferred_teams": [],
    "preferred_teammate": None,
    "lap_times": {},
}


async def _league(tmp_path: Any, *, state: str = "COLLECTING_NATIONALITY",
                  draft: dict[str, Any] | None = None,
                  driver_state: str = "PENDING_SIGNUP_COMPLETION") -> SeasonLeague:
    """`setup_league` at Signups, the window open; Kimi at *driver_state*, his wizard at *state*
    holding *draft*, nationality required, no lap times asked, saved in a save of the test's own
    as a signup job's record saves it; his signup channel standing. Today's wizard handler off
    the queue is a stand-in that does nothing, so that only what the listener asks of the queue
    shows."""
    from leaguebot.signup.models.signup_module import (
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.core.db.database import get_connection
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
                selected_track_ids=[],
                slots=[],
                team_names=["Alpha", "Beta"],
            ),
            draft_answers=dict(draft or {}),
            current_lap_track_index=0,
            last_activity_at=league.clock.now.isoformat(),
        ))
        await db.commit()
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    league.bot.wizard_service.get_wizard_by_channel = AsyncMock(
        side_effect=signups.get_wizard_by_channel
    )
    league.bot.wizard_service.handle_message = AsyncMock(return_value=None)
    return league


def _kimi(league: SeasonLeague) -> Any:
    kimi = league._member(SIGNING_UP)
    kimi.bot = False
    return kimi


async def _type(league: SeasonLeague, content: str) -> Any:
    """Kimi types *content* in his signup channel, and the signup cog hears it. Gives the
    message."""
    from leaguebot.signup.cogs.signup_cog import SignupCog

    message = typed_message(league.channel(KIMI_CHANNEL), _kimi(league), content,
                            created_at=league.clock.now)
    message.guild = league.guild
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    await cog.on_message(message)
    return message


async def _press_platform(league: SeasonLeague, platform: str) -> Any:
    """Kimi presses *platform* on the platform question; gives his press. The view is handed his
    account and the change queue when it is built (A23)."""
    from leaguebot.signup.services.signup_views import PlatformButtonView

    press = member_interaction(league.bot, user=league._member(SIGNING_UP))
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    view = PlatformButtonView(str(SIGNING_UP), league.bot.change_queue)
    [button] = [child for child in view.children if getattr(child, "label", None) == platform]
    await button.callback(press)
    return press


async def _wizard(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows(
        "SELECT * FROM signup_wizard_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return row


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    import json

    return dict(json.loads((await _wizard(league))["draft_answers_json"]))


async def _driver_state(league: SeasonLeague) -> str:
    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return str(row["current_state"])


async def _changes_of(league: SeasonLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


def _texts(league: SeasonLeague) -> list[str]:
    return league.channel(KIMI_CHANNEL).texts


def _panels(league: SeasonLeague) -> list[Any]:
    """The review panels posted in Kimi's channel."""
    return [call for call in league.channel(KIMI_CHANNEL).send.call_args_list
            if type(call.kwargs.get("view")).__name__ == "AdminReviewView"]


def _revoked(league: SeasonLeague) -> bool:
    return any(call.kwargs.get("send_messages") is False
               for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list)


# ---------------------------------------------------------------------------
# A typed answer (C81)
# ---------------------------------------------------------------------------


async def test_a_typed_answer_is_marked_and_saved_with_the_next_question_when_its_turn_comes(
    tmp_path,
):
    """Kimi types "British" at the nationality question. At once his message is marked ⏳,
    nothing is posted in his channel and nothing is saved, and one `signup.answer` change waits
    on the lane of his channel. When the queue runs his nationality is saved, his wizard asks the
    platform question (posted in his channel) and the ⏳ is taken off his message."""
    league = await _league(tmp_path)

    message = await _type(league, "British")

    assert MARK in message.marks
    assert _texts(league) == []
    assert "nationality" not in await _draft(league)
    [change] = await _changes_of(league, ANSWER_KIND)
    assert _lane(change) == KIMI_LANE

    await run_queue(league.bot)

    assert (await _draft(league))["nationality"] == "British"
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_PLATFORM"
    assert any("Step 2 — Platform" in text for text in _texts(league))
    assert MARK not in message.marks


async def test_rearming_the_24_hours_cannot_be_discarded(tmp_path):
    """Kimi types "British" at the nationality question. His answer is saved, but the scheduler
    refuses to arm his 24 hours of inactivity again, so his channel's lane stops at
    `arm_inactivity`. A league admin's Discard on it is refused, saying Kimi's signup would never
    time out if he stopped answering and, while he corrects it, the season's placements could not
    be confirmed until the bot restarts. The lane stays stopped there, his nationality saved and
    the platform question not yet posted. Once the scheduler takes it, a Retry arms his 24 hours
    from then and posts the platform question."""
    league = await _league(tmp_path)
    arm_once = league.bot.scheduler_service.arm_once

    def refusing(name: str, *_args: Any, **_kwargs: Any) -> None:
        if name == "signup_inactivity":
            raise RuntimeError("the job store is unavailable")

    arm_once.side_effect = refusing

    await _type(league, "British")
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path, KIMI_LANE)
    assert stopped is not None and stopped["name"] == "arm_inactivity"

    refused = await discard_job(league.bot, lane=KIMI_LANE)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it <@{SIGNING_UP}>'s signup would never time "
        "out if they stopped answering, and while they correct it the season's placements could "
        "not be confirmed until the bot restarts. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, KIMI_LANE))["id"] == stopped["id"]
    assert (await _draft(league))["nationality"] == "British"
    assert not any("Step 2 — Platform" in text for text in _texts(league))

    arm_once.side_effect = None
    await retry_job(league.bot, lane=KIMI_LANE)

    assert await stopped_job(league.db_path) is None
    assert arm_once.call_args.args == (
        "signup_inactivity", str(SIGNING_UP), league.clock.now + timedelta(hours=24)
    )
    assert any("Step 2 — Platform" in text for text in _texts(league))


async def test_a_typed_answer_while_its_turn_waits_is_turned_away_with_a_note_and_a_line(
    tmp_path,
):
    """Kimi types "British", and before its turn comes types "French". The second message is
    turned away at once: it is deleted, a note in his channel says his earlier message is still
    waiting its turn, and a line in the log channel records the refusal at the nationality step.
    Only one `signup.answer` change is asked; when the queue runs his nationality is British."""
    league = await _league(tmp_path)

    await _type(league, "British")
    second = await _type(league, "French")

    assert second.gone
    assert any("still waiting its turn" in text for text in _texts(league))
    assert "nationality" in league.log().lower()
    assert len(await _changes_of(league, ANSWER_KIND)) == 1

    await run_queue(league.bot)

    assert (await _draft(league))["nationality"] == "British"


async def test_a_typed_answer_refused_is_answered_by_its_job_and_recorded(tmp_path):
    """Kimi types "Klingon" at the nationality question. Nothing is posted at once. When the
    queue runs, the job answers in his channel that the nationality is invalid, the refusal is
    recorded in the log channel at the nationality step, nothing is saved, his wizard still asks
    his nationality, and the ⏳ is taken off his message."""
    league = await _league(tmp_path)

    message = await _type(league, "Klingon")

    assert _texts(league) == []

    await run_queue(league.bot)

    assert any(text.startswith("❌ Invalid nationality") for text in _texts(league))
    assert "nationality" in league.log().lower()
    assert "nationality" not in await _draft(league)
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_NATIONALITY"
    assert MARK not in message.marks


async def test_a_typed_answer_to_a_question_the_wizard_has_moved_past_is_refused_naming_the_step(
    tmp_path,
):
    """Kimi types "British" at the nationality question; before its turn comes his wizard has
    moved on to the platform ID question. When the queue runs the answer is refused: nothing is
    saved, his wizard still asks his platform ID, and a line in the log channel names the
    nationality step the answer was typed at."""
    league = await _league(tmp_path)

    await _type(league, "British")
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_PLATFORM_ID' "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await run_queue(league.bot)

    assert "nationality" not in await _draft(league)
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_PLATFORM_ID"
    assert "nationality" in league.log().lower()


async def test_a_refused_next_question_stops_the_queue_with_the_answer_saved(tmp_path):
    """Kimi types "British", and Discord refuses every post in his channel. When the queue runs
    his nationality is saved and his wizard moves to the platform question, but the turns in his
    channel stop at `show_next`; the ⏳ stays on his message, and the league lane is not
    stopped."""
    league = await _league(tmp_path)
    league.channel(KIMI_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    message = await _type(league, "British")
    await run_queue(league.bot)

    assert (await _draft(league))["nationality"] == "British"
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_PLATFORM"
    job = await stopped_job(league.db_path, KIMI_LANE)
    assert job is not None and job["name"] == "show_next"
    assert await stopped_job(league.db_path, "league") is None
    assert MARK in message.marks


async def test_a_button_only_step_revokes_typing_in_the_job_that_posts_it(tmp_path):
    """Kimi types "British"; the next question, his platform, is answered by buttons alone. At
    once his typing is left as it is. When the queue runs, the `show_next` job revokes his typing
    in his channel and posts the platform question; no job of its own touches his
    permissions."""
    league = await _league(tmp_path)

    await _type(league, "British")

    assert not _revoked(league)

    await run_queue(league.bot)

    assert _revoked(league)
    assert any("Step 2 — Platform" in text for text in _texts(league))
    [change] = await _changes_of(league, ANSWER_KIND)
    names = [job["name"] for job in await step_rows(league.db_path, change["id"])]
    assert "show_next" in names
    assert not any("permission" in name or "typing" in name for name in names)


async def test_an_answer_is_taken_while_the_league_is_stopped(tmp_path):
    """The league lane is stopped at a job Discord refuses. Kimi types "British": his answer runs
    on his own lane regardless. His nationality is saved and the platform question posted, and
    the league lane is still stopped."""
    from tests.support.review_league import block_queue

    league = await _league(tmp_path)
    await block_queue(league, lane="league")

    await _type(league, "British")
    await run_queue(league.bot)

    assert (await _draft(league))["nationality"] == "British"
    assert any("Step 2 — Platform" in text for text in _texts(league))
    assert await stopped_job(league.db_path, "league") is not None


async def test_a_driver_s_answer_writes_only_the_wizard_row(tmp_path):
    """Kimi types "British" and the queue runs. His wizard's row has changed; his driver profile
    and the signup records are exactly as they were (decision 22: every change of a driver's
    status is the league's)."""
    league = await _league(tmp_path)
    profiles = await league.rows("SELECT * FROM driver_profiles ORDER BY id")
    records = await league.rows("SELECT * FROM signup_records ORDER BY id")
    before = await _wizard(league)

    await _type(league, "British")
    await run_queue(league.bot)

    assert await _wizard(league) != before
    assert await league.rows("SELECT * FROM driver_profiles ORDER BY id") == profiles
    assert await league.rows("SELECT * FROM signup_records ORDER BY id") == records


# ---------------------------------------------------------------------------
# A wizard button (C82)
# ---------------------------------------------------------------------------


async def test_a_wizard_button_answer_is_a_turn_acknowledged_privately_and_updated(tmp_path):
    """Kimi's wizard asks his platform, and he presses Steam. At once he is answered privately
    that it is under way, and nothing is saved. When the queue runs his platform is saved as
    Steam, his wizard asks his platform ID (posted in his channel), and his private answer is
    updated with the outcome."""
    league = await _league(tmp_path, state="COLLECTING_PLATFORM", draft={"nationality": "British"})

    press = await _press_platform(league, "Steam")

    assert acknowledgement(press).startswith("⏳")
    assert "platform" not in await _draft(league)

    await run_queue(league.bot)

    assert (await _draft(league))["platform"] == "Steam"
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_PLATFORM_ID"
    assert any("Step 3 — Platform ID" in text for text in _texts(league))
    assert updated_reply(press)


async def test_a_button_answer_s_line_records_the_answer(tmp_path):
    """Kimi presses Steam at the platform question and the queue runs. A line in the log channel
    records his answer, "Platform: Steam"."""
    league = await _league(tmp_path, state="COLLECTING_PLATFORM", draft={"nationality": "British"})

    await _press_platform(league, "Steam")
    await run_queue(league.bot)

    assert "Platform: Steam" in league.log()


# ---------------------------------------------------------------------------
# The last answer (C83)
# ---------------------------------------------------------------------------


async def test_the_last_answer_commits_the_record_and_posts_the_review_panel_as_jobs(tmp_path):
    """Kimi has answered every question but his notes, and types "Happy to race anywhere". At
    once nothing is saved and no panel posted. When the queue runs his signup is saved with
    every answer and his notes, he is Pending Admin Approval, his wizard is cleared, the review
    panel is posted in his channel, and the ⏳ is taken off his message."""
    league = await _league(tmp_path, state="COLLECTING_NOTES", draft=ANSWERED_TO_NOTES)

    message = await _type(league, "Happy to race anywhere")

    assert await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    ) == []
    assert _panels(league) == []

    await run_queue(league.bot)

    [record] = await league.rows(
        "SELECT * FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert (record["nationality"], record["notes"]) == ("British", "Happy to race anywhere")
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    wizard = await _wizard(league)
    assert wizard["wizard_state"] == "UNENGAGED"
    assert await _draft(league) == {}
    assert len(_panels(league)) == 1
    assert MARK not in message.marks


async def test_the_last_answer_hands_the_submission_to_the_league_and_the_panel_comes_back_on_the_lane(
    tmp_path,
):
    """Kimi types his notes, his last answer, and the queue runs. His `signup.answer` turn runs
    on his channel's lane; it hands a `signup.submit` change, asked after it, to the league lane;
    that asks back a `signup.panel` change, asked after it, on his lane, which posts the panel.
    All three are done, and his ⏳ came off only once the panel was up (R10)."""
    league = await _league(tmp_path, state="COLLECTING_NOTES", draft=ANSWERED_TO_NOTES)

    await _type(league, "Happy to race anywhere")
    await run_queue(league.bot)

    [answer] = await _changes_of(league, ANSWER_KIND)
    [submit] = await _changes_of(league, SUBMIT_KIND)
    [panel] = await _changes_of(league, PANEL_KIND)
    assert (_lane(answer), _lane(submit), _lane(panel)) == (KIMI_LANE, "league", KIMI_LANE)
    assert answer["id"] < submit["id"] < panel["id"]
    assert {answer["state"], submit["state"], panel["state"]} == {"DONE"}
    assert len(_panels(league)) == 1
    panel_jobs = [job["name"] for job in await step_rows(league.db_path, panel["id"])]
    assert panel_jobs[-1] == "clear_mark"


async def test_a_corrected_answer_commits_the_correction_and_posts_a_fresh_panel(tmp_path):
    """A league manager asked Kimi to correct his platform ID: he is Pending Driver Correction,
    his signup already saved with "kimi7", his wizard asking the platform ID again. He types
    "kimi_new" and the queue runs. His same signup record now holds "kimi_new", he is Pending
    Admin Approval again, and a fresh review panel is posted in his channel."""
    draft = dict(ANSWERED_TO_NOTES, notes="Happy to race anywhere", _is_correction=True)
    league = await _league(tmp_path, state="COLLECTING_PLATFORM_ID", draft=draft,
                           driver_state="PENDING_DRIVER_CORRECTION")
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, nationality, platform, "
        "platform_id, driver_type, notes, signup_channel_id) "
        "VALUES (?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', "
        "'Happy to race anywhere', ?)",
        SEASON_ID, str(SIGNING_UP), KIMI_CHANNEL,
    )
    [before] = await league.rows(
        "SELECT id FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )

    await _type(league, "kimi_new")
    await run_queue(league.bot)

    [after] = await league.rows(
        "SELECT id, platform_id FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert (after["id"], after["platform_id"]) == (before["id"], "kimi_new")
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert len(_panels(league)) == 1


#: For each step, what Kimi types, and the refusal and step name the wizard gives him today.
_REFUSED = [
    pytest.param("COLLECTING_NATIONALITY", "Atlantis", "Nationality",
                 "❌ Invalid nationality. Please enter your full nationality (e.g. `British`) "
                 "or country name (e.g. `United Kingdom`), or type `other`.",
                 id="nationality"),
    pytest.param("COLLECTING_PLATFORM", "Commodore", "Platform",
                 "❌ Invalid platform. Please choose one of: Steam / EA / Xbox / PlayStation",
                 id="platform"),
    pytest.param("COLLECTING_PLATFORM_ID", "   ", "Platform ID",
                 "❌ Platform ID cannot be empty.", id="platform id"),
    pytest.param("COLLECTING_AVAILABILITY", "evenings", "Availability",
                 "❌ Please enter slot IDs as numbers (e.g. `1 3`).", id="availability"),
    pytest.param("COLLECTING_DRIVER_TYPE", "Part-Time", "Driver Type",
                 "❌ Please choose one of: Full-Time Driver / Reserve Driver", id="driver type"),
    pytest.param("COLLECTING_PREFERRED_TEAMS", "Alpha, Beta, Gamma, Delta", "Preferred Teams",
                 "❌ Please select up to 3 teams.", id="preferred teams"),
    pytest.param("COLLECTING_LAP_TIME", "quick", "Lap Times",
                 "❌ Invalid Time Trial time. Use format `M:ss.mmm` (e.g. `1:23.456`).",
                 id="lap time"),
    pytest.param("COLLECTING_NOTES", "x" * 51, "Notes",
                 "❌ Notes must be 50 characters or fewer.", id="notes"),
]


@pytest.mark.parametrize(("state", "typed", "step", "refusal"), _REFUSED)
def test_judge_answer_holds_each_step_s_rules(state, typed, step, refusal):
    """Kimi's wizard is at *state*: Time Trial times, one track to time, no screenshot asked,
    nationality required, no availability slots. He types *typed*. Judged on its own, without a
    database or Discord, the answer is refused with today's reply for that step, and the refusal
    names the step its log line names."""
    from types import SimpleNamespace

    from leaguebot.signup.models.signup_module import (
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.signup.services.wizard_changes import Refused, judge_answer

    wizard = SignupWizardRecord(
        id=1,
        discord_user_id=str(SIGNING_UP),
        wizard_state=WizardState(state),
        signup_channel_id=KIMI_CHANNEL,
        config_snapshot=ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=["01"],
            slots=[],
            team_names=["Alpha", "Beta"],
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at="2026-10-13T12:00:00+00:00",
    )

    judged = judge_answer(wizard, SimpleNamespace(content=typed, attachments=[]))

    assert isinstance(judged, Refused)
    assert (judged.step, judged.reply) == (step, refusal)
