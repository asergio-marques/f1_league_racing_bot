"""The two per-tier colour commands (051).

The commands themselves are wrapped by `@league_manager_only` and cannot be
invoked without a gateway, so — as in `test_image_cog_asset_directories` — the shared body
is called unbound against a `MagicMock(spec=ImageCog)`. What is asserted is what a manager
would see and what was stored, which is all the command decides.

Every outcome is recorded in the log channel (#482): a refusal as one "⛔" line naming the
command, or the form where a form refused it; a success as one line naming the command that
was run, or that opened the form.
"""
from __future__ import annotations

import functools
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.cogs.image_cog import MAX_PALETTE_IMPORT_BYTES, ImageCog
from leaguebot.image.models.image_module import ValidityReport
from leaguebot.image.services.image_config_service import ImageConfigService
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    logged,
    said,
)
from tests.support.undecorate import undecorate

TOGGLE = "images config per-tier-colour-toggle"
SET_COLOUR = "images config per-tier-set-colour"
BULK = "images config per-tier-bulk-colour"
IMPORT = "images config colour-xml-import"

#: A division name with no letter or digit in it: nothing an artwork file could be named for.
UNSTORABLE = "***"

_FORM_NOT_RECHECKED = "#482: a colour form does not yet check the module on submit"


def _interaction(cog, command=None):
    """An interaction for *command* through *cog*'s bot, kept on the cog for `_said`.

    Both bodies defer before they read a template (#165), so the double tracks it.
    """
    cog.asked = _image_interaction(command, bot=cog.bot)
    return cog.asked


def _cog(*, enabled=True, declared=frozenset(), shortfall=None):
    """The cog, unbound, with the module on and a store that reports each value as a change.

    Replies go through the real `_reply`, so a refusal and a success are read off the
    interaction alike.
    """
    cog = MagicMock(spec=ImageCog)
    cog.bot = log_bot()
    cog._module_gate = AsyncMock(return_value=True)
    cog._reply = ImageCog._reply
    cog._declared_colour_slots = AsyncMock(return_value=set(declared))

    cog._config_service = MagicMock()
    cog._config_service.set_flag = AsyncMock(return_value=True)
    cog._config_service.set_tier_colour = AsyncMock(return_value=True)
    cog._config_service.get_config = AsyncMock(
        return_value=MagicMock(per_tier_colour_enabled=enabled)
    )

    cog._validity_service = MagicMock()
    cog._validity_service.colour_shortfall = AsyncMock(return_value=shortfall or {})
    return cog


def _said(cog) -> str:
    return said(cog.asked)


# ── The toggle ────────────────────────────────────────────────────────────

async def test_the_toggle_stores_what_it_was_given():
    cog = _cog()
    await ImageCog._set_per_tier_colours(cog, _interaction(cog, TOGGLE), True)
    cog._config_service.set_flag.assert_awaited_once_with("per_tier_colour_enabled", True)


async def test_turning_it_on_reports_what_is_still_wanted():
    """The moment the shortfall becomes real is the moment to say so."""
    cog = _cog(shortfall={"calendar_template": ["`accent` is not set for **Division 1**"]})
    await ImageCog._set_per_tier_colours(cog, _interaction(cog, TOGGLE), True)
    said = _said(cog)
    assert "accent" in said and "Division 1" in said and "calendar_template" in said


async def test_turning_it_on_with_everything_set_says_so():
    cog = _cog()
    await ImageCog._set_per_tier_colours(cog, _interaction(cog, TOGGLE), True)
    assert "every tier" in _said(cog).lower()


async def test_turning_it_off_demands_nothing():
    cog = _cog(shortfall={"calendar_template": ["`accent` is not set for **Division 1**"]})
    await ImageCog._set_per_tier_colours(cog, _interaction(cog, TOGGLE), False)
    said = _said(cog)
    assert "off" in said
    assert "accent" not in said


async def test_nothing_is_stored_while_the_module_is_disabled():
    cog = _cog()
    cog._module_gate = AsyncMock(return_value=False)
    await ImageCog._set_per_tier_colours(cog, _interaction(cog, TOGGLE), True)
    cog._config_service.set_flag.assert_not_awaited()


# ── Setting a colour ──────────────────────────────────────────────────────

