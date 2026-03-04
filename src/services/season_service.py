"""SeasonService — season, division, round, and session management."""

from __future__ import annotations

import logging
from datetime import date, datetime

from db.database import get_connection
from models.division import Division
from models.round import Round, RoundFormat
from models.season import Season, SeasonStatus
from models.session import Session, SessionType, SESSIONS_BY_FORMAT

log = logging.getLogger(__name__)


class SeasonService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Season
    # ------------------------------------------------------------------

    async def create_season(self, server_id: int, start_date: date) -> Season:
        """Insert a new SETUP season and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO seasons (server_id, start_date, status) VALUES (?, ?, ?)",
                (server_id, start_date.isoformat(), SeasonStatus.SETUP.value),
            )
            await db.commit()
            season_id = cursor.lastrowid

        return Season(
            id=season_id,
            server_id=server_id,
            start_date=start_date,
            status=SeasonStatus.SETUP,
        )

    async def get_active_season(self, server_id: int) -> Season | None:
        """Return the ACTIVE season for *server_id*, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, status FROM seasons "
                "WHERE server_id = ? AND status = ?",
                (server_id, SeasonStatus.ACTIVE.value),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_season(row)

    async def transition_to_active(self, season_id: int) -> None:
        """Set season status to ACTIVE."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = ? WHERE id = ?",
                (SeasonStatus.ACTIVE.value, season_id),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Division
    # ------------------------------------------------------------------

    async def add_division(
        self,
        season_id: int,
        name: str,
        mention_role_id: int,
        forecast_channel_id: int,
        race_day: int,
        race_time: str,
    ) -> Division:
        """Insert a division and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO divisions
                    (season_id, name, mention_role_id, forecast_channel_id, race_day, race_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (season_id, name, mention_role_id, forecast_channel_id, race_day, race_time),
            )
            await db.commit()
            div_id = cursor.lastrowid

        return Division(
            id=div_id,
            season_id=season_id,
            name=name,
            mention_role_id=mention_role_id,
            forecast_channel_id=forecast_channel_id,
            race_day=race_day,
            race_time=race_time,
        )

    async def get_divisions(self, season_id: int) -> list[Division]:
        """Return all divisions for *season_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, "
                "race_day, race_time FROM divisions WHERE season_id = ?",
                (season_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_division(r) for r in rows]

    # ------------------------------------------------------------------
    # Round
    # ------------------------------------------------------------------

    async def add_round(
        self,
        division_id: int,
        round_number: int,
        fmt: RoundFormat,
        track_name: str | None,
        scheduled_at: datetime,
    ) -> Round:
        """Insert a round and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO rounds
                    (division_id, round_number, format, track_name, scheduled_at,
                     phase1_done, phase2_done, phase3_done)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0)
                """,
                (
                    division_id,
                    round_number,
                    fmt.value,
                    track_name,
                    scheduled_at.isoformat(),
                ),
            )
            await db.commit()
            round_id = cursor.lastrowid

        return Round(
            id=round_id,
            division_id=division_id,
            round_number=round_number,
            format=fmt,
            track_name=track_name,
            scheduled_at=scheduled_at,
        )

    async def get_round(self, round_id: int) -> Round | None:
        """Return a single round by ID."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "phase1_done, phase2_done, phase3_done FROM rounds WHERE id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()
        return _row_to_round(row) if row else None

    async def get_division_rounds(self, division_id: int) -> list[Round]:
        """Return all rounds for *division_id* ordered by round_number."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "phase1_done, phase2_done, phase3_done FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_round(r) for r in rows]

    async def update_round_field(self, round_id: int, field: str, value: object) -> None:
        """Generic field updater used by amendment_service."""
        allowed = {"track_name", "format", "scheduled_at", "phase1_done", "phase2_done", "phase3_done"}
        if field not in allowed:
            raise ValueError(f"Field {field!r} not updatable via this method")
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE rounds SET {field} = ? WHERE id = ?",  # noqa: S608
                (value, round_id),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def create_sessions_for_round(self, round_id: int, fmt: RoundFormat) -> list[Session]:
        """Insert Session rows for every session type defined by *fmt*."""
        session_types: list[SessionType] = SESSIONS_BY_FORMAT.get(fmt, [])
        sessions: list[Session] = []

        async with get_connection(self._db_path) as db:
            for st in session_types:
                cursor = await db.execute(
                    "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                    (round_id, st.value),
                )
                sessions.append(
                    Session(id=cursor.lastrowid, round_id=round_id, session_type=st)
                )
            await db.commit()

        return sessions

    async def get_sessions(self, round_id: int) -> list[Session]:
        """Return all sessions for *round_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, round_id, session_type, phase2_slot_type, phase3_slots "
                "FROM sessions WHERE round_id = ?",
                (round_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_session(r) for r in rows]

    async def update_session_phase2(self, session_id: int, slot_type: str) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = ? WHERE id = ?",
                (slot_type, session_id),
            )
            await db.commit()

    async def update_session_phase3(self, session_id: int, slots: list[str]) -> None:
        import json
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase3_slots = ? WHERE id = ?",
                (json.dumps(slots), session_id),
            )
            await db.commit()

    async def clear_session_phase_data(self, round_id: int) -> None:
        """Clear phase2 / phase3 data for all sessions in a round (used by amendments)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = NULL, phase3_slots = NULL WHERE round_id = ?",
                (round_id,),
            )
            await db.commit()


# ------------------------------------------------------------------
# Row mappers
# ------------------------------------------------------------------

def _row_to_season(row: object) -> Season:
    return Season(
        id=row["id"],
        server_id=row["server_id"],
        start_date=date.fromisoformat(row["start_date"]),
        status=SeasonStatus(row["status"]),
    )


def _row_to_division(row: object) -> Division:
    return Division(
        id=row["id"],
        season_id=row["season_id"],
        name=row["name"],
        mention_role_id=row["mention_role_id"],
        forecast_channel_id=row["forecast_channel_id"],
        race_day=row["race_day"],
        race_time=row["race_time"],
    )


def _row_to_round(row: object) -> Round:
    return Round(
        id=row["id"],
        division_id=row["division_id"],
        round_number=row["round_number"],
        format=RoundFormat(row["format"]),
        track_name=row["track_name"],
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
        phase1_done=bool(row["phase1_done"]),
        phase2_done=bool(row["phase2_done"]),
        phase3_done=bool(row["phase3_done"]),
    )


def _row_to_session(row: object) -> Session:
    import json

    slots_raw = row["phase3_slots"]
    return Session(
        id=row["id"],
        round_id=row["round_id"],
        session_type=SessionType(row["session_type"]),
        phase2_slot_type=row["phase2_slot_type"],
        phase3_slots=json.loads(slots_raw) if slots_raw else None,
    )
