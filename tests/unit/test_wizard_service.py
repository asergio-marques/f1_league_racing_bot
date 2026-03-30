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
    def test_full_adjective_accepted(self):
        assert _validate("British") == "British"

    def test_full_adjective_case_insensitive(self):
        assert _validate("british") == "British"
        assert _validate("BRITISH") == "British"

    def test_country_name_accepted(self):
        assert _validate("United Kingdom") == "British"

    def test_country_name_case_insensitive(self):
        assert _validate("united kingdom") == "British"
        assert _validate("GERMANY") == "German"

    def test_other_lowercase_accepted(self):
        assert _validate("other") == "Other"

    def test_other_uppercase_accepted(self):
        assert _validate("OTHER") == "Other"

    def test_other_mixed_case_accepted(self):
        assert _validate("Other") == "Other"

    def test_two_letter_iso_code_rejected_gb(self):
        """GB was the old format — must now be rejected."""
        assert _validate("gb") is None

    def test_two_letter_iso_code_rejected_us(self):
        assert _validate("us") is None

    def test_unknown_string_rejected(self):
        assert _validate("xyzzy") is None

    def test_empty_string_rejected(self):
        assert _validate("") is None

    def test_leading_trailing_whitespace_stripped(self):
        assert _validate("  British  ") == "British"

    def test_german_country_name_stored_as_adjective(self):
        assert _validate("Germany") == "German"

    def test_usa_alias_accepted(self):
        assert _validate("USA") == "American"


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
