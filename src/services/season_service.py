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


class SeasonImmutableError(Exception):
    """Raised when a mutation is attempted on a COMPLETED (archived) season."""


class SeasonService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # Drivers displaced by the most recent save_pending_snapshot — real and mock
        # alike — awaiting restore_driver_seats once the caller has re-seeded the teams.
        self._pending_driver_seats: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # Season
    # ------------------------------------------------------------------

    async def create_season(self, server_id: int, start_date: date | None = None) -> Season:
        """Insert a new SETUP season and return it.

        Raises ``sqlite3.IntegrityError`` where the server already holds a live season:
        migration 049 permits one SETUP-or-ACTIVE row per server, and this method applies
        none of the checks `/season setup` makes before its own writes. Nothing in
        ``src/`` calls it — season setup writes its season through
        :meth:`save_pending_snapshot` — so any new caller wants those checks first.
        """
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

    async def get_setup_or_active_season(self, server_id: int) -> Season | None:
        """Return the live (SETUP or ACTIVE) season for *server_id*, or None.

        A server holds at most one, enforced by the partial unique index migration 049
        builds, so the two states cannot both be present and there is nothing to choose
        between. The ordering is stated anyway: this was a bare ``LIMIT 1`` over both
        states, which returned an uncontracted row wherever the invariant had been
        broken, and matching :meth:`get_previewable_season` keeps every reader of a
        league's current season agreeing on which one that is.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, status, season_number FROM seasons "
                "WHERE server_id = ? AND status IN ('SETUP', 'ACTIVE') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def get_previewable_season(self, server_id: int) -> Season | None:
        """Return the season an `/images test` preview draws, or None.

        The server's one live season — SETUP or ACTIVE. A COMPLETED or CANCELLED season is
        never previewable: a preview is a check on what the league is running or about to
        run, and a server keeps its whole archive besides.

        A server holds at most one live season (migration 049), so the ACTIVE-before-SETUP
        ordering below no longer arbitrates anything and is kept as defence: it costs
        nothing, and it means this and every other reader of "the season of this server"
        answer the same row even on a database that predates the constraint.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, status, season_number FROM seasons "
                "WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def get_previous_season_number(self, server_id: int) -> int:
        """The highest season number *server_id* has already committed, or 0.

        "Committed" means the number has been issued and cannot be re-used — every status
        but SETUP. A season still in setup holds a provisional number and is excluded, so
        that a league drafting its next season does not push the count forward twice.

        Not `server_configs.previous_season_number`, which is written by nothing and reads
        0 on every server whatever its history.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT MAX(season_number) AS highest FROM seasons "
                "WHERE server_id = ? AND status != 'SETUP'",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None or row["highest"] is None:
            return 0
        return int(row["highest"])

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

    async def has_active_or_setup_season(self, server_id: int) -> bool:
        """Return True if an ACTIVE or SETUP season exists for *server_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP') LIMIT 1",
                (server_id,),
            )
            row = await cursor.fetchone()
        return row is not None

    async def count_completed_seasons(self, server_id: int) -> int:
        """Return the count of COMPLETED seasons for *server_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(id) FROM seasons WHERE server_id = ? AND status = 'COMPLETED'",
                (server_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def count_persisted_seasons(self, server_id: int) -> int:
        """Return the count of all persisted (non-SETUP) seasons for *server_id*.

        Includes ACTIVE, COMPLETED, and CANCELLED seasons — every season whose
        number has already been committed.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(id) FROM seasons WHERE server_id = ? AND status != 'SETUP'",
                (server_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def complete_season(self, season_id: int) -> None:
        """Transition a season to COMPLETED (archive it in-place)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = 'COMPLETED' WHERE id = ?",
                (season_id,),
            )
            await db.commit()

    async def cancel_season(self, season_id: int) -> None:
        """Transition a season to CANCELLED (immutable, all data preserved)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = 'CANCELLED' WHERE id = ?",
                (season_id,),
            )
            await db.commit()

    async def assert_season_mutable(self, season: "Season") -> None:
        """Raise SeasonImmutableError if *season* is COMPLETED or CANCELLED."""
        if season.status in (SeasonStatus.COMPLETED, SeasonStatus.CANCELLED):
            raise SeasonImmutableError(
                f"Season {season.season_number} is archived and cannot be modified."
            )

    async def save_pending_snapshot(
        self,
        server_id: int,
        start_date: date,
        existing_season_id: int,
        divisions: list[dict],
        game_edition: int = 0,
    ) -> tuple[int, int]:
        """Atomically replace the SETUP season snapshot for *server_id* in the DB.

        Deletes the previous SETUP season (if *existing_season_id* is non-zero)
        and re-inserts the full pending config.  Sessions are NOT created here —
        they are created at approve time.

        **Everything not held in the PendingConfig must be carried across by hand.**
        The rebuild drops every division, team, seat and round and re-inserts them with
        new row ids, so anything hanging off the old ids is lost unless it is saved
        before the teardown and restored after it, keyed by division *name* rather than
        by id. What that covers, audited 2026-09-04:

        - the divisions row itself — name, role, tier and the weather forecast channel
          come back through the PendingConfig; ``lineup_channel_id`` and
          ``calendar_channel_id`` are saved and restored here;
        - ``division_results_config`` — results, standings and penalty channels, and
          the reserves-in-standings flag;
        - ``attendance_division_config`` — the RSVP and attendance channels;
        - ``season_points_links`` — the points configurations attached to the season;
        - every seated driver, real and mock alike, by team name and seat number.

        Three things are deliberately **not** carried, each harmless: ``lineup_message_id``
        and ``calendar_message_id`` (nothing has been posted for a season still in setup,
        so they are null); ``signup_division_config`` (two keys and a vestigial column,
        recreated with ``INSERT OR IGNORE`` whenever it is next wanted); and
        ``season_amendment_state`` (an amendment runs against a season that is already
        approved, which this never rewrites).

        A new per-division or per-season setting added anywhere in the bot belongs in
        the list above, or it will be silently destroyed by the next `/round add`.

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
                # First snapshot: season_number = count of all persisted seasons + 1.
                # Persisted seasons (ACTIVE, COMPLETED, CANCELLED) have already used their
                # number, so the next season is simply one higher than that tally.
                season_number = await self.count_persisted_seasons(server_id) + 1

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
                # division name → seated mock drivers, restored once the new
                # divisions and their teams exist again.
                seats_by_division: dict[str, list[dict]] = {}
                for div_row in div_rows:
                    old_div_id = div_row[0]
                    cursor2 = await db.execute(
                        "SELECT name, lineup_channel_id, calendar_channel_id "
                        "FROM divisions WHERE id = ?",
                        (old_div_id,),
                    )
                    name_row = await cursor2.fetchone()
                    if name_row:
                        saved_div_names[old_div_id] = name_row[0]
                        saved_channel_cfg[old_div_id] = {
                            "lineup_channel_id": name_row[1],
                            "calendar_channel_id": name_row[2],
                        }
                    cursor2 = await db.execute(
                        "SELECT results_channel_id, standings_channel_id, "
                        "reserves_in_standings, penalty_channel_id "
                        "FROM division_results_config WHERE division_id = ?",
                        (old_div_id,),
                    )
                    cfg_row = await cursor2.fetchone()
                    if cfg_row:
                        saved_channel_cfg.setdefault(old_div_id, {}).update({
                            "results_channel_id": cfg_row[0],
                            "standings_channel_id": cfg_row[1],
                            "reserves_in_standings": cfg_row[2],
                            "penalty_channel_id": cfg_row[3],
                        })
                    cursor2 = await db.execute(
                        "SELECT rsvp_channel_id, attendance_channel_id "
                        "FROM attendance_division_config WHERE division_id = ?",
                        (old_div_id,),
                    )
                    att_row = await cursor2.fetchone()
                    if att_row:
                        saved_channel_cfg.setdefault(old_div_id, {}).update({
                            "rsvp_channel_id": att_row[0],
                            "attendance_channel_id": att_row[1],
                        })

                # name → channel config (for lookup when new division IDs are known)
                channels_by_name: dict[str, dict] = {
                    saved_div_names[did]: cfg
                    for did, cfg in saved_channel_cfg.items()
                    if did in saved_div_names
                }

                # Save attached points config names before the season row is deleted
                # (season_points_links has ON DELETE CASCADE so they disappear with it).
                cursor = await db.execute(
                    "SELECT config_name FROM season_points_links "
                    "WHERE season_id = ? ORDER BY config_name",
                    (existing_season_id,),
                )
                saved_config_names: list[str] = [
                    r[0] for r in await cursor.fetchall()
                ]

                # Cascade-delete the old SETUP season manually (no ON DELETE CASCADE on
                # seasons/divisions, though division_results_config does have it).
                # Clean up test drivers, driver_season_assignments, team_instances/team_seats
                # first to avoid FK violations.
                for div_row in div_rows:
                    old_div_id = div_row[0]

                    # Every driver seated in this division, mock or real. The rows they
                    # sit in are about to be deleted and re-created with new ids, so the
                    # placement is recorded by name — division, team, seat number — and
                    # restored once the new rows exist.
                    #
                    # Real drivers are captured for exactly the same reason mock ones
                    # are, and the distinction that used to be drawn here was a defect:
                    # `is_test_driver = 1` was captured and restored while a real
                    # driver's assignment was deleted by division and restored by
                    # nothing. A league that placed its grid during SETUP and then ran
                    # `/round add` had every placement silently destroyed — the profile
                    # surviving, so nobody appeared to be missing, while the seat and the
                    # assignment were gone. A season-setup command shapes divisions and rounds;
                    # it must not unplace anybody.
                    cursor2 = await db.execute(
                        """
                        SELECT dp.id     AS profile_id,
                               ti.name   AS team_name,
                               ts.seat_number
                        FROM driver_profiles dp
                        JOIN team_seats ts ON ts.driver_profile_id = dp.id
                        JOIN team_instances ti ON ti.id = ts.team_instance_id
                        WHERE ti.division_id = ?
                        """,
                        (old_div_id,),
                    )
                    seated_rows = await cursor2.fetchall()
                    if seated_rows:
                        div_name = saved_div_names.get(old_div_id)
                        if div_name is not None:
                            seats_by_division.setdefault(div_name, []).extend(
                                {
                                    "profile_id": r["profile_id"],
                                    "team_name": r["team_name"],
                                    "seat_number": r["seat_number"],
                                }
                                for r in seated_rows
                            )
                        seated_ids = [r["profile_id"] for r in seated_rows]
                        ph = ",".join("?" * len(seated_ids))
                        await db.execute(
                            f"UPDATE team_seats SET driver_profile_id = NULL "
                            f"WHERE driver_profile_id IN ({ph})",
                            seated_ids,
                        )

                    # The assignments of this division go with the division row. Those of
                    # a seated driver are re-created by restore_driver_seats; one
                    # belonging to a driver who sat in no seat has nothing to restore it
                    # to, and is the same row the division's deletion would orphan.
                    await db.execute(
                        "DELETE FROM driver_season_assignments WHERE division_id = ?",
                        (old_div_id,),
                    )

                    cursor2 = await db.execute(
                        "SELECT id FROM team_instances WHERE division_id = ?",
                        (old_div_id,),
                    )
                    inst_rows = await cursor2.fetchall()
                    for inst_row in inst_rows:
                        await db.execute(
                            "DELETE FROM team_seats WHERE team_instance_id = ?",
                            (inst_row[0],),
                        )
                    await db.execute(
                        "DELETE FROM team_instances WHERE division_id = ?", (old_div_id,)
                    )
                    await db.execute(
                        "DELETE FROM rounds WHERE division_id = ?", (old_div_id,)
                    )
                await db.execute(
                    "DELETE FROM divisions WHERE season_id = ?", (existing_season_id,)
                )
                await db.execute(
                    "DELETE FROM seasons WHERE id = ?", (existing_season_id,)
                )
            else:
                channels_by_name = {}
                saved_config_names = []
                seats_by_division = {}

            cursor = await db.execute(
                "INSERT INTO seasons (server_id, start_date, status, season_number, game_edition) "
                "VALUES (?, ?, 'SETUP', ?, ?)",
                (server_id, start_date.isoformat(), season_number, game_edition),
            )
            new_season_id: int = cursor.lastrowid  # type: ignore[assignment]

            # Restore season-level points config attachments under the new season ID.
            for config_name in saved_config_names:
                await db.execute(
                    "INSERT OR IGNORE INTO season_points_links (season_id, config_name) "
                    "VALUES (?, ?)",
                    (new_season_id, config_name),
                )

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

                # Restore any previously-assigned channel config for this division
                # (by name), which would otherwise be lost because
                # save_pending_snapshot deletes and re-creates division rows.
                saved = channels_by_name.get(div_data["name"])
                if saved:
                    # lineup / calendar channels live directly on the divisions row
                    if saved.get("lineup_channel_id") or saved.get("calendar_channel_id"):
                        await db.execute(
                            "UPDATE divisions SET lineup_channel_id = ?, calendar_channel_id = ? "
                            "WHERE id = ?",
                            (saved.get("lineup_channel_id"), saved.get("calendar_channel_id"), div_db_id),
                        )
                    # results / standings / penalty config row
                    if (
                        saved.get("results_channel_id")
                        or saved.get("standings_channel_id")
                        or saved.get("penalty_channel_id")
                    ):
                        await db.execute(
                            "INSERT INTO division_results_config "
                            "(division_id, results_channel_id, standings_channel_id, "
                            " reserves_in_standings, penalty_channel_id) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                div_db_id,
                                saved.get("results_channel_id"),
                                saved.get("standings_channel_id"),
                                saved.get("reserves_in_standings") if saved.get("reserves_in_standings") is not None else 1,
                                saved.get("penalty_channel_id"),
                            ),
                        )
                    # attendance channels (cascade-deleted with old division row)
                    if saved.get("rsvp_channel_id") or saved.get("attendance_channel_id"):
                        await db.execute(
                            "INSERT INTO attendance_division_config "
                            "(division_id, server_id, rsvp_channel_id, attendance_channel_id) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                div_db_id,
                                server_id,
                                saved.get("rsvp_channel_id"),
                                saved.get("attendance_channel_id"),
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

        # The teams a mock driver was seated in do not exist yet — the caller re-seeds
        # them from the server defaults once this returns. Reseating therefore waits for
        # restore_driver_seats(), which the caller invokes at that point.
        self._pending_driver_seats = seats_by_division
        return new_season_id, season_number

    async def restore_driver_seats(self, season_id: int) -> None:
        """Reseat the drivers that the last snapshot of *season_id* displaced.

        Pairs with save_pending_snapshot: it releases the seat of every driver in a SETUP
        season before deleting the division rows they hang off, and this puts them back
        once the divisions and their teams have been re-created. Matching is by division
        name, team name and seat number, the same way channel configuration is carried
        across the rebuild — the row IDs are all new.

        **Real and mock drivers alike.** The snapshot used to capture only
        ``is_test_driver = 1`` while deleting a real driver's assignment by division and
        restoring nothing, so a league that placed its grid during SETUP lost every
        placement to the next `/round add` — silently, the profile surviving while the
        seat and the assignment did not.

        A driver whose team or seat no longer exists (the league manager renamed the
        team, or shrank it) is left unseated rather than moved somewhere arbitrary. That
        is a placement the manager must make again, and it is the one case this cannot
        carry across; it is never a deletion of the driver.
        """
        seats_by_division = self._pending_driver_seats
        if not seats_by_division:
            return
        self._pending_driver_seats = {}

        async with get_connection(self._db_path) as db:
            for div_name, seats in seats_by_division.items():
                cursor = await db.execute(
                    "SELECT id FROM divisions WHERE season_id = ? AND name = ?",
                    (season_id, div_name),
                )
                div_row = await cursor.fetchone()
                if div_row is None:
                    continue
                div_id = div_row[0]

                for seat in seats:
                    cursor = await db.execute(
                        "SELECT id, is_reserve FROM team_instances "
                        "WHERE division_id = ? AND LOWER(name) = LOWER(?)",
                        (div_id, seat["team_name"]),
                    )
                    team_row = await cursor.fetchone()
                    if team_row is None:
                        continue
                    team_id, is_reserve = team_row[0], bool(team_row[1])

                    cursor = await db.execute(
                        "SELECT id FROM team_seats "
                        "WHERE team_instance_id = ? AND seat_number = ? "
                        "  AND driver_profile_id IS NULL",
                        (team_id, seat["seat_number"]),
                    )
                    seat_row = await cursor.fetchone()
                    if seat_row is None:
                        if not is_reserve:
                            continue
                        # The reserve team carries no fixed seat count, so its seats are
                        # re-created on demand exactly as add_test_driver creates them.
                        cursor = await db.execute(
                            "INSERT INTO team_seats "
                            "(team_instance_id, seat_number, driver_profile_id) "
                            "VALUES (?, ?, NULL)",
                            (team_id, seat["seat_number"]),
                        )
                        seat_id = cursor.lastrowid
                    else:
                        seat_id = seat_row[0]

                    await db.execute(
                        "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
                        (seat["profile_id"], seat_id),
                    )
                    await db.execute(
                        "INSERT INTO driver_season_assignments "
                        "(driver_profile_id, season_id, division_id, team_seat_id) "
                        "VALUES (?, ?, ?, ?)",
                        (seat["profile_id"], season_id, div_id, seat_id),
                    )
            await db.commit()

    async def load_all_setup_seasons(self) -> list[dict]:
        """Return raw data for every SETUP-status season to rebuild PendingConfig on startup."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, start_date, season_number, game_edition FROM seasons WHERE status = 'SETUP'"
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
                    "game_edition": s_row["game_edition"] if "game_edition" in s_row.keys() else 0,
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

    async def all_rounds_finalized(self, server_id: int) -> bool:
        """True if every non-CANCELLED round in every non-CANCELLED division of the active season has finalized=1."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.server_id = ?
                  AND s.status    = 'ACTIVE'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                  AND r.finalized = 0
                """,
                (server_id,),
            )
            row = await cursor.fetchone()
        return row is not None and row[0] == 0

    async def get_unfinalized_rounds(self, server_id: int) -> list[dict]:
        """Return name, round_number, and division for every non-CANCELLED unfinalized round in the active season."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT d.name AS division, r.round_number, r.track_name
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.server_id = ?
                  AND s.status    = 'ACTIVE'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                  AND r.finalized = 0
                ORDER BY d.name, r.round_number
                """,
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

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
                    await db.execute(f"DELETE FROM race_session_results WHERE session_result_id IN ({sph})", session_result_ids)
                    await db.execute(f"DELETE FROM qualifying_session_results WHERE session_result_id IN ({sph})", session_result_ids)
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

    async def get_previewable_divisions(
        self, server_id: int, *, timeout: float | None = None
    ) -> list[Division]:
        """The divisions of the season an `/images test` preview draws, in one connection.

        Exactly `get_previewable_season` followed by `get_divisions`, which is what the
        preview autocomplete used to call in turn — two `aiosqlite.connect` opens, two
        `PRAGMA foreign_keys`, two closes, on a path with three seconds to answer Discord in.
        Doing both on one connection halves that, and the second query is only reached when
        the first found a season.

        Empty where the server holds no previewable season, which is the same answer the
        pair gave and is why the preview's division parameter is optional.

        *timeout* is passed to the connection as its lock wait; pass
        `AUTOCOMPLETE_TIMEOUT_SECONDS` on the autocomplete path so a contended database
        gives up well inside Discord's budget rather than answering into a dead token.

        Both original methods are left untouched and still used elsewhere; this is an
        addition, not a replacement.
        """
        async with get_connection(self._db_path, timeout=timeout) as db:
            cursor = await db.execute(
                "SELECT id FROM seasons "
                "WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                (server_id,),
            )
            season_row = await cursor.fetchone()
            if season_row is None:
                return []

            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier, "
                "lineup_channel_id, calendar_channel_id, lineup_message_id, "
                "calendar_message_id "
                "FROM divisions WHERE season_id = ? ORDER BY tier",
                (season_row[0],),
            )
            rows = await cursor.fetchall()
        return [_row_to_division(r) for r in rows]

    async def get_divisions(self, season_id: int) -> list[Division]:
        """Return all divisions for *season_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier, "
                "lineup_channel_id, calendar_channel_id, lineup_message_id, "
                "calendar_message_id "
                "FROM divisions WHERE season_id = ? ORDER BY tier",
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

    async def set_division_penalty_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.penalty_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, penalty_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET penalty_channel_id = excluded.penalty_channel_id",
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
                       d.lineup_channel_id, d.calendar_channel_id, d.lineup_message_id,
                       d.calendar_message_id,
                       drc.results_channel_id, drc.standings_channel_id,
                       drc.penalty_channel_id
                FROM divisions d
                LEFT JOIN division_results_config drc ON drc.division_id = d.id
                WHERE d.season_id = ?
                ORDER BY d.tier
                """,
                (season_id,),
            )
            rows = await cursor.fetchall()
        result: list[Division] = []
        for r in rows:
            div = _row_to_division(r)
            div.results_channel_id = r["results_channel_id"]
            div.standings_channel_id = r["standings_channel_id"]
            div.penalty_channel_id = r["penalty_channel_id"]
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

            # team_seats → team_instances: no cascade, must be deleted manually
            await db.execute(
                "DELETE FROM team_seats WHERE team_instance_id IN "
                "(SELECT id FROM team_instances WHERE division_id = ?)",
                (division_id,),
            )
            await db.execute("DELETE FROM team_instances WHERE division_id = ?", (division_id,))
            # driver_season_assignments.division_id has no cascade
            await db.execute(
                "DELETE FROM driver_season_assignments WHERE division_id = ?", (division_id,)
            )
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
                """
                SELECT r.division_id, s.status AS season_status
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons s ON s.id = d.season_id
                WHERE r.id = ?
                """,
                (round_id,),
            )
            row = await cursor.fetchone()
            division_id = row["division_id"] if row else None

            if row and row["season_status"] in ("COMPLETED", "CANCELLED"):
                raise SeasonImmutableError(
                    f"Round {round_id} belongs to an archived season and cannot be cancelled."
                )

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
        game_edition=row["game_edition"] if "game_edition" in row.keys() else 0,
    )


def _row_to_division(row: object) -> Division:
    keys = row.keys()
    return Division(
        id=row["id"],
        season_id=row["season_id"],
        name=row["name"],
        mention_role_id=row["mention_role_id"],
        forecast_channel_id=row["forecast_channel_id"],
        status=row["status"],
        tier=row["tier"] if "tier" in keys else 0,
        lineup_channel_id=row["lineup_channel_id"] if "lineup_channel_id" in keys else None,
        calendar_channel_id=row["calendar_channel_id"] if "calendar_channel_id" in keys else None,
        lineup_message_id=row["lineup_message_id"] if "lineup_message_id" in keys else None,
        # Guarded like every other optional column, so a database that has not yet run
        # migration 040 still loads its divisions rather than raising.
        calendar_message_id=(
            row["calendar_message_id"] if "calendar_message_id" in keys else None
        ),
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
