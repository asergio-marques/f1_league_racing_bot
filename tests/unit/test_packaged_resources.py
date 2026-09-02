"""What ships in ``resources/`` (044, US5).

A clean clone must draw every graphic out of packaged placeholders, and the two
imagery classes must be drawn at the aspect their class carries. A slot left at the
wrong shape is invisible in the SVG source and shows only in the raster, so these
tests are the offline half of that check; the PNG verification in quickstart.md is
the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from lxml import etree

from models.image_constants import (
    ASSET_ASPECT_TOLERANCE,
    PACKAGED_ASSET_ASPECTS,
    ASPECT_TEMPLATES,
    FALLBACK_ASSET_NAME,
    MYSTERY_ASSET_NAME,
)

SVG_NS = "http://www.w3.org/2000/svg"
ROOT = Path(__file__).resolve().parents[2]
#: Everything the module ships now sits under ``resources/defaults/`` (047 FR-037),
#: leaving ``resources/`` itself for a league's own directories.
RESOURCES = ROOT / "resources" / "defaults"


def _svg_aspect(path: Path) -> float:
    root = etree.parse(str(path)).getroot()
    width = float(re.sub(r"[a-z%]+$", "", root.get("width")))
    height = float(re.sub(r"[a-z%]+$", "", root.get("height")))
    return width / height


# --------------------------------------------------------------------------
# The reserved files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("directory", ["flags", "tracks"])
def test_both_imagery_directories_ship_their_reserved_files(directory):
    """``mystery.svg`` is reserved in the flag directory as in the track directory.

    A round of the mystery format conceals its track and thereby its country, so
    both classes resolve the literal ``Mystery``.
    """
    path = RESOURCES / directory
    assert (path / FALLBACK_ASSET_NAME).is_file(), f"{directory} has no fallback"
    assert (path / MYSTERY_ASSET_NAME).is_file(), f"{directory} has no mystery file"


@pytest.mark.parametrize(
    ("directory", "asset_class"),
    [("flags", "flag"), ("tracks", "track")],
)
@pytest.mark.parametrize("filename", [FALLBACK_ASSET_NAME, MYSTERY_ASSET_NAME])
def test_a_packaged_asset_carries_its_classs_aspect(directory, asset_class, filename):
    expected = PACKAGED_ASSET_ASPECTS[asset_class]
    found = _svg_aspect(RESOURCES / directory / filename)
    assert abs(found - expected) / expected <= ASSET_ASPECT_TOLERANCE, (
        f"{directory}/{filename} is {found:.4f}, expected {expected:.4f}"
    )


@pytest.mark.parametrize("filename", [FALLBACK_ASSET_NAME, MYSTERY_ASSET_NAME])
def test_a_packaged_asset_carries_no_text(filename):
    """Text in an asset font-substitutes and rasterises differently per machine."""
    for directory in ("flags", "tracks"):
        root = etree.parse(str(RESOURCES / directory / filename)).getroot()
        assert root.find(f".//{{{SVG_NS}}}text") is None, (
            f"{directory}/{filename} carries text"
        )


@pytest.mark.parametrize("filename", [FALLBACK_ASSET_NAME, MYSTERY_ASSET_NAME])
def test_a_packaged_asset_is_plain_svg(filename):
    """XIV.6 — no clipPath, gradient or filter.

    Checked against parsed *elements*, never against the raw text: these files carry
    a comment naming the very constructs they must not use, and a substring test
    would fail on the documentation rather than on the drawing.
    """
    forbidden = ("clipPath", "linearGradient", "radialGradient", "filter", "pattern", "mask")
    for directory in ("flags", "tracks"):
        root = etree.parse(str(RESOURCES / directory / filename)).getroot()
        for tag in forbidden:
            found = root.find(f".//{{{SVG_NS}}}{tag}")
            assert found is None, f"{directory}/{filename} uses <{tag}>"
        for node in root.iter():
            for attribute in ("clip-path", "filter", "mask"):
                assert not node.get(attribute), (
                    f"{directory}/{filename} sets {attribute}"
                )


# --------------------------------------------------------------------------
# The shipped templates — every image slot at its class's aspect (T047)
# --------------------------------------------------------------------------

def _template_paths():
    return sorted((RESOURCES / "templates").glob("*.svg"))


def test_every_shipped_template_is_covered_by_this_check():
    assert len(_template_paths()) == 15, "the module ships fifteen templates"


@pytest.mark.parametrize(
    "path", _template_paths(), ids=lambda p: p.stem
)
def test_every_image_slot_of_a_shipped_template_carries_its_classs_aspect(path):
    """The check that catches a missed re-geometry (044, T024/T025).

    Four types' round headings moved from the track class to the flag class in this
    increment. A slot renamed but left square would letterbox every flag drawn into
    it, and the generator never pads.

    Two halves since 2026-09-01, when the shape a class carries became the league's to
    choose. The first is what production still enforces: the slots of a class agree with one
    another. The second is what production deliberately no longer does -- that the shape they
    agree *on* is the one our own artwork is drawn at. Nothing in `src/` requires that of the
    shipped set any more, so this is the only thing holding the fifteen templates and the
    artwork that fills them together, and it has to be asserted here or not at all.
    """
    from models.image_constants import (
        PACKAGED_ASSET_ASPECTS,
        RATIO_CONSISTENT_ASSET_CLASSES,
    )
    from services.image_validity_service import (
        class_aspect_faults_of,
        class_aspect_of,
        stretch_faults_of,
    )

    root = etree.parse(str(path)).getroot()

    faults = stretch_faults_of(root, path.stem) + class_aspect_faults_of(root, path.stem)
    assert not faults, "; ".join(faults)

    for asset_class in sorted(RATIO_CONSISTENT_ASSET_CLASSES):
        found = class_aspect_of(root, path.stem, asset_class)
        if found is None:
            continue  # this template draws none of that class
        expected = PACKAGED_ASSET_ASPECTS[asset_class]
        assert abs(found - expected) / expected <= 0.01, (
            f"{path.name} draws {asset_class} at {found:.4f}, but the artwork we ship for "
            f"it is {expected:.4f} — one of the two has moved without the other"
        )


@pytest.mark.parametrize(
    ("template", "field_id", "asset_class"),
    [
        ("calendar_template", "round_1_flag", "flag"),
        ("calendar_template", "round_1_image", "track"),
        ("rsvp_template", "track_flag", "flag"),
        ("rsvp_template", "track_image", "track"),
        ("standings_drivers_template", "round_1_flag", "flag"),
        ("standings_constructors_template", "round_1_flag", "flag"),
        ("attendance_template", "round_1_flag", "flag"),
        ("weather_p1_template", "track_flag", "flag"),
    ],
)
def test_the_packaged_templates_declare_the_expected_slots(template, field_id, asset_class):
    """The shipped calendar and check-in examples declare **both** classes.

    A league authoring its own template has a working example of each; the two
    graphics that may draw a map are the only ones that do.
    """
    root = etree.parse(str(RESOURCES / "templates" / f"{template}.svg")).getroot()
    node = root.find(f".//*[@id='{field_id}']")
    assert node is not None, f"{template} declares no {field_id}"

    expected = PACKAGED_ASSET_ASPECTS[asset_class]
    found = float(node.get("width")) / float(node.get("height"))
    assert abs(found - expected) / expected <= ASSET_ASPECT_TOLERANCE, (
        f"{template}/{field_id} is {found:.4f}, expected {expected:.4f}"
    )


@pytest.mark.parametrize(
    "template",
    [
        "standings_drivers_template",
        "standings_constructors_template",
        "attendance_template",
        "weather_p1_template",
        "weather_p2_template",
        "weather_p2_sprint_template",
        "weather_p3_template",
        "weather_p3_sprint_template",
    ],
)
def test_only_the_calendar_and_check_in_templates_declare_a_circuit_map(template):
    """XIV.13 — every other type draws the country flag and nothing else."""
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")
    assert 'id="track_image"' not in text
    assert not re.search(r'id="round_\d+_image"', text)


def test_the_two_map_bearing_templates_are_exactly_the_calendar_and_the_check_in():
    bearing = {
        path.stem
        for path in _template_paths()
        if re.search(r'id="(track_image|round_\d+_image)"',
                     path.read_text(encoding="utf-8"))
    }
    assert bearing == {"calendar_template", "rsvp_template"}


# --------------------------------------------------------------------------
# The grand prix name, drawn only where one round is the subject (2026-09-01)
# --------------------------------------------------------------------------

#: The sheets that stand after a whole season. Each names the round it stands after by
#: number, and naming that one round's grand prix beneath the heading read as a subtitle
#: for the table rather than as a fact about it.
SEASON_SHEETS = (
    "attendance_template",
    "standings_drivers_template",
    "standings_constructors_template",
)


@pytest.mark.parametrize("template", SEASON_SHEETS)
def test_a_season_sheet_does_not_name_a_grand_prix(template):
    """``race_name`` stays in each catalogue; these files decline to declare it."""
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")
    assert 'id="race_name"' not in text


def test_every_other_round_scoped_template_still_names_its_grand_prix():
    """The check-in, the results sheets, the verdict and the forecasts each draw one round."""
    naming = {
        path.stem
        for path in _template_paths()
        if 'id="race_name"' in path.read_text(encoding="utf-8")
    }
    assert naming == {
        "rsvp_template",
        "results_qualifying_template",
        "results_race_template",
        "verdicts_template",
        "weather_p1_template",
        "weather_p2_template",
        "weather_p2_sprint_template",
        "weather_p3_template",
        "weather_p3_sprint_template",
    }


# --------------------------------------------------------------------------
# The weather heading — grand prix over circuit, country as a flag (2026-09-01)
# --------------------------------------------------------------------------

#: The five phase templates. The mystery notice declares none of these fields: it announces
#: that no forecast is coming for a round whose circuit is concealed.
WEATHER_PHASES = (
    "weather_p1_template",
    "weather_p2_template",
    "weather_p2_sprint_template",
    "weather_p3_template",
    "weather_p3_sprint_template",
)


def _weather_root(template):
    return etree.parse(str(RESOURCES / "templates" / f"{template}.svg")).getroot()


def _field(root, field_id):
    found = root.xpath(f'//*[@id="{field_id}"]')
    return found[0] if found else None


@pytest.mark.parametrize("template", WEATHER_PHASES)
def test_a_forecast_leads_with_the_grand_prix(template):
    root = _weather_root(template)
    assert (_field(root, "race_name").get("class") or "").split() == ["track"]


@pytest.mark.parametrize("template", WEATHER_PHASES)
def test_a_forecast_puts_the_circuit_beneath_the_grand_prix(template):
    root = _weather_root(template)
    circuit = _field(root, "track_name")
    assert (circuit.get("class") or "").split() == ["sub"]
    assert float(circuit.get("y")) > float(_field(root, "race_name").get("y"))


@pytest.mark.parametrize("template", WEATHER_PHASES)
def test_the_circuit_line_has_the_whole_row(template):
    """The country gave up its half of this line; the circuit took all of it.

    The bound lives in the stylesheet, as it does on every other template — the element
    carried a style attribute of its own until 2026-09-02.
    """
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")
    body = re.search(r"\.sub\s+\{([^}]*)\}", text).group(1).replace(" ", "")
    assert "inline-size:1104px" in body
    assert _field(_weather_root(template), "track_name").get("style") is None


@pytest.mark.parametrize("template", WEATHER_PHASES)
def test_the_mandatory_headline_needs_no_group_and_the_optional_line_carries_one(template):
    """``race_name`` is mandatory here and ``track_name`` optional, as on the check-in call.

    The headline never leaves, so a group for it would be chrome with nothing to remove. The
    circuit line does leave — a round whose track matches no record on the server resolves
    no circuit name — and its group is what takes it off cleanly.
    """
    root = _weather_root(template)
    assert _field(root, "race_name_group") is None
    assert _field(root, "track_name_group") is not None


@pytest.mark.parametrize("template", WEATHER_PHASES + ("weather_mystery_template",))
def test_no_forecast_writes_the_country_out(template):
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")
    assert 'id="country_name"' not in text
    assert 'id="track_flag"' in text or template == "weather_mystery_template"


# --------------------------------------------------------------------------
# The calendar's grand prix name, set to be the thing the eye lands on (2026-09-02)
# --------------------------------------------------------------------------

CALENDAR = "calendar_template"


def _calendar_style(class_name: str) -> dict[str, str]:
    """One rule of the calendar's stylesheet, as a property map."""
    text = (RESOURCES / "templates" / f"{CALENDAR}.svg").read_text(encoding="utf-8")
    body = re.search(rf"\.{class_name}\s*\{{([^}}]*)\}}", text).group(1)
    return {
        key.strip(): value.strip()
        for key, _, value in (part.partition(":") for part in body.split(";"))
        if key.strip()
    }


