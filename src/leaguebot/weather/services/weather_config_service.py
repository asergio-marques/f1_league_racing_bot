"""weather_config_service.py — CRUD for the weather_pipeline_config table.

Provides the league's configurable phase horizons (Phase 1 in days, Phase 2 in
days, Phase 3 in hours) with defaults of 5 / 2 / 2 matching the previously
hardcoded schedule_round values.

Ordering invariant (enforced before every write):
    (phase_1_days × 24) > (phase_2_days × 24) > phase_3_hours   [strict]
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection
from leaguebot.weather.models.weather_config import WeatherPipelineConfig

_DEFAULTS = WeatherPipelineConfig()  # default field values only


async def get_weather_pipeline_config(db_path: str) -> WeatherPipelineConfig:
    """Return the league's stored config, or default values if absent."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config"
        )
        row = await cursor.fetchone()
    if row is None:
        return WeatherPipelineConfig()
    return WeatherPipelineConfig(
        phase_1_days=row["phase_1_days"],
        phase_2_days=row["phase_2_days"],
        phase_3_hours=row["phase_3_hours"],
    )


def describe_deadlines(config: WeatherPipelineConfig) -> list[str]:
    """The three deadlines, one line apiece, as every surface that reads them back words them.

    `/weather config view` and both season reviews print these lines, so the league reads its
    deadlines in the same words wherever it looks (issue #118).
    """
    return [
        f"  • Phase 1 deadline: {config.phase_1_days} day(s) before race",
        f"  • Phase 2 deadline: {config.phase_2_days} day(s) before race",
        f"  • Phase 3 deadline: {config.phase_3_hours}h before race",
    ]


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
    days: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_1_days.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path)
    err = validate_ordering(days, current.phase_2_days, current.phase_3_hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET phase_1_days = excluded.phase_1_days
            """,
            (days, current.phase_2_days, current.phase_3_hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        phase_1_days=days,
        phase_2_days=current.phase_2_days,
        phase_3_hours=current.phase_3_hours,
    )


async def set_phase_2_days(
    db_path: str,
    days: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_2_days.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path)
    err = validate_ordering(current.phase_1_days, days, current.phase_3_hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET phase_2_days = excluded.phase_2_days
            """,
            (current.phase_1_days, days, current.phase_3_hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        phase_1_days=current.phase_1_days,
        phase_2_days=days,
        phase_3_hours=current.phase_3_hours,
    )


async def set_phase_3_hours(
    db_path: str,
    hours: int,
) -> WeatherPipelineConfig | str:
    """Upsert phase_3_hours.  Returns updated config, or an error string on violation."""
    current = await get_weather_pipeline_config(db_path)
    err = validate_ordering(current.phase_1_days, current.phase_2_days, hours)
    if err:
        return err
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET phase_3_hours = excluded.phase_3_hours
            """,
            (current.phase_1_days, current.phase_2_days, hours),
        )
        await db.commit()
    return WeatherPipelineConfig(
        phase_1_days=current.phase_1_days,
        phase_2_days=current.phase_2_days,
        phase_3_hours=hours,
    )
