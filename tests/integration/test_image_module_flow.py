"""Integration tests for the Image module.

Exercises the full migrated schema via ``run_migrations``, then drives the service layer
the cog handlers call. The gate tests here are:

* ``test_disable_retains_configuration`` — the Principle X.6 exception (FR-004a, SC-008)
* ``test_toggles_are_inert``            — this increment changed no posted output (SC-004)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.image_constants import (  # noqa: E402
    ASPECTS,
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    TEST_KIND_TEMPLATES,
)
from services.image_config_service import ImageConfigService  # noqa: E402
from services.module_service import ModuleService  # noqa: E402

SERVER_ID = 4242


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "image_flow.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
def config_service(db_path):
    return ImageConfigService(db_path)


@pytest.fixture
def module_service(db_path):
    return ModuleService(db_path)


async def _enable(module_service, config_service):
    """The service-layer half of `_enable_images`."""
    await config_service.create_with_defaults(SERVER_ID)
    await module_service.set_images_enabled(SERVER_ID, True)


# ── T011 ──────────────────────────────────────────────────────────────────


async def test_enable_creates_defaults(module_service, config_service):
    assert await module_service.is_images_enabled(SERVER_ID) is False

    await _enable(module_service, config_service)

    assert await module_service.is_images_enabled(SERVER_ID) is True

    cfg = await config_service.get_config(SERVER_ID)
    assert cfg is not None
    assert cfg.template_directory == "resources/templates"
    assert cfg.fastest_lap_colour == "#A020F0"

    toggles = await config_service.get_toggles(SERVER_ID)
    assert len(toggles) == 8
    assert set(toggles) == set(ASPECTS)
    assert not any(toggles.values())


async def test_enable_is_idempotent_across_repeat_calls(module_service, config_service):
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "resources/mine")
    await _enable(module_service, config_service)

    cfg = await config_service.get_config(SERVER_ID)
    assert cfg.template_directory == "resources/mine"


# ── T012 — the gate for the X.6 exception ─────────────────────────────────


async def test_disable_retains_configuration(module_service, config_service):
    await _enable(module_service, config_service)

    # Customise something from every group of settable values.
    await config_service.set_field(SERVER_ID, "template_directory", "resources/my_templates")
    await config_service.set_field(SERVER_ID, "calendar_template", "my_calendar.svg")
    await config_service.set_field(SERVER_ID, "flag_directory", "resources/my_flags")
    await config_service.set_field(SERVER_ID, "fastest_lap_colour", "#00FF88")
    await config_service.set_field(SERVER_ID, "time_zone", "Europe/Lisbon")
    await config_service.set_aspect(SERVER_ID, "standings", True)

    before = await config_service.get_config(SERVER_ID)
    toggles_before = await config_service.get_toggles(SERVER_ID)

    await module_service.set_images_enabled(SERVER_ID, False)
    assert await module_service.is_images_enabled(SERVER_ID) is False

    # Nothing may be cleared by the disable itself.
    during = await config_service.get_config(SERVER_ID)
    assert during.template_directory == "resources/my_templates"
    assert await config_service.get_toggles(SERVER_ID) == toggles_before

    await _enable(module_service, config_service)

    after = await config_service.get_config(SERVER_ID)
    assert after == before, "re-enabling must restore the exact prior configuration"
    assert await config_service.get_toggles(SERVER_ID) == toggles_before
    assert toggles_before["standings"] is True


async def test_disable_preserves_every_settable_column(module_service, config_service):
    await _enable(module_service, config_service)

    probes = {}
    for column in ("template_directory", *TEMPLATE_COLUMNS, *ASSET_DIRECTORIES):
        probes[column] = f"custom_{column}"
        await config_service.set_field(SERVER_ID, column, probes[column])
    for aspect in ASPECTS:
        await config_service.set_aspect(SERVER_ID, aspect, True)

    await module_service.set_images_enabled(SERVER_ID, False)
    await _enable(module_service, config_service)

    cfg = await config_service.get_config(SERVER_ID)
    for column, expected in probes.items():
        assert getattr(cfg, column) == expected
    assert all((await config_service.get_toggles(SERVER_ID)).values())


async def test_disable_retains_notice_history(module_service, config_service, db_path):
    await _enable(module_service, config_service)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO image_render_notices "
            "(server_id, image_type, rendered_at, notice_kind, field_id, detail) "
            "VALUES (?, 'calendar_template', '2026-08-10T00:00:00+00:00', "
            "'FONT_SUBSTITUTED', 'driver_1', 'Inter unavailable')",
            (SERVER_ID,),
        )
        await db.commit()

    await module_service.set_images_enabled(SERVER_ID, False)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM image_render_notices WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == 1


# ── T013 ──────────────────────────────────────────────────────────────────


async def test_commands_gated_when_disabled(module_service, config_service):
    """A server that never enabled the module has no configuration to read."""
    assert await module_service.is_images_enabled(SERVER_ID) is False
    assert await config_service.get_config(SERVER_ID) is None
    assert not any((await config_service.get_toggles(SERVER_ID)).values())


async def test_gate_reads_flag_not_row_presence(module_service, config_service):
    """After a disable the row still exists, so the gate must read the flag."""
    await _enable(module_service, config_service)
    await module_service.set_images_enabled(SERVER_ID, False)

    assert await config_service.get_config(SERVER_ID) is not None
    assert await module_service.is_images_enabled(SERVER_ID) is False


# ── T028 — templates relocate independently (SC-002) ──────────────────────

VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"></svg>'


@pytest.fixture()
def template_dir(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(VALID_SVG)
    return tmp_path


async def test_template_relocation(module_service, config_service, template_dir):
    from services.image_validity_service import evaluate_all_templates

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_all_templates(config, root=template_dir)
    assert all(r.valid for r in reports.values()), "baseline: all fifteen resolve"

    # Point one template at a file that is not there.
    await config_service.set_field(SERVER_ID, "standings_drivers_template", "gone.svg")
    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_all_templates(config, root=template_dir)

    assert not reports["standings_drivers_template"].valid
    assert sum(1 for r in reports.values() if r.valid) == 14
    assert reports["standings_constructors_template"].valid, (
        "the other half of the standings pair must be unaffected"
    )


async def test_season_review_and_config_view_agree(
    module_service, config_service, template_dir, monkeypatch
):
    """FR-033: both surfaces render from the same AspectStatus list, so they cannot drift.

    One template is broken and one aspect enabled, then the aspect section of each
    surface is compared line for line.
    """
    import services.image_render_service as render_service
    from cogs.image_cog import ImageCog
    from cogs.season_cog import SeasonCog
    from services.image_validity_service import ImageValidityService

    monkeypatch.setattr(render_service, "converter_available", lambda **_: True)

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_field(SERVER_ID, "weather_p3_sprint_template", "gone.svg")
    await config_service.set_aspect(SERVER_ID, "weather", True)

    monkeypatch.setattr(
        "utils.paths.PROJECT_ROOT", template_dir, raising=False
    )

    validity = ImageValidityService(config_service, module_service)

    class _Bot:
        image_config_service = config_service
        image_validity_service = validity
        module_service_attr = module_service

    bot = _Bot()
    bot.module_service = module_service

    image_cog = ImageCog.__new__(ImageCog)
    image_cog.bot = bot
    season_cog = SeasonCog.__new__(SeasonCog)
    season_cog.bot = bot

    view_lines = await image_cog.build_aspect_section(SERVER_ID)
    review_lines = await season_cog._build_image_review_section(SERVER_ID)

    def aspect_lines(lines):
        return [ln.strip() for ln in lines if ln.strip().startswith(("✅", "❌", "⚠️", "↳"))]

    assert aspect_lines(view_lines) == aspect_lines(review_lines)
    assert any("phase 3" in ln.lower() and "sprint" in ln.lower() for ln in review_lines)


# ── T068 — rendering from sample data, with no season (FR-036, SC-005) ────

RICH_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" width="600" height="400">'
    '<text id="title" style="font-family:Arial;font-size:24px">t</text>'
    '<text id="driver_1" style="font-family:Arial;font-size:18px;inline-size:120px">d</text>'
    '<rect id="box" x="10" y="200" width="300" height="80"/>'
    '<text id="justification" style="font-family:Arial;font-size:18px;'
    'line-height:1.3;shape-inside:url(#box)">j</text>'
    "</svg>"
).encode()


def _render_service(db_path, config_service, module_service):
    from services.image_render_service import ImageRenderService
    from services.image_validity_service import ImageValidityService

    return ImageRenderService(
        db_path, config_service, ImageValidityService(config_service, module_service)
    )


async def test_render_without_season(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """Every kind renders on a server with no season configured at all."""
    from services.image_render_service import converter_available
    from services.image_sample_data import build_spec

    if not converter_available(use_cache=False):
        pytest.skip("rasteriser not installed on this host")

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    for filename in TEMPLATE_COLUMNS.values():
        (template_dir / "templates" / filename).write_bytes(RICH_TEMPLATE)

    service = _render_service(db_path, config_service, module_service)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM seasons WHERE server_id = ?", (SERVER_ID,))
        assert (await cursor.fetchone())[0] == 0, "precondition: no season exists"

    for kind, templates in TEST_KIND_TEMPLATES.items():
        for template_key in templates:
            outcome = await service.render(
                SERVER_ID,
                template_key,
                lambda root, k=template_key: build_spec(k, root),
                output_dir=tmp_path / kind,
            )
            assert outcome.problem is None, f"{kind}/{template_key}: {outcome.problem}"
            assert len(outcome.png_paths) == 1
            assert outcome.png_paths[0].exists()
            assert outcome.png_paths[0].stat().st_size > 0


async def test_wrapped_text_lands_inside_its_box_in_the_rasterised_png(tmp_path):
    """A PNG-level regression guard for the shape-inside trap.

    Inkscape treats a `<text>` carrying any `shape-inside` declaration — `none`
    included — as SVG2 flowed text and ignores the per-tspan positions, collapsing the
    field to the top edge. Every SVG-level assertion still passed while this was broken,
    because the coordinates in the markup were correct; only the rasterised output
    showed it. Hence this test looks at pixels.
    """
    from services.image_render_service import converter_available, rasterise
    from utils.svg_document import parse_svg_bytes
    from utils.svg_fill import FillSpec, fill

    if not converter_available(use_cache=False):
        pytest.skip("rasteriser not installed on this host")

    PIL = pytest.importorskip("PIL.Image")

    template = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">'
        '<rect width="400" height="300" fill="#FFFFFF"/>'
        '<rect id="box" x="20" y="120" width="360" height="150" fill="none"/>'
        '<text id="j" style="font-family:Arial;font-size:16px;line-height:1.3;'
        'fill:#000000;shape-inside:url(#box)">placeholder</text>'
        "</svg>"
    ).encode()

    root = parse_svg_bytes(template)
    result = fill(
        FillSpec(root=root, text={"j": "Alpha bravo charlie delta echo foxtrot golf. " * 3})
    )
    png = rasterise(result.svg, tmp_path / "wrap.png", result.canvas)

    image = PIL.open(png).convert("L")
    width, height = image.size

    def ink_in(top: int, bottom: int) -> int:
        return sum(
            1
            for y in range(top, bottom)
            for x in range(width)
            if image.getpixel((x, y)) < 128
        )

    above_box = ink_in(0, 110)
    inside_box = ink_in(120, min(275, height))

    assert inside_box > 200, "the wrapped text did not render inside its box"
    assert above_box == 0, "text leaked above the box — shape-inside was left on the element"


async def test_absent_converter_is_reported_and_no_render_attempted(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """SC-007: the reason is stated and nothing is rendered while the binary is missing."""
    import services.image_render_service as render_module

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    monkeypatch.setattr(render_module, "converter_available", lambda **_: False)

    attempted = []
    monkeypatch.setattr(
        render_module,
        "rasterise",
        lambda *a, **k: attempted.append(1),
    )

    service = _render_service(db_path, config_service, module_service)
    outcome = await service.render(
        SERVER_ID, "calendar_template", lambda root: None, output_dir=tmp_path
    )

    assert outcome.problem is not None
    assert "inkscape" in outcome.problem.lower()
    assert outcome.png_paths == []
    assert attempted == [], "a render was attempted with no converter present"


async def test_absent_converter_message_names_the_binary_and_the_env_var():
    from services.image_render_service import converter_absent_message

    message = converter_absent_message()
    assert "Inkscape" in message
    assert "INKSCAPE" in message
    assert "package" in message.lower()


async def test_absent_converter_makes_enabled_aspects_invalid_at_review(
    module_service, config_service, template_dir, monkeypatch
):
    import services.image_render_service as render_module
    from models.image_module import STATE_ENABLED_INVALID
    from services.image_validity_service import ImageValidityService

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    monkeypatch.setattr(render_module, "converter_available", lambda **_: False)

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_aspect(SERVER_ID, "calendar", True)

    validity = ImageValidityService(config_service, module_service)
    statuses = {s.aspect: s for s in await validity.aspect_statuses(SERVER_ID)}

    assert statuses["calendar"].state == STATE_ENABLED_INVALID
    assert any("converter" in r.lower() for r in statuses["calendar"].blocking_reasons)


async def test_multi_variant_kinds_cover_two_templates():
    """FR-040: four kinds must return both of their variants."""
    multi = {k: v for k, v in TEST_KIND_TEMPLATES.items() if len(v) > 1}
    assert set(multi) == {"results", "standings", "weather-p2", "weather-p3"}
    assert all(len(v) == 2 for v in multi.values())


async def test_render_raises_notices_without_failing(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """A substituted font and a truncated field are notices, not problems (XIV.4)."""
    from services.image_render_service import converter_available

    if not converter_available(use_cache=False):
        pytest.skip("rasteriser not installed on this host")

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    (template_dir / "templates" / "verdicts_template.svg").write_bytes(RICH_TEMPLATE)

    from services.image_sample_data import build_spec

    service = _render_service(db_path, config_service, module_service)
    outcome = await service.render(
        SERVER_ID,
        "verdicts_template",
        lambda root: build_spec("verdicts_template", root),
        output_dir=tmp_path,
    )

    assert outcome.problem is None
    assert outcome.png_paths
    kinds = {n.notice_kind for n in outcome.notices}
    assert "INLINE_SIZE_TRUNCATED" in kinds or "WRAP_TRUNCATED" in kinds

    # Notices are persisted for the audit trail (Principle V, XIV.4).
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM image_render_notices WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == len(outcome.notices) > 0


async def test_render_problem_yields_no_image(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """png_paths is empty whenever problem is set — never a partial image."""
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_field(SERVER_ID, "calendar_template", "absent.svg")

    service = _render_service(db_path, config_service, module_service)
    outcome = await service.render(
        SERVER_ID, "calendar_template", lambda root: None, output_dir=tmp_path
    )

    assert outcome.problem is not None
    assert outcome.png_paths == []
    assert outcome.ok is False


async def test_render_is_off_the_event_loop(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """The rasteriser must be reached through asyncio.to_thread, not called inline.

    A blocking subprocess on the event loop stalls the scheduler, the retry worker and
    every in-flight interaction. It passes every unit test and degrades production, so
    it is asserted structurally.
    """
    import threading

    from services.image_render_service import converter_available
    from services.image_sample_data import build_spec

    if not converter_available(use_cache=False):
        pytest.skip("rasteriser not installed on this host")

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    (template_dir / "templates" / "calendar_template.svg").write_bytes(RICH_TEMPLATE)

    main_thread = threading.get_ident()
    seen: list[int] = []

    import services.image_render_service as render_module

    original = render_module.rasterise

    def _recording(svg, destination, canvas):
        seen.append(threading.get_ident())
        return original(svg, destination, canvas)

    monkeypatch.setattr(render_module, "rasterise", _recording)

    service = _render_service(db_path, config_service, module_service)
    outcome = await service.render(
        SERVER_ID,
        "calendar_template",
        lambda root: build_spec("calendar_template", root),
        output_dir=tmp_path,
    )

    assert outcome.problem is None
    assert seen, "rasterise was never called"
    assert seen[0] != main_thread, "rasterise ran on the event loop thread"


# ── T049/T050 — the fastest-lap contrast path (FR-026, FR-026a, FR-027) ───


def _image_cog(config_service, module_service):
    from cogs.image_cog import ImageCog
    from services.image_validity_service import ImageValidityService

    class _Bot:
        pass

    bot = _Bot()
    bot.image_config_service = config_service
    bot.module_service = module_service
    bot.image_validity_service = ImageValidityService(config_service, module_service)

    cog = ImageCog.__new__(ImageCog)
    cog.bot = bot
    return cog


def _race_template(background_markup: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1084">'
        f"{background_markup}"
        "</svg>"
    ).encode()


async def test_contrast_is_measured_against_the_declared_background(
    module_service, config_service, template_dir, monkeypatch
):
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    (template_dir / "templates" / "results_race_template.svg").write_bytes(
        _race_template('<rect id="fastest_lap_background" fill="#FFFFFF"/>')
    )

    cog = _image_cog(config_service, module_service)
    ratio, background, problem = await cog._measure_fastest_lap_contrast(SERVER_ID, "#000000")

    assert problem is None
    assert background == "#FFFFFF"
    assert ratio == pytest.approx(21.0, abs=0.01)


async def test_contrast_reads_the_stylesheet_not_just_the_attribute(
    module_service, config_service, template_dir, monkeypatch
):
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    # The stylesheet wins over the presentation attribute, so the measured background
    # must be the black the template actually paints, not the white attribute.
    (template_dir / "templates" / "results_race_template.svg").write_bytes(
        _race_template(
            "<style>#fastest_lap_background { fill: #000000; }</style>"
            '<rect id="fastest_lap_background" fill="#FFFFFF"/>'
        )
    )

    cog = _image_cog(config_service, module_service)
    ratio, background, problem = await cog._measure_fastest_lap_contrast(SERVER_ID, "#FFFFFF")

    assert problem is None
    assert background == "#000000"
    assert ratio == pytest.approx(21.0, abs=0.01)


async def test_contrast_unmeasurable_when_template_is_invalid(
    module_service, config_service, template_dir, monkeypatch
):
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_field(SERVER_ID, "results_race_template", "gone.svg")

    cog = _image_cog(config_service, module_service)
    ratio, background, problem = await cog._measure_fastest_lap_contrast(SERVER_ID, "#A020F0")

    assert ratio is None and background is None
    assert "invalid" in problem.lower()


async def test_contrast_unmeasurable_when_background_element_is_absent(
    module_service, config_service, template_dir, monkeypatch
):
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    (template_dir / "templates" / "results_race_template.svg").write_bytes(
        _race_template('<rect id="something_else" fill="#FFFFFF"/>')
    )

    cog = _image_cog(config_service, module_service)
    ratio, background, problem = await cog._measure_fastest_lap_contrast(SERVER_ID, "#A020F0")

    assert ratio is None and background is None
    assert "fastest_lap_background" in problem
    # It must be reported as unmeasurable, not as a template validity failure.
    from services.image_validity_service import evaluate_all_templates

    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_all_templates(config, root=template_dir)
    assert reports["results_race_template"].valid


async def test_contrast_unmeasurable_when_fill_is_a_gradient(
    module_service, config_service, template_dir, monkeypatch
):
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    (template_dir / "templates" / "results_race_template.svg").write_bytes(
        _race_template('<rect id="fastest_lap_background" fill="url(#grad)"/>')
    )

    cog = _image_cog(config_service, module_service)
    ratio, background, problem = await cog._measure_fastest_lap_contrast(SERVER_ID, "#A020F0")

    assert ratio is None and background is None
    assert "not" in problem.lower() and "plain colour" in problem.lower()


# ── T040 — asset directories relocate independently (SC-002) ──────────────


async def test_asset_directory_independence(module_service, config_service, tmp_path, monkeypatch):
    from services.image_validity_service import evaluate_directories

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", tmp_path, raising=False)
    await _enable(module_service, config_service)

    # Give every asset directory a real folder.
    for column, (_cmd, default) in ASSET_DIRECTORIES.items():
        (tmp_path / default).mkdir(parents=True, exist_ok=True)

    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_directories(config, root=tmp_path)
    assert all(r.valid for r in reports.values()), "baseline: all seven resolve"

    for column in ASSET_DIRECTORIES:
        await config_service.set_field(SERVER_ID, column, "resources/absent")
        config = await config_service.get_config(SERVER_ID)
        reports = evaluate_directories(config, root=tmp_path)

        assert not reports[column].valid
        assert sum(1 for r in reports.values() if r.valid) == 6, (
            f"relocating {column} disturbed another asset directory"
        )
        assert "not found" in reports[column].reason.lower()
        assert str(reports[column].resolved_path).endswith("absent")

        await config_service.set_field(SERVER_ID, column, ASSET_DIRECTORIES[column][1])


async def test_asset_directory_escaping_root_is_reported(
    module_service, config_service, tmp_path, monkeypatch
):
    from services.image_validity_service import evaluate_directories

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", tmp_path, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "flag_directory", "../../elsewhere")

    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_directories(config, root=tmp_path)

    assert not reports["flag_directory"].valid
    assert "outside the project root" in reports["flag_directory"].reason


# ── T036 — the gate for the whole increment (FR-017a, SC-004) ─────────────


async def test_toggles_are_inert(module_service, config_service, template_dir):
    """With every aspect toggled on, no source module's output path changes.

    This increment delivers the configuration surface and the diagnostic only. The
    toggles are stored and reported; nothing reads them to decide what to post. The
    assertion is structural: no module outside the image module imports or queries the
    toggle state, so no posting path can branch on it.
    """
    import pathlib

    await _enable(module_service, config_service)
    for aspect in ASPECTS:
        await config_service.set_aspect(SERVER_ID, aspect, True)
    assert all((await config_service.get_toggles(SERVER_ID)).values())

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    image_module_files = {
        "image_cog.py",
        "image_config_service.py",
        "image_validity_service.py",
        "image_render_service.py",
        "image_constants.py",
        "image_module.py",
    }

    offenders = []
    for path in src.rglob("*.py"):
        if path.name in image_module_files:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("image_aspect_toggles", "get_toggles", "is_aspect_enabled"):
            if marker in text:
                offenders.append(f"{path.name}: {marker}")

    assert not offenders, (
        "a source module reads image toggle state, so output could branch on it: "
        + ", ".join(offenders)
    )


async def test_no_source_module_posting_path_imports_the_render_service(
    module_service, config_service
):
    """The render service must not be reachable from a posting path in this increment."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    posting_services = [
        "results_post_service.py",
        "standings_service.py",
        "rsvp_service.py",
        "verdict_announcement_service.py",
        "mystery_notice_service.py",
        "phase1_service.py",
        "phase2_service.py",
        "phase3_service.py",
    ]

    for name in posting_services:
        path = src / "services" / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "image_render_service" not in text, f"{name} reaches the render service"
        assert "image_config_service" not in text, f"{name} reads image config"


