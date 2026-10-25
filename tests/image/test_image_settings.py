"""The image settings on the change queue (#439, slice 7).

Every `/images` command that changes the module's configuration asks one settings change:
acknowledged at once naming what is being set, checked at the ask and again as it runs, and saving
the value, its audit entry and its log line in one save (core specification, "The record of what
changed" and "How a change is carried out"; image specification :20-41, :76-103, :119-137,
:156-187). The work a setting does not need after its save (checking it against the templates,
arming or removing the daily portrait job) runs as a job of its own, which may fail without
holding up the queue; where the change arms the daily job, the Success line waits for the arming,
so that a failed arming records no success (image :132).

Each test drives the real command, form or button past its tier guard, on a database built by the
migrations, with the real image services, the real queue and "now" pinned, and runs the queue.
The league holds the image module's default configuration, every output switched off, and the
templates the bot ships; the rasteriser is taken as present, since no test here draws.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.core.utils.paths import resolve_within_project_root
from leaguebot.image.models.image_constants import (
    ASSET_DIRECTORIES,
    ASSET_LABELS,
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from tests.support.change_queue import (
    MEMBER_ID,
    change_rows,
    maybe_await,
    restart_queue,
    run_queue,
    stopped_job,
)
from tests.support.settings_league import (
    audit_rows,
    fault_in,
    interaction,
    last_said,
    log_lines,
    said,
    settings_league,
)
from tests.support.undecorate import undecorate

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: What a daily job that cannot be armed says became of the change, as today (image :132).
DAILY_JOB_NOT_SCHEDULED = (
    "The setting is stored, and the daily job will be scheduled when the bot next starts."
)
#: What a daily job that cannot be removed adds to the success (plan 2.11, Assumed 12).
NOT_REMOVED = (
    "⚠️ The setting is saved, but the daily job could not be removed; it does nothing while the "
    "setting is off."
)
#: What a check against the templates that faults after the save adds to the success (plan 2.11).
NOT_CHECKED = (
    "⚠️ The setting is saved, but it could not be checked against your templates; "
    "`/images config view` shows how it stands."
)
IMPORT_NOT_SAVED = "Nothing from the import was saved. Run the import again to retry."
#: What a pasted palette whose save faults says became of it, as the results pastes do (plan 2.9).
PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
#: The colour form a pasted palette arrives through, as its replies and lines name it.
PALETTE_FORM = "the “Set one tier's colours” form"
CALENDAR = app_commands.Choice(name="Calendar", value="calendar")
TWELVE_HOUR = app_commands.Choice(name="12-hour (2:30 PM)", value="12H")
TWO_TIERS = (
    '<palettes><division name="Division 1"><colour slot="accent">#3DD6F5</colour></division>'
    '<division name="Division 2"><colour slot="accent">#F5A623</colour></division></palettes>'
)
#: A folder inside the project root that holds no template.
NO_TEMPLATES = "resources/defaults/tracks"


@pytest.fixture(autouse=True)
def _rasteriser_present(monkeypatch):
    """The rasteriser taken as present wherever the check asks for it: no test here draws, and
    switching an output on is refused on a host without it."""
    import leaguebot.image.services.image_render_service as render

    monkeypatch.setattr(render, "converter_available", lambda: True)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        return
    monkeypatch.setattr(image_settings, "converter_available", lambda: True, raising=False)


# ---------------------------------------------------------------------------
# The league
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, **config: Any) -> Any:
    """The league of the module docstring, images alone on, *config* written over the image
    configuration's defaults, and `aspects` (where given) switched on."""
    league = await settings_league(tmp_path, modules=("images",))
    aspects = config.pop("aspects", ())
    async with get_connection(league.db_path) as db:
        for column, value in config.items():
            await db.execute(f"UPDATE image_config SET {column} = ?", (value,))
        for aspect in aspects:
            await db.execute(
                "UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = ?", (aspect,)
            )
        await db.commit()
    return league


async def _rows(db_path: str, sql: str) -> list[tuple[Any, ...]]:
    async with get_connection(db_path) as db:
        return [tuple(row) for row in await (await db.execute(sql)).fetchall()]


async def _write(db_path: str, sql: str, *args: Any) -> None:
    async with get_connection(db_path) as db:
        await db.execute(sql, args)
        await db.commit()


def _column(column: str) -> str:
    return f"SELECT {column} FROM image_config"


TIER_COLOURS = "SELECT division_slug, slot, colour FROM image_tier_colour ORDER BY division_slug, slot"
CALENDAR_SWITCH = "SELECT enabled FROM image_aspect_toggles WHERE aspect = 'calendar'"
DAILY = "SELECT pfp_daily, pfp_daily_time FROM image_config"


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------

Ask = Callable[[Any], Awaitable[Any]]


def _slash(method: str, command: str, **args: Any) -> Ask:
    """Run `/<command>` (`ImageCog.<method>`) as Admin with *args*; the queue is not run."""
    async def ask(league: Any) -> Any:
        from leaguebot.image.cogs.image_cog import ImageCog

        asked = interaction(league.bot, command)
        asked.response.send_modal = AsyncMock()
        await undecorate(getattr(ImageCog, method))(ImageCog(league.bot), asked, **args)
        return asked
    return ask


def _directory(column: str, value: str) -> Ask:
    name = ASSET_DIRECTORIES[column][0]
    return _slash(f"config_{name.replace('-', '_')}", f"images config {name}", directory=value)


def _template(column: str, filename: str) -> Ask:
    name = TEMPLATE_COMMAND_NAMES[column]
    return _slash(
        f"config_{name.replace('-', '_')}_template", f"images template {name}", filename=filename
    )


def _toggle() -> Ask:
    return _slash("config_toggle", "images config toggle", aspect=CALENDAR)


def _time_format() -> Ask:
    return _slash("config_time_format", "images config time-format", clock=TWELVE_HOUR)


