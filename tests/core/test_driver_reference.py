"""A driver named as text: resolved to their profile, and offered by name as it is typed (#600).

Under test mode the `/driver` commands take a driver as a text field, a mention or an ID, since
Discord's member field cannot carry a fake driver. `driver_reference` turns that text into the
driver's profile through any account they have held, and lists the server's drivers by name for
the field's autocomplete: a fake driver by its test name, a real one by the name their signup
gives through core's `driver_names_read` hook, which a stub answers here so that no signup table is
read.

The drivers: Lewis (profile 1, account 101, signed up as "Lewis"); Ghost, a fake driver (profile 2,
account 9000000000000000001); and Kimi (profile 3), whose account was 201 and is now 202, signed up
as "Kimi".
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.models.driver_names import SignupName, SignupNames
from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.framework.utils.hooks import AwaitedHook

LEWIS, GHOST, KIMI_OLD, KIMI = "101", "9000000000000000001", "201", "202"


def _names_hook(names: dict[int, str]) -> AwaitedHook:
    """`driver_names_read` as signup would answer it: each profile of *names* named by its signup."""
    hook = AwaitedHook()

    async def _answer(profile_ids, _season_id=None):
        return SignupNames(
            drivers={
                pid: SignupName(name=names[pid], username=None, nationality=None)
                for pid in profile_ids if pid in names
            },
            nationality_collected=False,
        )

    hook.subscribe(_answer)
    return hook


async def _league(tmp_path, *, extra: int = 0) -> str:
    """The drivers of the module docstring, and *extra* further fake drivers "Fake 00" onwards."""
    db_path = os.path.join(str(tmp_path), "drivers.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (1, ?, 'ASSIGNED')", (LEWIS,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (2, ?, 'UNASSIGNED', 1, 'Ghost')", (GHOST,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (3, ?, 'ASSIGNED')", (KIMI_OLD,),
        )
        # Kimi moves to a new account: the old one stays among his accounts.
        await db.execute("UPDATE driver_profiles SET discord_user_id = ? WHERE id = 3", (KIMI,))
        for n in range(extra):
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, 'UNASSIGNED', 1, ?)",
                (str(9000000000000000100 + n), f"Fake {n:02d}"),
            )
        await db.commit()
    return db_path


@pytest.mark.parametrize("text", [f"<@{LEWIS}>", LEWIS, f"  {LEWIS}  "])
async def test_a_mention_or_an_id_resolves_to_the_driver(tmp_path, text):
    from leaguebot.core.services.drivers.driver_reference import resolve_driver

    driver = await resolve_driver(await _league(tmp_path), text)

    assert driver is not None
    assert (driver.profile_id, driver.discord_user_id, driver.is_test_driver) == (1, LEWIS, False)


@pytest.mark.parametrize("text", ["Lewis", "", "<#101>", "555"])
async def test_text_naming_no_driver_resolves_to_none(tmp_path, text):
    from leaguebot.core.services.drivers.driver_reference import resolve_driver

    assert await resolve_driver(await _league(tmp_path), text) is None


async def test_a_past_account_resolves_to_the_drivers_current_one(tmp_path):
    from leaguebot.core.services.drivers.driver_reference import resolve_driver

    driver = await resolve_driver(await _league(tmp_path), KIMI_OLD)

    assert driver is not None
    assert (driver.profile_id, driver.discord_user_id) == (3, KIMI)


async def test_a_fake_drivers_id_resolves_to_its_profile(tmp_path):
    from leaguebot.core.services.drivers.driver_reference import resolve_driver

    driver = await resolve_driver(await _league(tmp_path), GHOST)

    assert driver is not None
    assert (driver.profile_id, driver.discord_user_id, driver.is_test_driver, driver.test_name) == (
        2, GHOST, True, "Ghost",
    )


async def test_drivers_are_offered_by_name_fake_by_test_name_real_by_signup(tmp_path):
    from leaguebot.core.services.drivers.driver_reference import driver_choices

    db_path = await _league(tmp_path)
    hook = _names_hook({1: "Lewis", 3: "Kimi"})

    offered = await driver_choices(db_path, hook, "")
    filtered = await driver_choices(db_path, hook, "gH")

    assert sorted((c.name, c.value) for c in offered) == [
        ("Ghost", GHOST), ("Kimi", KIMI), ("Lewis", LEWIS),
    ]
    assert [(c.name, c.value) for c in filtered] == [("Ghost", GHOST)]


async def test_the_name_list_is_bounded_to_what_discord_shows(tmp_path):
    from leaguebot.core.services.drivers.driver_reference import driver_choices

    db_path = await _league(tmp_path, extra=30)

    offered = await driver_choices(db_path, _names_hook({1: "Lewis", 3: "Kimi"}), "")

    assert len(offered) == 25
