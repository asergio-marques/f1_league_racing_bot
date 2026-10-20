"""The order the signup wizard asks its questions in, as each answer's turn moves it on.

Issue #208. This is the wizard's spine: every answer ends by moving the wizard on, which decides
what the driver is asked next, whether they are asked at all, and when the signup is committed.

On the change queue (#439, slice 6b, C81–C83) an answer is a `signup.answer` turn on the lane of
the driver's signup channel: its jobs save the answer, move the wizard on and post the next
question (granting or revoking typing in the same job). So each test here gives the answer as a
driver does — typed in his channel, or pressed on the step's buttons — and reads what the queue
leaves once it has run.

**Three steps are conditional, and each is skipped for a different reason.**

- *Nationality* is asked only when the league requires it, and it is the **first** step, so
  skipping it decides where the wizard starts (pinned on the start, in
  `test_wizard_start_and_prompts.py`).
- *Preferred teams* is skipped for a **Reserve Driver**. A reserve is not placed in a team, so
  asking which team they would like collects an answer nothing can honour — and the driver's
  own earlier answer is what decides it.
- *Lap times* repeat, once per track the league selected, and are skipped entirely when it
  selected none. This is the only step the wizard can stay on, and `current_lap_track_index` is
  what distinguishes "ask again for the next track" from "move on".

**The write permission flips around the button-only steps.** Platform, driver type and
preferred teams are answered by pressing a button, and the driver's ability to type in the
channel is revoked on the way in and restored on the way out. A driver left without write
permission cannot answer the next typed question and the wizard stalls until it times out.

**Correction mode short-circuits the whole thing.** A driver amending one answer after review
must commit that correction, not resume the sequence and be asked every later question again.

The league: `setup_league` at Signups with the window open. Kimi (105) is part-way through
signing up (Pending Signup Completion), his signup channel (6405) standing and recording what it
is sent; his wizard is at the step a test names, three availability slots offered, the teams
Alpha and Beta, and the tracks a test names.
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.support.change_queue import member_interaction, run_queue, stopped_job, typed_message
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
KIMI_LANE = f"channel:{KIMI_CHANNEL}"
INACTIVITY_TIMER = "signup_inactivity"
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

NO_ANSWER_TURN = (
    "#439: a signup answer is saved and the wizard moved on at once off the queue by the wizard "
    "service, with no signup.answer turn on the lane of the driver's signup channel, and its "
    "buttons are not handed the change queue"
)


async def _league(tmp_path: Any, state: str, *, tracks: tuple[str, ...] = (),
                  lap_index: int = 0, nationality_required: bool = False,
                  draft: dict[str, Any] | None = None,
                  driver_state: str = "PENDING_SIGNUP_COMPLETION") -> SeasonLeague:
    """`setup_league` at Signups, the window open; Kimi at *driver_state*, his wizard at *state*
    holding *draft*, asking lap times for *tracks* from *lap_index*, three slots and the teams
    Alpha and Beta offered, saved in a save of the test's own; his signup channel standing.
    Today's wizard handlers off the queue are stand-ins that do nothing, so that only what the
    answer asks of the queue shows."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.signup.models.signup_module import (
        AvailabilitySlot,
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await signing_up(league, SIGNING_UP, state=driver_state)
    slots = [
        AvailabilitySlot(
            id=surrogate,
            slot_id=AvailabilitySlot.make_slot_id(day, time_hhmm),
            slot_sequence_id=ordinal,
            day_of_week=day,
            time_hhmm=time_hhmm,
            display_label=AvailabilitySlot.make_label(day, time_hhmm),
        )
        for surrogate, ordinal, day, time_hhmm in (
            (41, 1, 1, "19:00"), (42, 2, 3, "20:00"), (43, 3, 5, "21:00"),
        )
    ]
    signups = SignupModuleService(league.db_path)
    async with get_connection(league.db_path) as db:
        await signups.save_wizard_on(db, SignupWizardRecord(
            id=-1,
            discord_user_id=str(SIGNING_UP),
            wizard_state=WizardState(state),
            signup_channel_id=KIMI_CHANNEL,
            config_snapshot=ConfigSnapshot(
                nationality_required=nationality_required,
                time_type="TIME_TRIAL",
                time_image_required=False,
                selected_track_ids=list(tracks),
                slots=slots,
                team_names=["Alpha", "Beta"],
            ),
            draft_answers=dict(draft or {}),
            current_lap_track_index=lap_index,
            last_activity_at=(league.clock.now - timedelta(hours=1)).isoformat(),
        ))
        await db.commit()
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    wizards = league.bot.wizard_service
    wizards.get_wizard_by_channel = AsyncMock(side_effect=signups.get_wizard_by_channel)
    for handler in ("handle_message", "handle_platform_button", "handle_driver_type_button",
                    "handle_preferred_teams_button", "handle_no_preference_teammate"):
        setattr(wizards, handler, AsyncMock(return_value=None))
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


