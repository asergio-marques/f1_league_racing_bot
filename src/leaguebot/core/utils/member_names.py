"""How the log channel names a member: their display name and their mention.

A leaf module, importing nothing of the bot's. `report_failure` in
`core/utils/interaction_errors.py` and the standard lines in `core/utils/log_lines.py` both name
the member who used an interaction, and `log_lines` cannot be imported from `interaction_errors`
without a cycle: it reaches the guild through `league_server`, which imports
`interaction_errors`. The two helpers that need only what the interaction carries live here
instead. `log_lines` imports them and so still offers them to the callers that take them from it.
"""

from __future__ import annotations

import discord


def member_named(display_name: str | None, member_id: int) -> str:
    """"Alex (<@4242>)", or the mention alone where the member has no name to give."""
    return f"{display_name} (<@{member_id}>)" if display_name else f"<@{member_id}>"


def interaction_member(interaction: discord.Interaction) -> str:
    """The member who used *interaction*, named as the log channel names them."""
    user = interaction.user
    return member_named(getattr(user, "display_name", None), user.id)
