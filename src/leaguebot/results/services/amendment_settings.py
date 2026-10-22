"""The amendment mode as settings on the change queue (#439, slice 7).

Each `/results amend …` command that turns the mode on or off, discards what is staged or stages
a value asks one `results.setting.…` change, built from a `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault). The check of each is the command's, in today's order and words,
run at the ask and again as the change starts, so a mode turned off, a season that moved on or an
approval taken in hand by a change ahead of it on the queue is taken into account. Every check
refuses first where the results module is off, then where the live season is not one the command
may act on (`stage_refusal`), is no longer the season asked about, or has its points amendment
being approved (`staging_refusal`: nothing outside the approval may put back or undo what it
approves).

**What a change carries.** Every payload is plain JSON, as the queue stores it, and names the
season it was asked about (`season_id`). A session is its `SessionType` value. The points of a
paste are `pairs`, `[[position, points], …]` in the order parsed, with the `notes` its repeated
positions earned; a fastest-lap change carries the one column it sets, `fl_points` or
`fl_position_limit`, and is named by it.

**The ordering warning is judged in the save,** on the table the change has just left staged, and
never at the ask: a change ahead may have moved the table.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import Verdict
from leaguebot.core.models.season import SeasonStatus
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import NOTHING_SAVED, SAVE, Saved, Setting
from leaguebot.core.utils.season_gate import stage_refusal
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import amendment_mode_service
from leaguebot.results.services.points_amendment_change import approval_in_hand, staging_refusal
from leaguebot.results.services.points_config_service import (
    BLOCKS_AMENDMENT,
    STANDS_BLOCKS_AMENDMENT,
    ordering_notice,
)
from leaguebot.results.services.points_settings import MODULE_OFF

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

AMEND_MODE = "results.setting.amend_mode"
AMEND_REVERT = "results.setting.amend_revert"
AMEND_POINTS = "results.setting.amend_points"
AMEND_FASTEST_LAP = "results.setting.amend_fastest_lap"

MODE_NOT_ACTIVE = "❌ Amendment mode is not active."
NOT_THE_SEASON_ASKED = (
    "❌ The season this was asked for is no longer the current one, so nothing was changed."
)
IN_SETUP = (
    "❌ `/results amend toggle` amends the points of a season under way. A season still being "
    "set up takes its points from the configurations attached to it: change those with "
    "`/results config`."
)
UNCOMMITTED = (
    "❌ Cannot disable amendment mode — uncommitted changes exist. "
    "Use `/results amend revert` to discard or `/results amend review` to apply."
)

#: What a fault in a paste's save tells its member: nothing was saved, and how to retry. The
#: other settings take the generic words.
_PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."


def _nothing_changed(reply: str) -> str:
    return f"ℹ️ {reply}"


def _session(payload: dict[str, Any]) -> SessionType:
    return SessionType(str(payload["session"]))


async def _gate(
    ctx: CheckContext, modules: "ModuleService", seasons: "SeasonService", command: str
) -> Verdict | None:
    """The refusal common to every amend command, or None where the change may go on.

    The module, then the live season's stage in the command's own words, then that it is still
    the season asked about, then that no approval of its points is in hand.
    """
    if not await modules.is_results_enabled():
        return Verdict.refuse(MODULE_OFF)
    season = await seasons.get_setup_or_active_season()
    if season is None:
        return Verdict.refuse(str(stage_refusal(None, command)))
    refusal = stage_refusal(season.stage, command)
    if refusal is not None:
        return Verdict.refuse(refusal)
    if season.id != int(ctx.payload["season_id"]):
        return Verdict.refuse(NOT_THE_SEASON_ASKED)
    job = await approval_in_hand(ctx.db_path, season.id)
    if job is not None:
        return Verdict.refuse(staging_refusal(season.season_number, job))
    return None


async def _mode_off(ctx: CheckContext) -> bool:
    """Whether the season of the request is not in amendment mode."""
    state = await amendment_mode_service.get_amendment_state(
        ctx.db_path, int(ctx.payload["season_id"])
    )
    return state is None or not state.amendment_active


def _amend_mode(modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`/results amend toggle`: the mode turned on or off, as the flip was resolved when asked.

    It is refused while the season is still in setup, which takes its points from the
    configurations attached to it. Turning it off is refused while changes are staged. A mode found already in the state asked
    for, where two presses raced, changes nothing and says so.
    """

    def command(payload: dict[str, Any]) -> str:
        return "/results amend toggle"

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await _gate(ctx, modules, seasons, "results amend toggle")
        if refusal is not None:
            return refusal
        season = await seasons.get_setup_or_active_season()
        if season is not None and season.status is SeasonStatus.SETUP:
            return Verdict.refuse(IN_SETUP)
        if not ctx.payload["active"]:
            state = await amendment_mode_service.get_amendment_state(
                ctx.db_path, int(ctx.payload["season_id"])
            )
            if state is not None and state.modified_flag:
                return Verdict.refuse(UNCOMMITTED)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        active = bool(payload["active"])
        saved = await amendment_mode_service.set_amendment_mode_on(
            db, int(payload["season_id"]), active
        )
        return dataclasses.replace(
            saved, details=(f"amendment_mode: {'enabled' if active else 'disabled'}",)
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        active = bool(payload["active"])
        if not results[SAVE]["changed"]:
            return _nothing_changed(
                f"Nothing changed: amendment mode is already **{'on' if active else 'off'}**."
            )
        if active:
            return "✅ Amendment mode enabled. Modification store initialised."
        return "✅ Amendment mode disabled."

    return Setting(
        kind=AMEND_MODE,
        command=command,
        doing=lambda payload: f"Turning amendment mode {'on' if payload['active'] else 'off'}",
        check=check,
        save=save,
        reply=reply,
        writes=(
            "season_amendment_state", "season_modification_entries", "season_modification_fl",
        ),
    )


def _amend_revert(modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`/results amend revert`: the modification store put back to the season's points."""

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await _gate(ctx, modules, seasons, "results amend revert")
        if refusal is not None:
            return refusal
        if await _mode_off(ctx):
            return Verdict.refuse(MODE_NOT_ACTIVE)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        saved = await amendment_mode_service.revert_on(db, int(payload["season_id"]))
        if not saved.changed:
            return dataclasses.replace(saved, details=("reason: nothing is staged",))
        return saved

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        if not results[SAVE]["changed"]:
            return _nothing_changed(
                "Nothing changed: nothing is staged, so the modification store already "
                "matches the season's points."
            )
        return "✅ Modification store reverted to current season points."

    return Setting(
        kind=AMEND_REVERT,
        command=lambda _payload: "/results amend revert",
        doing=lambda _payload: "Reverting the modification store",
        check=check,
        save=save,
        reply=reply,
        writes=(
            "season_amendment_state", "season_modification_entries", "season_modification_fl",
        ),
    )


def _amend_points(modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`/results amend session` (one position) and `/results amend bulk-session` (a paste).

    A single position already staged at the value asked is "Nothing changed". A paste is all or
    nothing, and its line counts the positions pasted, as the command's always has.
    """

    def command(payload: dict[str, Any]) -> str:
        return (
            "/results amend bulk-session" if payload.get("bulk") else "/results amend session"
        )

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await _gate(ctx, modules, seasons, command(ctx.payload).lstrip("/"))
        if refusal is not None:
            return refusal
        if await _mode_off(ctx):
            return Verdict.refuse(MODE_NOT_ACTIVE)
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        session = _session(payload)
        name = str(payload["config"])
        season_id = int(payload["season_id"])
        pairs = [(int(position), int(points)) for position, points in payload["pairs"]]
        saved = await amendment_mode_service.stage_points_on(
            db, season_id, name, session.value, pairs
        )
        warnings = await amendment_mode_service.modification_ordering_warnings_on(
            db, season_id, name, session.value
        )
        result = {**saved.result, "warnings": warnings}
        if payload.get("bulk"):
            values = tuple(f"P{position} → {points} pts" for position, points in pairs)
            return dataclasses.replace(
                saved,
                result=result,
                # A paste of values all staged already writes nothing, and is recorded as that.
                outcome=f"{len(pairs)} change(s)" if saved.changed else None,
                details=(f"config: {name}, session: {session.label()}", *values),
            )
        (position, points), = pairs
        return dataclasses.replace(
            saved,
            result=result,
            details=(
                f"config: {name}",
                f"session: {session.label()}, position: {position}, points: {points}",
            ),
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results[SAVE]
        name = str(payload["config"])
        label = _session(payload).label()
        stands = not saved["changed"]
        if payload.get("bulk"):
            if stands:
                return _nothing_changed(
                    f"Nothing changed: every position pasted already stands in config **{name}**."
                )
            lines = [
                f"✅ Amended in modification store for config **{name}** ({label}):\n"
                + "\n".join(
                    f"  P{position} → {points} pts" for position, points in payload["pairs"]
                )
            ]
            if payload.get("notes"):
                lines.append(
                    "⚠️ Notes:\n" + "\n".join(f"  • {n}" for n in payload["notes"])
                )
            notice = ordering_notice(name, label, saved["warnings"], BLOCKS_AMENDMENT)
            if notice:
                lines.append(notice.lstrip("\n"))
            return "\n".join(lines)
        (position, points), = payload["pairs"]
        notice = ordering_notice(
            name, label, saved["warnings"],
            STANDS_BLOCKS_AMENDMENT if stands else BLOCKS_AMENDMENT,
        )
        if stands:
            return _nothing_changed(
                f"Nothing changed: **{name}** {label} P{position} already stands at "
                f"{points} pts in the modification store." + notice
            )
        return (
            f"✅ Updated in modification store: **{name}** {label} P{position} "
            f"→ {points} pts." + notice
        )

    def doing(payload: dict[str, Any]) -> str:
        name = str(payload["config"])
        label = _session(payload).label()
        if payload.get("bulk"):
            return f"Staging {len(payload['pairs'])} position(s) of **{name}** {label}"
        (position, points), = payload["pairs"]
        return f"Staging **{name}** {label} P{position} → {points} pts"

    return Setting(
        kind=AMEND_POINTS,
        command=command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("season_amendment_state", "season_modification_entries"),
        not_saved=lambda payload: _PASTE_NOT_SAVED if payload.get("bulk") else NOTHING_SAVED,
    )


def _amend_fastest_lap(modules: "ModuleService", seasons: "SeasonService") -> Setting:
    """`/results amend fl` (the bonus) and `/results amend fl-plimit` (the position limit)."""

    def column(payload: dict[str, Any]) -> str:
        return "fl_points" if "fl_points" in payload else "fl_position_limit"

    def command(payload: dict[str, Any]) -> str:
        return "/results amend fl" if column(payload) == "fl_points" else "/results amend fl-plimit"

    async def check(ctx: CheckContext) -> Verdict:
        refusal = await _gate(ctx, modules, seasons, command(ctx.payload).lstrip("/"))
        if refusal is not None:
            return refusal
        if await _mode_off(ctx):
            return Verdict.refuse(MODE_NOT_ACTIVE)
        if _session(ctx.payload).is_qualifying:
            if column(ctx.payload) == "fl_points":
                return Verdict.refuse(
                    "❌ Fastest-lap bonus cannot be set for qualifying sessions."
                )
            return Verdict.refuse("❌ Position limit cannot be set for qualifying sessions.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        session = _session(payload)
        name = str(payload["config"])
        season_id = int(payload["season_id"])
        if column(payload) == "fl_points":
            saved = await amendment_mode_service.stage_fastest_lap_on(
                db, season_id, name, session.value, fl_points=int(payload["fl_points"])
            )
            value = f"fl_bonus: {payload['fl_points']}"
        else:
            saved = await amendment_mode_service.stage_fastest_lap_on(
                db, season_id, name, session.value,
                fl_position_limit=int(payload["fl_position_limit"]),
            )
            value = f"fl_position_limit: {payload['fl_position_limit']}"
        return dataclasses.replace(
            saved, details=(f"config: {name}", f"session: {session.label()}, {value}")
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        name = str(payload["config"])
        label = _session(payload).label()
        stands = not results[SAVE]["changed"]
        if column(payload) == "fl_points":
            points = payload["fl_points"]
            if stands:
                return _nothing_changed(
                    f"Nothing changed: **{name}** {label} FL bonus already stands at "
                    f"{points} pts in the modification store."
                )
            return (
                f"✅ Updated in modification store: **{name}** {label} FL bonus "
                f"→ {points} pts."
            )
        limit = payload["fl_position_limit"]
        if stands:
            return _nothing_changed(
                f"Nothing changed: **{name}** {label} FL position limit already stands at "
                f"top {limit} in the modification store."
            )
        return (
            f"✅ Updated in modification store: **{name}** {label} FL position limit "
            f"→ top {limit}."
        )

    def doing(payload: dict[str, Any]) -> str:
        name = str(payload["config"])
        label = _session(payload).label()
        if column(payload) == "fl_points":
            return f"Staging **{name}** {label} FL bonus → {payload['fl_points']} pts"
        return f"Staging **{name}** {label} FL position limit → top {payload['fl_position_limit']}"

    return Setting(
        kind=AMEND_FASTEST_LAP,
        command=command,
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("season_amendment_state", "season_modification_fl"),
    )


def amendment_settings(
    *, modules: "ModuleService", seasons: "SeasonService"
) -> tuple[Setting, ...]:
    """The amendment mode's settings: the services are read through those handed in."""
    return (
        _amend_mode(modules, seasons),
        _amend_revert(modules, seasons),
        _amend_points(modules, seasons),
        _amend_fastest_lap(modules, seasons),
    )


__all__ = [
    "AMEND_FASTEST_LAP", "AMEND_MODE", "AMEND_POINTS", "AMEND_REVERT", "MODE_NOT_ACTIVE",
    "amendment_settings",
]
