"""The hook a round made final calls, as the builder makes it, and one that records what it is
handed, for a test that closes rounds as FINAL on a real database.

Core's two closers that make a round FINAL (results turned off, and a season cancelled) hand the
rounds they made FINAL to the hook core declares for a round's results made final
(architecture.md, "How modules and core fit together"): `rounds_made_final`, on the save's
connection. Results signs up its part, which marks the drivers who raced each round as former
drivers. A test that closes rounds hands the hook made here by the builder's own
`_sign_up_to_rounds_made_final`, so that results' part is the bot's and not a copy, or one that
records what it is handed and does nothing else.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def rounds_made_final() -> Any:
    """The `rounds_made_final` hook, as the builder makes it and signs results up, to hand a
    closer."""
    from leaguebot.__main__ import _sign_up_to_rounds_made_final

    bot = SimpleNamespace()
    _sign_up_to_rounds_made_final(bot)
    return bot.rounds_made_final


def recorder() -> tuple[Any, list[tuple[Any, ...]]]:
    """A subscriber that records what each call hands it and does nothing else, and the list it
    records into: one tuple of the call's arguments per call."""
    handed: list[tuple[Any, ...]] = []

    async def record(*args: Any) -> None:
        handed.append(args)

    return record, handed


def recording_hook() -> tuple[Any, list[tuple[Any, ...]]]:
    """A hook whose one subscriber is a `recorder`, and the list it records into."""
    from leaguebot.core.utils.hooks import AwaitedHook

    record, handed = recorder()
    hook = AwaitedHook()
    hook.subscribe(record)
    return hook, handed
