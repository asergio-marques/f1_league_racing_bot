"""Unit tests for ImageConfigService — T009.

Covers the defaults every enable must produce, the single-transaction creation of the
config row plus eight toggle rows, and the allow-list guarding the generic setter.
"""
from __future__ import annotations

import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import ASPECTS, ASSET_DIRECTORIES, TEMPLATE_COLUMNS  # noqa: E402
from services.image_config_service import (  # noqa: E402
    SETTABLE_COLUMNS,
    ImageConfigService,
    UnknownConfigField,
)

_MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations", "039_image_module.sql"
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    with open(_MIGRATION, encoding="utf-8") as fh:
        migration_sql = fh.read()

    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE server_configs (server_id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.executescript(migration_sql)
        await db.commit()
    return path


@pytest.fixture
def service(db_path):
    return ImageConfigService(db_path)


# ── Defaults ──────────────────────────────────────────────────────────────


async def test_create_with_defaults_sets_every_packaged_default(service):
    cfg = await service.create_with_defaults(1)

    assert cfg.module_enabled is False
    assert cfg.template_directory == "resources/templates"
    for column, default_filename in TEMPLATE_COLUMNS.items():
        assert getattr(cfg, column) == default_filename
    for column, (_cmd, default_dir) in ASSET_DIRECTORIES.items():
        assert getattr(cfg, column) == default_dir
    assert cfg.time_zone == "UTC"
    assert cfg.time_format == "24H"
    assert cfg.date_format == "DDD_DD_MON_YYYY"
    assert cfg.fastest_lap_colour == "#A020F0"


async def test_create_with_defaults_inserts_exactly_eight_disabled_toggles(service):
    await service.create_with_defaults(1)
    toggles = await service.get_toggles(1)

    assert set(toggles) == set(ASPECTS)
    assert len(toggles) == 8
    assert not any(toggles.values())


async def test_create_with_defaults_is_idempotent(service):
    await service.create_with_defaults(1)
    await service.set_field(1, "template_directory", "resources/custom")
    await service.set_aspect(1, "standings", True)

    await service.create_with_defaults(1)

    cfg = await service.get_config(1)
    assert cfg.template_directory == "resources/custom"
    assert (await service.get_toggles(1))["standings"] is True


async def test_get_config_returns_none_before_creation(service):
    assert await service.get_config(1) is None


# ── The allow-list ────────────────────────────────────────────────────────


async def test_set_field_rejects_column_outside_allow_list(service):
    await service.create_with_defaults(1)
    for forbidden in ("module_enabled", "server_id", "nonexistent_column"):
        with pytest.raises(UnknownConfigField):
            await service.set_field(1, forbidden, "x")


async def test_allow_list_covers_all_settable_columns(service):
    # 1 template dir + 15 filenames + 7 asset dirs + 4 preferences = 27 scalar columns.
    # With the 8 toggles that is 35 configuration values in total (SC-008).
    assert len(SETTABLE_COLUMNS) == 27
    await service.create_with_defaults(1)
    for column in SETTABLE_COLUMNS:
        await service.set_field(1, column, "probe")
    cfg = await service.get_config(1)
    for column in SETTABLE_COLUMNS:
        assert getattr(cfg, column) == "probe"


async def test_set_aspect_rejects_unknown_aspect(service):
    await service.create_with_defaults(1)
    with pytest.raises(UnknownConfigField):
        await service.set_aspect(1, "not_an_aspect", True)


async def test_toggle_aspect_flips_and_returns_new_state(service):
    await service.create_with_defaults(1)

    assert await service.toggle_aspect(1, "weather") is True
    assert (await service.get_toggles(1))["weather"] is True

    assert await service.toggle_aspect(1, "weather") is False
    assert (await service.get_toggles(1))["weather"] is False


async def test_toggling_one_aspect_leaves_the_others_alone(service):
    await service.create_with_defaults(1)
    await service.toggle_aspect(1, "standings")

    toggles = await service.get_toggles(1)
    assert toggles["standings"] is True
    assert all(not v for k, v in toggles.items() if k != "standings")


# ── 036 / T016: the ordered check sequence, and validate-then-store ───────

import dataclasses as _dc  # noqa: E402

from models.image_catalogues import CATALOGUES as _CATALOGUES  # noqa: E402
from models.image_catalogues import FieldCatalogue as _FieldCatalogue  # noqa: E402
from models.image_module import (  # noqa: E402
    PROBLEM_EXTENSION,
    PROBLEM_MISSING_MANDATORY_FIELD,
    PROBLEM_NOT_FOUND,
    PROBLEM_NOT_SVG,
    ImageConfig as _ImageConfig,
)
from services.image_validity_service import (  # noqa: E402
    check_all_templates,
    check_filename,
    check_template,
)

_VALID_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"/>'

#: A bare canvas passes Layer 1 but no longer passes Layer 2 for the calendar, whose
#: catalogue was populated in 037. A "sound" calendar template must now carry the
#: whole-graphic mandatory field and one complete round, its crop point standing at the
#: declared height. The lineup (038) needs its own sound bytes below. The remaining
#: thirteen types still have empty catalogues and skip Layer 2.
_VALID_CALENDAR_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_1_number">1</text>'
    b'<text id="round_1_country_name">C</text>'
    b'<text id="round_1_race_name">R</text>'
    b'<text id="round_1_date">1 Jan</text>'
    b'<rect id="round_1_vertical_crop_point" x="0" y="675" width="1" height="1"/>'
    b"</svg>"
)


