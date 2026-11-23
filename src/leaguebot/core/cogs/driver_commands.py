"""What each `/driver` command does once the driver it names is known (#600).

The commands are said twice: by the member group, whose driver field Discord fills with a member,
and by the test-mode group, whose driver field takes text so that a fake driver, which has no
account to pick, can be named. What the two share is here, each command's body as a function of the
bot, the interaction and a `ResolvedDriver`, so that a command acts the same on whichever driver
reaches it. Each cog resolves its own field to a driver and calls the body, which refuses, logs and
asks the queue as the command always has. There is no base class: a Discord command group is one
cog's, and the cogs differ in their fields, not in what they do.

Every body is called with the interaction deferred, but `reassign`'s, which answers a refusal of the
stage before it has read its parameters.
"""
from __future__ import annotations

from dataclasses import dataclass

import discord

from leaguebot.core.models.season import ONGOING_STAGES, SeasonStage
from leaguebot.core.services.drivers.driver_changes import (
    DRIVER_ASSIGN,
    DRIVER_MOVE,
    DRIVER_REASSIGN,
    DRIVER_REJECT,
    DRIVER_RELEASE,
    DRIVER_SACK,
    DRIVER_UNASSIGN,
    NOT_PLACING_REFUSAL,
    NOT_UNASSIGNED_REFUSAL,
)
from leaguebot.core.services.seasons.season_service import SeasonImmutableError
from leaguebot.framework.utils.interaction_errors import describe
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.log_lines import refuse

#: The stages `/driver assign` and `/driver unassign` are available in: while the season is built,
#: and mid-season for the drivers of the window just closed.
_PLACING_STAGES = frozenset({SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS})


@dataclass(frozen=True)
class ResolvedDriver:
    """The driver a command acts on: the account they use now, the name the command calls them by,
    and the server member behind that account where there is one (a fake driver has none)."""

    account: str
    name: str
    member: discord.Member | None = None


async def reassign_stage_ok(bot: LeagueBot, interaction: discord.Interaction) -> bool:
    """Whether the season is in a stage `/driver reassign` may run in; where it is not, the
    interaction has been refused.

    **Only while drivers are being placed** — Placements, or Ongoing, placements.
    Re-keying a profile onto another Discord account is part of settling who sits where;
    everywhere else `/driver move` is the command for changing where a driver sits. The
    consequence is accepted: a person who changes account between seasons is re-keyed
    once the next season reaches placements, which is the moment they would be placed
    anyway.

    Checked before the parameters, so that a manager running it in the wrong stage is told the
    rule rather than told they mis-typed a snowflake.
    """
    from leaguebot.core.utils.season_gate import PLACEMENT_STAGES, season_for_command

    return await season_for_command(
        interaction,
        bot.season_service,
        "driver reassign",
        stages=PLACEMENT_STAGES,
        refusal=(
            "⛔ A driver's account is changed only while drivers are being placed "
            "— while the season is in **Placements**, or in **Ongoing, placements** "
            "with the drivers of a closed signup window still to place. Elsewhere, "
            "`/driver move` is the command for changing where a driver sits."
        ),
    ) is not None


async def reassign(
    bot: LeagueBot,
    interaction: discord.Interaction,
    old_account: str,
    new_user: discord.Member,
) -> None:
    """Make *new_user* the current account of the driver *old_account* names.

    The account it replaces joins the driver's past accounts, and every account the driver
    has held goes on identifying them. Nothing the league holds is rewritten.

    The reassignment, its roles, the held signup channel and the portrait are a change on
    the queue, which refuses again as it starts what the service refuses
    (`core/services/drivers/driver_changes.py`).
    """
    await bot.change_queue.ask(
        DRIVER_REASSIGN,
        {
            "old_user_id": old_account,
            "new_user_id": str(new_user.id),
            "new_name": new_user.display_name,
        },
        interaction=interaction, what=describe(interaction),
    )


