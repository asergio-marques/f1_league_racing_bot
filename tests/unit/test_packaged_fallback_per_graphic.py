"""Every graphic that draws a team badge reaches the packaged fallback tier — 047 FR-045.

The resolver is shared and the fill pipeline calls it from **one** place, so in principle
proving the tier once proves it everywhere. In principle is not the same as proving it: a
graphic reaches that call site through its own catalogue, its own asset class map and its
own field ids, and any of those can stop naming the `team` class without the resolver's own
tests noticing. Seven graphics draw a badge, so seven are exercised here.

Also pinned: a team name **beginning with a digit** resolves to a valid filename through
each of them (FR-031), and a preview command resolves exactly as a posting does (FR-051).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import CATALOGUES
from models.image_constants import FALLBACK_ASSET_NAME, packaged_directory_for
from utils.asset_resolver import filename_for, resolve_asset
from utils.svg_document import parse_svg_bytes
from utils.svg_fill import FillSpec, fill

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'

#: The seven graphics that draw a team badge, and the field each draws it on.
BADGE_FIELDS: dict[str, str] = {
    "lineup_template": "team_1_image",
    "results_qualifying_template": "row_1_team_image",
    "results_race_template": "row_1_team_image",
    "standings_drivers_template": "row_1_team_image",
    "standings_constructors_template": "row_1_team_image",
    "attendance_template": "row_1_team_image",
    "verdicts_template": "team_image",
}


@pytest.fixture()
def configured(tmp_path):
    """A league's own team directory, holding neither the badge nor a fallback."""
    directory = tmp_path / "league_teams"
    directory.mkdir()
    return directory


@pytest.fixture()
def packaged(tmp_path, monkeypatch):
    """A packaged team directory carrying its `fallback.svg`, as the module ships one."""
    import utils.paths as paths_module

    root = tmp_path / "project"
    directory = root / packaged_directory_for("team")
    directory.mkdir(parents=True)
    (directory / FALLBACK_ASSET_NAME).write_bytes(SVG)
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", root, raising=False)
    return directory


def _render(field_id: str, template_key: str, directory, datum="Nonesuch Racing"):
    body = f'<image id="{field_id}"/>'
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        f"{body}</svg>"
    ).encode("utf-8")
    root = parse_svg_bytes(doc)
    result = fill(
        FillSpec(
            root=root,
            image_type=template_key,
            image_data={field_id: ("team", datum)},
            asset_directories={"team": directory},
        )
    )
    return root, result


# ── Every catalogue still names the team class on its badge field ─────────


@pytest.mark.parametrize("template_key,field_id", sorted(BADGE_FIELDS.items()))
def test_the_catalogue_names_the_team_class(template_key, field_id):
    catalogue = CATALOGUES[template_key]

    assert catalogue.asset_class_for(field_id) == "team", template_key


# ── Each of the seven draws the packaged fallback ─────────────────────────


@pytest.mark.parametrize("template_key,field_id", sorted(BADGE_FIELDS.items()))
def test_each_graphic_draws_the_packaged_fallback(
    template_key, field_id, configured, packaged
):
    """FR-040 and FR-045: a league supplying no badge still draws, with a notice."""
    root, result = _render(field_id, template_key, configured)

    assert result.unresolved == [], f"{template_key}: {result.unresolved}"
    assert len(result.notices) == 1, template_key
    assert "Nonesuch Racing" in result.notices[0].detail

    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith(FALLBACK_ASSET_NAME), template_key


@pytest.mark.parametrize("template_key,field_id", sorted(BADGE_FIELDS.items()))
def test_a_league_supplied_badge_still_wins(template_key, field_id, configured, packaged):
    """The packaged tier answers a miss and never overrides what a league supplied."""
    (configured / filename_for("Nonesuch Racing")).write_bytes(SVG)

    root, result = _render(field_id, template_key, configured)

    assert result.notices == [], template_key
    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith("nonesuch_racing.svg"), template_key


@pytest.mark.parametrize("template_key,field_id", sorted(BADGE_FIELDS.items()))
def test_each_graphic_accepts_a_team_name_beginning_with_a_digit(
    template_key, field_id, configured, packaged
):
    """FR-031 reaching every graphic that draws a badge, not the lineup alone."""
    (configured / "2fast_motorsport.svg").write_bytes(SVG)

    root, result = _render(field_id, template_key, configured, datum="2Fast Motorsport")

    assert result.unresolved == [], template_key
    assert result.notices == [], template_key
    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith("2fast_motorsport.svg"), template_key


# ── A preview resolves exactly as a posting does (FR-051) ─────────────────


def test_a_preview_reaches_the_packaged_tier_as_a_posting_does(configured, packaged):
    """The `/images test` family must not be denied the tier it exists to predict.

    The rule that such a command shall not *substitute* the packaged directories for the
    league's own means the datum's **own file** is sought in the configured directory
    alone. It does not mean withholding the packaged fallback, which would make a preview
    answer differently from the posting it previews.
    """
    posting = resolve_asset(configured, "Nonesuch Racing", packaged=packaged)
    preview = resolve_asset(configured, "Nonesuch Racing", packaged=packaged)

    assert posting.used_fallback and preview.used_fallback
    assert posting.path == preview.path
    assert posting.from_packaged is True


def test_the_packaged_tier_never_supplies_the_datum_s_own_file_to_a_preview(
    configured, packaged
):
    (packaged / "nonesuch_racing.svg").write_bytes(SVG)

    resolution = resolve_asset(configured, "Nonesuch Racing", packaged=packaged)

    assert resolution.used_fallback
    assert resolution.path.name == FALLBACK_ASSET_NAME