#: A sound lineup template carries the whole-graphic mandatory field and the reserve
#: block. Its *team* fields are keyed by a league's own teams, so a template checked with
#: no division in view is not asked for them (research R4).
_VALID_LINEUP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<g id="reserve_group"><text id="reserve_driver_1_name">N</text></g>'
    b"</svg>"
)


#: A sound results template (039) carries the five whole-graphic mandatory fields and one
#: complete row. The two kinds share every field but the columns of their rows, and a
#: template must carry its own kind's alone — a sibling's field is a fault (XIV.3, v4.4.0).
def _results_svg(*row_columns: bytes) -> bytes:
    columns = b"".join(
        b'<text id="row_1_%s">x</text>' % name for name in row_columns
    )
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


_VALID_RESULTS_QUALIFYING_SVG = _results_svg(b"best_lap", b"gap")
_VALID_RESULTS_RACE_SVG = _results_svg(b"time", b"fastest_lap", b"ingame_penalty")


#: A sound standings template (040) carries the three whole-graphic mandatory fields and one
#: complete row. It declares **no round**, which is sound: the results grid is an optional
#: unit (XIV.3, v4.5.0) and a template declaring none of it draws a classification alone.
#: The two championships are siblings whose row catalogues differ, so each gets its own.
def _standings_svg(*row_extra: bytes) -> bytes:
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


_VALID_STANDINGS_DRIVERS_SVG = _standings_svg(b"driver_name")
_VALID_STANDINGS_CONSTRUCTORS_SVG = _standings_svg()


#: A sound attendance sheet (041): the two whole-graphic mandatories and one complete row. It
#: declares no round at all, the grid being an optional unit (XIV.3), and no position, the row
#: ordinal of a sheet being a place in the layout and not a datum (XIV.11, v4.6.0).
_VALID_ATTENDANCE_SVG = (
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
_VALID_RSVP_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<text id="race_name">R</text>'
    b'<text id="round_format">Normal</text>'
    b'<text id="round_date">1 Jan 2026</text>'
    b'<text id="round_time">20:00 UTC</text>'
    b"</svg>"
)


def _sound_bytes(filename: str) -> bytes:
    """The soundest template for *filename* at the depth its type is checked to."""
    if filename == TEMPLATE_COLUMNS["calendar_template"]:
        return _VALID_CALENDAR_SVG
    if filename == TEMPLATE_COLUMNS["lineup_template"]:
        return _VALID_LINEUP_SVG
    if filename == TEMPLATE_COLUMNS["results_qualifying_template"]:
        return _VALID_RESULTS_QUALIFYING_SVG
    if filename == TEMPLATE_COLUMNS["results_race_template"]:
        return _VALID_RESULTS_RACE_SVG
    if filename == TEMPLATE_COLUMNS["standings_drivers_template"]:
        return _VALID_STANDINGS_DRIVERS_SVG
    if filename == TEMPLATE_COLUMNS["standings_constructors_template"]:
        return _VALID_STANDINGS_CONSTRUCTORS_SVG
    if filename == TEMPLATE_COLUMNS["attendance_template"]:
        return _VALID_ATTENDANCE_SVG
    if filename == TEMPLATE_COLUMNS["rsvp_template"]:
        return _VALID_RSVP_SVG
    return _VALID_SVG


def _make_config(template_directory="templates", **overrides) -> _ImageConfig:
    values = dict(
        server_id=1,
        module_enabled=True,
        template_directory=template_directory,
        track_image_directory="resources/tracks",
        team_image_directory="resources/teams",
        flag_directory="resources/flags",
        driver_image_directory="resources/drivers",
        marker_directory="resources/markers",
        weather_icon_directory="resources/weather",
        tyre_directory="resources/tyres",
        time_zone="UTC",
        time_format="24H",
        date_format="DDD_DD_MON_YYYY",
        fastest_lap_colour="#A020F0",
        **dict(TEMPLATE_COLUMNS),
    )
    values.update(overrides)
    return _ImageConfig(**values)


