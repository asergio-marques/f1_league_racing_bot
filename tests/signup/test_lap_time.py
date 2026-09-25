"""A driver's lap time at signup — `WizardService._normalise_lap_time`.

Read by the shared strict parser (#362, decided 2026-09-21): always a dot and exactly three
digits after it, as the results paste reads times and as the prompt has always shown. Signup
once read `1:23:456` and `1:23.4` and guessed at them; those are now refused and the driver is
asked again, so the two ways a time enters the bot can no longer disagree.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _normalise(raw: str):
    """Call _normalise_lap_time without instantiating WizardService."""
    from leaguebot.signup.services.wizard_service import WizardService
    return WizardService._normalise_lap_time(raw)


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
    assert _normalise(typed) == stored


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
    assert _normalise(typed) is None