def _palette(division: str, text: str) -> Ask:
    """Submit the "Set one tier's colours" form for *division* with *text* pasted into it."""
    async def ask(league: Any) -> Any:
        from leaguebot.image.cogs.image_cog import ImageCog, TierPaletteModal

        modal = TierPaletteModal(ImageCog(league.bot), division)
        modal.block._value = text  # type: ignore[attr-defined]
        asked = interaction(league.bot)
        await modal.on_submit(asked)
        return asked
    return ask


def _import_file(xml: str) -> Ask:
    """`/images config colour-xml-import` with *xml* attached as a file."""
    raw = xml.encode("utf-8")
    attachment = MagicMock(spec=discord.Attachment)
    attachment.size = len(raw)
    attachment.read = AsyncMock(return_value=raw)
    return _slash("config_colour_xml_import", "images config colour-xml-import", file=attachment)


def _confirm(time_of_day: str) -> Ask:
    """Press ✅ Confirm on the daily portrait confirmation offered for *time_of_day* UTC."""
    async def ask(league: Any) -> Any:
        from leaguebot.image.cogs.image_cog import ImageCog, PortraitTimeConfirm

        opened = interaction(league.bot, "images use-pfp daily-toggle")
        view = PortraitTimeConfirm(ImageCog(league.bot), time_of_day, opened)
        press = interaction(league.bot)

        async def _take_down(*_args: Any, **_kwargs: Any) -> None:
            # Editing the message the buttons sit on answers the press, as Discord's does.
            press.response.is_done = MagicMock(return_value=True)

        press.response.edit_message = AsyncMock(side_effect=_take_down)
        await view.confirm.callback(press)
        return press
    return ask


def _refused(what: str) -> str:
    return f"⛔ {what} refused for {NAMED} — "


def _failed(what: str) -> str:
    return f"❌ {what} failed for {NAMED} — "


def _matches(value: Any, expected: Any) -> None:
    if callable(expected):
        assert expected(value), value
    else:
        assert value == expected


def _starts(text: str) -> Callable[[str], bool]:
    return lambda reply: reply.startswith(text)


