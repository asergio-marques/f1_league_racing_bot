"""The hub's panel: the options modules register, and what a press on one does (issue #279).

**The hub service names no option.** A module registers one, saying when it is offered and
what a press does; core's one, About, is registered by `services/about_service.py` (#258) and
tested there. A panel offering nothing says so. The tests register their own options against a
registry emptied for each.

**A press is judged when it is made.** A panel can outlive the module that filled it — posted,
then the module disabled — so the option is asked again at the press, and refused where it is
no longer offered, rather than running for a module that is off.

Every test that builds a view is `async def`: discord.py 2.5.0, which the Pi carries, asks for
a running loop when a view is made.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import hub_service  # noqa: E402
from services.config_service import ConfigService  # noqa: E402
from services.hub_service import (  # noqa: E402
    CUSTOM_ID_PREFIX,
    HubOption,
    HubPanelView,
    apply_hub_permissions,
    offered_options,
    reapply_hub_permissions,
    recover_hub,
    refresh_panel,
    register_option,
    registered_options,
    render_panel,
)
from utils.league_server import LeagueView  # noqa: E402


@pytest.fixture(autouse=True)
def empty_registry(monkeypatch):
    """Each test starts from the registry as it ships: empty."""
    monkeypatch.setattr(hub_service, "_OPTIONS", {})


def _option(key="licence", *, label="View licence", order=10, offered=None, respond=None):
    return HubOption(
        key=key,
        label=label,
        order=order,
        respond=respond or AsyncMock(),
        offered=offered,
    )


def _offered(value: bool):
    return AsyncMock(return_value=value)


def _interaction(client=None):
    interaction = MagicMock()
    if client is None:
        # A server with no hub set: the refresh after a refused press has nothing to do.
        client = MagicMock()
        client.config_service.get_server_config = AsyncMock(return_value=None)
    interaction.client = client
    interaction.response.send_message = AsyncMock()
    return interaction


# ── The registry ──────────────────────────────────────────────────────────


def test_the_hub_service_registers_no_option_itself():
    """The service names no option. Core's one, About (#258), is registered by
    `services/about_service.py`; every other is a module's. The fixture above clears the
    registry, so this sees what `hub_service` alone would hold."""
    assert registered_options() == []


def test_options_are_ordered_by_their_order_then_their_key():
    """Registration order is where a module happens to be imported; the panel should not
    move because an import did."""
    late, early, tied = _option("zeta", order=20), _option("beta", order=5), _option("alpha", order=20)
    for option in (late, early, tied):
        register_option(option)

    assert [o.key for o in registered_options()] == ["beta", "alpha", "zeta"]


def test_two_options_may_not_share_a_key():
    """The key routes the press: a second option under it would answer for the first."""
    register_option(_option("licence"))

    with pytest.raises(ValueError):
        register_option(_option("licence", label="Something else"))


def test_registering_the_same_option_again_is_harmless():
    option = _option()
    register_option(option)
    register_option(option)

    assert registered_options() == [option]


async def test_only_the_options_offered_are_on_the_panel():
    register_option(_option("on", offered=_offered(True)))
    register_option(_option("off", offered=_offered(False)))
    register_option(_option("always"))

    assert [o.key for o in await offered_options(MagicMock())] == ["always", "on"]


# ── The panel ─────────────────────────────────────────────────────────────


async def test_an_empty_panel_says_so_and_carries_no_buttons():
    text, view = render_panel([])

    assert "Nothing is offered here yet." in text
    assert view is None


async def test_each_option_is_a_button_keyed_by_its_custom_id():
    """The custom id is what survives a restart, so it is the key and nothing else."""
    options = [_option("licence", label="View licence"), _option("about", label="About")]

    text, view = render_panel(options)

    assert "Press an option below." in text
    assert [(b.label, b.custom_id) for b in view.children] == [
        ("View licence", f"{CUSTOM_ID_PREFIX}licence"),
        ("About", f"{CUSTOM_ID_PREFIX}about"),
    ]


async def test_the_panel_is_persistent_and_refuses_other_servers():
    """No timeout, a custom id on every button, and the league-server check every view the
    bot posts carries."""
    view = HubPanelView([_option()])

    assert view.timeout is None
    assert view.is_persistent()
    assert isinstance(view, LeagueView)


# ── A press ───────────────────────────────────────────────────────────────


async def test_a_press_is_answered_by_its_option():
    respond = AsyncMock()
    register_option(_option("licence", respond=respond, offered=_offered(True)))
    view = HubPanelView(registered_options())
    interaction = _interaction()

    await view.children[0].callback(interaction)

    respond.assert_awaited_once_with(interaction)
    interaction.response.send_message.assert_not_awaited()


async def test_a_press_on_an_option_no_longer_offered_is_refused():
    """The panel was posted while the module was on; it has been disabled since."""
    respond = AsyncMock()
    offered = _offered(True)
    register_option(_option("licence", respond=respond, offered=offered))
    view = HubPanelView(registered_options())
    offered.return_value = False
    interaction = _interaction()

    await view.children[0].callback(interaction)

    respond.assert_not_awaited()
    reply = interaction.response.send_message.await_args
    assert "no longer offered" in reply.args[0]
    assert reply.kwargs["ephemeral"] is True


async def test_a_press_on_an_option_no_module_registers_any_more_is_refused():
    """A button left on a panel by a version of the bot that offered it."""
    view = HubPanelView([_option("retired")])
    interaction = _interaction()

    await view.children[0].callback(interaction)

    assert "no longer offered" in interaction.response.send_message.await_args.args[0]


# ── Posting and refreshing the panel ──────────────────────────────────────

SERVER_ID = 27901
HUB = 7900
PANEL = 7901
BASE_ROLE, MANAGER_ROLE, ADMIN_ROLE = 7910, 7911, 7912


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "hub.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, league_admin_role_id) "
            "VALUES (?, ?, 100, 101, ?)",
            (SERVER_ID, MANAGER_ROLE, ADMIN_ROLE),
        )
        await db.commit()
    return path


async def _configure(db_path, **columns):
    async with get_connection(db_path) as db:
        for column, value in columns.items():
            await db.execute(f"UPDATE server_configs SET {column} = ?", (value,))
        await db.commit()


def _role(role_id):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    return role


def _hub_channel(*, edit_raises=None, send_raises=None):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = HUB
    partial = MagicMock()
    partial.edit = AsyncMock(side_effect=edit_raises)
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.send = AsyncMock(
        side_effect=send_raises, return_value=MagicMock(id=PANEL + 1)
    )
    channel.edit = AsyncMock()
    channel._partial = partial
    return channel


def _guild(channel, *, roles=(BASE_ROLE, MANAGER_ROLE, ADMIN_ROLE)):
    known = {rid: _role(rid) for rid in roles}
    guild = MagicMock()
    guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == HUB else None)
    guild.get_role = MagicMock(side_effect=lambda rid: known.get(rid))
    guild.default_role = _role(1)
    guild.me = _role(2)
    guild._roles = known
    return guild


def _bot(db_path, guild):
    bot = MagicMock()
    bot.config_service = ConfigService(db_path)
    bot.get_guild = MagicMock(return_value=guild)
    bot.add_view = MagicMock()
    return bot


def _not_found():
    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Message")


def _forbidden():
    return discord.HTTPException(MagicMock(status=403, reason="Forbidden"), "Missing Access")


async def test_nothing_is_posted_while_no_hub_is_set(db_path):
    channel = _hub_channel()

    assert await refresh_panel(_bot(db_path, _guild(channel))) is None

    channel.send.assert_not_awaited()


async def test_the_panel_is_posted_and_its_id_kept(db_path):
    """Kept so that the next refresh edits it in place rather than posting a second."""
    await _configure(db_path, hub_channel_id=HUB)
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))

    assert await refresh_panel(bot) is None

    assert "Nothing is offered here yet." in channel.send.await_args.args[0]
    assert (await bot.config_service.get_server_config()).hub_message_id == PANEL + 1


async def test_a_standing_panel_is_edited_in_place(db_path):
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=PANEL)
    register_option(_option("licence"))
    channel = _hub_channel()

    assert await refresh_panel(_bot(db_path, _guild(channel))) is None

    channel.get_partial_message.assert_called_once_with(PANEL)
    edit = channel._partial.edit.await_args
    assert "Press an option below." in edit.kwargs["content"]
    assert [b.custom_id for b in edit.kwargs["view"].children] == ["hub:licence"]
    channel.send.assert_not_awaited()


async def test_a_deleted_panel_is_posted_again(db_path):
    """Somebody deleted it by hand; the hub is not left empty."""
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=PANEL)
    channel = _hub_channel(edit_raises=_not_found())
    bot = _bot(db_path, _guild(channel))

    assert await refresh_panel(bot) is None

    channel.send.assert_awaited_once()
    assert (await bot.config_service.get_server_config()).hub_message_id == PANEL + 1


async def test_a_hub_channel_gone_from_the_server_is_reported(db_path):
    await _configure(db_path, hub_channel_id=HUB)
    guild = _guild(None)

    fault = await refresh_panel(_bot(db_path, guild))

    assert f"id {HUB}" in fault and "not in the server" in fault


async def test_a_panel_the_bot_may_not_post_is_reported(db_path):
    await _configure(db_path, hub_channel_id=HUB)
    channel = _hub_channel(send_raises=_forbidden())

    fault = await refresh_panel(_bot(db_path, _guild(channel)))

    assert "could not be posted" in fault


async def test_a_stale_press_refreshes_the_panel(db_path):
    """The panel still carried the option; after the refusal it no longer does."""
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=PANEL)
    offered = _offered(False)
    register_option(_option("licence", offered=offered))
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))
    view = HubPanelView(registered_options())

    await view.children[0].callback(_interaction(bot))

    assert "Nothing is offered here yet." in channel._partial.edit.await_args.kwargs["content"]


# ── Who may see the hub ───────────────────────────────────────────────────


def _overwrites(channel) -> dict:
    return channel.edit.await_args.kwargs["overwrites"]


async def test_with_no_base_role_every_member_sees_the_hub_and_none_posts(db_path):
    """Decided 2026-09-22: the hub serves every member where the league has no base role."""
    channel = _hub_channel()
    guild = _guild(channel)

    assert await apply_hub_permissions(_bot(db_path, guild), guild, channel) is None

    everyone = _overwrites(channel)[guild.default_role]
    assert (everyone.view_channel, everyone.send_messages) == (True, False)


async def test_with_a_base_role_only_its_holders_see_the_hub(db_path):
    await _configure(db_path, base_role_id=BASE_ROLE)
    channel = _hub_channel()
    guild = _guild(channel)

    await apply_hub_permissions(_bot(db_path, guild), guild, channel)

    overwrites = _overwrites(channel)
    assert overwrites[guild.default_role].view_channel is False
    base = overwrites[guild._roles[BASE_ROLE]]
    assert (base.view_channel, base.send_messages) == (True, False)


async def test_both_tier_roles_see_the_hub_and_neither_posts(db_path):
    """The panel is the only message there; a manager's post would push it out of sight."""
    await _configure(db_path, base_role_id=BASE_ROLE)
    channel = _hub_channel()
    guild = _guild(channel)

    await apply_hub_permissions(_bot(db_path, guild), guild, channel)

    for role_id in (MANAGER_ROLE, ADMIN_ROLE):
        tier = _overwrites(channel)[guild._roles[role_id]]
        assert (tier.view_channel, tier.send_messages) == (True, False)
    me = _overwrites(channel)[guild.me]
    assert (me.view_channel, me.send_messages) == (True, True)


