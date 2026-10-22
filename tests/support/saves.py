"""Committing writers of the tests' own, for seeding a league's settings (#439, slice 7).

Every settings command of results, weather and images saves through the change queue, so `src/`
keeps no writer that opens its own connection and commits: each setting has an `_on` form that
writes on the connection it is handed and commits nothing. A test that only needs a value in
place, to seed what it really tests, calls the twin of the writer it used before, here, under the
same name and with the same arguments, so that it changes its import alone. Each twin opens a
connection, calls the `_on` form, commits, and returns what the old writer answered where a test
reads it (whether the value changed, or how many slots did).

**Until the build writes an `_on` form, its twin calls today's writer.** The tests stage lands
before any production code, and seeding must hold on every commit between, so `_on_form` looks
the form up and each twin falls back to the writer it replaces while the form is absent. Once
the build deletes that writer, the fallback is never reached. The actor and the clock some old
writers took are accepted and ignored: the queue stamps the audit entry now, and a seed records
none.

The image twins take the `ImageConfigService` they used to be called on, or its database path.
"""
from __future__ import annotations

import importlib
from typing import Any, Awaitable, Callable

from leaguebot.core.db.database import get_connection

POINTS = "leaguebot.results.services.points_config_service"
SEASON_POINTS = "leaguebot.results.services.season_points_service"
AMENDMENT_MODE = "leaguebot.results.services.amendment_mode_service"
AMENDMENT = "leaguebot.core.services.amendment_service"
IMAGE = "leaguebot.image.services.image_config_service"
WEATHER = "leaguebot.weather.services.weather_config_service"

#: Each weather deadline's column, and the writer that set it until the build.
_DEADLINE_WRITERS = {
    "phase_1_days": "set_phase_1_days",
    "phase_2_days": "set_phase_2_days",
    "phase_3_hours": "set_phase_3_hours",
}


def _on_form(module_path: str, name: str) -> Callable[..., Any] | None:
    """The `_on` form *name* of *module_path*, or None while the build has not written it."""
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError:
        return None
    return getattr(module, name, None)


def _today(module_path: str, name: str) -> Callable[..., Awaitable[Any]]:
    """The committing writer *name* that `src/` carries until the build deletes it."""
    return getattr(importlib.import_module(module_path), name)


async def _commit(db_path: str, form: Callable[..., Awaitable[Any]], *args: Any,
                  **kwargs: Any) -> Any:
    """Call *form* on a connection of its own and commit what it wrote; return its `Saved`."""
    async with get_connection(db_path) as db:
        saved = await form(db, *args, **kwargs)
        await db.commit()
    return saved


def _path(target: Any) -> str:
    """The database path of *target*, an `ImageConfigService` or a path already."""
    return target if isinstance(target, str) else target._db_path


# ---------------------------------------------------------------------------
# Results: the points configurations
# ---------------------------------------------------------------------------


async def create_config(db_path: str, config_name: str) -> None:
    """Add the points configuration *config_name*."""
    form = _on_form(POINTS, "create_config_on")
    if form is None:
        await _today(POINTS, "create_config")(db_path, config_name)
        return
    await _commit(db_path, form, config_name)


async def remove_config(db_path: str, config_name: str) -> None:
    """Remove the points configuration *config_name*, with its setup seasons' links."""
    form = _on_form(POINTS, "remove_config_on")
    if form is None:
        await _today(POINTS, "remove_config")(db_path, config_name)
        return
    await _commit(db_path, form, config_name)


async def set_session_points(db_path: str, config_name: str, session_type: Any, position: int,
                             points: int) -> None:
    """Set one position's points in one session of *config_name*."""
    form = _on_form(POINTS, "config_points_on")
    if form is None:
        await _today(POINTS, "set_session_points")(
            db_path, config_name, session_type, position, points
        )
        return
    await _commit(db_path, form, config_name, session_type, [(position, points)])


async def set_session_points_many(db_path: str, config_name: str, session_type: Any,
                                  pairs: list[tuple[int, int]], **_actor: Any) -> None:
    """Set several positions' points in one session of *config_name*, in one save."""
    form = _on_form(POINTS, "config_points_on")
    if form is None:
        await _today(POINTS, "set_session_points_many")(
            db_path, config_name, session_type, pairs, **_actor
        )
        return
    await _commit(db_path, form, config_name, session_type, list(pairs))


