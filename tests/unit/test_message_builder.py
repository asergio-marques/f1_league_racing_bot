"""Unit tests for message_builder slot-simplification helpers.

FR-024 (amended 2026-03-04):
  - When all drawn slots for a session are the exact same weather type AND len > 1:
      forecast: single type label (e.g. "Clear")
      log:      "<type> (draws: <slot>, <slot>, ...)"
  - Otherwise: existing arrow-joined format.
  - Single-slot sessions (len == 1) are EXEMPT — no simplification treatment.
"""

import pytest
from utils.message_builder import format_slots_for_forecast, format_slots_for_log


# ---------------------------------------------------------------------------
# format_slots_for_forecast
# ---------------------------------------------------------------------------

class TestFormatSlotsForForecast:

    # --- all-same multi-slot: each canonical type ---

    def test_all_same_clear(self):
        assert format_slots_for_forecast(["Clear", "Clear", "Clear"]) == "Clear"

    def test_all_same_light_cloud(self):
        assert format_slots_for_forecast(["Light Cloud", "Light Cloud"]) == "Light Cloud"

    def test_all_same_overcast(self):
        assert format_slots_for_forecast(["Overcast", "Overcast", "Overcast"]) == "Overcast"

    def test_all_same_wet(self):
        assert format_slots_for_forecast(["Wet", "Wet"]) == "Wet"

    def test_all_same_very_wet(self):
        assert format_slots_for_forecast(["Very Wet", "Very Wet", "Very Wet", "Very Wet"]) == "Very Wet"

    # --- mixed types: no simplification ---

    def test_mixed_two_types(self):
        result = format_slots_for_forecast(["Clear", "Wet", "Clear"])
        assert result == "*Clear* → *Wet* → *Clear*"

    def test_mixed_all_different(self):
        result = format_slots_for_forecast(["Clear", "Light Cloud", "Overcast"])
        assert result == "*Clear* → *Light Cloud* → *Overcast*"

    def test_nearly_same_last_differs(self):
        result = format_slots_for_forecast(["Clear", "Clear", "Wet"])
        assert result == "*Clear* → *Clear* → *Wet*"

    # --- single-slot exempt ---

    def test_single_slot_clear(self):
        # single slot: return as plain label, no italic wrapper, no arrow
        assert format_slots_for_forecast(["Clear"]) == "Clear"

    def test_single_slot_wet(self):
        assert format_slots_for_forecast(["Wet"]) == "Wet"

    def test_single_slot_does_not_get_parens(self):
        result = format_slots_for_forecast(["Overcast"])
        assert "draws" not in result
        assert "→" not in result


# ---------------------------------------------------------------------------
# format_slots_for_log
# ---------------------------------------------------------------------------

class TestFormatSlotsForLog:

    # --- all-same multi-slot: simplified + raw draws in parens ---

    def test_all_same_clear(self):
        result = format_slots_for_log(["Clear", "Clear", "Clear"])
        assert result == "Clear (draws: Clear, Clear, Clear)"

    def test_all_same_light_cloud(self):
        result = format_slots_for_log(["Light Cloud", "Light Cloud"])
        assert result == "Light Cloud (draws: Light Cloud, Light Cloud)"

    def test_all_same_overcast(self):
        result = format_slots_for_log(["Overcast", "Overcast", "Overcast"])
        assert result == "Overcast (draws: Overcast, Overcast, Overcast)"

    def test_all_same_wet(self):
        result = format_slots_for_log(["Wet", "Wet"])
        assert result == "Wet (draws: Wet, Wet)"

    def test_all_same_very_wet_four_slots(self):
        result = format_slots_for_log(["Very Wet", "Very Wet", "Very Wet", "Very Wet"])
        assert result == "Very Wet (draws: Very Wet, Very Wet, Very Wet, Very Wet)"

    # --- mixed types: plain arrow-joined, no parens ---

    def test_mixed_two_types(self):
        result = format_slots_for_log(["Clear", "Wet"])
        assert result == "Clear → Wet"

    def test_mixed_three_types(self):
        result = format_slots_for_log(["Clear", "Light Cloud", "Overcast"])
        assert result == "Clear → Light Cloud → Overcast"

    def test_nearly_same_last_differs(self):
        result = format_slots_for_log(["Wet", "Wet", "Clear"])
        assert result == "Wet → Wet → Clear"
        assert "(draws:" not in result

    # --- single-slot exempt ---

    def test_single_slot_overcast(self):
        assert format_slots_for_log(["Overcast"]) == "Overcast"

    def test_single_slot_no_parens(self):
        result = format_slots_for_log(["Clear"])
        assert "draws" not in result
        assert "→" not in result


