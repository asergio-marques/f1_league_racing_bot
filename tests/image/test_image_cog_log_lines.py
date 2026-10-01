"""What the image module's commands write to the league's log channel.

Every command that changes something, or tries to, records every outcome there, whoever used it
(the core specification's "The record of what changed"; #482): a refusal as one "⛔" line naming
the command, or the form that refused it. A command that changes nothing — `/images config
view` and the twelve previews — records no outcome of its own.

Each command is reached past its permission guard, against the real cog, with the shared doubles
of `tests/support/image_cog_doubles.py`.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.utils.messages import chunk_message
import leaguebot.core.utils.paths as paths
import leaguebot.image.cogs.image_cog as cog_module
import leaguebot.image.services.image_validity_service as validity
from leaguebot.image.cogs.image_cog import ImageCog, TierPaletteModal
from leaguebot.image.models.image_constants import ASSET_DIRECTORIES
from leaguebot.image.services.image_validity_service import Problem
from tests.support.image_cog_doubles import (
    assert_one_line,
    assert_one_refusal,
    interaction,
    log_bot,
    logged,
    said,
    scheduler,
)
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


def _module_off_cog() -> ImageCog:
    bot = log_bot()
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    return ImageCog(bot)


async def _run(cog: ImageCog, name: str):
    """Run `/<name>` through *cog*, every option given a value of its kind."""
    command = _command(name)
    asked = interaction(name, bot=cog.bot)
    arguments = [_argument(parameter) for parameter in command.parameters]
    await undecorate(command)(cog, asked, *arguments)
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
async def test_an_acting_command_refused_by_the_module_gate_records_one_line(name):
    cog = _module_off_cog()

    asked = await _run(cog, name)

    assert "The Image module is not enabled" in said(asked)
    assert_one_refusal(cog.bot, f"`/{name}`")


@pytest.mark.parametrize("name", LOOKING)
async def test_a_command_that_changes_nothing_records_nothing_when_refused(name):
    cog = _module_off_cog()

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


async def test_a_long_refusal_of_a_folder_is_sent_whole_in_parts():
    cog = ImageCog(log_bot())
    asked = interaction("images config template-directory", bot=cog.bot)
    problems = [
        Problem(
            kind="NOT_FOUND",
            detail=f"fault {index}: " + "y" * 380,
            template_key=f"t{index}_template",
        )
        for index in range(6)
    ]

    await cog._reject_directory(
        asked,
        "Template directory",
        "`resources/mine` does not hold every template the bot needs.",
        problems=problems,
        searched="resources/mine",
    )

    reply = said(asked)
    assert all(len(part) <= 2000 for part in asked.said)
    assert len(asked.said) > 1
    assert "fault 5: " in reply
    assert "Searched: `resources/mine`" in reply


# ── A5: each command's own refusal ────────────────────────────────────────

_REFUSAL_NOT_RECORDED = "#482: an image command's own refusal is not yet recorded as a refusal"

#: Where the cog stores a folder it was given, whatever the host's own path.
FOLDER = "resources/league/mine"
#: The colour form a pasted palette arrives through, as its refusals name it.
BULK_FORM = "the “Set one tier's colours” form"


def _working_cog(monkeypatch, tmp_path, *, changed=True, **config) -> ImageCog:
    """The real cog with the module on, every drawing and folder sound, and a store whose
    setters report each value as a change (or, where *changed* is false, as already held).

    *config* overrides the stored configuration: portraits obtained from Discord, updated
    before each drawing and not daily, per-tier colours on.
    """
    bot = log_bot()
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    store = bot.image_config_service
    store.set_field = AsyncMock(return_value=changed)
    store.set_flag = AsyncMock(return_value=changed)
    store.set_tier_colour = AsyncMock(return_value=changed)
    store.set_tier_colours = AsyncMock(
        side_effect=lambda division, colours: len(colours) if changed else 0
    )
    store.set_pfp_flag = AsyncMock()
    store.set_aspect = AsyncMock()
    store.is_aspect_enabled = AsyncMock(return_value=False)
    store.get_toggles = AsyncMock(return_value={})
    store.candidate_config = AsyncMock(return_value=SimpleNamespace())
    values = {
        "use_pfp": True,
        "pfp_prerender": True,
        "pfp_daily": False,
        "pfp_daily_time": "03:00",
        "per_tier_colour_enabled": True,
    }
    values.update(config)
    store.get_config = AsyncMock(return_value=SimpleNamespace(**values))
    bot.image_validity_service.colour_shortfall = AsyncMock(return_value={})
    bot.scheduler_service = scheduler()

    cog = ImageCog(bot)
    cog._declared_colour_slots = AsyncMock(return_value={"accent", "ink"})
    cog._aspect_blocking_reasons_if_enabled = AsyncMock(return_value=[])
    cog._measure_fastest_lap_contrast = AsyncMock()
    monkeypatch.setattr(cog_module, "fastest_lap_contrast_lines", lambda reading: [])
    monkeypatch.setattr(paths, "resolve_within_project_root", lambda value, root=None: tmp_path)
    monkeypatch.setattr(cog_module, "relative_to_root", lambda resolved: FOLDER)
    monkeypatch.setattr(validity, "check_template", lambda proposed, column: None)
    monkeypatch.setattr(validity, "blocking_template_problems", lambda proposed, toggles: [])
    monkeypatch.setattr(cog_module, "is_known_zone", lambda zone: zone == "Europe/Lisbon")
    return cog


async def _run_with(cog: ImageCog, name: str, *arguments):
    """Run `/<name>` through *cog* with *arguments*, past its permission guard."""
    asked = interaction(name, bot=cog.bot)
    await undecorate(_command(name))(cog, asked, *arguments)
    return asked


def _attached(raw: bytes) -> MagicMock:
    """A file attached to `/images config colour-xml-import`, holding *raw*."""
    attachment = MagicMock()
    attachment.size = len(raw)
    attachment.read = AsyncMock(return_value=raw)
    return attachment


def _empty_folder(monkeypatch):
    def _refuse(value, root=None):
        raise ValueError("Directory cannot be empty.")

    monkeypatch.setattr(paths, "resolve_within_project_root", _refuse)


def _broken_drawing(monkeypatch):
    broken = MagicMock()
    broken.message.return_value = "the calendar drawing marks no title."
    monkeypatch.setattr(validity, "check_template", lambda proposed, column: broken)


def _no_configuration(cog):
    cog.bot.image_config_service.get_config = AsyncMock(return_value=None)


def _nothing_to_amend(cog):
    cog.bot.image_config_service.candidate_config = AsyncMock(return_value=None)


#: Each refusal of plan section 2 not pinned in a module's own test file: the command, what
#: it is given, how the server stands, and a phrase of the reply it gives today.
OWN_REFUSALS = [
    pytest.param(
        "images use-pfp prerender-toggle", (), {"use_pfp": False}, None,
        "Driver portraits are not being obtained from Discord",
        id="prerender-toggle-portraits-off",
    ),
    pytest.param(
        "images use-pfp daily-toggle", (), {"use_pfp": False}, None,
        "Driver portraits are not being obtained from Discord",
        id="daily-toggle-portraits-off",
    ),
    pytest.param(
        "images use-pfp prerender-toggle", (), {"pfp_prerender": True, "pfp_daily": False}, None,
        "Cannot disable pre-render updates",
        id="prerender-toggle-last-trigger",
    ),
    pytest.param(
        "images use-pfp toggle", (), {}, _no_configuration,
        "The image module has no configuration yet.",
        id="use-pfp-toggle-no-configuration",
    ),
    pytest.param(
        "images config template-directory", ("",), {}, "empty folder",
        "Directory cannot be empty.",
        id="template-directory-empty",
    ),
    pytest.param(
        "images config template-directory", (FOLDER,), {}, _nothing_to_amend,
        "this server has no image configuration to amend.",
        id="template-directory-no-configuration",
    ),
    pytest.param(
        "images config track-image-directory", ("",), {}, "empty folder",
        "Directory cannot be empty.",
        id="asset-directory-empty",
    ),
    pytest.param(
        "images template calendar", ("drawings/calendar.svg",), {}, None,
        "not a path",
        id="template-given-a-path",
    ),
    pytest.param(
        "images template calendar", ("calendar.svg",), {}, _nothing_to_amend,
        "this server has no image configuration to amend.",
        id="template-no-configuration",
    ),
    pytest.param(
        "images template calendar", ("calendar.svg",), {}, "broken drawing",
        "the calendar drawing marks no title.",
        id="template-invalid",
    ),
    pytest.param(
        "images config fastest-lap-colour", ("purple",), {}, None,
        "The stored colour is unchanged.",
        id="fastest-lap-colour-invalid",
    ),
    pytest.param(
        "images config per-tier-set-colour", ("Division 1", "not a slot!", "#A78BFA"), {}, None,
        "Nothing was stored.",
        id="per-tier-set-colour-invalid-slot",
    ),
    pytest.param(
        "images config per-tier-set-colour", ("Division 1", "accent", "purple"), {}, None,
        "Nothing was stored.",
        id="per-tier-set-colour-invalid-colour",
    ),
    pytest.param(
        "images config colour-xml-import", (_attached(b""),), {}, None,
        "The attached file is empty.",
        id="colour-xml-import-empty",
    ),
    pytest.param(
        "images config colour-xml-import", (_attached(b"\xff\xfe<palettes>"),), {}, None,
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
        {}, None,
        "Bad",
        id="colour-xml-import-every-block-rejected",
    ),
    pytest.param(
        "images config time-zone", ("Mars/Olympus",), {}, None,
        "is not a recognised time zone",
        id="time-zone-unknown",
    ),
]

_SETUPS = {"empty folder": _empty_folder, "broken drawing": _broken_drawing}


@pytest.mark.xfail(strict=True, reason=_REFUSAL_NOT_RECORDED)
@pytest.mark.parametrize("name, arguments, config, setup, reply", OWN_REFUSALS)
async def test_a_command_s_own_refusal_records_one_line_naming_it(
    monkeypatch, tmp_path, name, arguments, config, setup, reply
):
    """The reply is the one given today; the log channel gains one "⛔" line naming the
    command, and nothing is stored."""
    cog = _working_cog(monkeypatch, tmp_path, **config)
    if isinstance(setup, str):
        _SETUPS[setup](monkeypatch)
    elif setup is not None:
        setup(cog)

    asked = await _run_with(cog, name, *arguments)

    assert reply in said(asked)
    store = cog.bot.image_config_service
    for setter in ("set_field", "set_flag", "set_tier_colour", "set_tier_colours", "set_pfp_flag"):
        getattr(store, setter).assert_not_awaited()
    assert_one_refusal(cog.bot, f"`/{name}`")


async def test_a_pasted_palette_with_an_unreadable_line_is_refused_by_the_form(
    monkeypatch, tmp_path
):
    """The colour form refuses the whole palette, as today, and the line names the form."""
    cog = _working_cog(monkeypatch, tmp_path)
    form = TierPaletteModal(cog, "Division 1")
    form.block._value = "accent #A78BFA\nink not-a-colour"
    submitted = interaction(None, bot=cog.bot)

    await form.on_submit(submitted)

    assert "could not be read" in said(submitted)
    cog.bot.image_config_service.set_tier_colours.assert_not_awaited()
    assert_one_refusal(cog.bot, BULK_FORM)


# ── A6, A7, A8: each success is recorded under its own command ────────────

_SUCCESS_UNNAMED = "#482: an image success line does not yet name its own command"

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


def _templates() -> list[str]:
    return [name for name in ACTING if name.startswith("images template ")]


def _folders() -> list[str]:
    return [f"images config {command}" for command, _league, _packaged in ASSET_DIRECTORIES.values()]


#: Each acting command carried out: the command, what it is given, how the server stands,
#: and the values its line must carry beneath, each as the forms it may take. A flip names
#: no value of its own here, but still carries one line beneath.
SUCCESSES = (
    [pytest.param(name, (FOLDER,), {}, [(FOLDER,)], id=name) for name in _folders()]
    + [
        pytest.param(
            "images config template-directory", (FOLDER,), {}, [(FOLDER,)],
            id="images config template-directory",
        )
    ]
    + [pytest.param(name, ("mine.svg",), {}, [("mine.svg",)], id=name) for name in _templates()]
    + [
        pytest.param(
            "images config toggle", (app_commands.Choice(name="Calendar", value="calendar"),),
            {}, [], id="images config toggle",
        ),
        pytest.param(
            "images config fastest-lap-colour", ("#a020f0",), {}, [("#A020F0",)],
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
            "images config time-format",
            (app_commands.Choice(name="24-hour (14:30)", value="24H"),), {},
            [("24-hour (14:30)", "24H")],
            id="images config time-format",
        ),
        pytest.param(
            "images config date-format",
            (app_commands.Choice(name="14 Jun 2026", value="DD_MON_YYYY"),), {},
            [("14 Jun 2026", "DD_MON_YYYY")],
            id="images config date-format",
        ),
        pytest.param(
            "images use-pfp toggle", (), {"use_pfp": False}, [], id="images use-pfp toggle",
        ),
        pytest.param(
            "images use-pfp prerender-toggle", (), {"pfp_prerender": False, "pfp_daily": True},
            [], id="images use-pfp prerender-toggle",
        ),
        pytest.param(
            "images use-pfp daily-toggle", (), {"pfp_daily": True}, [],
            id="images use-pfp daily-toggle (off)",
        ),
    ]
)


def _carries(details: list[str], forms: tuple[str, ...]) -> bool:
    return any(form.casefold() in detail.casefold() for detail in details for form in forms)


@pytest.mark.xfail(strict=True, reason=_SUCCESS_UNNAMED)
@pytest.mark.parametrize("name, arguments, config, values", SUCCESSES)
async def test_a_success_records_one_line_naming_its_own_command(
    monkeypatch, tmp_path, name, arguments, config, values
):
    """"Race Control (<@42>) | /<command> | Success", with what was set beneath."""
    cog = _working_cog(monkeypatch, tmp_path, **config)

    asked = await _run_with(cog, name, *arguments)

    assert "✅" in said(asked) or "disabled" in said(asked)
    details = assert_one_line(cog.bot, name)
    assert details, "no value is recorded beneath the line"
    for forms in values:
        assert _carries(details, forms), (forms, details)


@pytest.mark.xfail(strict=True, reason=_SUCCESS_UNNAMED)
async def test_confirming_daily_portraits_records_one_line_naming_the_command(
    monkeypatch, tmp_path
):
    """The Confirm's line names `/images use-pfp daily-toggle`, the command that opened the
    form, with the time set beneath."""
    cog = _working_cog(monkeypatch, tmp_path)
    opener = interaction("images use-pfp daily-toggle", bot=cog.bot)
    await undecorate(_command("images use-pfp daily-toggle"))(cog, opener)
    form = opener.response.send_modal.await_args.args[0]
    form.time_of_day._value = "19:00"
    submitted = interaction(None, bot=cog.bot)
    await form.on_submit(submitted)
    view = submitted.response.send_message.await_args.kwargs["view"]

    await view.confirm.callback(interaction(None, bot=cog.bot))

    details = assert_one_line(cog.bot, "images use-pfp daily-toggle")
    assert _carries(details, ("19:00",)), details


async def test_a_pasted_palette_records_one_line_naming_the_command_that_opened_its_form(
    monkeypatch, tmp_path
):
    """The colour form's line names `/images config per-tier-bulk-colour`, each slot beneath."""
    cog = _working_cog(monkeypatch, tmp_path)
    form = TierPaletteModal(cog, "Division 1")
    form.block._value = "accent #A78BFA\nink #F7F6F8"

    await form.on_submit(interaction(None, bot=cog.bot))

    details = assert_one_line(cog.bot, "images config per-tier-bulk-colour")
    for forms in [("Division 1",), ("accent",), ("#A78BFA",), ("ink",), ("#F7F6F8",)]:
        assert _carries(details, forms), (forms, details)


