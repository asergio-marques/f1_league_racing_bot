"""The two per-tier colour commands (051).

The commands themselves are wrapped by `@channel_guard` and `@admin_only` and cannot be
invoked without a gateway, so — as in `test_image_cog_asset_directories` — the shared body
is called unbound against a `MagicMock(spec=ImageCog)`. What is asserted is what a manager
would see and what was stored, which is all the command decides.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog  # noqa: E402
from models.image_module import ValidityReport  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = 1
    return interaction


def _cog(*, enabled=True, declared=frozenset(), shortfall=None):
    cog = MagicMock(spec=ImageCog)
    cog._guard_module_enabled = AsyncMock(return_value=True)
    cog._reply = AsyncMock()
    cog._log = AsyncMock()
    cog._declared_colour_slots = AsyncMock(return_value=set(declared))

    cog._config_service = MagicMock()
    cog._config_service.set_flag = AsyncMock()
    cog._config_service.set_tier_colour = AsyncMock()
    cog._config_service.get_config = AsyncMock(
        return_value=MagicMock(per_tier_colour_enabled=enabled)
    )

    cog._validity_service = MagicMock()
    cog._validity_service.colour_shortfall = AsyncMock(return_value=shortfall or {})
    return cog


def _said(cog) -> str:
    return cog._reply.await_args.args[1]


# ── The toggle ────────────────────────────────────────────────────────────

async def test_the_toggle_stores_what_it_was_given():
    cog = _cog()
    await ImageCog._set_per_tier_colours(cog, _interaction(), True)
    cog._config_service.set_flag.assert_awaited_once_with(1, "per_tier_colour_enabled", True)


async def test_turning_it_on_reports_what_is_still_wanted():
    """The moment the shortfall becomes real is the moment to say so."""
    cog = _cog(shortfall={"calendar_template": ["`accent` is not set for **Division 1**"]})
    await ImageCog._set_per_tier_colours(cog, _interaction(), True)
    said = _said(cog)
    assert "accent" in said and "Division 1" in said and "calendar_template" in said


async def test_turning_it_on_with_everything_set_says_so():
    cog = _cog()
    await ImageCog._set_per_tier_colours(cog, _interaction(), True)
    assert "every tier" in _said(cog).lower()


async def test_turning_it_off_demands_nothing():
    cog = _cog(shortfall={"calendar_template": ["`accent` is not set for **Division 1**"]})
    await ImageCog._set_per_tier_colours(cog, _interaction(), False)
    said = _said(cog)
    assert "off" in said
    assert "accent" not in said


async def test_nothing_is_stored_while_the_module_is_disabled():
    cog = _cog()
    cog._guard_module_enabled = AsyncMock(return_value=False)
    await ImageCog._set_per_tier_colours(cog, _interaction(), True)
    cog._config_service.set_flag.assert_not_awaited()


# ── Setting a colour ──────────────────────────────────────────────────────

async def test_a_colour_is_stored_canonically():
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "Accent", "#a78bfa")
    cog._config_service.set_tier_colour.assert_awaited_once_with(
        1, "Division 1", "accent", "#A78BFA"
    )


@pytest.mark.parametrize("colour", ["red", "#GGGGGG", "#A78BF", "A78BFA", ""])
async def test_a_malformed_colour_stores_nothing(colour):
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", colour)
    cog._config_service.set_tier_colour.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


@pytest.mark.parametrize("slot", ["a { } body {", "accent}", "", "a" * 65])
async def test_a_malformed_slot_stores_nothing(slot):
    """The command is the first of the two gates; the service is the second."""
    cog = _cog()
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", slot, "#A78BFA")
    cog._config_service.set_tier_colour.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_a_slot_no_template_marks_is_stored_and_called_out():
    """Stored, because a slot may be set before the template that uses it is drawn."""
    cog = _cog(declared={"wash"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", "#A78BFA")
    cog._config_service.set_tier_colour.assert_awaited_once()
    assert "No template of yours marks" in _said(cog)


async def test_a_slot_a_template_marks_is_not_called_out():
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", "#A78BFA")
    assert "No template of yours marks" not in _said(cog)


async def test_storing_while_the_feature_is_off_says_it_will_not_draw():
    cog = _cog(enabled=False, declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", "#A78BFA")
    said = _said(cog)
    assert "off" in said and "per-tier-colour-toggle" in said


async def test_storing_while_the_feature_is_on_says_nothing_of_the_sort():
    cog = _cog(enabled=True, declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", "#A78BFA")
    assert "per-tier-colour-toggle" not in _said(cog)


async def test_nothing_is_stored_for_a_colour_while_the_module_is_disabled():
    cog = _cog()
    cog._guard_module_enabled = AsyncMock(return_value=False)
    await ImageCog._set_tier_colour(cog, _interaction(), "Division 1", "accent", "#A78BFA")
    cog._config_service.set_tier_colour.assert_not_awaited()


# ── The command surface itself ────────────────────────────────────────────

def test_both_commands_exist_and_the_group_stays_inside_the_ceiling():
    import inspect

    source = inspect.getsource(ImageCog)
    assert 'name="per-tier-colour-toggle"' in source
    assert 'name="per-tier-set-colour"' in source
    # Discord allows twenty-five subcommands to a group; see the note in `image_cog`.
    assert source.count("@config.command") <= 25


def test_the_division_parameter_completes_like_every_other():
    import inspect

    source = inspect.getsource(ImageCog)
    assert (
        'config_per_tier_set_colour.autocomplete("division")(_division_autocomplete)'
        in source
    )


def test_the_flag_is_written_through_the_boolean_setter():
    """`set_field` is string-valued; a boolean through it would store `'True'`."""
    import inspect

    source = inspect.getsource(ImageCog._set_per_tier_colours)
    assert "set_flag(" in source
    assert "set_field(" not in source


def test_the_declared_slots_helper_reads_the_reports_rather_than_the_files():
    """Layer 1 has already parsed all fifteen; reading them again to ask this is waste."""
    import inspect

    source = inspect.getsource(ImageCog._declared_colour_slots)
    assert "template_reports" in source
    assert "load_svg" not in source


async def test_the_declared_slots_helper_ignores_invalid_templates():
    cog = MagicMock(spec=ImageCog)
    cog._validity_service = MagicMock()
    cog._validity_service.template_reports = AsyncMock(
        return_value={
            "calendar_template": ValidityReport(
                "calendar_template", None, True, 1, colour_slots=frozenset({"accent"})
            ),
            "lineup_template": ValidityReport(
                "lineup_template", None, False, 1, colour_slots=frozenset({"ghost"})
            ),
        }
    )
    assert await ImageCog._declared_colour_slots(cog, 1) == {"accent"}


def test_the_service_exposes_what_the_commands_call():
    for name in ("set_flag", "set_tier_colour", "get_tier_palette", "get_all_tier_colours"):
        assert callable(getattr(ImageConfigService, name))


# ── Bulk paste and XML import ─────────────────────────────────────────────

def _bulk_cog(*, declared=frozenset(), written=2):
    cog = MagicMock(spec=ImageCog)
    cog._reply = AsyncMock()
    cog._log = AsyncMock()
    cog._declared_colour_slots = AsyncMock(return_value=set(declared))
    cog._config_service = MagicMock()
    cog._config_service.set_tier_colours = AsyncMock(return_value=written)
    return cog


async def test_a_clean_block_is_stored():
    cog = _bulk_cog(declared={"accent", "ink"})
    await ImageCog.apply_tier_block(
        cog, _interaction(), "Division 2", "accent #A78BFA\nink #F7F6F8"
    )
    cog._config_service.set_tier_colours.assert_awaited_once_with(
        1, "Division 2", {"accent": "#A78BFA", "ink": "#F7F6F8"}
    )


async def test_one_bad_line_stores_none_of_them():
    """The division is the unit of atomicity; a half-applied tier looks deliberate."""
    cog = _bulk_cog()
    await ImageCog.apply_tier_block(
        cog, _interaction(), "Division 2", "accent #A78BFA\nink purple"
    )
    cog._config_service.set_tier_colours.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_every_bad_line_is_named_not_just_the_first():
    cog = _bulk_cog()
    await ImageCog.apply_tier_block(cog, _interaction(), "D", "bad\nalso bad\nstill bad")
    assert _said(cog).count("•") == 3


async def test_a_bulk_slot_no_template_marks_is_called_out():
    cog = _bulk_cog(declared={"accent"})
    await ImageCog.apply_tier_block(
        cog, _interaction(), "D", "accent #A78BFA\nwash #101418"
    )
    assert "wash" in _said(cog) and "No template of yours marks" in _said(cog)


async def test_a_good_xml_import_stores_every_block():
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(),
        '<palettes>'
        '<division name="D1"><colour slot="accent">#3DD6F5</colour></division>'
        '<division name="D2"><colour slot="accent">#A78BFA</colour></division>'
        '</palettes>',
    )
    assert cog._config_service.set_tier_colours.await_count == 2
    assert "2 tier(s)" in _said(cog)


async def test_a_rejected_block_does_not_stop_the_others():
    """The decision: one mistyped tier does not cost a league the rest of the import."""
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(),
        '<palettes>'
        '<division name="Good"><colour slot="accent">#A78BFA</colour></division>'
        '<division name="Bad"><colour slot="accent">purple</colour></division>'
        '</palettes>',
    )
    cog._config_service.set_tier_colours.assert_awaited_once()
    said = _said(cog)
    assert "Good" in said and "Bad" in said and "not imported" in said


async def test_unreadable_xml_stores_nothing():
    cog = _bulk_cog()
    await ImageCog.apply_tier_xml(cog, _interaction(), "<palettes><division")
    cog._config_service.set_tier_colours.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_an_import_is_always_logged():
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(),
        '<palettes><division name="D"><colour slot="accent">#A78BFA</colour>'
        '</division></palettes>',
    )
    cog._log.assert_awaited_once()


async def test_a_failed_import_is_logged_too():
    """An audit trail that records only successes hides the interesting half."""
    cog = _bulk_cog()
    await ImageCog.apply_tier_xml(cog, _interaction(), "<broken")
    cog._log.assert_awaited_once()


# ── The commands and their modals ─────────────────────────────────────────

async def test_the_modals_can_be_constructed():
    """`async def` because apt's discord.py needs a running loop in `Modal.__init__`."""
    from cogs.image_cog import TierPaletteModal, TierPaletteXmlModal

    assert TierPaletteModal(MagicMock(), "Division 1") is not None
    assert TierPaletteXmlModal(MagicMock()) is not None


def test_both_new_commands_exist_and_the_group_still_fits():
    import inspect

    source = inspect.getsource(ImageCog)
    assert 'name="per-tier-bulk-colour"' in source
    assert 'name="colour-xml-import"' in source
    assert source.count("@config.command") <= 25


def test_the_attachment_size_is_capped():
    """A palette is a few hundred bytes per tier; anything larger is not one."""
    from cogs.image_cog import MAX_PALETTE_IMPORT_BYTES

    assert 0 < MAX_PALETTE_IMPORT_BYTES <= 1_000_000
    assert "MAX_PALETTE_IMPORT_BYTES" in __import__("inspect").getsource(
        ImageCog.config_colour_xml_import.callback
    )