async def assign(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
    division: str,
    team: str,
) -> None:
    """Assign an Unassigned driver to a team and division."""
    # Resolve season, and the stage placements may be made in
    season = await bot.season_service.get_setup_or_active_season()
    if season is None or season.stage not in _PLACING_STAGES:
        await refuse(
            interaction,
            NOT_PLACING_REFUSAL.format(command="/driver assign"),
            what=describe(interaction),
        )
        return

    try:
        await bot.season_service.assert_season_mutable(season)
    except SeasonImmutableError:
        await refuse(
            interaction,
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            what=describe(interaction),
        )
        return

    # Resolve division
    resolved = await bot.placement_service.resolve_division(season.id, division)
    if resolved is None:
        await refuse(
            interaction,
            f"⛔ Division **{division}** not found in the active season.",
            what=describe(interaction),
        )
        return
    division_id, division_name = resolved

    # A team is typed by its shorthand.
    reference = await bot.team_service.resolve_division_team(division_id, team)
    if reference.team is None:
        await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
        return

    # Fetch the driver profile
    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None:
        await refuse(
            interaction,
            f"⛓ No driver profile found for **{driver.name}**.",
            what=describe(interaction),
        )
        return

    # The assignment is a change on the queue, which refuses again as it starts what is
    # refused here (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_ASSIGN,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
            "division_id": division_id,
            "division": division_name,
            "team_name": reference.team["name"],
            "team_full_name": reference.team["full_name"],
        },
        interaction=interaction, what=describe(interaction),
    )


async def unassign(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
    division: str,
) -> None:
    """Remove a driver's placement from a specific division."""
    season = await bot.season_service.get_setup_or_active_season()
    if season is None or season.stage not in _PLACING_STAGES:
        await refuse(
            interaction,
            NOT_PLACING_REFUSAL.format(command="/driver unassign"),
            what=describe(interaction),
        )
        return

    try:
        await bot.season_service.assert_season_mutable(season)
    except SeasonImmutableError:
        await refuse(
            interaction,
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            what=describe(interaction),
        )
        return

    resolved = await bot.placement_service.resolve_division(season.id, division)
    if resolved is None:
        await refuse(
            interaction,
            f"⛔ Division **{division}** not found in the active season.",
            what=describe(interaction),
        )
        return
    division_id, division_name = resolved

    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None:
        await refuse(
            interaction,
            f"⛓ No driver profile found for **{driver.name}**.",
            what=describe(interaction),
        )
        return

    # The removal is a change on the queue, which refuses again as it starts what is
    # refused here (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_UNASSIGN,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
            "division_id": division_id,
            "division": division_name,
        },
        interaction=interaction, what=describe(interaction),
    )


async def move(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
    from_division: str,
    team: str,
    to_division: str | None,
) -> None:
    """Move a committed driver in one step.

    Full-time to Reserve, team to team, or a promotion or relegation between divisions,
    with the roles swapped and each lineup touched posted once. Available in the three
    ongoing stages; before placements are confirmed a seat is changed by unassigning and
    assigning again.
    """
    season = await bot.season_service.get_confirmed_season()
    if season is None or season.stage not in ONGOING_STAGES:
        await refuse(
            interaction,
            "⛔ `/driver move` is available only while the season is ongoing.",
            what=describe(interaction),
        )
        return

    resolved_from = await bot.placement_service.resolve_division(season.id, from_division)
    if resolved_from is None:
        await refuse(
            interaction,
            f"⛔ Division **{from_division}** not found in the active season.",
            what=describe(interaction),
        )
        return
    resolved_to = resolved_from
    if to_division is not None:
        found_to = await bot.placement_service.resolve_division(season.id, to_division)
        if found_to is None:
            await refuse(
                interaction,
                f"⛔ Division **{to_division}** not found in the active season.",
                what=describe(interaction),
            )
            return
        resolved_to = found_to

    # A team is typed by its shorthand, in the division moved into.
    reference = await bot.team_service.resolve_division_team(resolved_to[0], team)
    if reference.team is None:
        await refuse(interaction, f"⛔ {reference.refusal}", what=describe(interaction))
        return

    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None:
        await refuse(
            interaction,
            f"⛓ No driver profile found for **{driver.name}**.",
            what=describe(interaction),
        )
        return

    # The move, its roles and its lineups are a change on the queue, which refuses again
    # as it starts what is refused here (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_MOVE,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
            "from_division_id": resolved_from[0],
            "from_division": resolved_from[1],
            "to_division_id": resolved_to[0],
            "to_division": resolved_to[1],
            "team_name": reference.team["name"],
            "team_full_name": reference.team["full_name"],
        },
        interaction=interaction, what=describe(interaction),
    )


