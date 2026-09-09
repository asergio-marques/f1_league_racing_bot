"""Injecting a tier's palette at render time (051).

Two things are pinned here and both are silent failures if they break.

**Where the injection happens.** Before the spec builder, not after. Builders read the
template's computed style — `_highlight_paints` pulls the standings highlight ink straight
out of the stylesheet — so a palette applied afterwards would leave them reading the
unpalette'd file, and a league could not drive that ink through a slot at all.

**That every posting path names its tier.** A path that forgets renders in the template's
own colours, which looks like a design choice rather than a bug, so nothing else would ever
catch it. That is the same reasoning behind
`test_no_builder_narrows_the_asset_directories_without_naming_the_logo`.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_render_service import ImageRenderService  # noqa: E402
from utils.svg_document import parse_svg_bytes  # noqa: E402

TEMPLATE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<defs><style>.accent { fill:#3DD6F5 }</style></defs>'
    b'<rect class="accent colour-fill-accent"/></svg>'
)

#: Every module that renders, and the attribute it must pass as the tier.
CALL_SITES = {
    "services/calendar_post_service.py": "drawing.division_name",
    "services/image_attendance_post.py": "drawing.division_name",
    "services/image_lineup_post.py": "drawing.division_name",
    "services/image_results_post.py": "drawing.division_name",
    "services/image_rsvp_post.py": "drawing.division_name",
    "services/image_standings_post.py": "drawing.division_name",
    "services/image_verdict_post.py": "drawing.division_name",
    "services/image_weather_post.py": "drawing.division_name",
    "cogs/image_cog.py": "context.division_name",
}


def _service(*, enabled=True, palette=None):
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value=MagicMock(per_tier_colour_enabled=enabled)
    )
    config_service.get_tier_palette = AsyncMock(return_value=palette or {})
    return ImageRenderService(config_service, MagicMock()), config_service


# ── Applying the palette ──────────────────────────────────────────────────

async def test_the_palette_is_painted_in():
    service, _ = _service(palette={"accent": "#A78BFA"})
    root = parse_svg_bytes(TEMPLATE)
    await service._apply_tier_palette(1, root, "Division 1")
    assert b"#A78BFA" in __import__("lxml.etree", fromlist=["etree"]).tostring(root)


async def test_nothing_is_painted_while_the_feature_is_off():
    """And the palette is never even read — an inert feature costs no queries."""
    service, config_service = _service(enabled=False, palette={"accent": "#A78BFA"})
    root = parse_svg_bytes(TEMPLATE)
    await service._apply_tier_palette(1, root, "Division 1")
    from lxml import etree

    assert b"#A78BFA" not in etree.tostring(root)
    config_service.get_tier_palette.assert_not_awaited()


async def test_a_tier_with_no_colours_leaves_the_template_alone():
    service, _ = _service(palette={})
    root = parse_svg_bytes(TEMPLATE)
    before = __import__("lxml.etree", fromlist=["etree"]).tostring(root)
    await service._apply_tier_palette(1, root, "Division 1")
    assert __import__("lxml.etree", fromlist=["etree"]).tostring(root) == before


async def test_a_server_with_no_image_config_is_not_an_error():
    service, config_service = _service()
    config_service.get_config = AsyncMock(return_value=None)
    await service._apply_tier_palette(1, parse_svg_bytes(TEMPLATE), "Division 1")


# ── Where it happens, and how it fails ────────────────────────────────────

def test_the_palette_is_applied_before_the_spec_builder():
    """After it, a builder reading the stylesheet would read the unpalette'd file."""
    source = inspect.getsource(ImageRenderService.render)
    assert source.index("_apply_tier_palette") < source.index("spec = spec_builder(root)")


def test_a_failing_palette_never_stops_a_graphic_posting():
    """A graphic in the wrong shade beats no graphic at all."""
    source = inspect.getsource(ImageRenderService.render)
    palette_at = source.index("_apply_tier_palette")
    assert "except Exception" in source[palette_at - 400 : palette_at + 400]


async def test_a_broken_palette_read_is_swallowed(tmp_path, monkeypatch):
    service, config_service = _service()
    config_service.get_config = AsyncMock(side_effect=RuntimeError("db is gone"))
    root = parse_svg_bytes(TEMPLATE)
    with pytest.raises(RuntimeError):
        await service._apply_tier_palette(1, root, "Division 1")
    # ...but `render` is what must not raise, and it wraps the call.
    assert "except Exception" in inspect.getsource(ImageRenderService.render)


# ── Every call site names its tier ────────────────────────────────────────

@pytest.mark.parametrize("path,expected", sorted(CALL_SITES.items()), ids=sorted(CALL_SITES))
def test_every_render_call_site_names_the_division(path, expected):
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / path
    ).read_text(encoding="utf-8")
    assert f"division_name={expected}," in source


def test_render_for_posting_hands_the_tier_on():
    """Seven of the nine paths reach `render` only through this wrapper."""
    source = inspect.getsource(ImageRenderService.render_for_posting)
    assert "division_name: str | None = None" in source
    assert "division_name=division_name," in source


def test_no_posting_path_was_missed():
    """A new posting path added without a tier is caught here rather than in production."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    callers = {
        str(p.relative_to(src)).replace(os.sep, "/")
        for p in src.rglob("*.py")
        if "render_for_posting(" in p.read_text(encoding="utf-8")
        and p.name != "image_render_service.py"
    }
    assert callers <= set(CALL_SITES), f"untracked posting paths: {callers - set(CALL_SITES)}"
