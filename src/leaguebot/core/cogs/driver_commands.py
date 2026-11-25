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

**`/driver assign-bulk`** (#633) is the one command that is a form: `AssignBulkModal` takes one driver
a line, `<@id>, division, team`, and `assign_bulk` places the list through one change. A group
passes the form how to name a pasted mention (`Resolver`), the member group refusing one outside
the server and swapping a past account for the current one, the test-mode group resolving it as it
resolves text, so a fake driver can be pasted. The paste is refused in up to three passes, each
naming every fault of its kind: a line that cannot be read, at the form; a driver, division or team
that is not found, here, after the stage and an archived season refuse the whole paste once; what
the seats, the drivers' states, test mode and the templates allow, by the change
(`driver_assign_bulk_change`).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
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
    NO_UNCONFIRMED_REFUSAL,
    NOT_PLACING_REFUSAL,
    NOT_UNASSIGNED_REFUSAL,
)
from leaguebot.core.services.drivers.driver_assign_bulk_change import (
    ASSIGN_BULK_COMMAND,
    ASSIGN_BULK_WHAT,
    DRIVER_ASSIGN_BULK,
)
from leaguebot.core.services.seasons.season_service import SeasonImmutableError
from leaguebot.core.utils.placement_paste import (
    PastedPlacement,
    format_placement_errors,
    parse_placement_paste,
)
from leaguebot.framework.utils.interaction_errors import describe
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.league_server import LeagueModal
from leaguebot.framework.utils.log_lines import refuse

#: The stages `/driver assign` and `/driver unassign` are available in: while the season is built,
#: and mid-season for the drivers of the window just closed.
_PLACING_STAGES = frozenset({SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS})


@dataclass(frozen=True)
class ResolvedDriver:
    """The driver a command acts on: the account they use now, and the name the command calls them
    by. No member is carried: nothing a command does once the driver is resolved needs one, and a
    fake driver has none."""

    account: str
    name: str


