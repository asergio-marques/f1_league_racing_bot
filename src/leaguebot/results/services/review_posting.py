"""The posting jobs a round's review republishes its tables through (#439, slice 2).

Every change type of a round's review (opening it, approving its reports and its appeals, an
amendment's stages) plans its posts from this module, so that all of them post, delete and name
a failure the same way. `plan_posts` reads, on the save it is handed, the jobs a republication of
a round needs; `posting_steps()` gives the jobs themselves, keyed by their names, for a change
type's `steps`; `not_done(ctx)` gives, from the change's jobs, one line for each posting job a
league admin discarded, for the change's outcome and its `Incomplete` line.

**Post the replacement, then delete what it replaces.** A republication used to delete the
league's copy and then post the new one, so a post Discord refused left the channel empty. Each
posting job posts and keeps the ids of what it replaces, and plans one `delete_message` job for
each, which runs after the new message stands. A stop between the two leaves both messages up and
the delete to be carried out when the bot starts again.

**A post's id is saved with its done mark** (`Step.record`): the new ids are written on the save
that marks the job done, so a stop can leave a message posted, never an id unsaved beside a job
marked done. A picture is the exception (see below).

**The job reads its channel when it runs**, never when it is planned: a channel set while the
job is stopped is posted to on the next try or Retry. A channel the division was never given plans
no job, as the republication has always stood silent about one never set; a channel given and since
deleted fails its job and stops the queue, for the manager to set the channel and press Retry.

**A job that fails part-way keeps what it sent.** The ids of the chunks of a table that Discord
refused midway, which the bot could not take down itself, travel on the failure; the next try
removes them first, so a retry leaves no second copy.

**A retry posts as text** (Constitution XIV, rule 8): the picture is attempted on the first try
alone. The picture is posted through image's own function, which saves its ids and deletes what it
replaces itself, so a drawn table has nothing for the job to record or delete (image's results
poster and the attendance sheet save their own ids until their passes move them, #288).

**A standings job covers a round's both championships**, since the text flow posts one message
carrying both and only the graphics are posted one each.

**What a stop names.** A posting job describes itself by what it posts and where ("round 3's
Feature Race results in <#700>"), so the stop notice, which carries the kind of fault, says what
to repair. No sync command is named while the bot is still trying the job: a sync would post a
second copy. Only a job discarded is named with the command that finishes it, or, while the season
is pending completion, where the sync commands are refused, with `/results rounds amend` instead.

**A channel is deleted by a job of its own** (`delete_channel`), last in an approval that ends a
round's channel: a channel already gone completes it, and its `record` forgets the amendment record
naming the channel in the save that marks it done. A channel Discord refuses to delete stops the
queue, and once discarded is named for deletion by hand.

**The batch notice is a pair of jobs** bracketing a republication, each a job like any other: a
notice Discord refuses stops the queue (the owner's rule, every failure stops it).
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    GuildUnavailable,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
)
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.change_queue import OutcomeContext, Step, StepContext
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.batch_notice import delete_notice, send_notice
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.models.standings_snapshot import (
    DriverStandingsSnapshot,
    TeamStandingsSnapshot,
)
from leaguebot.results.services import standings_service
from leaguebot.results.services.results_post_service import (
    _delete_posting,
    _label_from_status,
    _load_driver_rows,
    _sr_from_row,
    delete_round_channel,
    driver_standings_for_display,
    produce_session_results,
    produce_standings,
    set_standings_message_id_on,
    standings_display_names,
)

log = logging.getLogger(__name__)

NAMES = "names"
POST_SESSION_RESULTS = "post_session_results"
POST_STANDINGS = "post_standings"
DELETE_MESSAGE = "delete_message"
POST_BATCH_NOTICE = "post_batch_notice"
DELETE_BATCH_NOTICE = "delete_batch_notice"
DELETE_CHANNEL = "delete_channel"

#: The notice a republication posts in the submission channel while it draws, where its change
#: type gives no words of its own.
DEFAULT_NOTICE = "\U0001f3a8 Updating results and standings — one moment."

_RESULTS_SYNC = "/results rounds sync"
_STANDINGS_SYNC = "/results standings sync"
_AMEND = "/results rounds amend"


def posting_steps() -> dict[str, Step]:
    """The posting jobs, keyed by their names, for a change type's `steps`."""
    return {
        NAMES: Step(NAMES, StepKind.ACT, _resolve_names, describe=_describe_names),
        POST_SESSION_RESULTS: Step(
            POST_SESSION_RESULTS, StepKind.ACT, _post_session_results,
            still_due=_session_still_due, describe=_describe_session_post,
            record=_record_session_post,
        ),
        POST_STANDINGS: Step(
            POST_STANDINGS, StepKind.ACT, _post_standings,
            describe=_describe_standings_post, record=_record_standings_post,
        ),
        DELETE_MESSAGE: Step(
            DELETE_MESSAGE, StepKind.DELETE, _delete_message, describe=_describe_delete,
        ),
        POST_BATCH_NOTICE: Step(
            POST_BATCH_NOTICE, StepKind.ACT, _post_batch_notice,
            describe=_describe_notice_post,
        ),
        DELETE_BATCH_NOTICE: Step(
            DELETE_BATCH_NOTICE, StepKind.DELETE, _delete_batch_notice,
            still_due=_notice_posted, describe=_describe_notice_delete,
        ),
        DELETE_CHANNEL: Step(
            DELETE_CHANNEL, StepKind.DELETE, _delete_channel,
            describe=_describe_channel_delete, record=_record_channel_deleted,
        ),
    }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


