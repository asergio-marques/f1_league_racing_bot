"""A division's lineup, asked of the change queue.

A change made outside the queue that moves or sacks a driver (attendance's sanctions) asks the
queue, as the bot's and repeatably, to post each lineup it touched (`lineup.post`): its reply says
what it did, and the post follows. A change on the queue plans the post as a job of its own
(`lineup_step`), as `/driver move`, `release` and `sack` and the season's approval and the
mid-season confirmation of placements do; `/driver assign` and `unassign` plan none, the
placement they write or remove never being confirmed. A lineup Discord refuses stops the queue at
the `post_lineup` job, to be tried again as text (Constitution XIV, rule 8) or discarded, instead
of being lost. A division with no lineup channel set stops it too, naming the command that sets
one.

The post and the deletion of the lineup it replaces are two jobs (`standing_posts`): the post job
saves the new id and the audit with its mark, and plans the deletion after it. The same job
(`lineup_step`) posts the lineups of a season's approval; a review's `refresh_lineup` reaches the
same post through the attendance hook.

**`LineupPoster` is the posting itself** (`refresh_lineup`): the picture where the image module's
drawing, handed by the builder, draws it, else the embed. The placement service keeps the seats
and posts nothing; a change that moves a seat names the divisions whose lineup is
owed, and the post job calls the poster.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.queue.change_queue import (
    ChangeQueue,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.audit_service import record_change_on
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.image_drawing import ImageDrawing
from leaguebot.core.services.posting.standing_post_handler import post_standing
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)

POST_LINEUP = "post_lineup"
LINEUP_POST = "lineup.post"

_LIVE = ("SETUP", "ACTIVE")


@dataclass
class LineupPosted:
    """A lineup posted: the message sent and the one it replaces, for the jobs after the post.

    *previous* is the division's recorded lineup message as it stood when the post was made, and
    *form* is "image" for a picture and "text" for the embed. *problem* is the image module's
    words for why a picture could not be drawn, carried for the job's own line where the lineup
    fell back to text, or None where nothing went wrong. Nothing is saved: the post job's
    `record` saves *message_id* (`lineup_posting.record_lineup_on`) and the job after it deletes
    *previous*.
    """

    message_id: int
    channel_id: int
    previous: int | None
    division_name: str
    form: str
    problem: str | None = None


class LineupPoster:
    """Posts a division's lineup: the picture where the image module draws it, else the embed.

    The seats are the placement service's. It posts through the guild it is
    given and reads the division, its channel and its committed seats from the database; it draws
    nothing itself, asking the image module's drawing the builder hands it (core's drawing
    protocol, `image_drawing.ImageDrawing`), and is handed no bot. Built by the builder over the
    bot's database and that drawing, and handed to every change that posts a lineup and to
    attendance's hook.
    """

    def __init__(self, db_path: str, *, drawing: ImageDrawing | None = None) -> None:
        self._db_path = db_path
        #: The image module's answers to core's drawing protocol, handed in by the builder. None
        #: in every unit test that posts a lineup alone: the lineup goes out as the embed.
        self._drawing = drawing

    async def refresh_lineup(
        self, guild: discord.Guild, division_id: int, *, as_text: bool = False
    ) -> LineupPosted | None:
        """Post the division's lineup afresh, **raising** where it could not be posted.

        What the post job of a change on the change queue calls: a lineup channel the division was
        given and the guild no longer holds, or a send Discord refuses, raises
        `StepFailedOnDiscord`, for the queue to stop on and try again; so does a division never
        given a lineup channel, the message naming `/division lineup-channel`. A division no longer
        there posts nothing and gives None. The post is all it does: the job's `record` saves the
        new id and a job after it deletes the message replaced.

        *as_text* skips the picture and posts the textual lineup: a job tried again goes as text
        (Constitution XIV, rule 8).
        """
        return await self._refresh_lineup_post(
            guild, division_id, as_text=as_text, require_channel=True
        )

    @posts_in_a_job
    async def _refresh_lineup_post(
        self,
        guild: discord.Guild,
        division_id: int,
        *,
        as_text: bool = False,
        require_channel: bool = False,
    ) -> LineupPosted | None:
        """Post the division's lineup: as a graphic where the drawing draws it, else as the
        embed.

        *as_text* skips asking the drawing altogether, whatever it would answer (Constitution
        XIV, rule 8: a job tried again goes as text). *require_channel* raises for a division
        never given a lineup channel, as the post job of a change must; without it such a
        division posts nothing, since configuring one is optional.

        **The picture is asked of the drawing in front of an untouched body.** Where the drawing
        the builder hands this service (core's drawing protocol) answers that the league
        draws its lineup, it draws the division's picture and this method posts it through the
        standing-post handler with no mentions, then releases the file. A picture that cannot be
        drawn — the drawing raises, or hands back a problem — falls through to the text embed
        below, carrying what went wrong for the job's own line. Where the league does not draw
        its lineup at all, the text embed is the lineup.

        Either way the lineup is posted before the message it replaces is deleted, by a job after
        it (Constitution XIV rule 8; image spec :706): this method deletes nothing and saves
        nothing, and a refusal raises `StepFailedOnDiscord` for the queue to stop on. A picture
        Discord refuses stands aside, so the text is posted in the same try; only a refused text
        post stops the lane.
        """
        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                "SELECT d.name AS div_name, d.lineup_channel_id, d.lineup_message_id "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
            div_row = await cur.fetchone()

        if div_row is None:
            return None
        if div_row["lineup_channel_id"] is None:
            if not require_channel:
                return None
            raise StepFailedOnDiscord(
                f"{div_row['div_name'] or division_id} has no lineup channel set: set one with "
                "/division lineup-channel, then press Retry"
            )

        lineup_channel_id: int = div_row["lineup_channel_id"]
        lineup_message_id: int | None = div_row["lineup_message_id"]
        div_name: str = div_row["div_name"] or str(division_id)

        # Resolve the channel. Needed for both forms, read once.
        channel = as_text_channel(guild.get_channel(lineup_channel_id))
        if channel is None:
            try:
                channel = as_text_channel(await guild.fetch_channel(lineup_channel_id))
            except (discord.NotFound, discord.HTTPException) as exc:
                log.error(
                    "_refresh_lineup_post: lineup channel %s not found for division %s",
                    lineup_channel_id, division_id,
                )
                raise StepFailedOnDiscord(
                    f"the lineup channel (id {lineup_channel_id}) is not in the server"
                ) from exc
        if not isinstance(channel, discord.TextChannel):
            raise StepFailedOnDiscord(
                f"the lineup channel (id {lineup_channel_id}) is not a text channel"
            )

        image_path: Path | None = None
        problem: str | None = None
        drawing = self._drawing
        # Asked before the drawing's catch-all: the gate stands aside from its own faults, and a
        # switch nobody signed up for is a fault in the bot's assembly, not a picture not drawn.
        if not as_text and drawing is not None and await drawing.lineup_wanted():
            try:
                picture = await drawing.draw_lineup(guild, division_id)
                if picture.problem is not None:
                    problem = picture.problem
                elif picture.path is not None:
                    image_path = picture.path
            except Exception as exc:  # noqa: BLE001 — never block a placement on this
                log.error("_refresh_lineup_post: image path failed: %s", exc, exc_info=True)
                problem = str(exc)

        # The drawn path is released whichever way this ends — posted, refused and fallen
        # back to text, or lost to a Discord fault that stops the queue at the posting — so it
        # is kept apart from *send_as_image*, which only says whether the picture is still the
        # one to try sending.
        drawn_path = image_path
        send_as_image = image_path is not None

        try:
            if send_as_image:
                assert image_path is not None  # true by construction: send_as_image says so
                attachment = discord.File(str(image_path), filename=image_path.name)
                try:
                    new_msg_id = await post_standing(
                        channel, mentions=discord.AllowedMentions.none(), file=attachment
                    )
                except discord.HTTPException:
                    # Discord refused the picture: fall to the text in the same try.
                    send_as_image = False
                else:
                    return LineupPosted(
                        new_msg_id, lineup_channel_id, lineup_message_id, div_name, "image",
                    )
                finally:
                    attachment.close()

            embed = self._lineup_embed(div_name, await self._lineup_rows(division_id))
            try:
                new_msg_id = await post_standing(
                    channel, mentions=discord.AllowedMentions.none(), embed=embed
                )
            except discord.HTTPException as exc:
                log.error("_refresh_lineup_post: failed to post embed: %s", exc)
                raise StepFailedOnDiscord(f"the lineup could not be posted: {exc}") from exc

            return LineupPosted(
                new_msg_id, lineup_channel_id, lineup_message_id, div_name, "text",
                problem=problem,
            )
        finally:
            if drawing is not None:
                drawing.discard_picture(drawn_path)

    async def _lineup_rows(self, division_id: int) -> list[aiosqlite.Row]:
        """The division's committed, assigned seats, for the textual lineup."""
        async with get_connection(self._db_path) as db:
            cur = await db.execute(
                """
                SELECT ti.full_name AS team_name, ti.is_reserve,
                       dp.discord_user_id,
                       dp.is_test_driver, dp.test_display_name
                FROM driver_season_assignments dsa
                JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                JOIN team_seats ts ON ts.id = dsa.team_seat_id
                JOIN team_instances ti ON ti.id = ts.team_instance_id
                JOIN seasons s ON s.id = dsa.season_id
                WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                  -- A placement not yet confirmed mid-season is not posted.
                  AND (dsa.committed = 1 OR s.status != 'ACTIVE')
                ORDER BY ti.is_reserve ASC, ti.full_name ASC
                """,
                (division_id,),
            )
            return list(await cur.fetchall())

    @staticmethod
    def _lineup_embed(div_name: str, assign_rows: Any) -> discord.Embed:
        """The textual lineup embed: regular teams, then reserves after a separator."""
        regular: dict[str, list[str]] = {}
        reserve: dict[str, list[str]] = {}
        for row in assign_rows:
            uid = int(row["discord_user_id"])
            mention = f"<@{uid}>"
            if row["is_test_driver"] and row["test_display_name"]:
                mention = f"<@{uid}> ({row['test_display_name']})"
            target = reserve if row["is_reserve"] else regular
            target.setdefault(row["team_name"], []).append(mention)

        parts: list[str] = [
            f"**{t}**: {', '.join(labels)}" for t, labels in regular.items()
        ]
        if reserve:
            if parts:
                parts.append("---")
            parts.extend(
                f"**{t}**: {', '.join(labels)}" for t, labels in reserve.items()
            )

        description = "\n".join(parts) if parts else "*(no drivers assigned)*"
        return discord.Embed(
            title=f"\U0001f4cb {div_name} Lineup",
            description=description,
            color=discord.Color.blurple(),
        )


def posted_result(posted: LineupPosted | None) -> dict[str, Any]:
    """What a lineup post job returns: the new message and the one it replaces, or an empty
    result where nothing was posted (the division is gone, or its picture is not posted by this
    job)."""
    if posted is None:
        return {}
    return {
        "new": posted.message_id, "old": posted.previous, "channel_id": posted.channel_id,
        "division_name": posted.division_name, "form": posted.form, "problem": posted.problem,
    }


#: What puts a lineup fallen back to text right: correct the template, and the picture is
#: posted with the next change to the division's drivers, there being no command that posts a
#: lineup again on its own.
_FIX_IT = (
    "fix the lineup template; the picture is posted with the next change to the division's "
    "drivers"
)


def fallen_back_to_text_line(ctx: StepContext, result: Mapping[str, Any]) -> tuple[str, ...]:
    """The lineup posting job's own line where it fell back to text, as the calendar's does:
    the member and the change it ran for, the division, the problem, and what puts it right.
    Empty where nothing went wrong."""
    problem = result.get("problem")
    if problem is None:
        return ()
    return (
        f"{ctx.named} | {ctx.what} | Lineup posted as text\n"
        f"  division: {result.get('division_name')}\n"
        f"  problem: {problem}\n"
        f"  to put it right: {_FIX_IT}",
    )


def _then(result: Mapping[str, Any] | None) -> tuple[PlannedStep, ...]:
    """The `delete_replaced` job for the lineup a post replaced, in `result`'s own words."""
    if not result or result.get("new") is None:
        return ()
    return standing_posts.replaced(
        result.get("channel_id"), result.get("old"), result.get("new"),
        what="lineup", division_name=str(result.get("division_name") or "the division"),
    )


async def record_lineup_on(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult, *, now: datetime
) -> None:
    """Save the new lineup message on the division, and the audit of the posting, in the save
    that marks the post job done. Nothing is committed here.

    The audit is the system's (actor 0); a picture says so. It is
    stamped *now*.
    """
    new = result.result.get("new")
    if new is None:
        return
    division_id = int(ctx.step_payload.get("division_id") or ctx.payload["division_id"])
    await db.execute(
        "UPDATE divisions SET lineup_message_id = ? WHERE id = ?", (int(new), division_id)
    )
    value: dict[str, Any] = {
        "channel_id": result.result.get("channel_id"),
        "division": result.result.get("division_name"),
    }
    if result.result.get("form") == "image":
        value["form"] = "image"
    await record_change_on(
        db, actor_id=0, actor_name="system", change_type="SIGNUP_LINEUP_POSTED",
        old_value={}, new_value=value, now=now, division_id=division_id,
    )


def lineup_step(
    lineups: LineupPoster, *, now: Callable[[], datetime], name: str = POST_LINEUP
) -> Step:
    """The job that posts a division's lineup, the job's own `division_id` or else the change's.

    The first try posts as configured (the picture where the image module has one); a try
    again posts the text. A division with no lineup channel raises, naming the command that sets it. A try after a save
    that failed removes the copy it sent first. The job returns the message posted and the one it
    replaces, saves the new one with its mark (`record_lineup_on`) and plans the deletion of the
    old one (`standing_posts`).
    """

    def value_of(ctx: StepContext, key: str, default: Any = None) -> Any:
        """The job's own value, else the change's."""
        return ctx.step_payload.get(key, ctx.payload.get(key, default))

    async def run(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        posted = await lineups.refresh_lineup(
            guild, int(value_of(ctx, "division_id")), as_text=ctx.tries > 0
        )
        result = posted_result(posted)
        return StepResult(
            result=result, lines=fallen_back_to_text_line(ctx, result), then=_then(result),
        )

    async def describe(ctx: StepContext) -> str:
        return f"posting the lineup of **{value_of(ctx, 'division_name', 'the division')}**"

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        await record_lineup_on(db, ctx, result, now=now())

    return Step(name, StepKind.ACT, run, describe=describe, record=record)


def lineup_post_change(lineups: LineupPoster, *, now: Callable[[], datetime]) -> ChangeType:
    """`lineup.post`: the bot's change, repeatable, that posts one division's lineup, its audit
    stamped by *now*, the queue's clock.

    The check drops the request where the division is gone or its season is no longer live, and
    refuses where it has no lineup channel set: the bot's change so refused stops the queue with
    the reason, to be put right and retried.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT d.name, d.lineup_channel_id, s.status FROM divisions d "
                "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (int(ctx.payload["division_id"]),),
            )
            row = await cursor.fetchone()
        if row is None or row["status"] not in _LIVE:
            return Verdict.not_due("the division is no longer in a live season")
        if row["lineup_channel_id"] is None:
            return Verdict.refuse(
                f"**{row['name']}** has no lineup channel set: set one with "
                f"`/division lineup-channel`, then press Retry."
            )
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=LINEUP_POST,
        opening=(PlannedStep(POST_LINEUP),),
        steps={
            POST_LINEUP: lineup_step(lineups, now=now),
            standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
        },
        check=check,
        key=lambda payload: f"{LINEUP_POST}:{payload['division_id']}",
        doing=lambda payload: f"posting the lineup of **{payload.get('division_name', 'the division')}**",
        outcome=outcome,
        repeatable=True,
    )


async def ask_lineup_posts(
    queue: ChangeQueue, db_path: str, division_ids: Sequence[int]
) -> None:
    """Ask the queue to post the lineup of each division in *division_ids*, in the order given and
    each once.

    A change made outside the queue that wants a standing post after it asks the queue for that
    post as a change of the bot's, of a kind that may be repeated; its reply is unchanged and the
    post follows. The names are read here, on a connection of its own.
    """
    wanted = list(dict.fromkeys(int(each) for each in division_ids))
    if not wanted:
        return
    async with get_connection(db_path) as db:
        names: dict[int, str] = {}
        for division_id in wanted:
            cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
            row = await cursor.fetchone()
            names[division_id] = row["name"] if row is not None else str(division_id)
    for division_id in wanted:
        payload: dict[str, Any] = {"division_id": division_id, "division_name": names[division_id]}
        await queue.ask(
            LINEUP_POST,
            payload,
            what=f"posting the lineup of **{names[division_id]}**",
            origin=ChangeOrigin.BOT,
        )