def _px(value: str) -> float:
    return float(value.removesuffix("px"))


def test_the_grand_prix_name_is_the_largest_thing_on_a_round_card():
    """It was set at the same size as the date and the round label, and read as neither.

    The card names a round; the grand prix is what a round *is*, so it carries the weight.
    """
    race = _px(_calendar_style("race")["font-size"])
    for smaller in ("rsub", "rsub2", "date", "time", "rlbl"):
        assert race > _px(_calendar_style(smaller)["font-size"]), smaller


def test_a_grand_prix_name_that_wraps_still_clears_the_circuit_name():
    """The guard on enlarging it further: two lines must not reach the line beneath.

    Every seeded grand prix name fits on one line at the size set here, so the second line
    is reached only by a name a league authored. It must not overprint the circuit when it
    is — which is why the baseline was raised with the type rather than left where it was.
    """
    root = etree.parse(str(RESOURCES / "templates" / f"{CALENDAR}.svg")).getroot()
    race_style = _calendar_style("race")
    size = _px(race_style["font-size"])
    line_height = float(race_style["line-height"])
    circuit_size = _px(_calendar_style("rsub")["font-size"])

    cards = sorted(
        (
            float(root.xpath(f'//*[@id="round_{n}_race_name"]')[0].get("y")),
            float(root.xpath(f'//*[@id="round_{n}_track_name"]')[0].get("y")),
        )
        for n in range(1, 13)
    )
    assert cards, "the shipped calendar declares no round"

    for race_y, circuit_y in cards:
        # The deepest ink of a wrapped second line, against the highest ink below it.
        second_line_bottom = race_y + size * line_height + size * 0.24
        circuit_top = circuit_y - circuit_size * 0.76
        assert second_line_bottom < circuit_top, (race_y, circuit_y)


