"""What the signup module, while it is on, still lacks for a season to be confirmed.

Signup signs two readers up with core (`signup/wiring.py`), so that core's season checks hold no
rule of signup's: `confirmation_refusals` to the hook a season's confirmation is refused through,
and `configuration_faults` to the hook a season's configuration faults are read through. Both
check signup's own switch and hand nothing while signup is off. They read only; a fault in reading
is not caught here and reaches the caller's own failure path, as any other read of the
confirmation's does.

**The approval is refused on the signup channel alone.** The league's two roles are not judged
again at the approval, since confirming the configuration required them while signup was on and
fixed them until the season ends. The configuration's faults name the roles as well as the channel,
since the configuration is the last moment a league can choose others freely.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from leaguebot.core.services.modules.confirmation_refusals import Confirmation, Refusal
from leaguebot.core.utils.channel_guard import league_role_faults

if TYPE_CHECKING:
    import discord

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

#: What a season's approval is refused on, one line to the reason beneath it in the log channel.
_NO_SIGNUP_CHANNEL = "**Signup channel** (use `/signup channel`)"


async def confirmation_refusals(
    modules: "ModuleService",
    signup: "SignupModuleService",
    season_id: int,
    confirmation: Confirmation,
) -> list[Refusal]:
    """What signup refuses a season's approval on: its signup channel, unset while signup is on.

    Subscribed to core's `confirmation_refused`, after results'. Signup refuses neither the
    placements review nor the configuration's confirmation here (its configuration faults are read
    through `configuration_faults`), and refuses nothing while it is off or holds no configuration
    at all. The words are those the approval has always refused with.
    """
    if confirmation is not Confirmation.APPROVAL:
        return []
    if not await modules.is_signup_enabled():
        return []
    config = await signup.get_config()
    if config is None or config.signup_channel_id is not None:
        return []
    return [Refusal(
        "❌ Season cannot be approved — signup module is enabled but "
        f"missing required configuration:\n• {_NO_SIGNUP_CHANNEL}",
        "signup module is enabled but missing required configuration:\n" + _NO_SIGNUP_CHANNEL,
    )]


async def configuration_faults(
    modules: "ModuleService",
    signup: "SignupModuleService",
    configs: "ConfigService",
    season_id: int,
    guild: "discord.Guild | None",
) -> list[str]:
    """Every fault in signup's part of a season's configuration, each a line a league manager
    reads, naming what to run to put it right: no signup channel, no base role, no driver role,
    and the league's two roles judged upon the server where *guild* is given
    (`league_role_faults`). Nothing while signup is off: the two roles are the league's, but only
    signup needs them.

    Subscribed to core's `configuration_faults_read`, and read where signup's lines stand in the
    configuration's faults, ahead of the team names, so the manager reads them in the same order.
    """
    if not await modules.is_signup_enabled():
        return []
    config = await signup.get_config()
    server_config = await configs.get_server_config()
    base_role_id = server_config.base_role_id if server_config is not None else None
    driver_role_id = server_config.driver_role_id if server_config is not None else None
    faults: list[str] = []
    if config is None or config.signup_channel_id is None:
        faults.append("The signup module has no **signup channel** — `/signup channel`.")
    if base_role_id is None:
        faults.append("The signup module needs the league's **base role** — `/bot base-role`.")
    if driver_role_id is None:
        faults.append(
            "The signup module needs the league's **driver role** — `/bot driver-role`."
        )
    faults += league_role_faults(guild, base_role_id, driver_role_id)
    return faults
