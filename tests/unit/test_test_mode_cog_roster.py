"""The test-mode command surface for driver nationality.

`/test-mode nationality` is the test-mode counterpart of `/signup nationality`, and
`/test-mode roster add` takes a nationality the way the signup wizard takes one. These
tests run the callbacks against a migrated database with Discord stubbed — no gateway, no
server, no running bot.

The guards are unwrapped as the other cog suites unwrap them: `channel_guard` and
`admin_only` have their own cover, and a stubbed interaction is not a `discord.Member`.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from cogs import test_mode_cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402

SERVER_ID = 6161
DIVISION = "Division 1"


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return bool(self.messages)

    async def defer(self, **kwargs):
        pass

    async def send_message(self, content, **kwargs):
        self.messages.append(content)


class _Followup:
    """Where a deferred command's replies land — one call per message sent."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content, **kwargs):
        self.messages.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = None
        self.response = _Response()
        self.followup = _Followup()
        self.user = SimpleNamespace(display_name="Tester", id=1)

    @property
    def sent(self) -> list[str]:
        """Every message the command sent, however it sent it, in order."""
        return self.response.messages + self.followup.messages

    @property
    def reply(self) -> str:
        assert self.sent, "the command replied with nothing"
        return self.sent[-1]


def _unwrap(cmd):
    """The innermost callback, bypassing channel_guard and admin_only."""
    return cmd.callback.__wrapped__.__wrapped__


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "testmode_cog.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, DIVISION),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Redline', 2, 0)",
            (division_id,),
        )
        team_id = cursor.lastrowid
        for seat_number in (1, 2):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )
        await db.commit()
    return path


@pytest.fixture
def cog(db_path):
    return test_mode_cog.TestModeCog(
        SimpleNamespace(
            db_path=db_path,
            config_service=ConfigService(db_path),
            output_router=SimpleNamespace(post_log=AsyncMock()),
        )
    )


async def _switch_off(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET test_mode_nationality_required = 0 "
            "WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()


async def _add(cog, *, name="Mock Alpha", nationality=None):
    interaction = _Interaction()
    await _unwrap(test_mode_cog.TestModeCog.roster_add)(
        cog, interaction, name, "Redline", DIVISION, nationality
    )
    return interaction


# ── /test-mode nationality ────────────────────────────────────────────────


class TestTheNationalityCommand:
    def test_it_sits_on_the_test_mode_group(self):
        assert "nationality" in {c.name for c in test_mode_cog.TestModeCog.test_mode.commands}

    async def test_it_starts_on(self, cog, db_path):
        """Migration 042 defaults it on, as the setting it parallels defaults on."""
        config = await cog.bot.config_service.get_server_config(SERVER_ID)

        assert config.test_mode_nationality_required is True

    async def test_it_toggles_off_and_back_on(self, cog):
        first = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.nationality)(cog, first)
        second = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.nationality)(cog, second)

        assert "**OFF**" in first.reply
        assert "**ON**" in second.reply

    async def test_the_flip_is_persisted(self, cog):
        await _unwrap(test_mode_cog.TestModeCog.nationality)(cog, _Interaction())

        config = await cog.bot.config_service.get_server_config(SERVER_ID)
        assert config.test_mode_nationality_required is False

    async def test_it_is_logged(self, cog):
        await _unwrap(test_mode_cog.TestModeCog.nationality)(cog, _Interaction())

        logged = cog.bot.output_router.post_log.await_args.args[1]
        assert "/test-mode nationality" in logged
        assert "disabled" in logged

    async def test_it_is_refused_while_test_mode_is_off(self, cog, db_path):
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = 0 WHERE server_id = ?",
                (SERVER_ID,),
            )
            await db.commit()

        interaction = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.nationality)(cog, interaction)

        assert "only available when test mode is enabled" in interaction.reply
        config = await cog.bot.config_service.get_server_config(SERVER_ID)
        assert config.test_mode_nationality_required is True


# ── /test-mode roster add ─────────────────────────────────────────────────


