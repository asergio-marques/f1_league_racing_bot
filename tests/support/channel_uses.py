"""The hooks core asks each module through for the channels its settings hold (#283).

A channel does one job, server-wide, so the registry asks every module which of its settings
holds a channel (`channel_uses_read`). Each module's channel columns are its own (architecture.md,
"Each table is read and written by its owner alone"), so the registry reads only core's.

The hook is made here by the builder's own `_sign_up_to_channel_uses_read`, so that the modules
signed up to it are the bot's and not a copy. Until the builder makes it, the registry reads every
module's columns itself and is handed no hook, and these helpers hand none: the tests using them
pass before and after, and the build need not touch them. The suite's precedent is
tests/core/test_change_queue.py, which hands `fault_outcome` only where `ChangeType` declares it.

The factory reset asks the same of every season, not only the live one, through a hook of its own
(`setting_channels_read`), made by the builder's `_sign_up_to_setting_channels_read` and handed
to `gather_targets` in the same way.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any


def sign_up_channel_uses_read(bot: Any) -> None:
    """Give *bot* the hook the registry asks each module for its channels through, made by the
    builder's own sign-up, which reads *bot*'s `db_path` alone; nothing until the builder makes
    one."""
    import leaguebot.__main__ as entry_point

    sign_up = getattr(entry_point, "_sign_up_to_channel_uses_read", None)
    if sign_up is not None:
        sign_up(bot)


def channel_uses_given(db_path: str) -> dict[str, Any]:
    """The keyword `find_channel_use` and `channel_refusal` are handed the hook by: the builder's
    hook on *db_path* where the registry takes one, and nothing until it does."""
    from leaguebot.core.services.channel_registry_service import find_channel_use

    if "channel_uses_read" not in inspect.signature(find_channel_use).parameters:
        return {}
    bot = SimpleNamespace(db_path=db_path)
    sign_up_channel_uses_read(bot)
    return {"channel_uses_read": bot.channel_uses_read}


def sign_up_setting_channels_read(bot: Any) -> None:
    """Give *bot* the hook the factory reset asks each module through for every channel its
    settings hold in any season, made by the builder's own sign-up, which reads *bot*'s `db_path`
    alone; nothing until the builder makes one."""
    import leaguebot.__main__ as entry_point

    sign_up = getattr(entry_point, "_sign_up_to_setting_channels_read", None)
    if sign_up is not None:
        sign_up(bot)


def setting_channels_given(db_path: str) -> dict[str, Any]:
    """The keyword `gather_targets` is handed the hook by: the builder's hook on *db_path* where
    the reset takes one, and nothing until it does."""
    from leaguebot.core.services.factory_reset_service import gather_targets

    if "setting_channels_read" not in inspect.signature(gather_targets).parameters:
        return {}
    bot = SimpleNamespace(db_path=db_path)
    sign_up_setting_channels_read(bot)
    return {"setting_channels_read": bot.setting_channels_read}
