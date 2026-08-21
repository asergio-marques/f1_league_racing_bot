"""Closed-set packaged fallback for markers and weather — Constitution XIV.13, v6.1.0.

Markers and weather icons are not a league's own values but the module's own fixed
vocabulary (`resources/defaults/markers`, `resources/defaults/weather`). A league that
points one of these two classes at a directory of its own that is missing an entry still
draws the module's own matching closed-set icon from the packaged directory, rather than
the generic `fallback.svg` placeholder every other class falls back to. Every other class
is unaffected — see `test_asset_resolver.py`'s 047 section for its (still current) rules.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.image_constants import FALLBACK_ASSET_NAME  # noqa: E402
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