def test_the_calendar_cards_tile_without_overlapping():
    """The card grew from 136 to 152 (2026-09-02); the row pitch had to grow with it.

    Every coordinate of a card is an offset from its own top edge, so enlarging one card
    without enlarging the step lays the next row over the last. This is the arithmetic that
    catches that.
    """
    root = etree.parse(str(RESOURCES / "templates" / f"{CALENDAR}.svg")).getroot()
    cards = sorted(
        (float(rect.get("y")), float(rect.get("height")))
        for rect in root.iter(f"{{{SVG_NS}}}rect")
        if rect.get("width") == "676" and rect.get("height") not in (None, "0")
    )
    assert cards, "the shipped calendar declares no round card"
    for (top, height), (next_top, _) in zip(cards, cards[2:]):
        assert top + height <= next_top, (top, height, next_top)


def test_the_last_calendar_crop_point_sits_at_the_declared_height():
    """A division filling the file must be drawn whole — the crop may not cut into it."""
    root = etree.parse(str(RESOURCES / "templates" / f"{CALENDAR}.svg")).getroot()
    declared = float(root.get("height"))
    crops = sorted(
        float(node.get("y"))
        for node in root.xpath('//*[contains(@id, "_vertical_crop_point")]')
    )
    assert crops, "the shipped calendar declares no crop point"
    assert crops[-1] == declared


