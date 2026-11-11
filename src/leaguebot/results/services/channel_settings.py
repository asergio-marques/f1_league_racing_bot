"""A division's results, standings and verdicts channels, as settings on the change queue.

`/results channel results`, `standings` and `verdicts` each ask one change built from a `Setting`
below by `setting_change` (`core/services/queue/setting_change.py`, which holds what every settings
change does with a value already held and with a fault), `{"division_id", "division_name",
"value", "channel_name"}`, for the division by its id: the command finds it from the name a manager
typed and carries that name for the reply and the line, so a division renamed while the change
waits still gets its channel.

**The refusals are the check, in the command's order and words**, made at the ask and again as the
change starts, since a change ahead of it may have switched results off, ended the season, deleted
the division or given the channel to another job: the module on; for verdicts, a channel the bot
may post in (read from the channel as the bot finds it by its id, so a channel gone or one the bot
has lost its permission in is refused as the command refuses it); a season live; the division in
it; then the channel free of any other job (`channel_registry_service`), the channel this very
division already has being refused as "Nothing was changed". Verdicts keeps wordings of its own
for the division not found and for its reply.

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

from leaguebot.core.models.change import AuditRecord, Verdict
from leaguebot.core.services.queue.change_types import CheckContext
from leaguebot.core.services.channel_registry_service import (
    ChannelUse,
    as_text_channel,
    find_channel_use,
    refusal,
)
from leaguebot.core.services.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.services.queue.setting_change import Saved, Setting
from leaguebot.results.services.division_channels import (
    set_division_penalty_channel,
    set_division_results_channel,
    set_division_standings_channel,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.seasons.season_service import SeasonService


RESULTS_CHANNEL = "results.setting.results_channel"
STANDINGS_CHANNEL = "results.setting.standings_channel"
VERDICTS_CHANNEL = "results.setting.verdicts_channel"

NOT_ENABLED = "❌ The Results & Standings module is not enabled."
NO_ACCESS = "❌ Cannot access that channel. Ensure the bot has permission to post there."


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
    channel_of: Callable[[int], object] | None,
) -> Setting:
    """The setting of the *key* channel (the channel registry's key and the word the command and
    its replies use), *write* its column's upsert, *audit* the entry of the channel moving from
    one to another in a division, *said* the reply's words for a verb, the division as typed, the
    channel's id and its name. *channel_of*, verdicts' alone, finds a channel by its id, for the
    bot's access to it to be judged."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        value = int(payload["value"])
        if not await modules.is_results_enabled():
            return Verdict.refuse(NOT_ENABLED)
        if channel_of is not None and not _may_post(channel_of(value)):
            return Verdict.refuse(NO_ACCESS)
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.refuse(NO_SEASON_REFUSAL)
        division = await seasons.get_division(int(payload["division_id"]))
        if division is None or division.season_id != season.id:
            return Verdict.refuse(not_found(payload["division_name"]))
        use = await find_channel_use(
            ctx.db_path, value, channel_uses_read=ctx.bot.channel_uses_read
        )
        if use is not None:
            return Verdict.refuse(
                refusal(f"<#{value}>", use, same_setting=(use == ChannelUse(key, division.name)))
            )
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


def _may_post(channel: object) -> bool:
    """Whether the bot finds *channel* and may post in it."""
    found = as_text_channel(channel)
    return found is not None and found.permissions_for(found.guild.me).send_messages


def _typed(channel_type: str) -> Callable[[int | None, int, int], AuditRecord]:
    """The audit entry of a results or standings channel moving, which names its type."""

    def audit(old: int | None, new: int, division_id: int) -> AuditRecord:
        return AuditRecord(
            "DIVISION_CHANNEL_SET",
            {"channel_type": channel_type, "channel_id": old},
            {"channel_type": channel_type, "channel_id": new},
            division_id,
        )

    return audit


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
    channel_of: Callable[[int], object],
) -> tuple[Setting, ...]:
    """The results channel's setting, the standings channel's and the verdicts channel's: the
    module and the season are read through the services handed in, and the channel the bot finds
    by its id through *channel_of*."""
    return (
        _channel_setting(
            kind=RESULTS_CHANNEL, key="results", said=_mentioned("Results"),
            write=set_division_results_channel, not_found=division_not_found,
            audit=_typed("results"), channel_of=None, modules=modules, seasons=seasons,
        ),
        _channel_setting(
            kind=STANDINGS_CHANNEL, key="standings", said=_mentioned("Standings"),
            write=set_division_standings_channel, not_found=division_not_found,
            audit=_typed("standings"), channel_of=None, modules=modules, seasons=seasons,
        ),
        _channel_setting(
            kind=VERDICTS_CHANNEL, key="verdicts", said=_named,
            write=set_division_penalty_channel, not_found=verdicts_division_not_found,
            audit=lambda old, new, division_id: AuditRecord(
                "VERDICTS_CHANNEL_SET", {"channel_id": old}, {"channel_id": new}, division_id
            ),
            channel_of=channel_of, modules=modules, seasons=seasons,
        ),
    )


__all__ = [
    "NOT_ENABLED", "NO_ACCESS", "RESULTS_CHANNEL", "STANDINGS_CHANNEL", "VERDICTS_CHANNEL",
    "results_channel_settings", "verdicts_division_not_found",
]
