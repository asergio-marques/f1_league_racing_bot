"""weather_config_service.py — CRUD for the weather_pipeline_config table.

Provides per-server configurable phase horizons (Phase 1 in days, Phase 2 in
days, Phase 3 in hours) with defaults of 5 / 2 / 2 matching the previously
hardcoded schedule_round values.

Ordering invariant (enforced before every write):
    (phase_1_days × 24) > (phase_2_days × 24) > phase_3_hours   [strict]
"""
from __future__ import annotations

from db.database import get_connection
from models.weather_config import WeatherPipelineConfig

_DEFAULTS = WeatherPipelineConfig(server_id=0)  # default field values only


async def get_weather_pipeline_config(
    db_path: str,
    server_id: int,
) -> WeatherPipelineConfig:
    """Return the stored config for *server_id*, or default values if absent."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase_1_days, phase_2_days, phase_3_hours "
            "FROM weather_pipeline_config WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return WeatherPipelineConfig(server_id=server_id)
    return WeatherPipelineConfig(
        server_id=server_id,
        phase_1_days=row["phase_1_days"],
        phase_2_days=row["phase_2_days"],
        phase_3_hours=row["phase_3_hours"],
    )


def validate_ordering(
    p1_days: int,
    p2_days: int,
    p3_hours: int,
) -> str | None:
    """Return None if the ordering invariant holds, or an error string if not.

    Invariant: (p1_days × 24) > (p2_days × 24) > p3_hours  (strict inequality)
    """
    p1_hours = p1_days * 24
    p2_hours = p2_days * 24
    if p1_hours <= p2_hours:
        return (
            f"Phase 1 deadline ({p1_days}d = {p1_hours}h) must be strictly greater than "
            f"Phase 2 deadline ({p2_days}d = {p2_hours}h)."
        )
    if p2_hours <= p3_hours:
        return (
            f"Phase 2 deadline ({p2_days}d = {p2_hours}h) must be strictly greater than "
            f"Phase 3 deadline ({p3_hours}h)."
        )
    return None


async def set_phase_1_days(
    db_path: str,
    server_id: int,
    days: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_1_days.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path, server_id)
    err = validate_ordering(days, current.phase_2_days, current.phase_3_hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (server_id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET phase_1_days = excluded.phase_1_days
            """,
            (server_id, days, current.phase_2_days, current.phase_3_hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        server_id=server_id,
        phase_1_days=days,
        phase_2_days=current.phase_2_days,
        phase_3_hours=current.phase_3_hours,
    )


async def set_phase_2_days(
    db_path: str,
    server_id: int,
    days: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_2_days.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path, server_id)
    err = validate_ordering(current.phase_1_days, days, current.phase_3_hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (server_id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET phase_2_days = excluded.phase_2_days
            """,
            (server_id, current.phase_1_days, days, current.phase_3_hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        server_id=server_id,
        phase_1_days=current.phase_1_days,
        phase_2_days=days,
        phase_3_hours=current.phase_3_hours,
    )


async def set_phase_3_hours(
    db_path: str,
    server_id: int,
    hours: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_3_hours.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path, server_id)
    err = validate_ordering(current.phase_1_days, current.phase_2_days, hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (server_id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET phase_3_hours = excluded.phase_3_hours
            """,
            (server_id, current.phase_1_days, current.phase_2_days, hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        server_id=server_id,
        phase_1_days=current.phase_1_days,
        phase_2_days=current.phase_2_days,
        phase_3_hours=hours,
    )