async def set_fl_bonus(db_path: str, config_name: str, session_type: Any, fl_points: int) -> None:
    """Set the fastest-lap bonus of one race session of *config_name*."""
    form = _on_form(POINTS, "config_fastest_lap_on")
    if form is None:
        await _today(POINTS, "set_fl_bonus")(db_path, config_name, session_type, fl_points)
        return
    await _commit(db_path, form, config_name, session_type, fl_points=fl_points)


async def set_fl_position_limit(db_path: str, config_name: str, session_type: Any,
                                limit: int) -> None:
    """Set the fastest-lap position limit of one race session of *config_name*."""
    form = _on_form(POINTS, "config_fastest_lap_on")
    if form is None:
        await _today(POINTS, "set_fl_position_limit")(db_path, config_name, session_type, limit)
        return
    await _commit(db_path, form, config_name, session_type, fl_position_limit=limit)


async def xml_import_config(db_path: str, config_name: str, payload: Any, **_actor: Any) -> None:
    """Import *payload*'s positions and fastest laps into *config_name*, in one save."""
    form = _on_form(POINTS, "xml_import_config_on")
    if form is None:
        await _today(POINTS, "xml_import_config")(db_path, config_name, payload, **_actor)
        return
    await _commit(db_path, form, config_name, payload.positions, payload.fastest_laps)


async def attach_config(db_path: str, season_id: int, config_name: str,
                        season_status: str = "SETUP") -> None:
    """Attach *config_name* to the season *season_id*; its status is no longer the writer's."""
    form = _on_form(SEASON_POINTS, "attach_config_on")
    if form is None:
        await _today(SEASON_POINTS, "attach_config")(
            db_path, season_id, config_name, season_status
        )
        return
    await _commit(db_path, form, season_id, config_name)


async def detach_config(db_path: str, season_id: int, config_name: str,
                        season_status: str = "SETUP") -> None:
    """Detach *config_name* from the season *season_id*."""
    form = _on_form(SEASON_POINTS, "detach_config_on")
    if form is None:
        await _today(SEASON_POINTS, "detach_config")(
            db_path, season_id, config_name, season_status
        )
        return
    await _commit(db_path, form, season_id, config_name)


# ---------------------------------------------------------------------------
# Results: the amendment mode
# ---------------------------------------------------------------------------


async def enable_amendment_mode(db_path: str, season_id: int) -> None:
    """Turn amendment mode on for *season_id*, copying its points into the store."""
    form = _on_form(AMENDMENT_MODE, "set_amendment_mode_on")
    if form is None:
        await _today(AMENDMENT, "enable_amendment_mode")(db_path, season_id)
        return
    await _commit(db_path, form, season_id, True)


async def disable_amendment_mode(db_path: str, season_id: int) -> None:
    """Turn amendment mode off for *season_id*, emptying the store."""
    form = _on_form(AMENDMENT_MODE, "set_amendment_mode_on")
    if form is None:
        await _today(AMENDMENT, "disable_amendment_mode")(db_path, season_id)
        return
    await _commit(db_path, form, season_id, False)


async def revert_modification_store(db_path: str, season_id: int) -> None:
    """Put the store back to the season's points."""
    form = _on_form(AMENDMENT_MODE, "revert_on")
    if form is None:
        await _today(AMENDMENT, "revert_modification_store")(db_path, season_id)
        return
    await _commit(db_path, form, season_id)


async def modify_session_points(db_path: str, season_id: int, config_name: str,
                                session_type: Any, pairs: list[tuple[int, int]],
                                **_actor: Any) -> None:
    """Stage positions' points of one session of *config_name*, in one save."""
    form = _on_form(AMENDMENT_MODE, "stage_points_on")
    if form is None:
        await _today(AMENDMENT, "modify_session_points")(
            db_path, season_id, config_name, session_type, pairs, **_actor
        )
        return
    await _commit(db_path, form, season_id, config_name, session_type, list(pairs))


async def modify_fl_bonus(db_path: str, season_id: int, config_name: str, session_type: Any,
                          fl_points: int) -> None:
    """Stage the fastest-lap bonus of one race session of *config_name*."""
    form = _on_form(AMENDMENT_MODE, "stage_fastest_lap_on")
    if form is None:
        await _today(AMENDMENT, "modify_fl_bonus")(
            db_path, season_id, config_name, session_type, fl_points
        )
        return
    await _commit(db_path, form, season_id, config_name, session_type, fl_points=fl_points)


