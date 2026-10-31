"""The per-tier colour commands (051): the toggle, one colour, the palette form and the import.

Every outcome is recorded in the log channel (#482): a refusal as one "⛔" line naming the
command, or the form where a form refused it; a success as one line naming the command that
was run, or that opened the form.

The commands run on the change queue (#439, slice 7): each is driven past its permission guard,
or its form submitted, on a league built by the migrations, with the real image services and the
real queue, which the test runs. What was typed and cannot be read (a malformed colour, slot,
division name, palette line or document, a file too large) is refused at once and queues
nothing; a change asked is acknowledged at once and answered once it has run, with the advice
read after the save. Which slots a template marks, and what the switched-on outputs still want,
are given to the validity service the league is handed, so no test here sweeps the templates.
"""
from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.image.cogs.image_cog import (
    MAX_PALETTE_IMPORT_BYTES,
    ImageCog,
    TierPaletteModal,
    TierPaletteXmlModal,
)
from leaguebot.image.models.image_module import ValidityReport
from leaguebot.image.services.image_config_service import ImageConfigService
from tests.support import saves
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import (
    QUEUED_MEMBER,
    assert_one_line,
    assert_one_refusal,
    interaction as _image_interaction,
    log_bot,
    logged,
    said,
)
from tests.support.settings_league import audit_rows, settings_league, switch
from tests.support.undecorate import undecorate

TOGGLE = "images config per-tier-colour-toggle"
SET_COLOUR = "images config per-tier-set-colour"
BULK = "images config per-tier-bulk-colour"
IMPORT = "images config colour-xml-import"

#: A division name with no letter or digit in it: nothing an artwork file could be named for.
UNSTORABLE = "***"
#: What a switched-on output still wants while a tier lacks a colour its template marks.
WANTED = {"calendar_template": ["`accent` is not set for **Division 1**"]}