async def test_a_part_applied_import_lists_the_blocks_it_passed_over(monkeypatch, tmp_path):
    """A7: one Success line, the tier stored beneath and "passed over:" naming the block that
    could not be read."""
    cog = _working_cog(monkeypatch, tmp_path)

    asked = await _run_with(cog, "images config colour-xml-import", _attached(PART_READABLE))

    assert "Bad" in said(asked)
    details = assert_one_line(cog.bot, "images config colour-xml-import")
    assert _carries(details, ("Good",)), details
    passed_over = [index for index, detail in enumerate(details) if detail.startswith("passed over:")]
    assert passed_over, details
    assert "Bad" in "\n".join(details[passed_over[0]:]), details


@pytest.mark.xfail(strict=True, reason="#482: image lines still read \"| /images config |\" bare")
async def test_no_line_names_the_config_group_bare(monkeypatch, tmp_path):
    """A8: across every success and every refusal above, no line reads "| /images config |",
    the name every image line carried whatever the command."""
    lines = []
    for case in SUCCESSES + OWN_REFUSALS:
        with monkeypatch.context() as patched:
            name, arguments, config, *rest = case.values
            cog = _working_cog(patched, tmp_path, **config)
            setup = rest[0] if len(rest) == 2 else None
            if isinstance(setup, str):
                _SETUPS[setup](patched)
            elif setup is not None:
                setup(cog)
            await _run_with(cog, name, *arguments)
            lines += logged(cog.bot)

    assert lines
    assert not [line for line in lines if "| /images config |" in line], lines


