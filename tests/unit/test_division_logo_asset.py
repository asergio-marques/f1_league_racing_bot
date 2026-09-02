"""A division's own logo, drawn from artwork keyed by the division's name — 2026-09-02.

The eighth asset class, and the first that is **optional decoration**. Nothing the bot ships
draws it: a league declares an `division_logo` slot in a template of its own, drops
`division_1.svg` into `resources/league/division-logos/`, and the graphic carries their crest.
A league that declares no slot never meets the class at all.

Two things separate it from the seven that came before, and both are pinned here because both
are the sort of thing a later reader would "fix" back:

* **Drawing the fallback says nothing.** Every other class reports a fallback, because a
  fallback there is a gap in the league's asset set. Here the fallback is the ordinary state
  of a league that has drawn no logo, and reporting it would put a notice on every graphic
  they post for an element they never asked for.
* **The class is held to no shape.** The template decides how large and what proportion, and
  two slots on the same template may decide differently, because a division supplies its own
  file for each of them rather than one file serving them all.

Its ordinary asset behaviour is *not* re-tested here — the two tiers, the slug, the href form
are `test_asset_resolver`'s subject and this class goes through exactly the same code.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    BLANK_FALLBACK_ASSET_CLASSES,
    FALLBACK_ASSET_NAME,
    NOTICE_ASSET_FALLBACK_USED,
    NOTICE_OPTIONAL_FIELD_EMPTIED,
    NOTICE_PACKAGED_ASSET_OFF_SHAPE,
    PACKAGED_ASSET_ASPECTS,
    packaged_directory_for,
)
from utils.asset_resolver import normalise  # noqa: E402
from utils.svg_document import FieldIndex, parse_svg_bytes  # noqa: E402
from utils.svg_fill import FillSpec, fill  # noqa: E402

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'
LOGO = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40">' \
       b'<rect width="40" height="40" fill="#123456"/></svg>'


@pytest.fixture()
def league_logos(tmp_path):
    """A league's own division-logo folder, empty until a test puts something in it."""
    directory = tmp_path / "league_division_logos"
    directory.mkdir()
    return directory


@pytest.fixture()
def packaged(tmp_path, monkeypatch):
    """A packaged tier carrying the blank fallback for every class these tests touch."""
    import utils.paths as paths_module

    root = tmp_path / "project"
    made = {}
    for asset_class in ("division_logo", "flag"):
        directory = root / packaged_directory_for(asset_class)
        directory.mkdir(parents=True)
        (directory / FALLBACK_ASSET_NAME).write_bytes(SVG)
        made[asset_class] = directory
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", root, raising=False)
    return made


def _render(directory, *, asset_class="division_logo", field="division_logo",
            datum="Division 1", width=120, height=120):
    """Fill one logo slot of the given shape, and return the filled tree and the notices."""
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" width="600" height="400">'
        f'<image id="{field}" width="{width}" height="{height}"/>'
        "</svg>"
    ).encode("utf-8")
    root = parse_svg_bytes(doc)
    result = fill(
        FillSpec(
            root=root,
            image_type="calendar_template",
            image_data={field: (asset_class, datum)},
            asset_directories={asset_class: directory},
        )
    )
    return root, result


def _kinds(result):
    return {notice.notice_kind for notice in result.notices}


def _href(root, field="division_logo"):
    element = FieldIndex(root).resolve(field)
    return None if element is None else element.get("href")


# ── the league's own artwork ──────────────────────────────────────────────


def test_a_division_with_a_logo_gets_it_drawn(league_logos, packaged):
    """The division's name is the key, through the same slug rule as every other class."""
    (league_logos / f"{normalise('Division 1')}.svg").write_bytes(LOGO)

    root, result = _render(league_logos)

    assert result.unresolved == []
    assert "division_1.svg" in _href(root)
    assert not result.notices


def test_the_key_is_the_division_name_normalised(league_logos, packaged):
    """A name with punctuation, case and an accent reaches one predictable filename."""
    (league_logos / "tier_2_pro.svg").write_bytes(LOGO)

    root, result = _render(league_logos, datum="Tier 2 — Pró!")

    assert "tier_2_pro.svg" in _href(root)
    assert result.unresolved == []


