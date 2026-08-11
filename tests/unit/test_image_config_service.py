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
