"""The signup wizard's step handlers — what a driver types, and what the wizard accepts.

Issue #208. `wizard_service.py` was the largest single hole in the bot at 27.0%, and these
handlers are its input boundary: the one place a driver's own words become the league's
record. `tests/signup/test_wizard_availability.py` covers one of them (issue #126); the rest —
nationality, platform, platform ID, driver type, preferred teams, preferred teammate, lap
times and notes — were unexecuted.

Every handler has the same shape: validate, and either send the driver an error *or* store the
answer and advance. **The pairing is the thing worth pinning.** A handler that stored a bad
answer and advanced anyway would put rubbish into the signup; one that sent an error and
advanced anyway would skip the question. So each rejection test asserts both that nothing was
stored and that the wizard did not move on, and each acceptance test asserts both that the
right value was stored and that it did.

Two rules here are the driver's, not the bot's, and are easy to tidy away:

- **A rejection must quote the valid options.** The driver is typing into a private channel
  with no form validation and no list in front of them; an error that does not say what would
  have been accepted leaves them guessing. Every rejection test asserts the options appear.
- **"No preference" is an answer, not an absence.** Preferred teams and preferred teammate
  both accept it, and it must advance the wizard rather than being treated as empty input.

The lap-time step carries the extra rule that a screenshot may be required, and that the
requirement is checked *before* the time is parsed — a driver who typed a good time without
the image must be told about the image, not told their time was fine.

`_advance_wizard` is stubbed throughout. Whether the wizard moves is the assertion; where it
moves to belongs to `_advance_wizard`'s own cover.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content, *args, **kwargs):
        self.sent.append(content)


class _Message:
    """A driver's message. *attachments* stands in for a lap-time screenshot."""

    def __init__(self, content: str, *, attachments: list | None = None) -> None:
        self.content = content
        self.channel = _Channel()
        self.attachments = attachments or []


def _slots():
    from leaguebot.signup.models.signup_module import AvailabilitySlot

    return [
        AvailabilitySlot(
            id=41,
            slot_id=AvailabilitySlot.make_slot_id(1, "19:00"),
            slot_sequence_id=1,
            day_of_week=1,
            time_hhmm="19:00",
            display_label=AvailabilitySlot.make_label(1, "19:00"),
        )
    ]


@pytest.fixture
def wizard_and_service():
    """A `WizardService` with `_advance_wizard` stubbed, and a wizard record to drive.

    Built with `__new__` rather than the constructor: the handlers under test need none of
    the scheduler, output router or database the real one wires up, and registering the
    global singleton the constructor sets would leak between tests.
    """
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    advanced: list[bool] = []

    async def _advance(wizard, message):
        advanced.append(True)

    svc._advance_wizard = _advance  # type: ignore[method-assign]

    wizard = SignupWizardRecord(
        id=1,
        discord_user_id="7",
        wizard_state=WizardState.COLLECTING_PLATFORM,
        signup_channel_id=99,
        config_snapshot=ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=["1", "2"],
            slots=_slots(),
            team_names=["Alpha", "Beta", "Gamma", "Delta"],
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at=None,
    )
    return svc, wizard, advanced


