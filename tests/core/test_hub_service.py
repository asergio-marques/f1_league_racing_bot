"""The hub's panel: the options modules register, and what a press on one does (issue #279).

**The hub service names no option.** A module registers one, saying when it is offered and
what a press does; core's one, About, is registered by `core/services/about_service.py` (#258) and
tested there. A panel offering nothing says so. The tests register their own options against a
registry emptied for each.

**A press is judged when it is made.** A panel can outlive the module that filled it — posted,
then the module disabled — so the option is asked again at the press, and refused where it is
no longer offered, rather than running for a module that is off.

Every test that builds a view is `async def`: discord.py 2.5.0, which the Pi carries, asks for
a running loop when a view is made.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services import hub_service
from leaguebot.core.services.config_service import ConfigService
from leaguebot.core.services.hub_service import (
    CUSTOM_ID_PREFIX,
    HubOption,
    HubPanelView,
    apply_hub_permissions,
    offered_options,
    reapply_hub_permissions,
    recover_hub,
    register_option,
    registered_options,
    render_panel,
)
from leaguebot.core.utils.league_server import LeagueView
from tests.support.change_queue import (
    SERVER_ID as LEAGUE_SERVER_ID,
    Clock,
    attach_queue,
    change_rows,
    http_error,
    league_double,
    member_interaction,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    text_channel,
)
from tests.support.mentions import NOBODY, notified


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
    """A press by Alex, not yet answered, as Discord delivers it."""
    interaction = MagicMock()
    if client is None:
        # A server with no hub set: the refresh after a refused press has nothing to do.
        client = MagicMock()
        client.config_service.get_server_config = AsyncMock(return_value=None)
        client.output_router.post_log = AsyncMock()
    interaction.client = client
    interaction.user.id = 4242
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    return interaction


# ── The registry ──────────────────────────────────────────────────────────


def test_the_hub_service_registers_no_option_itself():
    """The service names no option. Core's one, About (#258), is registered by
    `core/services/about_service.py`; every other is a module's. The fixture above clears the
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


@pytest.mark.parametrize(
    "registered, named",
    [
        pytest.param(
            True,
            "the hub's “View licence” option",
            id="no longer offered",
        ),
        pytest.param(
            False,
            "the hub's `licence` option",
            id="no longer registered",
        ),
    ],
)
async def test_a_stale_press_is_recorded_in_the_log_channel(registered, named):
    """A press on a button that tries to act is refused in the log channel as well as to the
    member (the core specification's "The record of what changed"). The option is named by its
    label, or by its key where no module registers it any more; the member is answered exactly
    as before."""
    option = _option("licence", label="View licence", offered=_offered(False))
    if registered:
        register_option(option)
    view = HubPanelView([option])
    interaction = _interaction()

    await view.children[0].callback(interaction)

    interaction.response.send_message.assert_awaited_once_with(
        hub_service.NO_LONGER_OFFERED, ephemeral=True
    )
    interaction.client.output_router.post_log.assert_awaited_once_with(
        f"⛔ {named} refused for Alex (<@4242>) — "
        "That option is no longer offered here. The panel has been refreshed."
    )


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


# ── The panel on the change queue (#439, slice 6b, C89–C90) ───────────────
#
# The panel is refreshed by `hub.refresh`, a bot's change on the league lane: a refused panel,
# or a hub channel set but gone, stops the queue with Retry and Discard rather than being
# reported in a line; the panel's id is saved by the job's record, with its mark; a hub not set
# is not due. A stale press and the start-up ask for the refresh rather than making it.
#
# The league: `league_double`'s, its configuration read from the database, with the hub set to
# channel 7900, which the league's server finds. Nothing of the change that is not built yet is
# imported at the head of the file.

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)



@pytest.fixture
async def league(tmp_path):
    """The league's queue, its hub set to channel 7900 and no panel yet posted."""
    path = str(tmp_path / "hub_queue.db")
    await run_migrations(path)
    await seed_server(path)
    await _configure(path, hub_channel_id=HUB)
    bot = league_double(path)
    bot.config_service = ConfigService(path)
    from leaguebot.core.services.output_router import OutputRouter

    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=path)
    hub = text_channel(HUB)
    guild = MagicMock(spec=discord.Guild)
    guild.id = LEAGUE_SERVER_ID
    guild.get_channel = MagicMock(side_effect={HUB: hub}.get)
    bot.get_guild = MagicMock(return_value=guild)
    bot.add_view = MagicMock()
    attach_queue(bot, path, now=Clock(NOW))
    return SimpleNamespace(db_path=path, bot=bot, hub=hub, guild=guild)


