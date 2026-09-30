"""The signup wizard's buttons — platform, driver type, preferred teams and teammate.

Issue #208. Four of the wizard's steps are answered by pressing a button rather than typing,
and those four handlers were unexecuted. They are not thin wrappers around the typed handlers:
a button arrives from Discord with no message and no channel, so each one has to find the
driver's wizard, check it is still on the step the button belongs to, and find the channel
again before it can do anything.

**Every handler re-checks the step, and that check is the interesting part.** A wizard channel
keeps its old messages, so the buttons from step 2 are still sitting there when the driver
reaches step 6. Pressing one must do nothing at all. Without the check, a driver scrolling up
and pressing an old button would overwrite an answer they had already given and shunt the
wizard backwards. Each handler gets a test for that, because the check is copied between them
rather than shared.

**Preferred teams is a sub-step loop and the only one with real logic.** A driver picks up to
three teams, one button press at a time; each pick narrows the choices offered next; and
**No Preference** finishes early with however many picks have accumulated. Three things end
the loop — three picks taken, no teams left to offer, or No Preference — and all three are
pinned, because each ends it by a different route and a reader collapsing them would strand a
driver on a step with no way forward.

The sub-step counter lives in `draft_answers` under `_pref_teams_step` and is **removed** when
the loop ends. It is bookkeeping, not an answer, and leaving it behind would carry it into the
committed signup.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99

_SILENT = (
    "#482: a button on a step already answered is turned away in silence, returning no reason "
    "for the view to answer and record"
)

_NO_STEP_LINE = "#482: a wizard step answered by a button writes no line in the log channel"

_PICKED_AGAIN = "#482: a team already picked is recorded a second time rather than turned away"

#: The reply a press on a step already answered gets (the plan, commit point 19).
_ALREADY_ANSWERED = "That step has already been answered."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(state, **draft):
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=state,
        signup_channel_id=CHANNEL_ID,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=[],
            team_names=["Alpha", "Beta", "Gamma", "Delta"],
        ),
        draft_answers=dict(draft),
        current_lap_track_index=0,
        last_activity_at=None,
    )


@pytest.fixture
def service():
    """A `WizardService` with its Discord edges stubbed, plus the channel and a record of
    every advance."""
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    advanced: list = []

    async def _advance_in_channel(wizard, channel, guild):
        advanced.append(wizard)

    svc._advance_wizard_in_channel = _advance_in_channel  # type: ignore[method-assign]
    svc._reset_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)

    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=channel)

    member = MagicMock()
    member.id = int(DRIVER_ID)
    member.display_name = "Alex"
    guild.get_member = MagicMock(return_value=member)

    signup_svc = MagicMock()
    signup_svc.save_wizard = AsyncMock(return_value=None)

    router = MagicMock()
    router.post_log = AsyncMock(return_value=None)

    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router = router
    svc._bot = bot
    svc._output_router = router

    return SimpleNamespace(
        svc=svc,
        advanced=advanced,
        channel=channel,
        guild=guild,
        signup_svc=signup_svc,
        router=router,
    )


def _serve(ctx, wizard) -> None:
    """Make `get_wizard` answer with *wizard*."""
    ctx.signup_svc.get_wizard = AsyncMock(return_value=wizard)


def _lines(ctx) -> list[str]:
    """Every line the press wrote in the log channel."""
    return [str(call.args[0]) for call in ctx.router.post_log.await_args_list]


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


async def test_a_platform_button_records_the_platform_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)
    _serve(service, wizard)

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert wizard.draft_answers["platform"] == "Steam"
    assert service.advanced == [wizard]


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_a_platform_button_pressed_on_a_later_step_does_nothing(service):
    """The step-2 buttons are still in the channel when the driver reaches step 5.
    Pressing one must not overwrite an answer and shunt the wizard backwards, and the handler
    says why, for the view to answer and record."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE)
    _serve(service, wizard)

    reason = await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert "platform" not in wizard.draft_answers
    assert service.advanced == []
    assert _ALREADY_ANSWERED in (reason or "")


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_a_button_from_a_driver_with_no_wizard_does_nothing(service):
    """The wizard channel is deleted when a signup completes, but a button could still be
    pressed from a cached view before the delete lands. The handler says why it did nothing
    rather than leaving the press unanswered."""
    _serve(service, None)

    reason = await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert service.advanced == []
    assert isinstance(reason, str) and reason


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_a_button_whose_channel_is_gone_does_not_advance(service):
    """The handler says why it did nothing rather than leaving the press unanswered."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)
    _serve(service, wizard)
    service.guild.get_channel = MagicMock(return_value=None)

    reason = await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert service.advanced == []
    assert isinstance(reason, str) and reason


# ---------------------------------------------------------------------------
# Driver type
# ---------------------------------------------------------------------------


async def test_a_driver_type_button_records_the_type_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE)
    _serve(service, wizard)

    await service.svc.handle_driver_type_button(
        DRIVER_ID, "Reserve Driver", service.guild
    )

    assert wizard.draft_answers["driver_type"] == "Reserve Driver"
    assert service.advanced == [wizard]


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_a_driver_type_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    reason = await service.svc.handle_driver_type_button(
        DRIVER_ID, "Reserve Driver", service.guild
    )

    assert "driver_type" not in wizard.draft_answers
    assert service.advanced == []
    assert _ALREADY_ANSWERED in (reason or "")


# ---------------------------------------------------------------------------
# Preferred teams — the sub-step loop
# ---------------------------------------------------------------------------


async def test_a_first_team_pick_is_recorded_and_the_next_is_offered(service):
    """The loop continues rather than advancing: a driver picking one team has not yet
    said they are finished."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha"]
    assert wizard.draft_answers["_pref_teams_step"] == 1
    assert service.advanced == []
    service.channel.send.assert_awaited_once()