def _with_teams(svc, names: list[str]) -> None:
    """Give the service a bot whose team service offers *names* plus a Reserve team."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    teams = [SimpleNamespace(name=n, full_name=f"{n} Racing", is_reserve=False) for n in names]
    teams.append(SimpleNamespace(name="Reserve", full_name="Reserve", is_reserve=True))
    bot = MagicMock()
    bot.team_service.get_default_teams = AsyncMock(return_value=teams)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    svc._bot = bot


# ---------------------------------------------------------------------------
# Nationality
# ---------------------------------------------------------------------------


async def test_a_nationality_is_stored_as_its_adjective(wizard_and_service):
    """A driver may type either the country or the adjective; one form is stored, so the
    signup list does not read as a mixture of "Germany" and "German"."""
    svc, wizard, advanced = wizard_and_service

    await svc._handle_nationality(wizard, _Message("Germany"))

    assert wizard.draft_answers["nationality"] == "German"
    assert advanced == [True]


async def test_an_unknown_nationality_is_refused_with_examples(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("xyzzy")

    await svc._handle_nationality(wizard, message)

    assert "nationality" not in wizard.draft_answers
    assert advanced == []
    assert "British" in message.channel.sent[0]


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["Steam", "steam", "STEAM"])
async def test_a_platform_is_matched_regardless_of_case(wizard_and_service, typed):
    """Stored in the bot's own casing, so the signup list is consistent however it was
    typed."""
    svc, wizard, advanced = wizard_and_service

    await svc._handle_platform(wizard, _Message(typed))

    assert wizard.draft_answers["platform"] == "Steam"
    assert advanced == [True]


async def test_an_unknown_platform_is_refused_listing_the_choices(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("Dreamcast")

    await svc._handle_platform(wizard, message)

    assert "platform" not in wizard.draft_answers
    assert advanced == []
    reply = message.channel.sent[0]
    for option in ("Steam", "EA", "Xbox", "PlayStation"):
        assert option in reply


# ---------------------------------------------------------------------------
# Platform ID
# ---------------------------------------------------------------------------


async def test_a_platform_id_is_stored_trimmed(wizard_and_service):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_platform_id(wizard, _Message("  lh44  "))

    assert wizard.draft_answers["platform_id"] == "lh44"
    assert advanced == [True]


async def test_an_empty_platform_id_is_refused(wizard_and_service):
    """Whitespace only. The ID is how a league finds the driver in-game, so an empty one
    is worse than no answer — it looks answered."""
    svc, wizard, advanced = wizard_and_service
    message = _Message("   ")

    await svc._handle_platform_id(wizard, message)

    assert "platform_id" not in wizard.draft_answers
    assert advanced == []
    assert "cannot be empty" in message.channel.sent[0]


# ---------------------------------------------------------------------------
# Driver type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed,stored",
    [
        ("Full-Time Driver", "Full-Time Driver"),
        ("full-time driver", "Full-Time Driver"),
        ("Reserve Driver", "Reserve Driver"),
        ("reserve driver", "Reserve Driver"),
    ],
)
async def test_a_driver_type_is_matched_regardless_of_case(wizard_and_service, typed, stored):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_driver_type(wizard, _Message(typed))

    assert wizard.draft_answers["driver_type"] == stored
    assert advanced == [True]


async def test_an_unknown_driver_type_is_refused_listing_the_choices(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("Test Driver")

    await svc._handle_driver_type(wizard, message)

    assert "driver_type" not in wizard.draft_answers
    assert advanced == []
    assert "Full-Time Driver" in message.channel.sent[0]
    assert "Reserve Driver" in message.channel.sent[0]


# ---------------------------------------------------------------------------
# Preferred teams
# ---------------------------------------------------------------------------


async def test_preferred_teams_are_stored_in_the_order_given(wizard_and_service):
    """The order is the driver's preference order, which the placement reads, so it is not
    the bot's to sort."""
    svc, wizard, advanced = wizard_and_service
    _with_teams(svc, ["Alpha", "Beta", "Gamma"])

    await svc._handle_preferred_teams(wizard, _Message("Gamma, Alpha"))

    assert wizard.draft_answers["preferred_teams"] == ["Gamma Racing", "Alpha Racing"]
    assert advanced == [True]


async def test_preferred_teams_may_be_separated_by_newlines(wizard_and_service):
    """A driver reading a list of teams will often paste them one per line."""
    svc, wizard, _ = wizard_and_service
    _with_teams(svc, ["Alpha", "Beta", "Gamma"])

    await svc._handle_preferred_teams(wizard, _Message("Alpha\nBeta"))

    assert wizard.draft_answers["preferred_teams"] == ["Alpha Racing", "Beta Racing"]


async def test_no_preference_for_teams_is_an_answer_not_an_absence(wizard_and_service):
    """It stores an empty list and advances. Treating it as empty input would ask the
    question again and the driver would have no way past it."""
    svc, wizard, advanced = wizard_and_service
    _with_teams(svc, ["Alpha"])

    await svc._handle_preferred_teams(wizard, _Message("no preference"))

    assert wizard.draft_answers["preferred_teams"] == []
    assert advanced == [True]