def test_the_calendar_background_covers_the_canvas():
    """The ground is painted to the declared height, not to the height it used to be."""
    root = etree.parse(str(RESOURCES / "templates" / f"{CALENDAR}.svg")).getroot()
    ground = next(
        rect for rect in root.iter(f"{{{SVG_NS}}}rect")
        if rect.get("x") == "0" and rect.get("y") == "0"
    )
    assert float(ground.get("height")) == float(root.get("height"))
    assert float(ground.get("width")) == float(root.get("width"))


# --------------------------------------------------------------------------
# One heading rhythm across the shipped templates (2026-09-02)
# --------------------------------------------------------------------------
#
# The round label, the title, the line under it and the rule beneath them all sit at fixed
# intervals. rsvp and the five forecasts had drifted to a 62px title-to-sub gap, left over
# from when their title held the *circuit* name and was allowed to wrap to two lines. The
# grand prix name that replaced it is short, so the allowance and the gap both went.
#
# The intervals are what is pinned, never the absolute positions: the rule sits lower on the
# forecasts than on the check-in call, because what they draw beneath it is taller.

LABEL_TO_TITLE = 48
TITLE_TO_SUB = 32
SUB_TO_RULE = 32

#: Templates whose heading is round label, title, sub, rule.
THREE_LINE_HEADINGS = (
    "rsvp_template",
    "results_qualifying_template",
    "results_race_template",
    "verdicts_template",
    "weather_p1_template",
    "weather_p2_template",
    "weather_p2_sprint_template",
    "weather_p3_template",
    "weather_p3_sprint_template",
)

