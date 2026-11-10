"""`core.team_add` and `core.team_remove`: `/team add` and `/team remove`, on the change queue.

Each is a change type of its own on the league lane, registered by the builder. A team is added
under a shorthand, a full name and a role, or removed by its shorthand, from the server's list, which
is open while no season's configuration is confirmed. The team is named at the ask, and the change
is one job, **the save**, which writes the team's row (`TeamService.add_default_team_on`,
`remove_default_team_on`), its role mapping (`PlacementService.set_team_role_config_on`,
`delete_team_role_config_on`), an audit entry for each (`TEAM_ADD` or `TEAM_REMOVE`, then
`TEAM_ROLE_CONFIG`) and the line that records the change, all stamped with the queue's clock and
saved whole or not at all.

**Every refusal is the check's,** at the ask and again as the change starts, in the log channel
either time: the list fixed once a season's configuration is confirmed, a role the bot cannot grant,
a role another team holds (a role belongs to one team only), and what `TeamService` refuses (the
protected Reserve name, a name taken, a name the rules refuse, a team no longer on the list). A
refusal found inside the save, a role taken or a name added between the check and the write, ends
the change in its words, writing nothing, and does not stop the lane: the save judges each again
before it writes the first. A fault in the save stops the lane, as a failed job does, and nothing of
the change is kept.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import AuditRecord, LEAGUE_LANE, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.services.season_service import live_season
from leaguebot.core.services.team_service import TeamService, resolve_team_reference
from leaguebot.core.utils.channel_guard import role_grant_refusal
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

TEAM_ADD = "core.team_add"
TEAM_REMOVE = "core.team_remove"

#: The name of the job, as the outcome and the tests know it.
SAVE = "save"

#: The reply once a league admin has discarded the stopped save, for each change.
NOTHING_ADDED = (
    'Nothing was added: "{full_name}" is not in the server\'s team list. Run `/team add` again.'
)
NOTHING_REMOVED = (
    'Nothing was removed: "{full_name}" stays in the server\'s team list. Run `/team remove` '
    "again."
)

_LIST_FIXED = (
    "⛔ The team list is fixed for Season {season} now that its configuration has been confirmed. "
    "`/team {command}` is available again once the season has ended, or while a new season is in "
    "configuration."
)


async def list_lock_refusal(db_path: str, command: str) -> Verdict | None:
    """The refusal of a change to the team list once a season's configuration is confirmed: the
    list is settled while the live season stands in Configuration, or while none is live, and fixed
    for the rest of the season from the moment its configuration is confirmed, since the signup
    wizard offers it as the preferred teams and every division is created from it."""
    season = await live_season(db_path)
    if season is None or season.stage is SeasonStage.CONFIGURATION:
        return None
    return Verdict.refuse(_LIST_FIXED.format(season=season.season_number, command=command))


def _saved(ctx: OutcomeContext) -> dict[str, Any] | None:
    """What the save returned, or None where it did not go through: a league admin discarded it."""
    view = next((each for each in reversed(ctx.steps) if each.name == SAVE), None)
    if view is None or not view.done or not view.result or not view.result.get("saved"):
        return None
    return view.result


def _refused(ctx: OutcomeContext) -> str | None:
    """The reply of a refusal the save found, or None."""
    view = next((each for each in reversed(ctx.steps) if each.name == SAVE), None)
    refused = (view.result or {}).get("refused") if view is not None else None
    return str(refused) if refused else None


def _refused_in_save(ctx: StepContext, refusal: ValueError) -> StepResult:
    """The result of a save that found a refusal: nothing written, the log channel told."""
    reply = f"⛔ {refusal}"
    return StepResult(
        result={"refused": reply},
        lines=(refusal_line(ctx.named, ctx.what, reply_reason(reply)),),
    )


def _actor(ctx: StepContext) -> None:
    if ctx.actor_id is None or ctx.actor_name is None:
        raise RuntimeError("a change to the team list is the act of a member, and none is recorded")


def team_add_change(
    *, teams: TeamService, placement: PlacementService, now: Callable[[], datetime]
) -> ChangeType:
    """The `core.team_add` change type, for the builder to register: *teams* and *placement* are
    what the check judges the team through and the save writes it through, *now* the queue's clock.

    The payload names the team (`shorthand` and `full_name`, as the form's submission trimmed them)
    and its role (`role_id`, and `role_name` as the line names it).
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals of the list's lock, the role and the team, each in its words."""
        payload = ctx.payload
        locked = await list_lock_refusal(ctx.db_path, "add")
        if locked is not None:
            return locked
        role_id = int(payload["role_id"])
        guild = await league_guild(ctx.bot)
        role = None if guild is None else guild.get_role(role_id)
        if role is None:
            return Verdict.refuse(
                f"⛔ <@&{role_id}> is no longer a role on the server. Choose a role of the team's "
                "own."
            )
        refusal = role_grant_refusal(role)
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}")
        holder = await placement.team_holding_role(role_id)
        if holder is not None:
            return Verdict.refuse(
                f'⛔ <@&{role_id}> is already the role of "{holder}". A role belongs to one team '
                "only."
            )
        try:
            async with get_connection(ctx.db_path) as db:
                await teams.judge_add_on(db, payload["shorthand"], full_name=payload["full_name"])
        except ValueError as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        _actor(ctx)
        payload = ctx.payload
        shorthand, full_name = payload["shorthand"], payload["full_name"]
        role_id = int(payload["role_id"])
        try:
            # Everything is judged before the first write, so a refusal leaves nothing behind.
            await teams.judge_add_on(db, shorthand, full_name=full_name)
            old_role_id = await placement.set_team_role_config_on(
                db, shorthand, role_id, now=now()
            )
            await teams.add_default_team_on(db, shorthand, full_name=full_name)
        except ValueError as refusal:
            return _refused_in_save(ctx, refusal)
        line = (
            f"{ctx.named} | /team add | Success\n"
            f"  team: {full_name}\n"
            f"  shorthand: {shorthand}\n"
            f"  role: {payload['role_name']} (<@&{role_id}>)"
        )
        return StepResult(
            result={"saved": True},
            audits=(
                AuditRecord(
                    "TEAM_ADD", None, {"shorthand": shorthand, "full_name": full_name}
                ),
                AuditRecord(
                    "TEAM_ROLE_CONFIG",
                    {"team": shorthand, "role_id": old_role_id},
                    {"team": shorthand, "role_id": role_id},
                ),
            ),
            lines=(line,),
        )

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if _saved(ctx) is None:
            return NOTHING_ADDED.format(**ctx.payload)
        return (
            f'✅ Team "{ctx.payload["full_name"]}" added as `{ctx.payload["shorthand"]}`, with '
            f"role <@&{ctx.payload['role_id']}>."
        )

    return ChangeType(
        kind=TEAM_ADD,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEAM_ADD}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda payload: f"Adding **{payload['full_name']}**",
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


def team_remove_change(
    *, teams: TeamService, placement: PlacementService
) -> ChangeType:
    """The `core.team_remove` change type, for the builder to register: *teams* and *placement*
    are what the check judges the team through and the save removes it through.

    The payload names the team by its `shorthand`, and by its `full_name` as the league was told
    it at the ask. The Reserve team is protected, so no payload names it.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals of the list's lock and of the team, each in its words."""
        locked = await list_lock_refusal(ctx.db_path, "remove")
        if locked is not None:
            return locked
        try:
            async with get_connection(ctx.db_path) as db:
                await _judge_remove(teams, db, ctx.payload["shorthand"])
        except ValueError as refusal:
            return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        _actor(ctx)
        shorthand = ctx.payload["shorthand"]
        try:
            full_name = await _judge_remove(teams, db, shorthand)
        except ValueError as refusal:
            return _refused_in_save(ctx, refusal)
        await teams.remove_default_team_on(db, shorthand)
        role_id = await placement.delete_team_role_config_on(db, shorthand)
        audits = [AuditRecord("TEAM_REMOVE", {"shorthand": shorthand, "full_name": full_name}, None)]
        if role_id is not None:
            audits.append(
                AuditRecord(
                    "TEAM_ROLE_CONFIG",
                    {"team": shorthand, "role_id": role_id},
                    {"team": shorthand, "role_id": None},
                )
            )
        line = f"{ctx.named} | /team remove | Success\n  team: {full_name}\n  shorthand: {shorthand}"
        return StepResult(result={"saved": True}, audits=tuple(audits), lines=(line,))

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if _saved(ctx) is None:
            return NOTHING_REMOVED.format(**ctx.payload)
        return f'✅ Team "{ctx.payload["full_name"]}" removed from the server list.'

    return ChangeType(
        kind=TEAM_REMOVE,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, save)},
        check=check,
        key=lambda payload: f"{TEAM_REMOVE}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda payload: f"Removing **{payload['full_name']}**",
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


async def _judge_remove(teams: TeamService, db: aiosqlite.Connection, shorthand: str) -> str:
    """The full name of the team *shorthand* names, read on *db*. Raises ValueError, in the words a
    league is told, where the list holds no such team or it is the protected Reserve team."""
    reference = resolve_team_reference(
        shorthand, await teams.teams_with_roles_on(db), scope=" in the server's list"
    )
    if reference.team is None:
        raise ValueError(str(reference.refusal))
    await teams.judge_remove_on(db, reference.team["name"])
    return str(reference.team["full_name"])


__all__ = [
    "NOTHING_ADDED", "NOTHING_REMOVED", "TEAM_ADD", "TEAM_REMOVE", "list_lock_refusal",
    "team_add_change", "team_remove_change",
]
