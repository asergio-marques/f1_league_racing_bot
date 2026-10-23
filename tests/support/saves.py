"""Committing writers of the tests' own, for seeding a league's settings (#439, slice 7).

Every settings command of results, weather and images saves through the change queue, so `src/`
keeps no writer that opens its own connection and commits: each setting has an `_on` form that
writes on the connection it is handed and commits nothing. A test that only needs a value in
place, to seed what it really tests, calls the twin of the writer it used before, here, under the
same name and with the same arguments, so that it changes its import alone. Each twin opens a
connection, calls the `_on` form, commits, and returns what the old writer answered where a test
reads it (whether the value changed, or how many slots did). The actor and the clock some old
writers took are accepted and ignored: the queue stamps the audit entry now, and a seed records
none.

The image twins take the `ImageConfigService` they used to be called on, or its database path.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from leaguebot.core.db.database import get_connection
from leaguebot.image.services.image_config_service import (
    set_aspect_on,
    set_daily_portraits_on,
    set_field_on,
    set_flag_on,
    set_tier_colours_on,
)
from leaguebot.results.services.amendment_mode_service import (
    revert_on,
    set_amendment_mode_on,
    stage_fastest_lap_on,
    stage_points_on,
)
from leaguebot.results.services.points_config_service import (
    config_fastest_lap_on,
    config_points_on,
    create_config_on,
    remove_config_on,
    xml_import_config_on,
)
from leaguebot.results.services.season_points_service import attach_config_on, detach_config_on
from leaguebot.weather.services.weather_config_service import (
    deadline_refusal,
    read_config_on,
    set_deadline_on,
)


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
    await _commit(db_path, create_config_on, config_name)


async def remove_config(db_path: str, config_name: str) -> None:
    """Remove the points configuration *config_name*, with its setup seasons' links."""
    await _commit(db_path, remove_config_on, config_name)


async def set_session_points(db_path: str, config_name: str, session_type: Any, position: int,
                             points: int) -> None:
    """Set one position's points in one session of *config_name*."""
    await _commit(db_path, config_points_on, config_name, session_type, [(position, points)])


async def set_session_points_many(db_path: str, config_name: str, session_type: Any,
                                  pairs: list[tuple[int, int]], **_actor: Any) -> None:
    """Set several positions' points in one session of *config_name*, in one save."""
    await _commit(db_path, config_points_on, config_name, session_type, list(pairs))


async def set_fl_bonus(db_path: str, config_name: str, session_type: Any, fl_points: int) -> None:
    """Set the fastest-lap bonus of one race session of *config_name*."""
    await _commit(db_path, config_fastest_lap_on, config_name, session_type, fl_points=fl_points)


async def set_fl_position_limit(db_path: str, config_name: str, session_type: Any,
                                limit: int) -> None:
    """Set the fastest-lap position limit of one race session of *config_name*."""
    await _commit(db_path, config_fastest_lap_on, config_name, session_type,
                  fl_position_limit=limit)


async def xml_import_config(db_path: str, config_name: str, payload: Any, **_actor: Any) -> None:
    """Import *payload*'s positions and fastest laps into *config_name*, in one save."""
    await _commit(db_path, xml_import_config_on, config_name, payload.positions,
                  payload.fastest_laps)


async def attach_config(db_path: str, season_id: int, config_name: str,
                        season_status: str = "SETUP") -> None:
    """Attach *config_name* to the season *season_id*; its status is no longer the writer's."""
    await _commit(db_path, attach_config_on, season_id, config_name)


async def detach_config(db_path: str, season_id: int, config_name: str,
                        season_status: str = "SETUP") -> None:
    """Detach *config_name* from the season *season_id*."""
    await _commit(db_path, detach_config_on, season_id, config_name)


# ---------------------------------------------------------------------------
# Results: the amendment mode
# ---------------------------------------------------------------------------


async def enable_amendment_mode(db_path: str, season_id: int) -> None:
    """Turn amendment mode on for *season_id*, copying its points into the store."""
    await _commit(db_path, set_amendment_mode_on, season_id, True)


async def disable_amendment_mode(db_path: str, season_id: int) -> None:
    """Turn amendment mode off for *season_id*, emptying the store."""
    await _commit(db_path, set_amendment_mode_on, season_id, False)


async def revert_modification_store(db_path: str, season_id: int) -> None:
    """Put the store back to the season's points."""
    await _commit(db_path, revert_on, season_id)


async def modify_session_points(db_path: str, season_id: int, config_name: str,
                                session_type: Any, pairs: list[tuple[int, int]],
                                **_actor: Any) -> None:
    """Stage positions' points of one session of *config_name*, in one save."""
    await _commit(db_path, stage_points_on, season_id, config_name, session_type, list(pairs))


async def modify_fl_bonus(db_path: str, season_id: int, config_name: str, session_type: Any,
                          fl_points: int) -> None:
    """Stage the fastest-lap bonus of one race session of *config_name*."""
    await _commit(db_path, stage_fastest_lap_on, season_id, config_name, session_type,
                  fl_points=fl_points)


async def modify_fl_position_limit(db_path: str, season_id: int, config_name: str,
                                   session_type: Any, limit: int) -> None:
    """Stage the fastest-lap position limit of one race session of *config_name*."""
    await _commit(db_path, stage_fastest_lap_on, season_id, config_name, session_type,
                  fl_position_limit=limit)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


async def set_field(target: Any, column: str, value: str) -> bool:
    """Set one of the image configuration's text columns; whether it changed."""
    return bool((await _commit(_path(target), set_field_on, column, value)).changed)


async def set_flag(target: Any, column: str, enabled: bool) -> bool:
    """Set one of the image configuration's flags; whether it changed."""
    return bool((await _commit(_path(target), set_flag_on, column, enabled)).changed)


async def set_pfp_flag(target: Any, column: str, enabled: bool) -> None:
    """Set one of the three portrait flags."""
    await _commit(_path(target), set_flag_on, column, enabled)


async def set_aspect(target: Any, aspect: str, enabled: bool) -> None:
    """Switch one image output on or off."""
    await _commit(_path(target), set_aspect_on, aspect, enabled)


async def set_tier_colour(target: Any, division_name: str, slot: str, colour: str) -> bool:
    """Set one slot's colour for one tier; whether it changed."""
    saved = await _commit(_path(target), set_tier_colours_on, division_name, {slot: colour})
    return bool(saved.changed)


async def set_tier_colours(target: Any, division_name: str, colours: dict[str, str]) -> int:
    """Set several slots for one tier; how many it changed."""
    saved = await _commit(_path(target), set_tier_colours_on, division_name, dict(colours))
    return len(saved.audits)


async def set_daily_portraits(target: Any, time_of_day: str) -> None:
    """Turn daily portrait updates on at *time_of_day* (HH:MM, UTC)."""
    await _commit(_path(target), set_daily_portraits_on, time_of_day)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------


async def set_deadline(db_path: str, column: str, value: int) -> Any:
    """Set one weather deadline: `phase_1_days`, `phase_2_days` or `phase_3_hours`.

    Answers what the old writers did: the configuration where it changed, None where the value
    was held already, or the refusal where it is out of order with the others (the check's
    refusal now, `deadline_refusal`).
    """
    async with get_connection(db_path) as db:
        reason = deadline_refusal(await read_config_on(db), column, value)
        if reason is not None:
            return reason
        saved = await set_deadline_on(db, column, value)
        await db.commit()
        if not saved.changed:
            return None
        return await read_config_on(db)