#: Templates whose heading is title, sub, rule — they stand for a whole season and so name
#: no round above the title.
TWO_LINE_HEADINGS = ("calendar_template", "lineup_template")


def _heading(template):
    """The heading baselines of *template*: (label, title, sub, rule)."""
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")

    def y(pattern):
        found = re.search(pattern, text)
        return int(found.group(1)) if found else None

    return (
        y(r'class="roundlbl" x="48" y="(\d+)"'),
        y(r'class="track" x="48" y="(\d+)"'),
        y(r'class="sub" x="48" y="(\d+)"'),
        y(r'<line x1="48" y1="(\d+)"'),
    )


@pytest.mark.parametrize("template", THREE_LINE_HEADINGS)
def test_a_three_line_heading_keeps_the_shared_rhythm(template):
    label, title, sub, rule = _heading(template)
    assert None not in (label, title, sub, rule), template
    assert title - label == LABEL_TO_TITLE
    assert sub - title == TITLE_TO_SUB
    assert rule - sub == SUB_TO_RULE


@pytest.mark.parametrize("template", TWO_LINE_HEADINGS)
def test_a_two_line_heading_keeps_the_same_intervals(template):
    _, title, sub, rule = _heading(template)
    assert None not in (title, sub, rule), template
    assert sub - title == TITLE_TO_SUB
    assert rule - sub == SUB_TO_RULE


@pytest.mark.parametrize("template", THREE_LINE_HEADINGS)
def test_a_heading_title_is_drawn_on_one_line(template):
    """A title free to wrap would reach the line 32px beneath it.

    The check-in call and the forecasts allowed two lines while their title held a circuit
    name. Their title is the grand prix name now — short enough to set on one line, and the
    module reduces the size of one that is not rather than wrapping it into the sub.
    """
    text = (RESOURCES / "templates" / f"{template}.svg").read_text(encoding="utf-8")
    rule_body = re.search(r"\.track\s+\{([^}]*)\}", text).group(1)
    assert "max-lines:1" in rule_body.replace(" ", ""), template
    assert "line-height" not in rule_body, template


# --------------------------------------------------------------------------
# Where both classes are drawn, they are framed at one height (2026-09-02)
# --------------------------------------------------------------------------
#
# The artwork itself cannot match: the flag class is authored at 3:2 and the track class at
# 1:1, and the two deliberately differ (XIV.6). What a reader sees side by side is the frame,
# and a shorter frame beside a taller one reads as a mistake rather than as two shapes. The
# frame is the plate where a template draws one, and the image itself where it does not.
#
# Only the calendar and the check-in call draw both — held by
# `test_the_two_map_bearing_templates_are_exactly_the_calendar_and_the_check_in` above.


def test_the_check_in_call_frames_its_flag_and_its_map_alike():
    """Its plates are the frame. The flag's was 97 against the map's 136."""
    root = etree.parse(str(RESOURCES / "templates" / "rsvp_template.svg")).getroot()

    def plate(group_id):
        group = root.xpath(f'//*[@id="{group_id}"]')[0]
        rect = next(iter(group.iter(f"{{{SVG_NS}}}rect")))
        return float(rect.get("width")), float(rect.get("height"))

    assert plate("track_flag_group") == plate("track_image_group")