async def _press(league: SeasonLeague, view_class: str, label: str, **built: Any) -> None:
    """Kimi presses *label* on the step's buttons in his channel, the view handed his account
    and the change queue when it is built (A23), and *built* besides."""
    from leaguebot.signup.services import signup_views

    press = member_interaction(league.bot, user=league._member(SIGNING_UP))
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    view = getattr(signup_views, view_class)(str(SIGNING_UP), league.bot.change_queue, **built)
    [button] = [child for child in view.children if getattr(child, "label", None) == label]
    await button.callback(press)


async def _answer(league: SeasonLeague, state: str) -> None:
    """Kimi gives an answer the step *state* accepts, as a driver gives it there."""
    if state == "COLLECTING_NATIONALITY":
        await _type(league, "British")
    elif state == "COLLECTING_PLATFORM":
        await _press(league, "PlatformButtonView", "Steam")
    elif state == "COLLECTING_PLATFORM_ID":
        await _type(league, "kimi7")
    elif state == "COLLECTING_AVAILABILITY":
        await _type(league, "1")
    elif state == "COLLECTING_PREFERRED_TEAMS":
        await _press(league, "PreferredTeamsButtonView", "No Preference",
                     team_names=["Alpha", "Beta"])
    elif state == "COLLECTING_PREFERRED_TEAMMATE":
        await _press(league, "NoPreferenceTeammateView", "No Preference")
    elif state == "COLLECTING_LAP_TIME":
        await _type(league, "1:23.456")
    else:
        raise AssertionError(f"no answer written for {state}")


async def _wizard(league: SeasonLeague) -> dict[str, Any] | None:
    rows = await league.rows(
        "SELECT * FROM signup_wizard_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return rows[0] if rows else None


async def _state(league: SeasonLeague) -> str:
    wizard = await _wizard(league)
    assert wizard is not None
    return str(wizard["wizard_state"])


def _typing(league: SeasonLeague) -> list[bool]:
    """Each change of Kimi's typing in his channel: True granted, False revoked."""
    return [call.kwargs["send_messages"]
            for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list
            if "send_messages" in call.kwargs]


# ---------------------------------------------------------------------------
# The ordinary sequence
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
@pytest.mark.parametrize(
    "current,expected",
    [
        ("COLLECTING_PLATFORM", "COLLECTING_PLATFORM_ID"),
        ("COLLECTING_PLATFORM_ID", "COLLECTING_AVAILABILITY"),
        ("COLLECTING_AVAILABILITY", "COLLECTING_DRIVER_TYPE"),
        ("COLLECTING_PREFERRED_TEAMS", "COLLECTING_PREFERRED_TEAMMATE"),
    ],
)
async def test_each_step_leads_to_the_next(tmp_path, current, expected):
    """Kimi answers the step *current*; once the queue runs his wizard asks *expected*, and one
    question is posted in his channel."""
    league = await _league(tmp_path, current)

    await _answer(league, current)
    await run_queue(league.bot)

    assert await _state(league) == expected
    assert len(league.channel(KIMI_CHANNEL).texts) == 1


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_moving_on_saves_the_wizard_and_resets_the_timeout(tmp_path):
    """The wizard survives a restart, so its new step is saved by the answer's own turn. Kimi,
    last active an hour ago, presses Steam at the platform question; once the queue runs his
    wizard is saved asking his platform ID, active as of now, and his 24 hours of inactivity
    are armed again from now."""
    league = await _league(tmp_path, "COLLECTING_PLATFORM")

    await _answer(league, "COLLECTING_PLATFORM")
    await run_queue(league.bot)

    wizard = await _wizard(league)
    assert wizard is not None
    assert wizard["wizard_state"] == "COLLECTING_PLATFORM_ID"
    assert wizard["last_activity_at"] == league.clock.now.isoformat()
    armed = [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
             if call.args and call.args[0] == INACTIVITY_TIMER]
    assert armed == [(INACTIVITY_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))]


# ---------------------------------------------------------------------------
# Nationality — asked only when the league wants it
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_nationality_leads_to_platform_when_it_is_required(tmp_path):
    """The league requires a nationality; Kimi types "British" at that question. Once the queue
    runs his wizard asks his platform."""
    league = await _league(tmp_path, "COLLECTING_NATIONALITY", nationality_required=True)

    await _answer(league, "COLLECTING_NATIONALITY")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_PLATFORM"


# ---------------------------------------------------------------------------
# Preferred teams — not asked of a reserve
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_a_reserve_driver_is_not_asked_which_team_they_prefer(tmp_path):
    """A reserve is not placed in a team, so the answer could not be honoured. Kimi presses
    Reserve Driver; once the queue runs his wizard asks for a preferred teammate, never his
    preferred teams."""
    league = await _league(tmp_path, "COLLECTING_DRIVER_TYPE")

    await _press(league, "DriverTypeButtonView", "Reserve Driver")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_PREFERRED_TEAMMATE"


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_a_full_time_driver_is_asked_which_team_they_prefer(tmp_path):
    """Kimi presses Full-Time Driver; once the queue runs his wizard asks his preferred teams."""
    league = await _league(tmp_path, "COLLECTING_DRIVER_TYPE")

    await _press(league, "DriverTypeButtonView", "Full-Time Driver")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_PREFERRED_TEAMS"


# ---------------------------------------------------------------------------
# Lap times — repeated per track, or skipped
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_lap_times_are_skipped_when_the_league_selected_no_tracks(tmp_path):
    """No tracks selected. Kimi presses No Preference at the teammate question; once the queue
    runs his wizard asks his notes."""
    league = await _league(tmp_path, "COLLECTING_PREFERRED_TEAMMATE", tracks=())

    await _answer(league, "COLLECTING_PREFERRED_TEAMMATE")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_NOTES"


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_lap_times_are_asked_for_when_tracks_are_selected(tmp_path):
    """Two tracks selected (01, 02). Kimi presses No Preference at the teammate question; once
    the queue runs his wizard asks a lap time."""
    league = await _league(tmp_path, "COLLECTING_PREFERRED_TEAMMATE", tracks=("01", "02"))

    await _answer(league, "COLLECTING_PREFERRED_TEAMMATE")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_LAP_TIME"


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_the_wizard_stays_on_lap_time_while_tracks_remain(tmp_path):
    """The only step the wizard can stay on. Two tracks selected; Kimi types his time for the
    first. Once the queue runs his wizard still asks a lap time, now for the second track, one
    question is posted, and his 24 hours are armed again."""
    league = await _league(tmp_path, "COLLECTING_LAP_TIME", tracks=("01", "02"), lap_index=0)

    await _answer(league, "COLLECTING_LAP_TIME")
    await run_queue(league.bot)

    wizard = await _wizard(league)
    assert wizard is not None
    assert (wizard["wizard_state"], wizard["current_lap_track_index"]) == (
        "COLLECTING_LAP_TIME", 1
    )
    assert len(league.channel(KIMI_CHANNEL).texts) == 1
    assert any(call.args and call.args[0] == INACTIVITY_TIMER
               for call in league.bot.scheduler_service.arm_once.call_args_list)


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_the_last_track_moves_the_wizard_on(tmp_path):
    """Two tracks selected; Kimi types his time for the second. Once the queue runs his wizard
    asks his notes."""
    league = await _league(tmp_path, "COLLECTING_LAP_TIME", tracks=("01", "02"), lap_index=1)

    await _answer(league, "COLLECTING_LAP_TIME")
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_NOTES"


