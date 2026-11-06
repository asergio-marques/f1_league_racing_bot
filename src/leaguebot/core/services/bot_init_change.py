"""`core.bot_init`: `/bot init`, on the change queue.

Not a `Setting` (`setting_change.py`): a setting writes one column of a server already
claimed, where `/bot init` claims the server itself, which no setting does. One SAVE step
claims it, writes the four settings and the `BOT_INITIALISED` audit entry in one save
(`ConfigService.claim_on`, on the save's own connection), stamped with the queue's clock. A
fault in it, or a check that raises as the change starts, ends the change on a fault rather
than stopping the lane (`Step.on_fault`, `ChangeType.on_check_fault`, `NOTHING_SAVED`, as
`setting_change` reuses them): no league admin role exists yet, before a first `/bot init`,
for anyone to clear a stop with.

**Three refusals are the save's to judge, not the check's:**

- **Another server already holds the league.** Judged again as the change starts, because a
  second `/bot init` asked before the first has run may lose the claim to it; recorded to the
  host's log alone, as it is at the ask, there being no league of this server's to log to
  (`docs/wip-specs/core_specification.md`, "The record of what changed").
- **A factory reset is still cleaning up Discord.** Judged only at the ask
  (`BotCog.handle_bot_init`): the flag it reads (`BotCog._clean_up`) is kept in memory on the
  cog, never in the database the queue reads, so it cannot be this module's `check` (what a
  change type refuses must be read from the database and the queue,
  `docs/design/architecture.md`, "How a change is carried out"). A `/bot init` asked while a
  reset is wiping the database is gone with every other queued change
  (`in_memory_state.clear_in_memory_state`), so none is ever left to judge this again as it
  starts.
- **The claim is lost to a race**, `claim_on` finding the row already claimed by the time the
  save runs: judged exactly where the claim is taken, never by a read beforehand that a second
  writer could still overtake. Whichever refusal fits whoever won is answered: another server's
  above, or this server's own claim, taken first by another save — the narrower "already
  configured" that `check` has not already caught.

None of the three can be a `Verdict.refuse` from `check`: that path always writes a line to the
league's log channel (`ChangeQueue._refuse`), which the first two must not reach, there being no
log channel of this server's league to write to yet. Instead the save reports them for itself, in
the step's own `result`, read back by `_outcome` to answer the member. Another server's claim and
a reset cleaning up queue no line; the host's log is written directly, as `_refuse_on_host` writes
it for the same two refusals judged at the ask. The lost race to this server's own claim queues its
refusal line on the save's connection, the claim just taken by the winner having set the log
channel it goes to.

**"Already configured" is `check`'s refusal**, judged at the ask and again as the change
starts, in the league's log channel both times, as any other member's refusal is
(`docs/wip-specs/core_specification.md`, "How a change is carried out": "A member's change
refused when it comes up to run shall be refused as ever, and the lane shall go on"). A second
`/bot init` queued behind a first that claims the server meets this refusal as it starts, word
for word as it would at the ask.

**The line, once claimed, goes to the new log channel** (`OutputRouter.queue_log_on`, on the
save's own connection, after the claim): the server just configured is told about itself, as
every other settings command's line is.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.change import (
    AuditRecord,
    LEAGUE_LANE,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.server_config import ServerConfig
from leaguebot.core.services.change_queue import ChangeType, CheckContext, Step, StepContext
from leaguebot.core.services.setting_change import NOTHING_SAVED
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

if TYPE_CHECKING:
    from leaguebot.core.services.config_service import ConfigService

log = logging.getLogger(__name__)

KIND = "core.bot_init"

#: The name of the step that claims the server, writes the four settings and the audit entry.
SAVE = "save"

#: `/bot init` refused because the server is already configured, word for word as the cog
#: refuses it at the ask: shown to the member, and the log channel's line's reason.
ALREADY_CONFIGURED = (
    "⚠️ This server is already configured, and `/bot init` runs once.\n"
    "To change a setting use `/bot log-channel`, `/bot interaction-channel`, "
    "`/bot interaction-role` and `/bot admin-role`.\n"
    "To move the league to another server, use `/bot pack` first."
)

#: Why another server is refused, word for word as `BotCog._refuse_on_host` refuses it at the
#: ask: one bot serves one league.
ANOTHER_SERVER = (
    "⛔ This bot already serves the league on another server. One bot serves one league."
)

#: The command as the line and the member's reply name it.
COMMAND = "/bot init"

#: The four settings `/bot init` claims: the column, the reply's label, and the line's own key
#: for it. The reply names each by its mention alone; the line adds a role's name beside its
#: mention, a channel's mention being clickable and so self-naming.
_SETTINGS = (
    ("league_admin_role_id", "League admin role", "league_admin_role"),
    ("interaction_role_id", "Interaction role", "interaction_role"),
    ("interaction_channel_id", "Interaction channel", "interaction_channel"),
    ("log_channel_id", "Log channel", "log_channel"),
)

#: The payload key holding a role's name, by column, for the line alone.
_ROLE_NAME_KEYS = {
    "league_admin_role_id": "league_admin_role_name",
    "interaction_role_id": "interaction_role_name",
}


def _mention(column: str, value: int) -> str:
    return f"<#{value}>" if column.endswith("channel_id") else f"<@&{value}>"


def _line_detail(column: str, line_key: str, payload: dict[str, Any], value: int) -> str:
    """One setting's line: a role's name beside its mention (wrapped in backticks by
    `OutputRouter._as_log_line`), a channel's mention alone, already clickable."""
    name_key = _ROLE_NAME_KEYS.get(column)
    mention = _mention(column, value)
    return f"{line_key}: {payload[name_key]} ({mention})" if name_key else f"{line_key}: {mention}"


def _check(config: "ConfigService") -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(ctx: CheckContext) -> Verdict:
        """"Already configured" alone: this server, asking again, is refused word for word, both
        at the ask and again as the change starts, in the league's log channel either time.
        Another server holding the league, and a factory reset cleaning up, are the save's to
        judge — see the module: a config claimed by a server other than this payload's is not
        this refusal."""
        league_server_id = await config.get_league_server_id()
        if league_server_id is not None and league_server_id == int(ctx.payload["server_id"]):
            return Verdict.refuse(ALREADY_CONFIGURED)
        return Verdict.go()

    return check


def _doing(_payload: dict[str, Any]) -> str:
    return "Saving the bot configuration"


def _save(config: "ConfigService") -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Claim the server, write the four settings and the `BOT_INITIALISED` audit entry, one
        save.

        `claim_on`'s own condition is where another server holding the league and a lost race
        are both found, there being no read beforehand that a second writer could still overtake:
        either way the claim is not taken, and this step tells which. Another server is answered
        without a line in the league's log channel — there being none of this server's to write
        to — and logged to the host directly. Already configured is this server's own claim,
        taken first by another save: its line goes to the log channel that claim has just set, on
        this save's connection, as any other refusal recorded as the change starts does.
        """
        payload = ctx.payload
        server_id = int(payload["server_id"])

        claimed = {
            "server_id": server_id,
            "interaction_role_id": int(payload["interaction_role_id"]),
            "league_admin_role_id": int(payload["league_admin_role_id"]),
            "interaction_channel_id": int(payload["interaction_channel_id"]),
            "log_channel_id": int(payload["log_channel_id"]),
        }
        created = await config.claim_on(
            db,
            ServerConfig(
                server_id=claimed["server_id"],
                interaction_role_id=claimed["interaction_role_id"],
                league_admin_role_id=claimed["league_admin_role_id"],
                interaction_channel_id=claimed["interaction_channel_id"],
                log_channel_id=claimed["log_channel_id"],
            ),
        )
        if not created:
            # The claim was not taken: either another server already holds the league, or this
            # one's own claim went through on another save first.
            now_held = await config.get_league_server_id()
            if now_held != server_id:
                log.info(
                    "%s refused for %s (%s): another server already holds the league",
                    ctx.what, ctx.named, ctx.actor_id,
                )
                return StepResult(result={"outcome": "another_server"})
            log.info(
                "%s refused for %s (%s): already configured",
                ctx.what, ctx.named, ctx.actor_id,
            )
            return StepResult(
                result={"outcome": "already_configured"},
                lines=(refusal_line(ctx.named, COMMAND, reply_reason(ALREADY_CONFIGURED)),),
            )

        details = tuple(
            _line_detail(column, line_key, payload, claimed[column])
            for column, _label, line_key in _SETTINGS
        )
        line = "\n".join(
            [f"{ctx.named} | {COMMAND} | Success", *(f"  {detail}" for detail in details)]
        )
        return StepResult(
            result={"outcome": "saved", **claimed},
            audits=(
                AuditRecord("BOT_INITIALISED", dict.fromkeys(claimed), claimed),
            ),
            lines=(line,),
        )

    return save


def _on_fault(ctx: StepContext, error: BaseException) -> StepResult:
    """A fault in the save: nothing saved, the member told to run `/bot init` again, and the
    host's log recording it — there being no log channel to write to while the claim is still
    unmade (`_failed` in `setting_change.py` is not reused: it would queue a line to a log
    channel that may not exist yet)."""
    log.error("%s failed for %s (%s)", ctx.what, ctx.named, ctx.actor_id, exc_info=error)
    return StepResult(result={"outcome": "faulted"})


def _fault_reply(what: str) -> str:
    """The member's reply to a fault: nothing saved, `/bot init` to run again, and the fault
    recorded in the host's log, the only place it goes before a log channel is claimed."""
    return failure_reply(
        what, NOTHING_SAVED.format(command=COMMAND), recorded="the bot host's log"
    )


def _check_fault_reply(what: str, _payload: dict[str, Any], _error: BaseException) -> str:
    """A check that raises as the change starts ends it the same way a fault in the save does
    (`ChangeType.on_check_fault`): nothing is saved, and the member is told to run `/bot init`
    again. Without this, a raised check would stop the league lane with no league admin role yet
    to clear it (see the module docstring)."""
    return _fault_reply(what)


def _outcome(ctx: Any) -> str:
    results = {view.name: dict(view.result) for view in ctx.steps if view.result is not None}
    saved = results.get(SAVE, {})
    if saved.get("outcome") == "faulted":
        return _fault_reply(ctx.what)
    if saved.get("outcome") == "already_configured":
        return ALREADY_CONFIGURED
    if saved.get("outcome") == "another_server":
        return ANOTHER_SERVER
    lines = "\n".join(
        f"**{label}**: {_mention(column, saved[column])}" for column, label, _key in _SETTINGS
    )
    return f"✅ Bot configuration saved!\n{lines}"


def bot_init_change(*, config: "ConfigService") -> ChangeType:
    """The `core.bot_init` change type, for the builder to register, *config* read and written as
    `_check` and `_save` need it: a service is handed what it needs by whoever builds it, never
    looked up on the bot (`docs/design/architecture.md`, "How modules and core fit together")."""
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(config), on_fault=_on_fault)},
        check=_check(config),
        key=lambda payload: f"{KIND}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("server_configs",),
        on_check_fault=_check_fault_reply,
    )


__all__ = ["ALREADY_CONFIGURED", "ANOTHER_SERVER", "KIND", "bot_init_change"]
