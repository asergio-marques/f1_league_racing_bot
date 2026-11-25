"""A division's results, standings and verdicts channels, as settings on the change queue.

`/results channel results`, `standings` and `verdicts` each ask one change built from a `Setting`
below by `setting_change` (`framework/services/queue/setting_change.py`, which holds what every settings
change does with a value already held and with a fault), `{"division_id", "division_name",
"value", "channel_name"}`, for the division by its id: the command finds it from the name a manager
typed and carries that name for the reply and the line, so a division renamed while the change
waits still gets its channel.

**The refusals are the check, in the command's order and words**, made at the ask and again as the
change starts, since a change ahead of it may have switched results off, ended the season, deleted
the division or given the channel to another job: the module on; a season live; the division in
it; the channel free of any other job (`channel_registry_service`), the channel this very division
already has being refused as "Nothing was changed"; and last the channel one the bot has the
permissions the setting's channel needs in (`division_channels.channel_needs`, with the *drawing*
the builder is handed, judged by `unusable_refusal` against the league's server as it stands when
the check runs). Verdicts keeps wordings of its own for the division not found and for its reply.

**A channel that still stands is never moved away from while its posts could not be carried.** The
results and the standings channel each refuse, last of the refusals above, a move away from a
channel the bot finds on the server and can use while an amendment of one of the division's rounds
is open (its corrections are not approved, and the posts to carry would publish them) or the season
is pending completion (the sync commands are refused there). A first set, and the replacement of a
channel that is gone or that the bot lacks a permission in, is a repair and is never refused: its old
posts went with it or cannot be reached. The verdicts channel carries nothing and has no such
refusal. A channel the bot cannot judge (no server to ask) is taken not to stand, as the check
stands aside everywhere else.

**The save writes the division's column and the audit entry with the division, together**, the
audit entry stamped by the queue's clock (`division_channels` holds the upserts); the column it
replaces is read in the save, never at the ask, so a change waiting behind another for the same
division records what that one left, and its reply reads "set" or "updated" by it.

**A save that replaced a channel asks the carry where the posts stand in a channel that stands.**
The results and the standings channel each plan an `After` step, `ask_carry`, where the save
replaced a channel (never a first set). It finds the channel the posts stand in (the channel moved
away from, or, where a carry still waiting was to move the posts there, the channel that carry
takes them from) and that it stands, which the check, testing only the channel replaced, did not
settle; it finds too that the sync's own plan (`plan_division_posts`) has posts to move, and
only then asks `results.channel.carry` (`publishing/channel_move`) as the bot's own follow-on, whose
job it is to move the tables or standings already up across, as a sync would; the setting's reply
then says so. The step asks and does nothing else, so the setting never stops the queue. A channel
that has gone since the save, whose posts went with it, a division with no posts up, and a channel
given back to the one the posts stand in (a move away and back before the carry ran) ask nothing.

**Each overtakes a stopped job on the league lane** (`Setting.repairs_the_bot`): a stop notice for
a channel gone tells the league to set the channel and press Retry, which a change waiting behind
the stop could never do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.results.models.switch import SWITCH_KEY
from leaguebot.core.models.channel_needs import Needs
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.seasons.approval_checks import PresenceState, judged_presence
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    AuditRecord,
    FollowOn,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.framework.services.queue.change_types import CheckContext, Step, StepContext
from leaguebot.framework.services.queue.queue_records import unfinished
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
    unusable_refusal,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.framework.services.queue.setting_change import SAVE, After, Saved, Setting
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.results.services.module.division_channels import (
    channel_needs,
    set_division_penalty_channel,
    set_division_results_channel,
    set_division_standings_channel,
)

from leaguebot.results.services.review.review_posting import plan_division_posts
from leaguebot.results.services.round_amendment.amendment_writes import (
    amendment_wait_text,
    open_amendment_in_division,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.image.services.render.image_drawing import ImageDrawing
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.framework.utils.hooks import AwaitedHook
    from leaguebot.framework.utils.league_bot import LeagueBot


RESULTS_CHANNEL = "results.setting.results_channel"
STANDINGS_CHANNEL = "results.setting.standings_channel"
VERDICTS_CHANNEL = "results.setting.verdicts_channel"

ASK_CARRY = "ask_carry"
#: The bot's own change that carries a moved channel's posts (`publishing/channel_move`), named
#: here, where it is asked, so that the module that runs it can import what it needs from here.
CHANNEL_CARRY = "results.channel.carry"

NOT_ENABLED = "❌ The Results & Standings module is not enabled."

#: The sync command that moves each kind of post, named where the step that asks the carry faults.
_SYNCS = {"results": "/results rounds sync", "standings": "/results standings sync"}


def carry_what(payload: dict[str, Any]) -> str:
    """What the carry of *payload* is called in the log: the posts, the division and both channels."""
    return (
        f"Carrying the {payload['which']} of **{payload['division_name']}** from "
        f"<#{payload['old_channel_id']}> to <#{payload['new_channel_id']}>"
    )


def verdicts_division_not_found(name: str) -> str:
    """Why a division *name* the manager typed is refused by `/results channel verdicts`."""
    return f'❌ Division "{name}" not found.'


async def held_channel(db_path: str, division_id: int, column: str) -> int | None:
    """The channel the division's *column* holds now, or None where it holds none."""
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            f"SELECT {column} FROM division_results_config "  # noqa: S608 — a column of ours
            "WHERE division_id = ?", (division_id,),
        )).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def _amended_refusal(key: str, name: str, held: int, amendment: dict[str, Any]) -> str:
    """Why the *key* channel of *name* cannot be moved away from *held* while *amendment* is open."""
    return (
        f"⏸️ Round {amendment['round_number']} of **{name}** is being amended in "
        f"<#{amendment['channel_id']}>, and its corrections are not approved yet, so the {key} "
        f"channel cannot be moved away from <#{held}> until that ends — {amendment_wait_text()}. "
        "Nothing was changed."
    )


