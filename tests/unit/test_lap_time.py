"""Unit tests for WizardService._normalise_lap_time edge cases — T051."""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


def _normalise(raw: str):
    """Import and call _normalise_lap_time without instantiating WizardService."""
    from services.wizard_service import WizardService
    return WizardService._normalise_lap_time(raw)


# ---------------------------------------------------------------------------
# Valid input cases
# ---------------------------------------------------------------------------


class TestNormaliseLapTimeValid:
    def test_canonical_dot_separator(self):
        assert _normalise("1:23.456") == "1:23.456"

    def test_colon_ms_normalised_to_dot(self):
        assert _normalise("1:23:456") == "1:23.456"

    def test_zero_pad_one_digit_ms(self):
        # "1:23.4" → zero-pad → "1:23.400"
        assert _normalise("1:23.4") == "1:23.400"

    def test_zero_pad_two_digit_ms(self):
        # "1:23.45" → zero-pad → "1:23.450"
        assert _normalise("1:23.45") == "1:23.450"

    def test_half_up_round_four_digit_ms_rounds_up(self):
        # "1:23.4567" → round to 3 digits → 456.7 → 457 → "1:23.457"
        assert _normalise("1:23.4567") == "1:23.457"

    def test_half_up_round_four_digit_ms_rounds_down(self):
        # "1:23.4561" → 456.1 → 456 → "1:23.456"
        assert _normalise("1:23.4561") == "1:23.456"

    def test_half_up_round_exactly_half(self):
        # "1:23.4565" → 456.5 → 457 (half-up) → "1:23.457"
        assert _normalise("1:23.4565") == "1:23.457"

    def test_strip_leading_whitespace(self):
        assert _normalise("  1:23.456") == "1:23.456"

    def test_strip_trailing_whitespace(self):
        assert _normalise("1:23.456  ") == "1:23.456"

    def test_strip_both_sides(self):
        assert _normalise("  1:23.456  ") == "1:23.456"

    def test_zero_minutes_valid(self):
        # 0-minute laps shouldn't appear in F1 but should still parse
        assert _normalise("0:59.999") == "0:59.999"

    def test_ms_carry_over_rounds_to_next_second(self):
        # "1:23.9997" → 999.7 → rounds to 1000 → carry-over → "1:24.000"
        assert _normalise("1:23.9997") == "1:24.000"


# ---------------------------------------------------------------------------
# Invalid input cases — should return None
# ---------------------------------------------------------------------------


class TestNormaliseLapTimeInvalid:
    def test_no_minutes_separator_returns_none(self):
        assert _normalise("23.456") is None

    def test_letters_in_time_returns_none(self):
        assert _normalise("1:2a.456") is None

    def test_empty_string_returns_none(self):
        assert _normalise("") is None

    def test_seconds_only_returns_none(self):
        assert _normalise("23:456") is None  # ambiguous without proper format

    def test_extra_colons_returns_none(self):
        # Two colons but wrong positions
        assert _normalise("1:2:3:4") is None

    def test_negative_value_returns_none(self):
        assert _normalise("-1:23.456") is None