# ── the blank, and the silence around it ──────────────────────────────────


def test_a_division_without_a_logo_is_told_nothing_at_all(league_logos, packaged):
    """**The decision this class exists to carry** (2026-09-02).

    A league that has drawn no logo is in the ordinary state, not an incomplete one. The
    packaged blank is drawn so the slot resolves, and neither notice is raised — not the
    fallback one, and not the off-shape one. Reporting either would mark every graphic the
    league posts over an element they never asked for, and the only cure available to them
    would be to supply the very file the bot already ships.
    """
    root, result = _render(league_logos)

    assert result.unresolved == []
    assert result.notices == []
    assert str(packaged["division_logo"]) in _href(root)


def test_the_blank_is_silent_however_the_slot_is_shaped(league_logos, packaged):
    """The off-shape notice is suppressed too, and not merely dodged by a square slot.

    `_packaged_shape_notice` compares a packaged file against `PACKAGED_ASSET_ASPECTS`, and a
    slot at 3:1 would trip it for any class recorded there. This class is not recorded, and
    is named in `BLANK_FALLBACK_ASSET_CLASSES` besides — a file with nothing drawn in it
    cannot be the wrong shape for anything.
    """
    root, result = _render(league_logos, width=360, height=120)

    assert NOTICE_PACKAGED_ASSET_OFF_SHAPE not in _kinds(result)
    assert result.notices == []


def test_the_slot_is_filled_rather_than_emptied(league_logos, packaged):
    """Silence is not the same as removing the field, and the difference is load-bearing.

    An emptied field raises `OPTIONAL_FIELD_EMPTIED`, which is a notice — the thing this
    class must not produce. Drawing the blank is what keeps the slot resolved and quiet at
    once.
    """
    root, result = _render(league_logos)

    assert NOTICE_OPTIONAL_FIELD_EMPTIED not in _kinds(result)
    assert _href(root) is not None


def test_a_missing_logo_is_never_fatal(league_logos, packaged):
    """A division always has a name, so the datum is never absent and the slug never empty.

    Resolution therefore ends at FOUND or at the packaged blank, never at MISSING, and a
    league cannot abandon a render by not having drawn a logo.
    """
    _, result = _render(league_logos, datum="A Division Nobody Drew")

    assert result.unresolved == []


# ── the silence is this class's alone ─────────────────────────────────────


def test_every_other_class_still_reports_its_fallback(league_logos, packaged):
    """The contrast that proves the suppression is scoped rather than a hole in the notice.

    Identical conditions, one class over: an unsupplied flag still says so, and still says
    it against the shape we ship.
    """
    _, result = _render(
        league_logos, asset_class="flag", field="round_1_flag",
        datum="Nonesuchland", width=360, height=120,
    )

    assert NOTICE_ASSET_FALLBACK_USED in _kinds(result)
    assert NOTICE_PACKAGED_ASSET_OFF_SHAPE in _kinds(result)


def test_only_the_division_logo_class_is_silent():
    """Named rather than derived, so widening it is a deliberate act."""
    assert BLANK_FALLBACK_ASSET_CLASSES == frozenset({"division_logo"})


# ── open set: a league's value, so our folder answers with the blank only ──


def test_the_packaged_folder_is_never_searched_for_a_divisions_own_name(
    league_logos, packaged
):
    """A division name is a value the league chose, so the class is **not** a closed set.

    Were it closed, a file that happened to ship under a division's slug would be drawn for a
    league that never supplied one — handing them artwork for a name only they could have
    picked. The blank answers instead.
    """
    (packaged["division_logo"] / "division_1.svg").write_bytes(LOGO)

    root, result = _render(league_logos, datum="Division 1")

    assert _href(root).endswith(FALLBACK_ASSET_NAME)
    assert result.notices == []


# ── what we actually ship ─────────────────────────────────────────────────


