"""The `/attendance` group, and what the subgroups beneath it share.

`ATTENDANCE_GROUP` is the one `/attendance` group; each subgroup's class names it as its parent,
so the subgroups sit under one group whichever file declares them. What else is here is what two
or more of them use: the wait for the catch-up after a restart, the two lines the log channel is
written, and the stub of what a subgroup takes from the cog it is composed into.
"""
from __future__ import annotations

import logging
from abc import abstractmethod

import discord
from discord import app_commands

from leaguebot.core.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import interaction_member

ATTENDANCE_GROUP = app_commands.Group(
    name="attendance",
    description="Attendance module commands.",
    default_permissions=None,
)

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
AFTER_THE_CATCH_UP = after_the_catch_up(lambda cog: cog.bot.change_queue)

log = logging.getLogger(__name__)


class AttendanceSubgroup:
    """What an `/attendance` subgroup's class takes from the `AttendanceCog` it is composed into.

    A subgroup's commands read the bot and call the cog's gate, which `AttendanceCog` holds, so
    the class declares them here for the type checker. Stubs, never the gate itself: a `Protocol`
    cannot be a base beside `commands.Cog`, whose metaclass it conflicts with, and typing `self`
    in every command would put a parameter annotation on each. `AttendanceCog` comes first in the
    order methods are found in, so what it defines is what runs.
    """

    bot: LeagueBot

    @abstractmethod
    async def _module_gate(self, interaction: discord.Interaction, *, record: bool = True) -> bool:
        """Whether the attendance module is on; where it is not, refuse and return False."""


def _command(interaction: discord.Interaction) -> str:
    """The slash command *interaction* ran, as a success line names it: "/attendance sync"."""
    return describe(interaction).strip("`")


def _line(
    interaction: discord.Interaction, what: str, outcome: str, details: tuple[str, ...]
) -> str:
    return (
        f"{interaction_member(interaction)} | {what} | {outcome}"
        + "".join(f"\n  {detail}" for detail in details)
    )


async def record(
    bot: LeagueBot,
    interaction: discord.Interaction,
    what: str,
    outcome: str,
    *details: str,
) -> None:
    """Write one line in the success form, "Name (<@id>) | *what* | *outcome*", *details* beneath.

    For a success and for nothing changed, where the line is not saved with what it records (the
    settings' lines are, by their change). The post is bare: a line reporting that nothing went
    wrong is no place for a catch-all, so a post that raises reaches the command's error
    handling. A press that failed without raising has `record_failed`, and a refusal, a cancel,
    a lapse and a fault have lines of their own (`core/utils/log_lines.py`, `report_failure`).
    """
    await bot.output_router.post_log(_line(interaction, what, outcome, details))


async def record_failed(
    bot: LeagueBot, interaction: discord.Interaction, what: str, *details: str
) -> None:
    """Write the "Failed" line of *what*, as `/attendance post-check-in`'s does.

    It reports a failure, and, as `report_failure` does, it must not raise where the member has
    been answered: a log channel that is down is the host's log's to hear of, and nothing else's.
    """
    try:
        await bot.output_router.post_log(_line(interaction, what, "Failed", details))
    except Exception as exc:  # noqa: BLE001 — the report itself might fail; the member is answered
        log.error(
            "could not record in the log channel that %s failed: %s", what, exc, exc_info=True
        )
