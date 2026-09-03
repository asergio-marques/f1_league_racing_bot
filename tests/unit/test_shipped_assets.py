"""What the bot ships under `resources/defaults/` must satisfy its own authoring contract.

These files are the second resolution tier, so on a fresh install every graphic is drawn
entirely out of them — a fault here reaches every league at once, and reaches them before
they have supplied anything of their own to mask it.

The contract (resources/README.md, Constitution XIV.6):

* authored at exactly the aspect declared for the class — the generator never pads, and the
  converter smears edge pixels across any letterbox band rather than leaving it clear;
* plain SVG: no `clipPath`, no filter (a gradient is allowed);
* **no text** — text font-substitutes, so an asset carrying any would rasterise differently
  from one machine to the next.

Verified as a PNG and not as SVG markup wherever the rasteriser is what would expose the
fault, per the marker rules in `tests/conftest.py`.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    PACKAGED_ASSET_ASPECTS,
    ASSET_ASPECT_TOLERANCE,
    ASSET_CLASS_TO_COLUMN,
    packaged_directory_for,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SVG_NS = "http://www.w3.org/2000/svg"


def _shipped_assets():
    for asset_class in sorted(ASSET_CLASS_TO_COLUMN):
        directory = PROJECT_ROOT / packaged_directory_for(asset_class)
        for path in sorted(directory.glob("*.svg")):
            yield asset_class, path


SHIPPED = list(_shipped_assets())


def _stretching_data() -> frozenset[str]:
    """The filenames drawn only into slots that stretch, and so held to no class aspect.

    Composed from the two services' own tables rather than restated, so a mark added there
    without a shape of its own cannot quietly appear here as one held to 1:1.
    """
    from models.image_constants import MARK_FALLBACK_ASSET_NAME
    from services.image_attendance_service import MARK_DATA
    from services.image_standings_service import HIGHLIGHT_DATA

    return frozenset(
        [f"{datum}.svg" for datum in (*HIGHLIGHT_DATA, *MARK_DATA)]
        # Their fallback stands in for them and is drawn into the same stretching slots.
        + [MARK_FALLBACK_ASSET_NAME]
    )


STRETCHING_FILES = _stretching_data()


def _declared_size(path: Path) -> tuple[float, float]:
    root = ET.parse(path).getroot()
    width, height = root.get("width"), root.get("height")
    if width and height:
        return float(re.sub(r"[^0-9.]", "", width)), float(re.sub(r"[^0-9.]", "", height))
    _min_x, _min_y, view_w, view_h = (float(v) for v in root.get("viewBox").split())
    return view_w, view_h


def test_there_is_something_to_check():
    """Guard against the glob silently matching nothing and every test below passing."""
    assert len(SHIPPED) >= 15


@pytest.mark.parametrize(
    ("asset_class", "path"), SHIPPED, ids=[f"{c}/{p.name}" for c, p in SHIPPED]
)
def test_every_shipped_asset_declares_its_class_aspect(asset_class, path):
    """A stretched mark is exempt, and its artwork is checked for being drawable instead.

    Nothing can be asserted about the ratio of a file whose slot distorts it to fit — the
    template decides the shape, and two templates may decide differently (XIV.6, v7.5.0).
    What still binds is that the file declares a size at all, since a viewBox-less asset has
    no intrinsic geometry to stretch.

    The exemption is by **datum** and not by class, because `marker` holds both: the square
    position-change arrows, still held to 1:1, and the standings and attendance marks, whose
    slots say for themselves that they stretch.

    A class recording no shape at all is exempt on the same terms (2026-09-02). `division_logo`
    ships one file with nothing drawn in it, letterboxed into whatever a league's template
    gives it, so there is no ratio for it to be authored at — but it still has to declare a
    size, for exactly the reason a stretching mark does.
    """
    width, height = _declared_size(path)
    if path.name in STRETCHING_FILES or asset_class not in PACKAGED_ASSET_ASPECTS:
        assert width > 0 and height > 0
        return

    expected = PACKAGED_ASSET_ASPECTS[asset_class]
    assert abs((width / height) - expected) <= expected * ASSET_ASPECT_TOLERANCE, (
        f"{path.name} is {width}x{height}, which is not {expected}:1 for `{asset_class}`"
    )


@pytest.mark.parametrize(
    ("asset_class", "path"), SHIPPED, ids=[f"{c}/{p.name}" for c, p in SHIPPED]
)
def test_no_shipped_asset_carries_text_or_a_forbidden_construct(asset_class, path):
    root = ET.parse(path).getroot()
    tags = {element.tag for element in root.iter()}

    # A gradient is **not** forbidden (Constitution XIV.6, v7.4.0). It was, without a reason
    # ever being recorded, while the templates depended on one throughout; two assets whose
    # gradients share an id were then shown to render independently, the rasteriser treating
    # each referenced file as its own document. Nothing was being protected against.
    for forbidden in ("text", "tspan", "clipPath", "filter", "flowRoot"):
        assert f"{{{SVG_NS}}}{forbidden}" not in tags, f"{path.name} carries <{forbidden}>"


def test_other_svg_ships_for_the_nationality_other():
    """`Other` is a value a driver chose, not an absence, and it is the module's own
    vocabulary — so the module supplies its flag rather than leaving it to the league."""
    assert (PROJECT_ROOT / packaged_directory_for("flag") / "other.svg").is_file()


# ── As a PNG, which is the only way some faults show ──────────────────────


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


@pytest.mark.rasteriser
@pytest.mark.parametrize("name", ["other.svg", "mystery.svg", "fallback.svg"])
def test_the_shipped_flags_rasterise_to_a_three_by_two_png(tmp_path, name):
    """The browser hides what the rasteriser exposes, so this asserts on real pixels."""
    from services.image_render_service import find_converter

    source = PROJECT_ROOT / packaged_directory_for("flag") / name
    out = tmp_path / f"{name}.png"

    subprocess.run(
        [
            find_converter(),
            "--export-type=png",
            f"--export-filename={out}",
            "--export-width=240",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )

    assert out.is_file(), "the rasteriser produced nothing"
    width, height = _png_size(out.read_bytes())
    assert width == 240
    assert abs((width / height) - PACKAGED_ASSET_ASPECTS["flag"]) <= 0.01, f"{width}x{height}"


@pytest.mark.rasteriser
def test_other_svg_actually_draws_something(tmp_path):
    """A file that parses, rasterises, and is blank would pass every check above.

    Nothing here asserts *what* is drawn — only that the globe is not, say, entirely
    outside the viewBox, which no amount of reading the markup reliably catches.
    """
    from services.image_render_service import find_converter

    source = PROJECT_ROOT / packaged_directory_for("flag") / "other.svg"
    out = tmp_path / "other.png"

    subprocess.run(
        [
            find_converter(),
            "--export-type=png",
            f"--export-filename={out}",
            "--export-width=240",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )

    # A blank 240x160 PNG of one flat colour compresses far smaller than a drawn one.
    assert out.stat().st_size > 1500, "other.svg rasterised to something suspiciously blank"
