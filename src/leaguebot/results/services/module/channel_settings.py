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

**The save writes the division's column and the audit entry with the division, together**, the
audit entry stamped by the queue's clock (`division_channels` holds the upserts); the column it
replaces is read in the save, never at the ask, so a change waiting behind another for the same
division records what that one left, and its reply reads "set" or "updated" by it.

**Each overtakes a stopped job on the league lane** (`Setting.repairs_the_bot`): a stop notice for
a channel gone tells the league to set the channel and press Retry, which a change waiting behind
the stop could never do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.results.models.switch import SWITCH_KEY
from leaguebot.framework.models.change import AuditRecord, Verdict
from leaguebot.framework.services.queue.change_types import CheckContext
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    find_channel_use,
    refusal,
    unusable_refusal,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.framework.services.queue.setting_change import Saved, Setting
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.results.services.module.division_channels import (
    channel_needs,
    set_division_penalty_channel,
    set_division_results_channel,
    set_division_standings_channel,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.image.services.render.image_drawing import ImageDrawing
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.framework.utils.hooks import AwaitedHook


RESULTS_CHANNEL = "results.setting.results_channel"
STANDINGS_CHANNEL = "results.setting.standings_channel"
VERDICTS_CHANNEL = "results.setting.verdicts_channel"

NOT_ENABLED = "❌ The Results & Standings module is not enabled."


def verdicts_division_not_found(name: str) -> str:
    """Why a division *name* the manager typed is refused by `/results channel verdicts`."""
    return f'❌ Division "{name}" not found.'


def _channel_setting(
    *,
    kind: str,
    key: str,
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
    its replies use), *write* its column's upsert, *audit* the entry of the channel moving from
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
        )

    def doing(payload: dict[str, Any]) -> str:
        return (
            f"Setting the {key} channel for **{payload['division_name']}** to "
            f"<#{int(payload['value'])}>"
        )

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
        return said(verb, name, int(payload["value"]), payload["channel_name"])

    return Setting(
        kind=kind,
        command=lambda _payload: f"/results channel {key}",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("division_results_config",),
        repairs_the_bot=True,
    )


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
            kind=RESULTS_CHANNEL, key="results", said=_mentioned("Results"),
            write=set_division_results_channel, not_found=division_not_found,
            audit=typed_channel_audit("results"), modules=modules,
            seasons=seasons,
            channel_uses_read=channel_uses_read, drawing=drawing,
        ),
        _channel_setting(
            kind=STANDINGS_CHANNEL, key="standings", said=_mentioned("Standings"),
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
    "NOT_ENABLED", "RESULTS_CHANNEL", "STANDINGS_CHANNEL", "VERDICTS_CHANNEL",
    "results_channel_settings", "typed_channel_audit", "verdicts_channel_audit",
    "verdicts_division_not_found",
]
