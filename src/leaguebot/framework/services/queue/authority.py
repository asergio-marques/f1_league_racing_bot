"""What the change queue asks of the league's authority tiers (architecture.md, "How the code is laid out").

Who holds which tier is a league rule, and core's (the core specification's "Who may do what"):
the framework declares here what its queue asks, core's configuration service fulfils it, and the
builder hands it to the queue. The queue never imports the rule. It sits beside the queue and not
among the models, because it takes a Discord member.
"""

from __future__ import annotations

from typing import Protocol

import discord

from leaguebot.framework.models.league_settings import SettingsRow


class AuthorityTiers(Protocol):
    """The tiers a stop notice's Retry and Discard are asked of, over the league's settings row.

    Each takes the row as the queue read it, `None` where no server is claimed, and the member who
    pressed.
    """

    def is_league_admin(self, config: SettingsRow | None, member: discord.Member) -> bool:
        """Whether *member* holds the league admin tier: who may discard a stopped job."""
        ...

    def is_league_manager(self, config: SettingsRow | None, member: discord.Member) -> bool:
        """Whether *member* holds the league manager tier, the league admin's carrying it: who
        may retry a stopped job."""
        ...
