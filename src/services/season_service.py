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

    async def create_season(self, server_id: int, start_date: date | None = None) -> Season:
        """Insert a new SETUP season and return it."""
        if start_date is None:
            start_date = date.today()
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
                "SELECT id, server_id, start_date, status, season_number FROM seasons "
                "WHERE server_id = ? AND status = ?",
                (server_id, SeasonStatus.ACTIVE.value),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_season(row)

    async def get_season_for_server(self, server_id: int) -> Season | None:
        """Return the most recent season for *server_id* regardless of status.

        Used by channel assignment commands that should work in any season state.
        Returns the season with the highest id for the server.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, status, season_number FROM seasons "
                "WHERE server_id = ? ORDER BY id DESC LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def get_setup_season(self, server_id: int) -> Season | None:
        """Return the SETUP season for *server_id*, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, status, season_number FROM seasons "
                "WHERE server_id = ? AND status = 'SETUP' LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def has_existing_season(self, server_id: int) -> bool:
        """Return True if any season row exists for *server_id* (any status)."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons WHERE server_id = ? LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        return row is not None

    async def has_active_or_completed_season(self, server_id: int) -> bool:
        """Return True if an ACTIVE or COMPLETED season exists for *server_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons WHERE server_id = ? AND status IN ('ACTIVE', 'COMPLETED') LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        return row is not None

    async def save_pending_snapshot(
        self,
        server_id: int,
        start_date: date,
        existing_season_id: int,
        divisions: list[dict],
    ) -> tuple[int, int]:
        """Atomically replace the SETUP season snapshot for *server_id* in the DB.

        Deletes the previous SETUP season (if *existing_season_id* is non-zero)
        and re-inserts the full pending config.  Sessions are NOT created here —
        they are created at approve time.

        Returns (new_season_id, season_number) so callers can update their in-memory state.
        """
        async with get_connection(self._db_path) as db:
            # Determine the season_number to carry forward
            if existing_season_id != 0:
                # Preserve the already-computed season_number from the existing SETUP row
                cursor = await db.execute(
                    "SELECT season_number FROM seasons WHERE id = ?",
                    (existing_season_id,),
                )
                row = await cursor.fetchone()
                season_number: int = row[0] if row else 1
            else:
                # First snapshot: derive from server_config.previous_season_number
                cursor = await db.execute(
                    "SELECT previous_season_number FROM server_configs WHERE server_id = ?",
                    (server_id,),
                )
                row = await cursor.fetchone()
                season_number = (row[0] if row else 0) + 1

            if existing_season_id != 0:
                # Save division_results_config keyed by division name so we can
                # restore channel assignments after divisions are re-created with new IDs.
                cursor = await db.execute(
                    "SELECT id FROM divisions WHERE season_id = ?",
                    (existing_season_id,),
                )
                div_rows = await cursor.fetchall()

                saved_channel_cfg: dict[int, dict] = {}  # div_id → config row
                saved_div_names: dict[int, str] = {}     # div_id → name
                for div_row in div_rows:
                    old_div_id = div_row[0]
                    cursor2 = await db.execute(
                        "SELECT name FROM divisions WHERE id = ?", (old_div_id,)
                    )
                    name_row = await cursor2.fetchone()
                    if name_row:
                        saved_div_names[old_div_id] = name_row[0]
                    cursor2 = await db.execute(
                        "SELECT results_channel_id, standings_channel_id, reserves_in_standings "
                        "FROM division_results_config WHERE division_id = ?",
                        (old_div_id,),
                    )
                    cfg_row = await cursor2.fetchone()
                    if cfg_row:
                        saved_channel_cfg[old_div_id] = {
                            "results_channel_id": cfg_row[0],
                            "standings_channel_id": cfg_row[1],
                            "reserves_in_standings": cfg_row[2],
                        }

                # name → channel config (for lookup when new division IDs are known)
                channels_by_name: dict[str, dict] = {
                    saved_div_names[did]: cfg
                    for did, cfg in saved_channel_cfg.items()
                    if did in saved_div_names
                }

                # Cascade-delete the old SETUP season manually (no ON DELETE CASCADE on
                # seasons/divisions, though division_results_config does have it).
                # Clean up team_instances/team_seats first to avoid orphaned rows.
                for div_row in div_rows:
                    cursor2 = await db.execute(
                        "SELECT id FROM team_instances WHERE division_id = ?",
                        (div_row[0],),
                    )
                    inst_rows = await cursor2.fetchall()
                    for inst_row in inst_rows:
                        await db.execute(
                            "DELETE FROM team_seats WHERE team_instance_id = ?",
                            (inst_row[0],),
                        )
                    await db.execute(
                        "DELETE FROM team_instances WHERE division_id = ?", (div_row[0],)
                    )
                    await db.execute(
                        "DELETE FROM rounds WHERE division_id = ?", (div_row[0],)
                    )
                await db.execute(
                    "DELETE FROM divisions WHERE season_id = ?", (existing_season_id,)
                )
                await db.execute(
                    "DELETE FROM seasons WHERE id = ?", (existing_season_id,)
                )
            else:
                channels_by_name = {}

            cursor = await db.execute(
                "INSERT INTO seasons (server_id, start_date, status, season_number) "
                "VALUES (?, ?, 'SETUP', ?)",
                (server_id, start_date.isoformat(), season_number),
            )
            new_season_id: int = cursor.lastrowid  # type: ignore[assignment]

            for div_data in divisions:
                cursor = await db.execute(
                    "INSERT INTO divisions "
                    "(season_id, name, mention_role_id, forecast_channel_id, tier) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        new_season_id,
                        div_data["name"],
                        div_data["role_id"],
                        div_data["channel_id"],
                        div_data.get("tier", 0),
                    ),
                )
                div_db_id: int = cursor.lastrowid  # type: ignore[assignment]

                # Restore any previously-assigned results/standings channels for
                # this division (by name), which would otherwise be lost because
                # save_pending_snapshot deletes and re-creates division rows.
                saved = channels_by_name.get(div_data["name"])
                if saved and (saved["results_channel_id"] or saved["standings_channel_id"]):
                    await db.execute(
                        "INSERT INTO division_results_config "
                        "(division_id, results_channel_id, standings_channel_id, reserves_in_standings) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            div_db_id,
                            saved["results_channel_id"],
                            saved["standings_channel_id"],
                            saved["reserves_in_standings"] if saved["reserves_in_standings"] is not None else 1,
                        ),
                    )

                for r in div_data["rounds"]:
                    await db.execute(
                        "INSERT INTO rounds "
                        "(division_id, round_number, format, track_name, "
                        " scheduled_at, phase1_done, phase2_done, phase3_done) "
                        "VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
                        (
                            div_db_id,
                            r["round_number"],
                            r["format"].value,
                            r["track_name"],
                            r["scheduled_at"].isoformat(),
                        ),
                    )

            await db.commit()
        return new_season_id, season_number

    async def load_all_setup_seasons(self) -> list[dict]:
        """Return raw data for every SETUP-status season to rebuild PendingConfig on startup."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, season_number FROM seasons WHERE status = 'SETUP'"
            )
            season_rows = await cursor.fetchall()

            result: list[dict] = []
            for s_row in season_rows:
                season_id = s_row["id"]

                cursor = await db.execute(
                    "SELECT id, name, mention_role_id, forecast_channel_id, tier "
                    "FROM divisions WHERE season_id = ?",
                    (season_id,),
                )
                div_rows = await cursor.fetchall()

                divisions: list[dict] = []
                for d_row in div_rows:
                    cursor2 = await db.execute(
                        "SELECT round_number, format, track_name, scheduled_at "
                        "FROM rounds WHERE division_id = ? ORDER BY round_number",
                        (d_row["id"],),
                    )
                    round_rows = await cursor2.fetchall()
                    rounds = [
                        {
                            "round_number": r["round_number"],
                            "format": RoundFormat(r["format"]),
                            "track_name": r["track_name"],
                            "scheduled_at": datetime.fromisoformat(r["scheduled_at"]),
                        }
                        for r in round_rows
                    ]
                    divisions.append({
                        "name": d_row["name"],
                        "role_id": d_row["mention_role_id"],
                        "channel_id": d_row["forecast_channel_id"],
                        "tier": d_row["tier"] if "tier" in d_row.keys() else 0,
                        "rounds": rounds,
                    })

                result.append({
                    "season_id": season_id,
                    "server_id": s_row["server_id"],
                    "start_date": date.fromisoformat(s_row["start_date"]),
                    "season_number": s_row["season_number"] if "season_number" in s_row.keys() else 0,
                    "divisions": divisions,
                })

        return result

    async def increment_previous_season_number(self, server_id: int) -> None:
        """Increment server_configs.previous_season_number by 1."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE server_configs "
                "SET previous_season_number = previous_season_number + 1 "
                "WHERE server_id = ?",
                (server_id,),
            )
            await db.commit()

    async def validate_division_tiers(self, season_id: int) -> None:
        """Validate division tiers form a gapless sequence 1..N.

        Raises ValueError with a diagnostic message if any tier is missing.
        Cancelled divisions are excluded from the check.
        """
        divisions = await self.get_divisions(season_id)
        active_divs = [d for d in divisions if d.status != "CANCELLED"]
        if not active_divs:
            return
        tiers = sorted(d.tier for d in active_divs)
        expected = list(range(1, len(tiers) + 1))
        if tiers != expected:
            existing = sorted(set(tiers))
            missing = sorted(set(expected) - set(tiers))
            raise ValueError(
                f"Division tiers are not sequential. "
                f"Current tiers: {existing}. "
                f"Missing tier(s): {missing}."
            )

    async def get_last_scheduled_at(self, server_id: int) -> datetime | None:
        """Return the latest scheduled_at across all ACTIVE rounds for the active season."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT MAX(r.scheduled_at)
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.server_id = ? AND s.status = 'ACTIVE'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                """,
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0])

    async def all_phases_complete(self, server_id: int) -> bool:
        """True if every non-MYSTERY, non-CANCELLED round in the active season has all 3 phases done."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.server_id = ?
                  AND s.status    = 'ACTIVE'
                  AND r.format   != 'MYSTERY'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                  AND (r.phase1_done = 0 OR r.phase2_done = 0 OR r.phase3_done = 0)
                """,
                (server_id,),
            )
            row = await cursor.fetchone()
        return row is not None and row[0] == 0

    async def get_all_server_ids_with_active_season(self) -> list[int]:
        """Return all server_ids that currently have an ACTIVE season row."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT DISTINCT server_id FROM seasons WHERE status = 'ACTIVE'"
            )
            rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def transition_to_active(self, season_id: int) -> None:
        """Set season status to ACTIVE."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = ? WHERE id = ?",
                (SeasonStatus.ACTIVE.value, season_id),
            )
            await db.commit()

    async def delete_season(self, season_id: int) -> None:
        """FK-safe cascade delete of one season and all its child records."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM divisions WHERE season_id = ?", (season_id,)
            )
            division_rows = await cursor.fetchall()
            division_ids = [r[0] for r in division_rows]

            round_ids: list[int] = []
            if division_ids:
                ph = ",".join("?" * len(division_ids))
                cursor = await db.execute(
                    f"SELECT id FROM rounds WHERE division_id IN ({ph})",
                    division_ids,
                )
                round_ids = [r[0] for r in await cursor.fetchall()]

            # ── Results module: round-level children ────────────────────────
            if round_ids:
                ph = ",".join("?" * len(round_ids))
                await db.execute(f"DELETE FROM round_submission_channels WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM driver_standings_snapshots WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM team_standings_snapshots WHERE round_id IN ({ph})", round_ids)
                # driver_session_results has no direct FK to rounds — delete via session_results
                cursor = await db.execute(
                    f"SELECT id FROM session_results WHERE round_id IN ({ph})", round_ids
                )
                session_result_ids = [r[0] for r in await cursor.fetchall()]
                if session_result_ids:
                    sph = ",".join("?" * len(session_result_ids))
                    await db.execute(f"DELETE FROM driver_session_results WHERE session_result_id IN ({sph})", session_result_ids)
                await db.execute(f"DELETE FROM session_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM forecast_messages WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM phase_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)

            # ── Results module: season-level children ───────────────────────
            await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_amendment_state WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_fl WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_entries WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_links WHERE season_id = ?", (season_id,))

            # ── Driver/team children ────────────────────────────────────────
            if division_ids:
                ph = ",".join("?" * len(division_ids))

                # Collect fake (test-mode) driver profile IDs so we can delete
                # them after their FK references are cleared.
                cursor = await db.execute(
                    f"""
                    SELECT DISTINCT dp.id
                    FROM driver_profiles dp
                    JOIN driver_season_assignments dsa ON dsa.driver_profile_id = dp.id
                    WHERE dp.is_test_driver = 1
                      AND dsa.division_id IN ({ph})
                    """,
                    division_ids,
                )
                test_profile_ids = [r[0] for r in await cursor.fetchall()]

                await db.execute(f"DELETE FROM driver_season_assignments WHERE division_id IN ({ph})", division_ids)
                await db.execute(f"DELETE FROM division_results_config WHERE division_id IN ({ph})", division_ids)

                # team_seats → team_instances → divisions
                cursor = await db.execute(
                    f"SELECT id FROM team_instances WHERE division_id IN ({ph})", division_ids
                )
                team_instance_ids = [r[0] for r in await cursor.fetchall()]
                if team_instance_ids:
                    tiph = ",".join("?" * len(team_instance_ids))
                    await db.execute(f"DELETE FROM team_seats WHERE team_instance_id IN ({tiph})", team_instance_ids)
                await db.execute(f"DELETE FROM team_instances WHERE division_id IN ({ph})", division_ids)
                await db.execute(f"DELETE FROM rounds WHERE division_id IN ({ph})", division_ids)

                # Remove orphaned fake driver profiles (test-mode roster)
                if test_profile_ids:
                    tph = ",".join("?" * len(test_profile_ids))
                    await db.execute(f"DELETE FROM driver_profiles WHERE id IN ({tph})", test_profile_ids)

            await db.execute("DELETE FROM divisions WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM seasons WHERE id = ?", (season_id,))
            await db.commit()

    # ------------------------------------------------------------------
    # Division
    # ------------------------------------------------------------------

    async def add_division(
        self,
        season_id: int,
        name: str,
        mention_role_id: int,
        forecast_channel_id: int | None = None,
        tier: int = 0,
    ) -> Division:
        """Insert a division and return it."""
        if tier != 0:
            if tier < 1:
                raise ValueError(f"Tier must be >= 1, got {tier}.")
            async with get_connection(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT 1 FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
                if await cursor.fetchone():
                    raise ValueError(
                        f"A division with tier {tier} already exists in this season."
                    )
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO divisions
                    (season_id, name, mention_role_id, forecast_channel_id, tier)
                VALUES (?, ?, ?, ?, ?)
                """,
                (season_id, name, mention_role_id, forecast_channel_id, tier),
            )
            await db.commit()
            div_id = cursor.lastrowid

        return Division(
            id=div_id,
            season_id=season_id,
            name=name,
            mention_role_id=mention_role_id,
            forecast_channel_id=forecast_channel_id,
            tier=tier,
        )

    async def get_divisions(self, season_id: int) -> list[Division]:
        """Return all divisions for *season_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier "
                "FROM divisions WHERE season_id = ?",
                (season_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_division(r) for r in rows]

    async def set_division_forecast_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Update divisions.forecast_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT forecast_channel_id FROM divisions WHERE id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?",
                (channel_id, division_id),
            )
            await db.commit()
        return old_id

    async def set_division_results_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.results_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id",
                (division_id, channel_id),
            )
            await db.commit()
        return old_id

    async def set_division_standings_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.standings_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, standings_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET standings_channel_id = excluded.standings_channel_id",
                (division_id, channel_id),
            )
            await db.commit()
        return old_id

    async def get_divisions_with_results_config(
        self, season_id: int
    ) -> list[Division]:
        """Return divisions with results_channel_id and standings_channel_id populated
        via LEFT JOIN to division_results_config. Used by the approval gate."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT d.id, d.season_id, d.name, d.mention_role_id, d.forecast_channel_id,
                       d.status, d.tier,
                       drc.results_channel_id, drc.standings_channel_id
                FROM divisions d
                LEFT JOIN division_results_config drc ON drc.division_id = d.id
                WHERE d.season_id = ?
                """,
                (season_id,),
            )
            rows = await cursor.fetchall()
        result: list[Division] = []
        for r in rows:
            div = _row_to_division(r)
            div.results_channel_id = r["results_channel_id"]
            div.standings_channel_id = r["standings_channel_id"]
            result.append(div)
        return result

    async def rename_division(self, division_id: int, new_name: str) -> None:
        """Update a division's name."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE divisions SET name = ? WHERE id = ?",
                (new_name, division_id),
            )
            await db.commit()

    async def delete_division(self, division_id: int) -> None:
        """Cascade-delete a division and all its child rows."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM rounds WHERE division_id = ?", (division_id,)
            )
            round_rows = await cursor.fetchall()
            round_ids = [r[0] for r in round_rows]

            if round_ids:
                ph = ",".join("?" * len(round_ids))
                await db.execute(f"DELETE FROM forecast_messages WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM phase_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM rounds WHERE division_id = ?", (division_id,))

            await db.execute("DELETE FROM divisions WHERE id = ?", (division_id,))
            await db.commit()

    async def cancel_division(
        self,
        division_id: int,
        server_id: int,
        actor_id: int,
        actor_name: str,
    ) -> None:
        """Mark a division CANCELLED and write an audit entry."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE divisions SET status = 'CANCELLED' WHERE id = ?",
                (division_id,),
            )
            await db.execute(
                """
                INSERT INTO audit_entries
                    (server_id, actor_id, actor_name, division_id, change_type,
                     old_value, new_value, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    actor_id,
                    actor_name,
                    division_id,
                    "division.status",
                    "ACTIVE",
                    "CANCELLED",
                    now.isoformat(),
                ),
            )
            await db.commit()

    async def duplicate_division(
        self,
        division_id: int,
        name: str,
        role_id: int,
        forecast_channel_id: int | None = None,
        day_offset: int = 0,
        hour_offset: float = 0.0,
        tier: int = 0,
    ) -> Division:
        """Copy a division (and all its rounds with shifted datetimes) into a new division."""
        from datetime import timedelta
        src_rounds = await self.get_division_rounds(division_id)
        async with get_connection(self._db_path) as db:
            # Find the season_id of the source division
            cursor = await db.execute(
                "SELECT season_id FROM divisions WHERE id = ?", (division_id,)
            )
            row = await cursor.fetchone()
            season_id: int = row[0]

            if tier != 0:
                if tier < 1:
                    raise ValueError(f"Tier must be >= 1, got {tier}.")
                cursor = await db.execute(
                    "SELECT 1 FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
                if await cursor.fetchone():
                    raise ValueError(
                        f"A division with tier {tier} already exists in this season."
                    )

            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier)"
                " VALUES (?, ?, ?, ?, ?)",
                (season_id, name, role_id, forecast_channel_id, tier),
            )
            await db.commit()
            new_div_id: int = cursor.lastrowid  # type: ignore[assignment]

            delta = timedelta(days=day_offset, hours=hour_offset)
            for rnd in src_rounds:
                new_dt = rnd.scheduled_at + delta
                await db.execute(
                    "INSERT INTO rounds"
                    " (division_id, round_number, format, track_name, scheduled_at,"
                    "  phase1_done, phase2_done, phase3_done)"
                    " VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
                    (
                        new_div_id,
                        rnd.round_number,  # will be renumbered next
                        rnd.format.value,
                        rnd.track_name,
                        new_dt.isoformat(),
                    ),
                )
            await db.commit()

        await self.renumber_rounds(new_div_id)

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier"
                " FROM divisions WHERE id = ?",
                (new_div_id,),
            )
            row = await cursor.fetchone()
        return _row_to_division(row)

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
                "phase1_done, phase2_done, phase3_done, status, finalized FROM rounds WHERE id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()
        return _row_to_round(row) if row else None

    async def get_division_rounds(self, division_id: int) -> list[Round]:
        """Return all rounds for *division_id* ordered by round_number."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "phase1_done, phase2_done, phase3_done, status, finalized FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_round(r) for r in rows]

    async def renumber_rounds(self, division_id: int) -> None:
        """Rewrite round_number for all rounds in a division, sorted ascending by scheduled_at."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM rounds WHERE division_id = ? ORDER BY scheduled_at",
                (division_id,),
            )
            rows = await cursor.fetchall()
            for i, row in enumerate(rows, start=1):
                await db.execute(
                    "UPDATE rounds SET round_number = ? WHERE id = ?",
                    (i, row[0]),
                )
            await db.commit()

    async def delete_round(self, round_id: int) -> None:
        """Delete a round and renumber siblings."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT division_id FROM rounds WHERE id = ?", (round_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return
            division_id: int = row[0]

            await db.execute("DELETE FROM forecast_messages WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM phase_results WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM sessions WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM rounds WHERE id = ?", (round_id,))
            await db.commit()

        await self.renumber_rounds(division_id)

    async def cancel_round(
        self,
        round_id: int,
        server_id: int,
        actor_id: int,
        actor_name: str,
    ) -> None:
        """Mark a round CANCELLED and write an audit entry."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT division_id FROM rounds WHERE id = ?", (round_id,)
            )
            row = await cursor.fetchone()
            division_id = row[0] if row else None

            await db.execute(
                "UPDATE rounds SET status = 'CANCELLED' WHERE id = ?",
                (round_id,),
            )
            await db.execute(
                """
                INSERT INTO audit_entries
                    (server_id, actor_id, actor_name, division_id, change_type,
                     old_value, new_value, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    actor_id,
                    actor_name,
                    division_id,
                    "round.status",
                    "ACTIVE",
                    "CANCELLED",
                    now.isoformat(),
                ),
            )
            await db.commit()

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
        season_number=row["season_number"] if "season_number" in row.keys() else 0,
    )


def _row_to_division(row: object) -> Division:
    return Division(
        id=row["id"],
        season_id=row["season_id"],
        name=row["name"],
        mention_role_id=row["mention_role_id"],
        forecast_channel_id=row["forecast_channel_id"],
        status=row["status"],
        tier=row["tier"] if "tier" in row.keys() else 0,
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
        status=row["status"],
        finalized=bool(row["finalized"]),
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
