"""`core.season_setup`: `/season setup`, on the change queue.

One SAVE step inserts the season being set up, in Configuration, and writes its
`SEASON_SETUP_STARTED` audit entry and the log channel's line in the same save, stamped with the
queue's clock (`SeasonService.begin_setup_on`, on the save's own connection, so that the season's
number is read where it is written). The season starts on the UTC date of the queue's "now": every
time inside the bot is in UTC (`docs/design/architecture.md`).

**A fault in the save stops the lane**, as a failed job does: unlike `/bot init`
(`bot_init_change.py`), a league admin exists by the time a season is set up, to discard the job
and clear the stop, so no `Step.on_fault` ends it on a fault.

**Both refusals are the check's**, judged at the ask and again as the change starts, in the
league's log channel either time: a setup already in progress, and a season being raced. The
second of two setups asked together is thus refused as it starts, in the words it would have met at
the ask, and the database's rule of one live season is never reached by an insert.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime
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
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService

KIND = "core.season_setup"

#: The name of the step that inserts the season, writes its audit entry and its line.
SAVE = "save"

#: The command as the line names it.
COMMAND = "/season setup"

#: `/season setup` refused while a season is being set up, word for word.
IN_PROGRESS = (
    "❌ A season setup is already in progress for this server. "
    "Use `/season placements-review` to approve, or `/season abort` to cancel it first."
)

#: `/season setup` refused while a season is being raced, word for word.
RACED = (
    "❌ A season is currently active for this server. "
    "Complete it before starting a new one."
)

#: The reply once a league admin has discarded the stopped save: nothing was begun.
DISCARDED = "Nothing was set up: no season was begun. Run `/season setup` again."


def _check(seasons: "SeasonService") -> Callable[[CheckContext], Awaitable[Verdict]]:
    async def check(_ctx: CheckContext) -> Verdict:
        """A season being set up, then a season being raced, refuses; both are read from the
        database, so the check at the ask and the check as the change starts agree on what they
        see."""
        if await seasons.get_setup_season() is not None:
            return Verdict.refuse(IN_PROGRESS)
        if await seasons.get_confirmed_season() is not None:
            return Verdict.refuse(RACED)
        return Verdict.go()

    return check


def _save(
    seasons: "SeasonService", now: Callable[[], datetime]
) -> Callable[[aiosqlite.Connection, StepContext], Awaitable[StepResult]]:
    async def save(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        """Insert the season being set up, its audit entry and its line, one save."""
        season = await seasons.begin_setup_on(
            db, start_date=now().date(), game_edition=int(ctx.payload["game_edition"]),
        )
        stage = season.stage
        assert stage is not None  # `begin_setup_on` begins every season in a stage
        new = {
            "season_id": season.id,
            "season_number": season.season_number,
            "game_edition": season.game_edition,
            "start_date": season.start_date.isoformat(),
            "status": season.status.value,
            "stage": stage.value,
        }
        line = (
            f"{ctx.named} | {COMMAND} | Success\n"
            f"  season: Season #{season.season_number} (F1 {season.game_edition})"
        )
        return StepResult(
            result={"outcome": "started", **new},
            audits=(AuditRecord("SEASON_SETUP_STARTED", dict.fromkeys(new), new),),
            lines=(line,),
        )

    return save


def _doing(_payload: dict[str, Any]) -> str:
    return "Starting the season setup"


def _outcome(ctx: OutcomeContext) -> str:
    saved = next(
        (view.result for view in ctx.steps if view.name == SAVE and view.result is not None), {}
    )
    if saved.get("outcome") != "started":
        return DISCARDED
    return (
        f"✅ Season setup started. **Season #{saved['season_number']} "
        f"(F1 {saved['game_edition']})** is being configured.\n\n"
        "Use `/division add` for each division, then `/round add` for each round.\n"
        "When done, run `/season placements-review` to review and approve."
    )


def season_setup_change(
    *, seasons: "SeasonService", now: Callable[[], datetime]
) -> ChangeType:
    """The `core.season_setup` change type, for the builder to register: *seasons* is what the
    check and the save read and write the season through, *now* the queue's clock, which dates the
    season."""
    return ChangeType(
        kind=KIND,
        opening=(PlannedStep(SAVE),),
        steps={SAVE: Step(SAVE, StepKind.SAVE, _save(seasons, now))},
        check=_check(seasons),
        key=lambda payload: f"{KIND}|{json.dumps(payload, sort_keys=True)}",
        doing=_doing,
        outcome=_outcome,
        lane=lambda _payload: LEAGUE_LANE,
        writes=("seasons",),
    )


__all__ = ["DISCARDED", "IN_PROGRESS", "KIND", "RACED", "season_setup_change"]
