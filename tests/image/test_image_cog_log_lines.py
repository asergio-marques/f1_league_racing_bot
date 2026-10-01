"""What the image module's commands write to the league's log channel.

Every command that changes something, or tries to, records every outcome there, whoever used it
(the core specification's "The record of what changed"; #482): a refusal as one "⛔" line naming
the command, or the form that refused it. A command that changes nothing — `/images config
view` and the twelve previews — records no outcome of its own.

Each command is reached past its permission guard, against the real cog, with the shared doubles
of `tests/support/image_cog_doubles.py`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands

from leaguebot.core.utils.messages import chunk_message
from leaguebot.image.cogs.image_cog import ImageCog
from leaguebot.image.services.image_validity_service import Problem
from tests.support.image_cog_doubles import (
    assert_one_refusal,
    interaction,
    log_bot,
    logged,
    said,
)
from tests.support.undecorate import undecorate

_GATE_NOT_RECORDED = "#482: the image module gate does not yet record a refusal"
_OWN_SPLITTER = "#482: /images config view still splits its report with the cog's own splitter"
_REPLY_CUT = "#482: a refused folder's reply is still cut at 1,900 characters"


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


@pytest.mark.xfail(strict=True, reason=_GATE_NOT_RECORDED)
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


@pytest.mark.xfail(strict=True, reason=_OWN_SPLITTER)
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


@pytest.mark.xfail(strict=True, reason=_REPLY_CUT)
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