async def test_a_deleted_base_role_keeps_the_hub_closed_and_says_so(db_path):
    """Opening it to everyone would read a deleted role as a league that chose none."""
    await _configure(db_path, base_role_id=BASE_ROLE)
    channel = _hub_channel()
    guild = _guild(channel, roles=(MANAGER_ROLE, ADMIN_ROLE))

    fault = await apply_hub_permissions(_bot(db_path, guild), guild, channel)

    assert _overwrites(channel)[guild.default_role].view_channel is False
    assert f"id {BASE_ROLE}" in fault and "/bot base-role" in fault


async def test_permissions_are_applied_again_to_the_hub_set(db_path):
    await _configure(db_path, hub_channel_id=HUB)
    channel = _hub_channel()

    assert await reapply_hub_permissions(_bot(db_path, _guild(channel))) is None

    channel.edit.assert_awaited_once()


async def test_permissions_are_left_alone_while_no_hub_is_set(db_path):
    channel = _hub_channel()

    assert await reapply_hub_permissions(_bot(db_path, _guild(channel))) is None

    channel.edit.assert_not_awaited()


# ── Start-up ──────────────────────────────────────────────────────────────


async def test_a_restart_routes_every_registered_option_and_refreshes(db_path):
    """Every option, offered or not: a stale press is refused in words, not in silence."""
    await _configure(db_path, hub_channel_id=HUB, hub_message_id=PANEL)
    register_option(_option("on", offered=_offered(True)))
    register_option(_option("off", offered=_offered(False)))
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))

    await recover_hub(bot)

    (view,), _ = bot.add_view.call_args
    assert sorted(b.custom_id for b in view.children) == ["hub:off", "hub:on"]
    channel._partial.edit.assert_awaited_once()


async def test_a_restart_with_no_option_registered_adds_no_view(db_path):
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))

    await recover_hub(bot)

    bot.add_view.assert_not_called()
