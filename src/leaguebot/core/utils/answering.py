"""Which interaction the code running now is answering, so that a failure can reach its member.

A command, a button press or a form submission is a member's request, and the line the bot
writes to the log channel about it records that member. Where the log channel cannot take the
line, the member is told, seen by them alone, through their own interaction. The code that
writes the line rarely has the interaction (181 callers write lines and none of them is handed
it), so the interaction is found here instead of being passed down through every one of them.

**`league_server.admits` records it.** Every command, view and form passes through `admits`
before its body runs, and discord.py runs that check and the body in one task, so what `admits`
records in this task's context is what every line the body writes finds. A task the body starts
inherits it, which is wanted while the interaction's token lives. A scheduled job, the retry
loop, a recovery at start-up and a Discord event run in no interaction's task, and find none:
there the failure goes to the host's log alone.

This is a leaf: it imports nothing of the bot, so that the router and `league_server` can both
use it.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

import discord


@dataclass
class Answering:
    """The interaction a task is answering, and whether its member has been warned already.

    *task* is the task that runs the command, which is what ends when the command is over: a
    warning for an interaction not yet answered waits for it, since sending first would take
    the command's one response. *warned* is set once the member has been told, so that
    however many of their lines fail they are told once.
    """

    interaction: discord.Interaction
    task: asyncio.Task[object] | None
    warned: bool = False


_answering: ContextVar[Answering | None] = ContextVar("answering", default=None)


def answer_for(interaction: discord.Interaction) -> None:
    """Record that the current task is answering *interaction*."""
    _answering.set(Answering(interaction, asyncio.current_task()))


def answering_now() -> Answering | None:
    """What the current task is answering, or None where it answers no interaction."""
    return _answering.get()