async def _league(tmp_path, *, modules=("images",), declared=(), shortfall=None,
                  per_tier=None) -> Any:
    """A league with *modules* on, per-tier colours set to *per_tier* where given, its templates
    marking the slots *declared*, and *shortfall* what the switched-on outputs still want (none
    by default); its bot is `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=modules)
    if per_tier is not None:
        await saves.set_flag(league.db_path, "per_tier_colour_enabled", per_tier)
    validity = league.bot.image_validity_service
    validity.template_reports = AsyncMock(return_value={
        "calendar_template": ValidityReport(
            "calendar_template", None, True, 1, colour_slots=frozenset(declared)
        ),
    })
    validity.colour_shortfall = AsyncMock(return_value=shortfall or {})
    log_bot(league)
    return league


def _asked(league, command=None):
    """An interaction from "Race Control" for `/<command>` (None: a form's) on *league*'s bot."""
    return _image_interaction(command, bot=league.bot)


async def _toggle(league, enable: bool):
    """`/images config per-tier-colour-toggle` given *enable*, then the queue."""
    asked = _asked(league, TOGGLE)
    await undecorate(ImageCog.config_per_tier_colour_toggle)(ImageCog(league.bot), asked, enable)
    await run_queue(league.bot)
    return asked


async def _set_colour(league, division="Division 1", slot="accent", colour="#A78BFA"):
    """`/images config per-tier-set-colour`, then the queue."""
    asked = _asked(league, SET_COLOUR)
    await undecorate(ImageCog.config_per_tier_set_colour)(
        ImageCog(league.bot), asked, division, slot, colour
    )
    await run_queue(league.bot)
    return asked


async def _paste(league, division: str, text: str):
    """Submit the "Set one tier's colours" form for *division* with *text*, then the queue."""
    form = TierPaletteModal(ImageCog(league.bot), division)
    form.block._value = text
    submitted = _asked(league)
    await form.on_submit(submitted)
    await run_queue(league.bot)
    return submitted


def _attached(document: str | bytes, *, size: int | None = None) -> MagicMock:
    """A file attached to `/images config colour-xml-import`, holding *document*."""
    raw = document.encode() if isinstance(document, str) else document
    attachment = MagicMock()
    attachment.size = len(raw) if size is None else size
    attachment.read = AsyncMock(return_value=raw)
    return attachment


async def _import(league, file):
    """`/images config colour-xml-import` with *file* attached, then the queue."""
    asked = _asked(league, IMPORT)
    await undecorate(ImageCog.config_colour_xml_import)(ImageCog(league.bot), asked, file)
    await run_queue(league.bot)
    return asked


async def _paste_document(league, document: str):
    """Paste *document* into the "Import tier colours" form, then the queue."""
    form = TierPaletteXmlModal(ImageCog(league.bot))
    form.payload._value = document
    submitted = _asked(league)
    await form.on_submit(submitted)
    await run_queue(league.bot)
    return submitted


async def _colours(league) -> list[tuple[str, str, str]]:
    """Every tier colour stored, as (division slug, slot, colour), sorted."""
    async with get_connection(league.db_path) as db:
        rows = await (await db.execute(
            "SELECT division_slug, slot, colour FROM image_tier_colour"
        )).fetchall()
    return sorted(tuple(row) for row in rows)


async def _per_tier(league) -> Any:
    """The per-tier colours flag as stored, or None where the league has no configuration."""
    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT per_tier_colour_enabled FROM image_config"
        )).fetchone()
    return None if row is None else row[0]


def _acknowledged(asked) -> None:
    """Acknowledged at once as under way, before anything else was said."""
    assert asked.said and asked.said[0].startswith("⏳ "), asked.said


def _not_asked(asked) -> None:
    """Answered at once, nothing queued."""
    assert not any(text and text.startswith("⏳ ") for text in asked.said), asked.said


# ── The toggle ────────────────────────────────────────────────────────────


async def test_turning_it_on_reports_what_is_still_wanted(tmp_path):
    """The moment the shortfall becomes real is the moment to say so: read once the colour
    setting is saved."""
    league = await _league(tmp_path, shortfall=WANTED)

    asked = await _toggle(league, True)

    _acknowledged(asked)
    reply = asked.said[-1]
    assert "accent" in reply and "Division 1" in reply and "calendar_template" in reply, reply


async def test_turning_it_on_with_everything_set_says_so(tmp_path):
    league = await _league(tmp_path)

    asked = await _toggle(league, True)

    _acknowledged(asked)
    assert "every tier" in asked.said[-1].lower(), asked.said
    assert await _per_tier(league) == 1


async def test_turning_it_off_demands_nothing(tmp_path):
    league = await _league(tmp_path, per_tier=True, shortfall=WANTED)

    asked = await _toggle(league, False)

    _acknowledged(asked)
    reply = asked.said[-1]
    assert "off" in reply and "accent" not in reply, reply
    assert await _per_tier(league) == 0


async def test_nothing_is_stored_while_the_module_is_disabled(tmp_path):
    league = await _league(tmp_path, modules=())

    asked = await _toggle(league, True)

    assert "Image module is not enabled" in said(asked)
    _not_asked(asked)
    assert await _per_tier(league) is None
    assert await audit_rows(league.db_path) == []


# ── Setting a colour ──────────────────────────────────────────────────────


async def test_a_colour_is_stored_canonically(tmp_path):
    league = await _league(tmp_path, declared={"accent"})

    asked = await _set_colour(league, slot="Accent", colour="#a78bfa")

    _acknowledged(asked)
    assert await _colours(league) == [("division_1", "accent", "#A78BFA")]


@pytest.mark.parametrize("colour", ["red", "#GGGGGG", "#A78BF", "A78BFA", ""])
async def test_a_malformed_colour_stores_nothing(colour, tmp_path):
    league = await _league(tmp_path, declared={"accent"})

    asked = await _set_colour(league, colour=colour)

    _not_asked(asked)
    assert await _colours(league) == []
    assert "Nothing was stored" in said(asked)


@pytest.mark.parametrize("slot", ["a { } body {", "accent}", "", "a" * 65])
async def test_a_malformed_slot_stores_nothing(slot, tmp_path):
    """The command is the first of the two gates; the service is the second."""
    league = await _league(tmp_path)

    asked = await _set_colour(league, slot=slot)

    _not_asked(asked)
    assert await _colours(league) == []
    assert "Nothing was stored" in said(asked)


async def test_a_slot_no_template_marks_is_stored_and_called_out(tmp_path):
    """Stored, because a slot may be set before the template that uses it is drawn."""
    league = await _league(tmp_path, declared={"wash"})

    asked = await _set_colour(league)

    _acknowledged(asked)
    assert await _colours(league) == [("division_1", "accent", "#A78BFA")]
    assert "No template of yours marks" in asked.said[-1], asked.said


async def test_a_slot_a_template_marks_is_not_called_out(tmp_path):
    league = await _league(tmp_path, declared={"accent"})

    asked = await _set_colour(league)

    _acknowledged(asked)
    assert "No template of yours marks" not in said(asked)


async def test_storing_while_the_feature_is_off_says_it_will_not_draw(tmp_path):
    league = await _league(tmp_path, per_tier=False, declared={"accent"})

    asked = await _set_colour(league)

    _acknowledged(asked)
    reply = asked.said[-1]
    assert "off" in reply and "per-tier-colour-toggle" in reply, reply


async def test_storing_while_the_feature_is_on_says_nothing_of_the_sort(tmp_path):
    league = await _league(tmp_path, per_tier=True, declared={"accent"})

    asked = await _set_colour(league)

    _acknowledged(asked)
    assert "per-tier-colour-toggle" not in said(asked)


async def test_nothing_is_stored_for_a_colour_while_the_module_is_disabled(tmp_path):
    league = await _league(tmp_path, modules=())

    asked = await _set_colour(league)

    assert "Image module is not enabled" in said(asked)
    _not_asked(asked)
    assert await _colours(league) == []


# ── The command surface itself ────────────────────────────────────────────

def test_both_commands_exist_and_the_group_stays_inside_the_ceiling():
    source = inspect.getsource(ImageCog)
    assert 'name="per-tier-colour-toggle"' in source
    assert 'name="per-tier-set-colour"' in source
    # Discord allows twenty-five subcommands to a group; see the note in `image_cog`.
    assert source.count("@config.command") <= 25


def test_the_division_parameter_completes_like_every_other():
    source = inspect.getsource(ImageCog)
    assert (
        'config_per_tier_set_colour.autocomplete("division")(_division_autocomplete)'
        in source
    )


async def test_the_flag_is_written_through_the_boolean_setter(tmp_path):
    """`set_field_on` is string-valued, and a boolean through it would store `'True'`: it
    refuses the flag, and the toggle stores an integer."""
    from leaguebot.image.services.image_config_service import UnknownConfigField, set_field_on

    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        with pytest.raises(UnknownConfigField):
            await set_field_on(db, "per_tier_colour_enabled", "True")

    await _toggle(league, True)

    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT typeof(per_tier_colour_enabled), per_tier_colour_enabled FROM image_config"
        )).fetchone()
    assert tuple(row) == ("integer", 1)


def test_the_declared_slots_helper_reads_the_reports_rather_than_the_files():
    """Layer 1 has already parsed all sixteen; reading them again to ask this is waste."""
    from leaguebot.image.services.image_setting_advice import declared_colour_slots

    source = inspect.getsource(declared_colour_slots)
    assert "template_reports" in source
    assert "load_svg" not in source


async def test_the_declared_slots_helper_ignores_invalid_templates():
    from leaguebot.image.services.image_setting_advice import declared_colour_slots

    validity = MagicMock()
    validity.template_reports = AsyncMock(
        return_value={
            "calendar_template": ValidityReport(
                "calendar_template", None, True, 1, colour_slots=frozenset({"accent"})
            ),
            "lineup_template": ValidityReport(
                "lineup_template", None, False, 1, colour_slots=frozenset({"ghost"})
            ),
        }
    )
    assert await declared_colour_slots(validity) == {"accent"}


def test_the_service_exposes_what_the_commands_call():
    from leaguebot.image.services import image_config_service

    for name in ("set_flag_on", "set_tier_colours_on"):
        assert callable(getattr(image_config_service, name))
    for name in ("get_tier_palette", "get_all_tier_colours"):
        assert callable(getattr(ImageConfigService, name))


# ── Bulk paste and XML import ─────────────────────────────────────────────


async def test_a_clean_block_is_stored(tmp_path):
    league = await _league(tmp_path, declared={"accent", "ink"})

    submitted = await _paste(league, "Division 2", "accent #A78BFA\nink #F7F6F8")

    _acknowledged(submitted)
    assert await _colours(league) == [
        ("division_2", "accent", "#A78BFA"), ("division_2", "ink", "#F7F6F8"),
    ]


async def test_one_bad_line_stores_none_of_them(tmp_path):
    """The division is the unit of atomicity; a half-applied tier looks deliberate."""
    league = await _league(tmp_path)

    submitted = await _paste(league, "Division 2", "accent #A78BFA\nink purple")

    _not_asked(submitted)
    assert await _colours(league) == []
    assert "Nothing was stored" in said(submitted)


async def test_every_bad_line_is_named_not_just_the_first(tmp_path):
    league = await _league(tmp_path)

    submitted = await _paste(league, "D", "bad\nalso bad\nstill bad")

    _not_asked(submitted)
    assert said(submitted).count("•") == 3


async def test_a_bulk_slot_no_template_marks_is_called_out(tmp_path):
    league = await _league(tmp_path, declared={"accent"})

    submitted = await _paste(league, "D", "accent #A78BFA\nwash #101418")

    _acknowledged(submitted)
    reply = submitted.said[-1]
    assert "wash" in reply and "No template of yours marks" in reply, submitted.said


TWO_TIERS = (
    '<palettes>'
    '<division name="D1"><colour slot="accent">#3DD6F5</colour></division>'
    '<division name="D2"><colour slot="accent">#A78BFA</colour></division>'
    '</palettes>'
)


async def test_a_good_xml_import_stores_every_block(tmp_path):
    league = await _league(tmp_path)

    asked = await _import(league, _attached(TWO_TIERS))

    _acknowledged(asked)
    assert await _colours(league) == [("d1", "accent", "#3DD6F5"), ("d2", "accent", "#A78BFA")]
    assert "2 tier(s)" in asked.said[-1], asked.said


async def test_a_rejected_block_does_not_stop_the_others(tmp_path):
    """The decision: one mistyped tier does not cost a league the rest of the import."""
    league = await _league(tmp_path)

    asked = await _import(league, _attached(
        '<palettes>'
        '<division name="Good"><colour slot="accent">#A78BFA</colour></division>'
        '<division name="Bad"><colour slot="accent">purple</colour></division>'
        '</palettes>'
    ))

    _acknowledged(asked)
    assert await _colours(league) == [("good", "accent", "#A78BFA")]
    reply = asked.said[-1]
    assert "Good" in reply and "Bad" in reply and "not imported" in reply, reply


async def test_unreadable_xml_stores_nothing(tmp_path):
    league = await _league(tmp_path)

    asked = await _import(league, _attached("<palettes><division"))

    _not_asked(asked)
    assert await _colours(league) == []
    assert "Nothing was stored" in said(asked)


async def test_an_import_is_always_logged(tmp_path):
    """One success line naming `/images config colour-xml-import`, each tier beneath, saved
    once the change has run."""
    league = await _league(tmp_path)

    asked = await _import(league, _attached(
        '<palettes><division name="D"><colour slot="accent">#A78BFA</colour>'
        '</division></palettes>'
    ))

    _acknowledged(asked)
    details = assert_one_line(league.bot, IMPORT)
    assert any("D" in detail for detail in details), details


async def test_a_failed_import_is_logged_too(tmp_path):
    """An audit trail that records only successes hides the interesting half. A document that
    cannot be read stores nothing, so it is recorded at once as one "⛔" line, not as a
    failure."""
    league = await _league(tmp_path)

    asked = await _import(league, _attached("<broken"))

    _not_asked(asked)
    assert_one_refusal(league.bot, f"`/{IMPORT}`")


async def test_an_unreadable_import_pasted_into_the_form_is_refused_by_the_form(tmp_path):
    """Pasted into the form rather than attached, the refusal names the form, not a command."""
    league = await _league(tmp_path)

    submitted = await _paste_document(league, "<broken")

    _not_asked(submitted)
    assert await _colours(league) == []
    assert "Nothing was stored" in said(submitted)
    assert_one_refusal(league.bot, "the “Import tier colours” form")


# ── The commands and their modals ─────────────────────────────────────────

async def test_the_modals_can_be_constructed():
    """`async def` because apt's discord.py needs a running loop in `Modal.__init__`."""
    assert TierPaletteModal(MagicMock(), "Division 1") is not None
    assert TierPaletteXmlModal(MagicMock()) is not None


def test_both_new_commands_exist_and_the_group_still_fits():
    source = inspect.getsource(ImageCog)
    assert 'name="per-tier-bulk-colour"' in source
    assert 'name="colour-xml-import"' in source
    assert source.count("@config.command") <= 25


def test_the_attachment_size_is_capped():
    """A palette is a few hundred bytes per tier; anything larger is not one."""
    assert 0 < MAX_PALETTE_IMPORT_BYTES <= 1_000_000
    assert "MAX_PALETTE_IMPORT_BYTES" in inspect.getsource(
        ImageCog.config_colour_xml_import.callback
    )


# ── A division name the bot cannot store colours under (F3) ──────────────

#: What the member is told for `UNSTORABLE`, from the setter and the bulk form alike.
UNSTORABLE_REPLY = (
    f"❌ `{UNSTORABLE}` is not a division name the bot can store colours under. "
    f"Nothing was stored."
)


async def test_the_setter_refuses_a_division_name_with_no_letter_or_digit(tmp_path):
    """Refused at once, before anything is queued, as a mistake of the manager's, not a
    fault."""
    league = await _league(tmp_path, declared={"accent"})

    asked = await _set_colour(league, division=UNSTORABLE)

    assert await _colours(league) == []
    assert said(asked) == UNSTORABLE_REPLY
    assert_one_refusal(league.bot, f"`/{SET_COLOUR}`")


async def test_the_bulk_form_refuses_a_division_name_with_no_letter_or_digit(tmp_path):
    league = await _league(tmp_path, declared={"accent"})

    submitted = await _paste(league, UNSTORABLE, "accent #A78BFA")

    assert await _colours(league) == []
    assert said(submitted) == UNSTORABLE_REPLY
    assert_one_refusal(league.bot, "the “Set one tier's colours” form")


async def test_the_import_passes_over_a_division_name_with_no_letter_or_digit(tmp_path):
    """Passed over with the other rejected blocks; the tiers beside it are still stored."""
    league = await _league(tmp_path)

    asked = await _import(league, _attached(
        '<palettes>'
        '<division name="D1"><colour slot="accent">#3DD6F5</colour></division>'
        f'<division name="{UNSTORABLE}"><colour slot="accent">#A78BFA</colour></division>'
        '</palettes>'
    ))

    _acknowledged(asked)
    assert await _colours(league) == [("d1", "accent", "#3DD6F5")]
    reply = asked.said[-1]
    assert "not imported" in reply and UNSTORABLE in reply, reply
    details = assert_one_line(league.bot, IMPORT)
    assert any(UNSTORABLE in detail for detail in details), details


async def test_an_import_naming_one_tier_twice_under_two_spellings_stores_only_the_first(
    tmp_path,
):
    """"Division 1" and "Division-1" are one tier to the store, so the second block is passed
    over, naming the earlier one it collides with, and only the first block's colours stand."""
    league = await _league(tmp_path)

    asked = await _import(league, _attached(
        '<palettes>'
        '<division name="Division 1"><colour slot="accent">#3DD6F5</colour></division>'
        '<division name="Division-1"><colour slot="accent">#A78BFA</colour></division>'
        '</palettes>'
    ))

    _acknowledged(asked)
    assert await league.bot.image_config_service.get_tier_palette("Division 1") == {
        "accent": "#3DD6F5"
    }
    reply = asked.said[-1]
    assert "across 1 tier(s)" in reply, reply
    assert "**Division-1**" not in reply, reply
    assert "not imported" in reply, reply
    assert any(
        "Division-1" in line and "Division 1" in line
        for line in reply.splitlines()
        if "•" in line
    ), reply
    details = assert_one_line(league.bot, IMPORT)
    passed_over = details[details.index("passed over:") + 1:]
    assert any("Division-1" in d and "Division 1" in d for d in passed_over), details
    assert not any(d.startswith("Division-1:") for d in details), details


# ── A colour form submitted after the module was switched off (F5) ───────

_SWITCHED_OFF = (
    "❌ The Image module was switched off while this form was open. Nothing was stored."
)


async def test_the_bulk_form_stores_nothing_once_the_module_is_switched_off(tmp_path):
    league = await _league(tmp_path, declared={"accent"})
    form = TierPaletteModal(ImageCog(league.bot), "Division 1")
    form.block._value = "accent #A78BFA"
    await switch(league.bot, ("images",), on=False)

    submitted = _asked(league)
    await form.on_submit(submitted)
    await run_queue(league.bot)

    _not_asked(submitted)
    assert await _colours(league) == []
    assert said(submitted) == _SWITCHED_OFF
    assert_one_refusal(league.bot, "the “Set one tier's colours” form")


async def test_the_import_form_stores_nothing_once_the_module_is_switched_off(tmp_path):
    league = await _league(tmp_path)
    form = TierPaletteXmlModal(ImageCog(league.bot))
    form.payload._value = (
        '<palettes><division name="D1"><colour slot="accent">#3DD6F5</colour>'
        '</division></palettes>'
    )
    await switch(league.bot, ("images",), on=False)

    submitted = _asked(league)
    await form.on_submit(submitted)
    await run_queue(league.bot)

    _not_asked(submitted)
    assert await _colours(league) == []
    assert said(submitted) == _SWITCHED_OFF
    assert_one_refusal(league.bot, "the “Import tier colours” form")


# ── A reply too long for one message (F4) ────────────────────────────────


async def test_a_long_bulk_reply_arrives_in_parts_and_is_recorded(tmp_path):
    """A hundred slots make a reply past Discord's 2,000 characters: once the change has run it
    is sent whole, in parts each within the limit, and the success line is recorded — itself
    past the limit, so the log channel takes it in parts too, which read in order are the one
    line naming the tier and every slot."""
    slots = [f"slot{i:03d}" for i in range(100)]
    league = await _league(tmp_path, declared=set(slots))

    submitted = await _paste(league, "Division 2", "\n".join(f"{slot} #A78BFA" for slot in slots))

    _acknowledged(submitted)
    parts = submitted.said[1:]
    assert len(parts) > 1
    assert all(len(part) <= 2000 for part in parts)
    assert all(slot in "\n".join(parts) for slot in slots)
    line_parts = logged(league.bot)
    assert len(line_parts) > 1, line_parts
    assert all(len(part) <= 2000 for part in line_parts)
    head, *details = "\n".join(line_parts).splitlines()
    assert head == f"{QUEUED_MEMBER} | /{BULK} | Success", head
    details = [detail.strip() for detail in details]
    assert "Tier colours: Division 2" in details
    assert all(f"{slot} = #A78BFA" in details for slot in slots)


# ── An attached palette too large to be one (F6) ─────────────────────────

async def test_an_oversized_file_is_refused_without_being_read(tmp_path):
    league = await _league(tmp_path)
    file = _attached(b"x" * (MAX_PALETTE_IMPORT_BYTES + 1))

    asked = await _import(league, file)

    file.read.assert_not_awaited()
    _not_asked(asked)
    assert await _colours(league) == []
    assert said(asked) == f"❌ File is too large (max {MAX_PALETTE_IMPORT_BYTES // 1000} KB)."
    assert_one_refusal(league.bot, f"`/{IMPORT}`")
