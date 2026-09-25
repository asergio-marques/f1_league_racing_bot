"""The league's two roles, and what changing one has to move with it (issue #276).

`/bot base-role` and `/bot driver-role` look like a pair and are not: one governs who can *see*
the signup channel, the other is granted to a driver when their signup is approved. Both were
the signup module's until a second module needed the base role; they are core's now, stored on
the server configuration, and survive the signup module being disabled.

**The base role is a channel permission, so changing it has to move the overwrite.** The signup
channel is visible to the base role and to nobody else; setting a new role without granting it
the overwrite would leave a league whose members cannot see the channel they are told to sign up
in, with nothing in the bot's replies to suggest why. And the *old* role's overwrite is removed
in the same breath, or the previous role keeps access it is no longer entitled to —
`test_the_previous_role_loses_its_access` is the half most easily forgotten, because nothing
visibly breaks when it is.

**Re-setting the same role does not remove its own overwrite.** The removal and the grant are
two calls, and a naive "remove the old, add the new" on the same role would briefly revoke and
restore it — or, if the grant failed in between, revoke it outright.

**The driver role touches no channel at all.** It is granted to a person, not applied to a
place, so it writes the configuration and stops. Tested explicitly because the two commands
share a body and the obvious tidy-up is to let the channel work run for both.

**A permissions failure does not fail the command.** Discord refuses an overwrite for reasons
that have nothing to do with the setting — a role above the bot's own, a channel it was removed
from — and the configured role is still the right one to store. The channel is repairable by
hand; a half-written configuration is not obviously repairable at all.

**Both are audited with the role they replaced.** A league that finds its members locked out
needs to know which role used to hold the access, and it is not recoverable from anywhere else
once the configuration is overwritten.

**Both are fixed once a season's configuration is confirmed.** A driver role changed mid-season
would leave every current driver holding the old one for good, the season's end revoking only
the new.

**Save a role deleted from the server** (#374), which nobody holds any longer. It may be replaced
while fixed, and the replacement is given to every driver — the base role too, the one time the
bot grants it — for without it a season could open no signup window until it ended.
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.bot_cog import BotCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.signup.services.signup_module_service import SignupModuleService
from tests.support.undecorate import undecorate

SERVER_ID = 27601
SIGNUP_CHANNEL = 700
OLD_ROLE = 3001
NEW_ROLE = 3002
ACTOR_ID = 77

#: Passed as *signup_channel* for a league whose signup module is not enabled at all.
NO_SIGNUP = object()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    base_role: int | None = OLD_ROLE,
    driver_role: int | None = OLD_ROLE,
    signup_channel=SIGNUP_CHANNEL,
    stage: str | None = None,
    claimed: bool = True,
    drivers: tuple[tuple[str, str], ...] = (),
) -> str:
    db_path = os.path.join(str(tmp_path), "league_roles.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, base_role_id, driver_role_id) "
            "VALUES (?, 900, 100, 101, ?, ?)",
            (SERVER_ID if claimed else None, base_role, driver_role),
        )
        if signup_channel is not NO_SIGNUP:
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, ?)",
                (signup_channel,),
            )
        if stage is not None:
            # A trigger holds the stage to its status: Configuration is a SETUP season.
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-01-01', ?, 4, ?)",
                ("SETUP" if stage == "CONFIGURATION" else "ACTIVE", stage),
            )
        for user_id, state in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, ?)",
                (user_id, state),
            )
        await db.commit()
    return db_path


def _make_cog(db_path: str) -> BotCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.placement_service = PlacementService(db_path)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return BotCog(bot)


def _role(role_id: int = NEW_ROLE, name: str = "Drivers", *, above_the_bot: bool = False):
    """A role the bot can grant, unless *above_the_bot* says otherwise."""
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def _interaction(*, channel_missing: bool = False, set_permissions_fails: bool = False,
                 old_role_missing: bool = False):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    channel = MagicMock(spec=discord.TextChannel)
    channel.id = SIGNUP_CHANNEL
    channel.set_permissions = AsyncMock(
        side_effect=RuntimeError("missing permissions") if set_permissions_fails else None
    )
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=None if channel_missing else channel)
    guild.get_role = MagicMock(
        return_value=None if old_role_missing else _role(OLD_ROLE, "Old members")
    )
    interaction.guild = guild
    interaction._channel = channel
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _base_role(cog, interaction, *, role=None):
    return await undecorate(BotCog.handle_base_role)(cog, interaction, role or _role())


async def _driver_role(cog, interaction, *, role=None):
    return await undecorate(BotCog.handle_driver_role)(cog, interaction, role or _role())


async def _stored(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM server_configs")
        return dict(await cursor.fetchone())


async def _audit_rows(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]


def _permission_calls(interaction) -> list:
    return interaction._channel.set_permissions.await_args_list


_BOTH = [
    pytest.param(_base_role, "base_role_id", "BASE_ROLE_SET", id="base"),
    pytest.param(_driver_role, "driver_role_id", "DRIVER_ROLE_SET", id="driver"),
]


# ---------------------------------------------------------------------------
# Both commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_the_role_is_stored_as_the_league_s(tmp_path, run, column, change_type):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = await _stored(db_path)

    await run(cog, _interaction())

    after = await _stored(db_path)
    assert after[column] == NEW_ROLE
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_league_with_signup_disabled_still_sets_the_role(
    tmp_path, run, column, change_type
):
    """The roles are the league's, not the signup module's: a league that runs without
    signups still has members, and the stewarding module and the hub read the base role."""
    db_path = await _make_db(tmp_path, signup_channel=NO_SIGNUP)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert (await _stored(db_path))[column] == NEW_ROLE
    assert _permission_calls(interaction) == []


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_the_change_records_the_role_it_replaced(tmp_path, run, column, change_type):
    """A league that finds its members locked out needs to know which role used to hold the
    access, and it is not recoverable from anywhere else once the configuration is
    overwritten."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await run(cog, _interaction())

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [change_type]
    assert json.loads(rows[0]["old_value"]) == {"role_id": OLD_ROLE}
    assert json.loads(rows[0]["new_value"]) == {"role_id": NEW_ROLE}


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_the_change_is_logged_with_the_roles_name(tmp_path, run, column, change_type):
    """An id in the log tells a manager reading it nothing."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await run(cog, _interaction(), role=_role(NEW_ROLE, "Verified members"))

    assert "Verified members" in str(cog.bot.output_router.post_log.await_args.args[0])


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_the_manager_is_told_which_role_was_set(tmp_path, run, column, change_type):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert f"<@&{NEW_ROLE}>" in _replied(interaction)


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_role_is_refused_once_the_configuration_is_confirmed(
    tmp_path, run, column, change_type
):
    """A driver role changed mid-season would leave every current driver holding the old one
    for good, the season's end revoking only the new."""
    db_path = await _make_db(tmp_path, stage="ONGOING")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert "fixed for Season 4" in _replied(interaction)
    assert (await _stored(db_path))[column] == OLD_ROLE
    assert await _audit_rows(db_path) == []
    assert _permission_calls(interaction) == []


