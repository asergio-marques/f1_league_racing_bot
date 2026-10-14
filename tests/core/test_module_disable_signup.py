"""Switching the signup module off, and everything that has to be stood down with it.

Issue #208. `ModuleCog._disable_signup` was uncovered. It is the most far-reaching of the
module toggles, because signup is the only module that holds a *channel's permissions*, an
in-flight wizard per driver, and scheduled jobs against each of them.

**The switch-off is a change on the queue** (#439 slice 6b, C71). `/module disable signup` is
acknowledged at once and asks `module.off:signup`, defined in signup's services as results' own
switch-off is in results': where the window is open, the close's jobs come first (each driver
returned and told, the button deleted, the closed notice posted); its last job, `switch_off`,
disarms every wizard's timers, deletes the configuration, turns the module off and audits it, in
one save. Nothing changes until the queue reaches it, and a step Discord refuses stops the queue
rather than being named and stepped over. The window's own close is pinned in
`tests/signup/test_window_changes.py`.

**Everything the module armed has to be disarmed, and nothing else.** A close timer, two
timers per open wizard, the permission overwrites on the signup channel. A timer left armed
fires days later against a module that is switched off: the inactivity timeout would
transition a driver whose signup no longer exists, and the channel-delete job would remove a
channel a league has since repurposed.

**Only the overwrites the bot applied are cleared, and by the command** (a permission, not a
message, so not a job). `/signup channel` sets five: everyone, the bot, the base role, the
interaction role and, since #116, the league admin role. The first four are what is reverted.
The admin role's is left standing, an allow for a role that already governs the league (#372,
closed as won't fix). A league that has added its own overwrites to that channel keeps them,
because the bot did not put them there and removing them would be the module reaching outside
itself on the way out.

**A permissions failure does not fail the disable.** Discord refuses an overwrite change for
reasons that have nothing to do with the toggle, and a module left half-disabled is worse than
a channel with a stale overwrite: one is repairable by hand, the other needs the toggle run
again in a state it may refuse.

**The channel is cleared; the time slots and question settings are kept** (issue #127). A
channel can be deleted or given another job while the module is off, so a re-enable asks for
it again; the slots and the three settings name nothing on Discord and stand again as they were,
as the signup specification requires.

**The league's two roles are not the module's configuration** (issue #276). The base role and
the driver role are core's, read from the server configuration, and survive the disable; only
the base role's overwrite on the signup channel goes.

The tests of the overwrites drive the command on a bot double with a real queue and run nothing
on it: the overwrites are the command's own. The rest run the switch-off on
`tests.support.season_league`'s league, its signup module on.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.module_cog import ModuleCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    member_interaction,
    queued_log_lines,
    run_queue,
    step_rows,
    tier_member,
    updated_reply,
)
from tests.support.season_league import (
    SIGNING_UP,
    SeasonLeague,
    setup_league,
    signing_up,
)
from tests.support.season_league import SIGNUP_CHANNEL as LEAGUE_SIGNUP_CHANNEL

SERVER_ID = 12208
SIGNUP_CHANNEL = 700
BASE_ROLE = 3001
INTERACTION_ROLE = 900
ACTOR_ID = 77

#: The change `/module disable signup` asks.
MODULE_OFF_KIND = "module.off:signup"
#: The two timers each open wizard holds.
INACTIVITY_TIMER = "signup_inactivity"
DELETE_TIMER = "signup_channel_delete"
#: A second driver part-way through signing up.
OTHER = 107

NO_MODULE_OFF_CHANGE = (
    "#439: /module disable signup turns the module off itself, off the queue, with no "
    "module.off:signup change"
)
NO_DISARM = (
    "#439: the scheduler service has no disarm, and a wizard's timers are cancelled by job id"
)


# ---------------------------------------------------------------------------
# A bot double, for the command's own work on the channel's overwrites
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
    """The command on a bot double whose services are mocks, with a real change queue attached
    that nothing runs: what these tests read is the command's own work."""
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
    attach_queue(bot, db_path, now=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc))

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
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        + interaction.edit_original_response.await_args_list
    )


async def _disable(cog, interaction) -> None:
    await cog._disable_signup(interaction)


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
# The league, for the switch-off the queue runs
# ---------------------------------------------------------------------------


