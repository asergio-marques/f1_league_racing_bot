"""Integration tests for the Image module.

Exercises the full migrated schema via ``run_migrations``, then drives the service layer
the cog handlers call. The gate tests here are:

* ``test_disable_retains_configuration`` — the Principle X.6 exception (FR-004a, SC-008)
* ``test_toggle_reply_*`` / ``test_aspect_section_footer_*`` — the two surfaces telling a
  manager which aspects actually post, both read from ``LIVE_POSTING_ASPECTS``
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.image_constants import (  # noqa: E402
    ASPECTS,
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
)
from services.image_config_service import ImageConfigService  # noqa: E402
from services.module_service import ModuleService  # noqa: E402
from tests.support import KIND_TEMPLATES  # noqa: E402

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
    assert cfg.template_directory == "resources/defaults/templates"
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

#: Valid at every depth currently checked — see the note in test_image_validity_layers.
VALID_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_1_number">1</text>'
    b'<text id="round_1_country_name">C</text>'
    b'<text id="round_1_race_name">R</text>'
    b'<text id="round_1_date">1 Jan</text>'
    b'<rect id="round_1_vertical_crop_point" x="0" y="675" width="1" height="1"/>'
    b'<g id="team_1_group"><text id="team_1_name">T</text>'
    b'<text id="team_1_driver_1_name">N</text></g>'
    b'<g id="reserve_group"><text id="reserve_driver_1_name">N</text></g>'
    b"</svg>"
)


#: A server team configuration for the lineup's sample. RICH_TEMPLATE declares one ordinal
#: block, which draws whichever team stands first — the template no longer has to agree
#: with a league's team *list*, only to declare at least as many blocks as it fields.
SAMPLE_TEAMS = [
    SimpleNamespace(name="Test Team", max_seats=2, is_reserve=False),
    SimpleNamespace(name="Reserve", max_seats=0, is_reserve=True),
]


def _results_svg(*row_columns: bytes) -> bytes:
    """A sound results template (039), built per kind.

    The two results templates are **siblings**, and a field of the other's row catalogue is
    a fault of the file (XIV.3, v4.4.0) — so one SVG carrying both kinds' columns would be
    sound for neither.
    """
    columns = b"".join(b'<text id="row_1_%s">x</text>' % name for name in row_columns)
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="race_name">R</text>'
        b'<text id="session_name">S</text>'
        b'<text id="result_status">F</text>'
        b'<g id="row_1_group">'
        b'<text id="row_1_position">1</text>'
        b'<text id="row_1_driver_name">N</text>'
        b'<text id="row_1_team_name">T</text>'
        b'<image id="row_1_team_image"/>'
        b'<text id="row_1_postrace_penalty">-</text>'
        b'<text id="row_1_appeal_penalty">-</text>'
        b'<text id="row_1_points">0</text>'
        + columns
        + b"</g></svg>"
    )


RESULTS_QUALIFYING_SVG = _results_svg(b"best_lap", b"gap")
RESULTS_RACE_SVG = _results_svg(b"time", b"fastest_lap", b"ingame_penalty")


def _standings_svg(*row_extra: bytes) -> bytes:
    """A sound standings template (040), built per championship.

    Declares no round at all, which is sound: the results grid is an optional unit (XIV.3,
    v4.5.0) and a template declaring none of it draws a classification alone. The two are
    siblings, so each carries its own row catalogue and never the other's.
    """
    extra = b"".join(b'<text id="row_1_%s">x</text>' % name for name in row_extra)
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b'<text id="result_status">F</text>'
        b'<g id="row_1_group">'
        b'<text id="row_1_position">1</text>'
        b'<text id="row_1_team_name">T</text>'
        b'<image id="row_1_team_image"/>'
        b'<text id="row_1_points">0</text>'
        + extra
        + b"</g></svg>"
    )


STANDINGS_DRIVERS_SVG = _standings_svg(b"driver_name")
STANDINGS_CONSTRUCTORS_SVG = _standings_svg()


#: A sound attendance sheet (041): the two whole-graphic mandatories and one complete row. It
#: declares no round at all, the grid being an optional unit (XIV.3), and no position, the row
#: ordinal of a sheet being a place in the layout and not a datum (XIV.11, v4.6.0).
ATTENDANCE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<g id="row_1_group">'
    b'<text id="row_1_driver_name">N</text>'
    b'<text id="row_1_points">0</text>'
    b"</g></svg>"
)

#: A sound check-in call (041). No session at all, and none of the values a button press can
#: change — which is what makes the type static (XIV.17).
RSVP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
    b'<text id="round_format">Normal</text>'
    b'<text id="round_date">1 Jan 2026</text>'
    b'<text id="round_time">20:00 UTC</text>'
    b"</svg>"
)


#: The weather headings every phase graphic must carry (042).
_WEATHER_HEADING = (
    b'<text id="division_name">D</text>'
    b'<text id="phase_description">P</text>'
    b'<text id="round_number">1</text>'
    b'<text id="track_name">T</text>'
)


def _weather_p2_svg(sessions: int) -> bytes:
    """A sound phase 2 template. No slot and no summary: both are phase 3's alone."""
    blocks = b"".join(
        b'<g id="session_%d_group">'
        b'<text id="session_%d_name">S</text>'
        b'<text id="session_%d_slot_type">Mixed</text>'
        b"</g>" % (n, n, n)
        for n in range(1, sessions + 1)
    )
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + _WEATHER_HEADING
        + blocks
        + b"</svg>"
    )


def _weather_p3_svg(sessions: int, slots: int) -> bytes:
    """A sound phase 3 template, declaring the slot floor its variant requires (XIV.12)."""
    blocks = b""
    for n in range(1, sessions + 1):
        cells = b"".join(
            b'<g id="session_%d_slot_%d_group">'
            b'<text id="session_%d_slot_%d_label">Clear</text>'
            b"</g>" % (n, m, n, m)
            for m in range(1, slots + 1)
        )
        blocks += (
            b'<g id="session_%d_group"><text id="session_%d_name">S</text>' % (n, n)
            + cells
            + b"</g>"
        )
    return (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + _WEATHER_HEADING
        + blocks
        + b"</svg>"
    )


