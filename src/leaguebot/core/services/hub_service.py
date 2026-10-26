"""The hub (issue #279): one channel every member of the league may use, and one panel in it.

Every command the bot has is a league manager's or a league admin's. The hub is the one
surface for everybody else: a channel set by `/bot hub-channel`, holding a single panel of
buttons. A button asks no tier; who may see the channel is who may press it.

**Core owns the channel and the panel; each module says what it adds.** A module registers a
`HubOption` at import, naming when it is offered — typically while the module is enabled — and
what a press does. This service knows none of them by name and registers nothing itself. Core
adds one option of its own, About, from `core/services/about_service.py` and offered always (decided
2026-09-22, #258); every other option is a module's. A panel offering nothing says so.

**A press is judged when it is made, not when the panel was posted.** A panel is refreshed
whenever what it offers may have changed, but a press can still arrive on one posted before a
module was disabled. The option is looked up by its key and asked again whether it is offered;
one that is not is refused, and the panel refreshed. The key is part of the button's custom id,
`hub:<key>`, and so must never change once an option has shipped.

**One persistent view carries every registered option.** Registered at start-up by
`recover_hub`, so a button survives a restart whether or not its option is offered at that
moment: the refusal above is the better answer to a stale press than silence.

**The channel is read-only to all but the bot.** The base role — or every member, where the
league has set none — and both tier roles may see it and press its buttons; nobody but the bot
may post there, so the panel is never pushed out of sight. The overwrites replace the channel's
own, as the signup channel's do, and are applied again whenever one of the three roles changes.
A base role that is set but has been deleted from the server keeps the hub **closed**, and says
so: opening it to every member would read a deleted role as a league that chose to have none.

**The panel is refreshed, never assumed.** `refresh_panel` edits the panel in place where it
stands and posts it again where it has been deleted, keeping the new message's id. That is one
job, "edit, or post again where gone", because the panel standing is the hub's own rule and the
job reads it as it runs. The refresh is the change queue's `hub.refresh`, asked when the hub is
set or moved, when a module is enabled or disabled, at start-up, and after a stale press; a panel
Discord refuses, or a hub channel set but gone, stops the queue for Retry and Discard. The queue
runs one refresh at a time on the league's lane, so two cannot both find the panel missing and
post two, and a burst of stale presses asks one while the first waits. The new message's id is
saved in the save that marks the job done.

**A panel's buttons reach the queue by being handed it.** The view the job posts and the view the
start-up routes are built with the change queue, which the builder gives `hub_refresh_change` and
`recover_hub`; a stale press asks the refresh of it. This service never looks the queue up on the
bot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable

import aiosqlite
import discord

from leaguebot.core.models.change import (
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.standing_post_handler import edit_standing, post_standing
from leaguebot.core.services.standing_posts import delete_replaced_step, remove_kept
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import CallbackButton, LeagueView, league_guild
from leaguebot.core.utils.log_lines import refuse

log = logging.getLogger(__name__)

__all__ = [
    "HubOption",
    "HubPanelView",
    "CUSTOM_ID_PREFIX",
    "register_option",
    "registered_options",
    "offered_options",
    "render_panel",
    "refresh_panel",
    "set_hub_message_on",
    "hub_refresh_change",
    "apply_hub_permissions",
    "reapply_hub_permissions",
    "recover_hub",
]

#: Every hub button's custom id starts so; the option's key follows.
CUSTOM_ID_PREFIX = "hub:"

PANEL_HEADING = "🏁 **League hub**"
PANEL_EMPTY = "Nothing is offered here yet."
PANEL_OFFERING = "Press an option below."
NO_LONGER_OFFERED = "⛔ That option is no longer offered here. The panel has been refreshed."


@dataclass(frozen=True)
class HubOption:
    """One button of the hub's panel, and what pressing it does.

    *key* is the button's identity, carried in its custom id: stable for as long as the option
    exists. *order* places it on the panel, lowest first, the key settling a tie. *offered* is
    asked each time the panel is drawn and each time the button is pressed; ``None`` offers it
    always. *respond* answers a press, which has passed the league-server check already.
    """

    key: str
    label: str
    order: int
    respond: Callable[[discord.Interaction], Awaitable[None]]
    offered: Callable[[Any], Awaitable[bool]] | None = None


_OPTIONS: dict[str, HubOption] = {}


def register_option(option: HubOption) -> None:
    """Add *option* to the panel's registry. A second option under one key is refused.

    Registering the same option twice is harmless, as a module imported twice would do; two
    options sharing a key would route one's presses to the other.
    """
    existing = _OPTIONS.get(option.key)
    if existing is not None and existing is not option:
        raise ValueError(f"a hub option is already registered as {option.key!r}")
    _OPTIONS[option.key] = option


def registered_options() -> list[HubOption]:
    """Every registered option, in panel order."""
    return sorted(_OPTIONS.values(), key=lambda option: (option.order, option.key))


async def is_offered(bot: LeagueBot, option: HubOption) -> bool:
    return option.offered is None or bool(await option.offered(bot))


async def offered_options(bot: LeagueBot) -> list[HubOption]:
    """The options the panel carries now, in panel order."""
    return [option for option in registered_options() if await is_offered(bot, option)]


def render_panel(
    options: list[HubOption], queue: ChangeQueue | None = None
) -> tuple[str, "HubPanelView | None"]:
    """The panel's text, and its view — ``None`` where nothing is offered. A view carries *queue*,
    which a stale press asks for the refresh; none, and a stale press only refuses."""
    if not options:
        return f"{PANEL_HEADING}\n{PANEL_EMPTY}", None
    return f"{PANEL_HEADING}\n{PANEL_OFFERING}", HubPanelView(options, queue)


class HubPanelView(LeagueView):
    """The hub's panel: one button per option, persistent, dispatching by the option's key.

    Built with the options offered when the panel is drawn, and at start-up with every option
    registered, so that each key is routed after a restart. *queue* is the change queue, handed
    by whoever builds the view (the refresh job, the start-up), for a stale press to ask the
    refresh of.
    """

    def __init__(self, options: list[HubOption], queue: ChangeQueue | None = None) -> None:
        super().__init__(timeout=None)
        for option in options:
            button = CallbackButton(
                label=option.label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{CUSTOM_ID_PREFIX}{option.key}",
                on_press=_callback_for(option.key, queue),
            )
            self.add_item(button)


def _callback_for(
    key: str, queue: ChangeQueue | None
) -> Callable[[discord.Interaction], Awaitable[None]]:
    async def callback(interaction: discord.Interaction) -> None:
        await press(interaction, key, queue)

    return callback


async def press(
    interaction: discord.Interaction, key: str, queue: ChangeQueue | None = None
) -> None:
    """Answer a press on the button keyed *key*, judging the option as it stands now.

    A press on an option no longer offered is a refusal of a button that tries to act, so it is
    recorded in the log channel through `refuse`, the option named by its label, or by its key
    where no module registers it any more. The member is answered as ever, and the panel's
    refresh is asked of *queue*, the bot's own change: a second stale press before the first
    refresh starts is dropped as the repeat it is.
    """
    bot = bot_of(interaction)
    option = _OPTIONS.get(key)
    if option is None or not await is_offered(bot, option):
        what = f"the hub's `{key}` option" if option is None else f"the hub's “{option.label}” option"
        await refuse(interaction, NO_LONGER_OFFERED, what=what)
        if queue is not None:
            await queue.ask(
                HUB_REFRESH,
                {"command": f"a stale press on {what}"},
                actor=interaction.user,
                what="refreshing the hub panel",
                origin=ChangeOrigin.BOT,
            )
        return
    await option.respond(interaction)


# ── Posting and refreshing the panel ──────────────────────────────────────


async def _hub_channel(bot: LeagueBot) -> tuple[Any, discord.Guild | None, Any, str | None]:
    """The server configuration, the league's guild, the hub channel and a fault, where any.

    The channel is None, with no fault, where no hub is set or the league's server is not to
    hand; None with a fault where the channel set is no longer in the server.
    """
    cfg = await bot.config_service.get_server_config()
    if cfg is None or cfg.hub_channel_id is None:
        return cfg, None, None, None
    guild = await league_guild(bot)
    if guild is None:
        return cfg, None, None, None
    channel = guild.get_channel(cfg.hub_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return cfg, guild, None, (
            f"The hub channel (id {cfg.hub_channel_id}) is not in the server."
        )
    return cfg, guild, channel, None


async def _hub_is_set(bot: LeagueBot) -> bool:
    cfg, _guild, _channel, _fault = await _hub_channel(bot)
    return cfg is not None and cfg.hub_channel_id is not None


async def set_hub_message_on(db: aiosqlite.Connection, message_id: int | None) -> None:
    """Write the id of the hub's panel on *db*, committing nothing: the refresh job's record, in
    the save that marks the job done."""
    await db.execute(
        "UPDATE server_configs SET hub_message_id = ? WHERE server_id IS NOT NULL", (message_id,)
    )


@posts_in_a_job
async def refresh_panel(
    bot: LeagueBot, queue: ChangeQueue, kept: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Bring the panel in line with what is offered now: the body of the `hub.refresh` job.

    Edits the panel where it stands; posts it where there is none or it has been deleted (both
    through the standing-post handler, notifying nobody), and
    gives back `{"new": <id>, "channel_id": <id>}` for the job's record to save where it posted, and
    `{"edited": <id>, "channel_id": <id>}`, which saves nothing, where it edited. Gives None only
    where no hub channel is set. The panel's buttons are handed *queue*. *kept* is what the job's
    last try posted and could not record, removed before the panel is posted again.

    Raises `StepFailedOnDiscord`, naming `/bot hub-channel`, where the channel set is no longer in
    the server, and where Discord refuses the edit or the post; `GuildUnavailable` where the
    league's server is not to hand.
    """
    await remove_kept(bot, kept)
    cfg, _guild, channel, fault = await _hub_channel(bot)
    if cfg is None or cfg.hub_channel_id is None:
        return None
    if channel is None:
        if fault is None:
            raise GuildUnavailable("the league's server is not in the cache")
        raise StepFailedOnDiscord(f"{fault} Set one with `/bot hub-channel`, then press Retry.")
    content, view = render_panel(await offered_options(bot), queue)
    if cfg.hub_message_id is not None:
        try:
            await edit_standing(
                channel.get_partial_message(cfg.hub_message_id),
                mentions=discord.AllowedMentions.none(),
                content=content,
                view=view,
            )
            return {"edited": cfg.hub_message_id, "channel_id": channel.id}
        except discord.NotFound:
            pass  # deleted by hand: posted again below
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"The hub panel in <#{channel.id}> could not be updated: {exc}"
            ) from exc
    try:
        message_id = await post_standing(
            channel, content, mentions=discord.AllowedMentions.none(), view=view
        )
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            f"The hub panel could not be posted in <#{channel.id}>: {exc}"
        ) from exc
    return {"new": message_id, "channel_id": channel.id}


