"""SignupModuleService — CRUD for signup module tables."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from db.database import get_connection
from models.signup_module import (
    AvailabilitySlot,
    ConfigSnapshot,
    SignupDivisionConfig,
    SignupModuleConfig,
    SignupModuleSettings,
    SignupRecord,
    SignupWizardRecord,
    WizardState,
)

log = logging.getLogger(__name__)


class SignupModuleService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Config ────────────────────────────────────────────────────────

    async def get_config(self) -> SignupModuleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signup_channel_id, base_role_id, signed_up_role_id, "
                "       signups_open, signup_button_message_id, selected_tracks_json, "
                "       signup_closed_message_id, close_at "
                "FROM signup_module_config",
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SignupModuleConfig(
            signup_channel_id=row["signup_channel_id"],
            base_role_id=row["base_role_id"],
            signed_up_role_id=row["signed_up_role_id"],
            signups_open=bool(row["signups_open"]),
            signup_button_message_id=row["signup_button_message_id"],
            selected_tracks=json.loads(row["selected_tracks_json"] or "[]"),
            signup_closed_message_id=row["signup_closed_message_id"],
            close_at=row["close_at"],
        )

    async def save_config(self, cfg: SignupModuleConfig) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_config
                    (id, signup_channel_id, base_role_id, signed_up_role_id,
                     signups_open, signup_button_message_id, selected_tracks_json,
                     signup_closed_message_id, close_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    signup_channel_id          = excluded.signup_channel_id,
                    base_role_id               = excluded.base_role_id,
                    signed_up_role_id          = excluded.signed_up_role_id,
                    signups_open               = excluded.signups_open,
                    signup_button_message_id   = excluded.signup_button_message_id,
                    selected_tracks_json       = excluded.selected_tracks_json,
                    signup_closed_message_id   = excluded.signup_closed_message_id,
                    close_at                   = excluded.close_at
                """,
                (
                    1,
                    cfg.signup_channel_id,
                    cfg.base_role_id,
                    cfg.signed_up_role_id,
                    int(cfg.signups_open),
                    cfg.signup_button_message_id,
                    json.dumps(cfg.selected_tracks),
                    cfg.signup_closed_message_id,
                    cfg.close_at,
                ),
            )
            await db.commit()

    async def delete_config(self) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM signup_module_config",
            )
            await db.commit()

    # ── Settings ──────────────────────────────────────────────────────

    async def get_settings(self) -> SignupModuleSettings:
        """Return settings row; if missing, return defaults."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT nationality_required, time_type, time_image_required "
                "FROM signup_module_settings",
            )
            row = await cursor.fetchone()
        if row is None:
            return SignupModuleSettings(
                nationality_required=True,
                time_type="TIME_TRIAL",
                time_image_required=True,
            )
        return SignupModuleSettings(
            nationality_required=bool(row["nationality_required"]),
            time_type=row["time_type"],
            time_image_required=bool(row["time_image_required"]),
        )

    async def save_settings(self, settings: SignupModuleSettings) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_settings
                    (id, nationality_required, time_type, time_image_required)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    nationality_required = excluded.nationality_required,
                    time_type            = excluded.time_type,
                    time_image_required  = excluded.time_image_required
                """,
                (
                    1,
                    int(settings.nationality_required),
                    settings.time_type,
                    int(settings.time_image_required),
                ),
            )
            await db.commit()

    # ── Availability slots ────────────────────────────────────────────

    async def get_slots(self) -> list[AvailabilitySlot]:
        """Return slots ordered chronologically (Mon→Sun, time asc).

        ``slot_sequence_id`` is the display ordinal, always 1..N in that order, and is
        therefore **recomputed on every call** — it changes whenever the slot list does.
        ``slot_id`` is the durable identity and is what a driver's recorded availability
        is stored against; see ``AvailabilitySlot``.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, day_of_week, time_hhmm "
                "FROM signup_availability_slots "
                " "
                "ORDER BY day_of_week ASC, time_hhmm ASC",
            )
            rows = await cursor.fetchall()
        return [
            AvailabilitySlot(
                id=row["id"],
                slot_id=AvailabilitySlot.make_slot_id(row["day_of_week"], row["time_hhmm"]),
                slot_sequence_id=i,
                day_of_week=row["day_of_week"],
                time_hhmm=row["time_hhmm"],
                display_label=AvailabilitySlot.make_label(row["day_of_week"], row["time_hhmm"]),
            )
            for i, row in enumerate(rows, start=1)
        ]

    async def add_slot(self, day_of_week: int, time_hhmm: str) -> AvailabilitySlot:
        """Insert a slot; raises ValueError on duplicate.

        Nothing is renumbered. The display ordinals of later slots do shift, because
        they are chronological positions computed on read, but no driver's recorded
        availability moves with them — it names the slot itself (issue #126).
        """
        async with get_connection(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO signup_availability_slots "
                    "(day_of_week, time_hhmm) "
                    "VALUES (?, ?)",
                    (day_of_week, time_hhmm),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError(
                        f"Slot already exists: day={day_of_week} time={time_hhmm}"
                    ) from exc
                raise
            await db.commit()

        # Fetch the newly assigned sequence ID for the inserted slot
        slots = await self.get_slots()
        inserted = next(
            (s for s in slots if s.day_of_week == day_of_week and s.time_hhmm == time_hhmm),
            None,
        )
        assert inserted is not None
        return inserted

    async def remove_slot_by_rank(self, slot_id: int) -> bool:
        """Remove the slot at chronological rank slot_id. Returns False if not found.

        The rank is the display ordinal a league types, not anything stored. Removing a
        slot renumbers nothing: every remaining slot keeps its durable identity, so the
        answers of drivers who chose them still mean the same times.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM signup_availability_slots "
                " "
                "ORDER BY day_of_week ASC, time_hhmm ASC",
            )
            rows = await cursor.fetchall()
            if slot_id < 1 or slot_id > len(rows):
                return False
            target_id = rows[slot_id - 1]["id"]
            await db.execute(
                "DELETE FROM signup_availability_slots WHERE id = ?",
                (target_id,),
            )
            await db.commit()
        return True

    # ── Window state helpers ──────────────────────────────────────────

    async def get_window_state(self) -> bool:
        """Return True if signups are currently open."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signups_open FROM signup_module_config",
            )
            row = await cursor.fetchone()
        return bool(row["signups_open"]) if row else False

    async def set_window_open(
        self, button_message_id: int, selected_tracks: list[str]
    ) -> None:
        """Open the window, and record it as a window of the server's active season.

        The record is what a signup made through this window is kept against (issue #220):
        the tracks it asked times for, and later its close time. A server with no active
        season records none — `/signup open` refuses there, so only a caller outside that
        command reaches this without one.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config "
                "SET signups_open = 1, signup_button_message_id = ?, selected_tracks_json = ?, "
                "    signup_closed_message_id = NULL "
                "",
                (button_message_id, json.dumps(selected_tracks)),
            )
            season_id = await self._live_season_id(db)
            if season_id is not None:
                await db.execute(
                    "INSERT INTO signup_windows (season_id, selected_tracks_json) "
                    "VALUES (?, ?)",
                    (season_id, json.dumps(selected_tracks)),
                )
            await db.commit()

    @staticmethod
    async def _live_season_id(db) -> int | None:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE status IN ('SETUP', 'ACTIVE') "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        return int(row["id"]) if row is not None else None

    async def get_windows(self, season_id: int) -> list[dict]:
        """Every signup window *season_id* opened, oldest first."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, selected_tracks_json, close_at, opened_at, closed_at "
                "FROM signup_windows WHERE season_id = ? ORDER BY id",
                (season_id,),
            )
            rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "season_id": row["season_id"],
                "selected_tracks": json.loads(row["selected_tracks_json"]),
                "close_at": row["close_at"],
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
            }
            for row in rows
        ]

    async def set_window_closed(
        self, *, closed_msg_id: int | None = None
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config "
                "SET signups_open = 0, signup_button_message_id = NULL, "
                "    signup_closed_message_id = ?, close_at = NULL "
                "",
                (closed_msg_id,),
            )
            # The window record keeps the close time it carried; only its closing is stamped.
            await db.execute(
                "UPDATE signup_windows SET closed_at = datetime('now') "
                "WHERE closed_at IS NULL",
            )
            await db.commit()

    async def set_close_at(self, close_at_iso: str | None) -> None:
        """Persist (or clear) the auto-close ISO 8601 UTC timestamp."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config SET close_at = ?",
                (close_at_iso,),
            )
            await db.execute(
                "UPDATE signup_windows SET close_at = ? "
                "WHERE closed_at IS NULL",
                (close_at_iso,),
            )
            await db.commit()

    async def snapshot_season_config(self, season_id: int) -> None:
        """Keep the signup configuration *season_id* is confirmed with (issue #220).

        Taken when the season's configuration is confirmed, from which point the module's
        settings are fixed until the season ends, so the snapshot is exactly what every
        signup of the season answered. Taken again only if called again — replaced, not
        duplicated.
        """
        settings = await self.get_settings()
        slots = await self.get_slots()
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT INTO season_signup_config "
                "(season_id, nationality_required, time_type, time_image_required, slots_json) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(season_id) DO UPDATE SET "
                "nationality_required = excluded.nationality_required, "
                "time_type = excluded.time_type, "
                "time_image_required = excluded.time_image_required, "
                "slots_json = excluded.slots_json, captured_at = datetime('now')",
                (
                    season_id,
                    int(settings.nationality_required),
                    settings.time_type,
                    int(settings.time_image_required),
                    json.dumps([
                        {"slot_id": slot.slot_id, "day_of_week": slot.day_of_week,
                         "time_hhmm": slot.time_hhmm}
                        for slot in slots
                    ]),
                ),
            )
            await db.commit()

    async def get_season_config(self, season_id: int) -> dict | None:
        """The signup configuration *season_id* was confirmed with, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT nationality_required, time_type, time_image_required, slots_json, "
                "captured_at FROM season_signup_config WHERE season_id = ?",
                (season_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "nationality_required": bool(row["nationality_required"]),
            "time_type": row["time_type"],
            "time_image_required": bool(row["time_image_required"]),
            "slots": json.loads(row["slots_json"]),
            "captured_at": row["captured_at"],
        }

    async def save_closed_message_id(self, msg_id: int | None) -> None:
        """Persist only the closed-status message ID without altering other fields."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config SET signup_closed_message_id = ? "
                "",
                (msg_id,),
            )
            await db.commit()

    # Convenience aliases (FR-017)

    async def set_signups_open(
        self, button_message_id: int, selected_tracks: list[str]
    ) -> None:
        """Alias for set_window_open."""
        await self.set_window_open(button_message_id, selected_tracks)

    async def set_signups_closed(
        self, *, closed_msg_id: int | None = None
    ) -> None:
        """Alias for set_window_closed."""
        await self.set_window_closed(closed_msg_id=closed_msg_id)

    async def save_selected_tracks(self, tracks: list[str]) -> None:
        """Persist selected_tracks without changing the open/closed state."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config SET selected_tracks_json = ?",
                (json.dumps(tracks),),
            )
            await db.commit()

    async def get_selected_tracks(self) -> list[str]:
        """Return the list of selected track IDs for the current signup window."""
        cfg = await self.get_config()
        return cfg.selected_tracks if cfg else []

    # ── SignupRecord CRUD ─────────────────────────────────────────────

    _RECORD_COLUMNS = (
        "id, season_id, window_id, discord_user_id, discord_username, "
        "server_display_name, nationality, platform, platform_id, availability_slot_ids, "
        "driver_type, preferred_teams, preferred_teammate, lap_times_json, notes, "
        "signup_channel_id, total_lap_ms"
    )

    async def get_record(
        self, discord_user_id: str, season_id: int | None = None
    ) -> SignupRecord | None:
        """The driver's latest signup, in *season_id* where one is named.

        Records are never overwritten (issue #220), so "the" record of a driver is the most
        recent of theirs: the one a review, a correction or an approval acts upon.
        """
        query = (
            f"SELECT {self._RECORD_COLUMNS} FROM signup_records WHERE discord_user_id = ?"
        )
        params: list = [discord_user_id]
        if season_id is not None:
            query += " AND season_id = ?"
            params.append(season_id)
        query += " ORDER BY id DESC LIMIT 1"
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_signup_record(row)

    async def get_records(self, season_id: int) -> list[SignupRecord]:
        """Every signup kept under *season_id*, oldest first."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                f"SELECT {self._RECORD_COLUMNS} FROM signup_records "
                "WHERE season_id = ? ORDER BY id",
                (season_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_signup_record(row) for row in rows]

    async def save_record(self, record: SignupRecord) -> int:
        """Store *record*, returning its id.

        A record not yet stored (``id`` of -1 or below 1) is inserted as a new signup, under
        the season and window it names or, where it names none, under the server's active
        season and that season's latest window. A stored record is updated in place.
        """
        fields = (
            record.discord_username,
            record.server_display_name,
            record.nationality,
            record.platform,
            record.platform_id,
            json.dumps(record.availability_slot_ids),
            record.driver_type,
            json.dumps(record.preferred_teams),
            record.preferred_teammate,
            json.dumps(record.lap_times),
            record.notes,
            record.signup_channel_id,
        )
        async with get_connection(self._db_path) as db:
            if record.id is not None and record.id > 0:
                await db.execute(
                    """
                    UPDATE signup_records SET
                        discord_username = ?, server_display_name = ?, nationality = ?,
                        platform = ?, platform_id = ?, availability_slot_ids = ?,
                        driver_type = ?, preferred_teams = ?, preferred_teammate = ?,
                        lap_times_json = ?, notes = ?, signup_channel_id = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (*fields, record.id),
                )
                await db.commit()
                return record.id

            season_id = record.season_id
            if season_id is None:
                season_id = await self._live_season_id(db)
            window_id = record.window_id
            if window_id is None and season_id is not None:
                cursor = await db.execute(
                    "SELECT MAX(id) AS id FROM signup_windows WHERE season_id = ?",
                    (season_id,),
                )
                row = await cursor.fetchone()
                window_id = row["id"] if row is not None else None
            cursor = await db.execute(
                """
                INSERT INTO signup_records
                    (season_id, window_id, discord_user_id, discord_username,
                     server_display_name, nationality, platform, platform_id,
                     availability_slot_ids, driver_type, preferred_teams,
                     preferred_teammate, lap_times_json, notes, signup_channel_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (season_id, window_id, record.discord_user_id, *fields),
            )
            await db.commit()
            new_id = int(cursor.lastrowid)
        record.id, record.season_id, record.window_id = new_id, season_id, window_id
        return new_id

    @staticmethod
    def _row_to_signup_record(row) -> SignupRecord:
        return SignupRecord(
            id=row["id"],
            discord_user_id=row["discord_user_id"],
            discord_username=row["discord_username"],
            server_display_name=row["server_display_name"],
            nationality=row["nationality"],
            platform=row["platform"],
            platform_id=row["platform_id"],
            availability_slot_ids=json.loads(row["availability_slot_ids"] or "[]"),
            driver_type=row["driver_type"],
            preferred_teams=json.loads(row["preferred_teams"] or "[]"),
            preferred_teammate=row["preferred_teammate"],
            lap_times=json.loads(row["lap_times_json"] or "{}"),
            notes=row["notes"],
            signup_channel_id=row["signup_channel_id"],
            total_lap_ms=row["total_lap_ms"] if "total_lap_ms" in row.keys() else None,
            season_id=row["season_id"] if "season_id" in row.keys() else None,
            window_id=row["window_id"] if "window_id" in row.keys() else None,
        )

    # ── SignupWizardRecord CRUD ────────────────────────────────────────

    async def get_wizard(
        self, discord_user_id: str
    ) -> SignupWizardRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_wizard_record(row)

    async def get_wizard_by_channel(
        self, channel_id: int
    ) -> SignupWizardRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records "
                "WHERE signup_channel_id = ?",
                (channel_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_wizard_record(row)

    async def save_wizard(self, wizard: SignupWizardRecord) -> None:
        snapshot_json = (
            json.dumps(self._snapshot_to_dict(wizard.config_snapshot))
            if wizard.config_snapshot else None
        )
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_wizard_records
                    (discord_user_id, wizard_state, signup_channel_id,
                     config_snapshot_json, draft_answers_json, current_lap_track_index,
                     last_activity_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    wizard_state             = excluded.wizard_state,
                    signup_channel_id        = excluded.signup_channel_id,
                    config_snapshot_json     = excluded.config_snapshot_json,
                    draft_answers_json       = excluded.draft_answers_json,
                    current_lap_track_index  = excluded.current_lap_track_index,
                    last_activity_at         = excluded.last_activity_at
                """,
                (
                    wizard.discord_user_id,
                    wizard.wizard_state.value,
                    wizard.signup_channel_id,
                    snapshot_json,
                    json.dumps(wizard.draft_answers),
                    wizard.current_lap_track_index,
                    wizard.last_activity_at,
                ),
            )
            await db.commit()

    async def mark_approved(self, discord_user_id: str) -> None:
        """Mark the latest signup of *discord_user_id* as approved — the one just reviewed.

        Read wherever "the driver's signup" is chosen: an approved signup outranks a later one
        that was not (issue #243, `driver_service.SIGNUP_PRECEDENCE_SQL`).
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_records SET approved = 1 WHERE id = ("
                "  SELECT id FROM signup_records WHERE discord_user_id = ?"
                "  ORDER BY id DESC LIMIT 1)",
                (discord_user_id,),
            )
            await db.commit()

    async def withdraw_approval(self, driver_profile_id: int) -> None:
        """Clear the approval of the driver's latest approved signup, under any of their accounts.

        For an approved driver the league turns down afterwards: the signup was rejected in
        the end, and no longer outranks the driver's others.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_records SET approved = 0 WHERE id = ("
                "  SELECT id FROM signup_records WHERE approved = 1"
                "  AND discord_user_id IN ("
                "    SELECT discord_user_id FROM driver_accounts WHERE driver_profile_id = ?)"
                "  ORDER BY id DESC LIMIT 1)",
                (driver_profile_id,),
            )
            await db.commit()

    async def rekey_wizard(self, from_account: str, to_account: str) -> None:
        """Move a wizard record to another account of the same driver (issue #243).

        A wizard record is the transient state of one signup and its channel, not a record of
        the league's, so it follows the driver to their new current account.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_wizard_records SET discord_user_id = ? "
                "WHERE discord_user_id = ?",
                (to_account, from_account),
            )
            await db.commit()

    async def delete_wizard(self, discord_user_id: str) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM signup_wizard_records WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            await db.commit()

    async def get_all_active_wizards(self) -> list[SignupWizardRecord]:
        """Return all wizard records not in UNENGAGED state for a server."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records "
                "WHERE wizard_state != 'UNENGAGED'",
            )
            rows = await cursor.fetchall()
        return [self._row_to_wizard_record(r) for r in rows]

    async def get_all_active_wizards_all_servers(self) -> list[SignupWizardRecord]:
        """Return all non-UNENGAGED wizard records across all servers (for restart recovery)."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records WHERE wizard_state != 'UNENGAGED'"
            )
            rows = await cursor.fetchall()
        return [self._row_to_wizard_record(r) for r in rows]

    @staticmethod
    def _row_to_wizard_record(row) -> SignupWizardRecord:
        from models.signup_module import ConfigSnapshot, AvailabilitySlot
        snapshot: ConfigSnapshot | None = None
        if row["config_snapshot_json"]:
            d = json.loads(row["config_snapshot_json"])
            # The snapshot stores each slot's durable identity; the display ordinal is
            # rebuilt from chronological order, which is how get_slots derived it in the
            # first place, so a driver mid-wizard still sees the numbers they were shown.
            # A snapshot written before the durable identity existed carries
            # slot_sequence_id and no slot_id: both are derived here, so it reads
            # correctly without migrating stored JSON.
            snapshot_slots = sorted(
                d.get("slots", []),
                key=lambda s: (s["day_of_week"], s["time_hhmm"]),
            )
            slots = [
                AvailabilitySlot(
                    id=s["id"],
                    slot_id=s.get("slot_id")
                    or AvailabilitySlot.make_slot_id(s["day_of_week"], s["time_hhmm"]),
                    slot_sequence_id=i,
                    day_of_week=s["day_of_week"],
                    time_hhmm=s["time_hhmm"],
                    display_label=AvailabilitySlot.make_label(s["day_of_week"], s["time_hhmm"]),
                )
                for i, s in enumerate(snapshot_slots, start=1)
            ]
            snapshot = ConfigSnapshot(
                nationality_required=d["nationality_required"],
                time_type=d["time_type"],
                time_image_required=d["time_image_required"],
                selected_track_ids=d["selected_track_ids"],
                slots=slots,
                team_names=d.get("team_names", []),
            )
        return SignupWizardRecord(
            id=row["id"],
            discord_user_id=row["discord_user_id"],
            wizard_state=WizardState(row["wizard_state"]),
            signup_channel_id=row["signup_channel_id"],
            config_snapshot=snapshot,
            draft_answers=json.loads(row["draft_answers_json"] or "{}"),
            current_lap_track_index=row["current_lap_track_index"],
            last_activity_at=row["last_activity_at"],
        )

    # ── SignupDivisionConfig CRUD ─────────────────────────────────────

    async def get_division_config(
        self, division_id: int
    ) -> SignupDivisionConfig | None:
        """Return the division config record or None if not set."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, lineup_channel_id "
                "FROM signup_division_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SignupDivisionConfig(
            id=row["id"],
            division_id=row["division_id"],
            lineup_channel_id=row["lineup_channel_id"],
        )

    async def upsert_division_config(
        self, division_id: int
    ) -> None:
        """Ensure a signup_division_config row exists for this division."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO signup_division_config (division_id) VALUES (?)",
                (division_id,),
            )
            await db.commit()

    # ── Config snapshot ───────────────────────────────────────────────

    async def capture_config_snapshot(self) -> ConfigSnapshot:
        """Capture a copy of current settings + slots + selected tracks for wizard isolation."""
        settings = await self.get_settings()
        slots = await self.get_slots()
        cfg = await self.get_config()
        return ConfigSnapshot(
            nationality_required=settings.nationality_required,
            time_type=settings.time_type,
            time_image_required=settings.time_image_required,
            selected_track_ids=cfg.selected_tracks if cfg else [],
            slots=list(slots),
        )

    @staticmethod
    def _snapshot_to_dict(snapshot: ConfigSnapshot) -> dict:
        """Freeze the configuration a wizard started against.

        Each slot is stored by its durable identity. The display ordinal is deliberately
        **not** stored: it is a chronological position, and storing one is what issue #126
        was. ``_row_to_wizard_record`` rebuilds it from the snapshot's own order.
        """
        return {
            "nationality_required": snapshot.nationality_required,
            "time_type": snapshot.time_type,
            "time_image_required": snapshot.time_image_required,
            "selected_track_ids": snapshot.selected_track_ids,
            "team_names": snapshot.team_names,
            "slots": [
                {
                    "id": s.id,
                    "slot_id": s.slot_id,
                    "day_of_week": s.day_of_week,
                    "time_hhmm": s.time_hhmm,
                }
                for s in snapshot.slots
            ],
        }