async def _ask_refresh(league, command="/module enable weather"):
    """The bot asks the queue to refresh the panel, as a module toggle's save does."""
    from leaguebot.core.models.change import ChangeOrigin

    return await league.bot.change_queue.ask(
        hub_service.HUB_REFRESH, {"command": command},
        what="refreshing the hub panel", origin=ChangeOrigin.BOT,
    )


async def _stored_panel(league):
    return (await league.bot.config_service.get_server_config()).hub_message_id


async def _refreshes(league):
    return [row for row in await change_rows(league.db_path)
            if row["kind"] == hub_service.HUB_REFRESH]


def _log(league) -> str:
    return "\n".join(league.bot.log_channel.sent)


async def test_the_panel_is_posted_and_its_id_kept(league):
    """Kept so that the next refresh edits it in place rather than posting a second. The hub
    is set and no panel stands; the refresh is asked and the queue run."""
    await _ask_refresh(league)
    await run_queue(league.bot)

    assert "Nothing is offered here yet." in league.hub.sent[0]
    assert await _stored_panel(league) == league.hub.posted[0].id


async def test_a_standing_panel_is_edited_in_place(league):
    """The panel stands in the hub; a refresh asked once an option is registered edits it in
    place, with the option's button, and posts nothing new."""
    standing = await league.hub.send("old panel")
    await _configure(league.db_path, hub_message_id=standing.id)
    register_option(_option("licence"))

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert "Press an option below." in standing.content
    assert [b.custom_id for b in standing.view.children] == ["hub:licence"]
    assert league.hub.sent == ["old panel"]


async def test_a_deleted_panel_is_posted_again(league):
    """Somebody deleted it by hand; the hub is not left empty. The panel recorded is gone from
    the channel; the refresh posts a fresh one and keeps its id."""
    await _configure(league.db_path, hub_message_id=PANEL)

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert len(league.hub.sent) == 1
    assert await _stored_panel(league) == league.hub.posted[0].id


@pytest.mark.xfail(strict=True, reason="#441: the hub panel edited leaves whom it notifies to the bot's default")
async def test_the_hub_panel_posted_or_edited_notifies_nobody(league):
    """The hub is set and no panel stands: a refresh posts it. An option is registered and the
    panel refreshed again: it is edited in place. Neither the post nor the edit notifies
    anybody."""
    await _ask_refresh(league)
    await run_queue(league.bot)
    panel = league.hub.posted[0]
    register_option(_option("licence"))
    await _ask_refresh(league)
    await run_queue(league.bot)

    assert len(league.hub.sent) == 1
    assert notified(league.hub.send.await_args.kwargs.get("allowed_mentions")) == NOBODY
    assert notified(panel.edit.await_args.kwargs.get("allowed_mentions")) == NOBODY


async def test_a_refused_panel_stops_the_queue(league):
    """Discord refuses the panel in the hub: the queue stops at the refresh, no panel id is
    kept, and once the bot is let post there, Retry posts it and keeps its id."""
    league.hub.fails = http_error(discord.Forbidden, status=403, text="Missing Access")

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert await _stored_panel(league) is None
    league.hub.fails = None
    await retry_job(league.bot)
    assert await stopped_job(league.db_path) is None
    assert await _stored_panel(league) == league.hub.posted[0].id


async def test_a_hub_channel_gone_stops_the_queue_naming_the_command(league):
    """The hub channel set (7900) is no longer in the server: the queue stops at the refresh,
    its notice naming `/bot hub-channel`, and nothing is posted."""
    league.guild.get_channel = MagicMock(return_value=None)

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert "/bot hub-channel" in _log(league)
    assert league.hub.sent == []