# ── T037 — the third state when a source module is disabled ───────────────


async def test_aspect_enabled_while_source_module_disabled(
    module_service, config_service, template_dir, monkeypatch
):
    import services.image_render_service as render_service
    from models.image_module import STATE_ENABLED_INVALID
    from services.image_validity_service import ImageValidityService

    monkeypatch.setattr(render_service, "converter_available", lambda **_: True)
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_aspect(SERVER_ID, "standings", True)

    # The results module backs the standings aspect and is disabled on this server.
    assert await module_service.is_results_enabled(SERVER_ID) is False

    validity = ImageValidityService(config_service, module_service)
    statuses = {s.aspect: s for s in await validity.aspect_statuses(SERVER_ID)}

    standings = statuses["standings"]
    assert standings.state == STATE_ENABLED_INVALID
    assert any("results" in r.lower() for r in standings.blocking_reasons)


async def test_toggle_state_survives_into_the_wiring_increment(
    module_service, config_service
):
    """A toggle set now is still set later — the league does not re-enter it."""
    await _enable(module_service, config_service)
    await config_service.set_aspect(SERVER_ID, "weather", True)
    await config_service.set_aspect(SERVER_ID, "verdicts", True)

    toggles = await config_service.get_toggles(SERVER_ID)
    assert toggles["weather"] is True
    assert toggles["verdicts"] is True
    assert toggles["calendar"] is False


async def test_every_template_is_independently_relocatable(
    module_service, config_service, template_dir
):
    from services.image_validity_service import evaluate_all_templates

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    for column in TEMPLATE_COLUMNS:
        await config_service.set_field(SERVER_ID, column, "absent.svg")
        config = await config_service.get_config(SERVER_ID)
        reports = evaluate_all_templates(config, root=template_dir)

        assert not reports[column].valid
        assert sum(1 for r in reports.values() if r.valid) == 14, (
            f"relocating {column} disturbed another template"
        )

        await config_service.set_field(SERVER_ID, column, TEMPLATE_COLUMNS[column])
