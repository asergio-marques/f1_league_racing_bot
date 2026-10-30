"""Unit tests for WizardService pure-logic helpers — T050.

Covers:
  - _normalise_lap_time (also tested exhaustively in test_lap_time.py)
  - _validate_nationality
  - WizardState enum completeness
  - _normalise_lap_time / _validate_nationality as static methods (no bot needed)
"""

from __future__ import annotations

import pytest


def _validate(raw: str):
    from leaguebot.signup.services.wizard_service import WizardService
    return WizardService._validate_nationality(raw)


def _normalise(raw: str):
    from leaguebot.signup.services.wizard_service import WizardService
    return WizardService._normalise_lap_time(raw)


# ---------------------------------------------------------------------------
# _validate_nationality
# ---------------------------------------------------------------------------


class TestValidateNationality:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("British", "British", id="adjective"),
            pytest.param("british", "British", id="adjective-lowercase"),
            pytest.param("BRITISH", "British", id="adjective-uppercase"),
            pytest.param("United Kingdom", "British", id="country"),
            pytest.param("united kingdom", "British", id="country-lowercase"),
            pytest.param("GERMANY", "German", id="country-uppercase"),
            pytest.param("Germany", "German", id="country-stored-as-adjective"),
            pytest.param("USA", "American", id="usa-alias"),
            pytest.param("other", "Other", id="other-lowercase"),
            pytest.param("OTHER", "Other", id="other-uppercase"),
            pytest.param("Other", "Other", id="other-mixed-case"),
            # GB was the old format and must now be refused.
            pytest.param("gb", None, id="iso-code-gb"),
            pytest.param("us", None, id="iso-code-us"),
            pytest.param("xyzzy", None, id="unknown"),
            pytest.param("", None, id="empty"),
            pytest.param("  British  ", "British", id="whitespace-stripped"),
        ],
    )
    def test_a_nationality_is_read_as_its_adjective_or_refused(self, raw, expected):
        assert _validate(raw) == expected


# ---------------------------------------------------------------------------
# WizardState enum completeness
# ---------------------------------------------------------------------------


class TestWizardStateEnum:
    def test_unengaged_exists(self):
        from leaguebot.signup.models.signup_module import WizardState
        assert WizardState.UNENGAGED.value == "UNENGAGED"

    def test_nine_collection_states_exist(self):
        from leaguebot.signup.models.signup_module import WizardState
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
        from leaguebot.signup.models.signup_module import WizardState
        assert len(WizardState) == 10  # UNENGAGED + 9 collection states


# ---------------------------------------------------------------------------
# _normalise_lap_time round-trip correctness
# ---------------------------------------------------------------------------


class TestNormaliseLapTimeRoundTrip:
    """Verify already-normalised values round-trip through _normalise unchanged."""

    def test_already_normalised_roundtrips(self):
        for t in ["1:23.456", "0:59.000", "2:01.999"]:
            assert _normalise(t) == t

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("1:23:456", id="colon-before-thousandths"),
            pytest.param("1:23.5", id="fewer-than-three-digits"),
        ],
    )
    def test_a_time_outside_the_one_form_is_refused(self, raw):
        """Strict since #362: signup reads times as the results paste does."""
        assert _normalise(raw) is None
