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


@pytest.fixture()
def no_packaged_tier(tmp_path, monkeypatch):
    """Empty the packaged fallback tier, so path 4 can still be reached.

    Since v6.0.0 the repository's own ``resources/defaults/<class>/fallback.svg`` answers
    a miss the configured directory cannot (047 FR-040). A test that means to reach the
    *fatal* outcome must therefore put the packaged tier out of view, or it will quietly
    be testing the third path instead of the fourth.
    """
    import utils.paths as paths_module

    empty = tmp_path / "no_project_root"
    empty.mkdir()
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", empty, raising=False)
    return empty


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
# 047 — the second tier: the packaged directory answers a miss the configured
#       directory cannot. Four paths, and no fifth.
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def packaged(tmp_path):
    directory = tmp_path / "packaged"
    directory.mkdir()
    return directory


def test_path_one_the_datum_s_own_file_in_the_configured_directory(flags, packaged):
    (flags / "dutch.svg").write_bytes(SVG)
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)

    resolution = resolve_asset(flags, "Dutch", packaged=packaged)

    assert resolution.found
    assert resolution.path == flags / "dutch.svg"
    assert resolution.from_packaged is False


def test_path_two_the_configured_fallback_wins_over_the_packaged_one(flags, packaged):
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)

    resolution = resolve_asset(flags, "Dutch", packaged=packaged)

    assert resolution.used_fallback
    assert resolution.path == flags / FALLBACK_ASSET_NAME
    assert resolution.from_packaged is False


def test_path_three_the_packaged_fallback_answers_where_the_configured_one_cannot(
    flags, packaged
):
    """047 FR-040. A league need no longer place a fallback of its own."""
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)

    resolution = resolve_asset(flags, "Dutch", packaged=packaged)

    assert resolution.used_fallback
    assert resolution.path == packaged / FALLBACK_ASSET_NAME
    assert resolution.from_packaged is True


def test_path_four_neither_tier_holds_a_fallback(flags, packaged):
    resolution = resolve_asset(flags, "Dutch", packaged=packaged)

    assert resolution.missing
    assert resolution.path is None


def test_the_packaged_tier_supplies_a_fallback_and_never_the_datum_s_own_file(
    flags, packaged
):
    """FR-042, and the negative that matters most.

    A file of the datum's own name sitting in the packaged directory must **not** be drawn
    for a league that did not supply it. Only `fallback.svg` is read from that tier.
    """
    (packaged / "dutch.svg").write_bytes(SVG)
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)

    resolution = resolve_asset(flags, "Dutch", packaged=packaged)

    assert resolution.used_fallback
    assert resolution.path == packaged / FALLBACK_ASSET_NAME
    assert resolution.path != packaged / "dutch.svg"


def test_omitting_the_packaged_tier_keeps_the_single_tier_behaviour(flags):
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert resolve_asset(flags, "Dutch").used_fallback
    assert resolve_asset(flags, "Dutch").from_packaged is False


def test_the_two_fallback_paths_are_indistinguishable_to_a_caller(flags, packaged):
    """FR-041: the same notice. Which tier answered is not something a league can act on."""
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)
    from_packaged_tier = resolve_asset(flags, "Dutch", packaged=packaged)

    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)
    from_configured_tier = resolve_asset(flags, "Dutch", packaged=packaged)

    assert from_packaged_tier.outcome is from_configured_tier.outcome
    assert from_packaged_tier.slug == from_configured_tier.slug


def test_has_fallback_reads_both_tiers(flags, packaged):
    """FR-043: every 'holds a fallback' means the two-tier check taken as a whole."""
    assert not has_fallback(flags, packaged=packaged)
    (packaged / FALLBACK_ASSET_NAME).write_bytes(SVG)
    assert has_fallback(flags, packaged=packaged)


def test_a_team_name_beginning_with_a_digit_resolves_to_a_valid_filename(flags):
    """047 FR-031 seen from the asset side: a filename may begin with a digit."""
    (flags / "2fast_motorsport.svg").write_bytes(SVG)

    assert resolve_asset(flags, "2Fast Motorsport").found


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


