"""What the image module's commands write to the league's log channel.

Every command that changes something, or tries to, records every outcome there, whoever used it
(the core specification's "The record of what changed"; #482): a refusal as one "⛔" line naming
the command, or the form that refused it. A command that changes nothing — `/images config
view` and the twelve previews — records no outcome of its own.

Each command is reached past its permission guard, against the real cog, with the shared doubles
of `tests/support/image_cog_doubles.py`. The image settings run on the change queue (#439, slice
7): a command that changes something is driven on a league built by the migrations, with the real
image services and the real queue, which the test runs; the member's reply is the
acknowledgement's update, and its lines are those the log channel took and those the queue saved.
The rasteriser is taken as present, since no test here draws.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.utils.messages import chunk_message
from leaguebot.image.cogs.image_cog import ImageCog, TierPaletteModal
from leaguebot.image.models.image_constants import (
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    TEMPLATE_COMMAND_NAMES,
)
from leaguebot.image.services.image_validity_service import Problem
from tests.support.change_queue import run_queue
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction,
    log_bot,
    logged,
    said,
)
from tests.support.settings_league import audit_rows, settings_league
from tests.support.undecorate import undecorate



def _commands() -> list[app_commands.Command]:
    """Every `/images` command, in name order."""
    return sorted(
        (
            command
            for command in ImageCog.images.walk_commands()
            if isinstance(command, app_commands.Command)
        ),
        key=lambda command: command.qualified_name,
    )


def _changes_nothing(command: app_commands.Command) -> bool:
    """`/images config view` and the previews, which change nothing."""
    return command.qualified_name == "images config view" or command.qualified_name.startswith(
        "images test "
    )


ACTING = [c.qualified_name for c in _commands() if not _changes_nothing(c)]
LOOKING = [c.qualified_name for c in _commands() if _changes_nothing(c)]


def _command(name: str) -> app_commands.Command:
    return next(command for command in _commands() if command.qualified_name == name)


def _argument(parameter: app_commands.Parameter):
    """A value of the kind *parameter* takes: the first of its choices, where it has some."""
    if parameter.choices:
        first = parameter.choices[0]
        return app_commands.Choice(name=first.name, value=first.value)
    if parameter.type is discord.AppCommandOptionType.boolean:
        return True
    if parameter.type is discord.AppCommandOptionType.integer:
        return 1
    if parameter.type is discord.AppCommandOptionType.attachment:
        return None
    return "Division 1"


@pytest.fixture(autouse=True)
def _rasteriser_present(monkeypatch):
    """The rasteriser taken as present wherever a check asks for it: no test here draws."""
    import leaguebot.image.services.image_render_service as render

    monkeypatch.setattr(render, "converter_available", lambda: True)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        return
    monkeypatch.setattr(image_settings, "converter_available", lambda: True, raising=False)


async def _league(tmp_path, *, modules=("images",), tier_colours=(), no_configuration=False,
                  **config) -> Any:
    """A league with *modules* on, the image configuration's defaults with *config* written over
    them, *tier_colours* (slug, slot, colour) stored, or, with *no_configuration*, the module on
    and no configuration at all; its bot is `log_bot`'s real-queue form."""
    league = await settings_league(tmp_path, modules=modules)
    async with get_connection(league.db_path) as db:
        for column, value in config.items():
            await db.execute(f"UPDATE image_config SET {column} = ?", (value,))
        for row in tier_colours:
            await db.execute(
                "INSERT INTO image_tier_colour (division_slug, slot, colour) VALUES (?, ?, ?)", row
            )
        if no_configuration:
            await db.execute("DELETE FROM image_config")
        await db.commit()
    if no_configuration:
        # The module's flag is a column of the configuration, so a league with the module on
        # and no configuration is reached only by the module service answering for it.
        league.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    log_bot(league)
    return league


async def _module_off_cog(tmp_path) -> ImageCog:
    return ImageCog((await _league(tmp_path, modules=())).bot)