async def _league(tmp_path: Any, *drivers: int, open_window: bool = False,
                  configured: bool = True) -> SeasonLeague:
    """`setup_league` with the signup module on. *open_window*: the window open in the signup
    channel (620), its Sign Up button standing; otherwise, where *configured*, the module's
    configuration names the signup channel with the window closed, and where not it holds no
    configuration at all. Each of *drivers* is part-way through signing up (Pending Signup
    Completion, their wizard at its notes question), their own signup channel standing."""
    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=open_window)
    if not open_window:
        await league.switch("signup", True)
        if configured:
            await league.write(
                "INSERT OR REPLACE INTO signup_module_config (id, signup_channel_id, "
                "signups_open) VALUES (1, ?, 0)",
                LEAGUE_SIGNUP_CHANNEL,
            )
    for user_id in drivers:
        await signing_up(league, user_id, state="PENDING_SIGNUP_COMPLETION")
        await league.write(
            "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NOTES' "
            "WHERE discord_user_id = ?",
            str(user_id),
        )
    return league


def _manager(league: SeasonLeague) -> Any:
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=ACTOR_ID))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name="module disable")
    return interaction


async def _switch_off(league: SeasonLeague) -> Any:
    """League manager 77 runs `/module disable signup`; gives his interaction."""
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = league.bot
    interaction = _manager(league)
    await cog._disable_signup(interaction)
    return interaction


async def _off_jobs(league: SeasonLeague) -> list[dict[str, Any]]:
    [change] = [row for row in await change_rows(league.db_path)
                if row["kind"] == MODULE_OFF_KIND]
    return await step_rows(league.db_path, change["id"])


async def _disables(league: SeasonLeague) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT change_type, old_value FROM audit_entries WHERE change_type = 'MODULE_DISABLE'"
    )


async def _log_lines(league: SeasonLeague) -> list[str]:
    return [str(line) for line in league.bot.log_channel.sent] + await queued_log_lines(
        league.db_path
    )


# ---------------------------------------------------------------------------
# The module goes off
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_the_module_is_disabled(tmp_path):
    """Manager 77 runs `/module disable signup` with the window closed. He is answered at once
    with an under-way line and the module is still on; when the queue runs, the module is off and
    his acknowledgement is updated to say it is disabled."""
    league = await _league(tmp_path)

    interaction = await _switch_off(league)

    assert acknowledgement(interaction).startswith("⏳")
    assert await league.bot.module_service.is_signup_enabled()

    await run_queue(league.bot)

    assert not await league.bot.module_service.is_signup_enabled()
    assert updated_reply(interaction).startswith("✅")
    assert "disabled" in updated_reply(interaction)


