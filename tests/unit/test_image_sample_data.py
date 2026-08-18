

# ---------------------------------------------------------------------------
# Weather sample data (042, T022)
# ---------------------------------------------------------------------------

class TestWeatherSampleData:
    """Six images across four commands, exercising every case FR-062–FR-065 enumerate."""

    def _drawing(self, key):
        from tests.support.image_sample_data import build_weather_drawing
        return build_weather_drawing(None, key)

    def test_the_rain_likelihood_is_not_a_whole_percentage(self):
        """FR-062 — so that the whole-number rounding is visible in the render."""
        from tests.support.image_sample_data import SAMPLE_RAIN_PROBABILITY

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


# ══════════════════════════════════════════════════════════════════════════
# 043 — the six verdicts `/images test verdicts` draws (T021-T023)
#
# specs/043-verdicts-image-generation/contracts/verdicts-posting.md § The test command.
# ══════════════════════════════════════════════════════════════════════════


VERDICT_TEMPLATE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" '
    b'xmlns:xlink="http://www.w3.org/1999/xlink" width="1200" height="675">'
    b'<text id="season_number">1</text>'
    b'<text id="division_name">D</text>'
    b'<text id="division_tier">1</text>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
    b'<g id="session_name_group"><text id="session_name">S</text></g>'
    b'<text id="verdict_stage">V</text>'
    b'<text id="driver_name">N</text>'
    b'<image id="driver_flag" xlink:href="x.svg"/>'
    b'<g id="team_name_group">'
    b'<text id="team_name">T</text><image id="team_image" xlink:href="x.svg"/>'
    b"</g>"
    b'<text id="penalty">P</text>'
    b'<text id="description">De</text>'
    b'<text id="justification">J</text>'
    b"</svg>"
)


def _verdict_root():
    from utils.svg_document import parse_svg_bytes

    return parse_svg_bytes(VERDICT_TEMPLATE)


def _all_verdict_drawings():
    from tests.support.image_sample_data import SAMPLE_VERDICT_CASES, build_verdict_drawing

    return {case: build_verdict_drawing(None, case=case) for case in SAMPLE_VERDICT_CASES}


def test_six_verdict_cases_are_drawn_from_the_one_template():
    from tests.support.image_sample_data import SAMPLE_VERDICT_CASES

    assert len(SAMPLE_VERDICT_CASES) == 6
    assert set(SAMPLE_VERDICT_CASES) == {
        "penalty_added_sprint",
        "penalty_removed",
        "penalty_dsq",
        "appeal",
        "autosack",
        "autoreserve",
    }


def test_the_six_cases_cover_all_three_kinds_and_both_signs_of_a_time_penalty():
    from services.image_verdict_service import VerdictKind

    drawings = _all_verdict_drawings()

    assert {d.kind for d in drawings.values()} == set(VerdictKind)
    assert drawings["penalty_added_sprint"].penalty == "5 seconds added"
    assert drawings["penalty_removed"].penalty == "3 seconds removed"
    assert drawings["penalty_dsq"].penalty == "Disqualified"
    assert drawings["appeal"].stage == "Appeal"


def test_one_case_draws_a_sprint_session_so_its_naming_can_be_judged():
    drawings = _all_verdict_drawings()
    assert drawings["penalty_added_sprint"].session_name == "Sprint Race"


def test_the_two_attendance_sanctions_name_no_session_and_no_team():
    drawings = _all_verdict_drawings()
    for case in ("autosack", "autoreserve"):
        assert drawings[case].session_name is None
        assert drawings[case].team_name is None


def test_the_composed_justification_reaches_the_canvas_carrying_a_name_alone():
    """The attendance module writes `<@id> (name)`; the graphic mentions nobody."""
    drawings = _all_verdict_drawings()
    for case in ("autosack", "autoreserve"):
        justification = drawings[case].justification
        assert "<@" not in justification
        assert justification.startswith("Ada Lovelace has reached")
        assert "Ada Lovelace (Ada Lovelace)" not in justification