WEATHER_SVGS = {
    "weather_p1_template": (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        + _WEATHER_HEADING
        + b'<text id="rain_probability">30%</text>'
        + b"</svg>"
    ),
    "weather_p2_template": _weather_p2_svg(2),
    "weather_p2_sprint_template": _weather_p2_svg(4),
    "weather_p3_template": _weather_p3_svg(2, 4),
    "weather_p3_sprint_template": _weather_p3_svg(4, 3),
    "weather_mystery_template": (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
        b'<text id="division_name">D</text>'
        b'<text id="round_number">1</text>'
        b"</svg>"
    ),
}


#: A sound verdict template (043): the eight mandatory fields, and no collection at all.
VERDICTS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<text id="session_name">Race</text>'
    b'<text id="verdict_stage">Post-Race Penalty</text>'
    b'<text id="driver_name">A Driver</text>'
    b'<text id="penalty">5 seconds added</text>'
    b'<text id="description">Contact at turn four.</text>'
    b'<text id="justification">Video evidence reviewed.</text>'
    b"</svg>"
)


def sound_bytes(template_key: str) -> bytes:
    """The soundest bytes for *template_key* at the depth its type is checked to."""
    if template_key == "verdicts_template":
        return VERDICTS_SVG
    if template_key in WEATHER_SVGS:
        return WEATHER_SVGS[template_key]
    if template_key == "results_qualifying_template":
        return RESULTS_QUALIFYING_SVG
    if template_key == "results_race_template":
        return RESULTS_RACE_SVG
    if template_key == "standings_drivers_template":
        return STANDINGS_DRIVERS_SVG
    if template_key == "standings_constructors_template":
        return STANDINGS_CONSTRUCTORS_SVG
    if template_key == "attendance_template":
        return ATTENDANCE_SVG
    if template_key == "rsvp_template":
        return RSVP_SVG
    return VALID_SVG


@pytest.fixture()
def scratch_slot():
    """Empty the verdicts catalogue, so a slot exists that constrains no template.

    Two tests below render RICH_TEMPLATE — a synthetic file belonging to no image type —
    and need a slot whose catalogue will not refuse it. Verdicts served that purpose while
    it was unspecified; as of 043 every one of the fifteen carries a catalogue, so the
    condition is staged rather than borrowed. What the tests prove is unchanged: a template
    renders, and its notices are raised and persisted.

    The sample data is still the verdict's own — ``build_spec`` keys on the template slot and
    not on the catalogue — so RICH_TEMPLATE's `justification` box is filled with fabricated
    prose and its wrapping is exercised for real.
    """
    from models.image_catalogues import CATALOGUES, FieldCatalogue

    saved = dict(CATALOGUES)
    CATALOGUES["verdicts_template"] = FieldCatalogue()
    yield "verdicts_template"
    CATALOGUES.clear()
    CATALOGUES.update(saved)


