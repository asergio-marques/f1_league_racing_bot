"""Unit tests for WizardService pure-logic helpers — T050.

Covers:
  - _normalise_lap_time (also tested exhaustively in test_lap_time.py)
  - _validate_nationality
  - WizardState enum completeness
  - _normalise_lap_time / _validate_nationality as static methods (no bot needed)
"""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _validate(raw: str):
    from services.wizard_service import WizardService
    return WizardService._validate_nationality(raw)


def _normalise(raw: str):
    from services.wizard_service import WizardService
    return WizardService._normalise_lap_time(raw)


# ---------------------------------------------------------------------------
# _validate_nationality
# ---------------------------------------------------------------------------


class TestValidateNationality:
    def test_two_letter_lowercase_accepted(self):
        assert _validate("gb") == "gb"

    def test_two_letter_uppercase_normalised_to_lowercase(self):
        assert _validate("GB") == "gb"

    def test_mixed_case_normalised(self):
        assert _validate("Gb") == "gb"

    def test_other_accepted_lowercase(self):
        assert _validate("other") == "other"

    def test_other_accepted_uppercase(self):
        assert _validate("OTHER") == "other"

    def test_other_accepted_mixed_case(self):
        assert _validate("Other") == "other"

    def test_one_letter_returns_none(self):
        assert _validate("g") is None

    def test_three_letters_returns_none(self):
        assert _validate("gbr") is None

    def test_empty_string_returns_none(self):
        assert _validate("") is None

    def test_digit_in_code_returns_none(self):
        assert _validate("g1") is None

    def test_whitespace_only_returns_none(self):
        assert _validate("  ") is None

    def test_leading_trailing_whitespace_stripped(self):
        # Validator strips before checking
        assert _validate("  gb  ") == "gb"

    def test_non_ascii_returns_none(self):
        assert _validate("ñe") is None


# ---------------------------------------------------------------------------
# WizardState enum completeness
# ---------------------------------------------------------------------------


class TestWizardStateEnum:
    def test_unengaged_exists(self):
        from models.signup_module import WizardState
        assert WizardState.UNENGAGED.value == "UNENGAGED"

    def test_nine_collection_states_exist(self):
        from models.signup_module import WizardState
        collection_states = [
            WizardState.COLLECTING_NATIONALITY,
            WizardState.COLLECTING_PLATFORM,
            WizardState.COLLECTING_PLATFORM_ID,
            WizardState.COLLECTING_AVAILABILITY,
            WizardState.COLLECTING_DRIVER_TYPE,
            WizardState.COLLECTING_PREFERRED_TEAMS,
            WizardState.COLLECTING_PREFERRED_TEAMMATE,
            WizardState.COLLECTING_LAP_TIME,
            WizardState.COLLECTING_NOTES,
        ]
        assert len(collection_states) == 9

    def test_total_state_count(self):
        from models.signup_module import WizardState
        assert len(WizardState) == 10  # UNENGAGED + 9 collection states


# ---------------------------------------------------------------------------
# _normalise_lap_time round-trip correctness
# ---------------------------------------------------------------------------


class TestNormaliseLapTimeRoundTrip:
    """Verify already-normalised values round-trip through _normalise unchanged."""

    def test_already_normalised_roundtrips(self):
        for t in ["1:23.456", "0:59.000", "2:01.999"]:
            assert _normalise(t) == t

    def test_colon_ms_converts_to_dot(self):
        assert _normalise("1:23:456") == "1:23.456"

    def test_short_ms_pads_to_three(self):
        assert _normalise("1:23.5") == "1:23.500"
