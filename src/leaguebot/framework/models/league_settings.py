"""The league's settings as the framework reads them: a row naming the roles and the log channel."""

from __future__ import annotations

from typing import Protocol


class SettingsRow(Protocol):
    """What the framework reads of the league's configuration row.

    Core's `ServerConfig` carries more, and fits this: the framework asks for the log channel
    (where the log writer posts), the league admin role (whom a stop notice mentions) and the
    interaction role (the league manager's), and nothing else.
    """

    log_channel_id: int
    league_admin_role_id: int | None
    interaction_role_id: int