def _pending_completion_refusal(key: str, name: str, held: int) -> str:
    """Why the *key* channel of *name* cannot be moved away from *held* in Pending completion."""
    return (
        f"❌ The season is pending completion, so the {key} channel of **{name}** cannot be moved "
        f"away from <#{held}> until the season is completed. Nothing was changed."
    )


def _channel_setting(
    *,
    kind: str,
    key: str,
    held_column: str | None = None,
    write: Callable[["aiosqlite.Connection", int, int | None], Awaitable[int | None]],
    not_found: Callable[[str], str],
    audit: Callable[[int | None, int, int], AuditRecord],
    said: Callable[[str, str, int, str], str],
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_uses_read: "AwaitedHook",
    drawing: "ImageDrawing | None",
) -> Setting:
    """The setting of the *key* channel (the channel registry's key and the word the command and
    its replies use), *held_column* the column holding it where the setting moves posts that stand
    in it (results, standings) and None where it carries none (verdicts), *write* its column's upsert, *audit* the entry of the channel moving from
    one to another in a division, *said* the reply's words for a verb, the division as typed, the
    channel's id and its name. *channel_uses_read* is core's hook that names the module holding a
    channel, and *drawing* the image module's, which says whether the channel's graphic could be a
    picture attaching a file."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        value = int(payload["value"])
        if not await modules.is_enabled(SWITCH_KEY):
            return Verdict.refuse(NOT_ENABLED)
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(not_found(payload["division_name"]))
        use = await find_channel_use(ctx.db_path, value, channel_uses_read=channel_uses_read)
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{value}>", use, same_setting=(use == ChannelUse(key, division.name)))
            )
        unusable = await unusable_refusal(
            await league_guild(ctx.bot),
            value,
            (await channel_needs(drawing))[key],
            ChannelUse(key, division.name),
        )
        if unusable is not None:
            return Verdict.refuse(unusable)
        if held_column is not None:
            held = await held_channel(ctx.db_path, division.id, held_column)
            if held is not None and await _stands(ctx.bot, held, (await channel_needs(drawing))[key]):
                amendment = await open_amendment_in_division(ctx.db_path, division.id)
                if amendment is not None:
                    return Verdict.refuse(_amended_refusal(key, division.name, held, amendment))
                if season.stage == SeasonStage.PENDING_COMPLETION:
                    return Verdict.refuse(_pending_completion_refusal(key, division.name, held))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = int(payload["value"])
        division_id = int(payload["division_id"])
        details = (f"division: {payload['division_name']}", f"channel: #{payload['channel_name']}")
        old_value = await write(db, division_id, value)
        if old_value == value:
            return Saved(changed=False, details=details)
        return Saved(
            changed=True,
            audits=(audit(old_value, value, division_id),),
            details=details,
            result={"replaced": old_value},
            then=(
                (PlannedStep(ASK_CARRY),)
                if held_column is not None and old_value is not None else ()
            ),
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Setting the {key} channel for **{payload['division_name']}** to "
            f"<#{int(payload['value'])}>"
        )

    posts = "tables" if key == "results" else "standings"

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        mention = f"<#{int(payload['value'])}>"
        name = payload["division_name"]
        if not saved["changed"]:
            # `check` refuses a channel the division already holds before the save runs, at the
            # ask and as the change starts, with nothing able to write between; this guard
            # answers in the same words should the save ever find it so.
            return f"ℹ️ {mention} is already the {key} channel for **{name}**. Nothing was changed."
        verb = "set" if saved["replaced"] is None else "updated"
        said_so = said(verb, name, int(payload["value"]), payload["channel_name"])
        if results.get(ASK_CARRY, {}).get("carrying"):
            said_so += f" The {posts} already up are being moved across."
        return said_so

    async def ask_carry(ctx: StepContext) -> StepResult:
        old = next(
            (view.result or {}).get("replaced") for view in ctx.steps if view.name == SAVE
        )
        if old is None:
            return StepResult(result={"carrying": False})
        old = await _where_posts_stand(ctx.db_path, ctx.payload, key, int(old))
        if old == int(ctx.payload["value"]) or not await _stands(
            ctx.bot, old, (await channel_needs(drawing))[key]
        ):
            return StepResult(result={"carrying": False})
        async with get_connection(ctx.db_path) as db:
            up = await plan_division_posts(
                db, int(ctx.payload["division_id"]), results_only=key == "results",
                standings_only=key == "standings",
            )
        if not up:
            return StepResult(result={"carrying": False})
        carry = {
            "division_id": int(ctx.payload["division_id"]),
            "division_name": ctx.payload["division_name"],
            "which": key,
            "old_channel_id": int(old),
            "new_channel_id": int(ctx.payload["value"]),
        }
        return StepResult(
            result={"carrying": True}, follow_ons=(FollowOn(CHANNEL_CARRY, carry, carry_what(carry)),)
        )

    def carry_fault_reply(_payload: dict[str, Any], reply: str) -> str:
        # The step reads the server and the database before it asks, so it can fail, and then no
        # carry is asked: the reply says what was not done and the sync that does it.
        return f"{reply} The {posts} already up were not moved: run `{_SYNCS[key]}` to move them."

    return Setting(
        kind=kind,
        command=lambda _payload: f"/results channel {key}",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("division_results_config",),
        after=(
            {ASK_CARRY: After(Step(ASK_CARRY, StepKind.ACT, ask_carry), carry_fault_reply)}
            if held_column is not None else {}
        ),
        repairs_the_bot=True,
    )


async def _where_posts_stand(
    db_path: str, payload: dict[str, Any], which: str, replaced: int
) -> int:
    """The channel the posts stand in, as far as the queue shows: where *replaced* is the channel an
    earlier carry of this division's *which* is still waiting to move them to, that carry's old
    channel, since nothing has gone up in *replaced*; otherwise *replaced* itself."""
    standing = replaced
    for waiting in await unfinished(db_path, [CHANNEL_CARRY]):
        if (
            int(waiting["division_id"]) == int(payload["division_id"])
            and waiting["which"] == which and int(waiting["new_channel_id"]) == standing
        ):
            standing = int(waiting["old_channel_id"])
    return standing


async def _stands(bot: "LeagueBot", channel_id: int, needs: Needs) -> bool:
    """Whether *channel_id* is on the league's server and the bot can use it as *needs* asks, as
    Discord proves it; False where there is no server to ask."""
    guild = await league_guild(bot)
    if guild is None:
        return False
    return (await judged_presence(guild, channel_id, needs)).state is PresenceState.PRESENT


def typed_channel_audit(channel_type: str) -> Callable[[int | None, int | None, int], AuditRecord]:
    """The audit entry of a results or standings channel moving, which names its type: set here,
    and cleared by the switch-on (*new* None) where another setting took it."""

    def audit(old: int | None, new: int | None, division_id: int) -> AuditRecord:
        return AuditRecord(
            "DIVISION_CHANNEL_SET",
            {"channel_type": channel_type, "channel_id": old},
            {"channel_type": channel_type, "channel_id": new},
            division_id,
        )

    return audit


def verdicts_channel_audit(old: int | None, new: int | None, division_id: int) -> AuditRecord:
    """The audit entry of a verdicts channel moving, which names only the id: set here, and
    cleared by the switch-on (*new* None) where another setting took it."""
    return AuditRecord(
        "VERDICTS_CHANNEL_SET", {"channel_id": old}, {"channel_id": new}, division_id
    )


def _mentioned(label: str) -> Callable[[str, str, int, str], str]:
    """The reply of a results or standings channel saved: the division in bold, the channel by
    its mention."""

    def said(verb: str, name: str, channel_id: int, _channel_name: str) -> str:
        return f"✅ {label} channel for **{name}** {verb} to <#{channel_id}>."

    return said


def _named(verb: str, name: str, _channel_id: int, channel_name: str) -> str:
    """The reply of a verdicts channel saved: the division plain, the channel by its name."""
    return f"✅ Verdicts channel for {name} {verb} to #{channel_name}."


def results_channel_settings(
    *,
    modules: "ModuleService",
    seasons: "SeasonService",
    channel_uses_read: "AwaitedHook",
    drawing: "ImageDrawing | None",
) -> tuple[Setting, ...]:
    """The results channel's setting, the standings channel's and the verdicts channel's: the
    module and the season are read through the services handed in, the module holding a channel
    through *channel_uses_read*, and whether a graphic could be a picture through *drawing*."""
    return (
        _channel_setting(
            kind=RESULTS_CHANNEL, key="results", held_column="results_channel_id", said=_mentioned("Results"),
            write=set_division_results_channel, not_found=division_not_found,
            audit=typed_channel_audit("results"), modules=modules,
            seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
        _channel_setting(
            kind=STANDINGS_CHANNEL, key="standings", held_column="standings_channel_id", said=_mentioned("Standings"),
            write=set_division_standings_channel, not_found=division_not_found,
            audit=typed_channel_audit("standings"), modules=modules,
            seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
        _channel_setting(
            kind=VERDICTS_CHANNEL, key="verdicts", said=_named,
            write=set_division_penalty_channel, not_found=verdicts_division_not_found,
            audit=verdicts_channel_audit,
            modules=modules, seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
    )


__all__ = [
    "ASK_CARRY", "CHANNEL_CARRY", "carry_what", "held_channel", "NOT_ENABLED", "RESULTS_CHANNEL", "STANDINGS_CHANNEL", "VERDICTS_CHANNEL",
    "results_channel_settings", "typed_channel_audit", "verdicts_channel_audit",
    "verdicts_division_not_found",
]