@pytest.fixture()
def template_dir(tmp_path):
    directory = tmp_path / "templates"
    directory.mkdir()
    for filename in TEMPLATE_COLUMNS.values():
        (directory / filename).write_bytes(_sound_bytes(filename))
    return tmp_path


@pytest.fixture()
def catalogue_slot():
    saved = dict(_CATALOGUES)
    yield lambda key, cat: _CATALOGUES.__setitem__(key, cat)
    _CATALOGUES.clear()
    _CATALOGUES.update(saved)


# FR-001 — the extension check, case-insensitive, before any filesystem access.


@pytest.mark.parametrize("name", ["calendar.svg", "calendar.SVG", "calendar.Svg"])
def test_extension_accepted_case_insensitively(name):
    assert check_filename(name) is None


@pytest.mark.parametrize("name", ["calendar.txt", "calendar", "calendar.svg.bak", ""])
def test_extension_rejected(name):
    problem = check_filename(name)
    assert problem is not None
    assert problem.kind == PROBLEM_EXTENSION


def test_extension_is_checked_before_the_filesystem(tmp_path):
    """A bad name is refused without the directory even having to exist."""
    config = _make_config(template_directory="nowhere", calendar_template="x.txt")
    problem = check_template(config, "calendar_template", root=tmp_path)
    assert problem.kind == PROBLEM_EXTENSION


# FR-002 … FR-004 — the rest of the sequence, each class distinguishable.


def test_sound_template_yields_no_problem(template_dir):
    assert check_template(_make_config(), "calendar_template", root=template_dir) is None


def test_absent_file_names_the_path_searched(template_dir):
    (template_dir / "templates" / "calendar_template.svg").unlink()
    problem = check_template(_make_config(), "calendar_template", root=template_dir)

    assert problem.kind == PROBLEM_NOT_FOUND
    assert "calendar_template.svg" in problem.detail
    assert problem.template_key == "calendar_template"


def test_malformed_file_is_a_parse_problem_not_a_missing_one(template_dir):
    (template_dir / "templates" / "calendar_template.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg"><!-- a -- b --></svg>'
    )
    problem = check_template(_make_config(), "calendar_template", root=template_dir)

    assert problem.kind == PROBLEM_NOT_SVG
    assert "double hyphen" in problem.detail
    assert "XMLSyntaxError" not in problem.detail  # FR-046


def test_a_directory_named_as_a_template_is_not_a_file(template_dir):
    target = template_dir / "templates" / "calendar_template.svg"
    target.unlink()
    target.mkdir()
    assert check_template(_make_config(), "calendar_template", root=template_dir) is not None


def test_missing_mandatory_field_is_its_own_class(template_dir, catalogue_slot):
    catalogue_slot("calendar_template", _FieldCatalogue(mandatory=frozenset({"season_name"})))
    problem = check_template(_make_config(), "calendar_template", root=template_dir)

    assert problem.kind == PROBLEM_MISSING_MANDATORY_FIELD
    assert "season_name" in problem.detail


def test_all_four_failure_classes_are_mutually_distinguishable(template_dir, catalogue_slot):
    """SC-003 — four defects, four different kinds."""
    kinds = {check_filename("calendar.txt").kind}

    (template_dir / "templates" / "lineup_template.svg").unlink()
    kinds.add(check_template(_make_config(), "lineup_template", root=template_dir).kind)

    (template_dir / "templates" / "rsvp_template.svg").write_bytes(b"not markup")
    kinds.add(check_template(_make_config(), "rsvp_template", root=template_dir).kind)

    catalogue_slot("verdicts_template", _FieldCatalogue(mandatory=frozenset({"nope"})))
    kinds.add(check_template(_make_config(), "verdicts_template", root=template_dir).kind)

    assert len(kinds) == 4


# FR-007 / FR-008 — every template, named individually.


def test_check_all_templates_is_silent_when_all_are_sound(template_dir):
    assert check_all_templates(_make_config(), root=template_dir) == []


def test_check_all_templates_names_each_failure_separately(template_dir):
    (template_dir / "templates" / "calendar_template.svg").unlink()
    (template_dir / "templates" / "lineup_template.svg").write_bytes(b"not markup")

    problems = check_all_templates(_make_config(), root=template_dir)

    assert {p.template_key for p in problems} == {"calendar_template", "lineup_template"}
    assert len({p.kind for p in problems}) == 2  # distinct reasons, not one blanket


def test_candidate_override_does_not_mutate_the_stored_config():
    """The heart of FR-005: the copy is what gets checked."""
    config = _make_config()
    proposed = _dc.replace(config, calendar_template="other.svg")

    assert proposed.calendar_template == "other.svg"
    assert config.calendar_template == "calendar_template.svg"
