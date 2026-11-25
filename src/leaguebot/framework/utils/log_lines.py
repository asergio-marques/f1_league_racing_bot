"""The standard lines a refusal, a cancel and a lapse write to the log channel.

Every command, button and form that changes something, or tries to, records every outcome in
the log channel — a success, a refusal or a failure, whoever used it (the core specification's
"The record of what changed"). A failure is `report_failure`'s, in
`framework/utils/interaction_errors.py`. This module holds the other two, and the change queue's
lines, so that each standard line is formed in one place:

- **`refuse`** answers the member, seen by them alone, and writes one line:
  "⛔ {what} refused for {member} — {reason}". The line itself is **`record_refusal`**'s, which
  writes it without answering, for a refusal whose reply is not an interaction's: an answer
  typed into the signup wizard is a message, refused by a public reply in the driver's channel.
- **`record_abandoned`** writes the line for a confirmation cancelled or left to lapse:
  "↩️ {what} cancelled by {member}", or "⌛ {what} lapsed unconfirmed (started by {member})".

**A member is named by their server display name and their mention**, "Alex (<@4242>)": the
name for the person reading the channel, the mention for the account it was. A member no longer
on the server is named by mention alone, and a line with nobody recorded as "a member".

**Neither ever raises.** Each runs where the command is already ending, and each half is
attempted on its own: a log channel that cannot be written must not cost the member their
answer, nor a reply that cannot be sent cost the league its record. A failure of either is
logged with its traceback.

**Every line goes through `OutputRouter.post_log`**, which divides a record too long for one
message; a reply too long for one is sent in parts through `chunk_message`, never cut off.

It lives apart from `interaction_errors` because it reaches the guild through `league_guild`,
and `league_server`, which defines that, imports `interaction_errors`. The two helpers that name a
member from what an interaction carries, `member_named` and `interaction_member`, live in
`framework/utils/member_names.py` so that `interaction_errors` can use them too; this module imports
them and so still offers them to its callers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import discord

from leaguebot.framework.models.change import (
    LEAGUE_LANE,
    RESULT_DRAWN,
    RESULT_NOTICES,
    RESULT_PICTURES,
    DrawnNotices,
    NoticeBlock,
    StepFailedOnDiscord,
    drawn_kept,
)
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.framework.utils.member_names import interaction_member, member_named  # noqa: F401
from leaguebot.utils.messages import chunk_message

log = logging.getLogger(__name__)

#: The marks a reply opens with, which the log line's own mark replaces.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳", "⛓", "⏸️", "⏸")


async def name_of_member(bot: Any, member_id: int | None) -> str:
    """The member *member_id*, named as the log channel names them, found on the league's server.

    For a line written without an interaction — a scheduled lapse. A member who has left the
    server, or who cannot be looked up, is named by mention alone, which still names them; nobody
    recorded is "a member". Never raises, because a line to the log channel must not fail the work
    it records.
    """
    if member_id is None:
        return "a member"
    member = None
    try:
        guild = await league_guild(bot)
        member = guild.get_member(int(member_id)) if guild is not None else None
    except Exception:  # noqa: BLE001 — the mention alone still names them
        log.warning("could not look up member %s on the league's server", member_id, exc_info=True)
    return member_named(getattr(member, "display_name", None), int(member_id))


def reply_reason(reply: str) -> str:
    """The reply's first line, without the mark it opens with: a refusal's line gives it as its reason.

    `refuse` takes it by default, and so does the change queue where it refuses a change when it
    runs.
    """
    lines = reply.strip().splitlines()
    first = lines[0].strip() if lines else ""
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


def reply_reasons(reply: str) -> str:
    """Every line of a multi-line *reply* as its reason, `; ` between them: a refusal that names
    more than one thing (`/bot init`'s two unusable channels, one to a line) is recorded whole,
    where `reply_reason` gives only the first line."""
    return "; ".join(reply_reason(line) for line in reply.splitlines() if line.strip())


async def _named(bot: Any, member: int | discord.abc.User | None) -> str:
    """*member* named as the log channel names them: a member object by the display name it
    carries, an id (or an object with no name) found on the league's server, nobody as "a member"."""
    if member is None or isinstance(member, int):
        return await name_of_member(bot, member)
    if isinstance(getattr(member, "display_name", None), str):
        return member_named(member.display_name, member.id)
    return await name_of_member(bot, member.id)


def refusal_line(named: str, what: str, reason: str, *, detail: str | None = None) -> str:
    """The log channel's line for *what* refused for *named*, because of *reason*.

    *detail*, where given, is written beneath the line on a line of its own: what the reason
    leaves out, for the log channel and not the member's reply.

    The one place a refusal's line is formed: `record_refusal` writes it, and so does the change
    queue where it refuses a change when it runs.
    """
    line = f"⛔ {what} refused for {named} — {reason}"
    return f"{line}\n{detail}" if detail else line


class _Lane:
    """How a line names the lane a change runs on: "the queue" for the league's, and "the turns in
    <#channel>" for a conversation's, with the verbs that follow it in the plural."""

    def __init__(self, lane: str) -> None:
        if lane.startswith("channel:"):
            self.the, self.is_, self.runs, self.stays = (
                f"the turns in <#{lane.split(':', 1)[1]}>", "are", "run", "stay",
            )
        else:
            self.the, self.is_, self.runs, self.stays = "the queue", "is", "runs", "stays"

    @property
    def The(self) -> str:  # noqa: N802 — the same words, capitalised
        return self.the[:1].upper() + self.the[1:]


def lane_name(lane: str) -> str:
    """How a reply names *lane* to a league admin: "the queue" for the league's, "the turns in
    <#channel>" for a conversation's. Never the lane's raw key."""
    return _Lane(lane).the


def stop_line(
    job_id: int, job: str, request: str, asker: str, fault: str, lane: str = LEAGUE_LANE,
    *, admins: int | None = None,
) -> str:
    """The log channel's line for the queue stopping at job *job_id*, *job* being what it does,
    *request* the change it belongs to, *asker* who asked and *fault* the kind of fault.

    *lane* is the lane it stopped: "The queue" for the league's, "The turns in <#channel>" for a
    conversation's. *admins*, where given, is the league admins' role, mentioned on a line of its
    own after the line, so that they are notified (the notice's caller says when).

    The change queue's line, formed here with the others.
    """
    who = _Lane(lane)
    line = (
        f"❌ {who.The} {who.is_} stopped at job #{job_id}: {job} for {request} ({asker}) failed "
        f"({fault}). The bot tries again 1, 5, 10, 15, 30 and 60 minutes after this; a league "
        f"manager or admin may press Retry at any time, and a league admin may press Discard "
        f"to drop it."
    )
    return line if admins is None else f"{line}\n<@&{admins}>"


def held_behind_text(job_id: int, lane: str = LEAGUE_LANE, *, overtaking: bool = False) -> str:
    """What a member's acknowledgement adds where *lane* is stopped at job *job_id*: their request
    runs once that job is cleared, or, where *overtaking* (a setting a league repairs itself with,
    `ChangeType.overtakes_a_stop`), that it runs past the stop instead, which stays as it was. A
    sentence for the end of the acknowledgement, not a log line."""
    who = _Lane(lane)
    if overtaking:
        return (
            f"{who.The} {who.is_} stopped at job #{job_id}, so this request runs at once, past "
            f"the stop; that job stays stopped."
        )
    return (
        f"{who.The} {who.is_} stopped at job #{job_id}, so this request runs once that job is "
        f"cleared."
    )


def hour_line(job_id: int, job: str) -> str:
    """The log channel's line for job *job_id* still failing after an hour: the bot tries no more
    on its own."""
    return (
        f"❌ Job #{job_id} ({job}) still fails after an hour. The bot has stopped trying on its "
        f"own: press Retry on its notice, or Discard."
    )


def restart_line(job_id: int, job: str, lane: str = LEAGUE_LANE) -> str:
    """The log channel's line for *lane* still stopped at job *job_id* after a restart: the bot
    no longer tries it on its own."""
    who = _Lane(lane)
    return (
        f"❌ {who.The} {who.is_} still stopped at job #{job_id} ({job}). After the restart the bot "
        f"no longer tries it on its own: press Retry or Discard on its notice."
    )


def went_through_line(job_id: int, job: str, lane: str = LEAGUE_LANE) -> str:
    """The log channel's line for a stopped job *job_id* going through, on a try or a Retry: the
    lane runs on."""
    return f"✅ Job #{job_id} ({job}) went through. {_Lane(lane).The} {_Lane(lane).runs} on."


def cleared_line(job_id: int, job: str, why: str, lane: str = LEAGUE_LANE) -> str:
    """The log channel's line for a stopped job *job_id* clearing without going through, *why*
    saying how: it was no longer due and was dropped, or its request was refused at its check.
    The lane runs on."""
    who = _Lane(lane)
    return f"ℹ️ Job #{job_id} ({job}) no longer stops {who.the}: {why}. {who.The} {who.runs} on."


def conversation_ended_line(job_id: int, job: str) -> str:
    """The log channel's line for job *job_id*, a turn of a conversation, found no longer due
    because the league ended the conversation: it was dropped."""
    return (
        f"ℹ️ Job #{job_id} ({job}) is no longer due: its conversation was ended, so it was "
        f"dropped."
    )


def retried_line(named: str, job_id: int, job: str, request: str) -> str:
    """The log channel's line for *named* pressing Retry on job *job_id*, *job* being what it
    does and *request* the change it belongs to."""
    return f"{named} | Retry job #{job_id} | Retried\n  {job} for {request}"


def retry_failed_line(
    named: str, job_id: int, job: str, fault: str, lane: str = LEAGUE_LANE
) -> str:
    """The log channel's line for the try *named* asked of job *job_id* failing, *fault* being the
    kind of fault: the lane stays stopped, on the schedule it was on."""
    who = _Lane(lane)
    return (
        f"❌ Retry of job #{job_id} ({job}) by {named} failed ({fault}). {who.The} {who.stays} "
        f"stopped."
    )


def discarded_line(named: str, job_id: int, job: str, request: str) -> str:
    """The log channel's line for *named*, a league admin, discarding job *job_id*, *job* being
    what it does and *request* the change it belongs to: what was not done is beneath it."""
    return f"{named} | Discard job #{job_id} | Discarded\n  not done: {job} for {request}"


async def record_refusal(
    bot: Any,
    member: int | discord.abc.User | None,
    *,
    what: str,
    reason: str,
    detail: str | None = None,
) -> None:
    """Record that *what* was refused for *member*, because of *reason*, without answering anyone.
    *detail*, where given, goes beneath the line (`refusal_line`).

    `refuse` writes its own through here, the line being formed by `refusal_line`. For a
    refusal whose reply is not an interaction's, and so has no `refuse`; *member* is named as
    `record_abandoned` names its own. Never raises: the refusal is already answered, and a record
    that fails is logged on the host, never answered a second time, because a line to the log
    channel must not fail the work it records.
    """
    try:
        named = await _named(bot, member)
        await bot.output_router.post_log(refusal_line(named, what, reason, detail=detail))
    except Exception:  # noqa: BLE001 — the refusal has already been answered
        log.warning("could not record in the log channel that %s was refused", what, exc_info=True)


async def refuse(
    interaction: discord.Interaction,
    reply: str,
    *,
    what: str,
    reason: str | None = None,
    detail: str | None = None,
) -> None:
    """Answer the member with *reply*, seen by them alone, and record the refusal of *what*.

    *what* names what was refused as the log channel should read it — `describe`'s name of the
    command, or that name with its context ("`/round amend` of round 3"). *reason* is the line's
    detail; it defaults to the reply's first line, and a refusal whose reply is a list (the bad
    lines of a paste) passes the list, joined on new lines. *detail* is written beneath the line,
    apart from the reply.

    The reply goes by `followup.send` once the interaction is answered or deferred, and by
    `response.send_message` otherwise, in as many parts as it needs. Never raises.
    """
    user_id = getattr(interaction.user, "id", None)
    try:
        for part in chunk_message(reply):
            if interaction.response.is_done():
                await interaction.followup.send(part, ephemeral=True)
            else:
                await interaction.response.send_message(part, ephemeral=True)
    except Exception:  # noqa: BLE001 — the refusal is still recorded
        log.warning("could not tell user %s that %s was refused", user_id, what, exc_info=True)

    bot = getattr(interaction, "client", None)
    if getattr(bot, "output_router", None) is None:
        return
    beneath = {"detail": detail} if detail else {}
    await record_refusal(
        bot,
        interaction.user,
        what=what,
        reason=reason if reason is not None else reply_reason(reply),
        **beneath,
    )


def abandoned_line(named: str, what: str, *, lapsed: bool, detail: str | None = None) -> str:
    """The log channel's line for *what* cancelled by *named*, or lapsed unconfirmed having been
    started by them, with *detail* beneath it (`record_abandoned` writes it)."""
    line = (
        f"⌛ {what} lapsed unconfirmed (started by {named})"
        if lapsed
        else f"↩️ {what} cancelled by {named}"
    )
    if detail:
        line += "".join(f"\n  {text}" for text in detail.splitlines())
    return line


def notices_block(drawn: str | None, body: Iterable[str]) -> str:
    """The block a picture's render notices are reported in, wherever they are: "Image render
    notices — *drawn*:" ("Image render notices:" where nothing names what was drawn), then *body*,
    the notices as image hands them back, already grouped by kind and indented beneath it."""
    heading = f"Image render notices — {drawn}:" if drawn else "Image render notices:"
    return "\n".join([heading, *body])


def drawn_notices_line(named: str, what: str, drawn: str, body: Iterable[str]) -> str:
    """The line of a job that drew a picture which came back with render notices: the member and
    the change it ran for, as every job line opens, then the `notices_block` for *drawn*, what
    was drawn ("lineup — Pro"), over *body*, the grouped notices. The notices are one line of the
    job, so they reach the log channel once, in one message, never as a second unheaded block."""
    return f"{named} | {what} | " + notices_block(drawn, body)


def job_notice_lines(named: str, what: str, lines: Iterable[str]) -> tuple[str, ...]:
    """A job's *lines*, each opened once with *named* and *what*, the member and the change the job
    ran for, as every line of the log opens (the core specification's "The record of what
    changed"): a notice block the drawing marked (`NoticeBlock`), a line saying why a picture was
    not drawn, every line the job hands back. A line already starting with exactly that opening
    (the verdict's fallback line, opened where it is formed) is left as it is, so opening a job's
    lines twice changes nothing. The change is named as the job names it, a command with its code
    marks ("`/season complete`").

    Two designs were rejected. Marking each line at its origin, as `NoticeBlock` marks a block,
    loses the mark wherever a line is rebuilt, and the framework may not import the modules whose
    drawings form the lines. Opening every line unconditionally under a rule that no caller passes
    a line already opened is a contract nothing enforces, and breaking it writes the opening twice.
    Comparing each line's start with the exact opening needs neither."""
    opening = f"{named} | {what} | "
    return tuple(line if line.startswith(opening) else opening + line for line in lines)


_NOTICES_HEADING = "Image render notices"


def notice_pictures(lines: Iterable[str], *, default: str) -> tuple[DrawnNotices, ...]:
    """The picture each notice block among *lines* (`NoticeBlock`) reports, as a reply lists it: the
    label its heading names ("Image render notices — <label>:", *default* where it names none) and
    the grouped notices beneath. The inverse of `notices_block`."""
    found: list[DrawnNotices] = []
    for line in lines:
        if not isinstance(line, NoticeBlock):
            continue
        heading, *body = str(line).split("\n")
        label = heading.removeprefix(_NOTICES_HEADING).removeprefix(" — ").removesuffix(":")
        found.append(DrawnNotices(label or default, tuple(body)))
    return tuple(found)


@dataclass(frozen=True)
class PictureLines:
    """The lines a job that drew pictures hands back (`framed_picture_lines`): *lines*, the
    pictures' notices among them framed and joined in one line; *pictures*, each picture's label
    and notices, for the change's reply; and *linked*, the index in *lines* of the line holding
    them, None where no picture had a notice."""

    lines: tuple[str, ...]
    pictures: tuple[DrawnNotices, ...] = ()
    linked: int | None = None

    def plus(self, *lines: str) -> "PictureLines":
        """These lines with *lines* after them, the pictures and the link as they were."""
        return PictureLines(self.lines + tuple(lines), self.pictures, self.linked)

    def result(self) -> dict[str, Any]:
        """What the job puts on its result for the reply to list its pictures (`RESULT_PICTURES`):
        nothing where no picture had a notice."""
        if not self.pictures:
            return {}
        return {
            RESULT_PICTURES: [
                {RESULT_DRAWN: each.label, RESULT_NOTICES: list(each.notices)}
                for each in self.pictures
            ]
        }

    def carried_by(self, stop: StepFailedOnDiscord) -> StepFailedOnDiscord:
        """*stop*, keeping these lines, pictures and link on the stop it saves, for the try that
        posts to hand back (`StepFailedOnDiscord.carrying`)."""
        return stop.carrying(self.lines, pictures=self.pictures, linked=self.linked)


def framed_picture_lines(
    named: str,
    what: str,
    lines: Iterable[str],
    *,
    kept: dict[str, Any] | None = None,
    label: str,
) -> PictureLines:
    """What a job that drew pictures writes: the lines an earlier try of it *kept* on its stop, then
    *lines*, those of this try, each opened once with *named* and *what* (`job_notice_lines`), and
    the notice blocks the drawing marked (`NoticeBlock`) joined in a single line, so that all the
    job's notices reach the log in one message, which the reply links to once. A picture
    drawn on an earlier try keeps its own pictures and link; *label* names a block that names no
    picture. Frame the blocks here, before `StepFailedOnDiscord.carrying` keeps them, because the
    mark is lost with the saved result."""
    earlier = drawn_kept(kept)
    new = tuple(lines)
    pictures = earlier.pictures + notice_pictures(new, default=label)
    framed = job_notice_lines(named, what, new)
    blocks = [line for line, each in zip(framed, new) if isinstance(each, NoticeBlock)]
    if blocks:
        first = next(n for n, each in enumerate(new) if isinstance(each, NoticeBlock))
        others = [line for line, each in zip(framed, new) if not isinstance(each, NoticeBlock)]
        framed = (*others[:first], "\n".join(blocks), *others[first:])
    linked = earlier.linked
    if linked is None and blocks:
        linked = len(earlier.lines) + first
    return PictureLines(earlier.lines + tuple(framed), pictures, linked)


def log_link(url: str) -> str:
    """The words a reply uses to point the member at the log message holding what it summarises,
    "([see them in the log](url))": the one wording for every reply that does, so a manager meets
    it the same wherever a picture was drawn."""
    return f"([see them in the log]({url}))"


def drawn_notices_section(groups: Iterable[DrawnNotices]) -> str:
    """The section a reply ends with for the pictures its work drew with render notices: the
    heading "⚠️ **Image notices**", then each picture under its label, with the `log_link` to the
    log message holding them where there is one, and its grouped notices beneath. A picture's label
    may carry bold of its own (the log line bolds the division's name in it); the section bolds the
    whole label, and a bold inside one would end it early and leave stray asterisks, so the label
    is flattened first and the log line is left as it was. "" where *groups* is empty, so a caller
    adds it and its separating blank line only when there is something to say (Constitution XIV.4:
    a command's output carries the notices of what it drew)."""
    lines: list[str] = []
    for group in groups:
        label = group.label.replace("**", "")
        lines.append(f"  **{label}**" + (f" {log_link(group.link)}" if group.link else ""))
        lines.extend(f"  {each}" for each in group.notices)
    return "\n".join(["⚠️ **Image notices**", *lines]) if lines else ""


async def record_abandoned(
    bot: Any,
    member: int | discord.abc.User | None,
    *,
    what: str,
    lapsed: bool,
    detail: str | None = None,
) -> None:
    """Record that *what* was cancelled by *member*, or lapsed unconfirmed, having been started
    by them.

    *member* is the member who pressed or started it: the interaction's own member where there
    is one, named by the display name it carries, or their id — a scheduled lapse has only
    that — found on the league's server (`name_of_member`). *detail*, where given, goes beneath
    the line: what became of the change and what to do next. Never raises.
    """
    try:
        named = await _named(bot, member)
        line = abandoned_line(named, what, lapsed=lapsed, detail=detail)
        await bot.output_router.post_log(line)
    except Exception:  # noqa: BLE001 — the confirmation has already ended
        log.warning("could not record in the log channel that %s ended", what, exc_info=True)
