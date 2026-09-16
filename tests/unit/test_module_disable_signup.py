"""Switching the signup module off, and everything that has to be stood down with it.

Issue #208. `ModuleCog._disable_signup` was uncovered. It is the most far-reaching of the
module toggles, because signup is the only module that holds a *channel's permissions*, an
in-flight wizard per driver, and scheduled jobs against each of them.

**Everything the module armed has to be disarmed, and nothing else.** A close timer, two
APScheduler jobs per open wizard, the permission overwrites on the signup channel. A job left
armed fires days later against a module that is switched off — the inactivity timeout would
transition a driver whose signup no longer exists, and the channel-delete job would remove a
channel a league has since repurposed.

**Open signups are force-closed first.** Disabling with signups open would leave drivers
part-way through a wizard with no way to finish and no notice that it had ended; the forced
close is what tells them.

**Only the overwrites the bot applied are cleared.** `/signup channel` sets four — everyone,
the bot, the base role and the interaction role — and those four are what is reverted. A league
that has added its own overwrites to that channel keeps them, because the bot did not put them
there and removing them would be the module reaching outside itself on the way out.

**A permissions failure does not fail the disable.** Discord refuses an overwrite change for
reasons that have nothing to do with the toggle, and a module left half-disabled is worse than
a channel with a stale overwrite — one is repairable by hand, the other needs the toggle run
again in a state it may refuse.

**The configuration is deleted, not kept.** Signup is the one module whose disable clears its
configuration, and re-enabling starts from nothing — which is the opposite of the image module,
where a re-enable is deliberately lossless. Worth stating, because the two sit in the same file.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import ModuleCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 12208
SIGNUP_CHANNEL = 700
BASE_ROLE = 3001
INTERACTION_ROLE = 900
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "disable_signup") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, 100, 101)",
            (SERVER_ID, INTERACTION_ROLE),
        )
        await db.commit()
    return db_path


def _config(*, signups_open: bool = False, channel=SIGNUP_CHANNEL, base_role=BASE_ROLE):
    return SimpleNamespace(
        server_id=SERVER_ID,
        signups_open=signups_open,
        signup_channel_id=channel,
        base_role_id=base_role,
    )


def _channel(*, set_permissions_fails: bool = False):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = SIGNUP_CHANNEL
    channel.set_permissions = AsyncMock(
        side_effect=RuntimeError("missing permissions") if set_permissions_fails else None
    )
    return channel


def _role(role_id: int, name: str = "role"):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    return role


def _guild(*, channel=None, roles=None, missing_channel: bool = False):
    roles = roles if roles is not None else {BASE_ROLE: _role(BASE_ROLE, "Drivers"),
                                             INTERACTION_ROLE: _role(INTERACTION_ROLE, "Admins")}
    guild = MagicMock()
    guild.default_role = _role(1, "@everyone")
    guild.me = MagicMock()
    guild.get_channel = MagicMock(return_value=None if missing_channel else channel)
    guild.get_role = MagicMock(side_effect=lambda rid: roles.get(rid))
    return guild


_UNSET = object()


def _make_cog(
    db_path: str,
    *,
    enabled: bool = True,
    config=_UNSET,
    wizards=None,
    guild=_UNSET,
    server_config=_UNSET,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_signup_enabled = AsyncMock(return_value=enabled)
    bot.module_service.set_signup_enabled = AsyncMock(return_value=None)
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_config = AsyncMock(
        return_value=_config() if config is _UNSET else config
    )
    bot.signup_module_service.delete_config = AsyncMock(return_value=None)
    bot.signup_module_service.get_all_active_wizards = AsyncMock(
        return_value=wizards if wizards is not None else []
    )
    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_signup_close_timer = MagicMock(return_value=None)
    bot.scheduler_service._scheduler = MagicMock()
    bot.scheduler_service._scheduler.remove_job = MagicMock(return_value=None)
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(interaction_role_id=INTERACTION_ROLE)
        if server_config is _UNSET
        else server_config
    )
    bot.get_guild = MagicMock(
        return_value=_guild(channel=_channel()) if guild is _UNSET else guild
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _disable(cog, interaction):
    with patch("cogs.module_cog.execute_forced_close", new=AsyncMock()) as close:
        await cog._disable_signup(interaction, SERVER_ID)
    return close


async def _audit(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value FROM audit_entries WHERE server_id = ?",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


def _reverted(guild) -> list[int]:
    channel = guild.get_channel(SIGNUP_CHANNEL)
    return [
        call.args[0].id
        for call in channel.set_permissions.await_args_list
        if call.kwargs.get("overwrite") is None
    ]


# ---------------------------------------------------------------------------
# The module goes off
# ---------------------------------------------------------------------------


async def test_the_module_is_disabled(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _disable(cog, interaction)

    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(SERVER_ID, False)
    assert "disabled" in _replied(interaction)


async def test_disabling_twice_does_no_work(tmp_path):
    """The second call would force-close signups that are already closed and audit an event
    that did not happen."""
    db_path = await _make_db(tmp_path, name="disable_twice")
    cog = _make_cog(db_path, enabled=False)
    interaction = _interaction()

    close = await _disable(cog, interaction)

    assert "already disabled" in _replied(interaction)
    close.assert_not_awaited()
    cog.bot.signup_module_service.delete_config.assert_not_awaited()
    assert await _audit(db_path) == []


async def test_the_disable_is_audited(tmp_path):
    db_path = await _make_db(tmp_path, name="disable_audit")
    cog = _make_cog(db_path)

    await _disable(cog, _interaction())

    rows = await _audit(db_path)
    assert [r["change_type"] for r in rows] == ["MODULE_DISABLE"]
    assert json.loads(rows[0]["old_value"]) == {"module": "signup"}


async def test_the_disable_is_logged(tmp_path):
    db_path = await _make_db(tmp_path, name="disable_log")
    cog = _make_cog(db_path)

    await _disable(cog, _interaction())

    assert "/module disable signup" in str(cog.bot.output_router.post_log.await_args.args[1])


async def test_the_configuration_is_cleared(tmp_path):
    """Signup is the one module whose disable clears its configuration — the opposite of
    the image module, where a re-enable is deliberately lossless."""
    db_path = await _make_db(tmp_path, name="disable_config")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _disable(cog, interaction)

    cog.bot.signup_module_service.delete_config.assert_awaited_once_with(SERVER_ID)
    assert "configuration has been cleared" in _replied(interaction)


# ---------------------------------------------------------------------------
# Open signups, and the timers behind them
# ---------------------------------------------------------------------------


async def test_open_signups_are_force_closed_first(tmp_path):
    """Disabling with signups open would leave drivers part-way through a wizard with no
    way to finish and no notice that it had ended."""
    db_path = await _make_db(tmp_path, name="disable_open")
    cog = _make_cog(db_path, config=_config(signups_open=True))

    close = await _disable(cog, _interaction())

    close.assert_awaited_once()
    assert close.await_args.kwargs["audit_action"] == "SIGNUP_FORCE_CLOSE"


async def test_closed_signups_are_not_force_closed_again(tmp_path):
    """It transitions every in-progress driver and posts into their channels; running it
    over an already-closed signup would notice nobody and post nothing, at the cost of a
    sweep over every driver profile on the server."""
    db_path = await _make_db(tmp_path, name="disable_closed")
    cog = _make_cog(db_path, config=_config(signups_open=False))

    close = await _disable(cog, _interaction())

    close.assert_not_awaited()


async def test_the_close_timer_is_cancelled(tmp_path):
    """A scheduled automatic close firing against a disabled module would run the
    force-close path over a league that has already stood the module down."""
    db_path = await _make_db(tmp_path, name="disable_timer")
    cog = _make_cog(db_path)

    await _disable(cog, _interaction())

    cog.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with(SERVER_ID)


async def test_every_open_wizards_jobs_are_cancelled(tmp_path):
    """Two per driver. Left armed, the inactivity timeout would transition a driver whose
    signup no longer exists, and the delete job would remove a channel a league has since
    repurposed."""
    db_path = await _make_db(tmp_path, name="disable_jobs")
    cog = _make_cog(
        db_path,
        wizards=[
            SimpleNamespace(discord_user_id="101"),
            SimpleNamespace(discord_user_id="102"),
        ],
    )

    await _disable(cog, _interaction())

    removed = {
        call.args[0] for call in cog.bot.scheduler_service._scheduler.remove_job.call_args_list
    }
    assert removed == {
        f"wizard_inactivity_{SERVER_ID}_101",
        f"wizard_channel_delete_{SERVER_ID}_101",
        f"wizard_inactivity_{SERVER_ID}_102",
        f"wizard_channel_delete_{SERVER_ID}_102",
    }


async def test_a_job_that_has_already_fired_is_stepped_over(tmp_path):
    """APScheduler raises for a job id it does not hold, and a wizard whose timeout fired
    an hour ago is ordinary."""
    db_path = await _make_db(tmp_path, name="disable_jobgone")
    cog = _make_cog(db_path, wizards=[SimpleNamespace(discord_user_id="101")])
    cog.bot.scheduler_service._scheduler.remove_job = MagicMock(
        side_effect=Exception("no such job")
    )

    await _disable(cog, _interaction())

    cog.bot.module_service.set_signup_enabled.assert_awaited_once()


async def test_a_league_with_no_configuration_still_disables(tmp_path):
    """A module toggled on and never configured; there are no wizards, no channel and no
    timers, and the flag still has to come down."""
    db_path = await _make_db(tmp_path, name="disable_noconfig")
    cog = _make_cog(db_path, config=None)
    interaction = _interaction()

    close = await _disable(cog, interaction)

    close.assert_not_awaited()
    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(SERVER_ID, False)
    assert len(await _audit(db_path)) == 1


# ---------------------------------------------------------------------------
# The channel's permissions
# ---------------------------------------------------------------------------


async def test_the_overwrites_the_bot_applied_are_cleared(tmp_path):
    """`/signup channel` sets four — everyone, the bot, the base role and the interaction
    role — and those four are what is reverted."""
    db_path = await _make_db(tmp_path, name="disable_perms")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, guild=guild)

    await _disable(cog, _interaction())

    assert set(_reverted(guild)) == {1, BASE_ROLE, INTERACTION_ROLE} | {guild.me.id}


async def test_a_league_with_no_base_role_reverts_the_rest(tmp_path):
    """Configuring one is optional; the other three overwrites were still applied."""
    db_path = await _make_db(tmp_path, name="disable_nobase")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, config=_config(base_role=None), guild=guild)

    await _disable(cog, _interaction())

    assert BASE_ROLE not in _reverted(guild)
    assert 1 in _reverted(guild)


async def test_a_base_role_that_has_been_deleted_is_stepped_over(tmp_path):
    """Its overwrite went with it, and the others still need reverting."""
    db_path = await _make_db(tmp_path, name="disable_basegone")
    guild = _guild(channel=_channel(), roles={INTERACTION_ROLE: _role(INTERACTION_ROLE)})
    cog = _make_cog(db_path, guild=guild)

    await _disable(cog, _interaction())

    assert INTERACTION_ROLE in _reverted(guild)
    cog.bot.module_service.set_signup_enabled.assert_awaited_once()


async def test_a_server_with_no_configuration_row_reverts_the_rest(tmp_path):
    """The interaction role comes from the server config, and a server without one is a
    broken state that must not stop the module being disabled."""
    db_path = await _make_db(tmp_path, name="disable_noservercfg")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, guild=guild, server_config=None)

    await _disable(cog, _interaction())

    assert INTERACTION_ROLE not in _reverted(guild)
    assert 1 in _reverted(guild)


async def test_a_permissions_failure_does_not_fail_the_disable(tmp_path):
    """A module left half-disabled is worse than a channel with a stale overwrite: one is
    repairable by hand, the other needs the toggle run again in a state it may refuse."""
    db_path = await _make_db(tmp_path, name="disable_permfail")
    cog = _make_cog(db_path, guild=_guild(channel=_channel(set_permissions_fails=True)))
    interaction = _interaction()

    await _disable(cog, interaction)

    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(SERVER_ID, False)
    assert "disabled" in _replied(interaction)


async def test_a_deleted_signup_channel_does_not_stop_the_disable(tmp_path):
    db_path = await _make_db(tmp_path, name="disable_chgone")
    cog = _make_cog(db_path, guild=_guild(missing_channel=True))

    await _disable(cog, _interaction())

    cog.bot.module_service.set_signup_enabled.assert_awaited_once()


async def test_a_league_with_no_signup_channel_reverts_nothing(tmp_path):
    """Nothing was ever applied, so there is nothing to revert — and asking Discord for
    channel `None` would raise."""
    db_path = await _make_db(tmp_path, name="disable_nochannel")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, config=_config(channel=None), guild=guild)

    await _disable(cog, _interaction())

    guild.get_channel.assert_not_called()


async def test_a_guild_the_bot_has_left_does_not_stop_the_disable(tmp_path):
    """The database half can be put right without Discord."""
    db_path = await _make_db(tmp_path, name="disable_noguild")
    cog = _make_cog(db_path, guild=None)

    await _disable(cog, _interaction())

    cog.bot.module_service.set_signup_enabled.assert_awaited_once()
    assert len(await _audit(db_path)) == 1


async def test_the_disable_defers_before_working(tmp_path):
    """Force-closing signups and clearing four overwrites is well past three seconds."""
    db_path = await _make_db(tmp_path, name="disable_defer")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _disable(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()
