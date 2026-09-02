"""`/test-mode toggle` refuses to enable test mode over a real league.

Test mode and a real roster may not share a server: switching test mode off deletes every
fake driver on it without confirmation, and while it is on the signup and placement paths
refuse real drivers outright. An open signup window is refused on the same footing — its
button would reject every driver who pressed it. So the toggle is guarded on the way *in*
only — leaving test mode is never refused.

The callback runs against a migrated database with Discord stubbed, and the guards are
unwrapped the way the other cog suites unwrap them.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs import test_mode_cog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402

SERVER_ID = 7272


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


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = None
        self.response = _Response()
        self.followup = SimpleNamespace(send=self._followup_send)
        self.user = SimpleNamespace(display_name="Tester", id=1)

    async def _followup_send(self, content, **kwargs):
        self.response.messages.append(content)

    @property
    def reply(self) -> str:
        assert self.response.messages, "the command replied with nothing"
        return self.response.messages[-1]


def _unwrap(cmd):
    """The innermost callback, bypassing channel_guard and admin_only."""
    return cmd.callback.__wrapped__.__wrapped__


async def _toggle(cog) -> _Interaction:
    interaction = _Interaction()
    await _unwrap(test_mode_cog.TestModeCog.toggle)(cog, interaction)
    return interaction


async def _flag(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?",
            (SERVER_ID,),
        )
        row = await cursor.fetchone()
    return bool(row["test_mode_active"])


async def _open_signups(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signups_open = 1 WHERE server_id = ?",
            (SERVER_ID,),
        )
        await db.commit()


async def _add_driver(db_path: str, user_id: str, state: str, *, test: bool = False) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(server_id, discord_user_id, current_state, is_test_driver) VALUES (?, ?, ?, ?)",
            (SERVER_ID, user_id, state, 1 if test else 0),
        )
        await db.commit()


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "toggle_guard.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 0)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (server_id, signup_channel_id, base_role_id, "
            "signed_up_role_id, signups_open) VALUES (?, 11, 12, 13, 0)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
def cog(db_path):
    return test_mode_cog.TestModeCog(
        SimpleNamespace(
            db_path=db_path,
            config_service=ConfigService(db_path),
            signup_module_service=SignupModuleService(db_path),
            output_router=SimpleNamespace(post_log=AsyncMock()),
        )
    )


# ── Enabling ──────────────────────────────────────────────────────────────


class TestEnabling:
    async def test_a_clean_server_may_enter_test_mode(self, cog, db_path):
        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply
        assert await _flag(db_path) is True

    async def test_a_real_driver_refuses_it(self, cog, db_path):
        await _add_driver(db_path, "4001", "ASSIGNED")

        interaction = await _toggle(cog)

        assert "cannot be enabled" in interaction.reply
        assert "**1** real driver" in interaction.reply

    async def test_the_flag_is_left_alone_when_refused(self, cog, db_path):
        """A refusal must not flip the flag: the toggle flips before it branches."""
        await _add_driver(db_path, "4002", "PENDING_ADMIN_APPROVAL")

        await _toggle(cog)

        assert await _flag(db_path) is False

    async def test_a_former_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, "4003", "NOT_SIGNED_UP")

        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply
        assert await _flag(db_path) is True

    async def test_a_fake_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, "9000000000000000001", "ASSIGNED", test=True)

        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply

    async def test_the_count_is_named(self, cog, db_path):
        await _add_driver(db_path, "4004", "UNASSIGNED")
        await _add_driver(db_path, "4005", "ASSIGNED")

        interaction = await _toggle(cog)

        assert "**2** real driver" in interaction.reply


class TestAnOpenSignupWindow:
    async def test_it_refuses_the_toggle(self, cog, db_path):
        await _open_signups(db_path)

        interaction = await _toggle(cog)

        assert "signups are open" in interaction.reply
        assert "`/signup close`" in interaction.reply

    async def test_the_flag_is_left_alone(self, cog, db_path):
        await _open_signups(db_path)

        await _toggle(cog)

        assert await _flag(db_path) is False

    async def test_the_window_is_not_closed_for_them(self, cog, db_path):
        """A flag flip must not post a public notice in a channel a league reads."""
        await _open_signups(db_path)

        await _toggle(cog)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT signups_open FROM signup_module_config WHERE server_id = ?",
                (SERVER_ID,),
            )
            row = await cursor.fetchone()
        assert bool(row["signups_open"]) is True

    async def test_a_closed_window_does_not_stand_in_the_way(self, cog, db_path):
        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply

    async def test_a_server_without_the_signup_module_is_not_held_up(self, cog, db_path):
        async with get_connection(db_path) as db:
            await db.execute(
                "DELETE FROM signup_module_config WHERE server_id = ?", (SERVER_ID,)
            )
            await db.commit()

        interaction = await _toggle(cog)

        assert "**enabled**" in interaction.reply


# ── Leaving ───────────────────────────────────────────────────────────────


class TestLeaving:
    async def test_nothing_holds_a_server_in_test_mode(self, cog, db_path, monkeypatch):
        """The guard is on the way in only — a server must always be able to leave."""
        await _open_signups(db_path)
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = 1 WHERE server_id = ?",
                (SERVER_ID,),
            )
            await db.commit()
        await _add_driver(db_path, "4006", "ASSIGNED")
        monkeypatch.setattr(
            "services.forecast_cleanup_service.flush_pending_deletions", AsyncMock()
        )

        interaction = await _toggle(cog)

        assert "**disabled**" in interaction.reply
        assert await _flag(db_path) is False