class TestRosterAddTakesANationality:
    async def test_the_reply_names_the_canonical_nationality(self, cog):
        interaction = await _add(cog, nationality="united kingdom")

        assert "Nationality: **British**" in interaction.reply

    async def test_an_invalid_one_is_refused(self, cog):
        interaction = await _add(cog, nationality="Martian")

        assert "Invalid nationality" in interaction.reply

    async def test_it_is_optional(self, cog):
        interaction = await _add(cog)

        assert "Added fake driver" in interaction.reply
        assert "Nationality:" not in interaction.reply

    async def test_it_reaches_the_log(self, cog):
        await _add(cog, nationality="Dutch")

        assert "nationality: Dutch" in cog.bot.output_router.post_log.await_args.args[1]


class TestRosterAddWhileTheSwitchIsOff:
    """The signup wizard drops the question rather than collecting an unkept answer."""

    async def test_giving_one_is_refused(self, cog, db_path):
        await _switch_off(db_path)

        interaction = await _add(cog, nationality="British")

        assert "Nationality is switched off for test mode" in interaction.reply

    async def test_no_driver_is_created_by_the_refused_command(self, cog, db_path):
        await _switch_off(db_path)

        await _add(cog, nationality="British")

        async with get_connection(db_path) as db:
            row = await (
                await db.execute(
                    "SELECT COUNT(*) AS n FROM driver_profiles WHERE is_test_driver = 1"
                )
            ).fetchone()
        assert row["n"] == 0

    async def test_a_driver_may_still_be_added_without_one(self, cog, db_path):
        await _switch_off(db_path)

        interaction = await _add(cog)

        assert "Added fake driver" in interaction.reply


# ── /test-mode roster list ────────────────────────────────────────────────


class TestRosterListShowsIt:
    async def _list(self, cog) -> str:
        """The whole listing, however many messages it took to send it."""
        interaction = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.roster_list)(cog, interaction, DIVISION)
        return "\n".join(interaction.sent)

    async def test_the_column_is_headed(self, cog):
        await _add(cog, nationality="Dutch")

        assert "Nationality" in await self._list(cog)

    async def test_a_drivers_own_is_printed(self, cog):
        await _add(cog, name="Mock Alpha", nationality="brazil")

        rendered = await self._list(cog)

        assert "Brazilian" in rendered

    async def test_a_driver_without_one_prints_a_dash(self, cog):
        await _add(cog, name="Mock Alpha")

        rendered = await self._list(cog)

        assert "Mock Alpha" in rendered
        assert "—" in rendered

    async def test_the_team_column_still_stands_beside_it(self, cog):
        await _add(cog, name="Mock Alpha", nationality="Dutch")

        rendered = await self._list(cog)

        assert "Redline" in rendered
        assert rendered.index("Redline") < rendered.index("Dutch")

    async def test_a_roster_too_large_for_one_message_is_sent_as_several(
        self, cog, db_path
    ):
        """The bug: past about twenty drivers Discord refused the whole listing.

        A body over 2000 characters is answered with a 400, not truncated, so the
        manager got no roster at all and a traceback in the log. The reserve team is
        used because it seats without limit — the point is the length of the reply,
        not how the drivers came to be seated.
        """
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM divisions WHERE name = ?", (DIVISION,)
            )
            division_id = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Reserve', 0, 1)",
                (division_id,),
            )
            await db.commit()

        for i in range(25):
            interaction = _Interaction()
            await _unwrap(test_mode_cog.TestModeCog.roster_add)(
                cog, interaction, f"Mock Driver {i:02d}", "Reserve", DIVISION, "Dutch"
            )

        interaction = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.roster_list)(
            cog, interaction, DIVISION
        )

        assert len(interaction.sent) > 1, "a roster this size must span several messages"
        for message in interaction.sent:
            assert len(message) <= 2000, f"a page of {len(message)} chars would 400"
        # Every driver still reaches the manager, across however many messages it took.
        whole = "\n".join(interaction.sent)
        for i in range(25):
            assert f"Mock Driver {i:02d}" in whole