async def test_disabling_twice_does_no_work(tmp_path):
    """The second call would close signups that are already closed and audit an event that did
    not happen: the manager is told the module is already disabled, and nothing is done or
    audited."""
    db_path = await _make_db(tmp_path, name="disable_twice")
    cog = _make_cog(db_path, enabled=False)
    interaction = _interaction()

    await _disable(cog, interaction)

    assert "already disabled" in _replied(interaction)
    cog.bot.signup_module_service.delete_config.assert_not_awaited()
    assert await _audit(db_path) == []
    assert [row for row in await change_rows(db_path)
            if row["kind"] == MODULE_OFF_KIND and row["state"] == "DONE"] == []


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_the_disable_is_audited(tmp_path):
    """Nothing is audited until the queue runs; then one MODULE_DISABLE row names the module."""
    league = await _league(tmp_path)

    await _switch_off(league)

    assert await _disables(league) == []

    await run_queue(league.bot)

    rows = await _disables(league)
    assert len(rows) == 1
    assert json.loads(rows[0]["old_value"]) == {"module": "signup"}


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_the_disable_is_logged(tmp_path):
    """Kimi is part-way through signing up when the module is disabled with the window open. Once
    the queue has run the switch-off, closing the window as its jobs, a line in the log channel
    names `/module disable signup`; until it runs, the module is still on."""
    league = await _league(tmp_path, SIGNING_UP, open_window=True)

    await _switch_off(league)

    assert await league.bot.module_service.is_signup_enabled()

    await run_queue(league.bot)

    assert not await league.bot.module_service.is_signup_enabled()
    assert any("/module disable signup" in line for line in await _log_lines(league))


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_the_configuration_is_cleared(tmp_path):
    """The configuration row, which holds the channel, is deleted by the switch-off's job: it
    stands until the queue runs, and is gone after, the acknowledgement updated to say the
    channel has been cleared. What survives it is pinned by
    `test_time_slots_and_settings_stand_again_after_a_re_enable`."""
    league = await _league(tmp_path)

    interaction = await _switch_off(league)

    assert await league.rows("SELECT * FROM signup_module_config") != []

    await run_queue(league.bot)

    assert await league.rows("SELECT * FROM signup_module_config") == []
    assert "Its channel has been cleared" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_disabling_signup_keeps_both_roles(tmp_path):
    """They are the league's, not the module's (issue #276). Disabling signup deleted them
    with its configuration row, and a league that ran on without signups lost the base role
    another module reads and the driver role its season's end revokes. The base role (3001) and
    the driver role (3002) are set; once the switch-off has run both stand, the configuration is
    gone (it stands until the queue runs) and the acknowledgement says the two roles are kept."""
    league = await _league(tmp_path)
    await league.write("UPDATE server_configs SET base_role_id = ?, driver_role_id = 3002",
                       BASE_ROLE)

    interaction = await _switch_off(league)

    assert await league.bot.signup_module_service.get_config() is not None

    await run_queue(league.bot)

    config = await league.bot.config_service.get_server_config()
    assert (config.base_role_id, config.driver_role_id) == (BASE_ROLE, 3002)
    assert await league.bot.signup_module_service.get_config() is None
    assert "base role and driver role" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_time_slots_and_settings_stand_again_after_a_re_enable(tmp_path):
    """Only the channel goes (issue #127). The slots and the three question settings live in
    tables no key joins to the configuration row, so nothing but the rule keeps them: a
    disable that cleared them too would pass every other test here. Every setting is away from
    its default and two slots are saved; the module is switched off through the queue (still on
    until it runs) and then
    enabled again: the settings and slots are as they were, the configuration naming no
    channel."""
    from leaguebot.signup.models.signup_module import SignupModuleSettings

    league = await _league(tmp_path)
    signup = league.bot.signup_module_service
    # Every setting away from its default, so a reset to defaults cannot pass for kept.
    await signup.save_settings(SignupModuleSettings(
        nationality_required=False, time_type="SHORT_QUALIFICATION", time_image_required=False,
    ))
    await signup.add_slot(3, "19:00")
    await signup.add_slot(6, "21:30")
    settings, slots = await signup.get_settings(), await signup.get_slots()

    await _switch_off(league)
    assert await league.bot.module_service.is_signup_enabled()
    await run_queue(league.bot)
    assert not await league.bot.module_service.is_signup_enabled()
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = league.bot
    await cog._enable_signup(_manager(league))

    assert await league.bot.module_service.is_signup_enabled()
    assert await signup.get_settings() == settings
    assert await signup.get_slots() == slots
    config = await signup.get_config()
    assert config is not None and config.signup_channel_id is None


# ---------------------------------------------------------------------------
# Open signups, and the timers behind them
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_closed_signups_are_not_force_closed_again(tmp_path):
    """Closing transitions every in-progress driver and posts into their channels; running it
    over an already-closed signup would notice nobody and post nothing. With the window closed,
    the switch-off's only job is `switch_off`."""
    league = await _league(tmp_path)

    await _switch_off(league)
    await run_queue(league.bot)

    assert [job["name"] for job in await _off_jobs(league)] == ["switch_off"]


async def test_the_close_timer_is_cancelled(tmp_path):
    """A scheduled automatic close firing against a disabled module would close the window over
    a league that has already stood the module down: once the switch-off has run, the close
    timer is cancelled."""
    league = await _league(tmp_path)

    await _switch_off(league)
    await run_queue(league.bot)

    league.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_every_open_wizards_jobs_are_cancelled(tmp_path):
    """Two timers per driver. Left armed, the inactivity timeout would transition a driver whose
    signup no longer exists, and the delete timer would remove a channel a league has since
    repurposed. Kimi (105) and driver 107 are part-way through signing up, the window closed:
    once the switch-off has run, each one's inactivity and channel-deletion timers are
    disarmed."""
    league = await _league(tmp_path, SIGNING_UP, OTHER)

    await _switch_off(league)
    await run_queue(league.bot)

    disarmed = {call.args[:2] for call in league.bot.scheduler_service.disarm.call_args_list}
    assert disarmed >= {
        (INACTIVITY_TIMER, str(SIGNING_UP)),
        (DELETE_TIMER, str(SIGNING_UP)),
        (INACTIVITY_TIMER, str(OTHER)),
        (DELETE_TIMER, str(OTHER)),
    }


