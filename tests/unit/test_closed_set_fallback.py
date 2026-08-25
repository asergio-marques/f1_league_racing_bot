"""Closed-set packaged fallback — Constitution XIV.13.

A datum that is the module's own vocabulary, rather than a value a league chose, is drawn
from the packaged directory *by its own name* when the league's own directory holds
neither it nor a fallback — because a league cannot be incomplete against a vocabulary it
did not define. It beats the generic `fallback.svg` every other datum falls back to.

Two things qualify, and they are one rule at two granularities rather than two rules:

* whole classes, where every datum they can be handed is the module's own — `marker` and
  `weather` (`CLOSED_SET_ASSET_CLASSES`);
* individual reserved slugs, in a class whose other data are the league's own — `mystery`
  and `other` in `flag` and `track` (`CLOSED_SET_ASSET_DATA`).

Everything else is unaffected, and the guard tests below hold that line: an ordinary
country is never handed a packaged file of its own name. See `test_asset_resolver.py`'s
047 section for the general rules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.image_constants import (  # noqa: E402
    FALLBACK_ASSET_NAME,
    is_closed_set_datum,
)
from utils.asset_resolver import AssetOutcome, resolve_asset  # noqa: E402
from utils.svg_document import parse_svg_bytes  # noqa: E402
from utils.svg_fill import FillSpec, fill  # noqa: E402

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"/>'


# ── resolve_asset(closed_set=True) directly ────────────────────────────────


@pytest.fixture()
def configured(tmp_path):
    """A league's own marker directory, holding neither the datum nor a fallback."""
    directory = tmp_path / "league_markers"
    directory.mkdir()
    return directory


@pytest.fixture()
def packaged(tmp_path):
    directory = tmp_path / "packaged_markers"
    directory.mkdir()
    (directory / FALLBACK_ASSET_NAME).write_bytes(SVG)
    return directory


def test_a_closed_set_class_draws_the_packaged_exact_file_over_the_generic_fallback(
    configured, packaged
):
    (packaged / "lost.svg").write_bytes(SVG)

    resolution = resolve_asset(configured, "lost", packaged=packaged, closed_set=True)

    assert resolution.outcome is AssetOutcome.FALLBACK
    assert resolution.from_packaged is True
    assert resolution.path == packaged / "lost.svg"
    assert resolution.path != packaged / FALLBACK_ASSET_NAME


def test_an_open_set_class_never_draws_the_packaged_exact_file(configured, packaged):
    """Regression guard: closed_set defaults to False and every other class is unaffected."""
    (packaged / "lost.svg").write_bytes(SVG)

    resolution = resolve_asset(configured, "lost", packaged=packaged)

    assert resolution.path == packaged / FALLBACK_ASSET_NAME


def test_a_closed_set_class_still_falls_through_to_the_generic_fallback_when_the_packaged_directory_lacks_the_exact_file(
    configured, packaged
):
    resolution = resolve_asset(configured, "lost", packaged=packaged, closed_set=True)

    assert resolution.path == packaged / FALLBACK_ASSET_NAME


def test_the_configured_directory_s_own_fallback_still_wins_over_the_packaged_exact_file(
    configured, packaged
):
    (configured / FALLBACK_ASSET_NAME).write_bytes(SVG)
    (packaged / "lost.svg").write_bytes(SVG)

    resolution = resolve_asset(configured, "lost", packaged=packaged, closed_set=True)

    assert resolution.path == configured / FALLBACK_ASSET_NAME
    assert resolution.from_packaged is False


def test_the_configured_directory_s_own_file_still_wins_outright(configured, packaged):
    (configured / "lost.svg").write_bytes(SVG)
    (packaged / "lost.svg").write_bytes(SVG)

    resolution = resolve_asset(configured, "lost", packaged=packaged, closed_set=True)

    assert resolution.outcome is AssetOutcome.FOUND
    assert resolution.path == configured / "lost.svg"


def test_a_datum_normalising_to_nothing_is_unaffected_by_closed_set(configured, packaged):
    resolution = resolve_asset(configured, "!!!", packaged=packaged, closed_set=True)

    assert resolution.path == packaged / FALLBACK_ASSET_NAME


