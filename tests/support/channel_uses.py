"""The hooks core asks each module through for the channels its settings hold.

A channel does one job, server-wide, so the registry asks every module which of its settings
holds a channel (`channel_uses_read`). Each module's channel columns are its own (architecture.md,
"Each table is read and written by its owner alone"), so the registry reads only core's.

The hook is made here by the builder's own `_sign_up_to_channel_uses_read`, so that the modules
signed up to it are the bot's and not a copy. The factory reset asks the same of every season, not
only the live one, through a hook of its own (`setting_channels_read`), made by the builder's
`_sign_up_to_setting_channels_read`.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def sign_up_channel_uses_read(bot: Any) -> None:
    """Give *bot* the hook the registry asks each module for its channels through, made by the
    builder's own sign-up, which reads *bot*'s `db_path` alone."""
    from leaguebot.__main__ import _sign_up_to_channel_uses_read

    _sign_up_to_channel_uses_read(bot)


def channel_uses_given(db_path: str) -> dict[str, Any]:
    """The keyword `find_channel_use` and `channel_refusal` are handed the hook by: the builder's
    hook on *db_path*."""
    bot = SimpleNamespace(db_path=db_path)
    sign_up_channel_uses_read(bot)
    return {"channel_uses_read": bot.channel_uses_read}


def sign_up_setting_channels_read(bot: Any) -> None:
    """Give *bot* the hook the factory reset asks each module through for every channel it holds
    in any season (those its settings name, those it posted to and those it created), made by the
    builder's own sign-up, which reads *bot*'s `db_path` alone."""
    from leaguebot.__main__ import _sign_up_to_setting_channels_read

    _sign_up_to_setting_channels_read(bot)


def setting_channels_given(db_path: str) -> dict[str, Any]:
    """The keyword `gather_targets` is handed the hook by: the builder's hook on *db_path*."""
    bot = SimpleNamespace(db_path=db_path)
    sign_up_setting_channels_read(bot)
    return {"setting_channels_read": bot.setting_channels_read}
