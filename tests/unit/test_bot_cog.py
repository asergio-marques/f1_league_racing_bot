"""Core server configuration — `/bot init` and the four settings beside it.

Four properties are pinned here, each of which a plausible tidy-up would undo.

**The four setting commands are not bound to the interaction channel.** The tier guards
admit a command only in the configured interaction channel and only to a holder of one of
the league's two roles. These commands exist to repair those very settings, so guarding them
would lock a league out of the failure they are for — a deleted interaction channel, or an
admin role removed from the server, would be unrecoverable short of wiping the
configuration. The tests below invoke them from the wrong channel, by a user holding no
role, and require them to work anyway.

**Each writes exactly one column.** `save_server_config` once carried a whole `ServerConfig`
into an upsert, which is how `/bot init force:True` came to switch test mode off: the model
it was handed had never read the stored row, so `test_mode_active` defaulted to False and
overwrote a live setting. The setters write a single column so no such carry-over is
possible.

**`/bot init` runs once.** A second run is refused rather than overwriting, which is what
makes the clobber above unreachable rather than merely corrected.

**Each is audited as well as logged** (issue #371). The log line is all these once wrote, which
left the database no record of who changed who may command and govern the bot, nor of what the
setting had been. The audit keeps the value each command replaced.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.bot_cog import BotCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 4242

#: The configured interaction channel and role. Every setting-command test below deliberately
#: acts from somewhere else, to prove the guard is absent.
CONFIGURED_CHANNEL = 111
CONFIGURED_ROLE = 222
CONFIGURED_LOG = 333
CONFIGURED_ADMIN_ROLE = 444


def _unwrap(cmd):
    """Strip `bot_setup_only` and return the command body.

    Unwrapping cannot by itself notice a guard being changed — it would simply strip the new
    layer instead — so the tier these commands sit at, and their exemption from the
    interaction channel, are pinned separately below.
    """
    return undecorate(cmd)


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "test.db")
    await run_migrations(db_path)
    return db_path


async def _seed_config(db_path: str, *, test_mode: int = 1) -> None:
    """A fully configured server, with test mode and both module flags on.

    They are on so that a command writing more than its own column shows up as a change to
    one of them.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id, "
            "test_mode_active, weather_module_enabled, signup_module_enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (SERVER_ID, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG,
             CONFIGURED_ADMIN_ROLE, test_mode),
        )
        await db.commit()


async def _row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        row = await cursor.fetchone()
    return dict(row) if row is not None else {}


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, division_id, change_type, old_value, new_value "
            "FROM audit_entries"
        )
        return [dict(r) for r in await cursor.fetchall()]


def _bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    # The two channel settings check the channel is not already doing another job, which
    # reads the database directly rather than through a service.
    bot.db_path = db_path
    bot.config_service = ConfigService(db_path)
    bot.output_router.post_log = AsyncMock()
    bot.team_service.seed_default_teams_if_empty = AsyncMock()
    return bot