def test_the_check_in_calls_flag_is_centred_in_its_plate():
    """A 3:2 flag in a square plate leaves room above and below; it is shared equally."""
    root = etree.parse(str(RESOURCES / "templates" / "rsvp_template.svg")).getroot()
    group = root.xpath('//*[@id="track_flag_group"]')[0]
    rect = next(iter(group.iter(f"{{{SVG_NS}}}rect")))
    image = root.xpath('//*[@id="track_flag"]')[0]

    above = float(image.get("y")) - float(rect.get("y"))
    below = (float(rect.get("y")) + float(rect.get("height"))) - (
        float(image.get("y")) + float(image.get("height"))
    )
    assert abs(above - below) < 0.01, (above, below)


def test_the_calendar_draws_its_flag_and_its_map_at_one_height():
    """It frames neither, so the images themselves are what must agree."""
    root = etree.parse(str(RESOURCES / "templates" / "calendar_template.svg")).getroot()
    for ordinal in range(1, 13):
        flag = root.xpath(f'//*[@id="round_{ordinal}_flag"]')[0]
        image = root.xpath(f'//*[@id="round_{ordinal}_image"]')[0]
        assert float(flag.get("height")) == float(image.get("height")), ordinal
        assert float(flag.get("y")) == float(image.get("y")), ordinal


# --------------------------------------------------------------------------
# The tokens every shipped template shares (2026-09-02)
# --------------------------------------------------------------------------
#
# Measured across all fifteen, these were already uniform but for a handful of strays — a
# division name bounded at 800 on one template and 860 on fourteen, and card radii of 10 and
# 14 among a dozen 12s. Nothing scales them: a 1104-wide weather card and a 132-wide limit
# plate carry the same radius, so a different one is drift.

GROUND = "#0B0D10"
ACCENT = "#E8113C"
PLATE_STROKE = "#262B33"
PLATE_FILL = "#14171C"
CARD_RADIUS = "12"

#: Radii other than the card's, each on an element the card radius would not suit.
#: `8` is a chip nested inside a card on the phase 3 forecasts; `4` a 44px portrait well
#: on the lineup. Both are smaller surfaces sitting on a larger one.
NESTED_RADII = {"8", "4"}


@pytest.mark.parametrize("path", _template_paths(), ids=lambda p: p.stem)
def test_a_shipped_template_paints_the_shared_ground_and_accent(path):
    text = path.read_text(encoding="utf-8")
    assert re.search(rf'<rect x="0" y="0"[^>]*fill="{GROUND}"', text), path.stem
    assert ACCENT in text, path.stem


@pytest.mark.parametrize("path", _template_paths(), ids=lambda p: p.stem)
def test_a_shipped_template_strokes_its_plates_alike(path):
    """One stroke for a plate on a card, and one fill under it."""
    text = path.read_text(encoding="utf-8")
    strokes = set(re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', text))
    assert strokes <= {PLATE_STROKE, "#2E343D"}, (path.stem, strokes)


@pytest.mark.parametrize("path", _template_paths(), ids=lambda p: p.stem)
def test_a_card_or_plate_carries_the_shared_corner_radius(path):
    """Size does not scale it — only a surface nested on another gets a smaller one."""
    text = path.read_text(encoding="utf-8")
    radii = set(re.findall(r'<rect [^>]*rx="([\d.]+)"', text))
    assert radii <= {CARD_RADIUS} | NESTED_RADII, (path.stem, radii)


@pytest.mark.parametrize("path", _template_paths(), ids=lambda p: p.stem)
def test_the_division_name_is_bounded_alike_everywhere(path):
    """It is the one field every template draws, and it is bounded the same on each."""
    body = re.search(r"\.title\s+\{([^}]*)\}", path.read_text(encoding="utf-8")).group(1)
    assert "inline-size:860px" in body.replace(" ", ""), path.stem
    assert "font-size:34px" in body.replace(" ", ""), path.stem
