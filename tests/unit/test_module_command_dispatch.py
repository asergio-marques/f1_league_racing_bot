"""`/module enable` and `/module disable` send each module to its own handler.

Issue #208. The two command bodies were uncovered — every module's handler has its own tests,
reached directly, so nothing checked that the command in front of them routes a choice to the
right one.

**The routing is the whole of the command, and a wrong branch is silent.** Enabling
"attendance" through the weather handler would report the weather module enabled and leave
attendance off. Each of the five choices is sent through both commands and asserted to reach
its own handler and no other.

**Signup is the fallthrough.** It is the `else` of both chains, so a sixth module added to the
choices and not to the dispatch would be treated as signup — the parametrisation below reads the
choice list itself, so such a module fails here rather than silently enabling signup.

**The attendance disable takes no server id.** It reads it from the interaction, unlike the
other four; pinned so a tidy-up that passes one does not break it.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs import module_cog  # noqa: E402
from cogs.module_cog import ModuleCog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 13808
MODULES = sorted(choice.value for choice in module_cog._MODULE_CHOICES)
HANDLED = {"weather", "results", "attendance", "images", "signup"}


def _cog():
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = MagicMock()
    for verb in ("enable", "disable"):
        for module in HANDLED:
            setattr(cog, f"_{verb}_{module}", AsyncMock())
    # The stage gate in front of the dispatch has tests of its own.
    cog._refuse_module_change = AsyncMock(return_value=False)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    return interaction


def _choice(value):
    return SimpleNamespace(name=value.title(), value=value)


def test_every_offered_module_has_a_handler():
    """A module added to the choices and not to the dispatch would fall through to
    signup."""
    assert set(MODULES) == HANDLED


@pytest.mark.parametrize("module", MODULES)
async def test_enable_reaches_the_modules_own_handler(module):
    cog = _cog()
    interaction = _interaction()

    await undecorate(ModuleCog.enable)(cog, interaction, _choice(module))

    getattr(cog, f"_enable_{module}").assert_awaited_once_with(interaction, SERVER_ID)
    for other in HANDLED - {module}:
        getattr(cog, f"_enable_{other}").assert_not_awaited()


@pytest.mark.parametrize("module", MODULES)
async def test_disable_reaches_the_modules_own_handler(module):
    cog = _cog()
    interaction = _interaction()

    await undecorate(ModuleCog.disable)(cog, interaction, _choice(module))

    handler = getattr(cog, f"_disable_{module}")
    handler.assert_awaited_once()
    assert handler.await_args.args[0] is interaction
    for other in HANDLED - {module}:
        getattr(cog, f"_disable_{other}").assert_not_awaited()


async def test_the_attendance_disable_takes_no_server_id():
    cog = _cog()
    interaction = _interaction()

    await undecorate(ModuleCog.disable)(cog, interaction, _choice("attendance"))

    assert cog._disable_attendance.await_args.args == (interaction,)