async def _run(cog: ImageCog, name: str):
    """Run `/<name>` through *cog*, every option given a value of its kind, then the queue."""
    command = _command(name)
    asked = interaction(name, bot=cog.bot)
    arguments = [_argument(parameter) for parameter in command.parameters]
    await undecorate(command)(cog, asked, *arguments)
    await run_queue(cog.bot)
    return asked


def test_both_lists_are_whole():
    """The two lists below cover every command, so a new one cannot slip past both."""
    assert "images config view" in LOOKING
    assert len([name for name in LOOKING if name.startswith("images test ")]) == 12
    assert "images config per-tier-bulk-colour" in ACTING
    assert "images use-pfp daily-toggle" in ACTING
    assert len(ACTING) + len(LOOKING) == len(_commands())


# ── A4: the module gate ───────────────────────────────────────────────────


@pytest.mark.parametrize("name", ACTING)
async def test_an_acting_command_refused_by_the_module_gate_records_one_line(tmp_path, name):
    cog = await _module_off_cog(tmp_path)

    asked = await _run(cog, name)

    assert "The Image module is not enabled" in said(asked)
    assert_one_refusal(cog.bot, f"`/{name}`")


@pytest.mark.parametrize("name", LOOKING)
async def test_a_command_that_changes_nothing_records_nothing_when_refused(tmp_path, name):
    cog = await _module_off_cog(tmp_path)

    asked = await _run(cog, name)

    assert "The Image module is not enabled" in said(asked)
    assert logged(cog.bot) == []


# ── A21 (S1): the configuration report arrives in parts ───────────────────


async def test_a_long_configuration_report_is_sent_in_the_parts_core_splits_it_into():
    bot = log_bot()
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    cog = ImageCog(bot)
    report = "\n".join(f"Setting {index:02d}: " + "x" * 48 for index in range(60))
    cog.build_configuration_report = AsyncMock(return_value=report)
    asked = interaction("images config view", bot=bot)

    await undecorate(ImageCog.config_view)(cog, asked)

    assert asked.said == chunk_message(report)
    assert logged(bot) == []


# ── A22: a refused folder's reply arrives whole ───────────────────────────

#: The folder the bot ships its templates in, inside the project root. The reply spells it as
#: the host does, so a check reads it through `Path` (backslashes on Windows).
SHIPPED_TEMPLATES = "resources/defaults/templates"


async def test_a_long_refusal_of_a_folder_is_sent_whole_in_parts(tmp_path, monkeypatch):
    """`/images config template-directory` on a folder the survey finds six long faults in: the
    refusal names every one and the folder searched, in more than one part, each fitting one
    Discord message."""
    import leaguebot.image.services.image_validity_service as validity

    problems = [
        Problem(
            kind="NOT_FOUND",
            detail=f"fault {index}: " + "y" * 380,
            template_key=f"t{index}_template",
        )
        for index in range(6)
    ]

    def survey(config, toggles, **kwargs):
        return list(problems)

    monkeypatch.setattr(validity, "blocking_template_problems", survey)
    try:
        import leaguebot.image.services.image_settings as image_settings
    except ImportError:
        pass
    else:
        monkeypatch.setattr(image_settings, "blocking_template_problems", survey, raising=False)
    league = await _league(tmp_path)

    asked = await _run_with(league, "images config template-directory", SHIPPED_TEMPLATES)

    reply = said(asked)
    assert all(len(part) <= 2000 for part in asked.said)
    assert len(asked.said) > 1
    assert "fault 5: " in reply
    assert any(
        line.startswith("Searched: `") and line.endswith(f"{Path(SHIPPED_TEMPLATES)}`")
        for line in reply.splitlines()
    ), reply


# ── A5: each command's own refusal ────────────────────────────────────────

#: The colour form a pasted palette arrives through, as its refusals name it.
BULK_FORM = "the “Set one tier's colours” form"
#: The league's own template folder, every output switched off, so nothing is needed of it.
TEMPLATE_DIRECTORY = "resources/league/templates"
#: The image configuration's tables: what a refused command must leave as it found them.
TABLES = ("image_config", "image_aspect_toggles", "image_tier_colour")
#: Portraits obtained from Discord, updated before each drawing, not daily.
PORTRAITS = {"use_pfp": 1, "pfp_prerender": 1, "pfp_daily": 0}


