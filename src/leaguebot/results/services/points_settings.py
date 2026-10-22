"""The points configurations as settings on the change queue (#439, slice 7).

Each `/results config …` command that changes the league's points configurations asks one
`results.setting.…` change, built from a `Setting` below by `setting_change`
(`core/services/setting_change.py`, which holds what every settings change does with a value
already held and with a fault). The check of each is the command's, in today's order and words,
run at the ask and again as the change starts, so a configuration removed, a module turned off or
a season taken up by a change ahead of it on the queue is taken into account. Every check refuses
first where the results module is off.

A name typed is held to the rules every name is held to in the command, at once, before it is
asked (`NAME.check`); a change's check judges only the state.

**What a change carries.** Every payload is plain JSON, as the queue stores it. A session is its
`SessionType` value. The points of a paste are `pairs`, `[[position, points], …]` in the order
parsed, with the `notes` its repeated positions earned; the points of an import are `positions`,
`{session: [[position, points], …]}`, `fastest_laps`, `{session: [bonus, limit or None]}` and
the parser's `warnings`. A fastest-lap change carries the one column it sets, `fl_points` or
`fl_position_limit`, and is named by it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import Verdict
from leaguebot.core.services.change_queue import CheckContext
from leaguebot.core.services.setting_change import NOTHING_SAVED, SAVE, Saved, Setting
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import points_config_service
from leaguebot.results.services.points_config_service import (
    BLOCKS_APPROVAL,
    STANDS_BLOCKS_APPROVAL,
    ordering_notice,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

CONFIG_ADD = "results.setting.config_add"
CONFIG_REMOVE = "results.setting.config_remove"
CONFIG_POINTS = "results.setting.config_points"
CONFIG_FASTEST_LAP = "results.setting.config_fastest_lap"
CONFIG_IMPORT = "results.setting.config_import"

MODULE_OFF = "❌ The Results & Standings module is not enabled on this server."

#: What a fault in a paste's save, or in an import's, tells its member: nothing was saved, and how
#: to retry. The other settings take the generic words.
_PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
_IMPORT_NOT_SAVED = "Nothing from the import was saved. Run the import again to retry."


def _not_found(name: str) -> str:
    return f"❌ Config **{name}** not found."


def _config_add(modules: "ModuleService") -> Setting:
    """`/results config add`: a configuration, every position at nought points."""

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(f"❌ A config named **{name}** already exists on this server.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        return await points_config_service.create_config_on(db, str(payload["config"]))

    def reply(payload: dict[str, Any], _results: Any) -> str:
        return f"✅ Config **{payload['config']}** created. All positions default to 0 points."

    return Setting(
        kind=CONFIG_ADD,
        command=lambda _payload: "/results config add",
        doing=lambda payload: f"Adding points configuration **{payload['config']}**",
        check=check,
        save=save,
        reply=reply,
        writes=("points_config_store",),
    )


def _config_remove(modules: "ModuleService") -> Setting:
    """`/results config remove`, straight or by its confirmation button.

    A request not yet confirmed is refused where a season in setup now stands on the
    configuration: the command asks first when one does (`confirmed` False means none stood when
    it was asked), and a season that took the configuration up meanwhile has not been asked about.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if not await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(_not_found(name))
        if not ctx.payload.get("confirmed"):
            standing = await points_config_service.setup_seasons_linking(ctx.db_path, name)
            if standing:
                seasons = ", ".join(f"Season #{number}" for _, number in standing)
                return Verdict.refuse(
                    f"❌ **{name}** was attached to {seasons} since you asked, so it was not "
                    "removed. Run `/results config remove` again to be asked first."
                )
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        return await points_config_service.remove_config_on(db, str(payload["config"]))

    def reply(payload: dict[str, Any], _results: Any) -> str:
        return f"✅ Config **{payload['config']}** removed."

    return Setting(
        kind=CONFIG_REMOVE,
        command=lambda _payload: "/results config remove",
        doing=lambda payload: f"Removing points configuration **{payload['config']}**",
        check=check,
        save=save,
        reply=reply,
        # The deletion cascades to the configuration's positions and fastest laps, which the
        # queue's authoriser sees as writes.
        writes=(
            "points_config_store", "points_config_entries", "points_config_fl",
            "season_points_links",
        ),
    )


def _session(payload: dict[str, Any]) -> SessionType:
    return SessionType(str(payload["session"]))


def _nothing_changed(reply: str) -> str:
    return f"\u2139\ufe0f {reply}"