@pytest.fixture()
def template_dir(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    for key, filename in TEMPLATE_COLUMNS.items():
        (directory / filename).write_bytes(sound_bytes(key))
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
        """The eight aspect lines, and the reasons hanging off them.

        Named by label rather than by marker: the review also states the driver-portrait
        settings, which are configuration rather than one of the eight aspects, and a
        marker-only filter swept those in and made the two surfaces look divergent when
        they are not.
        """
        from models.image_constants import ASPECT_LABELS

        labels = tuple(ASPECT_LABELS.values())
        kept = []
        for raw in lines:
            line = raw.strip()
            if line.startswith("↳"):
                kept.append(line)
            elif line.startswith(("✅", "❌", "⚠️")) and any(
                label in line for label in labels
            ):
                kept.append(line)
        return kept

    assert aspect_lines(view_lines) == aspect_lines(review_lines)
    assert any("phase 3" in ln.lower() and "sprint" in ln.lower() for ln in review_lines)

    # Every ❌ explains itself. A red cross and an aspect name told a manager nothing
    # about why the aspect was off, and this is the report they read before approving.
    rows = aspect_lines(review_lines)
    crosses = [i for i, line in enumerate(rows) if line.startswith("❌")]
    assert crosses, "the fixture must leave some aspect switched off"
    for index in crosses:
        assert rows[index + 1 : index + 2] and rows[index + 1].startswith("↳"), (
            f"{rows[index]!r} is reported not-OK with no explanation beneath it"
        )


# ── Which aspects actually post ───────────────────────────────────────────
#
# Both surfaces read LIVE_POSTING_ASPECTS, so they follow it when a posting path ships
# and cannot drift apart in the meantime. These assert the *shape* of the claim rather
# than its present membership, so wiring standings up does not falsify them.


def test_live_and_pending_aspects_partition_the_eight():
    """Every aspect is one or the other, and neither set is invented."""
    from models.image_constants import LIVE_POSTING_ASPECTS, PENDING_POSTING_ASPECTS

    assert LIVE_POSTING_ASPECTS <= set(ASPECTS)
    assert set(PENDING_POSTING_ASPECTS) | set(LIVE_POSTING_ASPECTS) == set(ASPECTS)
    assert not set(PENDING_POSTING_ASPECTS) & LIVE_POSTING_ASPECTS
    # Report order is preserved, so the footer names them as the list above shows them.
    assert list(PENDING_POSTING_ASPECTS) == [
        a for a in ASPECTS if a in set(PENDING_POSTING_ASPECTS)
    ]


def test_toggle_reply_for_a_live_aspect_makes_no_not_yet_claim():
    """An aspect that posts must not tell a manager it does nothing (the 035 wording)."""
    from cogs.image_cog import toggle_enabled_lines
    from models.image_constants import ASPECT_LABELS, LIVE_POSTING_ASPECTS

    for aspect in sorted(LIVE_POSTING_ASPECTS):
        lines = toggle_enabled_lines(aspect, ASPECT_LABELS[aspect], [])
        text = "\n".join(lines)
        assert "not yet in effect" not in text.lower(), aspect
        assert text.startswith(f"✅ **{ASPECT_LABELS[aspect]}** image output **enabled**.")


def test_toggle_reply_for_a_pending_aspect_says_so():
    """An aspect with no posting path still warns, or the manager thinks it broken."""
    from cogs.image_cog import toggle_enabled_lines
    from models.image_constants import ASPECT_LABELS, PENDING_POSTING_ASPECTS

    for aspect in PENDING_POSTING_ASPECTS:
        lines = toggle_enabled_lines(aspect, ASPECT_LABELS[aspect], [])
        text = "\n".join(lines)
        assert "Not yet in effect" in text, aspect
        assert "/images test" in text, aspect


def test_toggle_reply_keeps_blocking_reasons():
    """The not-yet notice must not have displaced the invalid-configuration warning."""
    from cogs.image_cog import toggle_enabled_lines

    lines = toggle_enabled_lines("calendar", "Calendar", ["the template is missing"])
    text = "\n".join(lines)
    assert "would not produce an image as configured" in text
    assert "↳ the template is missing" in text


async def test_aspect_section_footer_names_only_pending_aspects(
    module_service, config_service, monkeypatch
):
    """`/images config view` must not disclaim the seven aspects that do post."""
    import services.image_render_service as render_service
    from cogs.image_cog import ImageCog
    from models.image_constants import (
        ASPECT_LABELS,
        LIVE_POSTING_ASPECTS,
        PENDING_POSTING_ASPECTS,
    )
    from services.image_validity_service import ImageValidityService

    monkeypatch.setattr(render_service, "converter_available", lambda **_: True)
    await _enable(module_service, config_service)

    validity = ImageValidityService(config_service, module_service)

    class _Bot:
        image_config_service = config_service
        image_validity_service = validity

    bot = _Bot()
    bot.module_service = module_service

    cog = ImageCog.__new__(ImageCog)
    cog.bot = bot

    lines = await cog.build_aspect_section(SERVER_ID)
    footer = "\n".join(ln for ln in lines if ln.startswith("_"))

    if not PENDING_POSTING_ASPECTS:
        assert footer == ""
        return

    assert "not yet in effect" in footer.lower()
    for aspect in PENDING_POSTING_ASPECTS:
        assert ASPECT_LABELS[aspect] in footer, aspect
    # The seven that post are named in the body above, never in the disclaimer.
    for aspect in LIVE_POSTING_ASPECTS:
        assert ASPECT_LABELS[aspect] not in footer, aspect


# ── T068 — rendering from sample data, with no season (FR-036, SC-005) ────

RICH_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" width="600" height="400">'
    '<text id="title" style="font-family:Arial;font-size:24px">t</text>'
    '<text id="driver_1" style="font-family:Arial;font-size:18px;inline-size:120px">d</text>'
    '<rect id="box" x="10" y="200" width="300" height="80"/>'
    '<text id="justification" style="font-family:Arial;font-size:18px;'
    'line-height:1.3;shape-inside:url(#box)">j</text>'
    # The calendar has a ratified catalogue (037), so a template it renders from must
    # carry its mandatory fields. Two rounds, so the sample's "one fewer than declared"
    # rule fabricates one and the crop lands on round 1's point rather than the canvas.
    '<text id="division_name">d</text>'
    '<text id="round_1_number">1</text>'
    '<text id="round_1_country_name">c</text>'
    '<text id="round_1_race_name">r</text>'
    '<text id="round_1_date">d</text>'
    '<rect id="round_1_vertical_crop_point" x="0" y="200" width="1" height="1"/>'
    '<text id="round_2_number">2</text>'
    '<text id="round_2_country_name">c</text>'
    '<text id="round_2_race_name">r</text>'
    '<text id="round_2_date">d</text>'
    '<rect id="round_2_vertical_crop_point" x="0" y="400" width="1" height="1"/>'
    # The lineup's fields are ordinal since v6.0.0, so one block serves whatever team the
    # division puts at it. The block and the reserve are both carried whatever a league's
    # teams are, there being nothing of a league in either.
    '<g id="team_1_group">'
    '<text id="team_1_name">t</text>'
    '<text id="team_1_driver_1_name">n</text>'
    '<text id="team_1_driver_2_name">n</text>'
    "</g>"
    '<g id="reserve_group">'
    '<text id="reserve_driver_1_name">n</text>'
    '<text id="reserve_driver_2_name">n</text>'
    "</g>"
    "</svg>"
).encode()


def _validity_service(config_service, module_service):
    from services.image_validity_service import ImageValidityService

    return ImageValidityService(config_service, module_service)


def _render_service(config_service, module_service):
    from services.image_render_service import ImageRenderService
    from services.image_validity_service import ImageValidityService

    return ImageRenderService(
        config_service, ImageValidityService(config_service, module_service)
    )


@pytest.mark.rasteriser
async def test_render_without_season(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path,
    scratch_slot,
):
    """Every kind renders on a server with no season configured at all."""
    from tests.support.image_sample_data import build_spec

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    for key, filename in TEMPLATE_COLUMNS.items():
        # The results (039), standings (040), attendance (041) and weather (042) templates
        # carry a populated catalogue, so the rich sample bytes would fail Layer 2 for them;
        # verdicts alone is still checked to Layer 1 and draws the generic sample filler.
        body = (
            sound_bytes(key)
            if key.startswith(("results_", "standings_", "attendance_", "rsvp_", "weather_"))
            else RICH_TEMPLATE
        )
        (template_dir / "templates" / filename).write_bytes(body)

    # PROJECT_ROOT is patched away from the repository, so the packaged asset directories
    # are not where the samples look for them. A results template declares a team badge and
    # a flag on every row, and an asset class with neither its file nor a fallback is fatal
    # by design (XIV.13) — so the fallbacks a real deployment ships are recreated here.
    for folder in ("teams", "flags", "tyres", "drivers", "tracks", "weather", "markers"):
        assets = template_dir / "resources" / "defaults" / folder
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "fallback.svg").write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>'
        )

    service = _render_service(config_service, module_service)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM seasons WHERE server_id = ?", (SERVER_ID,))
        assert (await cursor.fetchone())[0] == 0, "precondition: no season exists"

    for kind, templates in KIND_TEMPLATES.items():
        for template_key in templates:
            outcome = await service.render(
                SERVER_ID,
                template_key,
                # The lineup's sample still needs the server's team configuration — a
                # badge and a name are drawn from it — though no longer to match the
                # template's fields. The cog fetches it; here it is supplied directly.
                lambda root, k=template_key: build_spec(k, root, teams=SAMPLE_TEAMS),
                output_dir=tmp_path / kind,
            )
            assert outcome.problem is None, f"{kind}/{template_key}: {outcome.problem}"
            assert len(outcome.png_paths) == 1
            assert outcome.png_paths[0].exists()
            assert outcome.png_paths[0].stat().st_size > 0


