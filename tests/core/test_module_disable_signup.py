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

**Only the overwrites the bot applied are cleared.** `/signup channel` sets five — everyone,
the bot, the base role, the interaction role and, since #116, the league admin role — and the
first four are what is reverted. The admin role's is left standing, an allow for a role that
already governs the league (#372, closed as won't fix). A league that has added its own
overwrites to that channel keeps them, because the bot did not put them there and removing them
would be the module reaching outside itself on the way out.

**A permissions failure does not fail the disable.** Discord refuses an overwrite change for
reasons that have nothing to do with the toggle, and a module left half-disabled is worse than
a channel with a stale overwrite — one is repairable by hand, the other needs the toggle run
again in a state it may refuse.

**The channel is cleared; the time slots and question settings are kept** (issue #127). A
channel can be deleted or given another job while the module is off, so a re-enable asks for
it again; the slots and the three settings name nothing on Discord and stand again as they were,
as the signup specification requires.

**The league's two roles are not the module's configuration** (issue #276). The base role and
the driver role are core's, read from the server configuration, and survive the disable; only
the base role's overwrite on the signup channel goes.
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

from leaguebot.core.cogs.module_cog import ModuleCog  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402

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


def _config(*, signups_open: bool = False, channel=SIGNUP_CHANNEL):
    return SimpleNamespace(
        server_id=SERVER_ID,
        signups_open=signups_open,
        signup_channel_id=channel,
    )


def _server_config(*, base_role=BASE_ROLE):
    return SimpleNamespace(interaction_role_id=INTERACTION_ROLE, base_role_id=base_role)


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
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
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
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(
        return_value=_server_config() if server_config is _UNSET else server_config
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
    with patch("leaguebot.core.cogs.module_cog.execute_forced_close", new=AsyncMock()) as close:
        await cog._disable_signup(interaction)
    return close


async def _audit(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value FROM audit_entries",
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

    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(False)
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

    assert "/module disable signup" in str(cog.bot.output_router.post_log.await_args.args[0])


async def test_the_configuration_is_cleared(tmp_path):
    """The configuration row, which holds the channel, is deleted. What survives it is
    pinned by `test_time_slots_and_settings_stand_again_after_a_re_enable`."""
    db_path = await _make_db(tmp_path, name="disable_config")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _disable(cog, interaction)

    cog.bot.signup_module_service.delete_config.assert_awaited_once_with()
    assert "Its channel has been cleared" in _replied(interaction)


async def test_disabling_signup_keeps_both_roles(tmp_path):
    """They are the league's, not the module's (issue #276). Disabling signup deleted them
    with its configuration row, and a league that ran on without signups lost the base role
    another module reads and the driver role its season's end revokes."""
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    db_path = await _make_db(tmp_path, name="disable_keeps_roles")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET base_role_id = ?, driver_role_id = 3002", (BASE_ROLE,)
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, ?)",
            (SIGNUP_CHANNEL,),
        )
        await db.commit()
    cog = _make_cog(db_path)
    cog.bot.config_service = ConfigService(db_path)
    cog.bot.signup_module_service = SignupModuleService(db_path)
    interaction = _interaction()

    await _disable(cog, interaction)

    config = await cog.bot.config_service.get_server_config()
    assert (config.base_role_id, config.driver_role_id) == (BASE_ROLE, 3002)
    assert await cog.bot.signup_module_service.get_config() is None
    assert "base role and driver role" in _replied(interaction)


async def test_time_slots_and_settings_stand_again_after_a_re_enable(tmp_path):
    """Only the channel goes (issue #127). The slots and the three question settings live in
    tables no key joins to the configuration row, so nothing but the rule keeps them: a
    disable that cleared them too would pass every other test here."""
    from leaguebot.signup.models.signup_module import SignupModuleConfig, SignupModuleSettings
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    db_path = await _make_db(tmp_path, name="disable_keeps_slots")
    cog = _make_cog(db_path)
    cog.bot.config_service = ConfigService(db_path)
    cog.bot.module_service = ModuleService(db_path)
    cog.bot.signup_module_service = SignupModuleService(db_path)
    signup = cog.bot.signup_module_service
    await cog.bot.module_service.set_signup_enabled(True)
    await signup.save_config(SignupModuleConfig(
        signup_channel_id=SIGNUP_CHANNEL, signups_open=False,
        signup_button_message_id=None, selected_tracks=[],
    ))
    # Every setting away from its default, so a reset to defaults cannot pass for kept.
    await signup.save_settings(SignupModuleSettings(
        nationality_required=False, time_type="SHORT_QUALIFICATION", time_image_required=False,
    ))
    await signup.add_slot(3, "19:00")
    await signup.add_slot(6, "21:30")
    settings, slots = await signup.get_settings(), await signup.get_slots()

    await _disable(cog, _interaction())
    await cog._enable_signup(_interaction())

    assert await cog.bot.module_service.is_signup_enabled()
    assert await signup.get_settings() == settings
    assert await signup.get_slots() == slots
    config = await signup.get_config()
    assert config is not None and config.signup_channel_id is None


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

    cog.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()


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
        f"wizard_inactivity_101",
        f"wizard_channel_delete_101",
        f"wizard_inactivity_102",
        f"wizard_channel_delete_102",
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
    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(False)
    assert len(await _audit(db_path)) == 1


# ---------------------------------------------------------------------------
# The channel's permissions
# ---------------------------------------------------------------------------


async def test_the_overwrites_the_bot_applied_are_cleared(tmp_path):
    """Everyone's, the bot's, the base role's and the interaction role's are reverted. The
    league admin role's, which `/signup channel` sets too, is left (#372)."""
    db_path = await _make_db(tmp_path, name="disable_perms")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, guild=guild)

    await _disable(cog, _interaction())

    assert set(_reverted(guild)) == {1, BASE_ROLE, INTERACTION_ROLE} | {guild.me.id}


async def test_a_league_with_no_base_role_reverts_the_rest(tmp_path):
    """Configuring one is optional; the other three overwrites were still applied."""
    db_path = await _make_db(tmp_path, name="disable_nobase")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, server_config=_server_config(base_role=None), guild=guild)

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

    cog.bot.module_service.set_signup_enabled.assert_awaited_once_with(False)
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