def _config_points(modules: "ModuleService") -> Setting:
    """`/results config session` (one position) and `/results config bulk-session` (a paste).

    A single position held already is "Nothing changed". A paste is all or nothing, and its
    line counts the positions pasted, as the command's always has; a paste whose every
    position is held writes no audit entry.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if not await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(_not_found(name))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        session = _session(payload)
        name = str(payload["config"])
        pairs = [(int(position), int(points)) for position, points in payload["pairs"]]
        saved = await points_config_service.config_points_on(db, name, session, pairs)
        if payload.get("bulk"):
            values = tuple(f"P{position} \u2192 {points} pts" for position, points in pairs)
            return dataclasses.replace(
                saved,
                outcome=f"{len(pairs)} change(s)",
                details=(f"config: {name}, session: {session.label()}", *values),
            )
        (position, points), = pairs
        return dataclasses.replace(
            saved,
            details=(
                f"config: {name}",
                f"session: {session.label()}, position: {position}, points: {points}",
            ),
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results[SAVE]
        session = _session(payload)
        name = str(payload["config"])
        label = session.label()
        stands = not saved["changed"]
        if payload.get("bulk"):
            lines = [
                f"\u2705 Applied to config **{name}** ({label}):\n"
                + "\n".join(
                    f"  P{position} \u2192 {points} pts" for position, points in payload["pairs"]
                )
            ]
            if payload.get("notes"):
                lines.append(
                    "\u26a0\ufe0f Notes:\n" + "\n".join(f"  \u2022 {n}" for n in payload["notes"])
                )
            notice = ordering_notice(name, label, saved["warnings"])
            if notice:
                lines.append(notice.lstrip("\n"))
            return "\n".join(lines)
        (position, points), = payload["pairs"]
        notice = ordering_notice(
            name, label, saved["warnings"], STANDS_BLOCKS_APPROVAL if stands else BLOCKS_APPROVAL
        )
        if stands:
            return _nothing_changed(
                f"Nothing changed: **{label}** position {position} already awards "
                f"{points} pts in config **{name}**." + notice
            )
        return f"\u2705 Set **{label}** position {position} \u2192 {points} pts in config **{name}**." + notice

    def doing(payload: dict[str, Any]) -> str:
        name = str(payload["config"])
        label = _session(payload).label()
        if payload.get("bulk"):
            return f"Setting {len(payload['pairs'])} position(s) of **{label}** in config **{name}**"
        (position, points), = payload["pairs"]
        return f"Setting **{label}** position {position} to {points} pts in config **{name}**"

    return Setting(
        kind=CONFIG_POINTS,
        command=lambda payload: (
            "/results config bulk-session" if payload.get("bulk") else "/results config session"
        ),
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("points_config_entries",),
        # A paste has its own words for a fault (the command's always had); a single position
        # takes the generic ones.
        not_saved=lambda payload: _PASTE_NOT_SAVED if payload.get("bulk") else NOTHING_SAVED,
    )


def _config_fastest_lap(modules: "ModuleService") -> Setting:
    """`/results config fl` (the bonus) and `/results config fl-plimit` (the position limit)."""

    def column(payload: dict[str, Any]) -> str:
        return "fl_points" if "fl_points" in payload else "fl_position_limit"

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if not await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(_not_found(name))
        if _session(ctx.payload).is_qualifying:
            if column(ctx.payload) == "fl_points":
                return Verdict.refuse(
                    "\u274c Fastest-lap bonus cannot be set for qualifying sessions."
                )
            return Verdict.refuse("\u274c Position limit cannot be set for qualifying sessions.")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        session = _session(payload)
        name = str(payload["config"])
        if column(payload) == "fl_points":
            saved = await points_config_service.config_fastest_lap_on(
                db, name, session, fl_points=int(payload["fl_points"])
            )
            value = f"fl_bonus: {payload['fl_points']}"
        else:
            saved = await points_config_service.config_fastest_lap_on(
                db, name, session, fl_position_limit=int(payload["fl_position_limit"])
            )
            value = f"fl_position_limit: {payload['fl_position_limit']}"
        return dataclasses.replace(
            saved, details=(f"config: {name}", f"session: {session.label()}, {value}")
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        label = _session(payload).label()
        name = str(payload["config"])
        stands = not results[SAVE]["changed"]
        if column(payload) == "fl_points":
            points = payload["fl_points"]
            if stands:
                return _nothing_changed(
                    f"Nothing changed: the fastest-lap bonus for **{label}** is already "
                    f"{points} pts in config **{name}**."
                )
            return (
                f"\u2705 Set fastest-lap bonus for **{label}** \u2192 {points} pts "
                f"in config **{name}**."
            )
        limit = payload["fl_position_limit"]
        if stands:
            return _nothing_changed(
                f"Nothing changed: the fastest-lap position limit for **{label}** is already "
                f"top {limit} in config **{name}**."
            )
        return (
            f"\u2705 Set fastest-lap position limit for **{label}** \u2192 top {limit} "
            f"eligible in config **{name}**."
        )

    def doing(payload: dict[str, Any]) -> str:
        label = _session(payload).label()
        name = str(payload["config"])
        if column(payload) == "fl_points":
            return (
                f"Setting the fastest-lap bonus for **{label}** to {payload['fl_points']} pts "
                f"in config **{name}**"
            )
        return (
            f"Setting the fastest-lap position limit for **{label}** to top "
            f"{payload['fl_position_limit']} in config **{name}**"
        )

    return Setting(
        kind=CONFIG_FASTEST_LAP,
        command=lambda payload: (
            "/results config fl" if column(payload) == "fl_points" else "/results config fl-plimit"
        ),
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("points_config_fl",),
    )


def _config_import(modules: "ModuleService") -> Setting:
    """`/results config xml-import`, from the form or a file: positions and fastest laps.

    The payload is what the cog parsed and found in order; the change only writes it. An import
    naming no session writes nothing and says so.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if not await modules.is_results_enabled():
            return Verdict.refuse(MODULE_OFF)
        name = str(ctx.payload["config"])
        if not await points_config_service.config_exists(ctx.db_path, name):
            return Verdict.refuse(_not_found(name))
        return Verdict.go()

    def value_lines(payload: dict[str, Any]) -> list[str]:
        """The values set, beneath the line, as "The record of what changed" asks."""
        lines = []
        for session, pairs in payload["positions"].items():
            lines.append(
                f"{SessionType(session).label()}: "
                + ", ".join(f"P{position} {points}" for position, points in sorted(pairs))
            )
        for session, (points, limit) in payload["fastest_laps"].items():
            limit_text = f", limit P{limit}" if limit is not None else ""
            lines.append(f"{SessionType(session).label()} fastest lap: {points} pts{limit_text}")
        return lines

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        name = str(payload["config"])
        positions = {
            SessionType(session): {int(position): int(points) for position, points in pairs}
            for session, pairs in payload["positions"].items()
        }
        fastest_laps = {
            SessionType(session): (int(points), None if limit is None else int(limit))
            for session, (points, limit) in payload["fastest_laps"].items()
        }
        saved = await points_config_service.xml_import_config_on(
            db, name, positions, fastest_laps
        )
        if not positions and not fastest_laps:
            # A file naming no session wrote nothing: recorded as that, never as a success.
            return dataclasses.replace(
                saved,
                outcome="Nothing changed",
                details=(f"config: {name}", "reason: the import named no session"),
            )
        return dataclasses.replace(
            saved, outcome="Success", details=(f"config: {name}", *value_lines(payload))
        )

    def reply(payload: dict[str, Any], _results: Any) -> str:
        lines: list[str] = []
        for session, pairs in payload["positions"].items():
            lines.append(f"  **{SessionType(session).label()}**: {len(pairs)} position(s) updated")
        for session, (points, limit) in payload["fastest_laps"].items():
            limit_text = f", limit P{limit}" if limit is not None else ""
            lines.append(f"  **{SessionType(session).label()}** FL: {points} pts{limit_text}")
        if payload["warnings"]:
            lines.append("\u26a0\ufe0f Warnings:")
            lines.extend(f"  \u2022 {w}" for w in payload["warnings"])
        summary = "\n".join(lines) or "No changes."
        return f"\u2705 Config **{payload['config']}** updated:\n{summary}"

    return Setting(
        kind=CONFIG_IMPORT,
        command=lambda _payload: "/results config xml-import",
        doing=lambda payload: f"Importing into config **{payload['config']}**",
        check=check,
        save=save,
        reply=reply,
        writes=("points_config_entries", "points_config_fl"),
        not_saved=_IMPORT_NOT_SAVED,
    )


def points_settings(*, modules: "ModuleService", seasons: "SeasonService") -> tuple[Setting, ...]:
    """The points configurations' settings: the services are read through those handed in."""
    del seasons  # handed now for the settings that read the season (the append, the detach)
    return (
        _config_add(modules),
        _config_remove(modules),
        _config_points(modules),
        _config_fastest_lap(modules),
        _config_import(modules),
    )


__all__ = [
    "CONFIG_ADD", "CONFIG_FASTEST_LAP", "CONFIG_IMPORT", "CONFIG_POINTS", "CONFIG_REMOVE",
    "MODULE_OFF", "points_settings",
]
