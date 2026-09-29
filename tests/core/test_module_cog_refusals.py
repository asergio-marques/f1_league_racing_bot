"""`/module disable` of a module already disabled is recorded in the log channel (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case asks to disable a module that is
already off, and expects today's reply, seen by the league admin alone, and exactly one standard
refusal line. `/module enable`'s refusals are already recorded, and the confirmation to disable
results is tested in `test_results_disable_cascade.py`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.module_cog import ModuleCog

ADMIN_ID = 4242


def _cog() -> ModuleCog:
    """A module cog whose every module reads as disabled."""
    cog = ModuleCog.__new__(ModuleCog)
    bot = MagicMock()
    for module in ("weather", "results", "images", "attendance", "signup"):
        setattr(bot.module_service, f"is_{module}_enabled", AsyncMock(return_value=False))
    bot.output_router.post_log = AsyncMock()
    cog.bot = bot
    return cog


def _interaction(cog: ModuleCog) -> MagicMock:
    """`/module disable` run by the league admin Alex, answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    interaction.command.qualified_name = "module disable"
    interaction.user.id = ADMIN_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize(
    ("module", "reason"),
    [
        ("weather", "Weather module is already disabled."),
        ("results", "Results & Standings module is already disabled."),
        ("images", "Image module is already disabled."),
        ("attendance", "Attendance module is already disabled."),
        ("signup", "Signup module is already disabled."),
    ],
    ids=["weather", "results", "images", "attendance", "signup"],
)
async def test_disabling_a_module_already_disabled_is_recorded(module, reason):
    """The admin is told the module is already disabled, and the refusal is recorded (#482)."""
    cog = _cog()
    interaction = _interaction(cog)

    await getattr(cog, f"_disable_{module}")(interaction)

    interaction.response.send_message.assert_awaited_once_with(f"⚠️ {reason}", ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    cog.bot.output_router.post_log.assert_awaited_once_with(
        f"⛔ `/module disable` refused for Alex (<@{ADMIN_ID}>) — {reason}"
    )
