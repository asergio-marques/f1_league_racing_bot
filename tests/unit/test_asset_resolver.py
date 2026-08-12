"""Asset resolution: normalised slug, then the directory's fallback (XIV.13).

The normalisation is asserted against the cases the proof of concept documents, because
every asset already shipped under `resources/` is named by that rule and a divergence here
would silently stop finding them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.image_constants import FALLBACK_ASSET_NAME  # noqa: E402
from utils.asset_resolver import (  # noqa: E402
    AssetOutcome,
    filename_for,
    has_fallback,
    normalise,
    resolve_asset,
)

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>'


# ── Normalisation (FR-042) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("datum", "expected"),
    [
        ("Red Bull Racing", "red_bull_racing"),
        ("São Paulo", "sao_paulo"),
        ("Emilia-Romagna", "emilia_romagna"),
        ("  McLaren  ", "mclaren"),
        ("Portugal", "portugal"),
        ("Spa-Francorchamps", "spa_francorchamps"),
        ("Mercedes-AMG PETRONAS", "mercedes_amg_petronas"),
        ("Nürburgring", "nurburgring"),
        ("ALFA ROMEO", "alfa_romeo"),
        ("Circuit de Barcelona—Catalunya", "circuit_de_barcelona_catalunya"),
    ],
)
def test_normalisation_matches_the_documented_rule(datum, expected):
    assert normalise(datum) == expected


def test_separator_is_an_underscore_not_a_hyphen():
    """v2.13.0 briefly said hyphen; it was withdrawn. Every shipped asset uses `_`."""
    assert normalise("Red Bull Racing") == "red_bull_racing"
    assert "-" not in normalise("Red-Bull-Racing")


@pytest.mark.parametrize("datum", ["", "   ", "!!!", "---", "…"])
def test_data_with_no_alphanumerics_normalise_to_empty(datum):
    assert normalise(datum) == ""


def test_runs_of_punctuation_collapse_to_one_underscore():
    assert normalise("A  --  B") == "a_b"


def test_normalisation_is_total_and_pure():
    assert normalise(None) == ""  # type: ignore[arg-type]
    assert normalise("Monza") == normalise("Monza")


def test_filename_appends_the_svg_extension():
    assert filename_for("Red Bull Racing") == "red_bull_racing.svg"


# ── Resolution and the fallback (FR-043, FR-044) ──────────────────────────


@pytest.fixture()
def flags(tmp_path):
    directory = tmp_path / "flags"
    directory.mkdir()
    (directory / "british.svg").write_bytes(SVG)
    return directory


def test_the_datum_s_own_file_is_used_when_present(flags):
    resolution = resolve_asset(flags, "British")

    assert resolution.outcome is AssetOutcome.FOUND
    assert resolution.found
    assert resolution.path.name == "british.svg"


def test_a_hyphenated_filename_is_not_found(flags):
    """Guards the slug rule from the inside: the wrong separator resolves to nothing."""
    (flags / "red-bull-racing.svg").write_bytes(SVG)
    assert resolve_asset(flags, "Red Bull Racing").missing


def test_missing_with_no_fallback(flags):
    resolution = resolve_asset(flags, "Portuguese")

    assert resolution.outcome is AssetOutcome.MISSING
    assert resolution.path is None
    assert resolution.slug == "portuguese"  # named so a notice can say what had no file


def test_fallback_is_used_when_the_directory_carries_one(flags):
    """The flag example from the brief: no Portuguese flag drawn, but a graphic still."""
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)

    resolution = resolve_asset(flags, "Portuguese")

    assert resolution.outcome is AssetOutcome.FALLBACK
    assert resolution.used_fallback
    assert resolution.path.name == FALLBACK_ASSET_NAME
    assert resolution.slug == "portuguese"


def test_the_datum_s_own_file_wins_over_the_fallback(flags):
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert resolve_asset(flags, "British").found


def test_a_datum_normalising_to_nothing_takes_the_fallback(flags):
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert resolve_asset(flags, "!!!").used_fallback


def test_a_datum_normalising_to_nothing_is_missing_without_a_fallback(flags):
    assert resolve_asset(flags, "!!!").missing


def test_a_datum_literally_named_fallback_collides_and_is_accepted(flags):
    """Spec A-007 records this as accepted rather than guarded against."""
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert resolve_asset(flags, "Fallback").found


def test_a_directory_holding_only_a_fallback_serves_every_datum(flags):
    for name in list(flags.iterdir()):
        name.unlink()
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)

    for datum in ("British", "Dutch", "Japanese"):
        assert resolve_asset(flags, datum).used_fallback


def test_an_absent_directory_is_missing_not_an_error(tmp_path):
    assert resolve_asset(tmp_path / "nope", "British").missing


def test_a_subdirectory_named_like_an_asset_is_not_a_file(flags):
    (flags / "dutch.svg").mkdir()
    assert resolve_asset(flags, "Dutch").missing


def test_resolution_does_not_try_other_extensions(flags):
    (flags / "dutch.png").write_bytes(b"x")
    assert resolve_asset(flags, "Dutch").missing


def test_has_fallback_reports_the_directory_s_tolerance(flags):
    assert not has_fallback(flags)
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert has_fallback(flags)


# ══════════════════════════════════════════════════════════════════════════
# T034 / T037 / T038 — resolution wired into the fill pipeline
# ══════════════════════════════════════════════════════════════════════════

from models.image_catalogues import FieldCatalogue  # noqa: E402
from utils.svg_document import parse_svg_bytes  # noqa: E402
from utils.svg_fill import FillSpec, fill  # noqa: E402

TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" width="400" height="300">'
    '{body}</svg>'
)


def render_with_assets(body: str, **kwargs):
    root = parse_svg_bytes(TEMPLATE.format(body=body).encode("utf-8"))
    return fill(FillSpec(root=root, image_type="t", **kwargs))


def test_a_resolved_asset_is_pointed_at_by_the_field(flags):
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "British")},
        asset_directories={"flag": flags},
    )
    out = parse_svg_bytes(result.svg)
    href = out.find(".//{http://www.w3.org/2000/svg}image").get("href")

    assert href.endswith("british.svg")
    assert result.unresolved == []
    assert result.notices == []


def test_a_fallback_is_drawn_and_names_the_datum_that_had_no_file(flags):
    """FR-043 — the brief's flag example, end to end."""
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)

    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
        catalogue=FieldCatalogue(mandatory=frozenset({"row_1_flag"})),
    )
    out = parse_svg_bytes(result.svg)

    assert result.unresolved == []          # non-fatal even though mandatory (A-008)
    assert out.find(".//{http://www.w3.org/2000/svg}image").get("href").endswith(
        FALLBACK_ASSET_NAME
    )
    assert len(result.notices) == 1
    notice = result.notices[0]
    assert notice.notice_kind == "ASSET_FALLBACK_USED"
    assert notice.field_id == "row_1_flag"
    assert "Portuguese" in notice.detail


