"""What the signup module, while it is on, still lacks for a season to be confirmed.

Signup signs two readers up with core (`signup/wiring.py`), so that core's season checks hold no
rule of signup's: `confirmation_refusals` to the hook a season's confirmation is refused through,
and `configuration_faults` to the hook a season's configuration faults are read through. Both
check signup's own switch and hand nothing while signup is off. They read only; a fault in reading
is not caught here and reaches the caller's own failure path, as any other read of the
confirmation's does.

**The placements review and the approval are refused on the signup channel alone.** The league's
two roles are not judged again, since confirming the configuration required them while signup was
on and fixed them until the season ends. The configuration's faults name the roles as well as the
channel, since the configuration is the last moment a league can choose others freely.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from leaguebot.signup.models.switch import SWITCH_KEY
from leaguebot.core.services.seasons.confirmation_refusals import Confirmation, Refusal
from leaguebot.core.services.seasons.approval_checks import (
    PresenceState,
    channel_fault,
    judged_presence,
)
from leaguebot.core.utils.channel_guard import league_role_faults
from leaguebot.signup.services.channel_uses import COMMAND, LABEL, channel_needs

if TYPE_CHECKING:
    import discord

    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

#: What a season's approval is refused on, one line to the reason beneath it in the log channel.
_NO_SIGNUP_CHANNEL = "**Signup channel** (use `/signup channel`)"
#: How the placements review's private lines close: the season is held back, and what to run after.
_WITHHELD = "The season is **not** offered for approval while that stands —"
_RUN_AGAIN = "run `/season placements-review` again."


async def confirmation_refusals(
    modules: "ModuleService",
    signup: "SignupModuleService",
    season_id: int,
    confirmation: Confirmation,
    guild: discord.Guild | None = None,
) -> list[Refusal]:
    """What signup refuses a season's approval on, and withholds the placements review's Approve
    button on: its signup channel, unset while signup is on, or, where *guild* (which the hook
    hands every subscriber, None where the caller has no server) is given, gone from the server
    or lacking a permission the bot needs there (`channel_needs`), named with what it lacks. A
    channel the server could not be asked about is not refused (`judged_presence`), a check that
    cannot tell refusing nothing.

    Subscribed to core's `confirmation_refused`, after results'. The placements review words each
    in a private reply and the approval in its refusal, whose unset-channel words are those it has
    always refused with. Signup refuses nothing at the configuration's confirmation here (its
    configuration faults are read through `configuration_faults`), nor while it is off or holds no
    configuration at all. A confirmed season's `/signup channel` replaces only a channel gone from
    the server, so an unusable channel's words say to give the bot the permissions and never to
    set another.
    """
    if confirmation is Confirmation.CONFIGURATION:
        return []
    if not await modules.is_enabled(SWITCH_KEY):
        return []
    config = await signup.get_config()
    if config is None:
        return []
    approval = confirmation is Confirmation.APPROVAL
    channel_id = config.signup_channel_id
    if channel_id is None:
        if not approval:
            return [Refusal(
                f"⛔ **No signup channel is set.** {_WITHHELD} set one with `{COMMAND}`, "
                f"then {_RUN_AGAIN}"
            )]
        return [Refusal(
            "❌ Season cannot be approved — signup module is enabled but "
            f"missing required configuration:\n• {_NO_SIGNUP_CHANNEL}",
            "signup module is enabled but missing required configuration:\n" + _NO_SIGNUP_CHANNEL,
        )]
    if guild is None:
        return []
    presence = await judged_presence(guild, channel_id, channel_needs())
    if presence.state is PresenceState.GONE:
        if not approval:
            return [Refusal(
                f"⛔ **The signup channel <#{channel_id}> is no longer on the server.** "
                f"{_WITHHELD} set it again with `{COMMAND}`, then {_RUN_AGAIN}"
            )]
        return [_channel_refusal(f"<#{channel_id}> is no longer on the server — `{COMMAND}`.")]
    if presence.state is PresenceState.UNUSABLE:
        missing = ", ".join(presence.missing)
        if not approval:
            return [Refusal(
                f"⛔ **The signup channel <#{channel_id}> is missing permissions the bot needs "
                f"there: {missing}.** {_WITHHELD} give the bot them, then {_RUN_AGAIN}"
            )]
        return [_channel_refusal(
            f"<#{channel_id}> is missing permissions the bot needs there: {missing} — "
            "give the bot them."
        )]
    return []


def _channel_refusal(item: str) -> Refusal:
    """The approval's refusal of a signup channel that is set but gone from the server or unusable
    there, laid out as the unset channel's is: a head and the item beneath it, which the log line
    carries."""
    reason = f"the signup channel cannot be used:\n• {item}"
    return Refusal(f"❌ Season cannot be approved — {reason}", reason)


async def configuration_faults(
    modules: "ModuleService",
    signup: "SignupModuleService",
    configs: "ConfigService",
    season_id: int,
    guild: "discord.Guild | None",
) -> list[str]:
    """Every fault in signup's part of a season's configuration, each a line a league manager
    reads, naming what to run to put it right: no signup channel, or one gone from the server or
    lacking a permission the bot needs there (`channel_fault`, judged where *guild* is given; one
    the server could not be asked about is not named), no base role, no driver role, and the
    league's two roles judged upon the server where *guild* is given (`league_role_faults`).
    Nothing while signup is off: the two roles are the league's, but only signup needs them.

    Subscribed to core's `configuration_faults_read`, and read where signup's lines stand in the
    configuration's faults, ahead of the team names, so the manager reads them in the same order.
    """
    if not await modules.is_enabled(SWITCH_KEY):
        return []
    config = await signup.get_config()
    server_config = await configs.get_server_config()
    base_role_id = server_config.base_role_id if server_config is not None else None
    driver_role_id = server_config.driver_role_id if server_config is not None else None
    faults: list[str] = []
    if config is None or config.signup_channel_id is None:
        faults.append("The signup module has no **signup channel** — `/signup channel`.")
    elif guild is not None:
        # Judged on the server as the roles are: named where it is gone or the bot lacks what it
        # needs there, and not where the server could not be asked.
        fault = await channel_fault(
            guild, None, config.signup_channel_id, LABEL, COMMAND, channel_needs()
        )
        if fault:
            faults.append(fault)
    if base_role_id is None:
        faults.append("The signup module needs the league's **base role** — `/bot base-role`.")
    if driver_role_id is None:
        faults.append(
            "The signup module needs the league's **driver role** — `/bot driver-role`."
        )
    faults += league_role_faults(guild, base_role_id, driver_role_id)
    return faults