# ---------------------------------------------------------------------------
# The write permission around button-only steps
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_write_is_revoked_when_entering_a_button_only_step(tmp_path):
    """Platform is answered by a button, so typing is turned off to stop a driver answering in a
    form the wizard will not read. Kimi types "British" at the nationality question; once the
    queue runs his typing in his channel is revoked."""
    league = await _league(tmp_path, "COLLECTING_NATIONALITY", nationality_required=True)

    await _answer(league, "COLLECTING_NATIONALITY")
    await run_queue(league.bot)

    assert _typing(league) == [False]


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_write_is_restored_when_leaving_a_button_only_step(tmp_path):
    """The half that matters most: a driver left without write permission cannot answer the next
    typed question. Kimi presses Steam; once the queue runs his typing is granted again."""
    league = await _league(tmp_path, "COLLECTING_PLATFORM")

    await _answer(league, "COLLECTING_PLATFORM")
    await run_queue(league.bot)

    assert _typing(league) == [True]


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_a_driver_who_has_left_the_server_does_not_stop_the_advance(tmp_path):
    """Kimi presses Steam, then leaves the server before his turn comes, so the server no longer
    finds him. Once the queue runs his wizard still asks his platform ID, and the turns in his
    channel are not stopped: the typing change is skipped rather than failing."""
    league = await _league(tmp_path, "COLLECTING_PLATFORM")

    await _answer(league, "COLLECTING_PLATFORM")
    league.guild.get_member = MagicMock(return_value=None)
    await run_queue(league.bot)

    assert await _state(league) == "COLLECTING_PLATFORM_ID"
    assert await stopped_job(league.db_path, KIMI_LANE) is None


# ---------------------------------------------------------------------------
# The end of the sequence
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_the_last_step_commits_the_signup(tmp_path):
    """Kimi has answered everything but his notes. He types "See you there"; once the queue runs
    he is Pending Admin Approval and his signup is saved for the season."""
    league = await _league(tmp_path, "COLLECTING_NOTES", draft=ANSWERED_TO_NOTES)

    await _type(league, "See you there")
    await run_queue(league.bot)

    [state] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert state["current_state"] == "PENDING_ADMIN_APPROVAL"
    records = await league.rows(
        "SELECT season_id FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert [row["season_id"] for row in records] == [SEASON_ID]


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_the_final_answers_are_saved_before_the_commit_reads_them(tmp_path):
    """The commit reads the draft back out of the database, so an unsaved last answer would be
    committed as though never given. Kimi types "See you there" as his notes; once the queue
    runs his saved signup holds them."""
    league = await _league(tmp_path, "COLLECTING_NOTES", draft=ANSWERED_TO_NOTES)

    await _type(league, "See you there")
    await run_queue(league.bot)

    [record] = await league.rows(
        "SELECT notes FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert record["notes"] == "See you there"


# ---------------------------------------------------------------------------
# Correction mode
# ---------------------------------------------------------------------------


async def _correcting_platform(tmp_path: Any) -> SeasonLeague:
    """A league manager asked Kimi to correct his platform: he is Pending Driver Correction, his
    signup already saved, his wizard asking his platform again and marked as a correction."""
    draft = dict(ANSWERED_TO_NOTES, notes="Happy to race anywhere", _is_correction=True)
    league = await _league(tmp_path, "COLLECTING_PLATFORM", draft=draft,
                           driver_state="PENDING_DRIVER_CORRECTION")
    await league.write(
        "INSERT INTO signup_records (season_id, discord_user_id, nationality, platform, "
        "platform_id, driver_type, notes, signup_channel_id) "
        "VALUES (?, ?, 'British', 'Steam', 'kimi7', 'Full-Time Driver', "
        "'Happy to race anywhere', ?)",
        SEASON_ID, str(SIGNING_UP), KIMI_CHANNEL,
    )
    return league


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_a_correction_commits_instead_of_resuming_the_sequence(tmp_path):
    """A driver amending one answer after review must not be asked every later question again.
    Kimi presses EA; once the queue runs his signup holds EA and he is Pending Admin Approval
    again, and no next question (his platform ID) is posted in his channel."""
    league = await _correcting_platform(tmp_path)

    await _press(league, "PlatformButtonView", "EA")
    await run_queue(league.bot)

    [record] = await league.rows(
        "SELECT platform FROM signup_records WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert record["platform"] == "EA"
    [state] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    assert state["current_state"] == "PENDING_ADMIN_APPROVAL"
    assert not any("Platform ID" in text for text in league.channel(KIMI_CHANNEL).texts)


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_TURN)
async def test_the_correction_flag_is_consumed_rather_than_left_behind(tmp_path):
    """Left in place the mark would make the driver's next answer a correction too. Kimi presses
    EA; once the queue runs his wizard no longer holds the correction mark."""
    import json

    league = await _correcting_platform(tmp_path)

    await _press(league, "PlatformButtonView", "EA")
    await run_queue(league.bot)

    wizard = await _wizard(league)
    draft = json.loads(wizard["draft_answers_json"] or "{}") if wizard else {}
    assert "_is_correction" not in draft
