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

import discord

from leaguebot.core.db.database import get_connection

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


async def build_drawing(db_path: str, guild, division_id: int, *, include_uncommitted: bool = False):
    """Resolve the division into a LineupDrawing, or raise LineupDataError.

    It reads only the database, and is handed its path: the lineup's drawing needs
    nothing else of the bot to resolve what it draws.

    A placement made mid-season and not yet confirmed is left out (#220), for the lineup of
    record shows who races. *include_uncommitted* draws it in, for the one caller that must
    see the lineup as it **will** stand: the mid-season placements review, which draws what
    confirming will post and withholds its button where that will not draw (#374).
    """
    from leaguebot.image.services.aspects.image_lineup_service import resolve_drawing
    from leaguebot.image.services.aspects.image_results_post import SIGNUP_FOR_SEASON_SQL

    async with get_connection(db_path) as db:
        division = await (
            await db.execute(
                "SELECT d.id, d.name, d.tier, s.id AS season_id, s.season_number "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()
        if division is None:
            from leaguebot.image.services.aspects.image_lineup_service import LineupDataError

            raise LineupDataError(f"division {division_id} no longer exists")
        season_id = division["season_id"]

        instances = await (
            await db.execute(
                "SELECT id, name, full_name, max_seats, is_reserve FROM team_instances "
                "WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                (division_id,),
            )
        ).fetchall()

        teams = []
        for instance in instances:
            seats = await (
                await db.execute(
                    # A mock driver has no signup record to hold a nationality and carries
                    # its own, read by the same branch on is_test_driver the name is.
                    "SELECT ts.seat_number, dp.discord_user_id, dp.is_test_driver, "
                    "       dp.test_display_name, sr.server_display_name, "
                    "       sr.discord_username, "
                    "       CASE WHEN dp.is_test_driver = 1 THEN dp.test_nationality "
                    "            ELSE sr.nationality END AS nationality "
                    "FROM team_seats ts "
                    # A placement not yet confirmed mid-season is not drawn (issue #220),
                    # unless the caller asks for the lineup as it will stand (#374).
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "      AND (dsa.committed = 1 OR ? OR NOT EXISTS ("
                    "          SELECT 1 FROM seasons s WHERE s.id = dsa.season_id "
                    "          AND s.status = 'ACTIVE')) "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    f"LEFT JOIN signup_records sr ON sr.id = {SIGNUP_FOR_SEASON_SQL} "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (division_id, int(include_uncommitted), season_id, instance["id"]),
                )
            ).fetchall()
            teams.append(
                SimpleNamespace(
                    name=instance["name"],
                    full_name=instance["full_name"],
                    is_reserve=bool(instance["is_reserve"]),
                    seats=[
                        SimpleNamespace(
                            seat_number=row["seat_number"],
                            discord_user_id=row["discord_user_id"],
                            server_display_name=row["server_display_name"],
                            discord_username=row["discord_username"],
                            test_display_name=row["test_display_name"],
                            nationality=row["nationality"],
                        )
                        for row in seats
                    ],
                )
            )


    # The suppression switch (FR-009): a lineup with no flags at all is exactly what a
    # league that switched nationality collection off configured, and raises nothing. Read
    # through the module's single reader rather than a copy of it — this path carried its
    # own, and its own read the wrong table.
    from leaguebot.image.services.aspects.image_results_post import _nationality_collected

    collected = await _nationality_collected(db_path)

    # The first link of the name chain is the account's display name on the server *at the
    # moment of generation*, which only the guild can answer (research R9).
    members = seated_members(guild, teams)
    display_names = {
        user_id: member.display_name for user_id, member in members.items()
    }

    return (
        division,
        resolve_drawing(
            division_name=division["name"],
            division_tier=division["tier"],
            season_number=division["season_number"],
            teams=teams,
            display_names=display_names,
            nationality_collected=collected,
        ),
        list(members.values()),
    )
