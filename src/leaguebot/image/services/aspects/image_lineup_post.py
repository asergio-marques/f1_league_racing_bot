"""Resolve a division's lineup (038).

Every lineup is drawn through core's drawing protocol (the lineup of record, `/team lineup`'s
output and a season review's) and posted by core
(`leaguebot.core.services.modules.image_drawing.ImageDrawing`, implemented by
`leaguebot.image.services.render.image_drawing.ImageDrawing`): this module holds what that
drawing needs of the division, resolving it into a `LineupDrawing` (`build_drawing`) and finding
the member behind each seat (`seated_members`).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import discord

from leaguebot.core.services.drivers.driver_names import read_nationality_collected

if TYPE_CHECKING:
    from leaguebot.framework.utils.hooks import AwaitedHook

#: The one template this aspect draws. Named here rather than inline because the render
#: body labels its directory resolution with it as well as passing it to the renderer, and
#: the two must not be able to drift.
LINEUP_TEMPLATE_KEY = "lineup_template"


def seated_members(guild, teams) -> dict[str, discord.Member]:
    """The Discord member behind each occupied seat, as user id -> Member.

    One reader for what the posting path and the preview path both need. Both once carried
    this loop verbatim, and both now want the Member for a second purpose -- the display name
    it answers, and the profile picture the portrait is obtained from -- so a second divergent
    copy would have been two.

    A seat with no driver, a driver the guild cannot resolve (they have left), and a mock
    driver carrying a synthetic id all simply do not appear.
    """
    members: dict[str, discord.Member] = {}
    if guild is None:
        return members
    for team in teams:
        for seat in team.seats:
            if seat.discord_user_id is None:
                continue
            member = guild.get_member(int(seat.discord_user_id))
            if member is not None:
                members[str(seat.discord_user_id)] = member
    return members


async def seated_teams(
    db_path: str,
    driver_names_read: AwaitedHook,
    division_id: int,
    season_id: int | None,
    *,
    include_uncommitted: bool = False,
) -> list[SimpleNamespace]:
    """The division's teams with their seats, in the shape the lineup's `resolve_drawing` takes.

    The seating is core's, as the lineup of record shows it (`division_seating`: every team, every
    seat, whoever holds it in any state, a placement not yet confirmed mid-season left out unless
    *include_uncommitted*). What a seat's driver is drawn under comes from core's record of them
    (`read_driver_records`): the signup the league holds for them to *season_id*, and for a mock
    driver, which has no signup to hold a nationality, its own.
    """
    from leaguebot.core.services.drivers.division_seating import division_seating
    from leaguebot.core.services.drivers.driver_names import read_driver_records

    seating = await division_seating(
        db_path, division_id, include_uncommitted=include_uncommitted
    )
    records = await read_driver_records(
        db_path,
        driver_names_read,
        {seat.occupant.discord_user_id for team in seating for seat in team.seats if seat.occupant},
        season_id=season_id,
    )

    teams = []
    for team in seating:
        seats = []
        for seat in team.seats:
            occupant = seat.occupant
            record = records.get(occupant.discord_user_id) if occupant else None
            seats.append(
                SimpleNamespace(
                    seat_number=seat.seat_number,
                    discord_user_id=occupant.discord_user_id if occupant else None,
                    profile_id=occupant.profile_id if occupant else None,
                    server_display_name=record.signup_name if record else None,
                    discord_username=record.signup_username if record else None,
                    test_display_name=occupant.test_display_name if occupant else None,
                    nationality=record.nationality if record else None,
                )
            )
        teams.append(
            SimpleNamespace(
                name=team.name,
                full_name=team.full_name,
                is_reserve=team.is_reserve,
                max_seats=team.max_seats,
                seats=seats,
            )
        )
    return teams


async def build_drawing(
    db_path: str,
    driver_names_read: AwaitedHook,
    guild,
    division_id: int,
    *,
    include_uncommitted: bool = False,
):
    """Resolve the division into a LineupDrawing, or raise LineupDataError.

    It reads only the database, and is handed its path: the lineup's drawing needs
    nothing else of the bot to resolve what it draws.

    A placement made mid-season and not yet confirmed is left out (#220), for the lineup of
    record shows who races. *include_uncommitted* draws it in, for the one caller that must
    see the lineup as it **will** stand: the mid-season placements review, which draws what
    confirming will post and withholds its button where that will not draw (#374).
    """
    from leaguebot.image.services.aspects.image_lineup_service import resolve_drawing

    from leaguebot.core.services.rounds.round_context import division_context

    division = await division_context(db_path, division_id)
    if division is None:
        from leaguebot.image.services.aspects.image_lineup_service import LineupDataError

        raise LineupDataError(f"division {division_id} no longer exists")
    season_id = division.season_id

    teams = await seated_teams(
        db_path, driver_names_read, division_id, season_id, include_uncommitted=include_uncommitted
    )

    # The suppression switch (FR-009): a lineup with no flags at all is exactly what a
    # league that switched nationality collection off configured, and raises nothing. Read
    # through the module's single reader rather than a copy of it — this path carried its
    # own, and its own read the wrong table.
    collected = await read_nationality_collected(db_path, driver_names_read)

    # The first link of the name chain is the account's display name on the server *at the
    # moment of generation*, which only the guild can answer (research R9).
    members = seated_members(guild, teams)
    display_names = {
        user_id: member.display_name for user_id, member in members.items()
    }

    return (
        division,
        resolve_drawing(
            division_name=division.name,
            division_tier=division.tier,
            season_number=division.season_number,
            teams=teams,
            display_names=display_names,
            nationality_collected=collected,
        ),
        list(members.values()),
    )
