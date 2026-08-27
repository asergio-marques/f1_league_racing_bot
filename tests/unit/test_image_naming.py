"""What a generated image is called — `utils.image_naming`.

The rule it exists to keep in one place: the picture is named for what it is **of**, not
for the template that drew it. A manager collecting a season's graphics could not otherwise
tell one division's `standings_drivers.png` from another's.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import TEMPLATE_COLUMNS
from utils.image_naming import (
    IMAGE_SUBJECTS,
    image_filename_stem,
    stem_for_drawing,
    subject_for_template,
)


# ── The parts, and the order of them ──────────────────────────────────────


def test_every_part_present_names_the_season_division_round_and_subject():
    assert (
        image_filename_stem(
            "standings_drivers",
            season_number="1",
            division_tier="1",
            division_name="Elite",
            round_number="10",
        )
        == "season1_division1_round10_standings_drivers"
    )


def test_the_tier_is_preferred_and_the_name_stands_in_for_it():
    """Several call sites carry no tier; a bare `division_None` would name nothing."""
    with_tier = image_filename_stem(
        "calendar", season_number=2, division_tier=3, division_name="Elite"
    )
    without = image_filename_stem(
        "calendar", season_number=2, division_tier=None, division_name="Elite"
    )
    assert with_tier == "season2_division3_calendar"
    assert without == "season2_elite_calendar"


@pytest.mark.parametrize("tier", [None, "", 0, "0"])
def test_a_tier_that_says_nothing_falls_through_to_the_name(tier):
    """Nought is what an unset tier carries, and must not be named as a tier.

    `/season review` already reads it that way — its tier tag is written only
    `if div.tier > 0` — so a graphic named `division0` would claim a tier the league
    never gave.
    """
    assert (
        image_filename_stem("lineup", division_tier=tier, division_name="Academy")
        == "academy_lineup"
    )


@pytest.mark.parametrize("season", [None, "", 0, "0"])
def test_an_unset_season_number_is_left_out_entirely(season):
    assert image_filename_stem("lineup", season_number=season) == "lineup"


def test_an_unknown_part_is_left_out_rather_than_named_as_unknown():
    assert image_filename_stem("lineup") == "lineup"
    assert image_filename_stem("lineup", season_number=4) == "season4_lineup"


def test_the_two_season_wide_graphics_carry_no_round():
    """The lineup and the calendar stand for a season, not for one round of it."""
    for subject in ("lineup", "calendar"):
        stem = image_filename_stem(
            subject, season_number=1, division_tier=1, round_number=None
        )
        assert stem == f"season1_division1_{subject}"
        assert "round" not in stem


# ── The slug rule ─────────────────────────────────────────────────────────


def test_a_division_name_is_put_through_the_module_s_own_slug_rule():
    """The same rule an asset filename follows, so no name can defeat a filesystem."""
    assert (
        image_filename_stem("calendar", division_name="Élite Ünlimited!")
        == "elite_unlimited_calendar"
    )


def test_an_unusably_long_division_name_is_cut_rather_than_carried():
    stem = image_filename_stem("calendar", division_name="D" * 200)
    assert len(stem) < 60
    assert stem.endswith("_calendar")


def test_a_division_name_that_normalises_to_nothing_is_left_out():
    assert image_filename_stem("calendar", division_name="!!!") == "calendar"


# ── The subject table ─────────────────────────────────────────────────────


def test_every_template_the_module_has_a_column_for_names_its_subject():
    """A template added without a subject would name its output after itself."""
    assert set(IMAGE_SUBJECTS) == set(TEMPLATE_COLUMNS)


def test_the_two_subjects_that_are_not_the_template_key():
    """A league knows its check-in by that name, not by the column that stores it."""
    assert subject_for_template("rsvp_template") == "checkin"
    assert subject_for_template("verdicts_template") == "verdict"


def test_an_unknown_template_still_names_something_usable():
    """A posting must never fail for want of a name."""
    assert subject_for_template("something_new_template") == "something_new"
    assert subject_for_template("") == "image"


# ── Reading a drawing ─────────────────────────────────────────────────────


def test_a_drawing_names_itself_from_its_own_fields():
    drawing = SimpleNamespace(
        template_key="attendance_template",
        season_number="5",
        division_tier="2",
        division_name="Academy",
        round_number="8",
    )
    assert stem_for_drawing(drawing) == "season5_division2_round8_attendance"


def test_a_caller_that_knows_better_than_the_template_may_say_so():
    """One results template draws four sessions; the session's own label names the file."""
    drawing = SimpleNamespace(
        template_key="results_qualifying_template",
        season_number="5",
        division_tier="2",
        round_number="8",
    )
    assert (
        stem_for_drawing(drawing, subject="Feature Qualifying results")
        == "season5_division2_round8_feature_qualifying_results"
    )


def test_a_drawing_missing_a_field_is_named_by_what_it_does_carry():
    """`getattr` throughout: the calendar's drawing carries no round number at all."""
    drawing = SimpleNamespace(
        template_key="calendar_template", division_name="Elite", season_number="5"
    )
    assert stem_for_drawing(drawing) == "season5_elite_calendar"


# ── The render service writes the name ────────────────────────────────────

#: An image type no catalogue claims, so these two tests exercise the *naming* alone and
#: not a template's field requirements — which have their own suites, and which would
#: otherwise have to be satisfied by a fixture that says nothing about filenames.
_UNCATALOGUED = "naming_probe_template"


@pytest.mark.asyncio
async def test_the_render_service_names_the_png_from_the_stem(tmp_path, monkeypatch):
    """One naming rule: the file on disk carries it, and every attachment reads it back.

    Naming the attachment at each posting site instead would put the rule in nine places
    and leave the one site that passes no filename at all — the verdict announcement —
    showing a league the raw template key.
    """
    import services.image_render_service as render_service
    from utils.svg_fill import FillSpec
    from lxml import etree

    monkeypatch.setattr(render_service, "converter_available", lambda *a, **k: True)

    def _rasterise(svg, destination, canvas):
        destination.write_bytes(b"\x89PNG\r\n\x1a\n")
        return destination

    monkeypatch.setattr(render_service, "rasterise", _rasterise)

    service = render_service.ImageRenderService.__new__(
        render_service.ImageRenderService
    )
    service._validity_service = SimpleNamespace(
        template_reports=_reports(tmp_path / "t.svg")
    )

    (tmp_path / "t.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    )

    outcome = await service.render(
        1,
        _UNCATALOGUED,
        lambda root: FillSpec(root=root, image_type=_UNCATALOGUED),
        output_dir=tmp_path / "out",
        persist_notices=False,
        filename_stem="season1_division1_lineup",
    )

    assert outcome.problem is None, outcome.problem
    assert outcome.png_paths[0].name == "season1_division1_lineup.png"


@pytest.mark.asyncio
async def test_no_stem_still_falls_back_to_the_template_key(tmp_path, monkeypatch):
    """A caller that names nothing must still get a file, as it always did."""
    import services.image_render_service as render_service
    from utils.svg_fill import FillSpec

    monkeypatch.setattr(render_service, "converter_available", lambda *a, **k: True)
    monkeypatch.setattr(
        render_service,
        "rasterise",
        lambda svg, destination, canvas: (
            destination.write_bytes(b"\x89PNG\r\n\x1a\n"),
            destination,
        )[1],
    )

    service = render_service.ImageRenderService.__new__(
        render_service.ImageRenderService
    )
    service._validity_service = SimpleNamespace(
        template_reports=_reports(tmp_path / "t.svg")
    )
    (tmp_path / "t.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
    )

    outcome = await service.render(
        1,
        _UNCATALOGUED,
        lambda root: FillSpec(root=root, image_type=_UNCATALOGUED),
        output_dir=tmp_path / "out",
        persist_notices=False,
    )

    assert outcome.png_paths[0].name == f"{_UNCATALOGUED}.png"


def _reports(path):
    """A validity service reporting one valid template resolving to *path*."""

    async def reports(server_id):
        return {
            _UNCATALOGUED: SimpleNamespace(
                valid=True, resolved_path=str(path), reason=None, failed_layer=None
            )
        }

    return reports