# ---------------------------------------------------------------------------
# Each setting, as it saves
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One image setting: its kind, how it is asked, what the acknowledgement names, the reply
    once run (or a test of it), the line after the member's name, each audit entry (its type, old
    and new value), a read of what it saved with the rows it holds, and the configuration it
    starts from."""

    kind: str
    ask: Ask
    doing: str
    reply: Any
    line: str
    audits: tuple[tuple[str, Any, Any], ...]
    query: str
    rows: list[tuple[Any, ...]]
    setup: dict[str, Any] = field(default_factory=dict)


def _directory_case(column: str) -> Case:
    name, default, packaged = ASSET_DIRECTORIES[column]
    label = ASSET_LABELS[column]
    return Case(
        "directory",
        _directory(column, packaged),
        f"Setting **{label}** to `{packaged}`",
        f"✅ **{label}** set to `{packaged}`.\n✅ Resolves.\n"
        f"Searched: `{resolve_within_project_root(packaged)}`",
        f"/images config {name} | Success\n  {label}: {packaged}",
        (("IMAGE_CONFIG_SET", {column: default}, {column: packaged}),),
        _column(column), [(packaged,)],
    )


def _template_case(column: str) -> Case:
    label = TEMPLATE_LABELS[column]
    filename = TEMPLATE_COLUMNS[column]
    return Case(
        "template",
        _template(column, filename),
        f"Setting the **{label}** template to `{filename}`",
        f"✅ **{label}** template set to `{filename}`.\n✅ Valid.",
        f"/images template {TEMPLATE_COMMAND_NAMES[column]} | Success\n"
        f"  {label} template: {filename}",
        (("IMAGE_CONFIG_SET", {column: "old.svg"}, {column: filename}),),
        _column(column), [(filename,)],
        {column: "old.svg"},
    )


def _portrait_case(method: str, command: str, column: str, label: str, enabled: bool,
                   setup: dict[str, Any]) -> Case:
    state = "enabled" if enabled else "disabled"
    return Case(
        "portraits",
        _slash(method, command),
        f"Turning **{label}** {'on' if enabled else 'off'}",
        f"{'✅' if enabled else '❌'} **{label}** {state}.",
        f"/{command} | Success\n  {label}: {state}",
        (("IMAGE_CONFIG_SET", {column: not enabled}, {column: enabled}),),
        _column(column), [(int(enabled),)],
        setup,
    )


#: Portraits obtained from Discord, updated before each drawing and daily at 03:00.
BOTH_UPDATES = {"use_pfp": 1, "pfp_prerender": 1, "pfp_daily": 1}
#: The sixteen templates, every one set by the tests, in `sorted()` order.
TEMPLATES = tuple(sorted(TEMPLATE_COLUMNS))
TEMPLATE_DIRECTORY = "resources/league/templates"

CASES: dict[str, Case] = {
    **{f"directory-{column}": _directory_case(column) for column in sorted(ASSET_DIRECTORIES)},
    "template-directory": Case(
        "template_directory",
        _slash("config_template_directory", "images config template-directory",
               directory=TEMPLATE_DIRECTORY),
        f"Setting **Template directory** to `{TEMPLATE_DIRECTORY}`",
        f"✅ **Template directory** set to `{TEMPLATE_DIRECTORY}`.\n"
        "✅ Every drawing the outputs you have switched on need is present and valid.\n"
        f"Searched: `{resolve_within_project_root(TEMPLATE_DIRECTORY)}`",
        f"/images config template-directory | Success\n  Template directory: {TEMPLATE_DIRECTORY}",
        (("IMAGE_CONFIG_SET", {"template_directory": "resources/defaults/templates"},
          {"template_directory": TEMPLATE_DIRECTORY}),),
        _column("template_directory"), [(TEMPLATE_DIRECTORY,)],
    ),
    **{f"template-{column}": _template_case(column) for column in TEMPLATES},
    "aspect-on": Case(
        "aspect",
        _toggle(),
        "Switching **Calendar** image output on",
        "✅ **Calendar** image output **enabled**.",
        "/images config toggle | Success\n  Calendar image output: enabled",
        (("IMAGE_ASPECT_SET", {"aspect": "calendar", "enabled": False},
          {"aspect": "calendar", "enabled": True}),),
        CALENDAR_SWITCH, [(1,)],
    ),
    "aspect-off": Case(
        "aspect",
        _toggle(),
        "Switching **Calendar** image output off",
        "❌ **Calendar** image output **disabled**. Posting stays as text.",
        "/images config toggle | Success\n  Calendar image output: disabled",
        (("IMAGE_ASPECT_SET", {"aspect": "calendar", "enabled": True},
          {"aspect": "calendar", "enabled": False}),),
        CALENDAR_SWITCH, [(0,)],
        {"aspects": ("calendar",)},
    ),
    "time-zone": Case(
        "presentation",
        _slash("config_time_zone", "images config time-zone", zone="Europe/Lisbon"),
        "Setting the time zone to `Europe/Lisbon`",
        "✅ Time zone set to `Europe/Lisbon`.\nTimes are shown in the offset that zone carries "
        "**on the date displayed**, so a season spanning a daylight-saving change stays correct.",
        "/images config time-zone | Success\n  Time zone: Europe/Lisbon",
        (("IMAGE_CONFIG_SET", {"time_zone": "UTC"}, {"time_zone": "Europe/Lisbon"}),),
        _column("time_zone"), [("Europe/Lisbon",)],
    ),
    "time-format": Case(
        "presentation",
        _time_format(),
        "Setting the clock format to **12-hour (2:30 PM)**",
        "✅ Clock format set to **12-hour (2:30 PM)**.",
        "/images config time-format | Success\n  Clock format: 12-hour (2:30 PM) (12H)",
        (("IMAGE_CONFIG_SET", {"time_format": "24H"}, {"time_format": "12H"}),),
        _column("time_format"), [("12H",)],
    ),
    "date-format": Case(
        "presentation",
        _slash("config_date_format", "images config date-format",
               style=app_commands.Choice(name="14 Jun 2026", value="DD_MON_YYYY")),
        "Setting the date format to **14 Jun 2026**",
        "✅ Date format set to **14 Jun 2026**.",
        "/images config date-format | Success\n  Date format: 14 Jun 2026 (DD_MON_YYYY)",
        (("IMAGE_CONFIG_SET", {"date_format": "DDD_DD_MON_YYYY"},
          {"date_format": "DD_MON_YYYY"}),),
        _column("date_format"), [("DD_MON_YYYY",)],
    ),
    "fastest-lap-colour": Case(
        "fastest_lap_colour",
        _slash("config_fastest_lap_colour", "images config fastest-lap-colour", colour="#00FF00"),
        "Setting the fastest-lap colour to `#00FF00`",
        _starts("✅ Fastest-lap colour set to `#00FF00`.\n"),
        "/images config fastest-lap-colour | Success\n  Fastest-lap colour: #00FF00",
        (("IMAGE_CONFIG_SET", {"fastest_lap_colour": "#A020F0"},
          {"fastest_lap_colour": "#00FF00"}),),
        _column("fastest_lap_colour"), [("#00FF00",)],
    ),
    "per-tier-toggle": Case(
        "per_tier",
        _slash("config_per_tier_colour_toggle", "images config per-tier-colour-toggle",
               enable=True),
        "Turning per-tier colours on",
        _starts("✅ Per-tier colours are now **on**.\n"),
        "/images config per-tier-colour-toggle | Success\n  Per-tier colours: on",
        (("IMAGE_CONFIG_SET", {"per_tier_colour_enabled": False},
          {"per_tier_colour_enabled": True}),),
        _column("per_tier_colour_enabled"), [(1,)],
    ),
    "tier-colour": Case(
        "tier_colours",
        _slash("config_per_tier_set_colour", "images config per-tier-set-colour",
               division="Division 1", slot="accent", colour="#A78BFA"),
        "Setting `accent` for **Division 1** to `#A78BFA`",
        _starts("✅ **Division 1** — `accent` set to `#A78BFA`."),
        "/images config per-tier-set-colour | Success\n"
        "  Tier colour: Division 1 / accent = #A78BFA",
        (("IMAGE_TIER_COLOUR_SET", {"division": "Division 1", "slot": "accent", "colour": None},
          {"division": "Division 1", "slot": "accent", "colour": "#A78BFA"}),),
        TIER_COLOURS, [("division_1", "accent", "#A78BFA")],
    ),
    "tier-palette": Case(
        "tier_colours",
        _palette("Division 1", "accent #A78BFA\nink #F7F6F8"),
        "Setting 2 colour(s) for **Division 1**",
        _starts(
            "✅ **Division 1** — 2 colour(s) set.\n  • `accent` = `#A78BFA`\n"
            "  • `ink` = `#F7F6F8`"
        ),
        "/images config per-tier-bulk-colour | Success\n  Tier colours: Division 1\n"
        "  accent = #A78BFA\n  ink = #F7F6F8",
        (("IMAGE_TIER_COLOUR_SET", {"division": "Division 1", "slot": "accent", "colour": None},
          {"division": "Division 1", "slot": "accent", "colour": "#A78BFA"}),
         ("IMAGE_TIER_COLOUR_SET", {"division": "Division 1", "slot": "ink", "colour": None},
          {"division": "Division 1", "slot": "ink", "colour": "#F7F6F8"})),
        TIER_COLOURS, [("division_1", "accent", "#A78BFA"), ("division_1", "ink", "#F7F6F8")],
    ),
    "tier-import": Case(
        "tier_import",
        _import_file(TWO_TIERS),
        "Importing colours for 2 tier(s)",
        "✅ Imported 2 colour(s) across 2 tier(s).\n  • **Division 1** — 1 colour(s)\n"
        "  • **Division 2** — 1 colour(s)",
        "/images config colour-xml-import | Success\n  Division 1: 1 colour(s)\n"
        "  Division 2: 1 colour(s)",
        (("IMAGE_TIER_COLOUR_SET", {"division": "Division 1", "slot": "accent", "colour": None},
          {"division": "Division 1", "slot": "accent", "colour": "#3DD6F5"}),
         ("IMAGE_TIER_COLOUR_SET", {"division": "Division 2", "slot": "accent", "colour": None},
          {"division": "Division 2", "slot": "accent", "colour": "#F5A623"})),
        TIER_COLOURS, [("division_1", "accent", "#3DD6F5"), ("division_2", "accent", "#F5A623")],
    ),
    "portraits-master": _portrait_case(
        "use_pfp_toggle", "images use-pfp toggle", "use_pfp", "Discord profile pictures", True,
        {},
    ),
    "portraits-prerender": _portrait_case(
        "use_pfp_prerender_toggle", "images use-pfp prerender-toggle", "pfp_prerender",
        "Pre-render portrait updates", True, {"use_pfp": 1, "pfp_prerender": 0, "pfp_daily": 1},
    ),
    "portraits-daily-off": _portrait_case(
        "use_pfp_daily_toggle", "images use-pfp daily-toggle", "pfp_daily",
        "Daily driver-portrait updates", False, BOTH_UPDATES,
    ),
    "daily-portraits": Case(
        "daily_portraits",
        _confirm("04:30"),
        "Enabling daily driver-portrait updates at **04:30 UTC**",
        "✅ **Daily driver-portrait updates** enabled, running at **04:30 UTC** each day.",
        "/images use-pfp daily-toggle | Success\n  Time of day: 04:30 UTC",
        (("IMAGE_CONFIG_SET", {"pfp_daily": False, "pfp_daily_time": "03:00"},
          {"pfp_daily": True, "pfp_daily_time": "04:30"}),),
        DAILY, [(1, "04:30")],
        {"use_pfp": 1},
    ),
}


async def _saves_with_record(tmp_path: Any, case: Case) -> Any:
    """Ask *case*, acknowledged at once naming what is being set with nothing saved yet, run the
    queue, and assert the value, its audit entries and its one line were saved, the
    acknowledgement updated with today's words; return the league."""
    league = await _league(tmp_path, **case.setup)
    before = await _rows(league.db_path, case.query)

    asked = await case.ask(league)
    assert said(asked)[0].startswith(f"⏳ {case.doing}. ")
    assert await _rows(league.db_path, case.query) == before
    await run_queue(league.bot)

    _matches(last_said(asked), case.reply)
    assert await _rows(league.db_path, case.query) == case.rows
    audits = await audit_rows(league.db_path)
    assert [(a["change_type"], a["actor_id"]) for a in audits] == [
        (kind, MEMBER_ID) for kind, _old, _new in case.audits
    ]
    for audit, (_kind, old, new) in zip(audits, case.audits):
        _matches(audit["old_value"], old)
        _matches(audit["new_value"], new)
    assert await log_lines(league) == [f"{NAMED} | {case.line}"]
    return league