# ── A20 (N1): a value already held changes nothing ────────────────────────

_HELD_AS_CHANGE = "#482: a value already held is still answered and recorded as a change"

#: Each image setting given the value it holds: the command, what it is given, the setter
#: that reports the value held, and the value the reply and the line must carry.
HELD = (
    [pytest.param(name, (FOLDER,), "set_field", [(FOLDER,)], id=name) for name in _folders()]
    + [
        pytest.param(
            "images config template-directory", (FOLDER,), "set_field", [(FOLDER,)],
            id="images config template-directory",
        )
    ]
    + [
        pytest.param(name, ("mine.svg",), "set_field", [("mine.svg",)], id=name)
        for name in _templates()
    ]
    + [
        pytest.param(
            "images config fastest-lap-colour", ("#a020f0",), "set_field", [("#A020F0",)],
            id="images config fastest-lap-colour",
        ),
        pytest.param(
            "images config per-tier-colour-toggle", (True,), "set_flag", [("on",)],
            id="images config per-tier-colour-toggle",
        ),
        pytest.param(
            "images config per-tier-set-colour", ("Division 1", "accent", "#a78bfa"),
            "set_tier_colour", [("#A78BFA",)],
            id="images config per-tier-set-colour",
        ),
        pytest.param(
            "images config time-zone", ("Europe/Lisbon",), "set_field", [("Europe/Lisbon",)],
            id="images config time-zone",
        ),
        pytest.param(
            "images config time-format",
            (app_commands.Choice(name="24-hour (14:30)", value="24H"),), "set_field",
            [("24-hour (14:30)", "24H")],
            id="images config time-format",
        ),
        pytest.param(
            "images config date-format",
            (app_commands.Choice(name="14 Jun 2026", value="DD_MON_YYYY"),), "set_field",
            [("14 Jun 2026", "DD_MON_YYYY")],
            id="images config date-format",
        ),
    ]
)