@pytest.mark.rasteriser
async def test_wrapped_text_lands_inside_its_box_in_the_rasterised_png(tmp_path):
    """A PNG-level regression guard for the shape-inside trap.

    Inkscape treats a `<text>` carrying any `shape-inside` declaration — `none`
    included — as SVG2 flowed text and ignores the per-tspan positions, collapsing the
    field to the top edge. Every SVG-level assertion still passed while this was broken,
    because the coordinates in the markup were correct; only the rasterised output
    showed it. Hence this test looks at pixels.
    """
    from services.image_render_service import rasterise
    from utils.svg_document import parse_svg_bytes
    from utils.svg_fill import FillSpec, fill

    from PIL import Image  # noqa: PLC0415

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

    image = Image.open(png).convert("L")
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

    service = _render_service(config_service, module_service)
    outcome = await service.render(
        SERVER_ID, "calendar_template", lambda root: None, output_dir=tmp_path
    )

    assert outcome.problem is not None
    assert "inkscape" in outcome.problem.detail.lower()
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
    from services.image_validity_service import (
        PLAIN_NO_RASTERISER,
        PLAIN_REMEDY_ASK_OPERATOR,
        ImageValidityService,
    )

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    monkeypatch.setattr(render_module, "converter_available", lambda **_: False)

    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_aspect(SERVER_ID, "calendar", True)

    validity = ImageValidityService(config_service, module_service)
    statuses = {s.aspect: s for s in await validity.aspect_statuses(SERVER_ID)}

    assert statuses["calendar"].state == STATE_ENABLED_INVALID
    line = next(
        r for r in statuses["calendar"].blocking_reasons if PLAIN_NO_RASTERISER in r
    )
    assert PLAIN_REMEDY_ASK_OPERATOR in line


async def test_multi_variant_kinds_cover_two_templates():
    """FR-040: four kinds must return both of their variants."""
    multi = {k: v for k, v in KIND_TEMPLATES.items() if len(v) > 1}
    assert set(multi) == {"results", "standings", "weather-p2", "weather-p3"}
    assert all(len(v) == 2 for v in multi.values())


@pytest.mark.rasteriser
async def test_render_raises_notices_without_failing(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path,
    scratch_slot,
):
    """A substituted font and a truncated field are notices, not problems (XIV.4)."""
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    (template_dir / "templates" / "verdicts_template.svg").write_bytes(RICH_TEMPLATE)

    from tests.support.image_sample_data import build_spec

    service = _render_service(config_service, module_service)
    # The DSQ case carries the justification fabricated an order of magnitude too long for
    # any box a league would draw, so RICH_TEMPLATE's 300x80 rectangle cuts it at the floor
    # and raises its notice — which is the degradation this test is about (043 FR-018).
    outcome = await service.render(
        SERVER_ID,
        "verdicts_template",
        lambda root: build_spec("verdicts_template", root, variant="penalty_dsq"),
        output_dir=tmp_path,
    )

    assert outcome.problem is None
    assert outcome.png_paths
    kinds = {n.notice_kind for n in outcome.notices}
    assert "FIELD_REDUCED" in kinds

    # Every notice is carried on the outcome, which is the whole of what XIV.4 requires:
    # the render survives, and `post_notices` reports them to the calculation log channel.
    # Nothing is written to the database — the audit table was withdrawn in migration 046.
    assert len(outcome.notices) > 0
    assert all(n.detail for n in outcome.notices)


