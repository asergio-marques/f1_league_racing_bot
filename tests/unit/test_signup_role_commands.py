"""The two roles a league sets for signing up, and what changing one has to move with it.

Issue #208. `/signup base-role` and `/signup complete-role` were uncovered. They look like a
pair and are not: one governs who can *see* the signup channel, the other is granted when a
signup is approved.

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

**The complete role touches no channel at all.** It is granted to a person, not applied to a
place, so it writes the configuration and stops. Tested explicitly because the two commands sit
next to each other and the obvious tidy-up is to give them a shared body.

**A permissions failure does not fail the command.** Discord refuses an overwrite for reasons
that have nothing to do with the setting — a role above the bot's own, a channel it was removed
from — and the configured role is still the right one to store. The channel is repairable by
hand; a half-written configuration is not obviously repairable at all.

**Both are audited with the role they replaced.** A league that finds its members locked out
needs to know which role used to hold the access, and it is not recoverable from anywhere else
once the configuration is overwritten.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 11308
SIGNUP_CHANNEL = 700
OLD_ROLE = 3001
NEW_ROLE = 3002
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "signup_roles") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _config(*, base_role=OLD_ROLE, signed_up_role=OLD_ROLE, channel=SIGNUP_CHANNEL):
    return SimpleNamespace(
        server_id=SERVER_ID,
        base_role_id=base_role,
        signed_up_role_id=signed_up_role,
        signup_channel_id=channel,
    )


_UNSET = object()


def _make_cog(db_path: str, *, config=_UNSET) -> SignupCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_config = AsyncMock(
        return_value=_config() if config is _UNSET else config
    )
    bot.signup_module_service.save_config = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _role(role_id: int = NEW_ROLE, name: str = "Drivers"):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
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
        return_value=None if old_role_missing else _role(OLD_ROLE, "Old drivers")
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
    return await undecorate(SignupCog.signup_base_role)(cog, interaction, role or _role())


async def _complete_role(cog, interaction, *, role=None):
    return await undecorate(SignupCog.signup_complete_role)(cog, interaction, role or _role())


async def _audit_rows(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries WHERE server_id = ?",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


def _permission_calls(interaction) -> list:
    return interaction._channel.set_permissions.await_args_list


# ---------------------------------------------------------------------------
# Both commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run,change_type",
    [(_base_role, "SIGNUP_BASE_ROLE_SET"), (_complete_role, "SIGNUP_COMPLETE_ROLE_SET")],
)
async def test_the_role_is_stored(tmp_path, run, change_type):
    db_path = await _make_db(tmp_path, name=f"store_{change_type}")
    cog = _make_cog(db_path)
    config = await cog.bot.signup_module_service.get_config(SERVER_ID)

    await run(cog, _interaction())

    cog.bot.signup_module_service.save_config.assert_awaited_once_with(config)


@pytest.mark.parametrize("run", [_base_role, _complete_role])
async def test_an_unconfigured_module_is_refused(tmp_path, run):
    """There is no configuration to write the role into, and creating one here would give a
    league a half-configured module it never asked to enable."""
    db_path = await _make_db(tmp_path, name="unconfigured")
    cog = _make_cog(db_path, config=None)
    interaction = _interaction()

    await run(cog, interaction)

    assert "not configured" in _replied(interaction)
    cog.bot.signup_module_service.save_config.assert_not_awaited()
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize(
    "run,change_type",
    [(_base_role, "SIGNUP_BASE_ROLE_SET"), (_complete_role, "SIGNUP_COMPLETE_ROLE_SET")],
)
async def test_the_change_records_the_role_it_replaced(tmp_path, run, change_type):
    """A league that finds its members locked out needs to know which role used to hold the
    access, and it is not recoverable from anywhere else once the configuration is
    overwritten."""
    db_path = await _make_db(tmp_path, name=f"audit_{change_type}")
    cog = _make_cog(db_path)

    await run(cog, _interaction())

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == [change_type]
    assert json.loads(rows[0]["old_value"]) == {"role_id": OLD_ROLE}
    assert json.loads(rows[0]["new_value"]) == {"role_id": NEW_ROLE}


@pytest.mark.parametrize("run", [_base_role, _complete_role])
async def test_the_change_is_logged_with_the_roles_name(tmp_path, run):
    """An id in the log tells a manager reading it nothing."""
    db_path = await _make_db(tmp_path, name="rolelog")
    cog = _make_cog(db_path)

    await run(cog, _interaction(), role=_role(NEW_ROLE, "Verified drivers"))

    assert "Verified drivers" in str(cog.bot.output_router.post_log.await_args.args[1])


@pytest.mark.parametrize("run", [_base_role, _complete_role])
async def test_the_manager_is_told_which_role_was_set(tmp_path, run):
    db_path = await _make_db(tmp_path, name="rolereply")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    assert f"<@&{NEW_ROLE}>" in _replied(interaction)


# ---------------------------------------------------------------------------
# The base role, and the channel it opens
# ---------------------------------------------------------------------------


async def test_the_new_role_is_granted_access_to_the_signup_channel(tmp_path):
    """Without it a league's members cannot see the channel they are told to sign up in,
    and nothing in the bot's replies suggests why."""
    db_path = await _make_db(tmp_path, name="base_grant")
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


async def test_the_channel_is_readable_but_not_writable(tmp_path):
    """Signing up is a button and a private channel, not a conversation in the public one —
    a base role that could post would turn the signup channel into a chat."""
    db_path = await _make_db(tmp_path, name="base_readonly")
    cog = _make_cog(db_path)
    interaction = _interaction()
    role = _role()

    await _base_role(cog, interaction, role=role)

    grant = [c for c in _permission_calls(interaction) if c.args[0] is role][0]
    assert grant.kwargs["send_messages"] is False
    assert grant.kwargs["use_application_commands"] is True


async def test_the_previous_role_loses_its_access(tmp_path):
    """The half most easily forgotten, because nothing visibly breaks when it is: the old
    role simply keeps access it is no longer entitled to."""
    db_path = await _make_db(tmp_path, name="base_revoke")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    revoked = [c for c in _permission_calls(interaction) if "overwrite" in c.kwargs]
    assert len(revoked) == 1
    assert revoked[0].args[0].id == OLD_ROLE


async def test_re_setting_the_same_role_does_not_revoke_it(tmp_path):
    """The removal and the grant are two calls; on one role a naive "remove then add" would
    briefly revoke it, and outright if the grant failed in between."""
    db_path = await _make_db(tmp_path, name="base_same")
    cog = _make_cog(db_path, config=_config(base_role=NEW_ROLE))
    interaction = _interaction()

    await _base_role(cog, interaction, role=_role(NEW_ROLE))

    assert [c for c in _permission_calls(interaction) if "overwrite" in c.kwargs] == []


async def test_a_league_setting_its_first_base_role_revokes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="base_first")
    cog = _make_cog(db_path, config=_config(base_role=None))
    interaction = _interaction()

    await _base_role(cog, interaction)

    assert len(_permission_calls(interaction)) == 1


async def test_a_previous_role_that_has_been_deleted_is_stepped_over(tmp_path):
    """Its overwrite went with it, and the new role still needs granting."""
    db_path = await _make_db(tmp_path, name="base_deletedrole")
    cog = _make_cog(db_path)
    interaction = _interaction(old_role_missing=True)
    role = _role()

    await _base_role(cog, interaction, role=role)

    assert [c for c in _permission_calls(interaction) if c.args[0] is role]
    cog.bot.signup_module_service.save_config.assert_awaited_once()


async def test_a_league_with_no_signup_channel_still_sets_the_role(tmp_path):
    """The channel is created when signups open; the role can be configured before then."""
    db_path = await _make_db(tmp_path, name="base_nochannel")
    cog = _make_cog(db_path, config=_config(channel=None))
    interaction = _interaction()

    await _base_role(cog, interaction)

    cog.bot.signup_module_service.save_config.assert_awaited_once()
    assert len(await _audit_rows(db_path)) == 1


async def test_a_deleted_signup_channel_still_sets_the_role(tmp_path):
    db_path = await _make_db(tmp_path, name="base_channelgone")
    cog = _make_cog(db_path)
    interaction = _interaction(channel_missing=True)

    await _base_role(cog, interaction)

    cog.bot.signup_module_service.save_config.assert_awaited_once()


async def test_a_permissions_failure_does_not_fail_the_command(tmp_path):
    """Discord refuses an overwrite for reasons that have nothing to do with the setting.
    The channel is repairable by hand; a half-written configuration is not obviously
    repairable at all."""
    db_path = await _make_db(tmp_path, name="base_permfail")
    cog = _make_cog(db_path)
    interaction = _interaction(set_permissions_fails=True)

    await _base_role(cog, interaction)

    cog.bot.signup_module_service.save_config.assert_awaited_once()
    assert "Signup base role set" in _replied(interaction)


async def test_setting_the_base_role_defers_before_touching_discord(tmp_path):
    """Two permission edits on a channel outrun the three-second window."""
    db_path = await _make_db(tmp_path, name="base_defer")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _base_role(cog, interaction)

    interaction.response.defer.assert_awaited_once()


# ---------------------------------------------------------------------------
# The complete role, which is not a channel permission
# ---------------------------------------------------------------------------


async def test_the_complete_role_touches_no_channel(tmp_path):
    """It is granted to a person, not applied to a place. Tested explicitly because the two
    commands sit next to each other and the obvious tidy-up is to share a body."""
    db_path = await _make_db(tmp_path, name="complete_nochannel")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _complete_role(cog, interaction)

    assert _permission_calls(interaction) == []


async def test_the_complete_role_replaces_the_stored_one(tmp_path):
    db_path = await _make_db(tmp_path, name="complete_replace")
    config = _config(signed_up_role=OLD_ROLE)
    cog = _make_cog(db_path, config=config)

    await _complete_role(cog, _interaction())

    assert config.signed_up_role_id == NEW_ROLE


async def test_the_complete_role_leaves_the_base_role_alone(tmp_path):
    """One configuration row holds both, and writing it back is where one overwrites the
    other."""
    db_path = await _make_db(tmp_path, name="complete_keepbase")
    config = _config(base_role=OLD_ROLE, signed_up_role=None)
    cog = _make_cog(db_path, config=config)

    await _complete_role(cog, _interaction())

    assert config.base_role_id == OLD_ROLE