#: The kind of the change that refreshes the panel.
HUB_REFRESH = "hub.refresh"
_REFRESH_STEP = "refresh"
DELETE_OLD = "delete_old"


def hub_refresh_change(queue: ChangeQueue) -> ChangeType:
    """The change that brings the hub's panel up to date, asked for by the bot.

    Asked in the save of whatever may have changed what the panel offers, so that the panel is
    refreshed as a change of its own, after the steps of the change that asked for it. The payload's
    ``command`` names what caused it. A hub not set is not due. The panel is refreshed by one job,
    `refresh_panel`, which a refusal stops the queue at; *queue* is handed to the panel's buttons.
    A hub that has moved names the panel it left, ``old_channel_id`` and ``old_message_id``: the old
    panel is deleted by a job of its own after the new is posted, by the shared delete step, so
    that the league is never left without one and a refused deletion stops the queue.
    Not repeatable: a refresh asked for a second time before the first starts is dropped, since the
    first will draw the panel as it stands then.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await _hub_is_set(ctx.bot):
            return Verdict.not_due("no hub channel is set")
        return Verdict.go()

    async def still_due(ctx: StepContext) -> bool:
        return await _hub_is_set(ctx.bot)

    async def refresh(ctx: StepContext) -> StepResult:
        posted = await refresh_panel(ctx.bot, queue, ctx.kept)
        if posted is None:
            return StepResult()
        old_channel, old_message = ctx.payload.get("old_channel_id"), ctx.payload.get("old_message_id")
        if old_channel is None or old_message is None:
            return StepResult(result=posted)
        if (int(old_channel), int(old_message)) == (
            posted["channel_id"], posted.get("new", posted.get("edited"))
        ):
            return StepResult(result=posted)  # the panel standing is the one named: keep it
        # A hub moved: the new panel is up, whether this job posted it or an earlier one did, so
        # the old comes down after it and never before.
        return StepResult(result=posted, then=(PlannedStep(DELETE_OLD, {
            "channel_id": int(old_channel), "message_id": int(old_message), "what": "hub panel",
        }),))

    async def record(db: aiosqlite.Connection, _ctx: StepContext, result: StepResult) -> None:
        new = result.result.get("new")
        if new is not None:
            await set_hub_message_on(db, int(new))

    async def describe(_ctx: StepContext) -> str:
        return "refreshing the hub panel"

    async def describe_old(ctx: StepContext) -> str:
        return f"deleting the old hub panel in <#{ctx.step_payload['channel_id']}>"

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=HUB_REFRESH,
        opening=(PlannedStep(_REFRESH_STEP),),
        steps={
            _REFRESH_STEP: Step(
                _REFRESH_STEP, StepKind.ACT, refresh, still_due=still_due, describe=describe,
                record=record,
            ),
            DELETE_OLD: replace(delete_replaced_step(DELETE_OLD), describe=describe_old),
        },
        check=check,
        key=lambda payload: f"{HUB_REFRESH}|{payload.get('command', '')}",
        doing=lambda _payload: "Refreshing the hub panel",
        outcome=outcome,
    )


# ── Who may see the hub ───────────────────────────────────────────────────


async def apply_hub_permissions(bot: LeagueBot, guild: discord.Guild, channel: Any) -> str | None:
    """Make *channel* the hub's: read-only to all but the bot. Returns a fault, or None.

    Replaces the channel's own overwrites. See the module docstring for who may see it, and
    for why a base role deleted from the server keeps the hub closed.
    """
    cfg = await bot.config_service.get_server_config()
    base_role_id = cfg.base_role_id if cfg is not None else None
    base_role = guild.get_role(base_role_id) if base_role_id is not None else None
    fault = None
    if base_role_id is not None and base_role is None:
        fault = (
            f"The base role (id {base_role_id}) is not in the server, so the hub <#{channel.id}> "
            f"is closed to members until `/bot base-role` sets one."
        )

    read_only = discord.PermissionOverwrite(view_channel=True, send_messages=False)
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=base_role_id is None, send_messages=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, embed_links=True
        ),
    }
    if base_role is not None:
        overwrites[base_role] = read_only
    if cfg is not None:
        for role_id in (cfg.interaction_role_id, cfg.league_admin_role_id):
            role = guild.get_role(role_id) if role_id else None
            if role is not None:
                overwrites[role] = read_only
    try:
        await channel.edit(overwrites=overwrites)
    except discord.HTTPException as exc:
        return f"The hub <#{channel.id}>'s permissions could not be set: {exc}"
    return fault


async def reapply_hub_permissions(bot: LeagueBot) -> str | None:
    """Apply the hub's permissions again, after one of the roles they name has changed."""
    _cfg, guild, channel, fault = await _hub_channel(bot)
    if channel is None or guild is None:
        return fault
    return await apply_hub_permissions(bot, guild, channel)


# ── Start-up ──────────────────────────────────────────────────────────────


async def recover_hub(bot: LeagueBot, *, queue: ChangeQueue) -> None:
    """Route every registered option's button after a restart, and ask the queue to refresh the panel.

    Every option is registered, not only those offered now: a press on one a module has
    since stopped offering is refused in words rather than failing in silence. The refresh is the
    bot's own change, asked before the queue starts; it is dropped where no hub is set. Routing
    a view is no post, so it is made here.
    """
    options = registered_options()
    if options:
        bot.add_view(HubPanelView(options, queue))
    await queue.ask(
        HUB_REFRESH,
        {"command": "the bot starting"},
        what="refreshing the hub panel",
        origin=ChangeOrigin.BOT,
    )