# ---------------------------------------------------------------------------
# Defect 13: each saved with its audit entry and line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", sorted(ASSET_DIRECTORIES))
async def test_an_asset_directory_saves_with_its_audit_entry_and_line(tmp_path, column):
    """Each of the eight asset directory commands, pointed from the league's folder at the one
    the bot ships: once run, the directory is stored, with one `IMAGE_CONFIG_SET` entry from the
    old folder to the new and its Success line naming the folder."""
    await _saves_with_record(tmp_path, CASES[f"directory-{column}"])


async def test_the_template_directory_saves_with_its_audit_entry_and_line(tmp_path):
    """`/images config template-directory resources/league/templates`, every output switched
    off: once run, the folder is stored, with one `IMAGE_CONFIG_SET` entry from the shipped
    folder to it and its Success line."""
    await _saves_with_record(tmp_path, CASES["template-directory"])


@pytest.mark.parametrize("column", TEMPLATES)
async def test_a_template_saves_with_its_audit_entry_and_line(tmp_path, column):
    """Each of the sixteen template commands by name, the template standing at `old.svg`, set
    to the file the bot ships: once run, the file is stored, with one `IMAGE_CONFIG_SET` entry
    from `old.svg` to it and its Success line."""
    await _saves_with_record(tmp_path, CASES[f"template-{column}"])


@pytest.mark.parametrize("route", ["aspect-on", "aspect-off"])
async def test_switching_an_output_saves_with_its_audit_entry_and_line(tmp_path, route):
    """`/images config toggle Calendar`, switching the calendar on from off, and off from on:
    once run, the switch is stored, with one `IMAGE_ASPECT_SET` entry naming the output and its
    state before and after, and its Success line."""
    await _saves_with_record(tmp_path, CASES[route])


@pytest.mark.parametrize("route", ["time-zone", "time-format", "date-format"])
async def test_the_presentation_settings_save_with_their_audit_entries_and_lines(tmp_path, route):
    """The time zone set to Europe/Lisbon, the clock to 12-hour and the date format to
    "14 Jun 2026": once run, each is stored, with one `IMAGE_CONFIG_SET` entry from its default
    and its Success line."""
    await _saves_with_record(tmp_path, CASES[route])


async def test_the_fastest_lap_colour_saves_with_its_audit_entry_and_line(tmp_path):
    """`/images config fastest-lap-colour #00FF00`: once run, the colour is stored, with one
    `IMAGE_CONFIG_SET` entry from #A020F0, and its Success line; the reply opens with today's
    words and goes on to the contrast."""
    await _saves_with_record(tmp_path, CASES["fastest-lap-colour"])


async def test_the_per_tier_colours_toggle_saves_with_its_audit_entry_and_line(tmp_path):
    """`/images config per-tier-colour-toggle True`: once run, per-tier colours are on, with one
    `IMAGE_CONFIG_SET` entry from off to on, and its Success line."""
    await _saves_with_record(tmp_path, CASES["per-tier-toggle"])