def test_the_fabricated_free_text_covers_five_lengths():
    """One line, exactly full, slightly over, wildly over, and neither entered."""
    from tests.support.image_sample_data import VERDICT_TEXT_NOT_PROVIDED

    drawings = _all_verdict_drawings()
    texts = [d.description for d in drawings.values()] + [
        d.justification for d in drawings.values()
    ]
    lengths = sorted(len(t) for t in texts)

    assert min(lengths) < 60, "no single-line case"
    assert any(150 < length < 400 for length in lengths), "no box-filling case"
    assert max(lengths) > 3000, "no case an order of magnitude over"
    assert VERDICT_TEXT_NOT_PROVIDED in texts, "no case where the steward entered neither"


def test_the_not_provided_text_carries_no_channel_markup():
    """The message italicises it; the graphic draws the value the markup adorned."""
    from tests.support.image_sample_data import VERDICT_TEXT_NOT_PROVIDED

    assert "*" not in VERDICT_TEXT_NOT_PROVIDED
    assert "_" not in VERDICT_TEXT_NOT_PROVIDED


def test_one_fabricated_text_carries_the_paragraphs_a_steward_wrote():
    drawings = _all_verdict_drawings()
    assert any("\n\n" in d.justification for d in drawings.values())


def test_the_nationalities_are_ones_the_signup_wizard_accepts():
    from tests.support.image_sample_data import SAMPLE_LINEUP_NATIONALITIES

    drawings = _all_verdict_drawings()
    used = {d.driver_nationality for d in drawings.values()}
    assert used <= set(SAMPLE_LINEUP_NATIONALITIES)
    assert "Other" in used, "the value recorded for a driver who stated none must appear"


def test_build_spec_draws_each_case_from_the_verdicts_template():
    from tests.support.image_sample_data import SAMPLE_VERDICT_CASES, build_spec

    for case in SAMPLE_VERDICT_CASES:
        spec = build_spec("verdicts_template", _verdict_root(), variant=case)
        assert spec.image_type == "verdicts_template"
        assert spec.text["division_name"] == "Test Division"
        assert spec.text["verdict_stage"]


def test_build_spec_empties_the_session_and_team_for_a_sanction():
    from tests.support.image_sample_data import build_spec

    spec = build_spec("verdicts_template", _verdict_root(), variant="autosack")
    assert "session_name" in spec.empty_quietly
    assert "team_name" in spec.empty_quietly
    assert "team_image" in spec.remove


# The three tests that stood here asserted on the wiring of the withdrawn
# `/images test <kind>` command — its `_SAMPLE_VARIANTS` table and the source text of its
# `needs_tracks` / `needs_teams` guards. That command is replaced by the eleven previews of
# feature 045, whose refusals are covered against the resolution path itself in
# `tests/unit/test_image_preview_service.py` rather than by reading the cog's source.
#
# The track guard has no successor: a preview is drawn against a real round, which names a
# real circuit, so "the server's track list is empty" is no longer a state a preview can be
# in. The team guard's successor is `require_teams`, covered by
# `test_a_division_with_only_a_reserve_team_is_refused`.


def test_every_sample_nationality_maps_to_a_country():
    """The test renders must resolve country-named flag files (044, T014).

    The fabricated drivers feed nationalities through the drawing services, which
    map them to countries. A nationality added here that the map does not carry
    would silently draw the flag directory's fallback on every test render.
    """
    from tests.support.image_sample_data import SAMPLE_LINEUP_NATIONALITIES
    from utils.country_data import NATIONALITY_COUNTRIES, country_for_nationality

    for nationality in SAMPLE_LINEUP_NATIONALITIES:
        assert nationality in NATIONALITY_COUNTRIES, (
            f"sample nationality {nationality!r} has no country"
        )
        assert country_for_nationality(nationality)

    # The "Other" case is deliberately present, and is not a country.
    assert "Other" in SAMPLE_LINEUP_NATIONALITIES
    assert country_for_nationality("Other") == "Other"
