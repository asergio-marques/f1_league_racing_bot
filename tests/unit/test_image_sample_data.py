

# ---------------------------------------------------------------------------
# Weather sample data (042, T022)
# ---------------------------------------------------------------------------

class TestWeatherSampleData:
    """Six images across four commands, exercising every case FR-062–FR-065 enumerate."""

    def _drawing(self, key):
        from services.image_sample_data import build_weather_drawing
        return build_weather_drawing(None, key)

    def test_the_rain_likelihood_is_not_a_whole_percentage(self):
        """FR-062 — so that the whole-number rounding is visible in the render."""
        from services.image_sample_data import SAMPLE_RAIN_PROBABILITY

        assert SAMPLE_RAIN_PROBABILITY * 100 != int(SAMPLE_RAIN_PROBABILITY * 100)
        assert self._drawing("weather_p1_template").rain_probability == "30%"

    def test_the_two_phase_two_rounds_show_all_three_weather_types(self):
        """FR-063."""
        types = set()
        for key in ("weather_p2_template", "weather_p2_sprint_template"):
            types |= {s.weather_type for s in self._drawing(key).sessions}
        assert types == {"Sunny", "Mixed", "Rain"}

    def test_the_two_phase_three_rounds_show_all_five_concrete_weathers(self):
        """FR-064."""
        labels = set()
        for key in ("weather_p3_template", "weather_p3_sprint_template"):
            for session in self._drawing(key).sessions:
                labels |= {slot.label for slot in session.slots}
        assert labels == {"Clear", "Light Cloud", "Overcast", "Wet", "Very Wet"}

    def test_the_sprint_round_holds_four_sessions_and_the_endurance_two(self):
        """FR-061 — between them the greatest session count the module can produce."""
        assert self._drawing("weather_p2_sprint_template").session_count == 4
        assert self._drawing("weather_p2_template").session_count == 2
        assert self._drawing("weather_p3_sprint_template").session_count == 4
        assert self._drawing("weather_p3_template").session_count == 2

    def test_the_endurance_race_reaches_four_slots(self):
        """The only session of the module that may be drawn so many."""
        sessions = self._drawing("weather_p3_template").sessions
        assert max(s.slot_count for s in sessions) == 4

    def test_the_phase_three_cases_are_all_present(self):
        """FR-064 — one slot, one weather throughout, differing, and the type's greatest."""
        sessions = self._drawing("weather_p3_sprint_template").sessions
        counts = [s.slot_count for s in sessions]
        assert 1 in counts, "a session of a single slot"

        one_weather = [s for s in sessions if s.slot_count > 1 and len({x.label for x in s.slots}) == 1]
        assert one_weather, "a session all of whose slots carry the same weather"
        assert one_weather[0].summary == one_weather[0].slots[0].label

        differing = [s for s in sessions if len({x.label for x in s.slots}) > 1]
        assert differing, "a session whose slots do not all carry the same weather"

        # The feature race is the longest session a sprint round holds, and reaches its three.
        assert sessions[3].slot_count == 3

    def test_every_sample_is_drawn_for_the_named_test_division(self):
        """FR-060."""
        for key in (
            "weather_p1_template",
            "weather_p2_template",
            "weather_p2_sprint_template",
            "weather_p3_template",
            "weather_p3_sprint_template",
            "weather_mystery_template",
        ):
            drawing = self._drawing(key)
            assert drawing.division_name == "Test Division"
            assert drawing.division_tier == "1"
            assert drawing.season_number == "1"
            assert drawing.round_number == "1"

    def test_the_mystery_sample_holds_no_session_and_no_forecast(self):
        """FR-065."""
        drawing = self._drawing("weather_mystery_template")
        assert drawing.sessions == []
        assert drawing.rain_probability is None
        assert drawing.track_name is None

    def test_no_summary_carries_channel_markup(self):
        for key in ("weather_p3_template", "weather_p3_sprint_template"):
            for session in self._drawing(key).sessions:
                assert "*" not in (session.summary or "")