async def test_a_tier_colour_saves_with_its_audit_entry_and_line(tmp_path):
    """`/images config per-tier-set-colour Division 1 accent #A78BFA`: once run, the colour is
    stored for division_1, with one `IMAGE_TIER_COLOUR_SET` entry naming the division and the
    slot, from no colour to #A78BFA, and its Success line."""
    await _saves_with_record(tmp_path, CASES["tier-colour"])


async def test_a_pasted_palette_saves_each_colour_changed_with_its_audit_entry_and_one_line(
    tmp_path
):
    """The "Set one tier's colours" form for Division 1, pasting accent and ink: once run, both
    are stored, with one `IMAGE_TIER_COLOUR_SET` entry per colour and one Success line naming
    both."""
    await _saves_with_record(tmp_path, CASES["tier-palette"])


async def test_a_colour_import_saves_every_tier_in_one_save_with_its_audit_entries(tmp_path):
    """A colour import attached as a file naming Division 1 and Division 2, whose save faults
    on the second tier's audit entry: neither tier is saved, the member is told nothing from
    the import was saved and to run it again, and one failure line is written. Imported again,
    both tiers are saved, with one `IMAGE_TIER_COLOUR_SET` entry each and one Success line."""
    league = await _league(tmp_path)

    asked = await _import_file(TWO_TIERS)(league)
    second_fails = AsyncMock(side_effect=[None, sqlite3.OperationalError("database or disk is full")])
    with patch("leaguebot.core.services.change_queue.record_change_on", second_fails):
        await run_queue(league.bot, steps=1)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply("`/images config colour-xml-import`", IMPORT_NOT_SAVED)
    assert await _rows(league.db_path, TIER_COLOURS) == []
    assert await audit_rows(league.db_path) == []
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith(_failed("`/images config colour-xml-import`"))]
    assert not any("| Success" in line for line in lines)

    (tmp_path / "again").mkdir()
    await _saves_with_record(tmp_path / "again", CASES["tier-import"])


async def test_a_fault_in_a_pasted_palette_s_save_saves_none_of_it_says_paste_it_again_and_the_lane_goes_on(
    tmp_path
):
    """The "Set one tier's colours" form for Division 1, pasting accent and ink, whose save
    faults on its audit entry: neither colour is saved, the member is told nothing from the
    paste was saved and to paste it again, one failure line names the form and no Success line
    names the colours, no job is stopped, and `/images config time-format` asked behind it is
    saved."""
    league = await _league(tmp_path)

    asked = await CASES["tier-palette"].ask(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(PALETTE_FORM, PASTE_NOT_SAVED)
    assert await _rows(league.db_path, TIER_COLOURS) == []
    assert [a["change_type"] for a in await audit_rows(league.db_path)] == ["IMAGE_CONFIG_SET"]
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith("❌ ")] == [
        line for line in lines if line.startswith(_failed(PALETTE_FORM))
    ] != []
    assert not any("Tier colours: Division 1" in line for line in lines)
    assert await stopped_job(league.db_path) is None
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


@pytest.mark.parametrize("route", ["portraits-master", "portraits-prerender", "portraits-daily-off"])
async def test_each_portrait_toggle_saves_with_its_audit_entry_and_line(tmp_path, route):
    """`/images use-pfp toggle` turning portraits on (daily updates off, so nothing is armed),
    `prerender-toggle` turning pre-render updates on (daily updates on), and `daily-toggle`
    turning daily updates off (pre-render updates on): once run, each is stored, with one
    `IMAGE_CONFIG_SET` entry from the old state to the new, and its Success line."""
    await _saves_with_record(tmp_path, CASES[route])


async def test_confirming_daily_portraits_saves_the_time_and_the_flag_in_one_save(tmp_path):
    """✅ Confirm on daily portrait updates at 04:30 UTC, portraits obtained from Discord: once
    run, the time and the flag are stored, with one `IMAGE_CONFIG_SET` entry holding both, the
    daily job is armed at 04:30, and the Success line names the time."""
    league = await _saves_with_record(tmp_path, CASES["daily-portraits"])

    league.scheduler.schedule_portrait_refresh.assert_called_once_with("04:30")


# ---------------------------------------------------------------------------
# On the queue
# ---------------------------------------------------------------------------


async def test_an_image_setting_whose_audit_entry_fails_saves_nothing_and_the_lane_goes_on(
    tmp_path
):
    """`/images config time-zone Europe/Lisbon` whose audit write faults: the zone stays UTC,
    the member is told nothing was saved and to run it again, one failure line is written and no
    Success line, and `/images config time-format 12-hour` asked behind it is saved."""
    league = await _league(tmp_path)

    asked = await CASES["time-zone"].ask(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot, steps=1)
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert last_said(asked) == failure_reply(
        "`/images config time-zone`",
        "Nothing was saved. Run `/images config time-zone` again to retry.",
    )
    assert await _rows(league.db_path, _column("time_zone")) == [("UTC",)]
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith("❌ ")] == [
        line for line in lines if line.startswith(_failed("`/images config time-zone`"))
    ] != []
    assert not any("Time zone: Europe/Lisbon" in line for line in lines)
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


async def test_a_template_broken_by_a_directory_set_ahead_of_it_is_refused_when_it_runs(tmp_path):
    """The template directory asked to move to a folder holding no template, then the calendar
    template (standing at `old.svg`) asked to be the shipped file, which the shipped folder
    holds: the second is acknowledged, and when it runs, the folder having moved, is refused as
    a template not changed, `old.svg` left in force."""
    league = await _league(tmp_path, calendar_template="old.svg")

    await _slash("config_template_directory", "images config template-directory",
                 directory=NO_TEMPLATES)(league)
    second = await _template("calendar_template", "calendar_template.svg")(league)
    assert said(second)[0].startswith("⏳ Setting the **Calendar** template to `calendar_template.svg`. ")
    await run_queue(league.bot)

    reply = last_said(second)
    assert reply.startswith("❌ **Calendar** template was **not** changed — ")
    assert reply.endswith("The previously configured filename is still in force.")
    assert await _rows(league.db_path, _column("template_directory")) == [(NO_TEMPLATES,)]
    assert await _rows(league.db_path, _column("calendar_template")) == [("old.svg",)]
    assert (await log_lines(league))[-1].startswith(_refused("`/images template calendar`"))


async def test_an_output_whose_drawings_broke_meanwhile_is_refused_when_it_runs_naming_why(
    tmp_path
):
    """`/images config toggle Calendar` asked while the calendar drawing is sound, the calendar
    template then pointed at a file that is not there before it runs: when it runs it is refused
    as not switched on, naming the drawing that cannot be found, in the reply and in its "⛔"
    line, and the calendar stays off with no audit entry."""
    league = await _league(tmp_path)

    asked = await _toggle()(league)
    assert said(asked)[0].startswith("⏳ Switching **Calendar** image output on. ")
    await _write(league.db_path, "UPDATE image_config SET calendar_template = 'missing.svg'")
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith(
        "⛔ **Calendar** image output was **not** switched on — it cannot be drawn as things "
        "stand:\n  • Calendar: the drawing file can't be found"
    )
    assert reply.endswith(
        "Put that right and run this command again. The output is still posted as text in the "
        "meantime."
    )
    assert await _rows(league.db_path, CALENDAR_SWITCH) == [(0,)]
    assert await audit_rows(league.db_path) == []
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].startswith(_refused("`/images config toggle`"))
    assert "Calendar: the drawing file can't be found" in lines[0]


async def test_the_daily_job_is_armed_by_a_job_after_the_save(tmp_path):
    """✅ Confirm on daily updates at 04:30: the save stores the time and the flag without
    arming the job or writing the Success line; the job after it arms the job at 04:30 and
    writes the Success line, once."""
    league = await _league(tmp_path, use_pfp=1)
    success = f"{NAMED} | {CASES['daily-portraits'].line}"

    await _confirm("04:30")(league)
    await run_queue(league.bot, steps=1)

    assert await _rows(league.db_path, DAILY) == [(1, "04:30")]
    league.scheduler.schedule_portrait_refresh.assert_not_called()
    assert success not in await log_lines(league)

    await run_queue(league.bot)

    league.scheduler.schedule_portrait_refresh.assert_called_once_with("04:30")
    assert (await log_lines(league)).count(success) == 1


@pytest.mark.parametrize("route", ["confirm", "master-toggle"])
async def test_a_daily_job_that_cannot_be_armed_is_reported_as_today_and_the_lane_goes_on(
    tmp_path, route
):
    """The scheduler refusing the daily job: ✅ Confirm on daily updates at 04:30, or
    `/images use-pfp toggle` turning portraits back on with daily updates set at 05:00. The
    setting stays stored, the member gets today's failure reply saying it is stored and will be
    scheduled when the bot next starts, one failure line is written and no Success line, no job
    is stopped, and `/images config time-format` asked behind it is saved."""
    if route == "confirm":
        league = await _league(tmp_path, use_pfp=1)
        ask, what, doing = _confirm("04:30"), "`/images use-pfp daily-toggle`", (
            "Enabling daily driver-portrait updates at **04:30 UTC**")
        stored, expected = DAILY, [(1, "04:30")]
    else:
        league = await _league(tmp_path, use_pfp=0, pfp_prerender=1, pfp_daily=1,
                               pfp_daily_time="05:00")
        ask, what, doing = _slash("use_pfp_toggle", "images use-pfp toggle"), (
            "`/images use-pfp toggle`"), "Turning **Discord profile pictures** on"
        stored, expected = _column("use_pfp"), [(1,)]
    league.scheduler.schedule_portrait_refresh.side_effect = RuntimeError("the job store refused")

    asked = await ask(league)
    assert said(asked)[0].startswith(f"⏳ {doing}. ")
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert await _rows(league.db_path, stored) == expected
    assert last_said(asked) == failure_reply(what, DAILY_JOB_NOT_SCHEDULED)
    lines = await log_lines(league)
    assert [line for line in lines if line.startswith(_failed(what))]
    assert not any(line.startswith(f"{NAMED} | /{what.strip('`')[1:]} | Success") for line in lines)
    assert await stopped_job(league.db_path) is None
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


async def test_a_stop_after_daily_portraits_are_saved_arms_the_job_when_the_change_carries_on(
    tmp_path
):
    """✅ Confirm on daily updates at 04:30, the bot stopping once the time and the flag are
    saved: after a restart the change carries on, arms the job at 04:30, writes the Success line
    once, and is done."""
    league = await _league(tmp_path, use_pfp=1)

    await _confirm("04:30")(league)
    await run_queue(league.bot, steps=1)
    league.scheduler.schedule_portrait_refresh.assert_not_called()

    await restart_queue(league.bot)
    try:
        await run_queue(league.bot)
    finally:
        await maybe_await(league.bot.change_queue.stop())

    league.scheduler.schedule_portrait_refresh.assert_called_once_with("04:30")
    success = f"{NAMED} | {CASES['daily-portraits'].line}"
    assert (await log_lines(league)).count(success) == 1
    assert [row["state"] for row in await change_rows(league.db_path)] == ["DONE"]


async def test_turning_portraits_off_removes_the_daily_job_after_the_save(tmp_path):
    """`/images use-pfp toggle` turning portraits off, daily updates on: the save stores the
    switch without touching the daily job; the job after it removes the daily job, once, and
    the member is told portraits are disabled."""
    league = await _league(tmp_path, **BOTH_UPDATES)

    asked = await _slash("use_pfp_toggle", "images use-pfp toggle")(league)
    await run_queue(league.bot, steps=1)

    assert await _rows(league.db_path, _column("use_pfp")) == [(0,)]
    league.scheduler.cancel_portrait_refresh.assert_not_called()

    await run_queue(league.bot)

    league.scheduler.cancel_portrait_refresh.assert_called_once_with()
    assert last_said(asked) == "❌ **Discord profile pictures** disabled."


