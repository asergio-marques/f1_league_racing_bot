"""Both of the league's roles can see the channels their buttons live in.

Four channels are created with permission overwrites keyed to a role, and every one of them
keyed only the *interaction* role. That was harmless while Discord's Administrator permission
carried the league manager tier, because an administrator could read every channel on the
server anyway.

It stopped being harmless the moment both tiers became roles the league configures
(issue #116). A league admin who does not also hold the interaction role is entitled to press
the buttons in all four — approving a signup, judging a penalty, cancelling an amendment —
and would have been unable to see any of them.

The admin role is skipped where the league has not set one, which is every server configured
before it existed.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402

SERVER_ID = 4242
MANAGER_ROLE = 222
ADMIN_ROLE = 444


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = f"role-{role_id}"
    return role


def _guild() -> MagicMock:
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.default_role = _role(1)
    guild.me = _role(2)
    guild.get_role = lambda role_id: _role(role_id) if role_id in (MANAGER_ROLE, ADMIN_ROLE) else None
    guild.get_channel = MagicMock(return_value=None)
    return guild


def _overwrite_role_ids(overwrites: dict) -> set[int]:
    return {getattr(target, "id", None) for target in overwrites}


async def _seed(tmp_path, *, admin_role_id: int | None = ADMIN_ROLE) -> str:
    """A migrated database holding a configured server, and one round to hang a channel off."""
    db_path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (SERVER_ID, MANAGER_ROLE, 111, 333, admin_role_id),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status) VALUES (1, ?, ?, 'ACTIVE')",
            (SERVER_ID, "2026-01-01"),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (1, 1, 'Pro', ?)",
            (MANAGER_ROLE,),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (1, 1, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            ("2026-06-01T14:00:00",),
        )
        await db.commit()
    return db_path


# ── The results submission channel ────────────────────────────────────────


async def _create_submission_channel(db_path, *, league_admin_role):
    from services import result_submission_service as rss

    guild = _guild()
    created: dict = {}

    async def _create(name, **kwargs):
        created.update(kwargs)
        created["name"] = name
        channel = MagicMock()
        channel.id = 901
        return channel

    guild.create_text_channel = _create
    await rss.create_submission_channel(
        guild, "Pro", 1, 1, 1, db_path,
        admin_role=_role(MANAGER_ROLE),
        league_admin_role=league_admin_role,
    )
    return created["overwrites"]


async def test_the_submission_channel_is_open_to_both_roles(tmp_path):
    db_path = await _seed(tmp_path)

    overwrites = await _create_submission_channel(db_path, league_admin_role=_role(ADMIN_ROLE))

    assert {MANAGER_ROLE, ADMIN_ROLE} <= _overwrite_role_ids(overwrites)


async def test_the_submission_channel_omits_an_admin_role_that_is_not_set(tmp_path):
    db_path = await _seed(tmp_path, admin_role_id=None)

    overwrites = await _create_submission_channel(db_path, league_admin_role=None)

    assert MANAGER_ROLE in _overwrite_role_ids(overwrites)
    assert ADMIN_ROLE not in _overwrite_role_ids(overwrites)


# ── The driver's signup wizard channel ────────────────────────────────────


async def _create_wizard_channel(*, league_admin_role_id):
    from services.wizard_service import WizardService

    guild = _guild()
    created: dict = {}

    async def _create(name, **kwargs):
        created.update(kwargs)
        return MagicMock()

    guild.create_text_channel = _create
    member = MagicMock(spec=discord.Member)
    member.name = "driver"

    service = WizardService.__new__(WizardService)
    await WizardService._create_wizard_channel(
        service, guild, member, None, MANAGER_ROLE, league_admin_role_id
    )
    return created["overwrites"]


async def test_the_wizard_channel_is_open_to_both_roles():
    """The admin review panel is posted here, so a league admin has to be able to read it."""
    overwrites = await _create_wizard_channel(league_admin_role_id=ADMIN_ROLE)

    assert {MANAGER_ROLE, ADMIN_ROLE} <= _overwrite_role_ids(overwrites)


async def test_the_wizard_channel_omits_an_admin_role_that_is_not_set():
    overwrites = await _create_wizard_channel(league_admin_role_id=None)

    assert MANAGER_ROLE in _overwrite_role_ids(overwrites)
    assert ADMIN_ROLE not in _overwrite_role_ids(overwrites)


# ── The signup channel ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "admin_role_id,expected",
    [(ADMIN_ROLE, {MANAGER_ROLE, ADMIN_ROLE}), (None, {MANAGER_ROLE})],
    ids=["admin role set", "admin role unset"],
)
async def test_the_signup_channel_is_open_to_both_roles(admin_role_id, expected, tmp_path):
    from cogs.signup_cog import SignupCog

    guild = _guild()
    channel = MagicMock()
    channel.id = 900
    channel.edit = AsyncMock()
    guild.get_channel = MagicMock(return_value=None)

    cog = SignupCog.__new__(SignupCog)
    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=MANAGER_ROLE,
            league_admin_role_id=admin_role_id,
            interaction_channel_id=111,
            log_channel_id=333,
        )
    )
    cfg = MagicMock()
    cfg.signup_channel_id = None
    cfg.base_role_id = None
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.save_config = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    bot.db_path = await _seed(tmp_path, admin_role_id=admin_role_id)
    cog.bot = bot

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = guild
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 7
    interaction.user.display_name = "manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    from tests.support.undecorate import undecorate

    await undecorate(SignupCog.signup_channel)(cog, interaction, channel)

    assert channel.edit.await_count == 1
    overwrites = channel.edit.await_args.kwargs["overwrites"]
    assert expected <= _overwrite_role_ids(overwrites)
    if admin_role_id is None:
        assert ADMIN_ROLE not in _overwrite_role_ids(overwrites)