@pytest.mark.xfail(strict=True, reason=_HELD_AS_CHANGE)
@pytest.mark.parametrize("name, arguments, setter, values", HELD)
async def test_a_value_already_held_changes_nothing(
    monkeypatch, tmp_path, name, arguments, setter, values
):
    """The store reports the value held and writes nothing; the reply says "Nothing changed",
    naming the value, and one "Nothing changed" line records it."""
    cog = _working_cog(monkeypatch, tmp_path, changed=False)

    asked = await _run_with(cog, name, *arguments)

    reply = said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "is already **" in reply and "✅" not in reply, reply
    getattr(cog.bot.image_config_service, setter).assert_awaited_once()
    details = assert_one_line(cog.bot, name, "Nothing changed")
    for forms in values:
        assert _carries([reply], forms), (forms, reply)
        assert _carries(details, forms), (forms, details)


@pytest.mark.xfail(strict=True, reason=_HELD_AS_CHANGE)
async def test_per_tier_colours_already_on_say_nothing_changed_alone(monkeypatch, tmp_path):
    """Turning per-tier colours on when they are already on lists no missing colour, though
    some are missing: the owner's decision of 2026-10-01."""
    cog = _working_cog(monkeypatch, tmp_path, changed=False)
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(
        return_value={"calendar_template": ["`accent` is not set for **Division 1**"]}
    )

    asked = await _run_with(cog, "images config per-tier-colour-toggle", True)

    reply = said(asked)
    assert reply.startswith("ℹ️ Nothing changed: "), reply
    assert "calendar_template" not in reply and "⚠️" not in reply, reply
    assert_one_line(cog.bot, "images config per-tier-colour-toggle", "Nothing changed")