async def test_a_daily_job_that_cannot_be_removed_says_so_and_the_lane_goes_on(tmp_path):
    """`/images use-pfp daily-toggle` turning daily updates off, the scheduler refusing to remove
    the job: the setting stays off, the reply is the success followed by a note that the job
    could not be removed and does nothing while the setting is off, the Success line and one
    failure line are written, and `/images config time-format` asked behind it is saved."""
    league = await _league(tmp_path, **BOTH_UPDATES)
    league.scheduler.cancel_portrait_refresh.side_effect = RuntimeError("the job store refused")

    asked = await _slash("use_pfp_daily_toggle", "images use-pfp daily-toggle")(league)
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert await _rows(league.db_path, _column("pfp_daily")) == [(0,)]
    reply = last_said(asked)
    assert reply.startswith("❌ **Daily driver-portrait updates** disabled.")
    assert reply.endswith(NOT_REMOVED)
    lines = await log_lines(league)
    assert lines.count(f"{NAMED} | {CASES['portraits-daily-off'].line}") == 1
    assert [line for line in lines if line.startswith(_failed("`/images use-pfp daily-toggle`"))]
    assert await stopped_job(league.db_path) is None
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


async def test_turning_portraits_off_when_the_daily_job_cannot_be_removed_says_so_and_the_lane_goes_on(
    tmp_path
):
    """`/images use-pfp toggle` turning portraits off with both updates on and the daily job
    armed, the scheduler refusing to remove the job: portraits stay off, the reply is the
    success followed by a note that the job could not be removed and does nothing while the
    setting is off, the Success line and one failure line are written, no job is stopped, and
    `/images config time-format` asked behind it is saved."""
    league = await _league(tmp_path, **BOTH_UPDATES)
    league.scheduler.cancel_portrait_refresh.side_effect = RuntimeError("the job store refused")

    asked = await _slash("use_pfp_toggle", "images use-pfp toggle")(league)
    assert said(asked)[0].startswith("⏳ Turning **Discord profile pictures** off. ")
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert await _rows(league.db_path, _column("use_pfp")) == [(0,)]
    league.scheduler.cancel_portrait_refresh.assert_called_once_with()
    reply = last_said(asked)
    assert reply.startswith("❌ **Discord profile pictures** disabled.")
    assert reply.endswith(NOT_REMOVED)
    lines = await log_lines(league)
    assert lines.count(
        f"{NAMED} | /images use-pfp toggle | Success\n  Discord profile pictures: disabled"
    ) == 1
    assert [line for line in lines if line.startswith(_failed("`/images use-pfp toggle`"))]
    assert await stopped_job(league.db_path) is None
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


@pytest.mark.parametrize(
    ("path", "verdict"),
    [
        ("resources/defaults/tracks", "✅ Resolves."),
        ("resources/defaults/templates/calendar_template.svg",
         "⚠️ That path exists but is not a directory."),
        ("resources/league/no-such-folder-439",
         "⚠️ Nothing is there yet — files placed there later will be picked up."),
    ],
    ids=["exists", "file", "missing"],
)
async def test_the_directory_reply_says_whether_it_resolves(tmp_path, path, verdict):
    """`/images config track-image-directory` pointed at a folder that is there, at a file, and
    at nothing: acknowledged at once, and once run the reply names the folder, whether it
    resolves, and where it was searched."""
    league = await _league(tmp_path)

    asked = await _directory("track_image_directory", path)(league)
    assert said(asked)[0].startswith(f"⏳ Setting **Circuit images** to `{path}`. ")
    await run_queue(league.bot)

    assert last_said(asked) == (
        f"✅ **Circuit images** set to `{path}`.\n{verdict}\n"
        f"Searched: `{resolve_within_project_root(path)}`"
    )


async def test_the_contrast_is_measured_after_the_colour_is_saved(tmp_path):
    """`/images config fastest-lap-colour #00FF00`: nothing reads the templates before the queue
    runs, and every read of them made to measure the contrast finds #00FF00 already stored; the
    reply goes on past its first line to the contrast."""
    league = await _league(tmp_path)
    validity = league.bot.image_validity_service
    reads = validity.template_reports
    stored_when_read: list[str] = []

    async def _reading(*args: Any, **kwargs: Any) -> Any:
        with sqlite3.connect(league.db_path) as db:
            stored_when_read.append(db.execute(_column("fastest_lap_colour")).fetchone()[0])
        return await reads(*args, **kwargs)

    validity.template_reports = _reading

    asked = await CASES["fastest-lap-colour"].ask(league)
    assert said(asked)[0].startswith("⏳ Setting the fastest-lap colour to `#00FF00`. ")
    assert stored_when_read == []
    await run_queue(league.bot)

    assert stored_when_read and set(stored_when_read) == {"#00FF00"}
    reply = last_said(asked)
    assert reply.startswith("✅ Fastest-lap colour set to `#00FF00`.\n")
    assert len(reply.splitlines()) > 1


async def test_a_check_after_the_save_that_faults_says_the_setting_is_saved_and_the_lane_goes_on(
    tmp_path
):
    """`/images config fastest-lap-colour #00FF00`, the templates failing to read when the
    contrast is measured after the save: the colour stays stored, the reply is the success with a
    note that it could not be checked against the templates, the Success line and one failure
    line are written, and `/images config time-format` asked behind it is saved."""
    league = await _league(tmp_path)
    league.bot.image_validity_service.template_reports = AsyncMock(
        side_effect=RuntimeError("templates unreadable")
    )

    asked = await CASES["fastest-lap-colour"].ask(league)
    behind = await _time_format()(league)
    await run_queue(league.bot)

    assert await _rows(league.db_path, _column("fastest_lap_colour")) == [("#00FF00",)]
    assert last_said(asked) == f"✅ Fastest-lap colour set to `#00FF00`.\n{NOT_CHECKED}"
    lines = await log_lines(league)
    assert lines.count(f"{NAMED} | {CASES['fastest-lap-colour'].line}") == 1
    assert [line for line in lines
            if line.startswith(_failed("`/images config fastest-lap-colour`"))]
    assert await stopped_job(league.db_path) is None
    assert last_said(behind) == "✅ Clock format set to **12-hour (2:30 PM)**."


