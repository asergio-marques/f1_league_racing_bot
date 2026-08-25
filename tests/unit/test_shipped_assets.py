"""What the bot ships under `resources/defaults/` must satisfy its own authoring contract.

These files are the second resolution tier, so on a fresh install every graphic is drawn
entirely out of them — a fault here reaches every league at once, and reaches them before
they have supplied anything of their own to mask it.

The contract (resources/README.md, Constitution XIV.6):

* authored at exactly the aspect declared for the class — the generator never pads, and the
  converter smears edge pixels across any letterbox band rather than leaving it clear;
* plain SVG: no `clipPath`, no gradient, no filter;
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
    ASSET_CLASS_ASPECTS,
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
    width, height = _declared_size(path)
    expected = ASSET_CLASS_ASPECTS[asset_class]

    assert abs((width / height) - expected) <= expected * ASSET_ASPECT_TOLERANCE, (
        f"{path.name} is {width}x{height}, which is not {expected}:1 for `{asset_class}`"
    )


@pytest.mark.parametrize(
    ("asset_class", "path"), SHIPPED, ids=[f"{c}/{p.name}" for c, p in SHIPPED]
)
def test_no_shipped_asset_carries_text_or_a_forbidden_construct(asset_class, path):
    root = ET.parse(path).getroot()
    tags = {element.tag for element in root.iter()}

    for forbidden in ("text", "tspan", "clipPath", "linearGradient", "radialGradient",
                      "filter", "flowRoot"):
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
    assert abs((width / height) - ASSET_CLASS_ASPECTS["flag"]) <= 0.01, f"{width}x{height}"


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