async def test_the_next_prompt_names_the_ordinal_and_the_picks_so_far(service):
    """The driver is several presses into a list they cannot see; the prompt is the only
    record of what they have already chosen."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    prompt = service.channel.send.await_args.args[0]
    assert "2nd" in prompt
    assert "Alpha" in prompt


async def test_a_team_already_picked_is_not_offered_again(service):
    """`excluded` is what stops a driver naming the same team as their first and second
    choice, which would waste a pick."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    view = service.channel.send.await_args.kwargs["view"]
    labels = [child.label for child in view.children if getattr(child, "label", None)]
    assert "Alpha" not in labels


@pytest.mark.xfail(strict=True, reason=_PICKED_AGAIN)
@pytest.mark.parametrize("correction", [False, True], ids=["signup", "correction"])
async def test_a_team_already_picked_is_refused_and_changes_nothing(service, correction):
    """Alex picked Alpha, scrolled up and pressed Alpha again on the first sub-step's message,
    in a first signup or while correcting the preferred teams. The handler records nothing,
    offers no next sub-step and writes no line, and says why: "That team has already been
    picked." (#482, D3)."""
    from leaguebot.signup.models.signup_module import WizardState

    draft = {"preferred_teams": ["Alpha"], "_pref_teams_step": 1}
    if correction:
        draft["_is_correction"] = True
    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS, **draft)
    _serve(service, wizard)

    reason = await service.svc.handle_preferred_teams_button(DRIVER_ID, "Alpha", service.guild)

    assert "That team has already been picked." in (reason or "")
    assert wizard.draft_answers["preferred_teams"] == ["Alpha"]
    assert wizard.draft_answers["_pref_teams_step"] == 1
    assert service.advanced == []
    service.channel.send.assert_not_awaited()
    assert _lines(service) == []


async def test_a_third_pick_ends_the_loop(service):
    """Three is the limit, so the third press advances rather than offering a fourth."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha", "Beta"],
        _pref_teams_step=2,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Gamma", service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha", "Beta", "Gamma"]
    assert service.advanced == [wizard]


async def test_running_out_of_teams_ends_the_loop(service):
    """A league with two teams cannot offer a third pick. Without this the driver would be
    shown a prompt with no buttons on it and no way forward."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS, preferred_teams=["Alpha"])
    wizard.config_snapshot.team_names = ["Alpha", "Beta"]
    wizard.draft_answers["_pref_teams_step"] = 1
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Beta", service.guild
    )

    assert service.advanced == [wizard]


async def test_no_preference_ends_the_loop_keeping_the_picks_so_far(service):
    """The third way out. A driver who wanted only one team says so by pressing No
    Preference, and that first pick must survive — discarding it would lose the one
    preference they expressed."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha"],
        _pref_teams_step=1,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, None, service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha"]
    assert service.advanced == [wizard]


async def test_no_preference_with_no_picks_at_all_stores_an_empty_list(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, None, service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == []
    assert service.advanced == [wizard]


@pytest.mark.parametrize("team", ["Gamma", None], ids=["third-pick", "no-preference"])
async def test_the_sub_step_counter_is_cleared_when_the_loop_ends(service, team):
    """It is bookkeeping, not an answer. Left in `draft_answers` it would be carried into
    the committed signup as though the driver had told the league something."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha", "Beta"],
        _pref_teams_step=2,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, team, service.guild
    )

    assert "_pref_teams_step" not in wizard.draft_answers


