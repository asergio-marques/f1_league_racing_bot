"""Storing a tier's colours, and the toggle that turns the feature on.

The table is keyed on the division's **normalised name**, not its id, and that is the
decision this file exists to pin. Divisions are rows belonging to a season, so a new season
replaces them; keying on the id would silently empty every league's palette at the one
moment they are least likely to re-check their graphics. `Division 1` and `division_1` are
therefore one tier, found by the same rule that finds `division_1.svg`.
"""
from __future__ import annotations

import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import _MIGRATIONS_DIR  # noqa: E402
from services.image_config_service import (  # noqa: E402
    FLAG_COLUMNS,
    PFP_FLAG_COLUMNS,
    SETTABLE_COLUMNS,
    ImageConfigService,
    UnknownConfigField,
)
from utils.svg_palette import InvalidSlot  # noqa: E402

_MIGRATIONS = (
    "039_image_module.sql",
    "043_league_asset_directories.sql",
    "044_standings_highlight_directory.sql",
    "045_marks_join_the_markers.sql",
    "047_driver_portraits.sql",
    "048_division_logo_directory.sql",
    "051_per_tier_colours.sql",
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE server_configs (server_id INTEGER PRIMARY KEY)")
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        for filename in _MIGRATIONS:
            with open(os.path.join(_MIGRATIONS_DIR, filename), encoding="utf-8") as fh:
                await db.executescript(fh.read())
        await db.execute("INSERT INTO image_config (server_id) VALUES (1)")
        await db.commit()
    return path


@pytest.fixture
def service(db_path):
    return ImageConfigService(db_path)


# ── The toggle ────────────────────────────────────────────────────────────

async def test_the_feature_starts_off(service):
    config = await service.get_config(1)
    assert config.per_tier_colour_enabled is False


async def test_the_toggle_flips_both_ways(service):
    await service.set_flag(1, "per_tier_colour_enabled", True)
    assert (await service.get_config(1)).per_tier_colour_enabled is True
    await service.set_flag(1, "per_tier_colour_enabled", False)
    assert (await service.get_config(1)).per_tier_colour_enabled is False


async def test_a_flag_outside_the_allow_list_is_refused(service):
    with pytest.raises(UnknownConfigField):
        await service.set_flag(1, "module_enabled", True)


async def test_the_portrait_setter_still_refuses_the_new_flag(service):
    """`set_pfp_flag` keeps its own narrower guard rather than inheriting the wider one."""
    with pytest.raises(UnknownConfigField):
        await service.set_pfp_flag(1, "per_tier_colour_enabled", True)


async def test_the_portrait_flags_still_work_through_the_old_name(service):
    await service.set_pfp_flag(1, "use_pfp", True)
    assert (await service.get_config(1)).use_pfp is True


def test_the_flag_is_not_reachable_through_the_string_setter():
    """A boolean written as a string would store `'True'`, which SQLite would read as 0."""
    assert "per_tier_colour_enabled" not in SETTABLE_COLUMNS
    assert "per_tier_colour_enabled" in FLAG_COLUMNS
    assert PFP_FLAG_COLUMNS < FLAG_COLUMNS


# ── Storing a colour ──────────────────────────────────────────────────────

async def test_a_colour_is_stored_and_read_back(service):
    await service.set_tier_colour(1, "Division 1", "accent", "#A78BFA")
    assert await service.get_tier_palette(1, "Division 1") == {"accent": "#A78BFA"}


async def test_setting_the_same_slot_again_replaces_it(service):
    await service.set_tier_colour(1, "Division 1", "accent", "#A78BFA")
    await service.set_tier_colour(1, "Division 1", "accent", "#4ADE80")
    assert await service.get_tier_palette(1, "Division 1") == {"accent": "#4ADE80"}


async def test_two_tiers_are_independent(service):
    await service.set_tier_colour(1, "Division 1", "accent", "#3DD6F5")
    await service.set_tier_colour(1, "Division 2", "accent", "#A78BFA")
    assert await service.get_tier_palette(1, "Division 1") == {"accent": "#3DD6F5"}
    assert await service.get_tier_palette(1, "Division 2") == {"accent": "#A78BFA"}


async def test_a_tier_may_hold_several_slots(service):
    await service.set_tier_colour(1, "Division 1", "accent", "#3DD6F5")
    await service.set_tier_colour(1, "Division 1", "wash", "#101418")
    assert await service.get_tier_palette(1, "Division 1") == {
        "accent": "#3DD6F5",
        "wash": "#101418",
    }


async def test_a_tier_with_no_colours_yields_an_empty_palette(service):
    assert await service.get_tier_palette(1, "Division 9") == {}


@pytest.mark.parametrize(
    "written,read",
    [
        ("Division 1", "division_1"),
        ("division_1", "Division 1"),
        ("DIVISION 1", "  Division   1  "),
    ],
)
async def test_a_tier_is_found_however_its_name_is_spelled(service, written, read):
    """One tier, one palette, whatever casing or spacing the two commands were given."""
    await service.set_tier_colour(1, written, "accent", "#A78BFA")
    assert await service.get_tier_palette(1, read) == {"accent": "#A78BFA"}


async def test_a_nameless_division_is_refused(service):
    with pytest.raises(UnknownConfigField):
        await service.set_tier_colour(1, "   ", "accent", "#A78BFA")


async def test_a_nameless_division_reads_as_empty_rather_than_raising(service):
    """The reader is on the render path and must never be the thing that breaks a post."""
    assert await service.get_tier_palette(1, "") == {}


async def test_a_slot_that_could_escape_a_selector_is_refused_at_the_service(service):
    """The command validates too; the service does not take that on trust."""
    with pytest.raises(InvalidSlot):
        await service.set_tier_colour(1, "Division 1", "a { } body {", "#A78BFA")
    assert await service.get_tier_palette(1, "Division 1") == {}


async def test_a_slot_is_stored_lower_cased(service):
    await service.set_tier_colour(1, "Division 1", "Accent", "#A78BFA")
    assert await service.get_tier_palette(1, "Division 1") == {"accent": "#A78BFA"}


# ── Reading every tier at once ────────────────────────────────────────────

async def test_all_tier_colours_are_grouped_by_tier(service):
    await service.set_tier_colour(1, "Division 2", "accent", "#A78BFA")
    await service.set_tier_colour(1, "Division 1", "accent", "#3DD6F5")
    await service.set_tier_colour(1, "Division 1", "wash", "#101418")

    assert await service.get_all_tier_colours(1) == {
        "division_1": {"accent": "#3DD6F5", "wash": "#101418"},
        "division_2": {"accent": "#A78BFA"},
    }


async def test_the_grouping_is_ordered_so_a_report_can_be_compared_by_eye(service):
    for name in ("Division 3", "Division 1", "Division 2"):
        await service.set_tier_colour(1, name, "zeta", "#111111")
        await service.set_tier_colour(1, name, "alpha", "#222222")

    palettes = await service.get_all_tier_colours(1)
    assert list(palettes) == ["division_1", "division_2", "division_3"]
    assert list(palettes["division_1"]) == ["alpha", "zeta"]


async def test_another_server_sees_none_of_it(service, db_path):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (2)")
        await db.commit()
    await service.set_tier_colour(1, "Division 1", "accent", "#A78BFA")
    assert await service.get_all_tier_colours(2) == {}
