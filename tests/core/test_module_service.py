"""Unit tests for ModuleService — T030."""

from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database with the league's one server_configs row."""
    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
    return path


def _modules(db_path: str):
    """The league's `ModuleService` on *db_path*, each module's switch reader signed up to it as
    the builder signs them up."""
    from leaguebot.__main__ import register_module_switches
    from leaguebot.core.services.modules.module_service import ModuleService

    modules = ModuleService(db_path)
    register_module_switches(modules)
    return modules


# ---------------------------------------------------------------------------
# ModuleService tests
# ---------------------------------------------------------------------------


#: Every optional module, by the name its switch is asked by (`is_<name>_enabled`).
MODULES = ["weather", "signup", "results", "attendance", "images"]


@pytest.mark.parametrize("module", MODULES)
async def test_a_module_is_off_by_default(db_path, module):
    svc = _modules(db_path)
    assert await getattr(svc, f"is_{module}_enabled")() is False


@pytest.mark.parametrize("module", MODULES)
async def test_each_module_s_switch_reads_its_own_flag_alone(db_path, module):
    """The module's flag turned on, as its switch leaves it: the module reads as on and every
    other module still as off. Every other module then turned on too: all read as on together.
    The module turned off again: it reads as off, and every other module still as on."""
    from tests.support.season_league import set_module_flag

    svc = _modules(db_path)

    async def switches() -> dict[str, bool]:
        return {name: await getattr(svc, f"is_{name}_enabled")() for name in MODULES}

    await set_module_flag(db_path, module, True)
    assert await switches() == {name: name == module for name in MODULES}

    for other in MODULES:
        if other != module:
            await set_module_flag(db_path, other, True)
    assert await switches() == {name: True for name in MODULES}

    await set_module_flag(db_path, module, False)
    assert await switches() == {name: name != module for name in MODULES}


@pytest.mark.parametrize("module", MODULES)
async def test_each_switch_is_answered_by_the_reader_its_module_registered(db_path, module):
    """`ModuleService` answers whether a module is on by asking the reader that module signed up,
    handed a connection to the league's database, and reads no flag itself: with every flag in
    the database off, a reader answering on is what the service says, and it was handed the
    league's database open."""
    from leaguebot.core.services.modules.module_service import ModuleService

    servers = []

    async def reader(db) -> bool:
        servers.append((await (await db.execute("SELECT server_id FROM server_configs")).fetchone())[0])
        return True

    svc = ModuleService(db_path)
    svc.register_switch(module, reader)

    assert await getattr(svc, f"is_{module}_enabled")() is True
    assert servers == [1]


async def test_a_switch_no_module_signed_up_for_is_an_error_not_off(db_path):
    """A module whose reader the builder never signed up is a fault in the bot's assembly, and
    asking its switch raises naming it rather than reading it as off."""
    from leaguebot.core.services.modules.module_service import ModuleService, SwitchNotSignedUp

    assert issubclass(SwitchNotSignedUp, LookupError)
    with pytest.raises(SwitchNotSignedUp, match="images"):
        await ModuleService(db_path).is_images_enabled()


async def test_a_switch_asked_on_a_held_connection_reads_on_that_connection(db_path):
    """A save asks a module's switch on the connection it holds (`is_enabled_on`): the reader is
    handed that connection, so a flag the save has set and not yet committed is what it reads."""
    from tests.support.season_league import set_module_flag

    svc = _modules(db_path)
    await set_module_flag(db_path, "signup", False)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET signup_module_enabled = 1")
        assert await svc.is_enabled_on(db, "signup") is True
        await db.rollback()
    assert await svc.is_signup_enabled() is False
