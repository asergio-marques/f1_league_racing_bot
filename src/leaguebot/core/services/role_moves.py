"""The step that moves a member's roles after their driver's change, ended by a fault, not stopping
the lane.

A driver's seat is the league's record, and the roles that follow it are Discord's copy: a role
Discord refuses to give or take leaves nothing half-done and nothing waiting on it, the member
being able to be given or relieved of it by hand. architecture.md, "A job that fails stops its
lane", makes such a step end the job on its fault (`Step.on_fault`), so the change that planned it
goes on to its lineups and its line and reports what was not done, rather than the lane stopping
for a Retry or a Discard that could change nothing a league sees.

`role_move_step` takes a member's roles first and gives theirs after. A step planned for each member
carries `{"user_id", "revoke", "grant"}`, the roles' ids as the change read them inside its save. A
member Discord cannot find has left the server, and their roles went with them: the step completes,
nothing taken or given. A refusal ends the job with `{"problem": ...}`, in plain words and naming
the roles and the kind of fault, never Discord's own message, for the change's reply and line to
carry (`not_done`). Where taking is refused, nothing is given, and the problem names both. A role no
longer on the server is, for a change that asks it (`give_what_remains`), passed over and named, the
roles still there given.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from leaguebot.core.models.change import GuildUnavailable, StepFailedOnDiscord, StepKind, StepResult
from leaguebot.core.services.change_queue import Step, StepContext
from leaguebot.core.utils.interaction_errors import describe_fault
from leaguebot.core.utils.league_server import league_guild

if TYPE_CHECKING:
    from leaguebot.core.services.placement_service import PlacementService

#: The name of the step, as the change types and their outcomes know it.
MOVE_ROLES = "move_roles"

#: The reason Discord's audit log is given for a role move, unless its change names its own.
PLACEMENT_REASON = "Driver placement"


class _RoleMoveFault(Exception):
    """A role move that failed, after *taking* had or had not been done: raised from the fault, so
    that `on_fault` knows what was left."""

    def __init__(self, taken: bool) -> None:
        super().__init__("a role move failed")
        self.taken = taken


def _mentions(role_ids: Sequence[int]) -> str:
    """The roles as a sentence lists them: "<@&1>", "<@&1> and <@&2>", "<@&1>, <@&2> and <@&3>"."""
    mentions = [f"<@&{each}>" for each in role_ids]
    if len(mentions) == 1:
        return mentions[0]
    return f"{', '.join(mentions[:-1])} and {mentions[-1]}"


def _named(role_ids: Sequence[int]) -> str:
    return f"{'the role' if len(role_ids) == 1 else 'the roles'} {_mentions(role_ids)}"


def _kind(error: BaseException) -> str:
    """The kind of fault *error* is in plain words: the Discord failure that caused it, where it
    names one, else the step's own reason."""
    if isinstance(error, StepFailedOnDiscord):
        cause = error.__cause__
        return error.reason if cause is None else describe_fault(cause)
    return describe_fault(error)


def problem_of(
    user_id: int, revoke: Sequence[int], grant: Sequence[int], *, taken: bool, kind: str
) -> str:
    """What a refused role move reports: the roles *revoke* not taken from, or *grant* not given
    to, the member *user_id*, and the *kind* of fault. Where taking was *taken*, only the grant is
    named."""
    member = f"<@{user_id}>"
    if taken or not revoke:
        return f"{_named(grant)} could not be given to {member}: {kind}"
    if not grant:
        return f"{_named(revoke)} could not be taken from {member}: {kind}"
    return (
        f"{_named(revoke)} could not be taken from {member}, nor "
        f"{_mentions(grant)} given: {kind}"
    )


def role_move_step(
    placement: "PlacementService",
    name: str = MOVE_ROLES,
    *,
    reason: str = PLACEMENT_REASON,
    give_what_remains: bool = False,
) -> Step:
    """The job that takes a member's roles and then gives theirs; see the module. *reason* is what
    Discord's audit log is given for taking them and for giving them.

    A role no longer on the server stops the giving of all of them, unless *give_what_remains*: the
    roles still on the server are given, and the job ends with a problem naming those that are not.

    It raises where Discord will not (`PlacementService.revoke_roles`, `grant_roles`), and
    `on_fault` ends the job on it with the problem named, so the queue goes on.
    """

    async def run(ctx: StepContext) -> StepResult:
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        each = ctx.step_payload
        user_id = int(each["user_id"])
        revoke = [int(role) for role in each.get("revoke", ())]
        grant = [int(role) for role in each.get("grant", ())]
        if revoke:
            try:
                found = await placement.revoke_roles(guild, user_id, *revoke, reason=reason)
            except Exception as error:
                raise _RoleMoveFault(taken=False) from error
            if not found:
                return StepResult(result={"gone": True})
        missing: list[int] = []
        if grant and give_what_remains:
            missing = [role for role in grant if guild.get_role(role) is None]
            grant = [role for role in grant if role not in missing]
        if grant:
            try:
                found = await placement.grant_roles(guild, user_id, *grant, reason=reason)
            except Exception as error:
                raise _RoleMoveFault(taken=True) from error
            if not found:
                return StepResult(result={"gone": True})
        if missing:
            gone = "it is" if len(missing) == 1 else "they are"
            problem = problem_of(
                user_id, (), missing, taken=True, kind=f"{gone} no longer on the server"
            )
            return StepResult(result={"problem": problem})
        return StepResult(result={})

    def on_fault(ctx: StepContext, error: BaseException) -> StepResult:
        each = ctx.step_payload
        fault = error if isinstance(error, _RoleMoveFault) else None
        cause = (error.__cause__ or error) if fault is not None else error
        problem = problem_of(
            int(each["user_id"]),
            [int(role) for role in each.get("revoke", ())],
            [int(role) for role in each.get("grant", ())],
            taken=fault is not None and fault.taken,
            kind=_kind(cause),
        )
        return StepResult(result={"problem": problem})

    async def describe(ctx: StepContext) -> str:
        return f"moving the roles of <@{ctx.step_payload['user_id']}>"

    return Step(name, StepKind.ACT, run, describe=describe, on_fault=on_fault)


__all__ = ["MOVE_ROLES", "PLACEMENT_REASON", "problem_of", "role_move_step"]

