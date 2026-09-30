"""`/signup channel` — setting the channel, and the permissions that go with it.

The command had no test of its own, and that is how a `NameError` shipped: the guard
against reusing the bot's command channel was replaced by the server-wide
`find_channel_use` check (775443d), and the `server_cfg` that guard had fetched was left
referenced fifty lines below, where the interaction role's overwrite is built. Nothing
exercised the command past the guard, so nothing noticed.

These drive the body to its end. The permission overwrites are the part that was broken
and the part most easily broken again, since they are built from three roles read from
three different places.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.undecorate import undecorate

SERVER_ID = 5511
INTERACTION_ROLE = 900
BASE_ROLE = 901


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "signup.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        # The base role is the league's, on the server configuration (issue #276).
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, base_role_id) "
            "VALUES (?, ?, 100, 101, ?)",
            (SERVER_ID, INTERACTION_ROLE, BASE_ROLE),
        )
        await db.execute("INSERT INTO signup_module_config (id) VALUES (1)")
        await db.commit()
    return path


def _channel(channel_id: int = 700):
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.edit = AsyncMock()
    return channel


def _interaction(guild):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = guild
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _guild():
    guild = MagicMock()
    guild.default_role = MagicMock()
    guild.me = MagicMock()
    guild.get_role = MagicMock(side_effect=lambda rid: MagicMock(name=f"role{rid}"))
    guild.get_channel = MagicMock(return_value=None)
    return guild


def _cog(db_path):
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


async def _run(cog, interaction, channel):
    """The command body, past whatever tier guard it wears."""
    body = undecorate(SignupCog.signup_channel)
    await body(cog, interaction, channel)


async def test_setting_a_free_channel_stores_it(db_path):
    """The regression: this raised `NameError` before reaching the store."""
    cog = _cog(db_path)
    guild = _guild()
    channel = _channel()

    await _run(cog, _interaction(guild), channel)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_module_config",
        )
        assert (await cursor.fetchone())[0] == channel.id


async def test_the_interaction_role_is_read_for_the_overwrites(db_path):
    """What the orphaned `server_cfg` was for.

    The role is fetched from the guild by the id on the server config; losing that read
    is what raised, and a silent `None` here would leave the stewards unable to see the
    channel they had just configured.
    """
    cog = _cog(db_path)
    guild = _guild()

    await _run(cog, _interaction(guild), _channel())

    asked = [call.args[0] for call in guild.get_role.call_args_list]
    assert INTERACTION_ROLE in asked, "the interaction role was never looked up"
    assert BASE_ROLE in asked, "the base role was never looked up"


async def test_a_channel_already_in_use_is_refused(db_path):
    """The guard that replaced the old one: 100 is the bot's command channel."""
    cog = _cog(db_path)
    interaction = _interaction(_guild())

    await _run(cog, interaction, _channel(100))

    reply = interaction.response.send_message.await_args.args[0]
    assert "already the" in reply

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT signup_channel_id FROM signup_module_config",
        )
        assert (await cursor.fetchone())[0] is None, "a refused channel was stored"


async def test_the_command_runs_to_the_end_without_an_unbound_name(db_path):
    """A blunt guard against the class of fault, not the instance.

    The body reads three roles and two configurations from four places, and an editing
    slip that drops one of the reads raises only when the command is run all the way
    through — which no test did before this file.
    """
    cog = _cog(db_path)
    interaction = _interaction(_guild())

    await _run(cog, interaction, _channel())

    # Reached the end: the command defers, so its confirmation is a followup, and no
    # refusal was sent through the response.
    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited()


# ---------------------------------------------------------------------------
# What the bot may not do, and every refusal, reach the log channel (#442)
# ---------------------------------------------------------------------------

#: The hub's words for a channel whose permissions the bot may not set.
def _may_not_edit(channel) -> str:
    return (
        f"❌ The bot needs **Manage Channel** and **Manage Permissions** on {channel.mention} "
        "to set who may see it. The signup channel was not changed."
    )


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _lines(cog) -> list[str]:
    return [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]


async def _stored(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
        row = await cursor.fetchone()
    return None if row is None else row[0]


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


def _assert_one_refusal_line(cog, command: str, said: str) -> None:
    [line] = _lines(cog)
    assert line.startswith("⛔ ")
    assert command in line
    assert "refused for Manager (<@42>)" in line
    assert said in line


@pytest.mark.parametrize(
    "manage_channels, manage_roles",
    [(True, False), (False, True)],
    ids=["only-manage-channels", "only-manage-roles"],
)
async def test_signup_channel_refuses_early_without_both_permissions(
    db_path, manage_channels, manage_roles
):
    """The channel's permissions are set with both, which Discord shows on a channel as Manage
    Channel and Manage Permissions. With either alone the command is refused before anything is
    edited, in the hub's words, nothing is saved, and the refusal is logged."""
    cog = _cog(db_path)
    cog.bot.user = MagicMock(id=1)
    guild = _guild()
    guild.get_member = MagicMock(return_value=MagicMock())
    channel = _channel()
    channel.permissions_for = MagicMock(
        return_value=SimpleNamespace(manage_channels=manage_channels, manage_roles=manage_roles)
    )
    interaction = _interaction(guild)
    interaction.client = cog.bot
    interaction.command.qualified_name = "signup channel"

    await _run(cog, interaction, channel)

    assert _replied(interaction) == _may_not_edit(channel)
    channel.edit.assert_not_awaited()
    assert await _stored(db_path) is None
    _assert_one_refusal_line(cog, "/signup channel", "Manage Permissions")


async def test_a_signup_channel_the_bot_may_not_edit_is_refused(db_path):
    """Discord refuses the edit on a first setting: the hub's words, no error text, nothing
    saved, and the refusal logged."""
    cog = _cog(db_path)
    channel = _channel()
    channel.edit = AsyncMock(side_effect=_forbidden())
    interaction = _interaction(_guild())
    interaction.client = cog.bot
    interaction.command.qualified_name = "signup channel"

    await _run(cog, interaction, channel)

    replied = _replied(interaction)
    assert _may_not_edit(channel) in replied
    assert "Missing Permissions" not in replied
    assert "Failed to apply" not in replied
    assert await _stored(db_path) is None
    _assert_one_refusal_line(cog, "/signup channel", "Manage Permissions")


async def test_a_refused_signup_channel_move_says_the_old_channel_was_cleared(db_path):
    """The old channel's permissions are cleared before the new one is tried. Where Discord
    then refuses the new one, the manager is told the old one needs putting right by hand, and
    the log line says so too, for whoever reads the log later."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE signup_module_config SET signup_channel_id = 650")
        await db.commit()
    cog = _cog(db_path)
    old = MagicMock(spec=discord.TextChannel)
    old.id = 650
    old.mention = "<#650>"
    old.edit = AsyncMock()
    guild = _guild()
    guild.get_channel = MagicMock(side_effect=lambda cid: old if cid == 650 else None)
    channel = _channel()
    channel.edit = AsyncMock(side_effect=_forbidden())
    interaction = _interaction(guild)
    interaction.client = cog.bot
    interaction.command.qualified_name = "signup channel"

    await _run(cog, interaction, channel)

    old.edit.assert_awaited_once_with(overwrites={})
    replied = _replied(interaction)
    assert _may_not_edit(channel) in replied
    assert (
        "The old signup channel <#650> has already had its permissions cleared, so it needs "
        "putting right by hand if you do not retry."
    ) in replied
    assert await _stored(db_path) == 650
    _assert_one_refusal_line(cog, "/signup channel", "Manage Permissions")
    _assert_one_refusal_line(
        cog,
        "/signup channel",
        "The old signup channel <#650> has already had its permissions cleared, so it needs "
        "putting right by hand if you do not retry.",
    )


async def test_a_signup_channel_edit_fault_goes_to_the_failure_path(db_path):
    """Anything but Discord's refusal is a fault in the bot: it goes to the command's failure
    path, which gives the standard reply, and nothing is saved."""
    cog = _cog(db_path)
    channel = _channel()
    channel.edit = AsyncMock(side_effect=RuntimeError("gateway closed"))
    interaction = _interaction(_guild())

    with pytest.raises(RuntimeError):
        await _run(cog, interaction, channel)

    assert "gateway closed" not in _replied(interaction)
    assert await _stored(db_path) is None


def _day(value: int = 5):
    choice = MagicMock()
    choice.value = str(value)
    choice.name = "Friday"
    return choice


#: Every refusal `/signup channel` makes, and the fixed-configuration refusal it shares with
#: the signup settings commands: the command's body, its arguments, the setup, the command
#: as the line names it, and a fragment of the reply.
SIGNUP_CHANNEL_REFUSALS = [
    pytest.param("signup_channel", lambda: (_channel(),), "unconfigured", "/signup channel",
                 "not configured", id="signup-channel-not-configured"),
    pytest.param("signup_channel", lambda: (_channel(100),), None, "/signup channel",
                 "already the", id="signup-channel-already-in-use"),
    pytest.param("signup_channel", lambda: (_channel(),), "fixed", "/signup channel",
                 "fixed for Season 3", id="signup-channel-fixed"),
    pytest.param("nationality", lambda: (), "fixed", "/signup nationality",
                 "fixed for Season 3", id="signup-nationality-fixed"),
    pytest.param("time_type", lambda: (), "fixed", "/signup time-type",
                 "fixed for Season 3", id="signup-time-type-fixed"),
    pytest.param("time_image", lambda: (), "fixed", "/signup time-image",
                 "fixed for Season 3", id="signup-time-image-fixed"),
    pytest.param("time_slot_add", lambda: (_day(), "21:00"), "fixed", "/signup time-slot add",
                 "fixed for Season 3", id="signup-time-slot-add-fixed"),
    pytest.param("time_slot_remove", lambda: (1,), "fixed", "/signup time-slot remove",
                 "fixed for Season 3", id="signup-time-slot-remove-fixed"),
]


@pytest.mark.parametrize("command, args, setup, named, said", SIGNUP_CHANNEL_REFUSALS)
async def test_every_signup_channel_refusal_reaches_the_log_channel(
    db_path, command, args, setup, named, said
):
    """Each refusal answers the manager as before and writes one line in the standard form."""
    async with get_connection(db_path) as db:
        if setup == "unconfigured":
            await db.execute("DELETE FROM signup_module_config")
        if setup == "fixed":
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', 'SETUP', 3, 'WAITING')"
            )
        await db.commit()
    cog = _cog(db_path)
    interaction = _interaction(_guild())
    interaction.client = cog.bot
    interaction.command.qualified_name = named.lstrip("/")

    await undecorate(getattr(SignupCog, command))(cog, interaction, *args())

    assert said in _replied(interaction)
    _assert_one_refusal_line(cog, named, said)


# ---------------------------------------------------------------------------
# The withdrawn alias (#124)
# ---------------------------------------------------------------------------


def test_the_signup_config_group_holds_only_view():
    """`/signup config channel` called the `/signup channel` command object, which is not
    callable, so it raised on every use and never set a channel (#124). It is withdrawn;
    `/signup channel` is the way to set it, and `view` is all the group holds."""
    assert [command.name for command in SignupCog.config_group.commands] == ["view"]


def test_nothing_current_offers_the_withdrawn_config_channel():
    """A guide or reply naming it would send a league to a command Discord no longer offers.
    `specs/` is a historical record and is not read."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / "src").rglob("*.py")),
        root / "README.md",
        *sorted((root / "docs" / "how-to").glob("*.md")),
        *sorted((root / "docs" / "wip-specs").glob("*.md")),
    ]
    offenders = sorted(
        str(path.relative_to(root))
        for path in paths
        if "/signup config channel" in path.read_text(encoding="utf-8")
    )
    assert offenders == []
