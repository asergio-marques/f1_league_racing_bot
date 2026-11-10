"""The hub's panel: the options modules register, and what a press on one does (issue #279).

**The hub service names no option.** The builder makes the bot's hub options and signs each
module's up on them, saying when it is offered and what a press does; core's one, About, is
defined by `core/services/about_service.py` (#258) and tested there. A panel offering nothing says
so. The tests sign their own options up on hub options of their own, made as the builder makes
them, and hand them to the bot or the press.

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
    render_panel,
)
from leaguebot.core.utils.league_server import LeagueView
from tests.support.change_queue import (
    SERVER_ID as LEAGUE_SERVER_ID,
    Clock,
    change_rows,
    http_error,
    league_bot,
    league_queue,
    member_interaction,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    text_channel,
)
from tests.support.mentions import NOBODY, notified


#: Why a test fails until the hub's options are held on an object the builder makes.
ON_THE_BOT = "#283: the hub's options are not yet held on an object the builder makes"


def _options(*options):
    """The hub's options as the builder makes them, each of *options* signed up on them."""
    from leaguebot.core.services.hub_service import HubOptions

    hub_options = HubOptions()
    for option in options:
        hub_options.register_option(option)
    return hub_options


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


# ── The options ───────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
def test_the_hub_service_registers_no_option_itself():
    """The service names no option, and nothing is signed up at import: the hub service keeps no
    registry of its own, and About's module, imported as the build imports it, signs nothing up.
    Hub options made as the builder makes them hold no option until one is signed up on them;
    core's one, About (#258), is signed up by the builder, and every other is a module's."""
    from leaguebot.core.services import about_service
    from leaguebot.core.services.hub_service import HubOptions

    assert not hasattr(hub_service, "_OPTIONS")
    assert not hasattr(hub_service, "register_option")
    assert not hasattr(about_service, "register_option")
    assert HubOptions().registered_options() == []


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
def test_options_are_ordered_by_their_order_then_their_key():
    """The order options are signed up in is where the builder happens to sign each module up;
    the panel should not move because that did."""
    late, early, tied = _option("zeta", order=20), _option("beta", order=5), _option("alpha", order=20)

    hub_options = _options(late, early, tied)

    assert [o.key for o in hub_options.registered_options()] == ["beta", "alpha", "zeta"]


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
def test_two_options_may_not_share_a_key():
    """The key routes the press: a second option under it, signed up on the same hub options,
    would answer for the first, and is refused."""
    hub_options = _options(_option("licence"))

    with pytest.raises(ValueError):
        hub_options.register_option(_option("licence", label="Something else"))


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
def test_registering_the_same_option_again_is_harmless():
    """Signing the same option up twice on one hub options is allowed: only a second option
    under one key is refused."""
    option = _option()
    hub_options = _options(option)
    hub_options.register_option(option)

    assert hub_options.registered_options() == [option]


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_only_the_options_offered_are_on_the_panel():
    """The panel carries the bot's own hub options that are offered now."""
    bot = MagicMock()
    bot.hub_options = _options(
        _option("on", offered=_offered(True)),
        _option("off", offered=_offered(False)),
        _option("always"),
    )

    assert [o.key for o in await offered_options(bot)] == ["always", "on"]


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


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_press_is_answered_by_its_option():
    """The option pressed is found on the bot's hub options, and answers the press."""
    respond = AsyncMock()
    option = _option("licence", respond=respond, offered=_offered(True))
    view = HubPanelView([option])
    interaction = _interaction()
    interaction.client.hub_options = _options(option)

    await view.children[0].callback(interaction)

    respond.assert_awaited_once_with(interaction)
    interaction.response.send_message.assert_not_awaited()


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
@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_stale_press_is_recorded_in_the_log_channel(registered, named):
    """A press on a button that tries to act is refused in the log channel as well as to the
    member (the core specification's "The record of what changed"). The option is named by its
    label, or by its key where no module registers it any more; the member is answered exactly
    as before, and the option does not run. The first case is a panel posted while the module
    was on and disabled since; the second, a button left on a panel by a version of the bot
    that offered it, the bot's hub options holding no option under its key."""
    respond = AsyncMock()
    option = _option("licence", label="View licence", offered=_offered(False), respond=respond)
    view = HubPanelView([option])
    interaction = _interaction()
    interaction.client.hub_options = _options(option) if registered else _options()

    await view.children[0].callback(interaction)

    respond.assert_not_awaited()
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
# The league: `league_bot`'s, its configuration read from the database, with the hub set to
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
    bot = await league_bot(path, real_config=True)
    hub = text_channel(HUB)
    guild = MagicMock(spec=discord.Guild)
    guild.id = LEAGUE_SERVER_ID
    guild.get_channel = MagicMock(side_effect={HUB: hub}.get)
    bot.get_guild = MagicMock(return_value=guild)
    league_queue(bot, path, now=Clock(NOW))
    return SimpleNamespace(db_path=path, bot=bot, hub=hub, guild=guild)


