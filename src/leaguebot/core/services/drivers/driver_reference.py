"""A driver named as text: found, and offered by name as it is typed (#600).

While test mode is on the `/driver` commands take a driver as a text field, a mention or an ID,
because Discord's member field cannot carry a fake driver. `resolve_driver` turns that text into the
driver's profile through any account they have held, as every command already names a driver, and
`driver_choices` lists the server's drivers by name for the field's autocomplete.

A driver is named as `driver_names` names them: a fake driver by its test name, a real one by the
name their signup gives, asked through core's `driver_names_read` hook so that no table of signup's
is read here. Neither reads a Discord member: an autocomplete has three seconds to answer, and a
fake driver has no member.
"""
from __future__ import annotations

from dataclasses import dataclass

from discord import app_commands

from leaguebot.core.services.drivers.driver_names import read_driver_records, resolve_driver_name
from leaguebot.core.services.drivers.driver_service import current_account_of
from leaguebot.core.utils.input_validator import parse_user
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.utils.hooks import AwaitedHook

#: The most choices Discord shows for one autocomplete.
_MAX_CHOICES = 25


@dataclass(frozen=True)
class DriverReference:
    """The driver a piece of text names: their profile, the account they use now, and, for a fake
    driver, the name they are given."""

    profile_id: int
    discord_user_id: str
    is_test_driver: bool
    test_name: str | None


async def resolve_driver(db_path: str, text: str | None) -> DriverReference | None:
    """The driver *text* names, a user mention or an ID of any account they have held, or None
    where it is neither, or names no driver."""
    account = parse_user(text)
    if account is None:
        return None
    async with get_connection(db_path) as db:
        current = await current_account_of(db, account)
        row = await (
            await db.execute(
                "SELECT id, discord_user_id, is_test_driver, test_display_name "
                "FROM driver_profiles WHERE discord_user_id = ?",
                (current,),
            )
        ).fetchone()
    if row is None:
        return None
    is_test = bool(row["is_test_driver"])
    return DriverReference(
        profile_id=int(row["id"]),
        discord_user_id=str(row["discord_user_id"]),
        is_test_driver=is_test,
        test_name=row["test_display_name"] if is_test else None,
    )


async def driver_choices(
    db_path: str, driver_names_read: AwaitedHook, current: str | None
) -> list[app_commands.Choice[str]]:
    """The drivers *current* could be the start of, by name, each sending the account they use now.

    Matched anywhere in the name, ignoring case, and offered in name order: at most the 25 Discord
    shows.
    """
    async with get_connection(db_path) as db:
        rows = await (await db.execute("SELECT discord_user_id FROM driver_profiles")).fetchall()
    accounts = [str(row["discord_user_id"]) for row in rows]
    records = await read_driver_records(db_path, driver_names_read, accounts)

    typed = (current or "").strip().casefold()
    named = []
    for account in accounts:
        record = records.get(account)
        name = resolve_driver_name(
            discord_user_id=account,
            signup_display_name=record.signup_name if record else None,
            signup_username=record.signup_username if record else None,
            test_display_name=record.test_name if record else None,
        )
        if typed in name.casefold():
            named.append((name, account))
    named.sort(key=lambda pair: (pair[0].casefold(), pair[1]))
    return [
        app_commands.Choice(name=name[:100], value=account)
        for name, account in named[:_MAX_CHOICES]
    ]
