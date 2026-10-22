"""weather_config_service.py — CRUD for the weather_pipeline_config table.

Provides the league's configurable phase horizons (Phase 1 in days, Phase 2 in
days, Phase 3 in hours) with defaults of 5 / 2 / 2 matching the previously
hardcoded schedule_round values.

Ordering invariant (enforced before every write):
    (phase_1_days × 24) > (phase_2_days × 24) > phase_3_hours   [strict]
"""
from __future__ import annotations

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import AuditRecord
from leaguebot.core.services.setting_change import Saved
from leaguebot.weather.models.weather_config import WeatherPipelineConfig

_DEFAULTS = WeatherPipelineConfig()  # default field values only

#: The three deadline columns, in the order the phases fire; the only columns a write may name.
DEADLINE_COLUMNS = ("phase_1_days", "phase_2_days", "phase_3_hours")


async def get_weather_pipeline_config(db_path: str) -> WeatherPipelineConfig:
    """Return the league's stored config, or default values if absent."""
    async with get_connection(db_path) as db:
        return await read_config_on(db)


async def read_config_on(db: aiosqlite.Connection) -> WeatherPipelineConfig:
    """The stored configuration read on *db*, or the defaults where none is stored."""
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


def deadline_details(config: WeatherPipelineConfig) -> tuple[str, str, str]:
    """The three deadlines in force, one detail apiece, as a success line lists them."""
    return (
        f"Phase 1: {config.phase_1_days} day(s)",
        f"Phase 2: {config.phase_2_days} day(s)",
        f"Phase 3: {config.phase_3_hours} hour(s)",
    )


def deadline_refusal(config: WeatherPipelineConfig, column: str, value: int) -> str | None:
    """Why setting *column* to *value* would break the ordering invariant against the rest of
    *config*, or None: `validate_ordering` with *column* replaced. Pure."""
    held = {name: getattr(config, name) for name in DEADLINE_COLUMNS}
    if column not in held:
        raise ValueError(f"{column!r} is not a weather deadline")
    held[column] = value
    return validate_ordering(held["phase_1_days"], held["phase_2_days"], held["phase_3_hours"])


async def set_deadline_on(db: aiosqlite.Connection, column: str, value: int) -> Saved:
    """Set the deadline *column* to *value* on *db*, committing nothing.

    A value the league already holds (the packaged default counts as held) writes nothing and
    comes back unchanged, with the one deadline beneath its line; otherwise the one column is
    upserted, one `WEATHER_CONFIG_SET` entry records it from the old value to the new, and the
    three deadlines now in force are the details. The new configuration is in the result, by
    column. The ordering is the check's to judge, not this write's.
    """
    if column not in DEADLINE_COLUMNS:
        raise ValueError(f"{column!r} is not a weather deadline")
    current = await read_config_on(db)
    old = getattr(current, column)
    if old == value:
        index = DEADLINE_COLUMNS.index(column)
        return Saved(
            changed=False,
            details=(deadline_details(current)[index],),
            result={name: getattr(current, name) for name in DEADLINE_COLUMNS},
        )
    await db.execute(
        f"""
        INSERT INTO weather_pipeline_config (id, phase_1_days, phase_2_days, phase_3_hours)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET {column} = excluded.{column}
        """,
        tuple(value if name == column else getattr(current, name) for name in DEADLINE_COLUMNS),
    )
    updated = {
        name: (value if name == column else getattr(current, name)) for name in DEADLINE_COLUMNS
    }
    return Saved(
        changed=True,
        audits=(AuditRecord("WEATHER_CONFIG_SET", {column: old}, {column: value}),),
        details=deadline_details(WeatherPipelineConfig(**updated)),
        result=updated,
    )