def test_a_mandatory_asset_with_no_file_and_no_fallback_is_fatal(flags, no_packaged_tier):
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


def test_a_missing_asset_with_no_fallback_is_fatal_whatever_the_field(flags, no_packaged_tier):
    """Asset resolution does not consult mandatory/optional. Uniform, both ways."""
    for catalogue in (
        FieldCatalogue(mandatory=frozenset({"row_1_flag"})),
        FieldCatalogue(optional=frozenset({"row_1_flag"})),
        None,
    ):
        result = render_with_assets(
            '<image id="row_1_flag"/>',
            image_data={"row_1_flag": ("flag", "Portuguese")},
            asset_directories={"flag": flags},
            **({"catalogue": catalogue} if catalogue is not None else {}),
        )
        assert result.unresolved, f"should be fatal for {catalogue}"
        assert "fallback.svg" in result.unresolved[0]


def test_the_fatal_miss_names_the_class_the_datum_and_both_files_looked_for(flags, no_packaged_tier):
    result = render_with_assets(
        '<image id="row_1_flag"/>',
        image_data={"row_1_flag": ("flag", "Portuguese")},
        asset_directories={"flag": flags},
    )
    detail = result.unresolved[0]

    assert "row_1_flag" in detail
    assert "flag" in detail
    assert "Portuguese" in detail
    assert "portuguese.svg" in detail
    assert "fallback.svg" in detail


def test_a_fallback_rescues_a_field_of_either_classification(flags):
    """The one thing that decides the outcome is whether the class carries a fallback."""
    (flags / FALLBACK_ASSET_NAME).write_bytes(SVG)

    for catalogue in (
        FieldCatalogue(mandatory=frozenset({"row_1_flag"})),
        FieldCatalogue(optional=frozenset({"row_1_flag"})),
    ):
        result = render_with_assets(
            '<image id="row_1_flag"/>',
            image_data={"row_1_flag": ("flag", "Portuguese")},
            asset_directories={"flag": flags},
            catalogue=catalogue,
        )
        assert result.unresolved == []
        assert {n.notice_kind for n in result.notices} == {"ASSET_FALLBACK_USED"}


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


# --------------------------------------------------------------------------
# Per-class aspect table (044, Constitution XIV.6)
# --------------------------------------------------------------------------

def test_every_asset_class_declares_an_aspect():
    """A class added later must not silently escape the aspect check.

    The check reads ASSET_CLASS_ASPECTS by class; a class present in the directory
    table but absent here would be validated against nothing at all.
    """
    from models.image_constants import ASSET_CLASS_ASPECTS, ASSET_CLASS_DIRECTORIES

    missing = set(ASSET_CLASS_DIRECTORIES) - set(ASSET_CLASS_ASPECTS)
    assert not missing, f"asset classes with no declared aspect: {sorted(missing)}"

    extra = set(ASSET_CLASS_ASPECTS) - set(ASSET_CLASS_DIRECTORIES)
    assert not extra, f"aspects declared for unknown classes: {sorted(extra)}"


def test_the_flag_class_is_three_by_two_and_the_track_class_square():
    """The two classes deliberately differ; the constraint is within a class."""
    from models.image_constants import ASSET_CLASS_ASPECTS

    assert ASSET_CLASS_ASPECTS["flag"] == pytest.approx(1.5)
    assert ASSET_CLASS_ASPECTS["track"] == pytest.approx(1.0)
    assert ASSET_CLASS_ASPECTS["flag"] != ASSET_CLASS_ASPECTS["track"]


def test_the_aspect_tolerance_admits_authoring_noise_and_catches_a_square_flag():
    from models.image_constants import ASSET_ASPECT_TOLERANCE, ASSET_CLASS_ASPECTS

    flag = ASSET_CLASS_ASPECTS["flag"]
    authored = 120.00001 / 80          # what Inkscape actually writes
    square = 120 / 120                 # a slot left at the track class's shape

    assert abs(authored - flag) / flag <= ASSET_ASPECT_TOLERANCE
    assert abs(square - flag) / flag > ASSET_ASPECT_TOLERANCE