async def test_a_colour_is_stored_canonically():
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "Accent", "#a78bfa")
    cog._config_service.set_tier_colour.assert_awaited_once_with(
        "Division 1", "accent", "#A78BFA"
    )


@pytest.mark.parametrize("colour", ["red", "#GGGGGG", "#A78BF", "A78BFA", ""])
async def test_a_malformed_colour_stores_nothing(colour):
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", colour)
    cog._config_service.set_tier_colour.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


@pytest.mark.parametrize("slot", ["a { } body {", "accent}", "", "a" * 65])
async def test_a_malformed_slot_stores_nothing(slot):
    """The command is the first of the two gates; the service is the second."""
    cog = _cog()
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", slot, "#A78BFA")
    cog._config_service.set_tier_colour.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_a_slot_no_template_marks_is_stored_and_called_out():
    """Stored, because a slot may be set before the template that uses it is drawn."""
    cog = _cog(declared={"wash"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", "#A78BFA")
    cog._config_service.set_tier_colour.assert_awaited_once()
    assert "No template of yours marks" in _said(cog)


async def test_a_slot_a_template_marks_is_not_called_out():
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", "#A78BFA")
    assert "No template of yours marks" not in _said(cog)


async def test_storing_while_the_feature_is_off_says_it_will_not_draw():
    cog = _cog(enabled=False, declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", "#A78BFA")
    said = _said(cog)
    assert "off" in said and "per-tier-colour-toggle" in said


async def test_storing_while_the_feature_is_on_says_nothing_of_the_sort():
    cog = _cog(enabled=True, declared={"accent"})
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", "#A78BFA")
    assert "per-tier-colour-toggle" not in _said(cog)


async def test_nothing_is_stored_for_a_colour_while_the_module_is_disabled():
    cog = _cog()
    cog._module_gate = AsyncMock(return_value=False)
    await ImageCog._set_tier_colour(cog, _interaction(cog, SET_COLOUR), "Division 1", "accent", "#A78BFA")
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
    assert await ImageCog._declared_colour_slots(cog) == {"accent"}


def test_the_service_exposes_what_the_commands_call():
    for name in ("set_flag", "set_tier_colour", "get_tier_palette", "get_all_tier_colours"):
        assert callable(getattr(ImageConfigService, name))


# ── Bulk paste and XML import ─────────────────────────────────────────────

def _bulk_cog(*, declared=frozenset(), written=2):
    cog = MagicMock(spec=ImageCog)
    cog.bot = log_bot()
    cog._reply = ImageCog._reply
    cog._declared_colour_slots = AsyncMock(return_value=set(declared))
    cog._config_service = MagicMock()
    cog._config_service.set_tier_colours = AsyncMock(return_value=written)
    return cog


async def test_a_clean_block_is_stored():
    cog = _bulk_cog(declared={"accent", "ink"})
    await ImageCog.apply_tier_block(
        cog, _interaction(cog), "Division 2", "accent #A78BFA\nink #F7F6F8"
    )
    cog._config_service.set_tier_colours.assert_awaited_once_with(
        "Division 2", {"accent": "#A78BFA", "ink": "#F7F6F8"}
    )


async def test_one_bad_line_stores_none_of_them():
    """The division is the unit of atomicity; a half-applied tier looks deliberate."""
    cog = _bulk_cog()
    await ImageCog.apply_tier_block(
        cog, _interaction(cog), "Division 2", "accent #A78BFA\nink purple"
    )
    cog._config_service.set_tier_colours.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_every_bad_line_is_named_not_just_the_first():
    cog = _bulk_cog()
    await ImageCog.apply_tier_block(cog, _interaction(cog), "D", "bad\nalso bad\nstill bad")
    assert _said(cog).count("•") == 3


async def test_a_bulk_slot_no_template_marks_is_called_out():
    cog = _bulk_cog(declared={"accent"})
    await ImageCog.apply_tier_block(
        cog, _interaction(cog), "D", "accent #A78BFA\nwash #101418"
    )
    assert "wash" in _said(cog) and "No template of yours marks" in _said(cog)


async def test_a_good_xml_import_stores_every_block():
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(cog),
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
        cog, _interaction(cog),
        '<palettes>'
        '<division name="Good"><colour slot="accent">#A78BFA</colour></division>'
        '<division name="Bad"><colour slot="accent">purple</colour></division>'
        '</palettes>',
    )
    cog._config_service.set_tier_colours.assert_awaited_once_with("Good", {"accent": "#A78BFA"})
    said = _said(cog)
    assert "Good" in said and "Bad" in said and "not imported" in said


async def test_unreadable_xml_stores_nothing():
    cog = _bulk_cog()
    await ImageCog.apply_tier_xml(cog, _interaction(cog), "<palettes><division")
    cog._config_service.set_tier_colours.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)


async def test_an_import_is_always_logged():
    """One success line naming `/images config colour-xml-import`, each tier beneath."""
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(cog),
        '<palettes><division name="D"><colour slot="accent">#A78BFA</colour>'
        '</division></palettes>',
    )
    details = assert_one_line(cog.bot, IMPORT)
    assert any("D" in detail for detail in details), details