async def _stored(db_path: str) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of the image configuration's tables."""
    async with get_connection(db_path) as db:
        return {
            table: [tuple(row) for row in await (await db.execute(f"SELECT * FROM {table}")).fetchall()]
            for table in TABLES
        }


async def _run_with(league: Any, name: str, *arguments):
    """Run `/<name>` as Race Control with *arguments*, past its permission guard, then the
    queue."""
    asked = interaction(name, bot=league.bot)
    await undecorate(_command(name))(ImageCog(league.bot), asked, *arguments)
    await run_queue(league.bot)
    return asked


async def _submit_palette(league: Any, text: str):
    """Submit the "Set one tier's colours" form for Division 1 with *text*, then run the queue."""
    form = TierPaletteModal(ImageCog(league.bot), "Division 1")
    form.block._value = text
    submitted = interaction(None, bot=league.bot)
    await form.on_submit(submitted)
    await run_queue(league.bot)
    return submitted


def _attached(raw: bytes) -> MagicMock:
    """A file attached to `/images config colour-xml-import`, holding *raw*."""
    attachment = MagicMock()
    attachment.size = len(raw)
    attachment.read = AsyncMock(return_value=raw)
    return attachment


def _reply(asked) -> str:
    """What the member's reply reads once the change has run: the last word they were given."""
    return asked.said[-1]


def _acknowledged(asked) -> None:
    """The command was acknowledged at once as under way, before anything else was said."""
    assert asked.said and asked.said[0].startswith("⏳ "), asked.said


#: Each refusal of plan section 2 not pinned in a module's own test file: the command, what
#: it is given, how the league stands, and a phrase of the reply it gives today.
OWN_REFUSALS = [
    pytest.param(
        "images use-pfp prerender-toggle", (), {"use_pfp": 0},
        "Driver portraits are not being obtained from Discord",
        id="prerender-toggle-portraits-off",
    ),
    pytest.param(
        "images use-pfp daily-toggle", (), {"use_pfp": 0},
        "Driver portraits are not being obtained from Discord",
        id="daily-toggle-portraits-off",
    ),
    pytest.param(
        "images use-pfp prerender-toggle", (), PORTRAITS,
        "Cannot disable pre-render updates",
        id="prerender-toggle-last-trigger",
    ),
    pytest.param(
        "images use-pfp toggle", (), {"no_configuration": True},
        "The image module has no configuration yet.",
        id="use-pfp-toggle-no-configuration",
    ),
    pytest.param(
        "images config template-directory", ("",), {},
        "Directory cannot be empty.",
        id="template-directory-empty",
    ),
    pytest.param(
        "images config template-directory", (TEMPLATE_DIRECTORY,), {"no_configuration": True},
        "this server has no image configuration to amend.",
        id="template-directory-no-configuration",
    ),
    pytest.param(
        "images config track-image-directory", ("",), {},
        "Directory cannot be empty.",
        id="asset-directory-empty",
    ),
    pytest.param(
        "images template calendar", ("drawings/calendar.svg",), {},
        "not a path",
        id="template-given-a-path",
    ),
    pytest.param(
        "images template calendar", ("calendar.svg",), {"no_configuration": True},
        "this server has no image configuration to amend.",
        id="template-no-configuration",
    ),
    pytest.param(
        "images template calendar", ("nothing-here.svg",), {},
        "nothing-here.svg",
        id="template-invalid",
    ),
    pytest.param(
        "images config fastest-lap-colour", ("purple",), {},
        "The stored colour is unchanged.",
        id="fastest-lap-colour-invalid",
    ),
    pytest.param(
        "images config per-tier-set-colour", ("Division 1", "not a slot!", "#A78BFA"), {},
        "Nothing was stored.",
        id="per-tier-set-colour-invalid-slot",
    ),
    pytest.param(
        "images config per-tier-set-colour", ("Division 1", "accent", "purple"), {},
        "Nothing was stored.",
        id="per-tier-set-colour-invalid-colour",
    ),
    pytest.param(
        "images config colour-xml-import", (_attached(b""),), {},
        "The attached file is empty.",
        id="colour-xml-import-empty",
    ),
    pytest.param(
        "images config colour-xml-import", (_attached(b"\xff\xfe<palettes>"),), {},
        "could not be decoded as UTF-8",
        id="colour-xml-import-not-utf-8",
    ),
    pytest.param(
        "images config colour-xml-import",
        (
            _attached(
                b'<palettes><division name="Bad"><colour slot="accent">purple</colour>'
                b"</division></palettes>"
            ),
        ),
        {},
        "Bad",
        id="colour-xml-import-every-block-rejected",
    ),
    pytest.param(
        "images config time-zone", ("Mars/Olympus",), {},
        "is not a recognised time zone",
        id="time-zone-unknown",
    ),
]