@pytest.mark.xfail(strict=True, reason=_HELD_AS_CHANGE)
async def test_a_pasted_palette_already_held_changes_nothing(monkeypatch, tmp_path):
    """Every colour in the pasted palette is already the tier's: the form says so, and the
    line names the command that opened it."""
    cog = _working_cog(monkeypatch, tmp_path, changed=False)
    form = TierPaletteModal(cog, "Division 1")
    form.block._value = "accent #A78BFA"
    submitted = interaction(None, bot=cog.bot)

    await form.on_submit(submitted)

    assert said(submitted).startswith("ℹ️ Nothing changed: "), said(submitted)
    assert_one_line(cog.bot, "images config per-tier-bulk-colour", "Nothing changed")


@pytest.mark.xfail(strict=True, reason=_HELD_AS_CHANGE)
@pytest.mark.parametrize(
    "document, passed_over",
    [
        pytest.param(ONE_TIER, None, id="every-block-held"),
        pytest.param(PART_READABLE, "Bad", id="held-with-a-block-passed-over"),
    ],
)
async def test_an_import_already_held_changes_nothing(monkeypatch, tmp_path, document, passed_over):
    """Every block the import could read is already held. The reply says "Nothing changed"
    and still lists each block it passed over, with its reason; one "Nothing changed" line."""
    cog = _working_cog(monkeypatch, tmp_path, changed=False)

    asked = await _run_with(cog, "images config colour-xml-import", _attached(document))

    reply = said(asked)
    assert "ℹ️ Nothing changed" in reply and "✅" not in reply, reply
    if passed_over:
        assert passed_over in reply, reply
    assert_one_line(cog.bot, "images config colour-xml-import", "Nothing changed")