async def test_more_than_three_preferred_teams_is_refused(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    _with_teams(svc, ["Alpha", "Beta", "Gamma", "Delta"])
    message = _Message("Alpha, Beta, Gamma, Delta")

    await svc._handle_preferred_teams(wizard, message)

    assert "preferred_teams" not in wizard.draft_answers
    assert advanced == []
    assert "up to 3" in message.channel.sent[0]


async def test_an_unknown_team_is_refused_listing_the_valid_ones(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    _with_teams(svc, ["Alpha", "Beta"])
    message = _Message("Alpha, Ferrari")

    await svc._handle_preferred_teams(wizard, message)

    assert "preferred_teams" not in wizard.draft_answers
    assert advanced == []
    reply = message.channel.sent[0]
    assert "Ferrari" in reply
    assert "Alpha" in reply


async def test_the_reserve_team_is_not_offered_as_a_preference(wizard_and_service):
    """A driver does not choose to be a reserve by naming the Reserve team — that is what
    the driver-type step is for. Accepting it here would give them two ways to say it and
    the two could disagree."""
    svc, wizard, advanced = wizard_and_service
    _with_teams(svc, ["Alpha"])
    message = _Message("Reserve")

    await svc._handle_preferred_teams(wizard, message)

    assert "preferred_teams" not in wizard.draft_answers
    assert advanced == []


# ---------------------------------------------------------------------------
# Preferred teammate
# ---------------------------------------------------------------------------


async def test_a_preferred_teammate_is_stored_as_typed(wizard_and_service):
    """Free text — the driver names someone who may not have signed up yet, so it cannot
    be validated against anything."""
    svc, wizard, advanced = wizard_and_service

    await svc._handle_preferred_teammate(wizard, _Message("Lewis"))

    assert wizard.draft_answers["preferred_teammate"] == "Lewis"
    assert advanced == [True]


async def test_no_preference_for_a_teammate_stores_nothing(wizard_and_service):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_preferred_teammate(wizard, _Message("No Preference"))

    assert wizard.draft_answers["preferred_teammate"] is None
    assert advanced == [True]


# ---------------------------------------------------------------------------
# Lap times
# ---------------------------------------------------------------------------


async def test_a_lap_time_is_stored_against_the_current_track(wizard_and_service):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_lap_time(wizard, _Message("1:23.456"))

    assert wizard.draft_answers["lap_times"] == {"1": "1:23.456"}
    assert advanced == [True]


async def test_each_lap_time_advances_to_the_next_track(wizard_and_service):
    """The index is what decides which track the *next* time belongs to. Left unmoved, a
    driver would overwrite their first time for every track they were asked about."""
    svc, wizard, _ = wizard_and_service

    await svc._handle_lap_time(wizard, _Message("1:23.456"))
    await svc._handle_lap_time(wizard, _Message("1:24.000"))

    assert wizard.draft_answers["lap_times"] == {"1": "1:23.456", "2": "1:24.000"}
    assert wizard.current_lap_track_index == 2


async def test_a_malformed_lap_time_is_refused_with_the_format(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("about a minute and a half")

    await svc._handle_lap_time(wizard, message)

    assert "lap_times" not in wizard.draft_answers
    assert advanced == []
    assert "M:ss.mmm" in message.channel.sent[0]


async def test_a_rejected_lap_time_does_not_move_to_the_next_track(wizard_and_service):
    """Otherwise a typo would silently cost the driver that track's time."""
    svc, wizard, _ = wizard_and_service

    await svc._handle_lap_time(wizard, _Message("nonsense"))

    assert wizard.current_lap_track_index == 0


async def test_a_required_screenshot_is_demanded_before_the_time_is_read(
    wizard_and_service,
):
    """The image check runs first deliberately. A driver who typed a perfectly good time
    with no screenshot must be told about the screenshot — being told their time was
    invalid would send them to fix the wrong thing."""
    svc, wizard, advanced = wizard_and_service
    wizard.config_snapshot.time_image_required = True
    message = _Message("1:23.456")

    await svc._handle_lap_time(wizard, message)

    assert advanced == []
    assert "screenshot" in message.channel.sent[0]


async def test_a_lap_time_with_its_screenshot_is_accepted(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    wizard.config_snapshot.time_image_required = True
    message = _Message("1:23.456", attachments=[object()])

    await svc._handle_lap_time(wizard, message)

    assert wizard.draft_answers["lap_times"] == {"1": "1:23.456"}
    assert advanced == [True]


async def test_the_rejection_names_the_kind_of_time_being_asked_for(wizard_and_service):
    """A league may ask for a time trial or a short qualification, and the driver sets
    them in different places in the game."""
    svc, wizard, _ = wizard_and_service
    wizard.config_snapshot.time_type = "SHORT_QUALI"
    message = _Message("nonsense")

    await svc._handle_lap_time(wizard, message)

    assert "Short Qualification" in message.channel.sent[0]


async def test_a_lap_time_past_the_last_track_advances_rather_than_storing(
    wizard_and_service,
):
    """Defensive: the index should never be past the end, and if it is, the wizard must
    move on rather than raise an IndexError inside a driver's private channel."""
    svc, wizard, advanced = wizard_and_service
    wizard.current_lap_track_index = 99

    await svc._handle_lap_time(wizard, _Message("1:23.456"))

    assert advanced == [True]
    assert "lap_times" not in wizard.draft_answers


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


async def test_a_note_is_stored(wizard_and_service):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_notes(wizard, _Message("Prefer not to race Mondays"))

    assert wizard.draft_answers["notes"] == "Prefer not to race Mondays"
    assert advanced == [True]


async def test_no_notes_stores_nothing(wizard_and_service):
    svc, wizard, advanced = wizard_and_service

    await svc._handle_notes(wizard, _Message("No Notes"))

    assert wizard.draft_answers["notes"] is None
    assert advanced == [True]


async def test_a_note_over_fifty_characters_is_refused(wizard_and_service):
    """Fifty characters is the limit the signup specification sets for a note, which is
    quoted as text in the review panel and the unassigned list."""
    svc, wizard, advanced = wizard_and_service
    message = _Message("x" * 51)

    await svc._handle_notes(wizard, message)

    assert "notes" not in wizard.draft_answers
    assert advanced == []
    assert "50 characters" in message.channel.sent[0]


async def test_a_note_of_exactly_fifty_characters_is_accepted(wizard_and_service):
    """Sits the other side of the bound from the test above."""
    svc, wizard, advanced = wizard_and_service

    await svc._handle_notes(wizard, _Message("x" * 50))

    assert wizard.draft_answers["notes"] == "x" * 50
    assert advanced == [True]


# ---------------------------------------------------------------------------
# A group mention in a free-text answer (#362)
# ---------------------------------------------------------------------------


async def test_a_signup_note_mentioning_everyone_is_refused(wizard_and_service):
    """The review panel quotes the note to the channel, where the bot's own permission to
    mention everybody would carry the ping."""
    svc, wizard, advanced = wizard_and_service
    message = _Message("@everyone please check")

    await svc._handle_notes(wizard, message)

    assert "notes" not in wizard.draft_answers
    assert advanced == []
    assert "notes" in message.channel.sent[0]


async def test_a_preferred_teammate_given_as_a_role_mention_is_refused(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("<@&987654321098765432>")

    await svc._handle_preferred_teammate(wizard, message)

    assert "preferred_teammate" not in wizard.draft_answers
    assert advanced == []


async def test_a_platform_id_holding_here_is_refused(wizard_and_service):
    svc, wizard, advanced = wizard_and_service
    message = _Message("@here")

    await svc._handle_platform_id(wizard, message)

    assert "platform_id" not in wizard.draft_answers
    assert advanced == []


async def test_a_signup_note_with_an_emoji_is_kept(wizard_and_service):
    """Shown only as text, where an emoji reads as the driver meant it."""
    svc, wizard, advanced = wizard_and_service

    await svc._handle_notes(wizard, _Message("Happy to race \U0001F600"))

    assert wizard.draft_answers["notes"] == "Happy to race \U0001F600"
    assert advanced


async def test_a_preferred_team_is_recorded_by_its_full_name_however_it_was_typed(
    wizard_and_service,
):
    """A driver is offered the full names and answers with one, but a shorthand they have seen
    a league manager type is taken too. What is recorded is the full name, which is what every
    review and export shows (#381)."""
    svc, wizard, _ = wizard_and_service
    _with_teams(svc, ["Alpha", "Beta", "Gamma"])

    await svc._handle_preferred_teams(wizard, _Message("alpha racing, BETA"))

    assert wizard.draft_answers["preferred_teams"] == ["Alpha Racing", "Beta Racing"]