async def modify_fl_position_limit(db_path: str, season_id: int, config_name: str,
                                   session_type: Any, limit: int) -> None:
    """Stage the fastest-lap position limit of one race session of *config_name*."""
    form = _on_form(AMENDMENT_MODE, "stage_fastest_lap_on")
    if form is None:
        await _today(AMENDMENT, "modify_fl_position_limit")(
            db_path, season_id, config_name, session_type, limit
        )
        return
    await _commit(db_path, form, season_id, config_name, session_type, fl_position_limit=limit)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _image_service(target: Any) -> Any:
    from leaguebot.image.services.image_config_service import ImageConfigService

    return ImageConfigService(_path(target))


async def set_field(target: Any, column: str, value: str) -> bool:
    """Set one of the image configuration's text columns; whether it changed."""
    form = _on_form(IMAGE, "set_field_on")
    if form is None:
        return bool(await _image_service(target).set_field(column, value))
    return bool((await _commit(_path(target), form, column, value)).changed)


async def set_flag(target: Any, column: str, enabled: bool) -> bool:
    """Set one of the image configuration's flags; whether it changed."""
    form = _on_form(IMAGE, "set_flag_on")
    if form is None:
        return bool(await _image_service(target).set_flag(column, enabled))
    return bool((await _commit(_path(target), form, column, enabled)).changed)


async def set_pfp_flag(target: Any, column: str, enabled: bool) -> None:
    """Set one of the three portrait flags."""
    form = _on_form(IMAGE, "set_flag_on")
    if form is None:
        await _image_service(target).set_pfp_flag(column, enabled)
        return
    await _commit(_path(target), form, column, enabled)


async def set_aspect(target: Any, aspect: str, enabled: bool) -> None:
    """Switch one image output on or off."""
    form = _on_form(IMAGE, "set_aspect_on")
    if form is None:
        await _image_service(target).set_aspect(aspect, enabled)
        return
    await _commit(_path(target), form, aspect, enabled)


async def set_tier_colour(target: Any, division_name: str, slot: str, colour: str) -> bool:
    """Set one slot's colour for one tier; whether it changed."""
    form = _on_form(IMAGE, "set_tier_colours_on")
    if form is None:
        return bool(await _image_service(target).set_tier_colour(division_name, slot, colour))
    return bool((await _commit(_path(target), form, division_name, {slot: colour})).changed)


async def set_tier_colours(target: Any, division_name: str, colours: dict[str, str]) -> int:
    """Set several slots for one tier; how many it changed."""
    form = _on_form(IMAGE, "set_tier_colours_on")
    if form is None:
        return int(await _image_service(target).set_tier_colours(division_name, colours))
    saved = await _commit(_path(target), form, division_name, dict(colours))
    return len(saved.audits)


async def set_daily_portraits(target: Any, time_of_day: str) -> None:
    """Turn daily portrait updates on at *time_of_day* (HH:MM, UTC)."""
    form = _on_form(IMAGE, "set_daily_portraits_on")
    if form is None:
        service = _image_service(target)
        await service.set_field("pfp_daily_time", time_of_day)
        await service.set_pfp_flag("pfp_daily", True)
        return
    await _commit(_path(target), form, time_of_day)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


async def set_deadline(db_path: str, column: str, value: int) -> Any:
    """Set one weather deadline: `phase_1_days`, `phase_2_days` or `phase_3_hours`.

    Answers what the old writers did: the configuration where it changed, None where the value
    was held already, or the refusal where it is out of order with the others (the check's
    refusal now, `deadline_refusal`).
    """
    form = _on_form(WEATHER, "set_deadline_on")
    if form is None:
        return await _today(WEATHER, _DEADLINE_WRITERS[column])(db_path, value)
    refusal = _on_form(WEATHER, "deadline_refusal")
    reader = _on_form(WEATHER, "read_config_on")
    async with get_connection(db_path) as db:
        if refusal is not None and reader is not None:
            reason = refusal(await reader(db), column, value)
            if reason is not None:
                return reason
        saved = await form(db, column, value)
        await db.commit()
        if not saved.changed:
            return None
        return await reader(db) if reader is not None else None