async def test_the_panel_s_id_is_saved_with_its_mark(league, monkeypatch):
    """The panel is posted; its id is written in the refresh job's own save, with its mark,
    never by a commit of the configuration service's own."""
    written = []
    own_commit = league.bot.config_service.set_core_setting

    async def _spy(column, value):
        written.append(column)
        return await own_commit(column, value)

    monkeypatch.setattr(league.bot.config_service, "set_core_setting", _spy)

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert "hub_message_id" not in written
    assert await _stored_panel(league) == league.hub.posted[0].id
    [change] = await _refreshes(league)
    assert all(job["done_at"] is not None for job in await step_rows(league.db_path, change["id"]))


async def test_a_hub_not_set_is_not_due(league):
    """No hub channel is set: the refresh asked is not due, so nothing is saved on the queue
    and nothing posted."""
    await _configure(league.db_path, hub_channel_id=None)

    assert await _ask_refresh(league) is None

    await run_queue(league.bot)
    assert await _refreshes(league) == []
    assert league.hub.sent == []


async def _panel_with(league, option):
    """The panel posted through the queue with *option* offered; its view, as Discord holds it."""
    register_option(option)
    await _ask_refresh(league)
    await run_queue(league.bot)
    return league.hub.posted[0].view


async def test_a_stale_press_refreshes_the_panel(league):
    """The panel still carried the option; Alex presses it after it stopped being offered. The
    press is refused and the refresh asked of the queue: the panel is unchanged until the queue
    runs, and then no longer carries the option."""
    offered = _offered(True)
    view = await _panel_with(league, _option("licence", offered=offered))
    offered.return_value = False

    await view.children[0].callback(member_interaction(league.bot))

    assert "Press an option below." in league.hub.posted[0].content
    assert [r["state"] for r in await _refreshes(league)] == ["DONE", "QUEUED"]
    await run_queue(league.bot)
    assert "Nothing is offered here yet." in league.hub.posted[0].content


async def test_a_burst_of_stale_presses_asks_one_refresh(league):
    """Alex presses the stale option twice before the queue runs: one refresh is asked,
    the second press's request dropped while the first waits, and the panel is refreshed once."""
    offered = _offered(True)
    view = await _panel_with(league, _option("licence", offered=offered))
    offered.return_value = False

    await view.children[0].callback(member_interaction(league.bot))
    await view.children[0].callback(member_interaction(league.bot))

    assert [r["state"] for r in await _refreshes(league)] == ["DONE", "QUEUED"]
    await run_queue(league.bot)
    assert league.hub.posted[0].edit.await_count == 1
    assert "Nothing is offered here yet." in league.hub.posted[0].content


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


async def test_a_restart_routes_every_registered_option_and_refreshes(league):
    """Every option, offered or not: a stale press is refused in words, not in silence. The
    panel stands in the hub; after a restart every registered option's button is routed again,
    and the panel's refresh is asked of the queue rather than made at once."""
    standing = await league.hub.send("old panel")
    await _configure(league.db_path, hub_message_id=standing.id)
    register_option(_option("on", offered=_offered(True)))
    register_option(_option("off", offered=_offered(False)))

    await recover_hub(league.bot, queue=league.bot.change_queue)

    (view,), _ = league.bot.add_view.call_args
    assert sorted(b.custom_id for b in view.children) == ["hub:off", "hub:on"]
    standing.edit.assert_not_awaited()
    assert [r["state"] for r in await _refreshes(league)] == ["QUEUED"]


async def test_the_start_up_asks_for_the_panel_before_the_queue_starts(league):
    """The hub is set and no panel stands. The start-up asks the queue for the panel, as the
    bot's, and posts nothing itself; the panel is posted, and its id kept, once the queue runs."""
    await recover_hub(league.bot, queue=league.bot.change_queue)

    assert league.hub.sent == []
    [change] = await _refreshes(league)
    assert change["state"] == "QUEUED"
    await run_queue(league.bot)
    assert "Nothing is offered here yet." in league.hub.sent[0]
    assert await _stored_panel(league) == league.hub.posted[0].id


async def test_a_restart_with_no_option_registered_adds_no_view(league):
    await recover_hub(league.bot, queue=league.bot.change_queue)

    league.bot.add_view.assert_not_called()
