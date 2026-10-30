"""The signup wizard's typed steps — what a driver types, and what the wizard accepts.

Issue #208. These steps are the input boundary: the one place a driver's own words become the
league's record. `tests/signup/test_wizard_availability.py` covers the availability step
(issue #126) and `tests/signup/test_lap_time.py` the strict lap-time form (#362); the rest —
nationality, platform, platform ID, driver type, preferred teams, preferred teammate, lap times
and notes — are here.

On the change queue (#439, slice 6b, C81) a typed answer is judged by `judge_answer`, pure: it
gives the answers to save (`Accepted.answers`), or the refusal (`Refused.reply`) and the step
its log line names (`Refused.step`), and saves and posts nothing itself. `signup.answer`'s jobs
save, post and write the line (`tests/signup/test_signup_answer_change.py`). **The pairing is
the thing worth pinning:** a refusal gives no answer to save, and an acceptance gives the right
value, so rubbish never reaches the signup and no question is skipped.

Two rules here are the driver's, not the bot's, and are easy to tidy away:

- **A rejection must quote the valid options.** The driver is typing into a private channel
  with no form validation and no list in front of them; an error that does not say what would
  have been accepted leaves them guessing.
- **"No preference" is an answer, not an absence.** Preferred teams and preferred teammate
  both accept it, and it is accepted rather than treated as empty input.

The lap-time step carries the extra rule that a screenshot may be required, and that the
requirement is checked *before* the time is parsed — a driver who typed a good time without
the image must be told about the image, not told their time was fine.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

# The league's teams as the wizard froze them at Sign Up: the full names a driver is offered,
# and each team's shorthand, which a league manager types and a driver may type too (#381).
FULL_NAMES = ["Alpha Racing", "Beta Racing", "Gamma Racing", "Delta Racing"]
SHORTHANDS = {"Alpha": "Alpha Racing", "Beta": "Beta Racing", "Gamma": "Gamma Racing",
              "Delta": "Delta Racing"}


def _typed(content: str, *, attachments: list | None = None):
    """A message Alex typed: its text, and any attachment (a lap-time screenshot)."""
    return SimpleNamespace(content=content, attachments=attachments or [])


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
def wizard():
    """Alex's wizard: nationality required, Time Trial times on tracks 1 and 2 with no
    screenshot asked, one availability slot, the four teams above; nothing answered yet. Each
    test sets the step it judges."""
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

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
            team_names=list(FULL_NAMES),
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at=None,
    )
    # Set apart from the constructor: the snapshot's shorthands are the build's (C81).
    wizard.config_snapshot.team_shorthands = dict(SHORTHANDS)  # type: ignore[attr-defined]
    return wizard


def _judge(wizard, state: str, content: str, *, attachments: list | None = None):
    """Judge *content* typed at *state* (the state's name without COLLECTING_)."""
    from leaguebot.signup.models.signup_module import WizardState
    from leaguebot.signup.services.wizard_changes import judge_answer

    wizard.wizard_state = WizardState[f"COLLECTING_{state}"]
    return judge_answer(wizard, _typed(content, attachments=attachments))


def _accepted(judged) -> dict:
    """The answers an accepted judgement gives to save."""
    from leaguebot.signup.services.wizard_changes import Accepted

    assert isinstance(judged, Accepted), judged
    return judged.answers


def _refused(judged) -> str:
    """The reply a refused judgement gives, after checking it gives nothing to save."""
    from leaguebot.signup.services.wizard_changes import Refused

    assert isinstance(judged, Refused), judged
    assert not hasattr(judged, "answers"), "a refusal gives nothing to save"
    return judged.reply


# ---------------------------------------------------------------------------
# Nationality
# ---------------------------------------------------------------------------


def test_a_nationality_is_stored_as_its_adjective(wizard):
    """A driver may type either the country or the adjective; one form is stored, so the
    signup list does not read as a mixture of "Germany" and "German"."""
    assert _accepted(_judge(wizard, "NATIONALITY", "Germany"))["nationality"] == "German"
    assert wizard.draft_answers == {}, "judging saves nothing: its job does"


def test_an_unknown_nationality_is_refused_with_examples(wizard):
    assert "British" in _refused(_judge(wizard, "NATIONALITY", "xyzzy"))


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", ["Steam", "steam", "STEAM"])
def test_a_platform_is_matched_regardless_of_case(wizard, typed):
    """Stored in the bot's own casing, so the signup list is consistent however it was
    typed."""
    assert _accepted(_judge(wizard, "PLATFORM", typed))["platform"] == "Steam"


def test_an_unknown_platform_is_refused_listing_the_choices(wizard):
    reply = _refused(_judge(wizard, "PLATFORM", "Dreamcast"))
    for option in ("Steam", "EA", "Xbox", "PlayStation"):
        assert option in reply


# ---------------------------------------------------------------------------
# Platform ID
# ---------------------------------------------------------------------------


def test_a_platform_id_is_stored_trimmed(wizard):
    assert _accepted(_judge(wizard, "PLATFORM_ID", "  lh44  "))["platform_id"] == "lh44"


def test_an_empty_platform_id_is_refused(wizard):
    """Whitespace only. The ID is how a league finds the driver in-game, so an empty one
    is worse than no answer — it looks answered."""
    assert "cannot be empty" in _refused(_judge(wizard, "PLATFORM_ID", "   "))


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
def test_a_driver_type_is_matched_regardless_of_case(wizard, typed, stored):
    assert _accepted(_judge(wizard, "DRIVER_TYPE", typed))["driver_type"] == stored


def test_an_unknown_driver_type_is_refused_listing_the_choices(wizard):
    reply = _refused(_judge(wizard, "DRIVER_TYPE", "Test Driver"))
    assert "Full-Time Driver" in reply
    assert "Reserve Driver" in reply


# ---------------------------------------------------------------------------
# Preferred teams
# ---------------------------------------------------------------------------


def test_preferred_teams_are_stored_in_the_order_given(wizard):
    """The order is the driver's preference order, which the placement reads, so it is not
    the bot's to sort."""
    answers = _accepted(_judge(wizard, "PREFERRED_TEAMS", "Gamma Racing, Alpha Racing"))
    assert answers["preferred_teams"] == ["Gamma Racing", "Alpha Racing"]


def test_preferred_teams_may_be_separated_by_newlines(wizard):
    """A driver reading a list of teams will often paste them one per line."""
    answers = _accepted(_judge(wizard, "PREFERRED_TEAMS", "Alpha Racing\nBeta Racing"))
    assert answers["preferred_teams"] == ["Alpha Racing", "Beta Racing"]


def test_no_preference_for_teams_is_an_answer_not_an_absence(wizard):
    """It gives an empty list to save. Treating it as empty input would ask the question again
    and the driver would have no way past it."""
    assert _accepted(_judge(wizard, "PREFERRED_TEAMS", "no preference"))["preferred_teams"] == []


def test_more_than_three_preferred_teams_is_refused(wizard):
    reply = _refused(_judge(wizard, "PREFERRED_TEAMS", "Alpha, Beta, Gamma, Delta"))
    assert "up to 3" in reply


def test_an_unknown_team_is_refused_listing_the_valid_ones(wizard):
    reply = _refused(_judge(wizard, "PREFERRED_TEAMS", "Alpha Racing, Ferrari"))
    assert "Ferrari" in reply
    assert "Alpha Racing" in reply


def test_the_reserve_team_is_not_offered_as_a_preference(wizard):
    """A driver does not choose to be a reserve by naming the Reserve team — that is what
    the driver-type step is for. The wizard froze only the teams a driver may prefer, so the
    Reserve team is refused as unknown."""
    _refused(_judge(wizard, "PREFERRED_TEAMS", "Reserve"))


def test_a_preferred_team_is_recorded_by_its_full_name_however_it_was_typed(wizard):
    """A driver is offered the full names and answers with one, but a shorthand they have seen
    a league manager type is taken too, in any case. What is recorded is the full name, which
    is what every review and export shows (#381)."""
    answers = _accepted(_judge(wizard, "PREFERRED_TEAMS", "alpha racing, BETA"))
    assert answers["preferred_teams"] == ["Alpha Racing", "Beta Racing"]


# ---------------------------------------------------------------------------
# Preferred teammate
# ---------------------------------------------------------------------------


def test_a_preferred_teammate_is_stored_as_typed(wizard):
    """Free text — the driver names someone who may not have signed up yet, so it cannot
    be validated against anything."""
    assert _accepted(_judge(wizard, "PREFERRED_TEAMMATE", "Lewis"))["preferred_teammate"] == "Lewis"


def test_no_preference_for_a_teammate_stores_nothing(wizard):
    answers = _accepted(_judge(wizard, "PREFERRED_TEAMMATE", "No Preference"))
    assert answers["preferred_teammate"] is None


# ---------------------------------------------------------------------------
# Lap times
# ---------------------------------------------------------------------------


def test_a_lap_time_is_stored_against_the_current_track(wizard):
    assert _accepted(_judge(wizard, "LAP_TIME", "1:23.456"))["lap_times"] == {"1": "1:23.456"}


def test_each_lap_time_advances_to_the_next_track(wizard):
    """The index is what decides which track a time belongs to. With the first track timed and
    the wizard moved on to the second, the next time is given against track 2, not track 1,
    so a driver never overwrites their first time."""
    wizard.draft_answers["lap_times"] = {"1": "1:23.456"}
    wizard.current_lap_track_index = 1

    answers = _accepted(_judge(wizard, "LAP_TIME", "1:24.000"))

    assert answers["lap_times"] == {"1": "1:23.456", "2": "1:24.000"}


def test_a_malformed_lap_time_is_refused_with_the_format(wizard):
    assert "M:ss.mmm" in _refused(_judge(wizard, "LAP_TIME", "about a minute and a half"))


def test_a_rejected_lap_time_does_not_move_to_the_next_track(wizard):
    """Otherwise a typo would silently cost the driver that track's time."""
    _refused(_judge(wizard, "LAP_TIME", "nonsense"))
    assert wizard.current_lap_track_index == 0


def test_a_required_screenshot_is_demanded_before_the_time_is_read(wizard):
    """The image check runs first deliberately. A driver who typed a perfectly good time
    with no screenshot must be told about the screenshot — being told their time was
    invalid would send them to fix the wrong thing."""
    wizard.config_snapshot.time_image_required = True
    assert "screenshot" in _refused(_judge(wizard, "LAP_TIME", "1:23.456"))


def test_a_lap_time_with_its_screenshot_is_accepted(wizard):
    wizard.config_snapshot.time_image_required = True
    answers = _accepted(_judge(wizard, "LAP_TIME", "1:23.456", attachments=[object()]))
    assert answers["lap_times"] == {"1": "1:23.456"}


def test_the_rejection_names_the_kind_of_time_being_asked_for(wizard):
    """A league may ask for a time trial or a short qualification, and the driver sets
    them in different places in the game."""
    wizard.config_snapshot.time_type = "SHORT_QUALI"
    assert "Short Qualification" in _refused(_judge(wizard, "LAP_TIME", "nonsense"))


def test_a_lap_time_past_the_last_track_advances_rather_than_storing(wizard):
    """Defensive: the index should never be past the end, and if it is, the answer is
    accepted so the wizard moves on, with no time stored, rather than raising an IndexError
    inside a driver's private channel."""
    wizard.current_lap_track_index = 99
    assert "lap_times" not in _accepted(_judge(wizard, "LAP_TIME", "1:23.456"))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def test_a_note_is_stored(wizard):
    answers = _accepted(_judge(wizard, "NOTES", "Prefer not to race Mondays"))
    assert answers["notes"] == "Prefer not to race Mondays"


def test_no_notes_stores_nothing(wizard):
    assert _accepted(_judge(wizard, "NOTES", "No Notes"))["notes"] is None


def test_a_note_over_fifty_characters_is_refused(wizard):
    """Fifty characters is the limit the signup specification sets for a note, which is
    quoted as text in the review panel and the unassigned list."""
    assert "50 characters" in _refused(_judge(wizard, "NOTES", "x" * 51))


def test_a_note_of_exactly_fifty_characters_is_accepted(wizard):
    """Sits the other side of the bound from the test above."""
    assert _accepted(_judge(wizard, "NOTES", "x" * 50))["notes"] == "x" * 50


# ---------------------------------------------------------------------------
# A group mention in a free-text answer (#362)
# ---------------------------------------------------------------------------


def test_a_signup_note_mentioning_everyone_is_refused(wizard):
    """The review panel quotes the note to the channel, where the bot's own permission to
    mention everybody would carry the ping."""
    assert "notes" in _refused(_judge(wizard, "NOTES", "@everyone please check"))


def test_a_signup_note_with_an_emoji_is_kept(wizard):
    """Shown only as text, where an emoji reads as the driver meant it."""
    answers = _accepted(_judge(wizard, "NOTES", "Happy to race \U0001F600"))
    assert answers["notes"] == "Happy to race \U0001F600"


# ---------------------------------------------------------------------------
# The log channel: a typed answer records only its refusal (#482)
# ---------------------------------------------------------------------------

# (the step as the wizard's state names it, the step as the line names it, what is typed,
# whether a screenshot is required)
_REFUSED_ANSWERS = [
    ("NATIONALITY", "Nationality", "xyzzy", False),
    ("PLATFORM", "Platform", "Dreamcast", False),
    ("PLATFORM_ID", "Platform ID", "   ", False),
    ("PLATFORM_ID", "Platform ID", "@here", False),
    ("AVAILABILITY", "Availability", "one", False),
    ("AVAILABILITY", "Availability", "9", False),
    ("AVAILABILITY", "Availability", ",", False),
    ("DRIVER_TYPE", "Driver Type", "Pro", False),
    ("PREFERRED_TEAMS", "Preferred Teams", "Alpha, Beta, Gamma, Delta", False),
    ("PREFERRED_TEAMS", "Preferred Teams", "Alpha, Ferrari", False),
    ("PREFERRED_TEAMMATE", "Preferred Teammate", "<@&987654321098765432>", False),
    ("LAP_TIME", "Lap Times", "1:23.456", True),
    ("LAP_TIME", "Lap Times", "fast", False),
    ("NOTES", "Notes", "x" * 51, False),
    ("NOTES", "Notes", "@everyone please check", False),
]


@pytest.mark.parametrize(
    "state,step,typed,screenshot_required",
    _REFUSED_ANSWERS,
    ids=[f"{row[1]}: {row[2].strip() or 'blank'}"[:40] for row in _REFUSED_ANSWERS],
)
def test_a_typed_answer_refused_keeps_its_reply_and_writes_one_refusal_line(
    wizard, state, step, typed, screenshot_required
):
    """A refused typed answer gives the reply posted in the driver's channel, starting ❌, and
    the step its one refusal line names (the line itself, "⛔ the <step> step of <driver>'s
    signup wizard refused …", is written by signup.answer's job: test_signup_answer_change),
    and gives nothing to save. The table carries the group mentions a free-text answer refuses
    (#362): `@here` as a platform ID, a role mention as a preferred teammate, `@everyone` in a
    note."""
    from leaguebot.signup.services.wizard_changes import Refused

    wizard.config_snapshot.time_image_required = screenshot_required

    judged = _judge(wizard, state, typed)

    assert isinstance(judged, Refused)
    assert not hasattr(judged, "answers"), "a refusal gives nothing to save"
    assert judged.reply.startswith("❌")
    assert judged.step == step


_ACCEPTED_ANSWERS = [
    ("NATIONALITY", "British"),
    ("PLATFORM", "Steam"),
    ("PLATFORM_ID", "lh44"),
    ("AVAILABILITY", "1"),
    ("DRIVER_TYPE", "Full-Time Driver"),
    ("PREFERRED_TEAMS", "Alpha, Beta"),
    ("PREFERRED_TEAMS", "No preference"),
    ("PREFERRED_TEAMMATE", "Max"),
    ("PREFERRED_TEAMMATE", "No preference"),
    ("LAP_TIME", "1:23.456"),
    ("NOTES", "Happy to race"),
    ("NOTES", "No notes"),
]


@pytest.mark.parametrize(
    "state,typed",
    _ACCEPTED_ANSWERS,
    ids=[f"{row[0].lower()}: {row[1]}" for row in _ACCEPTED_ANSWERS],
)
def test_a_typed_answer_accepted_writes_no_line(wizard, state, typed):
    """A typed answer is a message, not a command, button or form, so an accepted one is no
    refusal and gives no reply and no step for a line: the "Submitted" line is the record of
    what was typed (owner, 2026-09-30, "Refusals only", kept for typed answers)."""
    from leaguebot.signup.services.wizard_changes import Accepted

    assert isinstance(_judge(wizard, state, typed), Accepted)
