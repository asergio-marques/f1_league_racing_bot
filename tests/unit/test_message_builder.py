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
