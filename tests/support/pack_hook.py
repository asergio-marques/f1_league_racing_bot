"""The pack's hook as the builder makes it, for a test that packs the bot on a real database.

`/bot pack` asks each module to clear its own rows tied to the server it leaves, through the hook
core declares for the bot packed and the builder signs each module up to (architecture.md, "How
modules and core fit together"): `bot_packed`. A test that packs hands `pack` the hook made here by
the builder's own `_sign_up_to_pack`, so that the modules' parts are the bot's and not a copy.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def pack_hook() -> Any:
    """The `bot_packed` hook, as the builder makes it and signs each module up, to hand a pack."""
    from leaguebot.__main__ import _sign_up_to_pack

    bot = SimpleNamespace()
    _sign_up_to_pack(bot)
    return bot.bot_packed