def _gone_old_role(interaction, new_role):
    """The stored role deleted from the server, and *new_role* on it."""
    interaction.guild.get_role = MagicMock(
        side_effect=lambda role_id: new_role if role_id == new_role.id else None
    )


def _members(interaction, *, refusing: tuple[int, ...] = ()) -> dict:
    members = {
        uid: MagicMock(
            add_roles=AsyncMock(
                side_effect=discord.Forbidden(MagicMock(status=403), "no")
                if uid in refusing
                else None
            )
        )
        for uid in (5001, 5002)
    }
    interaction.guild.get_member = MagicMock(side_effect=members.get)
    return members


_TWO_DRIVERS = (("5001", "ASSIGNED"), ("5002", "UNASSIGNED"), ("5003", "NOT_SIGNED_UP"))


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_role_gone_from_the_server_may_be_replaced_while_fixed(
    tmp_path, run, column, change_type
):
    """Nobody holds a deleted role, so the reason for fixing it is spent — and refusing would
    leave the season unable to open a window until it ended."""
    db_path = await _make_db(tmp_path, stage="ONGOING")
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    _gone_old_role(interaction, role)

    await run(cog, interaction, role=role)

    assert (await _stored(db_path))[column] == NEW_ROLE
    assert "no longer on the server" in _replied(interaction)
    assert [r["change_type"] for r in await _audit_rows(db_path)] == [change_type]
    assert "replaced: a role no longer on the server" in str(
        cog.bot.output_router.post_log.await_args.args[0]
    )


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_role_never_set_stays_fixed(tmp_path, run, column, change_type):
    """Not set is not gone: a league that ran its season with no signup module chose no role."""
    db_path = await _make_db(tmp_path, stage="ONGOING", base_role=None, driver_role=None)
    cog = _make_cog(db_path)
    interaction = _interaction()
    _gone_old_role(interaction, _role())

    await run(cog, interaction)

    assert "fixed for Season 4" in _replied(interaction)
    assert (await _stored(db_path))[column] is None


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_the_replacement_is_given_to_every_driver(tmp_path, run, column, change_type):
    """Every driver lost the old role with it. Whoever the driver role belongs to is given the
    replacement — the base role too, the one time the bot grants it."""
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    _gone_old_role(interaction, role)
    members = _members(interaction)

    await run(cog, interaction, role=role)

    for member in members.values():
        member.add_roles.assert_awaited_once_with(role, reason="League role replaced")
    assert "given to 2 driver(s)" in _replied(interaction)
    assert "given to: 2 driver(s)" in str(cog.bot.output_router.post_log.await_args.args[0])


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_driver_who_could_not_be_given_it_is_named(tmp_path, run, column, change_type):
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    _gone_old_role(interaction, role)
    _members(interaction, refusing=(5002,))

    await run(cog, interaction, role=role)

    assert "could not be given to <@5002> — give it to them by hand" in _replied(interaction)
    assert "not given to: <@5002>" in str(cog.bot.output_router.post_log.await_args.args[0])