@pytest.mark.xfail(strict=True, reason=NO_DISARM)
async def test_a_job_that_has_already_fired_is_stepped_over():
    """APScheduler raises for a job it does not hold, and a wizard whose timeout fired an hour
    ago is ordinary. Driver 101's two timers have both fired: disarming each, as the switch-off
    does, raises nothing."""
    from apscheduler.jobstores.base import JobLookupError

    from leaguebot.core.services.scheduler_service import SchedulerService

    # The real scheduler service, over an APScheduler that no longer holds the job.
    scheduler = SchedulerService.__new__(SchedulerService)
    scheduler._scheduler = MagicMock()
    scheduler._scheduler.get_job = MagicMock(return_value=None)
    scheduler._scheduler.remove_job = MagicMock(side_effect=JobLookupError("no job"))

    scheduler.disarm(INACTIVITY_TIMER, "101")
    scheduler.disarm(DELETE_TIMER, "101")


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_a_league_with_no_configuration_still_disables(tmp_path):
    """A module toggled on and never configured: there are no wizards, no channel and no timers,
    and the flag still has to come down. The switch-off's only job is `switch_off`, the module is
    off and one MODULE_DISABLE is audited."""
    league = await _league(tmp_path, configured=False)

    await _switch_off(league)
    await run_queue(league.bot)

    assert [job["name"] for job in await _off_jobs(league)] == ["switch_off"]
    assert not await league.bot.module_service.is_signup_enabled()
    assert len(await _disables(league)) == 1


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


async def test_a_server_with_no_configuration_row_reverts_the_rest(tmp_path):
    """The interaction role comes from the server config, and a server without one is a
    broken state that must not stop the module being disabled."""
    db_path = await _make_db(tmp_path, name="disable_noservercfg")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, guild=guild, server_config=None)

    await _disable(cog, _interaction())

    assert INTERACTION_ROLE not in _reverted(guild)
    assert 1 in _reverted(guild)


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_a_permissions_failure_does_not_fail_the_disable(tmp_path):
    """A module left half-disabled is worse than a channel with a stale overwrite: one is
    repairable by hand, the other needs the toggle run again in a state it may refuse. Discord
    refuses every overwrite change on the signup channel: the manager is answered with an
    under-way line, the module still on; once the queue has run, the module is off and the
    acknowledgement says it is disabled."""
    league = await _league(tmp_path)
    refusing = _channel(set_permissions_fails=True)
    finding = league.guild.get_channel.side_effect
    league.guild.get_channel = MagicMock(
        side_effect=lambda cid: refusing if int(cid) == LEAGUE_SIGNUP_CHANNEL else finding(cid)
    )

    interaction = await _switch_off(league)

    assert acknowledgement(interaction).startswith("⏳")
    assert await league.bot.module_service.is_signup_enabled()

    await run_queue(league.bot)

    assert not await league.bot.module_service.is_signup_enabled()
    assert "disabled" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_a_deleted_signup_channel_does_not_stop_the_disable(tmp_path):
    """The module's configuration names the signup channel (620), which has since been deleted
    from the server: the module is still on until the queue runs, and off once it has."""
    league = await _league(tmp_path)

    await _switch_off(league)

    assert await league.bot.module_service.is_signup_enabled()

    await run_queue(league.bot)

    assert league.guild.get_channel(LEAGUE_SIGNUP_CHANNEL) is None
    assert not await league.bot.module_service.is_signup_enabled()


async def test_a_league_with_no_signup_channel_reverts_nothing(tmp_path):
    """Nothing was ever applied, so there is nothing to revert, and asking Discord for
    channel `None` would raise."""
    db_path = await _make_db(tmp_path, name="disable_nochannel")
    guild = _guild(channel=_channel())
    cog = _make_cog(db_path, config=_config(channel=None), guild=guild)

    await _disable(cog, _interaction())

    guild.get_channel.assert_not_called()


@pytest.mark.xfail(strict=True, reason=NO_MODULE_OFF_CHANGE)
async def test_a_guild_the_bot_has_left_does_not_stop_the_disable(tmp_path):
    """The database half can be put right without Discord: with the league's server out of
    the bot's reach, the module is still on until the queue runs, and the switch-off then turns
    it off and audits it."""
    league = await _league(tmp_path)
    league.bot.get_guild = MagicMock(return_value=None)

    await _switch_off(league)

    assert await league.bot.module_service.is_signup_enabled()

    await run_queue(league.bot)

    assert not await league.bot.module_service.is_signup_enabled()
    assert len(await _disables(league)) == 1