@pytest.mark.parametrize("name, arguments, config, reply", OWN_REFUSALS)
async def test_a_command_s_own_refusal_records_one_line_naming_it(
    tmp_path, name, arguments, config, reply
):
    """The reply is the one given today; the log channel gains one "⛔" line naming the
    command, and nothing is stored."""
    league = await _league(tmp_path, **config)
    before = await _stored(league.db_path)

    asked = await _run_with(league, name, *arguments)

    assert reply in said(asked)
    assert await _stored(league.db_path) == before
    assert await audit_rows(league.db_path) == []
    assert_one_refusal(league.bot, f"`/{name}`")


async def test_a_pasted_palette_with_an_unreadable_line_is_refused_by_the_form(tmp_path):
    """The colour form refuses the whole palette, as today, and the line names the form."""
    league = await _league(tmp_path)

    submitted = await _submit_palette(league, "accent #A78BFA\nink not-a-colour")

    assert "could not be read" in said(submitted)
    assert (await _stored(league.db_path))["image_tier_colour"] == []
    assert_one_refusal(league.bot, BULK_FORM)


# ── A6, A7, A8: each success is recorded under its own command ────────────

#: A palette file whose one block a league could store.
ONE_TIER = (
    b'<palettes><division name="Division 1"><colour slot="accent">#A78BFA</colour>'
    b"</division></palettes>"
)
#: A palette file with one block to store and one, "Bad", that cannot be read.
PART_READABLE = (
    b'<palettes><division name="Good"><colour slot="accent">#A78BFA</colour></division>'
    b'<division name="Bad"><colour slot="accent">purple</colour></division></palettes>'
)
CALENDAR = app_commands.Choice(name="Calendar", value="calendar")
TWENTY_FOUR_HOUR = app_commands.Choice(name="24-hour (14:30)", value="24H")
TWELVE_HOUR = app_commands.Choice(name="12-hour (2:30 PM)", value="12H")
DAY_MONTH_YEAR = app_commands.Choice(name="14 Jun 2026", value="DD_MON_YYYY")


def _template_command(column: str) -> str:
    return f"images template {TEMPLATE_COMMAND_NAMES[column]}"


def _folders() -> list[tuple[str, str]]:
    """Each asset directory command, with the folder the bot ships for it."""
    return sorted(
        (f"images config {command}", packaged)
        for command, _league, packaged in ASSET_DIRECTORIES.values()
    )


def _folder_column(name: str) -> str:
    return next(
        column for column, (command, _league, _packaged) in ASSET_DIRECTORIES.items()
        if f"images config {command}" == name
    )