async def plan_posts(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    label: str,
    later_rounds: bool = False,
    notice: bool | str = False,
    notice_channel_id: int | None = None,
) -> list[PlannedStep]:
    """The jobs that republish *round_id*'s results and standings under *label*, in order, read on *db*.

    The notice that brackets them, where *notice* asks for one (``True`` for the default words,
    or the words themselves): the submission channel's, or *notice_channel_id* where the caller
    posts it elsewhere (an amendment's channel). Then one results job for each ACTIVE session whose
    division has a results channel; one standings job for the round where the division has a
    standings channel; and, where *later_rounds*, one for each later round of the division that is
    not cancelled and has standings posted, in round order, each under its own lifecycle label.
    A round with no ACTIVE session had nothing posted and plans none of its own.

    Each posting job carries the command that finishes it should a league admin discard it
    (`/results rounds amend` while the season is pending completion, where the sync commands are
    refused), since an outcome is formed with nothing awaited.
    """
    row = await (
        await db.execute(
            "SELECT r.division_id, r.round_number, drc.results_channel_id, "
            "       drc.standings_channel_id "
            "FROM rounds r LEFT JOIN division_results_config drc "
            "     ON drc.division_id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
    ).fetchone()
    if row is None:
        return []
    stage = await (
        await db.execute(
            "SELECT stage FROM seasons WHERE status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1"
        )
    ).fetchone()
    pending = stage is not None and stage["stage"] == SeasonStage.PENDING_COMPLETION.value
    planned: list[PlannedStep] = []

    notice_channel = notice_channel_id
    if notice and notice_channel is None:
        submission = await (
            await db.execute(
                "SELECT channel_id FROM round_submission_channels WHERE round_id = ?",
                (round_id,),
            )
        ).fetchone()
        notice_channel = None if submission is None else int(submission["channel_id"])
    notice_text = DEFAULT_NOTICE if notice is True else (notice or "")
    if notice and notice_channel is not None:
        planned.append(
            PlannedStep(POST_BATCH_NOTICE, {"channel_id": notice_channel, "text": notice_text})
        )

    sessions = await (
        await db.execute(
            "SELECT id, session_type FROM session_results "
            "WHERE round_id = ? AND status = 'ACTIVE' "
            "ORDER BY id",
            (round_id,),
        )
    ).fetchall()
    if sessions:
        if row["results_channel_id"]:
            for session in sessions:
                planned.append(PlannedStep(POST_SESSION_RESULTS, {
                    "session_id": int(session["id"]), "round_id": round_id, "label": label,
                    "round_number": row["round_number"],
                    "session": f"{_session_name(session['session_type'])} results",
                    "remedy": _AMEND if pending else _RESULTS_SYNC,
                }))
        if row["standings_channel_id"]:
            planned.append(_standings_job(round_id, row["round_number"], label, pending))

    if later_rounds and row["standings_channel_id"]:
        later = await (
            await db.execute(
                "SELECT r.id, r.round_number, r.status FROM rounds r "
                "WHERE r.division_id = ? AND r.round_number > ? AND r.status != 'CANCELLED' "
                "  AND EXISTS (SELECT 1 FROM driver_standings_snapshots s "
                "              WHERE s.round_id = r.id AND s.division_id = r.division_id "
                "                AND (s.standings_message_id IS NOT NULL "
                "                     OR s.constructor_standings_message_id IS NOT NULL)) "
                "ORDER BY r.round_number",
                (row["division_id"], row["round_number"]),
            )
        ).fetchall()
        for rnd in later:
            planned.append(_standings_job(
                int(rnd["id"]), rnd["round_number"], _label_from_status(rnd["status"] or ""),
                pending,
            ))

    if notice and notice_channel is not None:
        planned.append(PlannedStep(DELETE_BATCH_NOTICE, {"channel_id": notice_channel}))
    return planned


def _standings_job(
    round_id: int, round_number: int, label: str, pending: bool, *, fresh: bool = False
) -> PlannedStep:
    payload: dict[str, Any] = {
        "round_id": round_id, "round_number": round_number, "label": label,
        "remedy": _AMEND if pending else _STANDINGS_SYNC,
    }
    if fresh:
        payload["fresh"] = True
    return PlannedStep(POST_STANDINGS, payload)


async def plan_division_posts(
    db: aiosqlite.Connection, division_id: int, *, division_name: str | None = None
) -> list[PlannedStep]:
    """The jobs that rebuild everything a division's results and standings channels show, read on
    *db*: every round's results in round order, then every round's standings in round order, each
    a new message (a repost, never an edit), under the round's own lifecycle label.

    Division-wide rather than one round, since a repost lands at the bottom of a channel and
    replacing one round alone would read 2, 3, 4, 5, 1. A round with no ACTIVE session had nothing
    posted and plans none; a channel the division was never given plans none of its jobs.

    With *division_name*, every job carries it as `"division"` and `not_done` names it ("Round 2's
    Feature Race results in Pro were not posted"): a change that reposts several divisions says
    which. Planned with none, as the amendment's rebuild plans them, the lines name no division.
    """
    config = await (
        await db.execute(
            "SELECT results_channel_id, standings_channel_id FROM division_results_config "
            "WHERE division_id = ?",
            (division_id,),
        )
    ).fetchone()
    if config is None:
        return []
    stage = await (
        await db.execute(
            "SELECT stage FROM seasons WHERE status IN ('SETUP', 'ACTIVE') ORDER BY id DESC LIMIT 1"
        )
    ).fetchone()
    pending = stage is not None and stage["stage"] == SeasonStage.PENDING_COMPLETION.value
    rounds = await (
        await db.execute(
            "SELECT DISTINCT r.id, r.round_number, r.status FROM rounds r "
            "JOIN session_results sr ON sr.round_id = r.id "
            "WHERE r.division_id = ? AND sr.status = 'ACTIVE' ORDER BY r.round_number",
            (division_id,),
        )
    ).fetchall()
    results: list[PlannedStep] = []
    standings: list[PlannedStep] = []
    for rnd in rounds:
        label = _label_from_status(rnd["status"] or "")
        if config["results_channel_id"]:
            sessions = await (
                await db.execute(
                    "SELECT id, session_type FROM session_results "
                    "WHERE round_id = ? AND status = 'ACTIVE' ORDER BY id",
                    (rnd["id"],),
                )
            ).fetchall()
            for session in sessions:
                results.append(PlannedStep(POST_SESSION_RESULTS, {
                    "session_id": int(session["id"]), "round_id": int(rnd["id"]), "label": label,
                    "round_number": rnd["round_number"],
                    "session": f"{_session_name(session['session_type'])} results",
                    "remedy": _AMEND if pending else _RESULTS_SYNC,
                }))
        if config["standings_channel_id"]:
            standings.append(
                _standings_job(int(rnd["id"]), rnd["round_number"], label, pending, fresh=True)
            )
    planned = [*results, *standings]
    if division_name is not None:
        for step in planned:
            step.payload["division"] = division_name
    return planned


def _in_division(payload: dict[str, Any]) -> str:
    """" in Pro" where the job was planned with its division's name, else nothing."""
    division = payload.get("division")
    return f" in {division}" if division else ""


def not_done(ctx: OutcomeContext, *, reposted: bool = False) -> list[str]:
    """One line for each posting job a league admin discarded, in the order the jobs stood.

    What was not done: a message not deleted is linked for deletion by hand, a notice left
    standing is linked too, a channel left standing is named. The tables not posted end with one
    line naming the commands that finish them, **both sync commands** since a league reads a
    round's results and the division's standings separately and each has its own, or
    `/results rounds amend` alone while the season is pending completion, where the sync commands
    are refused (the jobs carry which, since an outcome is formed with nothing awaited). A table
    that stopped part-way keeps the messages it had already sent, and a discard names them for
    deletion by hand: nothing else will remove them (a retry did).

    *reposted* is for a change that posts every table afresh itself once the outcome is formed (a
    review's opening whose prompt was discarded too, asking for the review again): the last line
    then says so in place of the commands, which would post the tables a second time.
    """
    lines: list[str] = []
    pending = False
    unposted = False
    for view in ctx.steps:
        result = view.result or {}
        if "discarded" not in result:
            continue
        payload = view.payload
        if view.name == POST_SESSION_RESULTS:
            unposted = True
            pending = pending or payload["remedy"] == _AMEND
            lines.append(
                f"⚠️ Round {payload.get('round_number', '?')}'s "
                f"{payload.get('session', 'results')}{_in_division(payload)} were not posted."
            )
        elif view.name == POST_STANDINGS:
            unposted = True
            pending = pending or payload["remedy"] == _AMEND
            lines.append(
                f"⚠️ Round {payload.get('round_number', '?')}'s standings"
                f"{_in_division(payload)} were not posted."
            )
        if view.name in (POST_SESSION_RESULTS, POST_STANDINGS) and result.get("new"):
            ids = ", ".join(str(i) for i in result["new"])
            lines.append(
                f"⚠️ A part of it was posted before it stopped and stands in "
                f"<#{result['channel_id']}> (message {ids}): delete it by hand, or the "
                f"{'review' if reposted else 'sync'} will post the whole table beside it."
            )
        elif view.name == DELETE_MESSAGE:
            link = result.get("link") or f"in <#{payload['channel_id']}>"
            lines.append(f"⚠️ An earlier message could not be deleted ({link}): delete it by hand.")
        elif view.name == POST_BATCH_NOTICE:
            lines.append("⚠️ The \"one moment\" notice was not posted.")
        elif view.name == DELETE_BATCH_NOTICE:
            link = result.get("link") or f"in <#{payload['channel_id']}>"
            lines.append(f"⚠️ The \"one moment\" notice could not be deleted ({link}): delete it by hand.")
        elif view.name == DELETE_CHANNEL:
            lines.append(
                f"⚠️ The channel <#{payload['channel_id']}> could not be deleted: delete it by hand."
            )
    if unposted and reposted:
        lines.append("Every table is posted again with the review.")
    elif unposted:
        lines.append(
            f"Repair the cause, then amend the round again with `{_AMEND}`: the sync commands are "
            "closed while the season is pending completion."
            if pending else
            f"Repair the cause, then run `{_RESULTS_SYNC}` and `{_STANDINGS_SYNC}`."
        )
    return lines


# ---------------------------------------------------------------------------
# The drivers' display names
# ---------------------------------------------------------------------------


async def _describe_names(ctx: StepContext) -> str:
    return "looking up the drivers' display names for the standings"


async def _resolve_names(ctx: StepContext) -> StepResult:
    """Resolve, from Discord, the names the division's standings are drawn under.

    A job of its own before the save that recomputes the snapshots, so that the save awaits
    nothing but its connection; the names also order a full tie, so the stored classification and
    the one the league is shown cannot disagree. Read by `display_names`. The division is the
    job's own, or the one of the change's `round_id`.
    """
    try:
        guild: discord.Guild | None = await _league_guild(ctx.bot)
    except GuildUnavailable:
        # Names are not what the change cannot go on without: the standings are ordered, in a
        # full tie, by user id, and a job that needs the server stops the queue on its own.
        guild = None
    division_id = ctx.step_payload.get("division_id")
    if division_id is None:
        division_id = (await _round_row(ctx.db_path, int(ctx.payload["round_id"])))["division_id"]
    names = await standings_display_names(ctx.db_path, int(division_id), guild, ctx.bot)
    return StepResult(result={"names": names})


def display_names(ctx: StepContext, division_id: int | None = None) -> dict[int, str] | None:
    """The names the change's `names` job resolved, or None where it resolved none (the keys
    came through JSON as text).

    With *division_id*, the names of that division's `names` job (a change that resolves several
    plans one for each, its payload's `division_id`); with none, the first job done, which is how
    a change of one division reads them.
    """
    for view in ctx.steps:
        if division_id is not None and view.payload.get("division_id") != division_id:
            continue
        if view.name == NAMES and view.done:
            found = (view.result or {}).get("names")
            return None if found is None else {int(k): v for k, v in found.items()}
    return None


# ---------------------------------------------------------------------------
# What a job reads when it runs
# ---------------------------------------------------------------------------


async def _league_guild(bot: LeagueBot) -> discord.Guild:
    """The league's server through core's `league_guild`, or `GuildUnavailable` where it gives None."""
    guild = await league_guild(bot)
    if guild is None:
        raise GuildUnavailable("the league's server is not in the cache")
    return guild


async def _round_row(db_path: str, round_id: int) -> aiosqlite.Row:
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT r.id, r.division_id, r.round_number, r.track_name, r.status, "
                "       d.name AS division_name, drc.results_channel_id, "
                "       drc.standings_channel_id, drc.reserves_in_standings "
                "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                "LEFT JOIN division_results_config drc ON drc.division_id = r.division_id "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
    if row is None:
        raise LookupError(f"round {round_id} is not in the database")
    return row


def _channel(guild: discord.Guild, channel_id: int, setting: str) -> discord.TextChannel:
    """The channel *channel_id* of the guild, or a failure naming what to repair where it is gone."""
    channel = as_text_channel(guild.get_channel(channel_id))
    if channel is None:
        raise StepFailedOnDiscord(
            f"the {setting} channel <#{channel_id}> no longer exists or cannot be posted in"
        )
    return channel


async def _remove_kept(guild: discord.Guild, kept: dict[str, Any] | None) -> None:
    """Take down the messages a job's last try sent and could not take down itself.

    They are removed before the job posts again, so a retry leaves no second copy. A channel
    since deleted holds none of them. A message that cannot be removed fails the job again,
    keeping what it still has to remove.
    """
    ids = [int(i) for i in (kept or {}).get("new") or []]
    channel_id = (kept or {}).get("channel_id")
    if not ids or channel_id is None:
        return
    channel = as_text_channel(guild.get_channel(int(channel_id)))
    if channel is None:
        return
    failures: list[discord.HTTPException] = []
    left: list[int] = []
    for message_id in ids:
        left.extend(await _delete_posting(
            channel, message_id, [message_id], label="part-posted message", failures=failures,
        ))
    if left:
        raise StepFailedOnDiscord(
            f"{len(left)} message(s) of the earlier try could not be removed",
            result={"new": left, "channel_id": int(channel_id)},
        ) from (failures[0] if failures else None)


def _stranded(error: Exception, channel_id: int) -> dict[str, Any] | None:
    """What a failed send left standing, which `_send_chunked` puts on the failure, as a result."""
    left = getattr(error, "left_standing", None)
    return {"new": list(left), "channel_id": channel_id} if left else None


def _deletes(channel_id: int, ids: list[int], what: str) -> tuple[PlannedStep, ...]:
    """One `delete_message` job for each of *ids*, in *channel_id*."""
    return tuple(
        PlannedStep(DELETE_MESSAGE, {"channel_id": channel_id, "message_id": int(i), "what": what})
        for i in ids
    )


# ---------------------------------------------------------------------------
# A session's results
# ---------------------------------------------------------------------------


async def _session_row(db_path: str, session_id: int) -> aiosqlite.Row | None:
    async with get_connection(db_path) as db:
        return await (
            await db.execute(
                "SELECT id, round_id, division_id, session_type, status, config_name, "
                "       submitted_by, submitted_at, results_message_id, results_message_ids "
                "FROM session_results WHERE id = ?",
                (session_id,),
            )
        ).fetchone()


def _session_name(session_type: str) -> str:
    return session_type.replace("_", " ").title()


async def _session_still_due(ctx: StepContext) -> bool:
    """A session still ACTIVE, and its division still given a results channel."""
    row = await _session_row(ctx.db_path, ctx.step_payload["session_id"])
    if row is None or row["status"] != "ACTIVE":
        return False
    rnd = await _round_row(ctx.db_path, row["round_id"])
    return bool(rnd["results_channel_id"])


async def _describe_session_post(ctx: StepContext) -> str:
    row = await _session_row(ctx.db_path, ctx.step_payload["session_id"])
    if row is None:
        return "posting a session's results"
    rnd = await _round_row(ctx.db_path, row["round_id"])
    where = f" in <#{rnd['results_channel_id']}>" if rnd["results_channel_id"] else ""
    return (
        f"posting round {rnd['round_number']}'s {_session_name(row['session_type'])} "
        f"results{where}"
    )


async def _post_session_results(ctx: StepContext) -> StepResult:
    payload = ctx.step_payload
    row = await _session_row(ctx.db_path, payload["session_id"])
    if row is None:
        raise LookupError(f"session result {payload['session_id']} is not in the database")
    rnd = await _round_row(ctx.db_path, row["round_id"])
    guild = await _league_guild(ctx.bot)
    channel_id = int(rnd["results_channel_id"])
    channel = _channel(guild, channel_id, "results")
    await _remove_kept(guild, ctx.kept)

    session_type = SessionType(row["session_type"])
    driver_rows = await _load_driver_rows(ctx.db_path, row["id"], session_type)
    points_map = {
        r.driver_user_id: r.points_awarded + getattr(r, "fastest_lap_bonus", 0)
        for r in driver_rows
    }
    is_sprint = str(await _round_format(ctx.db_path, row["round_id"])).upper() == "SPRINT"
    try:
        posted = await produce_session_results(
            ctx.db_path, _sr_from_row(row), driver_rows, points_map, channel, guild,
            rnd["round_number"], rnd["track_name"] or "Unknown", payload["label"], is_sprint,
            # The picture on the first try alone; a retry posts the text (XIV rule 8).
            bot=ctx.bot if ctx.tries == 0 else None,
        )
    except Exception as error:
        kept = _stranded(error, channel_id)
        if kept is not None:
            raise StepFailedOnDiscord(
                "the results were posted in part and the rest was refused", result=kept
            ) from error
        raise
    result = {
        "new": [] if posted.drawn else posted.new, "old": posted.old,
        "drawn": posted.drawn, "channel_id": channel_id,
    }
    what = f"round {rnd['round_number']}'s {_session_name(row['session_type'])} results"
    return StepResult(result=result, then=_deletes(channel_id, posted.old, what))


async def _round_format(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        row = await (
            await db.execute("SELECT format FROM rounds WHERE id = ?", (round_id,))
        ).fetchone()
    return "" if row is None else str(row["format"])


async def _record_session_post(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """Save the new ids on the session, with the job's mark. A picture saved its own."""
    new = result.result.get("new") or []
    if not new:
        return
    await db.execute(
        "UPDATE session_results SET results_message_id = ?, results_message_ids = ? WHERE id = ?",
        (new[0], json.dumps(new), ctx.step_payload["session_id"]),
    )


# ---------------------------------------------------------------------------
# A round's standings
# ---------------------------------------------------------------------------


async def _describe_standings_post(ctx: StepContext) -> str:
    rnd = await _round_row(ctx.db_path, ctx.step_payload["round_id"])
    where = f" in <#{rnd['standings_channel_id']}>" if rnd["standings_channel_id"] else ""
    return f"posting round {rnd['round_number']}'s standings{where}"


async def _post_standings(ctx: StepContext) -> StepResult:
    payload = ctx.step_payload
    round_id = payload["round_id"]
    rnd = await _round_row(ctx.db_path, round_id)
    if not rnd["standings_channel_id"]:
        return StepResult(result={"dropped": True})
    division_id = int(rnd["division_id"])
    guild = await _league_guild(ctx.bot)
    channel_id = int(rnd["standings_channel_id"])
    channel = _channel(guild, channel_id, "standings")
    await _remove_kept(guild, ctx.kept)

    driver_snaps, team_snaps = await _stored_standings(
        ctx.db_path, division_id, round_id, guild, ctx.bot
    )
    show_reserves = (
        bool(rnd["reserves_in_standings"]) if rnd["reserves_in_standings"] is not None else True
    )
    try:
        tables = await produce_standings(
            ctx.db_path, division_id, round_id, rnd["round_number"], rnd["track_name"] or "Unknown",
            channel, driver_snaps, team_snaps, guild, show_reserves, payload["label"],
            bot=ctx.bot if ctx.tries == 0 else None,
            fresh=bool(payload.get("fresh")),
        )
    except Exception as error:
        kept = _stranded(error, channel_id)
        if kept is not None:
            raise StepFailedOnDiscord(
                "the standings were posted in part and the rest was refused", result=kept
            ) from error
        raise
    what = f"round {rnd['round_number']}'s standings"
    then: list[PlannedStep] = []
    new: list[int] = []
    for table in tables:
        then.extend(_deletes(channel_id, table.old, what))
        if not table.edited:
            new.extend(table.new)
    result = {
        "tables": [
            {"new": t.new, "old": t.old, "edited": t.edited, "championship": t.championship}
            for t in tables
        ],
        "new": new, "channel_id": channel_id,
        "division_id": division_id, "round_id": round_id,
    }
    return StepResult(result=result, then=tuple(then))


async def _stored_standings(
    db_path: str, division_id: int, round_id: int, guild: discord.Guild, bot: LeagueBot
) -> tuple[list[DriverStandingsSnapshot], list[TeamStandingsSnapshot]]:
    """The standings the round's snapshots hold, which a posting draws.

    The snapshot is the order a league was shown, and the save that changed the round's points
    has recomputed it (on names the `names` job resolved), so a posting draws what is stored
    rather than computing it a second time. Where the round holds no snapshot, or no team's, the
    standings are computed as they always were.
    """
    async with get_connection(db_path) as db:
        drivers = await (await db.execute(
            "SELECT driver_user_id, standing_position, total_points, finish_counts, "
            "       first_finish_rounds FROM driver_standings_snapshots "
            "WHERE round_id = ? AND division_id = ? ORDER BY standing_position",
            (round_id, division_id),
        )).fetchall()
        teams = await (await db.execute(
            "SELECT team_instance_id, standing_position, total_points, finish_counts, "
            "       first_finish_rounds FROM team_standings_snapshots "
            "WHERE round_id = ? AND division_id = ? ORDER BY standing_position",
            (round_id, division_id),
        )).fetchall()
        raced = {
            int(r["driver_user_id"]) for r in await (await db.execute(
                "SELECT rsr.driver_user_id FROM race_session_results rsr "
                "JOIN session_results sr ON sr.id = rsr.session_result_id "
                "JOIN rounds r ON r.id = sr.round_id "
                "WHERE r.division_id = ? AND sr.status = 'ACTIVE' "
                "UNION SELECT qsr.driver_user_id FROM qualifying_session_results qsr "
                "JOIN session_results sr ON sr.id = qsr.session_result_id "
                "JOIN rounds r ON r.id = sr.round_id "
                "WHERE r.division_id = ? AND sr.status = 'ACTIVE'",
                (division_id, division_id),
            )).fetchall()
        }
    if drivers:
        driver_snaps = [
            DriverStandingsSnapshot(
                id=0, round_id=round_id, division_id=division_id,
                driver_user_id=int(r["driver_user_id"]),
                standing_position=int(r["standing_position"]),
                total_points=int(r["total_points"]),
                finish_counts={int(k): v for k, v in json.loads(r["finish_counts"]).items()},
                first_finish_rounds={
                    int(k): v for k, v in json.loads(r["first_finish_rounds"]).items()
                },
                race_participant=int(r["driver_user_id"]) in raced,
            )
            for r in drivers
        ]
    else:
        driver_snaps = await driver_standings_for_display(
            db_path, division_id, round_id, guild, bot
        )
    if teams:
        team_snaps = [
            TeamStandingsSnapshot(
                id=0, round_id=round_id, division_id=division_id,
                team_instance_id=int(r["team_instance_id"]),
                standing_position=int(r["standing_position"]),
                total_points=int(r["total_points"]),
                finish_counts={int(k): v for k, v in json.loads(r["finish_counts"]).items()},
                first_finish_rounds={
                    int(k): v for k, v in json.loads(r["first_finish_rounds"]).items()
                },
            )
            for r in teams
        ]
    else:
        team_snaps = await standings_service.compute_team_standings(
            db_path, division_id, round_id
        )
    return driver_snaps, team_snaps


async def _record_standings_post(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """Save each table's ids with the job's mark: an edit keeps its anchor, a post names its own."""
    if result.result.get("dropped"):
        return
    for table in result.result["tables"]:
        if table["championship"] is None:
            continue
        # A table with nothing new is a championship whose message a text table replaced: its
        # id is forgotten, the old message coming down in its own job.
        new = table["new"]
        await set_standings_message_id_on(
            db, result.result["division_id"], result.result["round_id"],
            new[0] if new else None, table["championship"],
            message_ids=json.dumps(new) if new else None,
        )


# ---------------------------------------------------------------------------
# Taking an old message down
# ---------------------------------------------------------------------------


async def _describe_delete(ctx: StepContext) -> str:
    payload = ctx.step_payload
    return f"deleting the earlier {payload.get('what', 'message')} in <#{payload['channel_id']}>"


async def _delete_message(ctx: StepContext) -> StepResult:
    """Delete one old message: a message already gone, or its channel, completes the job."""
    payload = ctx.step_payload
    guild = await _league_guild(ctx.bot)
    channel = as_text_channel(guild.get_channel(int(payload["channel_id"])))
    if channel is None:
        return StepResult(result={"gone": True})
    message_id = int(payload["message_id"])
    failures: list[discord.HTTPException] = []
    left = await _delete_posting(
        channel, message_id, [message_id], label=payload.get("what", "message"),
        failures=failures,
    )
    if left:
        raise StepFailedOnDiscord(
            "the earlier message could not be deleted",
            result={"link": channel.get_partial_message(message_id).jump_url},
        ) from (failures[0] if failures else None)
    return StepResult(result={"deleted": True})


# ---------------------------------------------------------------------------
# The batch notice
# ---------------------------------------------------------------------------


async def _describe_notice_post(ctx: StepContext) -> str:
    return f"posting the notice that results are being updated in <#{ctx.step_payload['channel_id']}>"


async def _describe_notice_delete(ctx: StepContext) -> str:
    return f"deleting the notice that results are being updated in <#{ctx.step_payload['channel_id']}>"


async def _post_batch_notice(ctx: StepContext) -> StepResult:
    guild = await _league_guild(ctx.bot)
    channel_id = int(ctx.step_payload["channel_id"])
    channel = _channel(guild, channel_id, "submission")
    message = await send_notice(channel, ctx.step_payload.get("text") or DEFAULT_NOTICE)
    return StepResult(
        result={"message_id": message.id, "channel_id": channel_id, "link": message.jump_url}
    )


def _posted_notice(ctx: StepContext) -> dict[str, Any] | None:
    """The result of the notice job that posted the notice in this job's channel, if it did."""
    for view in reversed(ctx.steps):
        result = view.result or {}
        if (
            view.name == POST_BATCH_NOTICE
            and view.payload.get("channel_id") == ctx.step_payload["channel_id"]
            and result.get("message_id") is not None
        ):
            return result
    return None


async def _notice_posted(ctx: StepContext) -> bool:
    """The notice is deleted only if it was posted: a discarded post leaves nothing to delete."""
    return _posted_notice(ctx) is not None


async def _delete_batch_notice(ctx: StepContext) -> StepResult:
    posted = _posted_notice(ctx)
    if posted is None:
        return StepResult(result={"dropped": True})
    guild = await _league_guild(ctx.bot)
    channel = as_text_channel(guild.get_channel(int(posted["channel_id"])))
    if channel is None:
        return StepResult(result={"gone": True})
    try:
        await delete_notice(channel.get_partial_message(int(posted["message_id"])))
    except discord.NotFound:
        raise
    except discord.HTTPException as error:
        raise StepFailedOnDiscord(
            "the notice could not be deleted", result={"link": posted.get("link")}
        ) from error
    return StepResult(result={"deleted": True})


# ---------------------------------------------------------------------------
# Taking a channel down
# ---------------------------------------------------------------------------


async def _describe_channel_delete(ctx: StepContext) -> str:
    payload = ctx.step_payload
    return f"deleting the {payload.get('what', 'channel')} <#{payload['channel_id']}>"


async def _delete_channel(ctx: StepContext) -> StepResult:
    """Delete a round's submission or amendment channel: a channel already gone completes the job.

    The job's `record` forgets the amendment record that names the channel, as
    `close_submission_channel` does once the channel has gone, so a channel that could not be
    deleted keeps it for the retry.
    """
    payload = ctx.step_payload
    guild = await _league_guild(ctx.bot)
    channel = guild.get_channel(int(payload["channel_id"]))
    if channel is None:
        return StepResult(result={"gone": True})
    try:
        await delete_round_channel(
            channel, reason=payload.get("reason") or "Results submission complete"
        )
    except discord.NotFound:
        raise
    except discord.HTTPException as error:
        raise StepFailedOnDiscord("the channel could not be deleted") from error
    return StepResult(result={"deleted": True})


async def _record_channel_deleted(
    db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
) -> None:
    """Forget the amendment record naming the channel, in the save that marks the deletion done:
    scoped by the channel as well as the round, since a fresh amendment of the round may by now
    hold its own record."""
    payload = ctx.step_payload
    await db.execute(
        "DELETE FROM round_amend_channels WHERE round_id = ? AND channel_id = ?",
        (int(payload["round_id"]), int(payload["channel_id"])),
    )


__all__ = [
    "NAMES", "display_names", "DEFAULT_NOTICE", "DELETE_BATCH_NOTICE", "DELETE_CHANNEL", "DELETE_MESSAGE", "POST_BATCH_NOTICE",
    "POST_SESSION_RESULTS", "POST_STANDINGS", "not_done", "plan_division_posts", "plan_posts", "posting_steps",
]