#: How a group names a pasted mention: the driver it acts on, or the words that say there is none,
#: which the paste's refusal gives under the line's number.
Resolver = Callable[[discord.Interaction, int], Awaitable["ResolvedDriver | str"]]


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
    new: ResolvedDriver,
) -> None:
    """Make *new*'s account the current account of the driver *old_account* names.

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
            "new_user_id": new.account,
            "new_name": new.name,
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
    """Assign a driver to a team and division."""
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

    # Fetch the driver profile. With the signup module off a member holding none is placed all
    # the same, and given one as the save places them.
    profile = await bot.driver_service.get_profile(driver.account)
    if profile is None and await bot.module_service.is_enabled("signup"):
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
            "driver_profile_id": None if profile is None else profile.id,
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


class AssignBulkModal(LeagueModal, title="Assign drivers in bulk"):
    """The pasted list of placements, a driver a line (`/driver assign-bulk`).

    The paste is read here, and one that cannot be is refused naming every fault with nothing
    asked of the queue; what it reads is placed by `assign_bulk`.
    """

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="<@driver>, division, team — one per line",
        style=discord.TextStyle.paragraph,
        placeholder="<@123456789012345678>, Pro, McLaren\n<@234567890123456789>, Am, Williams",
        required=True,
        max_length=4000,
    )

    def __init__(self, bot: LeagueBot, resolve: Resolver) -> None:
        super().__init__()
        self._league_bot = bot
        self._resolve = resolve

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # The lines are resolved against the server and the database, which can outrun the three
        # seconds an interaction may take to be answered.
        await interaction.response.defer(ephemeral=True)
        pasted, errors = parse_placement_paste(self.entries.value)
        if errors:
            await refuse(
                interaction,
                format_placement_errors(errors),
                what=ASSIGN_BULK_WHAT,
                reason="\n".join(errors),
            )
            return
        await assign_bulk(self._league_bot, interaction, pasted, self._resolve)


async def assign_bulk(
    bot: LeagueBot,
    interaction: discord.Interaction,
    pasted: list[PastedPlacement],
    resolve: Resolver,
) -> None:
    """Place the drivers of a pasted list, in its order, through one change.

    The stage and an archived season refuse the whole paste, once. Otherwise every line's driver,
    division and team is found, and every line that is not found is named at once; what is found is
    asked of the queue, which judges the seats, the drivers' states and the templates
    (`driver_assign_bulk_change`), and places all of the list or none.
    """
    season = await bot.season_service.get_setup_or_active_season()
    if season is None or season.stage not in _PLACING_STAGES:
        await refuse(
            interaction,
            NOT_PLACING_REFUSAL.format(command=ASSIGN_BULK_COMMAND),
            what=ASSIGN_BULK_WHAT,
        )
        return
    try:
        await bot.season_service.assert_season_mutable(season)
    except SeasonImmutableError:
        await refuse(
            interaction,
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            what=ASSIGN_BULK_WHAT,
        )
        return

    signup_on = await bot.module_service.is_enabled("signup")
    divisions: dict[str, tuple[int, str] | None] = {}
    lines: list[dict[str, object]] = []
    faults: list[str] = []
    for each in pasted:
        found = await resolve(interaction, each.account)
        if isinstance(found, str):
            faults.append(f"Line {each.line}: {found}")
            continue
        if each.division not in divisions:
            divisions[each.division] = await bot.placement_service.resolve_division(
                season.id, each.division
            )
        resolved = divisions[each.division]
        if resolved is None:
            faults.append(
                f"Line {each.line}: Division **{each.division}** not found in the active season."
            )
            continue
        division_id, division_name = resolved
        reference = await bot.team_service.resolve_division_team(division_id, each.team)
        if reference.team is None:
            faults.append(f"Line {each.line}: {reference.refusal}")
            continue
        profile = await bot.driver_service.get_profile(found.account)
        if profile is None and signup_on:
            faults.append(f"Line {each.line}: No driver profile found for **{found.name}**.")
            continue
        lines.append(
            {
                "line": each.line,
                "driver_profile_id": None if profile is None else profile.id,
                "discord_user_id": found.account,
                "driver_name": found.name,
                "division_id": division_id,
                "division": division_name,
                "team_name": reference.team["name"],
                "team_full_name": reference.team["full_name"],
            }
        )
    if faults:
        await refuse(
            interaction,
            format_placement_errors(faults),
            what=ASSIGN_BULK_WHAT,
            reason="\n".join(faults),
        )
        return

    # The placement is a change on the queue, which judges every line again as it starts.
    await bot.change_queue.ask(
        DRIVER_ASSIGN_BULK,
        {"season_id": season.id, "lines": lines},
        interaction=interaction, what=ASSIGN_BULK_WHAT,
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
    """Turn down a driver: an Unassigned one, or, with the signup module off, one holding
    only unconfirmed placements.

    Available while the season is in Placements or Ongoing, placements — where the
    drivers of a window are settled. The driver returns to Not Signed Up and loses the
    driver role (with signup off, every unconfirmed placement is removed and no role is
    taken, none having been given); their signup is kept with the season.
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

    # With the signup module off a driver is turned down for holding only unconfirmed
    # placements, not for being Unassigned, which no signup makes them.
    signup_off = not await bot.module_service.is_enabled("signup")
    profile = await bot.driver_service.get_profile(driver.account)
    if signup_off:
        turned_down = profile is not None and await bot.placement_service.can_reject(
            driver_profile_id=profile.id, season_id=season.id
        )
        refusal = f"⛔ {NO_UNCONFIRMED_REFUSAL}"
    else:
        turned_down = profile is not None and profile.current_state is DriverState.UNASSIGNED
        refusal = NOT_UNASSIGNED_REFUSAL
    if profile is None or not turned_down:
        await refuse(
            interaction,
            refusal.format(driver_name=driver.name),
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
            "signup_off": signup_off,
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