#: Each acting command carried out: the command, what it is given, how the league stands, and
#: the values its line must carry beneath, each as the forms it may take. A flip names no value
#: of its own here, but still carries one line beneath.
SUCCESSES = (
    [pytest.param(name, (packaged,), {}, [(packaged,)], id=name) for name, packaged in _folders()]
    + [
        pytest.param(
            "images config template-directory", (TEMPLATE_DIRECTORY,), {},
            [(TEMPLATE_DIRECTORY,)], id="images config template-directory",
        )
    ]
    + [
        pytest.param(
            _template_command(column), (TEMPLATE_COLUMNS[column],), {column: "old.svg"},
            [(TEMPLATE_COLUMNS[column],)], id=_template_command(column),
        )
        for column in sorted(TEMPLATE_COLUMNS)
    ]
    + [
        pytest.param(
            "images config toggle", (CALENDAR,), {}, [], id="images config toggle",
        ),
        pytest.param(
            "images config fastest-lap-colour", ("#00ff00",), {}, [("#00FF00",)],
            id="images config fastest-lap-colour",
        ),
        pytest.param(
            "images config per-tier-colour-toggle", (True,), {}, [],
            id="images config per-tier-colour-toggle",
        ),
        pytest.param(
            "images config per-tier-set-colour", ("Division 1", "accent", "#a78bfa"), {},
            [("Division 1",), ("accent",), ("#A78BFA",)],
            id="images config per-tier-set-colour",
        ),
        pytest.param(
            "images config colour-xml-import", (_attached(ONE_TIER),), {}, [("Division 1",)],
            id="images config colour-xml-import",
        ),
        pytest.param(
            "images config time-zone", ("Europe/Lisbon",), {}, [("Europe/Lisbon",)],
            id="images config time-zone",
        ),
        pytest.param(
            "images config time-format", (TWELVE_HOUR,), {}, [("12-hour (2:30 PM)", "12H")],
            id="images config time-format",
        ),
        pytest.param(
            "images config date-format", (DAY_MONTH_YEAR,), {},
            [("14 Jun 2026", "DD_MON_YYYY")],
            id="images config date-format",
        ),
        pytest.param(
            "images use-pfp toggle", (), {"use_pfp": 0}, [], id="images use-pfp toggle",
        ),
        pytest.param(
            "images use-pfp prerender-toggle", (),
            {"use_pfp": 1, "pfp_prerender": 0, "pfp_daily": 1}, [],
            id="images use-pfp prerender-toggle",
        ),
        pytest.param(
            "images use-pfp daily-toggle", (), {"use_pfp": 1, "pfp_prerender": 1, "pfp_daily": 1},
            [], id="images use-pfp daily-toggle (off)",
        ),
    ]
)


def _carries(details: list[str], forms: tuple[str, ...]) -> bool:
    return any(form.casefold() in detail.casefold() for detail in details for form in forms)


def _audited_by_the_member(audits: list[dict[str, Any]]) -> None:
    """At least one audit entry was saved, each an image setting's and the member's."""
    assert audits, "no audit entry was saved"
    for audit in audits:
        assert audit["change_type"].startswith("IMAGE_"), audit
        assert audit["actor_id"] == 42, audit


@pytest.mark.parametrize("name, arguments, config, values", SUCCESSES)
async def test_a_success_records_one_line_naming_its_own_command(
    tmp_path, name, arguments, config, values
):
    """Acknowledged at once; once run, the reply is today's success and the log channel holds
    one line, "Race Control (`<@42>`) | /<command> | Success", with what was set beneath, saved
    with its audit entry."""
    league = await _league(tmp_path, **config)

    asked = await _run_with(league, name, *arguments)

    _acknowledged(asked)
    assert "✅" in _reply(asked) or "disabled" in _reply(asked), asked.said
    details = assert_one_line(league.bot, name)
    assert details, "no value is recorded beneath the line"
    for forms in values:
        assert _carries(details, forms), (forms, details)
    _audited_by_the_member(await audit_rows(league.db_path))


