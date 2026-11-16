"""How a driver is named and where they come from, as every picture and every ordering draws them.

Core owns the drivers, so this is the one reader of a driver's profile, accounts and test-mode
fields, and of whether the league collects a nationality. What a signup records of a driver is
signup's, asked through core's `driver_names_read` hook for the season drawn (`read_driver_records`);
core merges it with what it holds itself: a mock driver has no signup, and carries its own name and
nationality, and while test mode is on its own switch stands in for signup's.

A caller is handed the hook (`bot.driver_names_read`) and passes it in: an empty one, as a bare test
stand-in carries, names a driver by their account alone and collects a nationality.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.driver_names import SignupName
from leaguebot.core.services.rounds.round_context import division_context
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.core.utils.input_validator import DRAWN_NAME

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriverRecord:
    """What is held of one driver to name them by: the signup's name, username and nationality for
    the season asked about, and the profile's own test-mode fields. Each is None where none is held.
    """

    profile_id: int
    discord_user_id: str
    signup_name: str | None
    signup_username: str | None
    signup_nationality: str | None
    test_name: str | None
    test_nationality: str | None
    is_test_driver: bool

    @property
    def nationality(self) -> str | None:
        """The nationality the driver is drawn under: a mock driver's own, else their signup's."""
        value = self.test_nationality if self.is_test_driver else self.signup_nationality
        return value or None


def resolve_driver_name(
    *,
    discord_user_id: str | int | None,
    display_name: str | None = None,
    signup_display_name: str | None = None,
    signup_username: str | None = None,
    test_display_name: str | None = None,
) -> str:
    """The first of the five links that yields a non-empty value (FR-005).

    An image cannot carry a Discord mention as the textual lineup does, so the chain ends
    at the user id rather than at nothing: every driver is named, always.

    **Each link is cleaned before it is judged** (#362). A Discord name is the one text a
    graphic draws that the league cannot control, so it is stripped of group mentions, emoji
    and markup rather than refused — the rules a name the league types is refused by. A link
    left empty by the cleaning, a name made only of emoji, falls through to the next.
    """
    for candidate in (
        display_name,
        signup_display_name,
        signup_username,
        test_display_name,
        None if discord_user_id is None else str(discord_user_id),
    ):
        if candidate is None:
            continue
        cleaned = DRAWN_NAME.check("name", str(candidate)).text
        if cleaned:
            return cleaned
    return ""


async def season_of_division(db_path: str, division_id: int | None) -> int | None:
    """The season *division_id* belongs to, or None."""
    if division_id is None:
        return None
    division = await division_context(db_path, division_id)
    return division.season_id if division is not None else None


async def read_driver_records(
    db_path: str,
    driver_names_read: AwaitedHook,
    user_ids: Iterable[str | int],
    *,
    season_id: int | None = None,
    any_account: bool = False,
) -> dict[str, DriverRecord]:
    """The record of each driver whose Discord account is among *user_ids*, keyed by that id as a
    string. A driver core holds no profile for is absent.

    The driver is found by the account their profile holds now, or, with *any_account*, by any
    account they have held: a verdict names the account the result was recorded under, which may be
    one they have since left. Their signup is the one signup hands back through *driver_names_read*
    for *season_id* (their latest where it holds none to it, or where *season_id* is None).
    """
    ids = sorted({str(user_id) for user_id in user_ids})
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    if any_account:
        sql = (
            "SELECT da.discord_user_id AS asked, dp.id, dp.discord_user_id, dp.is_test_driver, "
            "       dp.test_display_name, dp.test_nationality "
            "FROM driver_accounts da JOIN driver_profiles dp ON dp.id = da.driver_profile_id "
            f"WHERE da.discord_user_id IN ({marks})"
        )
    else:
        sql = (
            "SELECT dp.discord_user_id AS asked, dp.id, dp.discord_user_id, dp.is_test_driver, "
            "       dp.test_display_name, dp.test_nationality "
            f"FROM driver_profiles dp WHERE dp.discord_user_id IN ({marks})"
        )
    async with get_connection(db_path) as db:
        rows = await (await db.execute(sql, ids)).fetchall()

    signups: dict[int, SignupName] = {}
    for handed in await driver_names_read.call([int(row["id"]) for row in rows], season_id):
        signups.update(handed.drivers)

    records: dict[str, DriverRecord] = {}
    for row in rows:
        signup = signups.get(int(row["id"]))
        records.setdefault(
            str(row["asked"]),
            DriverRecord(
                profile_id=int(row["id"]),
                discord_user_id=str(row["discord_user_id"]),
                signup_name=signup.name if signup else None,
                signup_username=signup.username if signup else None,
                signup_nationality=signup.nationality if signup else None,
                test_name=row["test_display_name"] if row["is_test_driver"] else None,
                test_nationality=row["test_nationality"],
                is_test_driver=bool(row["is_test_driver"]),
            ),
        )
    return records


async def read_nationality_collected(db_path: str, driver_names_read: AwaitedHook) -> bool:
    """Whether the league collects a driver's nationality at all.

    Where it does not, a graphic with no flags is exactly what was configured and raises
    nothing (XIV.4's configured absence). Where it does, a driver who stated none is an
    ordinary emptied optional field and reports as one.

    While test mode is active the test-mode switch stands in for signup's, so a maintainer may see
    the graphics of a server under test both with flags and without them without disturbing the
    setting real signups run on. Otherwise the answer is signup's, handed back through
    *driver_names_read*; with no subscriber, or where the stand-in cannot be read, flags are drawn.
    """
    try:
        async with get_connection(db_path) as db:
            config = await (
                await db.execute(
                    "SELECT test_mode_active, test_mode_nationality_required FROM server_configs"
                )
            ).fetchone()
    except sqlite3.Error:
        log.warning(
            "the nationality switch could not be read, so flags are drawn", exc_info=True
        )
        return True
    if config is not None and config["test_mode_active"]:
        return bool(config["test_mode_nationality_required"])
    for handed in await driver_names_read.call([], None):
        return bool(handed.nationality_collected)
    return True


async def read_driver_names(
    db_path: str,
    driver_names_read: AwaitedHook,
    guild: discord.Guild | None,
    user_ids: list[int],
    *,
    division_id: int | None = None,
) -> dict[int, str]:
    """The name each driver is drawn under (XIV.16, and the wip-spec's person-name rule).

    The display name of their Discord account on the server at the moment of generation,
    falling through the names the league recorded for them (in their signup to the season of
    *division_id*, where one is named) and ending at the user id. A graphic carries no mention,
    so this is what stands in its place.
    """
    records = await read_driver_records(
        db_path,
        driver_names_read,
        user_ids,
        season_id=await season_of_division(db_path, division_id),
    )
    names: dict[int, str] = {}
    for user_id in user_ids:
        member = guild.get_member(user_id) if guild is not None else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.HTTPException):
                member = None
        record = records.get(str(user_id))
        names[user_id] = resolve_driver_name(
            discord_user_id=user_id,
            display_name=member.display_name if member is not None else None,
            signup_display_name=record.signup_name if record else None,
            signup_username=record.signup_username if record else None,
            test_display_name=record.test_name if record else None,
        )
    return names


async def read_driver_nationalities(
    db_path: str,
    driver_names_read: AwaitedHook,
    user_ids: list[int],
    *,
    division_id: int | None = None,
) -> dict[int, str | None]:
    """Each driver's recorded nationality, or None where they stated none.

    Read from their signup to the season of *division_id*, where one is named. A mock driver has no
    signup to hold one, and carries its own instead.
    """
    records = await read_driver_records(
        db_path,
        driver_names_read,
        user_ids,
        season_id=await season_of_division(db_path, division_id),
    )
    return {int(user_id): record.nationality for user_id, record in records.items()}