async def test_render_problem_yields_no_image(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """png_paths is empty whenever problem is set — never a partial image."""
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_field(SERVER_ID, "calendar_template", "absent.svg")

    service = _render_service(config_service, module_service)
    outcome = await service.render(
        SERVER_ID, "calendar_template", lambda root: None, output_dir=tmp_path
    )

    assert outcome.problem is not None
    assert outcome.png_paths == []
    assert outcome.ok is False


@pytest.mark.rasteriser
async def test_render_is_off_the_event_loop(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """The rasteriser must be reached through asyncio.to_thread, not called inline.

    A blocking subprocess on the event loop stalls the scheduler, the retry worker and
    every in-flight interaction. It passes every unit test and degrades production, so
    it is asserted structurally.
    """
    import threading

    from tests.support.image_sample_data import build_spec

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

    service = _render_service(config_service, module_service)
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
    """A sound race results template carrying *background_markup* behind the field.

    Sound at Layer 2 as well as Layer 1 since 039 populated the results catalogues: the
    contrast measurement reads the template the league has **configured**, and a template
    that could not be configured is not one to measure.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1084">'
        f"{background_markup}"
        '<text id="division_name">D</text>'
        '<text id="round_number">1</text>'
        '<text id="race_name">R</text>'
        '<text id="session_name">S</text>'
        '<text id="result_status">F</text>'
        '<g id="row_1_group">'
        '<text id="row_1_position">1</text>'
        '<text id="row_1_driver_name">N</text>'
        '<text id="row_1_team_name">T</text>'
        '<image id="row_1_team_image"/>'
        '<text id="row_1_postrace_penalty">-</text>'
        '<text id="row_1_appeal_penalty">-</text>'
        '<text id="row_1_points">0</text>'
        '<text id="row_1_time">t</text>'
        '<text id="row_1_fastest_lap">f</text>'
        '<text id="row_1_ingame_penalty">-</text>'
        "</g></svg>"
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
    for column, (_cmd, default, _packaged) in ASSET_DIRECTORIES.items():
        (tmp_path / default).mkdir(parents=True, exist_ok=True)

    config = await config_service.get_config(SERVER_ID)
    reports = evaluate_directories(config, root=tmp_path)
    assert all(r.valid for r in reports.values()), "baseline: every class resolves"

    for column in ASSET_DIRECTORIES:
        await config_service.set_field(SERVER_ID, column, "resources/absent")
        config = await config_service.get_config(SERVER_ID)
        reports = evaluate_directories(config, root=tmp_path)

        assert not reports[column].valid
        # Counted from the table rather than written out, so adding a class does not put
        # this test wrong about a property that has nothing to do with how many there are.
        assert sum(1 for r in reports.values() if r.valid) == len(ASSET_DIRECTORIES) - 1, (
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


async def test_only_wired_aspects_read_their_toggle(
    module_service, config_service, template_dir
):
    """A toggle may be read only by the image module, or by an aspect actually wired up.

    035 delivered the configuration surface with every toggle inert, and this assertion
    guarded that. 037 wires the **calendar**: `calendar_post_service` reads the toggle to
    decide whether the calendar is conveyed as a graphic or in the traditional textual
    manner, which is the whole point of the aspect.

    The assertion therefore narrows rather than disappears — it still catches a source
    module branching on a toggle whose aspect nobody has built.
    """
    import pathlib

    await _enable(module_service, config_service)
    for aspect in ASPECTS:
        await config_service.set_aspect(SERVER_ID, aspect, True)
    assert all((await config_service.get_toggles(SERVER_ID)).values())

    src = pathlib.Path(__file__).resolve().parents[2] / "src"

    #: The posting paths of the aspects **nobody has wired yet**. Each must stay ignorant
    #: of the toggle state until its own increment builds it — otherwise output could
    #: branch on a toggle whose behaviour was never specified.
    #:
    #: `season_cog.py` and `calendar_post_service.py` are deliberately absent: 037 wired
    #: the calendar, and reading the toggle to choose between the graphic and the textual
    #: calendar is precisely what that wiring is.
    unwired_posting_paths = [
        "results_post_service.py",
        "rsvp_service.py",
        "attendance_service.py",
        "phase1_service.py",
        "phase2_service.py",
        "phase3_service.py",
        "placement_service.py",
        "penalty_service.py",
    ]

    offenders = []
    for name in unwired_posting_paths:
        for path in src.rglob(name):
            text = path.read_text(encoding="utf-8")
            for marker in ("image_aspect_toggles", "get_toggles", "is_aspect_enabled"):
                if marker in text:
                    offenders.append(f"{path.name}: {marker}")

    assert not offenders, (
        "a posting path for an unwired aspect reads image toggle state, so its output "
        "could branch on a toggle nothing has specified: " + ", ".join(offenders)
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


# ══════════════════════════════════════════════════════════════════════════
# 036 / T015 — validate-then-store (FR-005, SC-002)
#
# The observation that matters: after ANY rejection, reading the configuration back
# returns the value that stood before the command. Before 036 the write happened first.
# ══════════════════════════════════════════════════════════════════════════

from models.image_catalogues import CATALOGUES as _CATALOGUES  # noqa: E402
from models.image_catalogues import FieldCatalogue as _FieldCatalogue  # noqa: E402
from models.image_module import (  # noqa: E402
    PROBLEM_EXTENSION,
    PROBLEM_MISSING_MANDATORY_FIELD,
    PROBLEM_NOT_FOUND,
    PROBLEM_NOT_SVG,
)
from services.image_validity_service import check_template  # noqa: E402

_GOOD_SVG = VALID_SVG


@pytest.fixture()
def catalogue_slot():
    saved = dict(_CATALOGUES)
    yield lambda key, cat: _CATALOGUES.__setitem__(key, cat)
    _CATALOGUES.clear()
    _CATALOGUES.update(saved)


async def _store_if_valid(config_service, column, filename, root):
    """The exact sequence `_set_template_filename` runs: candidate → check → maybe write.

    Driven at the service layer rather than through Discord, so the assertion is about
    what reaches the database, not about how a reply is worded.
    """
    proposed = await config_service.candidate_config(SERVER_ID, column, filename)
    problem = check_template(proposed, column, root=root)
    if problem is None:
        await config_service.set_field(SERVER_ID, column, filename)
    return problem


@pytest.fixture()
async def configured(module_service, config_service, tmp_path):
    """Module enabled, template directory populated, one known-good filename stored."""
    await _enable(module_service, config_service)

    directory = tmp_path / "templates"
    directory.mkdir(exist_ok=True)
    for key, filename in TEMPLATE_COLUMNS.items():
        (directory / filename).write_bytes(sound_bytes(key))
    (directory / "known_good.svg").write_bytes(_GOOD_SVG)

    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_field(SERVER_ID, "calendar_template", "known_good.svg")
    return tmp_path


@pytest.mark.asyncio
async def test_wrong_extension_is_refused_and_nothing_written(config_service, configured):
    problem = await _store_if_valid(
        config_service, "calendar_template", "calendar.txt", configured
    )

    assert problem is not None and problem.kind == PROBLEM_EXTENSION
    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"


@pytest.mark.asyncio
async def test_absent_file_is_refused_and_nothing_written(config_service, configured):
    problem = await _store_if_valid(
        config_service, "calendar_template", "nope.svg", configured
    )

    assert problem is not None and problem.kind == PROBLEM_NOT_FOUND
    assert "nope.svg" in problem.detail  # names the full path searched (FR-006)
    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"


@pytest.mark.asyncio
async def test_malformed_file_is_refused_and_nothing_written(config_service, configured):
    (configured / "templates" / "broken.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"><!-- a -- b --></svg>'
    )
    problem = await _store_if_valid(
        config_service, "calendar_template", "broken.svg", configured
    )

    assert problem is not None and problem.kind == PROBLEM_NOT_SVG
    assert "double hyphen" in problem.detail
    assert "XMLSyntaxError" not in problem.detail
    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"


@pytest.mark.asyncio
async def test_missing_mandatory_field_is_refused_and_nothing_written(
    config_service, configured, catalogue_slot
):
    catalogue_slot(
        "calendar_template", _FieldCatalogue(mandatory=frozenset({"season_name"}))
    )
    (configured / "templates" / "fieldless.svg").write_bytes(_GOOD_SVG)

    problem = await _store_if_valid(
        config_service, "calendar_template", "fieldless.svg", configured
    )

    assert problem is not None and problem.kind == PROBLEM_MISSING_MANDATORY_FIELD
    assert "season_name" in problem.detail
    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"


@pytest.mark.asyncio
async def test_a_sound_template_is_accepted_and_written(config_service, configured):
    (configured / "templates" / "replacement.svg").write_bytes(_GOOD_SVG)

    problem = await _store_if_valid(
        config_service, "calendar_template", "replacement.svg", configured
    )

    assert problem is None
    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "replacement.svg"


@pytest.mark.asyncio
async def test_a_run_of_rejections_never_erodes_the_stored_value(
    config_service, configured
):
    """SC-002 stated as a sequence: four refusals in a row change nothing."""
    for filename in ("x.txt", "gone.svg", "also_gone.svg", "still.png"):
        await _store_if_valid(config_service, "calendar_template", filename, configured)

    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"


@pytest.mark.asyncio
async def test_rejection_of_one_template_leaves_the_others_alone(
    config_service, configured
):
    await _store_if_valid(config_service, "lineup_template", "missing.svg", configured)

    stored = await config_service.get_config(SERVER_ID)
    assert stored.calendar_template == "known_good.svg"
    assert stored.lineup_template == TEMPLATE_COLUMNS["lineup_template"]


# ══════════════════════════════════════════════════════════════════════════
# 036 / T022 — season review reports, season approval blocks (FR-007 … FR-009)
# ══════════════════════════════════════════════════════════════════════════

from services.image_validity_service import check_all_templates as _check_all  # noqa: E402
from services.image_validity_service import describe as _describe  # noqa: E402


async def _problem_lines(config_service, root, *, module_enabled=True):
    """What both `/season review` and `/season approve` compute (FR-008a).

    One evaluation, two surfaces. Driven at the service layer so the assertion is about
    the findings, not about how a Discord embed renders them.
    """
    if not module_enabled:
        return []
    config = await config_service.get_config(SERVER_ID)
    return [_describe(problem) for problem in _check_all(config, root=root)]


@pytest.mark.asyncio
async def test_sound_templates_contribute_no_finding(config_service, configured):
    assert await _problem_lines(config_service, configured) == []


@pytest.mark.asyncio
async def test_two_broken_templates_are_named_individually(config_service, configured):
    """FR-008 — both, separately, with distinct reasons. Not a count, not a group."""
    (configured / "templates" / "lineup_template.svg").unlink()
    (configured / "templates" / "rsvp_template.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"><!-- a -- b --></svg>'
    )

    lines = await _problem_lines(config_service, configured)

    from services.image_validity_service import PLAIN_FILE_MISSING, PLAIN_NOT_A_DRAWING

    assert len(lines) == 2
    joined = " | ".join(lines)
    assert "Lineup" in joined
    assert "Check-in call" in joined          # the rsvp template's label
    # The two faults stay distinguishable once said plainly: one file is absent, the
    # other is present but is not a drawing.
    assert PLAIN_FILE_MISSING in joined
    assert PLAIN_NOT_A_DRAWING in joined
    # Distinct reasons, not one blanket line repeated.
    assert lines[0] != lines[1]


@pytest.mark.asyncio
async def test_a_failing_template_names_itself_not_its_aspect(config_service, configured):
    """Weather has six templates behind one aspect; the report must name the one."""
    (configured / "templates" / "weather_p2_sprint_template.svg").unlink()

    lines = await _problem_lines(config_service, configured)

    assert len(lines) == 1
    assert "sprint" in lines[0].lower()
    assert "phase 2" in lines[0].lower()


@pytest.mark.asyncio
async def test_disabled_module_contributes_no_finding(config_service, configured):
    """FR-009 — with the module off, neither command verifies anything."""
    (configured / "templates" / "lineup_template.svg").unlink()

    assert await _problem_lines(config_service, configured, module_enabled=False) == []


@pytest.mark.asyncio
async def test_review_and_approval_read_the_same_evaluation(config_service, configured):
    """FR-008a — the two surfaces cannot disagree, because there is one computation."""
    (configured / "templates" / "lineup_template.svg").unlink()

    review = await _problem_lines(config_service, configured)
    approval = await _problem_lines(config_service, configured)

    assert review == approval and review != []


@pytest.mark.asyncio
async def test_missing_template_directory_reports_once_not_fifteen_times(
    config_service, configured
):
    """Existing 035 behaviour, retained: one shared reason, still one report each."""
    from services.image_validity_service import PLAIN_DIRECTORY_MISSING

    await config_service.set_field(SERVER_ID, "template_directory", "no_such_dir")

    lines = await _problem_lines(config_service, configured)

    assert len(lines) == len(TEMPLATE_COLUMNS)
    assert all(PLAIN_DIRECTORY_MISSING in line for line in lines)
    # The folder is the fault, not the fifteen files, and the line says so.
    assert not any("can't be found where the bot was told to look" in line for line in lines)


# ══════════════════════════════════════════════════════════════════════════
# 036 / T027 — the same fault, opposite behaviour (FR-029, FR-030, FR-032)
#
# If both origins fall back to text, FR-030 is not implemented. That is the whole test.
# ══════════════════════════════════════════════════════════════════════════

from models.image_module import PostingOrigin  # noqa: E402
from services.image_render_service import (  # noqa: E402
    POST_IMAGE,
    POST_TEXT_FALLBACK,
    REJECT_COMMAND,
    ImageRenderService,
)


@pytest.fixture()
def failing_render_service(db_path, config_service, module_service, monkeypatch):
    """A render service whose renders always meet the same fatal fault."""
    service = _render_service(config_service, module_service)

    async def always_fails(server_id, image_type, spec_builder, **kwargs):
        from models.image_module import PROBLEM_NOT_FOUND, Problem, RenderOutcome

        return RenderOutcome(
            problem=Problem(
                kind=PROBLEM_NOT_FOUND,
                detail="file not found: calendar_template.svg",
                template_key=image_type,
            )
        )

    monkeypatch.setattr(service, "render", always_fails)
    return service


@pytest.mark.asyncio
async def test_commanded_posting_rejects_and_posts_nothing(failing_render_service):
    decision = await failing_render_service.render_for_posting(
        SERVER_ID, "calendar_template", lambda root: None,
        posting_origin=PostingOrigin.COMMANDED,
    )

    assert decision.action == REJECT_COMMAND
    assert decision.rejects
    assert decision.png_paths == []
    assert "file not found" in decision.caller_message()


@pytest.mark.asyncio
async def test_scheduled_posting_falls_back_to_text(failing_render_service):
    decision = await failing_render_service.render_for_posting(
        SERVER_ID, "calendar_template", lambda root: None,
        posting_origin=PostingOrigin.SCHEDULED,
    )

    assert decision.action == POST_TEXT_FALLBACK
    assert decision.falls_back_to_text
    assert decision.png_paths == []


@pytest.mark.asyncio
async def test_the_two_origins_differ_on_the_identical_fault(failing_render_service):
    """SC-007 stated directly."""
    commanded = await failing_render_service.render_for_posting(
        SERVER_ID, "calendar_template", lambda root: None,
        posting_origin=PostingOrigin.COMMANDED,
    )
    scheduled = await failing_render_service.render_for_posting(
        SERVER_ID, "calendar_template", lambda root: None,
        posting_origin=PostingOrigin.SCHEDULED,
    )

    assert commanded.action != scheduled.action
    assert commanded.problem.detail == scheduled.problem.detail


@pytest.mark.asyncio
async def test_posting_origin_is_required_and_never_inferred(failing_render_service):
    """A new call site must state which it is; there is no default to fall into."""
    with pytest.raises(TypeError):
        await failing_render_service.render_for_posting(
            SERVER_ID, "calendar_template", lambda root: None,
        )

    with pytest.raises(TypeError):
        await failing_render_service.render_for_posting(
            SERVER_ID, "calendar_template", lambda root: None,
            posting_origin="COMMANDED",  # a string is not the enum
        )


@pytest.mark.asyncio
async def test_an_internal_problem_tells_the_user_nothing_to_act_on(
    db_path, config_service, module_service, monkeypatch
):
    """UNKNOWN_IMAGE_TYPE is a caller defect; echoing it would send a league hunting."""
    # The rasteriser check runs first, so on a host without Inkscape it would answer
    # RASTERISER and the unknown-type branch under test would never be reached. The
    # test is about the reply to a caller defect, not about rasterising.
    import services.image_render_service as render_service

    monkeypatch.setattr(render_service, "converter_available", lambda **_: True)

    service = _render_service(config_service, module_service)

    decision = await service.render_for_posting(
        SERVER_ID, "no_such_template", lambda root: None,
        posting_origin=PostingOrigin.COMMANDED,
    )

    assert decision.rejects
    assert decision.problem.is_internal
    message = decision.caller_message()
    assert "no_such_template" not in message
    assert "operator" in message


@pytest.mark.asyncio
@pytest.mark.rasteriser
async def test_a_clean_render_posts_the_image(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    from tests.support.image_sample_data import build_spec

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")

    service = _render_service(config_service, module_service)
    # `output_dir` so the render lands under pytest's own directory. Without it this test
    # is a caller that never posts and so never discards, and it litters the host's
    # temporary directory on every run.
    decision = await service.render_for_posting(
        SERVER_ID,
        "calendar_template",
        lambda root: build_spec("calendar_template", root),
        posting_origin=PostingOrigin.SCHEDULED,
        output_dir=tmp_path,
    )

    assert decision.action == POST_IMAGE
    assert decision.posts_image
    assert decision.png_paths


def test_notice_formatting_names_field_and_kind():
    from models.image_module import RenderNotice

    text = ImageRenderService.format_notices(
        [
            RenderNotice(
                image_type="calendar_template",
                notice_kind="ASSET_FALLBACK_USED",
                detail="no flag for `portugal`",
                field_id="row_1_flag",
            )
        ]
    )
    assert "ASSET_FALLBACK_USED" in text
    assert "row_1_flag" in text
    assert "portugal" in text


# ══════════════════════════════════════════════════════════════════════════
# 036 / T033 — no error text may reach a channel drivers read (FR-032, SC-006)
#
# Enumerated statically rather than by exercising every path: the requirement is about
# what the code *can* do, and a runtime test only covers the paths it happens to hit.
# ══════════════════════════════════════════════════════════════════════════

import ast as _ast  # noqa: E402
import pathlib as _pathlib  # noqa: E402

_SRC = _pathlib.Path(__file__).resolve().parents[2] / "src"

#: Sends that reach only the person who ran the command.
_INTERACTION_SENDS = {"send_message", "send"}


def _non_ephemeral_sends(path):
    """Every interaction send in *path* that does not pass ephemeral=True."""
    tree = _ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call) or not isinstance(node.func, _ast.Attribute):
            continue
        if node.func.attr not in _INTERACTION_SENDS:
            continue
        target = _ast.unparse(node.func.value)
        if "interaction" not in target and "followup" not in target:
            continue
        ephemeral = any(
            kw.arg == "ephemeral" and getattr(kw.value, "value", None) is True
            for kw in node.keywords
        )
        if not ephemeral:
            offenders.append(f"{path.name}:{node.lineno} {_ast.unparse(node.func)}")
    return offenders


def test_every_image_cog_reply_is_ephemeral():
    """The image module speaks to the caller, never to the room."""
    assert _non_ephemeral_sends(_SRC / "cogs" / "image_cog.py") == []


def test_the_render_service_writes_only_to_the_log_channel():
    """Its sole Discord sink is post_log; it holds no channel of its own (XIV.8)."""
    source = (_SRC / "services" / "image_render_service.py").read_text(encoding="utf-8")

    assert "post_log" in source
    for forbidden in ("post_forecast", "get_channel", "fetch_channel", "send_message"):
        assert forbidden not in source, f"render service reaches for {forbidden}"


def test_the_season_approval_refusal_is_ephemeral():
    """The template gate refuses in front of the admin, not the league."""
    source = (_SRC / "cogs" / "season_cog.py").read_text(encoding="utf-8")
    marker = "the image module is enabled"
    assert marker in source

    tail = source[source.index(marker):source.index(marker) + 600]
    assert "ephemeral=True" in tail


# ── 038: test mode changes nothing in the lineup path (T060, FR-035/36) ───


def test_no_lineup_module_branches_on_test_mode():
    """FR-035 — generation, posting and replacement behave identically in test mode.

    Asserted structurally rather than by driving a test-mode season: the requirement is
    that no branch on the flag *exists*, which is stronger than any single scenario.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    for relative in (
        "services/image_lineup_service.py",
        "services/image_lineup_post.py",
    ):
        text = (src / relative).read_text(encoding="utf-8")
        assert "test_mode" not in text, relative


def test_the_lineup_draws_a_test_driver_through_the_name_chain():
    """FR-036 — a test driver is drawn by its test display name, not skipped."""
    import sys as _sys

    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from services.image_lineup_service import resolve_driver_name

    # No Discord account and no signup record: the chain reaches the fourth link.
    assert (
        resolve_driver_name(discord_user_id="900001", test_display_name="Test Driver 1")
        == "Test Driver 1"
    )


# ══════════════════════════════════════════════════════════════════════════
# 043 — a verdict from an approved review, end to end (T038)
#
# Discord is stubbed: the send site records what it was handed. What this proves that the
# unit tests do not is that a real render, through the real config and validity services,
# produces a PNG for the real packaged template — and that the message beside it carries
# the mention alone.
# ══════════════════════════════════════════════════════════════════════════


class _RecordingChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.guild = type("_G", (), {"id": SERVER_ID, "get_role": lambda self, r: None})()

    async def send(self, content=None, *, file=None, **_kwargs):
        self.sent.append((content, file))


@pytest.mark.rasteriser
async def test_an_approved_penalty_posts_a_graphic_and_only_a_mention(
    db_path, module_service, config_service, template_dir, monkeypatch, tmp_path
):
    """One PNG per penalty, on a message carrying the driver mention and nothing besides."""
    from pathlib import Path

    from services import verdict_announcement_service as vas
    from services.image_verdict_service import VerdictKind

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_field(SERVER_ID, "template_directory", "templates")
    await config_service.set_aspect(SERVER_ID, "verdicts", True)

    # The packaged template, so the render is against the file a league actually gets.
    packaged = (
        Path(__file__).resolve().parents[2]
        / "resources"
        / "defaults"
        / "templates"
        / "verdicts_template.svg"
    )
    (template_dir / "templates" / "verdicts_template.svg").write_bytes(
        packaged.read_bytes()
    )

    # PROJECT_ROOT is patched away from the repository, so the packaged asset directories
    # are out of reach. A league carrying a fallback in each class is the ordinary case and
    # is what the flag and the badge resolve through here (XIV.13).
    for asset_class in ("flags", "teams"):
        directory = template_dir / "resources" / "defaults" / asset_class
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "fallback.svg").write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"/>'
        )

    bot = type(
        "_Bot",
        (),
        {
            "db_path": db_path,
            "module_service": module_service,
            "image_config_service": config_service,
            "image_validity_service": _validity_service(config_service, module_service),
            "image_render_service": _render_service(config_service, module_service),
            "output_router": None,
        },
    )()

    # The context queries have no season in this fixture; the graphic draws what it can and
    # the optional fields it cannot determine are emptied, which is the point.
    channel = _RecordingChannel()
    await vas._send_verdict(
        bot,
        channel,
        server_id=SERVER_ID,
        db_path=db_path,
        round_id=1,
        kind=VerdictKind.PENALTY,
        season_number=1,
        division_name="Pro Division",
        round_number=7,
        session_label="Feature Race",
        driver_discord_id=123456789012345678,
        driver_display_name="Ada Lovelace",
        driver_name="Ada Lovelace",
        penalty_description="5 seconds added",
        description_text="Contact at turn four.",
        justification_text="Video evidence reviewed by the panel.",
        team_name="Red Bull",
    )

    assert len(channel.sent) == 1
    content, attached = channel.sent[0]
    assert content == "<@123456789012345678>"
    assert attached is not None, "the verdict must post as a graphic"
    assert "Justification" not in content and "Ada Lovelace" not in content


