"""The claim on the league's server, released and taken again (issue #247).

`/bot pack` frees the claim and keeps the configuration row, because the row carries what is
the league's — test mode and the weather and signup flags — as well as what is the server's.
These pin that the row survives the release, that nothing reads a released row as a
configured server, and that the next `/bot init` claims it again, once.
"""
from __future__ import annotations

import dataclasses
import os
import sqlite3

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.services.config_service import ConfigService

OLD_SERVER = 4242
NEW_SERVER = 5353


def _config(server_id: int) -> ServerConfig:
    return ServerConfig(
        server_id=server_id,
        interaction_role_id=server_id + 1,
        interaction_channel_id=server_id + 2,
        log_channel_id=server_id + 3,
        league_admin_role_id=server_id + 4,
    )


@pytest.fixture
async def service(tmp_path) -> ConfigService:
    db_path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(db_path)
    service = ConfigService(db_path)
    assert await service.save_server_config(_config(OLD_SERVER))
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET test_mode_active = 1, weather_module_enabled = 1, "
            "signup_module_enabled = 1, test_mode_nationality_required = 0"
        )
        await db.commit()
    return service


async def _row(service: ConfigService) -> dict:
    async with get_connection(service._db_path) as db:
        cursor = await db.execute("SELECT * FROM server_configs")
        rows = await cursor.fetchall()
    assert len(rows) == 1
    return dict(rows[0])


async def test_releasing_the_claim_clears_the_server_and_its_four_settings(service):
    await service.release_claim()

    row = await _row(service)
    assert row["server_id"] is None
    assert row["interaction_role_id"] is None
    assert row["interaction_channel_id"] is None
    assert row["log_channel_id"] is None
    assert row["league_admin_role_id"] is None


async def test_releasing_the_claim_keeps_what_is_the_league_s(service):
    await service.release_claim()

    row = await _row(service)
    assert row["test_mode_active"] == 1
    assert row["weather_module_enabled"] == 1
    assert row["signup_module_enabled"] == 1
    assert row["test_mode_nationality_required"] == 0


async def test_a_released_row_is_no_configured_server(service):
    await service.release_claim()

    assert await service.get_server_config() is None
    assert await service.get_league_server_id() is None


async def test_a_released_row_takes_no_setting(service):
    """The settings belong to the next server's `/bot init`, not to whoever asks first."""
    await service.release_claim()

    assert await service.set_core_setting("log_channel_id", 99) is False
    assert (await _row(service))["log_channel_id"] is None


async def test_another_server_claims_a_released_row(service):
    await service.release_claim()

    assert await service.save_server_config(_config(NEW_SERVER)) is True

    config = await service.get_server_config()
    assert config.server_id == NEW_SERVER
    assert config.interaction_channel_id == NEW_SERVER + 2
    assert config.league_admin_role_id == NEW_SERVER + 4
    assert await service.get_league_server_id() == NEW_SERVER


async def test_the_claim_carries_test_mode_and_the_module_flags_over(service):
    """`ServerConfig` defaults test mode off; the claim must write neither it nor the flags."""
    await service.release_claim()
    await service.save_server_config(_config(NEW_SERVER))

    config = await service.get_server_config()
    assert config.test_mode_active is True
    assert config.test_mode_nationality_required is False
    row = await _row(service)
    assert row["weather_module_enabled"] == 1
    assert row["signup_module_enabled"] == 1


def test_server_config_carries_no_module_flags():
    """Whether a module is on is `module_service`'s to answer (issue #158).

    `ServerConfig` once carried the weather and signup flags, which nothing read and which
    no other module had, so a reader taking enablement from it got a partial answer that
    looked whole — the trap #153 removed for `previous_season_number`.
    """
    names = [f.name for f in dataclasses.fields(ServerConfig)]

    assert [n for n in names if n.endswith("_module_enabled")] == []


async def test_only_one_server_claims_a_released_row(service):
    await service.release_claim()

    assert await service.save_server_config(_config(NEW_SERVER)) is True
    assert await service.save_server_config(_config(OLD_SERVER)) is False
    assert await service.get_league_server_id() == NEW_SERVER


async def test_a_claimed_row_is_not_claimed_again(service):
    assert await service.save_server_config(_config(NEW_SERVER)) is False
    assert await service.get_league_server_id() == OLD_SERVER


async def test_the_table_holds_one_row_at_most(service):
    with pytest.raises(sqlite3.IntegrityError):
        async with get_connection(service._db_path) as db:
            await db.execute(
                "INSERT INTO server_configs (id, server_id) VALUES (2, ?)", (NEW_SERVER,)
            )


def test_no_read_takes_a_row_for_a_configured_server():
    """"Is the bot set up?" asks whether the row holds a server, never whether a row exists.

    A packed bot keeps its row with the claim cleared, so `SELECT 1 FROM server_configs`
    answers yes for a bot that serves nobody. Reads of the league's own columns — test mode,
    the module flags — are rightly unconditional and are not what this looks for, nor is
    the claim's own `NOT EXISTS`, which must refuse while any row exists.
    """
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    existence = re.compile(
        r"(?<!EXISTS \()SELECT\s+1\s+FROM\s+server_configs(?!\s+WHERE\s+server_id)"
    )
    offenders = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if existence.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []
