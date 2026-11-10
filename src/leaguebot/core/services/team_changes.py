"""`core.team_add`, `core.team_remove`, `core.team_modify` and `core.team_reserve_role`: `/team add`,
`/team remove`, `/team modify` and `/team reserve-role`, on the change queue.

Each is a change type of its own on the league lane, registered by the builder. A team is added
under a shorthand, a full name and a role, or removed by its shorthand, from the server's list, which
is open while no season's configuration is confirmed. The team is named at the ask, and an add or
a remove is one job, **the save**, which writes the team's row (`TeamService.add_default_team_on`,
`remove_default_team_on`), its role mapping (`PlacementService.set_team_role_config_on`,
`delete_team_role_config_on`), an audit entry for each (`TEAM_ADD` or `TEAM_REMOVE`, then
`TEAM_ROLE_CONFIG`) and the line that records the change, all stamped with the queue's clock and
saved whole or not at all.

A modify changes a team's names, its role, or both, and the Reserve team's role is set or cleared
(the Reserve team's names are not a league's to change). Each is the save, which writes the team's
row (`modify_default_team_on`), its mapping (`set_team_role_config_on`, `rename_team_role_config_on`
or `delete_team_role_config_on`) and the audit entries (`TEAM_NAMES` where the names change, and one
`TEAM_ROLE_CONFIG` for the mapping, a rename and a new role together being one entry), and then, where
the role changed, one job for each driver seated in the team, **the role move**
(`role_moves.role_move_step`, planned by the save from what it read inside it), and last the job
that writes the line, so that it carries the drivers moved and what was not done. A role Discord
refuses a driver ends that driver's job and is reported, in the reply and the line, and the lane goes
on: the seat is the league's record and the role Discord's copy of it. Only a driver moved in full
is counted.

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
from leaguebot.core.models.season import Season, SeasonStage
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.core.services.role_moves import MOVE_ROLES, role_move_step
from leaguebot.core.services.season_service import live_season
from leaguebot.core.services.team_service import RESERVE_NAME, TeamService, resolve_team_reference
from leaguebot.core.utils.asset_slug import normalise
from leaguebot.core.utils.channel_guard import role_grant_refusal
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.log_lines import refusal_line, reply_reason

TEAM_ADD = "core.team_add"
TEAM_REMOVE = "core.team_remove"
TEAM_MODIFY = "core.team_modify"
TEAM_RESERVE_ROLE = "core.team_reserve_role"

#: The names of the jobs, as the outcome and the tests know them.
SAVE = "save"
CLOSE = "close"

#: The reasons Discord's audit log is given for the roles of the drivers seated in a team.
MODIFY_REASON = "Team's role changed"
RESERVE_REASON = "Reserve team's role changed"

#: The reply once a league admin has discarded the stopped save, for each change.
NOTHING_ADDED = (
    'Nothing was added: "{full_name}" is not in the server\'s team list. Run `/team add` again.'
)
NOTHING_REMOVED = (
    'Nothing was removed: "{full_name}" stays in the server\'s team list. Run `/team remove` '
    "again."
)

NOTHING_MODIFIED = (
    'Nothing was changed: "{was_full_name}" stands as it was. Run `/team modify` again.'
)
NOTHING_RESERVED = (
    "Nothing was changed: the Reserve team's role stands as it was. Run `/team reserve-role` "
    "again."
)

_GONE = '"{full_name}" is no longer in the server\'s team list.'
_NAMES_FIXED = (
    "⛔ The team list is fixed now that the season's configuration is confirmed, so a team's "
    "shorthand and full name cannot change. Only its role can, and nothing was written."
)
_SEASON_DONE = (
    "⛔ Every division of Season {season} is done, so `/team {command}` no longer has anything to "
    "act on — completing the season revokes the team roles. Repair the mapping once the season "
    "has ended."
)

_LIST_FIXED = (
    "⛔ The team list is fixed for Season {season} now that its configuration has been confirmed. "
    "`/team {command}` is available again once the season has ended, or while a new season is in "
    "configuration."
)


def list_lock_reply(season: Season | None, command: str) -> str | None:
    """What a change to the team list is answered once the configuration of *season*, the live
    one, is confirmed, else None: the list is settled while the live season stands in
    Configuration, or while none is live, and fixed for the rest of the season from the moment its
    configuration is confirmed, since the signup wizard offers it as the preferred teams and every
    division is created from it."""
    if season is None or season.stage is SeasonStage.CONFIGURATION:
        return None
    return _LIST_FIXED.format(season=season.season_number, command=command)


async def list_lock_refusal(db_path: str, command: str) -> Verdict | None:
    """`list_lock_reply` of the league's live season, as a refusal."""
    reply = list_lock_reply(await live_season(db_path), command)
    return None if reply is None else Verdict.refuse(reply)


def gone_reply(full_name: str) -> str:
    """What a change to the team *full_name* is answered where it has left the server's list."""
    return "⛔ " + _GONE.format(full_name=full_name)


def season_done_reply(season: Season | None, command: str) -> str | None:
    """What a role mapping is answered once every division of *season*, the live one, is finished
    or cancelled, else None.

    A team's role may be repaired in every stage of a live season but Pending completion, and
    with no live season at all the mapping is the server's own and free to
    change, the team list itself being settled on the same terms. So this refuses only where a
    season is live and has run its course: completing it revokes the team roles.
    """
    if season is None or season.stage is not SeasonStage.PENDING_COMPLETION:
        return None
    return _SEASON_DONE.format(season=season.season_number, command=command)


async def season_done_refusal(db_path: str, command: str) -> Verdict | None:
    """`season_done_reply` of the league's live season, as a refusal."""
    reply = season_done_reply(await live_season(db_path), command)
    return None if reply is None else Verdict.refuse(reply)


async def _role_refusal(
    ctx: CheckContext, placement: PlacementService, role_id: int, *,
    other_than: str | None = None, suffix: str = "",
) -> Verdict | None:
    """The refusal of the role *role_id* for a team, or None: a role no longer on the server, one
    the bot cannot grant, or one a team other than *other_than* holds, each in its words, *suffix*
    following it.

    Where the league's server is not in the cache the role cannot be looked up and is not refused
    for it: only the holder is checked, and a role job that finds no server ends with the problem
    named, as the queue's role step does."""
    guild = await league_guild(ctx.bot)
    if guild is not None:
        role = guild.get_role(role_id)
        if role is None:
            return Verdict.refuse(
                f"⛔ <@&{role_id}> is no longer a role on the server. Choose a role of the "
                f"team's own.{suffix}"
            )
        refusal = role_grant_refusal(role)
        if refusal is not None:
            return Verdict.refuse(f"⛔ {refusal}{suffix}")
    holder = await placement.team_holding_role(role_id, other_than=other_than)
    if holder is not None:
        return Verdict.refuse(
            f'⛔ <@&{role_id}> is already the role of "{holder}". A role belongs to one team '
            f"only.{suffix}"
        )
    return None


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
        refused = await _role_refusal(ctx, placement, int(payload["role_id"]))
        if refused is not None:
            return refused
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


def _moves(
    users: list[int], old_role: int | None, new_role: int | None
) -> tuple[PlannedStep, ...]:
    """The role move planned for each of *users*: *old_role* taken, *new_role* given."""
    return tuple(
        PlannedStep(
            MOVE_ROLES,
            {
                "user_id": user_id,
                "revoke": [] if old_role is None else [old_role],
                "grant": [] if new_role is None else [new_role],
            },
        )
        for user_id in users
    )


def _moved(ctx: OutcomeContext) -> int:
    """How many drivers the role moves moved in full: not one Discord refused, nor one who has
    left the server."""
    return sum(
        1 for view in ctx.steps
        if view.name == MOVE_ROLES and view.done
        and not (view.result or {}).get("problem") and not (view.result or {}).get("gone")
    )


def _problems(ctx: OutcomeContext) -> list[str]:
    """What the role moves left undone, in the order they ran: a role Discord refused."""
    return [
        str(view.result["problem"]) for view in ctx.steps
        if view.name == MOVE_ROLES and view.result and view.result.get("problem")
    ]


def _with_problems(reply: str, ctx: OutcomeContext) -> str:
    """*reply*, and beneath it what Discord refused, where it refused any."""
    problems = _problems(ctx)
    if not problems:
        return reply
    return reply + "\n⚠️ Done, but on Discord:\n" + "\n".join(f"• {each}" for each in problems)


def _close_step(command: str, details: Callable[[StepContext], str]) -> Step:
    """The job that writes the line of *command*, last, so that it carries what was not done:
    *details* is what follows the line's first, where the save went through."""

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        if _saved(ctx) is None:
            return StepResult(result={"closed": True})
        line = (
            f"{ctx.named} | {command} | Success\n{details(ctx)}"
            + "".join(f"\n  not done: {each}" for each in _problems(ctx))
        )
        return StepResult(result={"closed": True}, lines=(line,))

    async def describe(_ctx: StepContext) -> str:
        return f"recording `{command}`"

    return Step(CLOSE, StepKind.SAVE, close, describe=describe)


def team_modify_change(
    *, teams: TeamService, placement: PlacementService, now: Callable[[], datetime]
) -> ChangeType:
    """The `core.team_modify` change type, for the builder to register: *teams* and *placement*
    are what the check judges the team through and the save writes it through, *now* the queue's
    clock, and *placement* gives the roles of the drivers seated in the team.

    The payload names the team as it stood at the ask (`was_shorthand`, `was_full_name`), its
    names as they are to be (`shorthand`, `full_name`, the form's trimmed) and its new role
    (`role_id`, with `role_name` as the line names it; None where the role is not changed).
    """

    def names_changed(payload: dict[str, Any]) -> bool:
        return (payload["shorthand"], payload["full_name"]) != (
            payload["was_shorthand"], payload["was_full_name"]
        )

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals of the season, the team, the list's lock, the role and the names, each in
        its words."""
        payload = ctx.payload
        refused = await season_done_refusal(ctx.db_path, "modify")
        if refused is not None:
            return refused
        async with get_connection(ctx.db_path) as db:
            current = await teams.server_team_on(db, payload["was_shorthand"])
            if current is None:
                return Verdict.refuse(gone_reply(payload["was_full_name"]))
        if names_changed(payload) and await list_lock_refusal(ctx.db_path, "modify") is not None:
            return Verdict.refuse(_NAMES_FIXED)
        if payload["role_id"] is not None:
            refused = await _role_refusal(
                ctx, placement, int(payload["role_id"]), other_than=payload["was_shorthand"],
                suffix=" Nothing was written.",
            )
            if refused is not None:
                return refused
        if names_changed(payload):
            try:
                async with get_connection(ctx.db_path) as db:
                    await teams.judge_modify_on(
                        db, payload["was_shorthand"], shorthand=payload["shorthand"],
                        full_name=payload["full_name"],
                    )
            except ValueError as refusal:
                return Verdict.refuse(f"⛔ {refusal}")
        return Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module. Everything is judged before the first write."""
        _actor(ctx)
        payload = ctx.payload
        was, shorthand = payload["was_shorthand"], payload["shorthand"]
        full_name = payload["full_name"]
        role_id = payload["role_id"]
        try:
            current = await teams.server_team_on(db, was)
            if current is None:
                raise ValueError(_GONE.format(full_name=payload["was_full_name"]))
            if names_changed(payload):
                await teams.judge_modify_on(db, was, shorthand=shorthand, full_name=full_name)
            old_role_id = current["role_id"]
            if role_id is not None:
                try:
                    old_role_id = await placement.set_team_role_config_on(
                        db, was, int(role_id), now=now()
                    )
                except ValueError as refusal:
                    raise ValueError(f"{refusal} Nothing was written.") from refusal
        except ValueError as refusal:
            return _refused_in_save(ctx, refusal)
        audits: list[AuditRecord] = []
        if names_changed(payload):
            await teams.modify_default_team_on(db, was, shorthand=shorthand, full_name=full_name)
            audits.append(
                AuditRecord(
                    "TEAM_NAMES",
                    {"shorthand": current["name"], "full_name": current["full_name"]},
                    {"shorthand": shorthand, "full_name": full_name},
                )
            )
        mapped = None if role_id is None else int(role_id)
        if shorthand != was:
            held = await placement.rename_team_role_config_on(db, was, shorthand, now=now())
            if mapped is None:
                mapped = held
        if role_id is not None or (shorthand != was and mapped is not None):
            audits.append(
                AuditRecord(
                    "TEAM_ROLE_CONFIG",
                    {"team": was, "role_id": old_role_id},
                    {"team": shorthand, "role_id": mapped},
                )
            )
        users = (
            await placement.seated_user_ids_on(db, was)
            if role_id is not None and old_role_id != role_id else []
        )
        return StepResult(
            result={"saved": True},
            audits=tuple(audits),
            then=_moves(users, old_role_id, mapped),
        )

    def details(ctx: StepContext) -> str:
        payload = ctx.payload
        role = (
            "" if payload["role_id"] is None
            else f"  role: {payload['role_name']} (<@&{payload['role_id']}>)\n"
        )
        return (
            f"  team: {payload['full_name']}\n  shorthand: {payload['shorthand']}\n{role}"
            f"  seated drivers moved: {_moved(ctx)}"
        )

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if _saved(ctx) is None:
            return NOTHING_MODIFIED.format(**ctx.payload)
        payload = ctx.payload
        lines = [f'✅ Team "{payload["full_name"]}" updated.']
        if payload["full_name"] != payload["was_full_name"]:
            lines.append(f'  Full name: "{payload["was_full_name"]}" → "{payload["full_name"]}"')
        if payload["shorthand"] != payload["was_shorthand"]:
            lines.append(f"  Shorthand: `{payload['was_shorthand']}` → `{payload['shorthand']}`")
            old_file, new_file = normalise(payload["was_shorthand"]), normalise(payload["shorthand"])
            if old_file != new_file:
                lines.append(
                    f"  Its artwork is now looked for as `{new_file}`, not `{old_file}` — "
                    "rename the file in the team image directory."
                )
        if payload["role_id"] is not None:
            lines.append(f"  Role: <@&{payload['role_id']}>")
            moved = _moved(ctx)
            if moved:
                lines.append(f"  {moved} seated driver(s) moved to the new role.")
        return _with_problems("\n".join(lines), ctx)

    return ChangeType(
        kind=TEAM_MODIFY,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps={
            SAVE: Step(SAVE, StepKind.SAVE, save),
            CLOSE: _close_step("/team modify", details),
            MOVE_ROLES: role_move_step(placement, reason=MODIFY_REASON),
        },
        check=check,
        key=lambda payload: f"{TEAM_MODIFY}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda payload: f"Changing **{payload['was_full_name']}**",
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


def team_reserve_role_change(
    *, placement: PlacementService, now: Callable[[], datetime]
) -> ChangeType:
    """The `core.team_reserve_role` change type, for the builder to register: *placement* is what
    the check judges the role through and the save maps it through, and gives the roles of the
    drivers seated in the Reserve team, *now* the queue's clock.

    The payload names the role the Reserve team is to be given (`role_id`, with `role_name` as the
    line names it), or None where its role is cleared.
    """

    async def check(ctx: CheckContext) -> Verdict:
        """The refusals of the season and of the role, each in its words."""
        refused = await season_done_refusal(ctx.db_path, "reserve-role")
        if refused is not None:
            return refused
        role_id = ctx.payload["role_id"]
        if role_id is None:
            return Verdict.go()
        return await _role_refusal(
            ctx, placement, int(role_id), other_than=RESERVE_NAME
        ) or Verdict.go()

    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """The one save; see the module."""
        _actor(ctx)
        role_id = ctx.payload["role_id"]
        try:
            if role_id is None:
                old_role_id = await placement.delete_team_role_config_on(db, RESERVE_NAME)
            else:
                old_role_id = await placement.set_team_role_config_on(
                    db, RESERVE_NAME, int(role_id), now=now()
                )
        except ValueError as refusal:
            return _refused_in_save(ctx, refusal)
        audits: tuple[AuditRecord, ...] = ()
        if role_id is not None or old_role_id is not None:
            audits = (
                AuditRecord(
                    "TEAM_ROLE_CONFIG",
                    {"team": RESERVE_NAME, "role_id": old_role_id},
                    {"team": RESERVE_NAME, "role_id": role_id},
                ),
            )
        users = (
            await placement.seated_user_ids_on(db, RESERVE_NAME) if old_role_id != role_id else []
        )
        return StepResult(
            result={"saved": True}, audits=audits, then=_moves(users, old_role_id, role_id)
        )

    def details(ctx: StepContext) -> str:
        role_id = ctx.payload["role_id"]
        return "  role: cleared" if role_id is None else (
            f"  role: {ctx.payload['role_name']} (<@&{role_id}>)"
        )

    def outcome(ctx: OutcomeContext) -> str:
        refused = _refused(ctx)
        if refused is not None:
            return refused
        if _saved(ctx) is None:
            return NOTHING_RESERVED
        role_id = ctx.payload["role_id"]
        reply = (
            "✅ Reserve team role cleared." if role_id is None
            else f"✅ Reserve team role set to <@&{role_id}>."
        )
        moved = _moved(ctx)
        if moved:
            reply += f" {moved} seated driver(s) moved to the new role."
        return _with_problems(reply, ctx)

    return ChangeType(
        kind=TEAM_RESERVE_ROLE,
        opening=(PlannedStep(SAVE), PlannedStep(CLOSE)),
        steps={
            SAVE: Step(SAVE, StepKind.SAVE, save),
            CLOSE: _close_step("/team reserve-role", details),
            MOVE_ROLES: role_move_step(placement, reason=RESERVE_REASON),
        },
        check=check,
        key=lambda payload: f"{TEAM_RESERVE_ROLE}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda payload: (
            "Clearing the Reserve team's role" if payload["role_id"] is None
            else "Setting the Reserve team's role"
        ),
        outcome=outcome,
        lane=lambda _payload: LEAGUE_LANE,
    )


__all__ = [
    "MODIFY_REASON", "NOTHING_ADDED", "NOTHING_MODIFIED", "NOTHING_REMOVED", "NOTHING_RESERVED",
    "RESERVE_REASON", "TEAM_ADD", "TEAM_MODIFY", "TEAM_REMOVE", "TEAM_RESERVE_ROLE",
    "gone_reply", "list_lock_refusal", "list_lock_reply", "season_done_refusal",
    "season_done_reply", "team_add_change",
    "team_modify_change", "team_remove_change", "team_reserve_role_change",
]