async def release(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
    division: str,
) -> None:
    """Release a committed driver from one division, in the ongoing stages."""
    season = await bot.season_service.get_confirmed_season()
    if season is None or season.stage not in ONGOING_STAGES:
        await refuse(
            interaction,
            "⛔ `/driver release` is available only while the season is ongoing.",
            what=describe(interaction),
        )
        return

    resolved = await bot.placement_service.resolve_division(season.id, division)
    if resolved is None:
        await refuse(
            interaction,
            f"⛔ Division **{division}** not found in the active season.",
            what=describe(interaction),
        )
        return

    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None:
        await refuse(
            interaction,
            f"⛓ No driver profile found for **{driver.name}**.",
            what=describe(interaction),
        )
        return

    # The release, its roles and its lineup are a change on the queue, which refuses again as
    # it starts what is refused here (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_RELEASE,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
            "division_id": resolved[0],
            "division": resolved[1],
        },
        interaction=interaction, what=describe(interaction),
    )


async def reject(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
) -> None:
    """Turn down an Unassigned driver.

    Available while the season is in Placements or Ongoing, placements — where the
    drivers of a window are settled. The driver returns to Not Signed Up and loses the
    driver role; their signup is kept with the season.
    """
    from leaguebot.core.models.driver_profile import DriverState

    season = await bot.season_service.get_setup_or_active_season()
    if season is None or season.stage not in _PLACING_STAGES:
        await refuse(
            interaction,
            NOT_PLACING_REFUSAL.format(command="/driver reject"),
            what=describe(interaction),
        )
        return

    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None or profile.current_state is not DriverState.UNASSIGNED:
        await refuse(
            interaction,
            NOT_UNASSIGNED_REFUSAL.format(driver_name=driver.name),
            what=describe(interaction),
        )
        return

    # The turn-down, its approval's withdrawal, its audit entry and the driver role are a
    # change on the queue, which refuses again as it starts what is refused here
    # (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_REJECT,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
        },
        interaction=interaction, what=describe(interaction),
    )


async def sack(
    bot: LeagueBot,
    interaction: discord.Interaction,
    driver: ResolvedDriver,
) -> None:
    """Sack a driver: revoke all roles, clear assignments, revert to Not Signed Up."""
    # Sacking is available only while the season is ongoing. Between seasons every driver has
    # already been returned to Not Signed Up by the season's end, and a season still being built
    # has no confirmed placement to sack anyone from.
    season = await bot.season_service.get_confirmed_season()
    if season is None or season.stage not in ONGOING_STAGES:
        await refuse(
            interaction,
            "⛔ `/driver sack` is available only while the season is ongoing.",
            what=describe(interaction),
        )
        return

    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None:
        await refuse(
            interaction,
            f"⛓ No driver profile found for **{driver.name}**.",
            what=describe(interaction),
        )
        return

    # The sack, its roles and its lineups are a change on the queue, which refuses again as
    # it starts what is refused here (`core/services/drivers/driver_changes.py`).
    await bot.change_queue.ask(
        DRIVER_SACK,
        {
            "driver_profile_id": profile.id,
            "discord_user_id": driver.account,
            "driver_name": driver.name,
            "season_id": season.id,
        },
        interaction=interaction, what=describe(interaction),
    )