async def test_confirming_daily_portraits_records_one_line_naming_the_command(tmp_path):
    """The Confirm's line names `/images use-pfp daily-toggle`, the command that opened the
    form, with the time set beneath, once the daily job is armed."""
    league = await _league(tmp_path, **PORTRAITS)
    cog = ImageCog(league.bot)
    opener = interaction("images use-pfp daily-toggle", bot=league.bot)
    await undecorate(_command("images use-pfp daily-toggle"))(cog, opener)
    form = opener.response.send_modal.await_args.args[0]
    form.time_of_day._value = "19:00"
    submitted = interaction(None, bot=league.bot)
    await form.on_submit(submitted)
    view = submitted.response.send_message.await_args.kwargs["view"]

    await view.confirm.callback(interaction(None, bot=league.bot))
    await run_queue(league.bot)

    league.scheduler.schedule_portrait_refresh.assert_called_once()
    details = assert_one_line(league.bot, "images use-pfp daily-toggle")
    assert _carries(details, ("19:00",)), details
    _audited_by_the_member(await audit_rows(league.db_path))


async def test_a_pasted_palette_records_one_line_naming_the_command_that_opened_its_form(
    tmp_path,
):
    """The colour form's line names `/images config per-tier-bulk-colour`, each slot beneath,
    saved with an audit entry per colour."""
    league = await _league(tmp_path)

    submitted = await _submit_palette(league, "accent #A78BFA\nink #F7F6F8")

    _acknowledged(submitted)
    details = assert_one_line(league.bot, "images config per-tier-bulk-colour")
    for forms in [("Division 1",), ("accent",), ("#A78BFA",), ("ink",), ("#F7F6F8",)]:
        assert _carries(details, forms), (forms, details)
    assert len(await audit_rows(league.db_path)) == 2


async def test_a_part_applied_import_lists_the_blocks_it_passed_over(tmp_path):
    """A7: acknowledged, then one Success line, the tier stored beneath and "passed over:"
    naming the block that could not be read; the reply names it too."""
    league = await _league(tmp_path)

    asked = await _run_with(league, "images config colour-xml-import", _attached(PART_READABLE))

    _acknowledged(asked)
    assert "Bad" in _reply(asked), asked.said
    details = assert_one_line(league.bot, "images config colour-xml-import")
    assert _carries(details, ("Good",)), details
    passed_over = [index for index, detail in enumerate(details) if detail.startswith("passed over:")]
    assert passed_over, details
    assert "Bad" in "\n".join(details[passed_over[0]:]), details
    assert (await _stored(league.db_path))["image_tier_colour"] == [("good", "accent", "#A78BFA")]


async def test_no_line_names_the_config_group_bare(tmp_path):
    """A8: across every success and every refusal above, no line reads "| /images config |",
    the name every image line carried whatever the command."""
    lines = []
    for index, case in enumerate(SUCCESSES + OWN_REFUSALS):
        name, arguments, config, *_rest = case.values
        (tmp_path / str(index)).mkdir()
        league = await _league(tmp_path / str(index), **config)
        await _run_with(league, name, *arguments)
        lines += logged(league.bot)

    assert lines
    assert not [line for line in lines if "| /images config |" in line], lines


# ── A20 (N1): a value already held changes nothing ────────────────────────

#: Each image setting given the value it holds: the command, what it is given, how the league
#: stands, and the value the reply and the line must carry.
HELD = (
    [
        pytest.param(name, (packaged,), {_folder_column(name): packaged}, [(packaged,)], id=name)
        for name, packaged in _folders()
    ]
    + [
        pytest.param(
            "images config template-directory", (TEMPLATE_DIRECTORY,),
            {"template_directory": TEMPLATE_DIRECTORY}, [(TEMPLATE_DIRECTORY,)],
            id="images config template-directory",
        )
    ]
    + [
        pytest.param(
            _template_command(column), (TEMPLATE_COLUMNS[column],), {},
            [(TEMPLATE_COLUMNS[column],)], id=_template_command(column),
        )
        for column in sorted(TEMPLATE_COLUMNS)
    ]
    + [
        pytest.param(
            "images config fastest-lap-colour", ("#a020f0",), {}, [("#A020F0",)],
            id="images config fastest-lap-colour",
        ),
        pytest.param(
            "images config per-tier-colour-toggle", (True,), {"per_tier_colour_enabled": 1},
            [("on",)], id="images config per-tier-colour-toggle",
        ),
        pytest.param(
            "images config per-tier-set-colour", ("Division 1", "accent", "#a78bfa"),
            {"tier_colours": [("division_1", "accent", "#A78BFA")]}, [("#A78BFA",)],
            id="images config per-tier-set-colour",
        ),
        pytest.param(
            "images config time-zone", ("Europe/Lisbon",), {"time_zone": "Europe/Lisbon"},
            [("Europe/Lisbon",)], id="images config time-zone",
        ),
        pytest.param(
            "images config time-format", (TWENTY_FOUR_HOUR,), {}, [("24-hour (14:30)", "24H")],
            id="images config time-format",
        ),
        pytest.param(
            "images config date-format", (DAY_MONTH_YEAR,), {"date_format": "DD_MON_YYYY"},
            [("14 Jun 2026", "DD_MON_YYYY")], id="images config date-format",
        ),
    ]
)