async def test_the_verdict_toggle_off_posts_the_textual_announcement(
    db_path, module_service, config_service, template_dir, monkeypatch
):
    """The toggle decides how a posting is dressed, never whether it happens."""
    from services import verdict_announcement_service as vas
    from services.image_verdict_service import VerdictKind

    monkeypatch.setattr("utils.paths.PROJECT_ROOT", template_dir, raising=False)
    await _enable(module_service, config_service)
    await config_service.set_aspect(SERVER_ID, "verdicts", False)

    bot = type(
        "_Bot",
        (),
        {
            "db_path": db_path,
            "module_service": module_service,
            "image_config_service": config_service,
            "image_validity_service": _validity_service(config_service, module_service),
            "image_render_service": _render_service(config_service, module_service),
        },
    )()

    channel = _RecordingChannel()
    await vas._send_verdict(
        bot,
        channel,
        server_id=SERVER_ID,
        db_path=db_path,
        round_id=1,
        kind=VerdictKind.PENALTY,
        season_number=1,
        division_name="Pro Division",
        round_number=7,
        session_label="Feature Race",
        driver_discord_id=123,
        driver_display_name="Ada Lovelace",
        driver_name="Ada Lovelace",
        penalty_description="5 seconds added",
        description_text="Contact at turn four.",
        justification_text="Reviewed.",
    )

    content, attached = channel.sent[0]
    assert attached is None
    assert "**Penalty**: 5 seconds added" in content
