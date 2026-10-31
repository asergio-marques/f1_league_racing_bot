"""Storing a tier's colours, and the toggle that turns the feature on.

The table is keyed on the division's **normalised name**, not its id, and that is the
decision this file exists to pin. Divisions are rows belonging to a season, so a new season
replaces them; keying on the id would silently empty every league's palette at the one
moment they are least likely to re-check their graphics. `Division 1` and `division_1` are
therefore one tier, found by the same rule that finds `division_1.svg`.

The writers are the image service's forms on a connection, `set_flag_on` and
`set_tier_colours_on`, which commit nothing (#439, slice 7); these tests reach them through the
tests' own committing twins, `tests/support/saves.py`, which also hold one slot as a one-entry
`set_tier_colours_on`.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.image.services.image_config_service import (
    FLAG_COLUMNS,
    PFP_FLAG_COLUMNS,
    SETTABLE_COLUMNS,
    ImageConfigService,
    UnknownConfigField,
)
from leaguebot.image.utils.svg_palette import InvalidSlot
from tests.support import saves

@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO image_config (id) VALUES (1)")
        await db.commit()
    return path


@pytest.fixture
def service(db_path):
    return ImageConfigService(db_path)


# ── The toggle ────────────────────────────────────────────────────────────

async def test_the_feature_starts_off(service):
    config = await service.get_config()
    assert config.per_tier_colour_enabled is False


async def test_the_toggle_flips_both_ways(service):
    await saves.set_flag(service, "per_tier_colour_enabled", True)
    assert (await service.get_config()).per_tier_colour_enabled is True
    await saves.set_flag(service, "per_tier_colour_enabled", False)
    assert (await service.get_config()).per_tier_colour_enabled is False


async def test_a_flag_outside_the_allow_list_is_refused(service):
    with pytest.raises(UnknownConfigField):
        await saves.set_flag(service, "module_enabled", True)


async def test_the_portrait_flags_are_written_by_the_same_flag_writer(service):
    """One flag writer holds all four flags: the three portrait flags and the per-tier one."""
    await saves.set_flag(service, "use_pfp", True)
    assert (await service.get_config()).use_pfp is True


def test_the_flag_is_not_reachable_through_the_string_setter():
    """A boolean written as a string would store `'True'`, which SQLite would read as 0."""
    assert "per_tier_colour_enabled" not in SETTABLE_COLUMNS
    assert "per_tier_colour_enabled" in FLAG_COLUMNS
    assert PFP_FLAG_COLUMNS < FLAG_COLUMNS


# ── Storing a colour ──────────────────────────────────────────────────────

async def test_a_colour_is_stored_and_read_back(service):
    await saves.set_tier_colour(service, "Division 1", "accent", "#A78BFA")
    assert await service.get_tier_palette("Division 1") == {"accent": "#A78BFA"}


async def test_setting_the_same_slot_again_replaces_it(service):
    await saves.set_tier_colour(service, "Division 1", "accent", "#A78BFA")
    await saves.set_tier_colour(service, "Division 1", "accent", "#4ADE80")
    assert await service.get_tier_palette("Division 1") == {"accent": "#4ADE80"}


async def _write_one_slot(service, tier, colour):
    await saves.set_tier_colour(service, tier, "accent", colour)


async def _write_in_bulk(service, tier, colour):
    await saves.set_tier_colours(service, tier, {"accent": colour})


@pytest.mark.parametrize("write", [_write_one_slot, _write_in_bulk], ids=["one slot", "bulk"])
async def test_two_tiers_are_independent(service, write):
    """Two tiers stay separate whichever writer stores them, one slot or in bulk."""
    await write(service, "Division 1", "#3DD6F5")
    await write(service, "Division 2", "#A78BFA")
    assert await service.get_tier_palette("Division 1") == {"accent": "#3DD6F5"}
    assert await service.get_tier_palette("Division 2") == {"accent": "#A78BFA"}


async def test_a_tier_may_hold_several_slots(service):
    await saves.set_tier_colour(service, "Division 1", "accent", "#3DD6F5")
    await saves.set_tier_colour(service, "Division 1", "wash", "#101418")
    assert await service.get_tier_palette("Division 1") == {
        "accent": "#3DD6F5",
        "wash": "#101418",
    }


async def test_a_tier_with_no_colours_yields_an_empty_palette(service):
    assert await service.get_tier_palette("Division 9") == {}


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
    await saves.set_tier_colour(service, written, "accent", "#A78BFA")
    assert await service.get_tier_palette(read) == {"accent": "#A78BFA"}


async def test_a_nameless_division_is_refused(service):
    """By the one-slot writer and the bulk writer alike."""
    with pytest.raises(UnknownConfigField):
        await saves.set_tier_colour(service, "   ", "accent", "#A78BFA")
    with pytest.raises(UnknownConfigField):
        await saves.set_tier_colours(service, "  ", {"accent": "#A78BFA"})


async def test_a_nameless_division_reads_as_empty_rather_than_raising(service):
    """The reader is on the render path and must never be the thing that breaks a post."""
    assert await service.get_tier_palette("") == {}


async def test_a_slot_that_could_escape_a_selector_is_refused_at_the_service(service):
    """The command validates too; the service does not take that on trust."""
    with pytest.raises(InvalidSlot):
        await saves.set_tier_colour(service, "Division 1", "a { } body {", "#A78BFA")
    assert await service.get_tier_palette("Division 1") == {}


async def test_a_slot_is_stored_lower_cased(service):
    await saves.set_tier_colour(service, "Division 1", "Accent", "#A78BFA")
    assert await service.get_tier_palette("Division 1") == {"accent": "#A78BFA"}


# ── Reading every tier at once ────────────────────────────────────────────

async def test_all_tier_colours_are_grouped_by_tier(service):
    await saves.set_tier_colour(service, "Division 2", "accent", "#A78BFA")
    await saves.set_tier_colour(service, "Division 1", "accent", "#3DD6F5")
    await saves.set_tier_colour(service, "Division 1", "wash", "#101418")

    assert await service.get_all_tier_colours() == {
        "division_1": {"accent": "#3DD6F5", "wash": "#101418"},
        "division_2": {"accent": "#A78BFA"},
    }


async def test_the_grouping_is_ordered_so_a_report_can_be_compared_by_eye(service):
    for name in ("Division 3", "Division 1", "Division 2"):
        await saves.set_tier_colour(service, name, "zeta", "#111111")
        await saves.set_tier_colour(service, name, "alpha", "#222222")

    palettes = await service.get_all_tier_colours()
    assert list(palettes) == ["division_1", "division_2", "division_3"]
    assert list(palettes["division_1"]) == ["alpha", "zeta"]


# ── Setting several at once (bulk / import) ───────────────────────────────

async def test_a_bulk_set_writes_every_slot(service):
    written = await saves.set_tier_colours(
        service, "Division 1", {"accent": "#A78BFA", "ink": "#F7F6F8"}
    )
    assert written == 2
    assert await service.get_tier_palette("Division 1") == {
        "accent": "#A78BFA", "ink": "#F7F6F8",
    }


async def test_a_bulk_set_merges_rather_than_replacing(service):
    """The decision of 2026-09-08: a slot the payload omits keeps the colour it had.

    A league pasting part of a palette is correcting part of a scheme, not declaring the
    whole of it, and losing the rest to an omission would be silent.
    """
    await saves.set_tier_colour(service, "Division 1", "ink", "#F4F7FA")
    await saves.set_tier_colours(service, "Division 1", {"accent": "#A78BFA"})
    assert await service.get_tier_palette("Division 1") == {
        "ink": "#F4F7FA", "accent": "#A78BFA",
    }


async def test_a_bulk_set_overwrites_a_slot_it_does_name(service):
    await saves.set_tier_colour(service, "Division 1", "accent", "#3DD6F5")
    await saves.set_tier_colours(service, "Division 1", {"accent": "#A78BFA"})
    assert await service.get_tier_palette("Division 1") == {"accent": "#A78BFA"}


async def test_a_bulk_set_of_nothing_writes_nothing(service):
    assert await saves.set_tier_colours(service, "Division 1", {}) == 0
    assert await service.get_tier_palette("Division 1") == {}


async def test_a_bulk_set_validates_every_slot(service):
    """One bad slot writes none of them — the division is the unit of atomicity."""
    with pytest.raises(InvalidSlot):
        await saves.set_tier_colours(
            service, "Division 1", {"accent": "#A78BFA", "a{}b": "#F7F6F8"}
        )
    assert await service.get_tier_palette("Division 1") == {}