def test_a_mandatory_asset_with_no_file_and_no_fallback_is_fatal(flags):
    """FR-044 — the graphic is meaningless without it."""
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
        catalogue=FieldCatalogue(mandatory=frozenset({"row_1_flag"})),
    )

    assert result.unresolved
    assert "row_1_flag" in result.unresolved[0]
    assert "Portuguese" in result.unresolved[0]


def test_an_optional_asset_with_no_file_and_no_fallback_is_left_out(flags):
    """FR-044 — the graphic is merely plainer."""
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
        catalogue=FieldCatalogue(optional=frozenset({"row_1_flag"})),
    )

    assert result.unresolved == []
    assert {n.notice_kind for n in result.notices} == {"OPTIONAL_FIELD_EMPTIED"}


def test_an_optional_asset_miss_removes_its_group_when_declared(flags):
    result = render_with_assets(
        '<g id="row_1_flag_group"><rect id="row_1_flag_plate"/>'
        '<image id="row_1_flag"/></g>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
        catalogue=FieldCatalogue(optional=frozenset({"row_1_flag"})),
    )
    from utils.svg_document import FieldIndex

    index = FieldIndex(parse_svg_bytes(result.svg))
    assert index.resolve("row_1_flag_plate") is None   # the plate went with it


def test_with_no_catalogue_a_miss_is_treated_as_optional(flags):
    """Fifteen empty catalogues ship in this increment; the module must stay usable."""
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
    )
    assert result.unresolved == []


def test_an_unconfigured_asset_class_is_reported(flags):
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("nonesuch", "British")},
        asset_directories={"flag": flags},
    )
    assert any("nonesuch" in line for line in result.unresolved)


def test_an_unknown_image_field_is_reported(flags):
    result = render_with_assets(
        '<image id="other"/>',
        image_data={"row_1_flag": ("flag", "British")},
        asset_directories={"flag": flags},
    )
    assert any("row_1_flag" in line for line in result.unresolved)


# ── href form: caught only by the rasteriser, never by the SVG (XIV.14) ───
#
# An absolute Windows path is not a URI. Inkscape resolves it to nothing and draws a
# broken-image icon — which a browser and a structural assertion both miss.

from utils.svg_fill import _as_href  # noqa: E402


def test_an_absolute_path_becomes_a_file_uri(tmp_path):
    target = tmp_path / "british.svg"
    target.write_bytes(SVG)

    href = _as_href(str(target))

    assert href.startswith("file:///")
    assert "\\" not in href
    assert href.endswith("british.svg")


def test_an_existing_uri_is_left_alone():
    for value in (
        "data:image/svg+xml;base64,AAAA",
        "file:///C:/assets/british.svg",
        "https://example.invalid/flag.svg",
    ):
        assert _as_href(value) == value


def test_a_relative_reference_is_left_alone():
    """A template may legally point at a file beside itself."""
    assert _as_href("british.svg") == "british.svg"
    assert _as_href("flags/british.svg") == "flags/british.svg"


def test_a_resolved_asset_is_written_as_a_uri_not_a_path(flags):
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "British")},
        asset_directories={"flag": flags},
    )
    out = parse_svg_bytes(result.svg)
    href = out.find(".//{http://www.w3.org/2000/svg}image").get("href")

    assert href.startswith("file:///"), "the rasteriser cannot resolve a bare path"