@pytest.mark.parametrize("name, arguments, config, values", HELD)
async def test_a_value_already_held_changes_nothing(tmp_path, name, arguments, config, values):
    """Acknowledged at once; once run, nothing is written, the reply says "Nothing changed",
    naming the value, one "Nothing changed" line records it, and no audit entry is saved."""
    league = await _league(tmp_path, **config)
    before = await _stored(league.db_path)

    asked = await _run_with(league, name, *arguments)

    _acknowledged(asked)
    reply = _reply(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "is already **" in reply and "✅" not in reply, reply
    assert await _stored(league.db_path) == before
    assert await audit_rows(league.db_path) == []
    details = assert_one_line(league.bot, name, "Nothing changed")
    for forms in values:
        assert _carries([reply], forms), (forms, reply)
        assert _carries(details, forms), (forms, details)


async def test_per_tier_colours_already_on_say_nothing_changed_alone(tmp_path):
    """Turning per-tier colours on when they are already on lists no missing colour, though
    the calendar, switched on, declares colours no tier holds: the owner's decision of
    2026-10-01."""
    league = await _league(tmp_path, per_tier_colour_enabled=1)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE image_aspect_toggles SET enabled = 1 WHERE aspect = 'calendar'")
        await db.commit()

    asked = await _run_with(league, "images config per-tier-colour-toggle", True)

    _acknowledged(asked)
    reply = _reply(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "calendar_template" not in reply and "⚠️" not in reply, reply
    assert_one_line(league.bot, "images config per-tier-colour-toggle", "Nothing changed")


async def test_a_pasted_palette_already_held_changes_nothing(tmp_path):
    """Every colour in the pasted palette is already the tier's: once run, the form's reply
    says so, the line names the command that opened it, and no audit entry is saved."""
    league = await _league(tmp_path, tier_colours=[("division_1", "accent", "#A78BFA")])

    submitted = await _submit_palette(league, "accent #A78BFA")

    _acknowledged(submitted)
    assert _reply(submitted).startswith("ℹ️ Nothing changed: "), submitted.said
    assert_one_line(league.bot, "images config per-tier-bulk-colour", "Nothing changed")
    assert await audit_rows(league.db_path) == []


@pytest.mark.parametrize(
    "document, passed_over",
    [
        pytest.param(ONE_TIER, None, id="every-block-held"),
        pytest.param(PART_READABLE, "Bad", id="held-with-a-block-passed-over"),
    ],
)
async def test_an_import_already_held_changes_nothing(tmp_path, document, passed_over):
    """Every block the import could read is already held. Once run, the reply says "Nothing
    changed" and still lists each block it passed over, with its reason; one "Nothing changed"
    line, and no audit entry."""
    league = await _league(
        tmp_path,
        tier_colours=[("division_1", "accent", "#A78BFA"), ("good", "accent", "#A78BFA")],
    )

    asked = await _run_with(league, "images config colour-xml-import", _attached(document))

    _acknowledged(asked)
    reply = _reply(asked)
    assert "ℹ️ Nothing changed" in reply and "✅" not in reply, reply
    if passed_over:
        assert passed_over in reply, reply
    assert_one_line(league.bot, "images config colour-xml-import", "Nothing changed")
    assert await audit_rows(league.db_path) == []
