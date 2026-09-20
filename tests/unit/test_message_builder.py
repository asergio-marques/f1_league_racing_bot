"""Unit tests for message_builder slot-simplification helpers.

FR-024 (amended 2026-03-04):
  - When all drawn slots for a session are the exact same weather type AND len > 1:
      forecast: single type label (e.g. "Clear")
      log:      "<type> (draws: <slot>, <slot>, ...)"
  - Otherwise: existing arrow-joined format.
  - Single-slot sessions (len == 1) are EXEMPT — no simplification treatment.
"""

import re

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
    PHASE_DESCRIPTIONS,
    format_rain_probability,
    format_session_weather_type,
    format_slot_sequence,
    phase1_message,
    phase2_message,
    phase3_message,
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


# ---------------------------------------------------------------------------
# How a forecast names itself (issue #112)
#
# The three posts described their own timing in fixed wording — "(5 days out)", "(2 days
# out)", "(2 hours out)" — and pointed forward to fixed ones. None of the builders was given
# the league's configured deadlines, so every league but one left on the defaults was told
# the wrong time. Nothing in the suite asserted on a heading, which is how it sat there.
# ---------------------------------------------------------------------------


def _phase1() -> str:
    return phase1_message(1, "Spa", 0.30)


def _phase2() -> str:
    return phase2_message(1, "Spa", [("Qualifying", "rain"), ("Race", "mixed")])


def _phase3() -> str:
    return phase3_message(
        1, "Spa", [("Qualifying", ["Clear"]), ("Race", ["Wet", "Very Wet"])]
    )


class TestAForecastNamesNoHorizon:
    """No forecast describes when it was posted or when the next one arrives (#112)."""

    @pytest.mark.parametrize(
        "message", [_phase1(), _phase2(), _phase3()], ids=["phase1", "phase2", "phase3"]
    )
    def test_no_message_names_a_horizon(self, message):
        """The wording the issue reported, in every form it took."""
        for horizon in ("days out", "hours out", "T−2", "T-2", "T−5", "T-5"):
            assert horizon not in message

    @pytest.mark.parametrize(
        "message", [_phase1(), _phase2(), _phase3()], ids=["phase1", "phase2", "phase3"]
    )
    def test_no_message_counts_days_or_hours_at_all(self, message):
        """A number of days or hours in any phrasing, not just the three literals replaced.

        The fix is that a forecast says nothing about its own timing — a rewording that
        reintroduced "in 5 days" or "2 hours before" would pass the test above and still be
        the defect.
        """
        assert not re.search(r"\d+\s*(day|hour)", message, re.IGNORECASE)

    @pytest.mark.parametrize("phase", [1, 2, 3])
    def test_each_message_is_titled_by_its_phase_description(self, phase):
        message = {1: _phase1, 2: _phase2, 3: _phase3}[phase]()
        heading = message.splitlines()[0]
        assert PHASE_DESCRIPTIONS[phase] in heading

    def test_no_message_names_a_phase_number(self):
        """The heading read "Phase 1", "Phase 2", "Phase 3"; the description replaces it.

        The graphics have never drawn a phase number (FR-011, FR-022) and the text no longer
        does either, the two now naming a phase from one constant.
        """
        for message in (_phase1(), _phase2(), _phase3()):
            assert "Phase 1" not in message
            assert "Phase 2" not in message
            assert "Phase 3" not in message

    def test_the_message_and_the_graphic_name_the_phase_alike(self):
        """One constant serves both, so a fork cannot open between them (XIV.7)."""
        from services.image_weather_service import (
            PHASE_DESCRIPTIONS as graphic_descriptions,
        )

        assert graphic_descriptions is PHASE_DESCRIPTIONS

    def test_the_earlier_phases_still_promise_a_closer_forecast(self):
        """The weather spec obliges phase 1 to say a more detailed forecast follows.

        Dropping the horizon does not drop the promise — only the hour it named.
        """
        assert "will follow" in _phase1()
        assert "will follow" in _phase2()

    def test_the_final_forecast_promises_nothing_further(self):
        assert "will follow" not in _phase3()

    def test_phase_2_says_the_final_forecast_is_the_more_accurate(self):
        """The qualitative statement that replaced the timing (decided 2026-09-20)."""
        assert "more accurate" in _phase2()
