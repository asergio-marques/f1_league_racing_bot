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
    """The country gave up its half of this line; the circuit took all of it."""
    circuit = _field(_weather_root(template), "track_name")
    assert "inline-size:1104px" in circuit.get("style")


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
