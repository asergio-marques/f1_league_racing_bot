"""A driver's lap time at signup, as the wizard's answer is judged.

Read by the shared strict parser (#362, decided 2026-09-21): always a dot and exactly three
digits after it, as the results paste reads times and as the prompt has always shown. Signup
once read `1:23:456` and `1:23.4` and guessed at them; those are now refused and the driver is
asked again, so the two ways a time enters the bot can no longer disagree.

On the change queue (#439, slice 6b, C81) a typed answer is judged by `judge_answer`, pure: it
gives the answers to save, or the refusal and the step its line names, and saves and posts
nothing itself.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

NO_JUDGE_ANSWER = (
    "#439: a typed lap time is judged inside the wizard service's handler, which saves it or "
    "posts its refusal at once; there is no pure judge_answer giving the answers to save or the "
    "refusal"
)


def _judge(raw: str):
    """A wizard asking for its one Time Trial lap (track 01), no screenshot asked and nothing
    answered yet, judges *raw* typed by the driver."""
    from leaguebot.signup.models.signup_module import (
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.signup.services.wizard_changes import judge_answer

    wizard = SignupWizardRecord(
        id=1,
        discord_user_id="7",
        wizard_state=WizardState.COLLECTING_LAP_TIME,
        signup_channel_id=99,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=["01"],
            slots=[],
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at=None,
    )
    return judge_answer(wizard, SimpleNamespace(content=raw, attachments=[]))


@pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
@pytest.mark.parametrize(
    "typed, stored",
    [
        ("1:23.456", "1:23.456"),
        ("  1:23.456  ", "1:23.456"),
        ("0:59.999", "0:59.999"),
        ("58.123", "0:58.123"),
        ("12:00.000", "12:00.000"),
    ],
)
def test_a_lap_time_in_the_strict_form_is_stored_as_m_ss_mmm(typed, stored):
    from leaguebot.signup.services.wizard_changes import Accepted

    judged = _judge(typed)
    assert isinstance(judged, Accepted)
    assert judged.answers["lap_times"] == {"01": stored}


@pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
@pytest.mark.parametrize(
    "typed",
    [
        "1:23:456",     # a colon before the thousandths
        "1:23.4",       # one digit after the dot
        "1:23.45",      # two
        "1:23.4567",    # four
        "1:23",         # none
        "1:60.000",     # sixty seconds
        "1:2a.456",
        "-1:23.456",
        "1:2:3:4",
        "",
        "   ",
    ],
)
def test_a_lap_time_outside_the_strict_form_is_refused(typed):
    """Refused, not guessed: the driver is asked again rather than seeded on a guess."""
    from leaguebot.signup.services.wizard_changes import Refused

    judged = _judge(typed)
    assert isinstance(judged, Refused)
    assert judged.step == "Lap Times"
    assert judged.reply == "❌ Invalid Time Trial time. Use format `M:ss.mmm` (e.g. `1:23.456`)."