async def test_a_replaced_base_role_says_the_other_members_need_it(tmp_path):
    """The bot knows its drivers and nobody else the league counts as a member."""
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    _gone_old_role(interaction, role)
    _members(interaction)

    await _base_role(cog, interaction, role=role)

    assert "other members need it too" in _replied(interaction)


async def test_a_replaced_driver_role_says_nothing_of_other_members(tmp_path):
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    _gone_old_role(interaction, role)
    _members(interaction)

    await _driver_role(cog, interaction, role=role)

    assert "other members" not in _replied(interaction)


async def test_a_base_role_replaced_by_everyone_is_given_to_nobody(tmp_path):
    """Everybody holds @everyone already."""
    db_path = await _make_db(tmp_path, stage="ONGOING", drivers=_TWO_DRIVERS)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()
    role.is_default.return_value = True
    _gone_old_role(interaction, role)
    members = _members(interaction)

    await _base_role(cog, interaction, role=role)

    assert "Everybody holds it already" in _replied(interaction)
    for member in members.values():
        member.add_roles.assert_not_awaited()


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_role_may_be_set_while_the_season_is_in_configuration(
    tmp_path, run, column, change_type
):
    db_path = await _make_db(tmp_path, stage="CONFIGURATION")
    cog = _make_cog(db_path)

    await run(cog, _interaction())

    assert (await _stored(db_path))[column] == NEW_ROLE


@pytest.mark.parametrize("run,column,change_type", _BOTH)
async def test_a_server_not_yet_set_up_is_refused(tmp_path, run, column, change_type):
    """The tier guard refuses first in practice; the body refuses too rather than writing a
    role into a row that serves no server."""
    db_path = await _make_db(tmp_path, claimed=False)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert "/bot init" in _replied(interaction)
    assert (await _stored(db_path))[column] == OLD_ROLE
    assert await _audit_rows(db_path) == []


def test_both_commands_are_a_league_manager_s_in_the_interaction_channel():
    """They repair nothing the guards read, so nothing exempts them from the channel rule —
    unlike the four `/bot` settings beside them."""
    from leaguebot.core.utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_MANAGER, TIER_ATTRIBUTE

    for command in (BotCog.handle_base_role, BotCog.handle_driver_role):
        assert getattr(command.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER
        assert not getattr(command.callback, CHANNEL_EXEMPT_ATTRIBUTE, False)


# ---------------------------------------------------------------------------
# The base role, and the channel it opens
# ---------------------------------------------------------------------------


async def test_the_new_role_is_granted_access_to_the_signup_channel(tmp_path):
    """Without it a league's members cannot see the channel they are told to sign up in,
    and nothing in the bot's replies suggests why."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()

    await _base_role(cog, interaction, role=role)

    grant = [c for c in _permission_calls(interaction) if c.args[0] is role]
    assert len(grant) == 1
    assert grant[0].kwargs == {
        "view_channel": True,
        "send_messages": False,
        "use_application_commands": True,
    }


async def test_the_previous_role_loses_its_access(tmp_path):
    """The half most easily forgotten, because nothing visibly breaks when it is: the old
    role simply keeps access it is no longer entitled to."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    revoked = [c for c in _permission_calls(interaction) if "overwrite" in c.kwargs]
    assert len(revoked) == 1
    assert revoked[0].args[0].id == OLD_ROLE


async def test_re_setting_the_same_role_does_not_revoke_it(tmp_path):
    """The removal and the grant are two calls; on one role a naive "remove then add" would
    briefly revoke it, and outright if the grant failed in between."""
    db_path = await _make_db(tmp_path, base_role=NEW_ROLE)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction, role=_role(NEW_ROLE))

    assert [c for c in _permission_calls(interaction) if "overwrite" in c.kwargs] == []