def _interaction(*, channel_id: int = 999) -> MagicMock:
    """An interaction from the *wrong* channel by default, by a user holding no role."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = channel_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 7
    interaction.user.display_name = "admin"
    interaction.user.roles = []
    interaction.response.send_message = AsyncMock()
    return interaction


def _channel(channel_id: int) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    return channel


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = "Stewards"
    role.mention = f"<@&{role_id}>"
    return role


# ── /bot init runs once ───────────────────────────────────────────────────


async def test_bot_init_configures_an_unconfigured_server(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG
    bot.team_service.seed_default_teams_if_empty.assert_awaited_once_with()


async def test_bot_init_is_audited_with_the_four_settings(tmp_path):
    """Issue #371. Nothing stood before the claim, so every value it replaced is null."""
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))

    await _unwrap(cog.handle_bot_init)(
        cog,
        _interaction(),
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    (row,) = await _audit_rows(db_path)
    assert row["change_type"] == "BOT_INITIALISED"
    assert row["actor_id"] == 7
    assert row["division_id"] is None
    new = {
        "server_id": SERVER_ID,
        "interaction_role_id": CONFIGURED_ROLE,
        "league_admin_role_id": CONFIGURED_ADMIN_ROLE,
        "interaction_channel_id": CONFIGURED_CHANNEL,
        "log_channel_id": CONFIGURED_LOG,
    }
    assert json.loads(row["new_value"]) == new
    assert json.loads(row["old_value"]) == dict.fromkeys(new)


async def test_bot_init_refuses_a_second_run_and_names_the_four_commands(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    reply = interaction.response.send_message.call_args.args[0]
    assert "runs once" in reply
    assert "/bot log-channel" in reply
    assert "/bot interaction-channel" in reply
    assert "/bot interaction-role" in reply
    assert "/bot admin-role" in reply

    row = await _row(db_path)
    assert row["interaction_role_id"] == CONFIGURED_ROLE
    assert row["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    assert row["interaction_channel_id"] == CONFIGURED_CHANNEL
    assert row["log_channel_id"] == CONFIGURED_LOG
    bot.team_service.seed_default_teams_if_empty.assert_not_awaited()
    assert await _audit_rows(db_path) == []


async def test_a_second_bot_init_does_not_switch_test_mode_off(tmp_path):
    """The regression this change exists to make unreachable.

    `/bot init force:True` built a `ServerConfig` without reading the stored row, so
    `test_mode_active` defaulted to False and the upsert wrote it — leaving test mode off
    with the test drivers still seated, and nothing said so.
    """
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path, test_mode=1)
    cog = BotCog(_bot(db_path))

    await _unwrap(cog.handle_bot_init)(
        cog, _interaction(), _role(900), _role(903), _channel(901), _channel(902)
    )

    assert (await _row(db_path))["test_mode_active"] == 1


async def test_bot_init_on_a_second_server_is_refused_and_writes_nothing(tmp_path):
    """Issue #244: one bot serves one league."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    reply = interaction.response.send_message.call_args.args[0]
    assert "another server" in reply
    async with get_connection(db_path) as db:
        rows = await (await db.execute("SELECT server_id FROM server_configs")).fetchall()
    assert [r["server_id"] for r in rows] == [SERVER_ID]
    bot.team_service.seed_default_teams_if_empty.assert_not_awaited()
    assert await _audit_rows(db_path) == []


async def test_a_pack_frees_the_server_for_another(tmp_path):
    """The claim is the configuration row's `server_id`, and `/bot pack` clears it (#247)."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    await _unwrap(cog.handle_pack)(
        cog, _deferred(_interaction(channel_id=CONFIGURED_CHANNEL)), "CONFIRM"
    )
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    assert await bot.config_service.get_league_server_id() == SERVER_ID + 1


async def test_a_lost_race_to_another_server_names_the_other_server(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    real = bot.config_service

    async def _lose(cfg):
        await _seed_config(db_path)  # the other server wins in between
        return await ConfigService.save_server_config(real, cfg)

    bot.config_service = MagicMock(wraps=real)
    bot.config_service.get_league_server_id = real.get_league_server_id
    bot.config_service.get_server_config = real.get_server_config
    bot.config_service.save_server_config = _lose
    cog = BotCog(bot)
    interaction = _interaction()
    interaction.guild_id = SERVER_ID + 1

    await _unwrap(cog.handle_bot_init)(
        cog, interaction, _role(900), _role(903), _channel(901), _channel(902)
    )

    assert "another server" in interaction.response.send_message.call_args.args[0]
    assert await real.get_league_server_id() == SERVER_ID


async def test_save_server_config_will_not_overwrite_an_existing_row(tmp_path):
    """The insert-only contract, at the layer that enforces it."""
    from models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=1,
            interaction_channel_id=2,
            log_channel_id=3,
        )
    )

    assert created is False
    assert (await _row(db_path))["log_channel_id"] == CONFIGURED_LOG


async def test_save_server_config_will_not_claim_a_second_server(tmp_path):
    """Issue #244: one bot serves one league, and the first server set up is the league's."""
    from models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID + 1,
            interaction_role_id=1,
            interaction_channel_id=2,
            log_channel_id=3,
        )
    )

    assert created is False
    assert await service.get_league_server_id() == SERVER_ID


async def test_no_server_is_the_league_s_before_bot_init(tmp_path):
    assert await ConfigService(await _make_db(tmp_path)).get_league_server_id() is None


# ── The four settings ─────────────────────────────────────────────────────

#: (command attribute, column, factory, the id it sets)
_SETTINGS = [
    ("handle_log_channel", "log_channel_id", _channel, 555),
    ("handle_interaction_channel", "interaction_channel_id", _channel, 556),
    ("handle_interaction_role", "interaction_role_id", _role, 557),
    ("handle_admin_role", "league_admin_role_id", _role, 558),
]

#: column → (the audit's change type, its value key, the value the seed holds)
_AUDITED = {
    "log_channel_id": ("LOG_CHANNEL_SET", "channel_id", CONFIGURED_LOG),
    "interaction_channel_id": ("INTERACTION_CHANNEL_SET", "channel_id", CONFIGURED_CHANNEL),
    "interaction_role_id": ("INTERACTION_ROLE_SET", "role_id", CONFIGURED_ROLE),
    "league_admin_role_id": ("LEAGUE_ADMIN_ROLE_SET", "role_id", CONFIGURED_ADMIN_ROLE),
}


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_writes_only_its_own_column(
    tmp_path, attribute, column, factory, new_id
):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))
    before = await _row(db_path)

    await _unwrap(getattr(cog, attribute))(cog, _interaction(), factory(new_id))

    after = await _row(db_path)
    assert after[column] == new_id
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_is_audited_with_the_value_it_replaced(
    tmp_path, attribute, column, factory, new_id
):
    """Issue #371: the log line alone left no record of who changed who governs the bot."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))
    change_type, key, old_id = _AUDITED[column]

    await _unwrap(getattr(cog, attribute))(cog, _interaction(), factory(new_id))

    (row,) = await _audit_rows(db_path)
    assert row["change_type"] == change_type
    assert row["actor_id"] == 7
    assert row["division_id"] is None
    assert json.loads(row["old_value"]) == {key: old_id}
    assert json.loads(row["new_value"]) == {key: new_id}


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_works_outside_the_interaction_channel(
    tmp_path, attribute, column, factory, new_id
):
    """Guarding these on the settings they repair would make the failure unrecoverable."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    # The whole decorator chain, not the unwrapped body: a channel-bound guard added later
    # would refuse this interaction, and refusing it is the failure this test is for.
    interaction = _interaction(channel_id=CONFIGURED_CHANNEL + 12345)
    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    assert (await _row(db_path))[column] == new_id
    assert "✅" in interaction.response.send_message.call_args.args[0]


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_refuses_an_unconfigured_server(
    tmp_path, attribute, column, factory, new_id
):
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))
    interaction = _interaction()

    await _unwrap(getattr(cog, attribute))(cog, interaction, factory(new_id))

    assert "/bot init" in interaction.response.send_message.call_args.args[0]
    assert await _row(db_path) == {}
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_refuses_a_member_of_neither_tier(
    tmp_path, attribute, column, factory, new_id
):
    """`bot_setup_only` is the whole of the gate on these, so it had better be on."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = False

    # The decorated command, not the unwrapped body.
    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    reply = interaction.response.send_message.call_args.args[0]
    assert "Administrator" in reply
    assert (await _row(db_path))[column] != new_id
    assert await _audit_rows(db_path) == []


@pytest.mark.parametrize(
    "attribute,column,factory,new_id", _SETTINGS, ids=[s[1] for s in _SETTINGS]
)
async def test_a_setting_command_admits_the_league_admin_role(
    tmp_path, attribute, column, factory, new_id
):
    """The role is the tier; Administrator is only the way back when the role is gone."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = False
    interaction.user.roles = [_role(CONFIGURED_ADMIN_ROLE)]

    await getattr(cog, attribute).callback(cog, interaction, factory(new_id))

    assert (await _row(db_path))[column] == new_id


async def test_a_setting_command_admits_an_administrator_before_the_bot_is_configured(
    tmp_path,
):
    """The bootstrap. `/bot init` has no configuration to read a role out of."""
    db_path = await _make_db(tmp_path)
    cog = BotCog(_bot(db_path))

    interaction = _interaction()
    interaction.user.guild_permissions.administrator = True
    interaction.user.roles = []

    await cog.handle_bot_init.callback(
        cog,
        interaction,
        _role(CONFIGURED_ROLE),
        _role(CONFIGURED_ADMIN_ROLE),
        _channel(CONFIGURED_CHANNEL),
        _channel(CONFIGURED_LOG),
    )

    assert (await _row(db_path))["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE


async def test_set_core_setting_refuses_a_column_of_the_callers_choosing(tmp_path):
    """The column is interpolated into the UPDATE, so the allow-list is load-bearing."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)

    with pytest.raises(ValueError):
        await service.set_core_setting("test_mode_active", 0)

    assert (await _row(db_path))["test_mode_active"] == 1


# ── The league's two roles (issue #276) ───────────────────────────────────


@pytest.mark.parametrize("column", ["base_role_id", "driver_role_id"])
async def test_a_league_role_is_written_alone_and_read_back(tmp_path, column):
    """The league's roles are core settings, written a column at a time like the four beside
    them, so setting one cannot carry a stale value over the other or over anything else."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)
    before = await _row(db_path)

    assert await service.set_core_setting(column, 5150) is True

    after = await _row(db_path)
    assert after[column] == 5150
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }
    assert getattr(await service.get_server_config(), column) == 5150


@pytest.mark.parametrize("column", ["hub_channel_id", "hub_message_id"])
async def test_the_hub_is_written_alone_and_read_back(tmp_path, column):
    """The hub's channel and its panel (issue #279) are written a column at a time too."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    service = ConfigService(db_path)
    before = await _row(db_path)

    assert await service.set_core_setting(column, 6060) is True

    after = await _row(db_path)
    assert after[column] == 6060
    assert {k: v for k, v in after.items() if k != column} == {
        k: v for k, v in before.items() if k != column
    }
    assert getattr(await service.get_server_config(), column) == 6060


async def test_a_league_that_has_set_neither_role_reads_none(tmp_path):
    """Both are optional until the signup module asks for them."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)

    config = await ConfigService(db_path).get_server_config()

    assert (config.base_role_id, config.driver_role_id) == (None, None)


@pytest.mark.parametrize(
    "attribute", [s[0] for s in _SETTINGS] + ["handle_bot_init"]
)
def test_no_core_command_is_bound_to_the_interaction_channel(attribute):
    """Stated structurally as well as behaviourally, because the reason is easy to lose.

    These commands repair the interaction channel and the two roles, so a guard that read
    any of them would refuse precisely the person who needs them. The tier the guard records
    is asserted rather than the number of decorators: counting them said the same thing only
    for as long as the guards came in pairs, and said nothing about which guards they were.
    """
    from cogs.bot_cog import BotCog as Cog
    from utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

    callback = getattr(Cog, attribute).callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is True, (
        f"{attribute} is bound to the interaction channel — a league whose interaction "
        f"channel was deleted can no longer repair it"
    )


# ── The league admin role ─────────────────────────────────────────────────


def test_bot_init_requires_the_league_admin_role():
    """A required parameter, not an optional one.

    The role is the whole of the league admin tier, so a server initialised without one has
    nobody who may cancel its season — every league admin command would be refused until
    somebody noticed. Asking for it up front is what stops that, and making the parameter
    optional again would quietly undo it.
    """
    from cogs.bot_cog import BotCog as Cog

    parameter = next(
        p for p in Cog.handle_bot_init.parameters if p.name == "league_admin_role"
    )
    assert parameter.required


async def test_save_server_config_persists_the_league_admin_role(tmp_path):
    from models.server_config import ServerConfig

    db_path = await _make_db(tmp_path)
    service = ConfigService(db_path)

    created = await service.save_server_config(
        ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=CONFIGURED_ROLE,
            league_admin_role_id=CONFIGURED_ADMIN_ROLE,
            interaction_channel_id=CONFIGURED_CHANNEL,
            log_channel_id=CONFIGURED_LOG,
        )
    )

    assert created is True
    assert (await _row(db_path))["league_admin_role_id"] == CONFIGURED_ADMIN_ROLE
    stored = await service.get_server_config()
    assert stored is not None
    assert stored.league_admin_role_id == CONFIGURED_ADMIN_ROLE


async def test_a_server_configured_before_the_role_existed_reads_none(tmp_path):
    """The migration adds the column nullable, and NULL means "not yet chosen".

    No value is invented for a league that predates the role. Reading it back as ``None``
    is what lets the guards refuse a league admin command and name `/bot admin-role`,
    rather than falling back to a Discord permission the league never picked.
    """
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, ?, ?, ?)",
            (SERVER_ID, CONFIGURED_ROLE, CONFIGURED_CHANNEL, CONFIGURED_LOG),
        )
        await db.commit()

    stored = await ConfigService(db_path).get_server_config()
    assert stored is not None
    assert stored.league_admin_role_id is None


# ── /bot pack ─────────────────────────────────────────────────────────────


def _packing_bot(db_path: str) -> MagicMock:
    bot = _bot(db_path)
    bot.scheduler_service.cancel_all = MagicMock(return_value=0)
    bot.get_cog = MagicMock(return_value=None)
    return bot


def _deferred(interaction: MagicMock) -> MagicMock:
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_bot_pack_is_a_league_admin_s_command_in_the_interaction_channel():
    """It releases the settings rather than repairing them, so it takes no setup exemption."""
    from cogs.bot_cog import BotCog as Cog
    from utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE

    callback = Cog.handle_pack.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is False


async def test_bot_pack_without_the_word_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction(channel_id=CONFIGURED_CHANNEL)

    await _unwrap(cog.handle_pack)(cog, interaction, "confirm")

    assert "CONFIRM" in interaction.response.send_message.call_args.args[0]
    assert await bot.config_service.get_league_server_id() == SERVER_ID
    bot.output_router.post_log.assert_not_awaited()


async def test_bot_pack_is_refused_while_a_season_is_current_and_logs_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-01-01', 'SETUP', 3, 'WAITING')"
        )
        await db.commit()
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _interaction(channel_id=CONFIGURED_CHANNEL)

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    reply = interaction.response.send_message.call_args.args[0]
    assert "Season 3 is current" in reply
    assert "waiting" in reply
    assert await bot.config_service.get_league_server_id() == SERVER_ID
    bot.output_router.post_log.assert_not_awaited()


async def test_bot_pack_logs_while_the_log_channel_still_exists(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _packing_bot(db_path)
    claimed_when_logged = []

    async def post_log(content):
        claimed_when_logged.append(await bot.config_service.get_league_server_id())

    bot.output_router.post_log = AsyncMock(side_effect=post_log)
    cog = BotCog(bot)
    interaction = _deferred(_interaction(channel_id=CONFIGURED_CHANNEL))

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    assert claimed_when_logged == [SERVER_ID]
    assert await bot.config_service.get_league_server_id() is None
    reply = interaction.followup.send.call_args.args[0]
    assert "/bot init" in reply
    assert "buttons now refuse" in reply


async def test_bot_pack_losing_a_race_to_a_new_season_says_so(tmp_path, monkeypatch):
    from services import pack_service

    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)

    async def refuse(*_args, **_kwargs):
        raise pack_service.PackRefused(4, "CONFIGURATION")

    monkeypatch.setattr(pack_service, "pack", refuse)
    bot = _packing_bot(db_path)
    cog = BotCog(bot)
    interaction = _deferred(_interaction(channel_id=CONFIGURED_CHANNEL))

    await _unwrap(cog.handle_pack)(cog, interaction, "CONFIRM")

    assert "Season 4 is current" in interaction.followup.send.call_args.args[0]
    assert "Refused" in bot.output_router.post_log.call_args_list[-1].args[0]


# ── /bot factory-reset ─────────────────────────────────────────────────────

OWNER_ID = 77


def _owner_interaction(user_id: int = OWNER_ID) -> MagicMock:
    interaction = _deferred(_interaction())
    interaction.user.id = user_id
    interaction.user.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    interaction.guild = MagicMock()
    interaction.guild.owner_id = OWNER_ID
    interaction.guild.get_channel = MagicMock(return_value=None)
    return interaction


def _resetting_bot(db_path: str, tmp_path) -> MagicMock:
    bot = _packing_bot(db_path)
    bot.scheduler_service._scheduler.running = False
    bot.scheduler_service._jobstore_path = str(tmp_path / "scheduler.db")
    bot.user.id = 1000
    return bot


async def _run(cog, interaction, confirm="CONFIRM"):
    """The guard is kept: who may run this is the point of half these tests."""
    await BotCog.handle_factory_reset.callback(cog, interaction, confirm)
    if cog._clean_up is not None:
        await cog._clean_up


def test_bot_factory_reset_is_the_server_owner_s_from_any_channel():
    from utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, SERVER_OWNER, TIER_ATTRIBUTE

    callback = BotCog.handle_factory_reset.callback
    assert getattr(callback, TIER_ATTRIBUTE) == SERVER_OWNER
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is True


async def test_bot_factory_reset_refuses_an_administrator_who_is_not_the_owner(tmp_path):
    """Whatever roles or permissions they hold: the owner is the only way in."""
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction(user_id=OWNER_ID + 1)
    interaction.user.guild_permissions = discord.Permissions(administrator=True)
    interaction.user.roles = [_role(CONFIGURED_ADMIN_ROLE)]

    await _run(cog, interaction)

    assert "owner" in interaction.response.send_message.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID


async def test_bot_factory_reset_without_the_word_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()

    await _run(cog, interaction, confirm="yes")

    assert "CONFIRM" in interaction.response.send_message.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID


async def test_bot_factory_reset_erases_nothing_without_a_backup(tmp_path, monkeypatch):
    from services import backup_service, factory_reset_service

    def fail(*_args, **_kwargs):
        raise backup_service.BackupError("the disk is full")

    monkeypatch.setattr(factory_reset_service, "take_backup", fail)
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()

    await _run(cog, interaction)

    reply = interaction.followup.send.call_args.args[0]
    assert "Nothing was erased" in reply and "the disk is full" in reply
    assert await ConfigService(db_path).get_league_server_id() == SERVER_ID
    interaction.user.send.assert_not_awaited()


async def test_bot_factory_reset_backs_up_then_wipes_then_reports_by_dm(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    bot = _resetting_bot(db_path, tmp_path)
    cog = BotCog(bot)
    interaction = _owner_interaction()

    await _run(cog, interaction)

    backups = sorted(p.name for p in tmp_path.iterdir() if ".factory-" in p.name)
    assert len(backups) == 1 and backups[0].startswith("test.factory-")
    assert await ConfigService(db_path).get_league_server_id() is None
    bot.scheduler_service.cancel_all.assert_called_once_with()
    reply = interaction.followup.send.call_args.args[0]
    assert backups[0] in reply
    assert "direct message" in reply
    progress = interaction.user.send.return_value
    assert progress.edit.await_args_list[-1].kwargs["content"].startswith("✅")


async def test_bot_factory_reset_reports_to_the_log_where_dms_are_closed(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()
    interaction.user.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "closed")
    )

    await _run(cog, interaction)

    assert "host's log" in interaction.followup.send.call_args.args[0]
    assert await ConfigService(db_path).get_league_server_id() is None


async def test_a_clean_up_that_breaks_says_where_it_stopped(tmp_path, monkeypatch):
    from services import factory_reset_service

    async def broken(*_args, **_kwargs):
        raise RuntimeError("gateway lost")

    monkeypatch.setattr(factory_reset_service, "clean_discord", broken)
    db_path = await _make_db(tmp_path)
    await _seed_config(db_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    interaction = _owner_interaction()

    await _run(cog, interaction)

    last = interaction.user.send.return_value.edit.await_args_list[-1].kwargs["content"]
    assert "stopped" in last and "gateway lost" in last


async def test_a_second_factory_reset_waits_for_the_first_clean_up(tmp_path):
    import asyncio

    db_path = await _make_db(tmp_path)
    cog = BotCog(_resetting_bot(db_path, tmp_path))
    cog._clean_up = asyncio.get_running_loop().create_future()
    interaction = _owner_interaction()

    await BotCog.handle_factory_reset.callback(cog, interaction, "CONFIRM")

    assert "still cleaning" in interaction.response.send_message.call_args.args[0]
    cog._clean_up.cancel()


def test_nothing_current_names_a_withdrawn_bot_command():
    """The five setup commands became `/bot …` and `/bot-reset` was withdrawn (issue #247).

    A reply or a guide naming the old form sends a league to a command Discord no longer
    offers. `specs/` and the constitution's sync reports are historical records and are not
    read; everything a league or a maintainer reads as current is.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    withdrawn = re.compile(
        r"/bot-(?:init|log-channel|interaction-channel|interaction-role|admin-role|reset)\b"
    )
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