async def test_two_presses_of_an_output_toggle_while_the_first_waits_are_one_change(tmp_path):
    """`/images config toggle Calendar` run twice with the calendar off before the queue runs:
    both read "off" and ask to switch it on, so the second is refused as a repeat, and once run
    the calendar is on, with one change and one audit entry."""
    league = await _league(tmp_path)

    await _toggle()(league)
    second = await _toggle()(league)
    await run_queue(league.bot)

    assert last_said(second).startswith(
        "⚠️ Switching **Calendar** image output on has already been asked for and has not "
        "started yet, so it was not asked for again."
    )
    assert await _rows(league.db_path, CALENDAR_SWITCH) == [(1,)]
    assert len(await audit_rows(league.db_path)) == 1
    assert len(await change_rows(league.db_path)) == 1


async def test_an_output_toggle_found_already_switched_when_it_runs_changes_nothing(tmp_path):
    """Image :180, a flip resolved when asked: `/images config toggle Calendar` with the calendar
    off asks to switch it on. Before the change runs the calendar is switched on (written here
    as a change that started before the ask would leave it). When it runs it finds the calendar
    already on: it changes nothing and says so, never switching it back off, with one "Nothing
    changed" line and no audit entry."""
    league = await _league(tmp_path)

    asked = await _toggle()(league)
    await _write(league.db_path, "UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = 'calendar'")
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "Calendar" in reply and "already **on**" in reply
    assert await _rows(league.db_path, CALENDAR_SWITCH) == [(1,)]
    assert await audit_rows(league.db_path) == []
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0] == f"{NAMED} | /images config toggle | Nothing changed"


@pytest.mark.parametrize(
    ("method", "command", "column", "label", "setup", "state"),
    [
        ("use_pfp_toggle", "images use-pfp toggle", "use_pfp", "Discord profile pictures",
         {}, "on"),
        ("use_pfp_prerender_toggle", "images use-pfp prerender-toggle", "pfp_prerender",
         "Pre-render portrait updates", {"use_pfp": 1, "pfp_prerender": 0, "pfp_daily": 1}, "on"),
        ("use_pfp_daily_toggle", "images use-pfp daily-toggle", "pfp_daily",
         "Daily driver-portrait updates", BOTH_UPDATES, "off"),
    ],
    ids=["toggle", "prerender-toggle", "daily-toggle"],
)
async def test_a_portrait_toggle_found_already_switched_when_it_runs_changes_nothing(
    tmp_path, method, command, column, label, setup, state
):
    """Image :180 for each `/images use-pfp` toggle, a flip resolved when asked: `toggle` with
    portraits off and `prerender-toggle` with pre-render updates off each ask to turn theirs on;
    `daily-toggle`, whose turning on opens the time form rather than asking a flip, is taken
    with daily updates on, asking to turn them off. Before the change runs the setting is put
    where it asks (written here as a change that started before the ask would leave it). When it
    runs it finds it already there: it changes nothing and says so, never flipping it back, with
    one "Nothing changed" line, no audit entry, and the daily job neither armed nor removed."""
    league = await _league(tmp_path, **setup)
    wanted = int(state == "on")

    asked = await _slash(method, command)(league)
    assert said(asked)[0].startswith(f"⏳ Turning **{label}** {state}. ")
    await _write(league.db_path, f"UPDATE image_config SET {column} = ?", wanted)
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert label in reply and f"already **{state}**" in reply
    assert await _rows(league.db_path, _column(column)) == [(wanted,)]
    assert await audit_rows(league.db_path) == []
    league.scheduler.schedule_portrait_refresh.assert_not_called()
    league.scheduler.cancel_portrait_refresh.assert_not_called()
    lines = await log_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0] == f"{NAMED} | /{command} | Nothing changed"


async def test_a_sub_toggle_whose_partner_was_switched_off_ahead_is_refused_when_it_runs(tmp_path):
    """Portraits obtained from Discord with both updates on: `prerender-toggle` asked to turn
    pre-render updates off, then `daily-toggle` asked to turn daily updates off while pre-render
    still stood on. The first runs; the second, now the only way portraits would be fetched, is
    refused when it runs in today's words, daily updates left on and the job left armed."""
    league = await _league(tmp_path, **BOTH_UPDATES)

    await _slash("use_pfp_prerender_toggle", "images use-pfp prerender-toggle")(league)
    second = await _slash("use_pfp_daily_toggle", "images use-pfp daily-toggle")(league)
    assert said(second)[0].startswith("⏳ Turning **Daily driver-portrait updates** off. ")
    await run_queue(league.bot)

    assert last_said(second) == (
        "❌ Cannot disable daily updates: pre-render updates are off, so this is the only way "
        "profile pictures would ever be fetched. Enable pre-render updates first, or turn off "
        "`/images use-pfp toggle` entirely."
    )
    assert await _rows(league.db_path, "SELECT pfp_prerender, pfp_daily FROM image_config") == [
        (0, 1)
    ]
    league.scheduler.cancel_portrait_refresh.assert_not_called()
    assert (await log_lines(league))[-1].startswith(_refused("`/images use-pfp daily-toggle`"))


#: One command of each of the eleven image settings, by kind.
KINDS = {case.kind: name for name, case in reversed(list(CASES.items()))}


@pytest.mark.parametrize("kind", sorted(KINDS))
async def test_the_image_settings_are_acknowledged_naming_what_is_set(tmp_path, kind):
    """One command, form, import or button of each of the eleven image settings is answered
    first with the queue's acknowledgement naming what is being set (a deferred one's as a
    follow-up), one change is queued, and nothing is saved before the queue runs."""
    case = CASES[KINDS[kind]]
    league = await _league(tmp_path, **case.setup)
    before = await _rows(league.db_path, case.query)

    asked = await case.ask(league)

    assert said(asked)[0].startswith(f"⏳ {case.doing}. ")
    assert await _rows(league.db_path, case.query) == before
    assert len(await change_rows(league.db_path)) == 1