async def test_a_continuing_loop_saves_the_wizard_and_resets_the_timeout(service):
    """Each press is activity. Without the reset a driver working through three picks
    could be timed out mid-choice."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    service.signup_svc.save_wizard.assert_awaited_once()
    service.svc._reset_inactivity_job.assert_awaited_once()
    assert wizard.last_activity_at is not None


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_a_team_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES)
    _serve(service, wizard)

    reason = await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    assert "preferred_teams" not in wizard.draft_answers
    assert service.advanced == []
    assert _ALREADY_ANSWERED in (reason or "")


# ---------------------------------------------------------------------------
# Preferred teammate
# ---------------------------------------------------------------------------


async def test_no_preference_for_a_teammate_records_none_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMMATE)
    _serve(service, wizard)

    await service.svc.handle_no_preference_teammate(DRIVER_ID, service.guild)

    assert wizard.draft_answers["preferred_teammate"] is None
    assert service.advanced == [wizard]


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_the_teammate_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES)
    _serve(service, wizard)

    reason = await service.svc.handle_no_preference_teammate(DRIVER_ID, service.guild)

    assert "preferred_teammate" not in wizard.draft_answers
    assert service.advanced == []
    assert _ALREADY_ANSWERED in (reason or "")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_SILENT)
async def test_no_notes_pressed_after_the_signup_was_submitted_does_nothing(service):
    """A driver whose signup has gone to review presses the No Notes left in the channel. It
    must not submit the signup a second time, and the handler says why."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.UNENGAGED)
    _serve(service, wizard)
    service.svc.commit_wizard = AsyncMock(return_value=None)  # type: ignore[method-assign]

    reason = await service.svc.handle_no_notes(DRIVER_ID, service.guild)

    service.svc.commit_wizard.assert_not_awaited()
    assert "notes" not in wizard.draft_answers
    assert _ALREADY_ANSWERED in (reason or "")


# ---------------------------------------------------------------------------
# The line each button answer writes
# ---------------------------------------------------------------------------


async def _press(ctx, handler: str, answer):
    if handler == "handle_platform_button":
        return await ctx.svc.handle_platform_button(DRIVER_ID, answer, ctx.guild)
    if handler == "handle_driver_type_button":
        return await ctx.svc.handle_driver_type_button(DRIVER_ID, answer, ctx.guild)
    if handler == "handle_preferred_teams_button":
        return await ctx.svc.handle_preferred_teams_button(DRIVER_ID, answer, ctx.guild)
    return await ctx.svc.handle_no_preference_teammate(DRIVER_ID, ctx.guild)


#: Each button step, the state it answers, the answer pressed, and what its line must carry.
_BUTTON_STEPS = [
    pytest.param("COLLECTING_PLATFORM", "handle_platform_button", "Steam",
                 ["Platform: Steam"], id="platform"),
    pytest.param("COLLECTING_DRIVER_TYPE", "handle_driver_type_button", "Reserve Driver",
                 ["Reserve Driver"], id="driver-type"),
    pytest.param("COLLECTING_PREFERRED_TEAMS", "handle_preferred_teams_button", "Alpha",
                 ["Alpha"], id="team"),
    pytest.param("COLLECTING_PREFERRED_TEAMS", "handle_preferred_teams_button", None,
                 ["team", "no preference"], id="team-no-preference"),
    pytest.param("COLLECTING_PREFERRED_TEAMMATE", "handle_no_preference_teammate", None,
                 ["teammate", "no preference"], id="teammate-no-preference"),
]


@pytest.mark.xfail(strict=True, reason=_NO_STEP_LINE)
@pytest.mark.parametrize("state, handler, answer, carries", _BUTTON_STEPS)
async def test_a_button_answer_writes_one_line_with_the_answer(
    service, state, handler, answer, carries
):
    """Alex, part-way through the wizard, answers a step by pressing a button: Steam at the
    platform step, Reserve Driver at the driver-type step, Alpha as a first preferred team, No
    Preference for teams, No Preference for a teammate. The press is accepted, and it writes
    one line in the wizard's family, naming Alex and carrying the answer (owner, 2026-09-30,
    "Log button steps after all")."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState[state])
    _serve(service, wizard)

    reason = await _press(service, handler, answer)

    assert reason is None
    lines = _lines(service)
    assert len(lines) == 1, lines
    assert lines[0].startswith("Alex (<@7>) | Signup | "), lines[0]
    for text in carries:
        assert text.lower() in lines[0].lower(), lines[0]


@pytest.mark.xfail(strict=True, reason=_NO_STEP_LINE)
async def test_the_platform_line_reads_as_the_plan_gives_it(service):
    """The one line whose words are fixed: "Alex (<@7>) | Signup | Platform: Steam"."""
    from leaguebot.signup.models.signup_module import WizardState

    _serve(service, _wizard(WizardState.COLLECTING_PLATFORM))

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert _lines(service) == ["Alex (<@7>) | Signup | Platform: Steam"]


@pytest.mark.parametrize(
    "state, handler, answer",
    [
        pytest.param("COLLECTING_PLATFORM", "handle_platform_button", "Steam", id="platform"),
        pytest.param("COLLECTING_DRIVER_TYPE", "handle_driver_type_button", "Reserve Driver",
                     id="driver-type"),
        pytest.param("COLLECTING_PREFERRED_TEAMS", "handle_preferred_teams_button", None,
                     id="team-no-preference"),
        pytest.param("COLLECTING_PREFERRED_TEAMMATE", "handle_no_preference_teammate", None,
                     id="teammate-no-preference"),
    ],
)
async def test_a_button_answer_that_ends_a_correction_writes_no_step_line(
    service, state, handler, answer
):
    """Alex was asked to correct one answer and gives it by pressing a button. That press ends
    the correction, and the correction's own "Correction submitted" line is its one line: the
    handler writes no step line beside it (one line per action)."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState[state], _is_correction=True)
    _serve(service, wizard)

    await _press(service, handler, answer)

    assert service.advanced == [wizard]
    assert _lines(service) == []