# ── Wired through the fill pipeline (marker and weather asset classes) ─────


def _render(asset_class: str, datum: str, directory):
    body = '<image id="row_1_marker"/>'
    doc = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
        f"{body}</svg>"
    ).encode("utf-8")
    root = parse_svg_bytes(doc)
    result = fill(
        FillSpec(
            root=root,
            image_type="t",
            image_data={"row_1_marker": (asset_class, datum)},
            asset_directories={asset_class: directory},
        )
    )
    return root, result


@pytest.fixture()
def real_packaged_project_root(monkeypatch):
    """Point the packaged-tier lookup at this repository's own `resources/`.

    `_packaged_directory` in `svg_fill.py` resolves `resources/defaults/<class>` against
    `utils.paths.PROJECT_ROOT`. The repository's real root already carries every closed-set
    file; pinning it here just keeps the test from depending on the directory a runner
    happens to be launched from.
    """
    import utils.paths as paths_module

    project_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", project_root, raising=False)
    return project_root


@pytest.mark.parametrize(
    ("asset_class", "datum", "expected_file"),
    [
        ("marker", "lost", "lost.svg"),
        ("weather", "very_wet", "very_wet.svg"),
    ],
)
def test_an_incomplete_custom_directory_still_draws_the_real_closed_set_icon(
    tmp_path, real_packaged_project_root, asset_class, datum, expected_file
):
    """The scenario this exists for: a customised directory missing an entry still draws
    the module's own correct icon, not the generic grey placeholder."""
    custom_directory = tmp_path / f"league_{asset_class}"
    custom_directory.mkdir()

    root, result = _render(asset_class, datum, custom_directory)

    assert result.unresolved == []
    assert len(result.notices) == 1
    assert result.notices[0].notice_kind == "ASSET_FALLBACK_USED"

    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith(expected_file), href
    assert not href.endswith(FALLBACK_ASSET_NAME)


# ── The predicate itself ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("asset_class", "slug"),
    [
        ("marker", "gained"),
        ("marker", "unchanged"),
        ("weather", "very_wet"),
        ("weather", "clear"),
    ],
)
def test_a_closed_set_class_qualifies_whatever_the_datum(asset_class, slug):
    """The class settles it: every datum these two can be handed is the module's own."""
    assert is_closed_set_datum(asset_class, slug) is True


@pytest.mark.parametrize("asset_class", ["flag", "track", "team", "driver", "tyre"])
@pytest.mark.parametrize("slug", ["mystery", "other"])
def test_a_reserved_slug_qualifies_whatever_the_class(asset_class, slug):
    """The datum settles it where the class is otherwise the league's own vocabulary."""
    assert is_closed_set_datum(asset_class, slug) is True


@pytest.mark.parametrize(
    ("asset_class", "slug"),
    [
        ("flag", "united_kingdom"),
        ("flag", "brazil"),
        ("track", "sao_paulo"),
        ("team", "red_bull_racing"),
        ("driver", "123456789"),
        ("tyre", "soft"),
    ],
)
def test_a_leagues_own_value_never_qualifies(asset_class, slug):
    """The line that keeps `flag` out of CLOSED_SET_ASSET_CLASSES meaningful.

    Were the whole class asserted, a league supplying eighteen of its twenty flags would
    be handed *ours* for the other two — a file it never chose, under a name it did.
    """
    assert is_closed_set_datum(asset_class, slug) is False


# ── The reserved slugs, through resolve_asset ─────────────────────────────


@pytest.fixture()
def league_flags(tmp_path):
    """A league's own flag directory, holding neither the datum nor a fallback."""
    directory = tmp_path / "league_flags"
    directory.mkdir()
    return directory


@pytest.fixture()
def packaged_flags(tmp_path):
    directory = tmp_path / "packaged_flags"
    directory.mkdir()
    (directory / FALLBACK_ASSET_NAME).write_bytes(SVG)
    (directory / "mystery.svg").write_bytes(SVG)
    (directory / "other.svg").write_bytes(SVG)
    return directory


