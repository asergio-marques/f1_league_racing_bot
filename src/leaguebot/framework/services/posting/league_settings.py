"""What the framework asks of the league's settings (architecture.md, "How the code is laid out").

The framework asks and core answers: it declares here what it needs, core's configuration service
fulfils it, and the builder hands it over, as `bot.league_settings` and to the services that ask.
The framework never reads core's services on the bot. It sits beside the log writer, its main
asker, and not among the models, because one of its methods takes a database connection.
"""

from __future__ import annotations

from typing import Protocol

import aiosqlite

from leaguebot.framework.models.league_settings import SettingsRow


class LeagueSettings(Protocol):
    """The league's settings, as the framework asks them."""

    async def get_league_server_id(self) -> int | None:
        """The league's server, or None while none is claimed."""
        ...

    async def get_server_config(self) -> SettingsRow | None:
        """The league's settings row, or None while no server is claimed."""
        ...

    async def get_server_config_on(self, db: aiosqlite.Connection) -> SettingsRow | None:
        """`get_server_config` on *db*, as that connection sees it: the log writer reads the
        log channel there, so that a line is saved with what it records."""
        ...
