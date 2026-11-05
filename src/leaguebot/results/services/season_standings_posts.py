"""Results' opening standings, posted when a season is approved (#283).

The opening classification brackets a season's start: the standings a league already reads
after every round, under the opening's heading and with no message text (see
:class:`leaguebot.core.models.classification_occasion.ClassificationOccasion`). Results posts
its own, one division at a time, each a job of the season's approval on the change queue, signed
up to the hook core declares for placements confirmed
(:func:`placements_confirmed_on`, architecture.md, "How modules and core fit together"). The
final standings move here too, in a later slice; until then ``season_classification_service``
still calls :func:`remove_earlier_try`, :func:`keeping` and :func:`produce_stranding` for its
own final posting, and that import is listed under ``.importlinter`` with #283.

**Each posting reads the division's first round as it runs** (never the table itself: through
core's `division_first_round`, architecture.md, "A module reads core's tables only through
core's services"), **and raises where it cannot post**, for the queue to stop on — except that it
returns without posting where no standings channel is set, or where no driver is ranked, neither
of which is a fault the queue should stop on. The standings channel is read from
`division_results_config`, where `/results channel standings` keeps it. No message id is
recorded (results specification: the opening classification is not kept), so a table already
sent when a later one fails is named on the failure (`result`), and the job keeps it: the next
try is handed it as *kept* and removes that copy before it posts both again.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from leaguebot.core.models.change import PlannedStep, StepKind, StepResult
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services.change_queue import Step, StepContext
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.season_lifecycle_service import require_guild
from leaguebot.core.services.season_service import division_first_round
from leaguebot.core.utils.league_bot import LeagueBot

OPENING_STANDINGS = "opening_standings"


async def _opening_round(db_path: str, division_id: int):
    """The division's first round, read through core's `division_first_round`, or None where it
    has none.

    The sheets are drawn against it: not because they stand after it, they stand after nothing,
    but because the grid of rounds down the side of both is read from the season's calendar and
    the heading context every posting resolves is keyed by a round. Every cell in that grid is
    empty, which is exactly what an opening sheet should show. Approval refuses a division with no
    rounds, so None cannot normally happen.
    """
    return await division_first_round(db_path, division_id)


async def remove_earlier_try(guild, kept: dict[str, Any] | None) -> dict[str, Any] | None:
    """Take down the standings a try before this one left posted in part, or None where it left none.

    No id of an opening or final standings is recorded, so a table already sent when a later one
    failed is put on the failure for the job to keep (`kept`), and the next try removes it here,
    from the channel it was posted in, which the standings channel may since have left: a channel
    the guild no longer holds holds none of them. A message that will not delete raises, with
    what is still to remove. Once the copies are down, what is returned names none left, so that
    a failure of this try names what it left, even nothing, and the job no longer keeps the
    copies removed.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services.results_post_service import remove_part_posted_messages

    earlier = [int(each) for each in (kept or {}).get("new") or []]
    if not earlier:
        return None
    posted_in = int((kept or {}).get("channel_id") or 0)
    earlier_channel = guild.get_channel(posted_in) if posted_in else None
    if earlier_channel is not None:
        left, failures = await remove_part_posted_messages(earlier_channel, earlier)
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) of the earlier try could not be removed",
                result={"new": left, "channel_id": posted_in},
            ) from (failures[0] if failures else None)
    return {"new": [], "channel_id": posted_in}


@asynccontextmanager
async def keeping(removed: dict[str, Any] | None, what: str) -> AsyncIterator[None]:
    """Carry what the earlier try's removal left on any failure of this try.

    Where the earlier try's copies are down (*removed* is not None), a failure of this one that
    names no result is given *removed*, and a failure that is not a Discord one becomes one that
    does, so that the job keeps what is true: nothing left standing.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord

    try:
        yield
    except StepFailedOnDiscord as failure:
        if removed is not None and failure.result is None:
            failure.result = removed
        raise
    except Exception as failure:
        if removed is None:
            raise
        raise StepFailedOnDiscord(
            f"the {what} standings could not be posted once the earlier try's were removed",
            result=removed,
        ) from failure


async def produce_stranding(what: str, channel_id, posting) -> None:
    """Await *posting*, results' ``produce_standings``, naming on its failure a table already sent.

    No id of the classification is recorded, so a table already sent when a later one failed is
    put on the failure for the job to keep, and the next try removes it.
    """
    from leaguebot.core.models.change import StepFailedOnDiscord

    try:
        await posting
    except Exception as failure:
        stranded = [int(each) for each in getattr(failure, "left_standing", None) or []]
        if stranded:
            raise StepFailedOnDiscord(
                f"the {what} standings were posted in part and the rest was refused",
                result={"new": stranded, "channel_id": int(channel_id)},
            ) from failure
        raise


async def post_opening_standings(
    bot: LeagueBot,
    guild,
    db_path: str,
    division_id: int,
    *,
    as_text: bool = False,
    kept: dict[str, Any] | None = None,
) -> None:
    """Post one division's opening standings, both championships, raising where it cannot.

    **The standings channel is read here, from `division_results_config`,** where results' own
    settings keep it, as the posting runs. **It returns without posting where no standings
    channel is set, or where no driver is ranked** (a division never given a channel, or one with
    nobody seated): a channel the guild no longer holds raises `StepFailedOnDiscord`, for the
    change queue to stop on. No message id is recorded (results specification: the opening
    classification is not kept), so a table already sent when a later one fails is named on the
    failure (`result`), and the job keeps it: the next try is handed it as *kept* and removes
    that copy, from the channel it was posted in, before it posts both again, failing again, with
    what it still has to remove, where a message will not delete. Once that copy is down, any
    failure of the try names what the try left, even nothing, so that the job no longer keeps the
    copy removed. *as_text* leaves the graphic out, which is how a job retries (Constitution XIV
    rule 8).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.image.services.image_results_post import _driver_names
    from leaguebot.results.services import standings_service
    from leaguebot.results.services.results_post_service import (
        produce_standings,
        reserves_in_standings,
    )

    opener = await _opening_round(db_path, division_id)
    if opener is None:
        return
    removed = await remove_earlier_try(guild, kept)
    async with keeping(removed, "opening"):
        async with get_connection(db_path) as db:
            config = await (
                await db.execute(
                    "SELECT standings_channel_id FROM division_results_config "
                    "WHERE division_id = ?",
                    (division_id,),
                )
            ).fetchone()
        channel_id = config["standings_channel_id"] if config is not None else None
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the standings channel (id {channel_id}) is not in the server"
            )

        # Twice, deliberately. The order is taken on the name the sheet will actually draw, and the
        # names are resolved from Discord by user id, so the roster has to be known before it can be
        # ordered. The first pass is read only for who is in it; the second is the one that counts.
        roster = await standings_service.opening_driver_standings(db_path, division_id)
        names = await _driver_names(
            bot, guild, [snapshot.driver_user_id for snapshot in roster], division_id=division_id
        )
        driver_snaps = await standings_service.opening_driver_standings(db_path, division_id, names)
        team_snaps = await standings_service.opening_team_standings(db_path, division_id)
        if not driver_snaps:
            return
        await produce_stranding(
            "opening",
            channel_id,
            produce_standings(
                db_path,
                division_id,
                opener.id,
                opener.round_number,
                opener.track_name or "",
                channel,
                driver_snaps,
                team_snaps,
                guild,
                await reserves_in_standings(db_path, division_id),
                "",
                bot=None if as_text else bot,
                occasion=ClassificationOccasion.SEASON_OPENING,
            ),
        )


async def placements_confirmed_on(
    db: Any, division_id: int, division_name: str
) -> tuple[PlannedStep, ...]:
    """Results' own job of placements confirmed (the season's approval): its opening standings
    for *division_id*, planned whatever results' switch says — :func:`opening_steps`'
    `still_due` drops it as it runs where results is off."""
    return (PlannedStep(OPENING_STANDINGS, {"division_id": division_id, "division_name": division_name}),)


def opening_steps(modules: ModuleService) -> dict[str, Step]:
    """Results' step definition for the opening standings, signed up to
    *placements_confirmed_steps* beside :func:`placements_confirmed_on`. The builder hands in
    *modules*, as it hands cancellation's steps theirs, so that `still_due` reads results'
    switch without looking the service up on the bot (architecture.md, "How modules and core
    fit together")."""

    async def run(ctx: StepContext) -> StepResult:
        guild = await require_guild(ctx.bot)
        await post_opening_standings(
            ctx.bot, guild, ctx.db_path, int(ctx.step_payload["division_id"]),
            as_text=ctx.tries > 0, kept=ctx.kept,
        )
        return StepResult()

    async def still_due(_ctx: StepContext) -> bool:
        return await modules.is_results_enabled()

    async def describe(ctx: StepContext) -> str:
        return f"posting the opening standings of **{ctx.step_payload['division_name']}**"

    def not_done(view: Any) -> str:
        """The lines a discarded opening standings job leaves for doing by hand, joined by a
        plain newline: `kept_line`'s caller (`_left`, architecture.md, "How modules and core fit
        together") splits them apart again, so that a table left standing in part gets its own
        "not done:" line in the approval's log and its own bullet in the reply, as it did before
        this job became results' own (#283)."""
        line = (
            f"**{view.payload['division_name']}** — its opening standings could not be "
            f"posted. No command posts them again; the first round's results post the "
            f"standings as usual."
        )
        stranded = (view.result or {}).get("new")
        if stranded:
            ids = ", ".join(str(each_id) for each_id in stranded)
            line += (
                f"\n**{view.payload['division_name']}** — a part of its opening standings "
                f"was posted before the job stopped and stands in "
                f"<#{(view.result or {}).get('channel_id')}> (message {ids}): delete it by hand."
            )
        return line

    return {
        OPENING_STANDINGS: Step(
            OPENING_STANDINGS, StepKind.ACT, run, still_due=still_due, describe=describe,
            not_done=not_done,
        ),
    }