@pytest.mark.parametrize("slug", ["mystery", "other"])
def test_a_reserved_flag_is_drawn_from_the_packaged_tier_by_its_own_name(
    league_flags, packaged_flags, slug
):
    resolution = resolve_asset(
        league_flags,
        slug,
        packaged=packaged_flags,
        closed_set=is_closed_set_datum("flag", slug),
    )

    assert resolution.outcome is AssetOutcome.FALLBACK
    assert resolution.from_packaged is True
    assert resolution.drew_own_file is True
    assert resolution.path == packaged_flags / f"{slug}.svg"


@pytest.mark.parametrize("slug", ["mystery", "other"])
def test_a_leagues_own_reserved_flag_still_wins_outright(
    league_flags, packaged_flags, slug
):
    (league_flags / f"{slug}.svg").write_bytes(SVG)

    resolution = resolve_asset(
        league_flags,
        slug,
        packaged=packaged_flags,
        closed_set=is_closed_set_datum("flag", slug),
    )

    assert resolution.outcome is AssetOutcome.FOUND
    assert resolution.path == league_flags / f"{slug}.svg"


def test_an_ordinary_country_is_never_handed_the_packaged_file_of_its_own_name(
    league_flags, packaged_flags
):
    """The guard that proves the reserved names are reserved and nothing more.

    A country flag is planted in the packaged tier deliberately: nothing ships under such
    a name today, so only an explicit plant proves the rule rather than the accident.
    """
    (packaged_flags / "united_kingdom.svg").write_bytes(SVG)

    resolution = resolve_asset(
        league_flags,
        "United Kingdom",
        packaged=packaged_flags,
        closed_set=is_closed_set_datum("flag", "united_kingdom"),
    )

    assert resolution.path == packaged_flags / FALLBACK_ASSET_NAME
    assert resolution.drew_own_file is False


# ── The reserved slugs, through the fill pipeline ─────────────────────────


@pytest.mark.parametrize(
    ("asset_class", "datum", "expected_file"),
    [
        ("flag", "Mystery", "mystery.svg"),
        ("flag", "Other", "other.svg"),
        ("track", "Mystery", "mystery.svg"),
    ],
)
def test_a_league_directory_still_draws_the_shipped_reserved_asset(
    tmp_path, real_packaged_project_root, asset_class, datum, expected_file
):
    """The out-of-the-box case now that the default directory is the league's, and empty."""
    custom_directory = tmp_path / f"league_{asset_class}"
    custom_directory.mkdir()

    root, result = _render(asset_class, datum, custom_directory)

    assert result.unresolved == []
    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith(expected_file), href
    assert not href.endswith(FALLBACK_ASSET_NAME)


def test_an_ordinary_country_in_an_empty_league_directory_gets_the_placeholder(
    tmp_path, real_packaged_project_root
):
    custom_directory = tmp_path / "league_flag"
    custom_directory.mkdir()

    root, result = _render("flag", "United Kingdom", custom_directory)

    assert result.unresolved == []
    element = root.find(".//{http://www.w3.org/2000/svg}image")
    href = element.get("{http://www.w3.org/1999/xlink}href") or element.get("href")
    assert href.endswith(FALLBACK_ASSET_NAME), href


# ── What the notice says ──────────────────────────────────────────────────


def test_a_packaged_exact_hit_does_not_claim_a_fallback_was_drawn(
    tmp_path, real_packaged_project_root
):
    """The module drew its own correct file; saying "fallback" would send a manager
    looking for artwork they were never expected to supply."""
    custom_directory = tmp_path / "league_flag"
    custom_directory.mkdir()

    _root, result = _render("flag", "Mystery", custom_directory)

    assert len(result.notices) == 1
    assert result.notices[0].notice_kind == "ASSET_FALLBACK_USED"
    detail = result.notices[0].detail
    assert "mystery.svg" in detail
    assert "fallback" not in detail.lower()


def test_a_genuine_placeholder_still_says_so(tmp_path, real_packaged_project_root):
    custom_directory = tmp_path / "league_flag"
    custom_directory.mkdir()

    _root, result = _render("flag", "United Kingdom", custom_directory)

    assert len(result.notices) == 1
    assert "fallback" in result.notices[0].detail.lower()