def test_the_shipped_fallback_has_nothing_drawn_in_it():
    """The silence above is only defensible because this file is invisible.

    A grey placeholder drawn on every graphic of every league that never asked for one, with
    no notice to explain it, would be worse than either reporting it or drawing nothing. So
    the file carries a size and a viewBox — enough to letterbox into any slot — and no
    painted element whatever.
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[2]
    path = (
        project_root / packaged_directory_for("division_logo") / FALLBACK_ASSET_NAME
    )
    assert path.is_file(), "the class ships no fallback"

    root = ET.parse(path).getroot()
    assert root.get("viewBox"), "no viewBox, so nothing to letterbox with"
    painted = [element.tag for element in root.iter()][1:]
    assert painted == [], f"the blank fallback draws {painted}"


def test_the_class_records_no_shape_for_its_own_artwork():
    """Nothing to author against, so nothing recorded — and nothing to compare a slot with."""
    assert "division_logo" not in PACKAGED_ASSET_ASPECTS


# ── the wiring: catalogues, builders, directory resolution ────────────────


def test_every_image_type_admits_the_field():
    """All fifteen, not the handful whose graphics seem to want one.

    Which aspects carry a logo is a league's choice, expressed by which of their own
    templates declare the slot. A catalogue quietly lacking the field would leave one aspect
    unable to draw it with nothing said — and saying nothing is precisely what this class
    does, so nobody would find out.
    """
    from models.image_catalogues import CATALOGUES, DIVISION_LOGO_ASSET

    assert len(CATALOGUES) == 15
    for key, catalogue in sorted(CATALOGUES.items()):
        assert "division_logo" in catalogue.optional, key
        assert catalogue.asset_class_for("division_logo") == DIVISION_LOGO_ASSET, key


def test_the_field_is_optional_on_every_type_and_mandatory_on_none():
    """A template declaring no such id is not faulty — that is what makes it opt-in."""
    from models.image_catalogues import CATALOGUES

    for key, catalogue in sorted(CATALOGUES.items()):
        assert "division_logo" not in catalogue.mandatory, key


def test_an_unknown_template_key_is_left_without_it():
    """`catalogue_for` returns an empty catalogue meaning "no specification at all".

    A field on it would make `is_empty` false, and layer 2 passes an empty catalogue over
    rather than reporting a depth nothing was checked to.
    """
    from models.image_catalogues import catalogue_for

    assert catalogue_for("no_such_template").is_empty


def test_every_posting_path_resolves_the_directory_whatever_else_it_draws():
    """Added by the shared resolver, so a posting path added later cannot omit it."""
    from types import SimpleNamespace

    from services.image_render_service import resolve_configured_directories

    config = SimpleNamespace(
        flag_directory="resources/defaults/flags",
        division_logo_directory="resources/defaults/division-logos",
    )

    directories, faults = resolve_configured_directories(
        config, (("flag", "flag_directory"),), image_type="calendar_template"
    )

    assert faults == {}
    assert "division_logo" in directories


def test_the_preview_resolves_every_class_there_is():
    """A preview is a diagnostic: a class it did not resolve would be reported as
    unconfigured, which is a lie a manager would act on."""
    from models.image_constants import ASSET_CLASS_TO_COLUMN
    from services.image_preview_service import ASSET_CLASS_COLUMNS

    assert dict(ASSET_CLASS_COLUMNS) == ASSET_CLASS_TO_COLUMN


@pytest.mark.parametrize(
    "module",
    [
        "image_calendar_service",
        "image_lineup_service",
        "image_results_service",
        "image_standings_service",
        "image_attendance_service",
        "image_rsvp_service",
        "image_weather_service",
        "image_verdict_service",
    ],
)
def test_every_builder_offers_the_division_name_as_the_key(module):
    """Eight builders, one idiom, guarded on what the template declares.

    A source-level check rather than a render: each builder needs a whole drawing, a
    template and a database to run, and what is being pinned is that none of the eight was
    missed — which is a fact about the source, not about one render.
    """
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(f"services.{module}"))

    assert "DIVISION_LOGO_FIELD in declared" in source, module
    assert "DIVISION_LOGO_ASSET, drawing.division_name" in source, module