async def test_a_failed_import_is_logged_too():
    """An audit trail that records only successes hides the interesting half. A document that
    cannot be read stores nothing, so it is recorded as one "⛔" line, not as a failure."""
    cog = _bulk_cog()
    await ImageCog.apply_tier_xml(cog, _interaction(cog), "<broken")
    lines = logged(cog.bot)
    assert len(lines) == 1, lines
    assert lines[0].startswith("⛔ ") and "refused for Race Control (<@42>)" in lines[0]


async def test_an_unreadable_import_pasted_into_the_form_is_refused_by_the_form():
    """Pasted into the form rather than attached, the refusal names the form, not a command."""
    from leaguebot.image.cogs.image_cog import TierPaletteXmlModal

    cog = _form_cog()
    modal = _submitted(TierPaletteXmlModal(cog), "payload", "<broken")

    await modal.on_submit(_interaction(cog))

    cog._config_service.set_tier_colours.assert_not_awaited()
    assert "Nothing was stored" in _said(cog)
    assert_one_refusal(cog.bot, "the “Import tier colours” form")


# ── The commands and their modals ─────────────────────────────────────────

async def test_the_modals_can_be_constructed():
    """`async def` because apt's discord.py needs a running loop in `Modal.__init__`."""
    from leaguebot.image.cogs.image_cog import TierPaletteModal, TierPaletteXmlModal

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
    from leaguebot.image.cogs.image_cog import MAX_PALETTE_IMPORT_BYTES

    assert 0 < MAX_PALETTE_IMPORT_BYTES <= 1_000_000
    assert "MAX_PALETTE_IMPORT_BYTES" in __import__("inspect").getsource(
        ImageCog.config_colour_xml_import.callback
    )


# ── A division name the bot cannot store colours under (F3) ──────────────

#: What the member is told for `UNSTORABLE`, from the setter and the bulk form alike.
UNSTORABLE_REPLY = (
    f"❌ `{UNSTORABLE}` is not a division name the bot can store colours under. "
    f"Nothing was stored."
)


async def test_the_setter_refuses_a_division_name_with_no_letter_or_digit():
    """Refused before anything is written, as a mistake of the manager's, not a fault."""
    cog = _cog(declared={"accent"})
    await ImageCog._set_tier_colour(
        cog, _interaction(cog, SET_COLOUR), UNSTORABLE, "accent", "#A78BFA"
    )
    cog._config_service.set_tier_colour.assert_not_awaited()
    assert _said(cog) == UNSTORABLE_REPLY
    assert_one_refusal(cog.bot, f"`/{SET_COLOUR}`")


def _submitted(modal, field: str, text: str):
    """*modal* with *text* typed into *field*, as Discord hands it back on submit."""
    getattr(modal, field)._value = text
    return modal


def _form_cog(**kwargs):
    """A bulk cog whose forms reach its real bodies, as the cog's own would, with the Image
    module still on when a form is submitted."""
    cog = _bulk_cog(**kwargs)
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    cog.apply_tier_block = functools.partial(ImageCog.apply_tier_block, cog)
    cog.apply_tier_xml = functools.partial(ImageCog.apply_tier_xml, cog)
    return cog


async def test_the_bulk_form_refuses_a_division_name_with_no_letter_or_digit():
    from leaguebot.image.cogs.image_cog import TierPaletteModal

    cog = _form_cog(declared={"accent"})
    modal = _submitted(TierPaletteModal(cog, UNSTORABLE), "block", "accent #A78BFA")
    interaction = _interaction(cog)

    await modal.on_submit(interaction)

    cog._config_service.set_tier_colours.assert_not_awaited()
    assert _said(cog) == UNSTORABLE_REPLY
    assert_one_refusal(cog.bot, "the “Set one tier's colours” form")