async def test_a_league_setting_its_first_base_role_revokes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, base_role=None)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    assert len(_permission_calls(interaction)) == 1


async def test_a_previous_role_that_has_been_deleted_is_stepped_over(tmp_path):
    """Its overwrite went with it, and the new role still needs granting."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(old_role_missing=True)
    role = _role()

    await _base_role(cog, interaction, role=role)

    assert [c for c in _permission_calls(interaction) if c.args[0] is role]
    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


async def test_a_league_with_no_signup_channel_yet_still_sets_the_role(tmp_path):
    db_path = await _make_db(tmp_path, signup_channel=None)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE
    assert len(await _audit_rows(db_path)) == 1
    assert _permission_calls(interaction) == []


async def test_a_deleted_signup_channel_still_sets_the_role(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _base_role(cog, _interaction(channel_missing=True))

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


async def test_a_permissions_failure_does_not_fail_the_command(tmp_path):
    """Discord refuses an overwrite for reasons that have nothing to do with the setting.
    The channel is repairable by hand; a half-written configuration is not obviously
    repairable at all."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(set_permissions_fails=True)

    await _base_role(cog, interaction)

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE
    assert "Base role** set" in _replied(interaction)


async def test_setting_the_base_role_defers_before_touching_discord(tmp_path):
    """Two permission edits on a channel outrun the three-second window."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    interaction.response.defer.assert_awaited_once()


# ---------------------------------------------------------------------------
# The driver role, which is not a channel permission
# ---------------------------------------------------------------------------


async def test_the_driver_role_touches_no_channel(tmp_path):
    """It is granted to a person, not applied to a place. Tested explicitly because the two
    commands share a body, and the obvious tidy-up is to let the channel work run for both."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _driver_role(cog, interaction)

    assert _permission_calls(interaction) == []


async def test_a_driver_role_the_bot_cannot_grant_is_refused(tmp_path):
    """It is granted at every approval, and `wizard_service.approve_signup` only logs a grant
    Discord refuses — so a role the bot cannot grant costs every driver of the window their
    role with nobody told (#374). Setting it is the moment a league is there to choose another,
    as `/team role` holds since #381."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _driver_role(cog, interaction, role=_role(above_the_bot=True))

    assert "Move my role above it" in _replied(interaction)
    assert (await _stored(db_path))["driver_role_id"] == OLD_ROLE
    assert await _audit_rows(db_path) == []


async def test_a_base_role_the_bot_cannot_grant_is_still_set(tmp_path):
    """The league grants the base role, not the bot, so where it sits in the role list is not
    the bot's concern."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _base_role(cog, _interaction(), role=_role(above_the_bot=True))

    assert (await _stored(db_path))["base_role_id"] == NEW_ROLE


def test_nothing_current_names_a_withdrawn_signup_role_command():
    """`/signup base-role`, `/signup complete-role` and `/signup config roles` were withdrawn
    when the roles became the league's (issue #276). A reply or a guide naming one sends a
    league to a command Discord no longer offers. `specs/` and the constitution's sync
    reports are historical records and are not read."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    withdrawn = re.compile(r"/signup (?:base-role|complete-role|config roles)\b")
    paths = [
        *sorted((root / "src").rglob("*.py")),
        root / "README.md",
        *sorted((root / "docs" / "how-to").glob("*.md")),
        *sorted((root / "docs" / "wip-specs").glob("*.md")),
    ]
    offenders = sorted(
        str(path.relative_to(root))
        for path in paths
        if withdrawn.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == []
