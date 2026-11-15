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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.services.render.image_render_service import ImageRenderService
from leaguebot.image.utils.svg_document import parse_svg_bytes

TEMPLATE = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<defs><style>.accent { fill:#3DD6F5 }</style></defs>'
    b'<rect class="accent colour-fill-accent"/></svg>'
)

#: Every module that renders, and the attribute it must pass as the tier.
CALL_SITES = {
    "image/services/aspects/image_attendance_post.py": "drawing.division_name",
    "image/services/render/image_drawing.py": "drawing.division_name",
    "image/services/aspects/image_lineup_post.py": "drawing.division_name",
    "image/services/aspects/image_results_post.py": "drawing.division_name",
    "image/services/aspects/image_rsvp_post.py": "drawing.division_name",
    "image/services/aspects/image_standings_post.py": "drawing.division_name",
    "image/services/aspects/image_verdict_post.py": "drawing.division_name",
    "image/services/aspects/image_verdict_banner_post.py": "drawing.division_name",
    "image/services/aspects/image_weather_post.py": "drawing.division_name",
    "image/cogs/image_cog.py": "context.division_name",
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
    await service.apply_tier_palette(root, "Division 1")
    assert b"#A78BFA" in __import__("lxml.etree", fromlist=["etree"]).tostring(root)


async def test_nothing_is_painted_while_the_feature_is_off():
    """And the palette is never even read — an inert feature costs no queries."""
    service, config_service = _service(enabled=False, palette={"accent": "#A78BFA"})
    root = parse_svg_bytes(TEMPLATE)
    await service.apply_tier_palette(root, "Division 1")
    from lxml import etree

    assert b"#A78BFA" not in etree.tostring(root)
    config_service.get_tier_palette.assert_not_awaited()


async def test_a_tier_with_no_colours_leaves_the_template_alone():
    service, _ = _service(palette={})
    root = parse_svg_bytes(TEMPLATE)
    before = __import__("lxml.etree", fromlist=["etree"]).tostring(root)
    await service.apply_tier_palette(root, "Division 1")
    assert __import__("lxml.etree", fromlist=["etree"]).tostring(root) == before


async def test_a_server_with_no_image_config_is_not_an_error():
    service, config_service = _service()
    config_service.get_config = AsyncMock(return_value=None)
    await service.apply_tier_palette(parse_svg_bytes(TEMPLATE), "Division 1")


# ── Where it happens, and how it fails ────────────────────────────────────

#: An image type no catalogue claims, so a render of it asks nothing of a template's fields.
_PROBE = "palette_probe_template"


async def _render(tmp_path, monkeypatch, palette, spec_builder=None):
    """Render the one-rectangle template for Division 1 with per-tier colours on and *palette*
    as the division's, with a rasteriser that writes a placeholder PNG."""
    from leaguebot.image.services.render import image_render_service
    from leaguebot.image.utils.svg_fill import FillSpec

    monkeypatch.setattr(image_render_service, "converter_available", lambda *a, **k: True)

    def _rasterise(svg, destination, canvas):
        destination.write_bytes(b"\x89PNG\r\n\x1a\n")
        return destination

    monkeypatch.setattr(image_render_service, "rasterise", _rasterise)
    template = tmp_path / "probe.svg"
    template.write_bytes(TEMPLATE)
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={_PROBE: SimpleNamespace(
        valid=True, reason=None, resolved_path=template,
    )})
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=MagicMock(per_tier_colour_enabled=True))
    config_service.get_tier_palette = AsyncMock(return_value=palette)
    return await ImageRenderService(config_service, validity).render(
        _PROBE,
        spec_builder or (lambda root: FillSpec(root=root, image_type=_PROBE)),
        output_dir=tmp_path / "out",
        division_name="Division 1",
    )


async def test_the_palette_is_applied_before_the_spec_builder(tmp_path, monkeypatch):
    """After it, a builder reading the stylesheet would read the unpalette'd file."""
    from leaguebot.image.utils.svg_fill import FillSpec

    seen: list[bytes] = []

    def builder(root):
        from lxml import etree

        seen.append(etree.tostring(root))
        return FillSpec(root=root, image_type=_PROBE)

    await _render(tmp_path, monkeypatch, {"accent": "#A78BFA"}, builder)

    assert len(seen) == 1 and b"#A78BFA" in seen[0]


@pytest.mark.xfail(
    strict=True,
    reason="#288: tier colours that cannot be applied are only logged to the host, "
    "never reported as a render notice",
)
async def test_a_failing_palette_never_stops_a_graphic_posting(tmp_path, monkeypatch):
    """A graphic in the wrong shade beats no graphic at all, but the league is told: a tier's
    colour that cannot be written into the template still lets the picture be drawn, and the
    render hands back one notice saying the tier's colours were not applied."""
    outcome = await _render(tmp_path, monkeypatch, {"accent": "#A78BFA\x00"})

    assert outcome.problem is None, outcome.problem
    assert len(outcome.png_paths) == 1
    assert [notice.image_type for notice in outcome.notices] == [_PROBE]
    assert "colour" in outcome.notices[0].detail.lower()


async def test_a_broken_palette_read_is_swallowed(tmp_path, monkeypatch):
    service, config_service = _service()
    config_service.get_config = AsyncMock(side_effect=RuntimeError("db is gone"))
    root = parse_svg_bytes(TEMPLATE)
    with pytest.raises(RuntimeError):
        await service.apply_tier_palette(root, "Division 1")
    # ...but `render` is what must not raise, and it wraps the call.
    assert "except Exception" in inspect.getsource(ImageRenderService.render)


# ── Every call site names its tier ────────────────────────────────────────

@pytest.mark.parametrize(
    "path,expected",
    [pytest.param(path, expected, id=path) for path, expected in sorted(CALL_SITES.items())],
)
def test_every_render_call_site_names_the_division(path, expected):
    source = (
        __import__("pathlib").Path(__file__).resolve().parents[2] / "src" / "leaguebot" / path
    ).read_text(encoding="utf-8")
    assert f"division_name={expected}," in source


def test_render_for_posting_hands_the_tier_on():
    """Eight of the ten paths reach `render` only through this wrapper."""
    source = inspect.getsource(ImageRenderService.render_for_posting)
    assert "division_name: str | None = None" in source
    assert "division_name=division_name," in source


def test_no_posting_path_was_missed():
    """A new posting path added without a tier is caught here rather than in production."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "leaguebot"
    callers = {
        str(p.relative_to(src)).replace(os.sep, "/")
        for p in src.rglob("*.py")
        if "render_for_posting(" in p.read_text(encoding="utf-8")
        and p.name != "image_render_service.py"
    }
    assert callers <= set(CALL_SITES), f"untracked posting paths: {callers - set(CALL_SITES)}"
