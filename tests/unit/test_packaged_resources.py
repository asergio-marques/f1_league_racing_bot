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