def _offer(league, *options):
    """The league's bot holds hub options of its own with *options* alone signed up on them, in
    place of the builder's, which hold About."""
    league.bot.hub_options = _options(*options)


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


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_standing_panel_is_edited_in_place(league):
    """The panel stands in the hub; a refresh asked once an option is signed up on the bot's hub
    options edits it in place, with the option's button, and posts nothing new."""
    standing = await league.hub.send("old panel")
    await _configure(league.db_path, hub_message_id=standing.id)
    _offer(league, _option("licence"))

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


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_the_hub_panel_posted_or_edited_notifies_nobody(league):
    """The hub is set, no panel stands and nothing is offered: a refresh posts it. An option is
    signed up on the bot's hub options and the panel refreshed again: it is edited in place.
    Neither the post nor the edit notifies anybody."""
    _offer(league)
    await _ask_refresh(league)
    await run_queue(league.bot)
    panel = league.hub.posted[0]
    league.bot.hub_options.register_option(_option("licence"))
    await _ask_refresh(league)
    await run_queue(league.bot)

    assert len(league.hub.sent) == 1
    assert notified(league.hub.send.await_args.kwargs.get("allowed_mentions")) == NOBODY
    assert notified(panel.edit.await_args.kwargs.get("allowed_mentions")) == NOBODY


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_hub_panel_emptied_of_options_loses_its_buttons(league):
    """The bot's hub options hold one option, and the panel is posted with its button; the
    option then stops being offered and the panel is refreshed through the queue. The panel says
    nothing is offered and carries no view, so no old button is left under it."""
    offered = _offered(True)
    _offer(league, _option("licence", offered=offered))
    await _ask_refresh(league)
    await run_queue(league.bot)
    panel = league.hub.posted[0]
    assert panel.view is not None
    offered.return_value = False

    await _ask_refresh(league)
    await run_queue(league.bot)

    assert "Nothing is offered here yet." in panel.content
    assert panel.view is None
    assert len(league.hub.sent) == 1


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


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_the_panel_s_id_is_saved_with_its_mark(league):
    """The hub is set, no panel stands and the bot's hub options hold no option; the refresh is
    asked and the queue run. The panel is posted; its id is kept, so that the next refresh edits
    it in place rather than posting a second, and is written in the refresh job's own save, with
    its mark."""
    _offer(league)
    await _ask_refresh(league)
    await run_queue(league.bot)

    assert "Nothing is offered here yet." in league.hub.sent[0]
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
    """The panel posted through the queue with *option* alone on the bot's hub options, and
    offered; its view, as Discord holds it."""
    _offer(league, option)
    await _ask_refresh(league)
    await run_queue(league.bot)
    return league.hub.posted[0].view


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
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

    assert await apply_hub_permissions(ConfigService(db_path), guild, channel) is None

    everyone = _overwrites(channel)[guild.default_role]
    assert (everyone.view_channel, everyone.send_messages) == (True, False)


async def test_with_a_base_role_only_its_holders_see_the_hub(db_path):
    await _configure(db_path, base_role_id=BASE_ROLE)
    channel = _hub_channel()
    guild = _guild(channel)

    await apply_hub_permissions(ConfigService(db_path), guild, channel)

    overwrites = _overwrites(channel)
    assert overwrites[guild.default_role].view_channel is False
    base = overwrites[guild._roles[BASE_ROLE]]
    assert (base.view_channel, base.send_messages) == (True, False)


async def test_both_tier_roles_see_the_hub_and_neither_posts(db_path):
    """The panel is the only message there; a manager's post would push it out of sight."""
    await _configure(db_path, base_role_id=BASE_ROLE)
    channel = _hub_channel()
    guild = _guild(channel)

    await apply_hub_permissions(ConfigService(db_path), guild, channel)

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

    fault = await apply_hub_permissions(ConfigService(db_path), guild, channel)

    assert _overwrites(channel)[guild.default_role].view_channel is False
    assert f"id {BASE_ROLE}" in fault and "/bot base-role" in fault


async def test_permissions_are_applied_again_to_the_hub_set(db_path):
    await _configure(db_path, hub_channel_id=HUB)
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))

    assert await reapply_hub_permissions(bot, bot.config_service) is None

    channel.edit.assert_awaited_once()


async def test_permissions_are_left_alone_while_no_hub_is_set(db_path):
    channel = _hub_channel()
    bot = _bot(db_path, _guild(channel))

    assert await reapply_hub_permissions(bot, bot.config_service) is None

    channel.edit.assert_not_awaited()


# ── Start-up ──────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_restart_routes_every_registered_option_and_refreshes(league):
    """Every option, offered or not: a stale press is refused in words, not in silence. The
    panel stands in the hub; after a restart every option on the bot's hub options has its button
    routed again, and the panel's refresh is asked of the queue rather than made at once."""
    standing = await league.hub.send("old panel")
    await _configure(league.db_path, hub_message_id=standing.id)
    _offer(league, _option("on", offered=_offered(True)), _option("off", offered=_offered(False)))

    await recover_hub(league.bot, queue=league.bot.change_queue)

    (view,), _ = league.bot.add_view.call_args
    assert sorted(b.custom_id for b in view.children) == ["hub:off", "hub:on"]
    standing.edit.assert_not_awaited()
    assert [r["state"] for r in await _refreshes(league)] == ["QUEUED"]


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_the_start_up_asks_for_the_panel_before_the_queue_starts(league):
    """The hub is set, no panel stands and the bot's hub options hold no option. The start-up
    asks the queue for the panel, as the bot's, and posts nothing itself; the panel is posted,
    and its id kept, once the queue runs."""
    _offer(league)
    await recover_hub(league.bot, queue=league.bot.change_queue)

    assert league.hub.sent == []
    [change] = await _refreshes(league)
    assert change["state"] == "QUEUED"
    await run_queue(league.bot)
    assert "Nothing is offered here yet." in league.hub.sent[0]
    assert await _stored_panel(league) == league.hub.posted[0].id


@pytest.mark.xfail(strict=True, reason=ON_THE_BOT)
async def test_a_restart_with_no_option_registered_adds_no_view(league):
    """The bot's hub options hold no option: a restart routes no view."""
    _offer(league)
    await recover_hub(league.bot, queue=league.bot.change_queue)

    league.bot.add_view.assert_not_called()