async def test_the_import_passes_over_a_division_name_with_no_letter_or_digit():
    """Passed over with the other rejected blocks; the tiers beside it are still stored."""
    cog = _bulk_cog(written=1)
    await ImageCog.apply_tier_xml(
        cog, _interaction(cog),
        '<palettes>'
        '<division name="D1"><colour slot="accent">#3DD6F5</colour></division>'
        f'<division name="{UNSTORABLE}"><colour slot="accent">#A78BFA</colour></division>'
        '</palettes>',
    )
    cog._config_service.set_tier_colours.assert_awaited_once_with("D1", {"accent": "#3DD6F5"})
    said_text = _said(cog)
    assert "not imported" in said_text and UNSTORABLE in said_text
    details = assert_one_line(cog.bot, IMPORT)
    assert any(UNSTORABLE in detail for detail in details), details


# ── A colour form submitted after the module was switched off (F5) ───────

_SWITCHED_OFF = (
    "❌ The Image module was switched off while this form was open. Nothing was stored."
)


@pytest.mark.xfail(strict=True, reason=_FORM_NOT_RECHECKED)
async def test_the_bulk_form_stores_nothing_once_the_module_is_switched_off():
    from leaguebot.image.cogs.image_cog import TierPaletteModal

    cog = _form_cog(declared={"accent"})
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    modal = _submitted(TierPaletteModal(cog, "Division 1"), "block", "accent #A78BFA")

    await modal.on_submit(_interaction(cog))

    cog._config_service.set_tier_colours.assert_not_awaited()
    assert _said(cog) == _SWITCHED_OFF
    assert_one_refusal(cog.bot, "the “Set one tier's colours” form")


@pytest.mark.xfail(strict=True, reason=_FORM_NOT_RECHECKED)
async def test_the_import_form_stores_nothing_once_the_module_is_switched_off():
    from leaguebot.image.cogs.image_cog import TierPaletteXmlModal

    cog = _form_cog()
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    modal = _submitted(
        TierPaletteXmlModal(cog),
        "payload",
        '<palettes><division name="D1"><colour slot="accent">#3DD6F5</colour>'
        '</division></palettes>',
    )

    await modal.on_submit(_interaction(cog))

    cog._config_service.set_tier_colours.assert_not_awaited()
    assert _said(cog) == _SWITCHED_OFF
    assert_one_refusal(cog.bot, "the “Import tier colours” form")


# ── A reply too long for one message (F4) ────────────────────────────────

async def test_a_long_bulk_reply_arrives_in_parts_and_is_recorded():
    """A hundred slots make a reply past Discord's 2,000 characters: it is sent whole, in
    parts each within the limit, and the success line is still recorded."""
    slots = [f"slot{i:03d}" for i in range(100)]
    cog = _bulk_cog(declared=set(slots), written=len(slots))
    await ImageCog.apply_tier_block(
        cog, _interaction(cog), "Division 2", "\n".join(f"{slot} #A78BFA" for slot in slots)
    )
    parts = cog.asked.said
    assert len(parts) > 1
    assert all(len(part) <= 2000 for part in parts)
    assert all(slot in _said(cog) for slot in slots)
    assert_one_line(cog.bot, BULK)


# ── An attached palette too large to be one (F6) ─────────────────────────

@pytest.mark.xfail(strict=True, reason="#482: an attached palette is read before its size is checked")
async def test_an_oversized_file_is_refused_without_being_read():
    cog = _bulk_cog()
    cog._module_gate = AsyncMock(return_value=True)
    file = MagicMock()
    file.size = MAX_PALETTE_IMPORT_BYTES + 1
    file.read = AsyncMock(return_value=b"x" * (MAX_PALETTE_IMPORT_BYTES + 1))
    interaction = _interaction(cog, IMPORT)

    await undecorate(ImageCog.config_colour_xml_import)(cog, interaction, file)

    file.read.assert_not_awaited()
    cog._config_service.set_tier_colours.assert_not_awaited()
    assert _said(cog) == f"❌ File is too large (max {MAX_PALETTE_IMPORT_BYTES // 1000} KB)."
    assert_one_refusal(cog.bot, f"`/{IMPORT}`")