# ---------------------------------------------------------------------------
# The three shared renderings (042, T013)
#
# Constitution XIV.7 obliges the graphic and the message to draw a shared value from one
# rendering. None of these three was reachable without composing a whole Discord message
# around it until this increment lifted them out.
# ---------------------------------------------------------------------------

from utils.message_builder import (  # noqa: E402
    format_rain_probability,
    format_session_weather_type,
    format_slot_sequence,
    phase1_message,
    session_type_label,
)


class TestFormatRainProbability:
    """The likelihood of rain, rounded to the nearest whole number (FR-023, FR-023a).

    Nothing asserted on this rendering before 042, which is what let it sit at one decimal
    place while the weather module's rule said otherwise.
    """

    def test_a_value_that_is_not_a_whole_percentage_rounds_to_the_nearest(self):
        assert format_rain_probability(0.3047) == "30%"
        assert format_rain_probability(0.3062) == "31%"

    def test_it_rounds_half_up_rather_than_to_even(self):
        # Python's round() would give 12 for 12.5 and 14 for 13.5; half-up gives 13 and 14.
        assert format_rain_probability(0.125) == "13%"
        assert format_rain_probability(0.135) == "14%"

    def test_the_bounds_are_whole(self):
        assert format_rain_probability(0.0) == "0%"
        assert format_rain_probability(1.0) == "100%"

    def test_no_decimal_point_survives(self):
        for raw in (0.0001, 0.5555, 0.9999):
            assert "." not in format_rain_probability(raw)

    def test_the_message_and_the_rendering_agree(self):
        rendered = format_rain_probability(0.3047)
        assert rendered in phase1_message(1, "Spa", 0.3047)


class TestFormatSessionWeatherType:
    """The type drawn for a session — one of exactly three (FR-026)."""

    @pytest.mark.parametrize(
        "raw,expected", [("sunny", "Sunny"), ("mixed", "Mixed"), ("rain", "Rain")]
    )
    def test_the_three_types(self, raw, expected):
        assert format_session_weather_type(raw) == expected


class TestFormatSlotSequence:
    """The sequence as a value, with the channel's emphasis left to the message (FR-029)."""

    def test_a_varying_sequence_carries_no_emphasis(self):
        assert format_slot_sequence(["Clear", "Wet"]) == "Clear → Wet"
        assert "*" not in format_slot_sequence(["Clear", "Light Cloud", "Overcast"])

    def test_the_forecast_message_still_emphasises(self):
        assert format_slots_for_forecast(["Clear", "Wet"]) == "*Clear* → *Wet*"

    def test_the_two_collapse_identically_for_one_weather(self):
        for slots in (["Clear"], ["Wet", "Wet"], ["Very Wet", "Very Wet", "Very Wet"]):
            assert format_slot_sequence(slots) == format_slots_for_forecast(slots)
            assert "*" not in format_slot_sequence(slots)

    def test_stripping_the_emphasis_is_never_the_graphic_s_job(self):
        """The value comes out unadorned; the message adds markup on top of it."""
        plain = format_slot_sequence(["Clear", "Wet", "Overcast"])
        emphasised = format_slots_for_forecast(["Clear", "Wet", "Overcast"])
        assert emphasised.replace("*", "") == plain


class TestSessionTypeLabel:
    """Already shared, and already correct — FR-025 needs no work (research R6)."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("SHORT_SPRINT_QUALIFYING", "Sprint Qualifying"),
            ("LONG_SPRINT_RACE", "Sprint Race"),
            ("SHORT_FEATURE_QUALIFYING", "Feature Qualifying"),
            ("LONG_FEATURE_RACE", "Feature Race"),
            ("SHORT_QUALIFYING", "Qualifying"),
            ("LONG_RACE", "Race"),
            ("FULL_QUALIFYING", "Qualifying"),
            ("FULL_RACE", "Race"),
        ],
    )
    def test_the_length_qualifier_is_stripped(self, raw, expected):
        assert session_type_label(raw) == expected
