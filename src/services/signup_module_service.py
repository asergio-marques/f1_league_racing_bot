"""SignupModuleService — CRUD for signup module tables."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from db.database import get_connection
from models.signup_module import (
    AvailabilitySlot,
    ConfigSnapshot,
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

    async def get_config(self, server_id: int) -> SignupModuleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id, signup_channel_id, base_role_id, signed_up_role_id, "
                "       signups_open, signup_button_message_id, selected_tracks_json, "
                "       signup_closed_message_id "
                "FROM signup_module_config WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SignupModuleConfig(
            server_id=row["server_id"],
            signup_channel_id=row["signup_channel_id"],
            base_role_id=row["base_role_id"],
            signed_up_role_id=row["signed_up_role_id"],
            signups_open=bool(row["signups_open"]),
            signup_button_message_id=row["signup_button_message_id"],
            selected_tracks=json.loads(row["selected_tracks_json"] or "[]"),
            signup_closed_message_id=row["signup_closed_message_id"],
        )

    async def save_config(self, cfg: SignupModuleConfig) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_config
                    (server_id, signup_channel_id, base_role_id, signed_up_role_id,
                     signups_open, signup_button_message_id, selected_tracks_json,
                     signup_closed_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    signup_channel_id          = excluded.signup_channel_id,
                    base_role_id               = excluded.base_role_id,
                    signed_up_role_id          = excluded.signed_up_role_id,
                    signups_open               = excluded.signups_open,
                    signup_button_message_id   = excluded.signup_button_message_id,
                    selected_tracks_json       = excluded.selected_tracks_json,
                    signup_closed_message_id   = excluded.signup_closed_message_id
                """,
                (
                    cfg.server_id,
                    cfg.signup_channel_id,
                    cfg.base_role_id,
                    cfg.signed_up_role_id,
                    int(cfg.signups_open),
                    cfg.signup_button_message_id,
                    json.dumps(cfg.selected_tracks),
                    cfg.signup_closed_message_id,
                ),
            )
            await db.commit()

    async def delete_config(self, server_id: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM signup_module_config WHERE server_id = ?",
                (server_id,),
            )
            await db.commit()

    # ── Settings ──────────────────────────────────────────────────────

    async def get_settings(self, server_id: int) -> SignupModuleSettings:
        """Return settings row; if missing, return defaults."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id, nationality_required, time_type, time_image_required "
                "FROM signup_module_settings WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return SignupModuleSettings(
                server_id=server_id,
                nationality_required=True,
                time_type="TIME_TRIAL",
                time_image_required=True,
            )
        return SignupModuleSettings(
            server_id=row["server_id"],
            nationality_required=bool(row["nationality_required"]),
            time_type=row["time_type"],
            time_image_required=bool(row["time_image_required"]),
        )

    async def save_settings(self, settings: SignupModuleSettings) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_settings
                    (server_id, nationality_required, time_type, time_image_required)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    nationality_required = excluded.nationality_required,
                    time_type            = excluded.time_type,
                    time_image_required  = excluded.time_image_required
                """,
                (
                    settings.server_id,
                    int(settings.nationality_required),
                    settings.time_type,
                    int(settings.time_image_required),
                ),
            )
            await db.commit()

    # ── Availability slots ────────────────────────────────────────────

    async def get_slots(self, server_id: int) -> list[AvailabilitySlot]:
        """Return slots ordered chronologically (Mon→Sun, time asc). Sequence IDs are
        always 1..N matching display order, regardless of stored values."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, day_of_week, time_hhmm "
                "FROM signup_availability_slots "
                "WHERE server_id = ? "
                "ORDER BY day_of_week ASC, time_hhmm ASC",
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [
            AvailabilitySlot(
                id=row["id"],
                server_id=row["server_id"],
                slot_sequence_id=i,
                day_of_week=row["day_of_week"],
                time_hhmm=row["time_hhmm"],
                display_label=AvailabilitySlot.make_label(row["day_of_week"], row["time_hhmm"]),
            )
            for i, row in enumerate(rows, start=1)
        ]

    async def add_slot(self, server_id: int, day_of_week: int, time_hhmm: str) -> AvailabilitySlot:
        """Insert a slot and resequence all slots chronologically; raises ValueError on duplicate."""
        async with get_connection(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO signup_availability_slots "
                    "(server_id, day_of_week, time_hhmm, slot_sequence_id) "
                    "VALUES (?, ?, ?, 0)",  # 0 is a placeholder; resequence fixes it
                    (server_id, day_of_week, time_hhmm),
                )
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError(
                        f"Slot already exists: day={day_of_week} time={time_hhmm}"
                    ) from exc
                raise
            await self._resequence_slots(db, server_id)
            await db.commit()

        # Fetch the newly assigned sequence ID for the inserted slot
        slots = await self.get_slots(server_id)
        inserted = next(
            (s for s in slots if s.day_of_week == day_of_week and s.time_hhmm == time_hhmm),
            None,
        )
        assert inserted is not None
        return inserted

    async def remove_slot_by_rank(self, server_id: int, slot_id: int) -> bool:
        """Remove the slot at chronological rank slot_id and resequence. Returns False if not found."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM signup_availability_slots "
                "WHERE server_id = ? "
                "ORDER BY day_of_week ASC, time_hhmm ASC",
                (server_id,),
            )
            rows = await cursor.fetchall()
            if slot_id < 1 or slot_id > len(rows):
                return False
            target_id = rows[slot_id - 1]["id"]
            await db.execute(
                "DELETE FROM signup_availability_slots WHERE id = ?",
                (target_id,),
            )
            await self._resequence_slots(db, server_id)
            await db.commit()
        return True

    @staticmethod
    async def _resequence_slots(db, server_id: int) -> None:
        """Assign slot_sequence_id values 1..N in chronological order (Mon → Sun, then time)."""
        cursor = await db.execute(
            "SELECT id FROM signup_availability_slots "
            "WHERE server_id = ? "
            "ORDER BY day_of_week ASC, time_hhmm ASC",
            (server_id,),
        )
        rows = await cursor.fetchall()
        for seq, row in enumerate(rows, start=1):
            await db.execute(
                "UPDATE signup_availability_slots SET slot_sequence_id = ? WHERE id = ?",
                (seq, row["id"]),
            )

    # ── Window state helpers ──────────────────────────────────────────

    async def get_window_state(self, server_id: int) -> bool:
        """Return True if signups are currently open."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signups_open FROM signup_module_config WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        return bool(row["signups_open"]) if row else False

    async def set_window_open(
        self, server_id: int, button_message_id: int, selected_tracks: list[str]
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config "
                "SET signups_open = 1, signup_button_message_id = ?, selected_tracks_json = ?, "
                "    signup_closed_message_id = NULL "
                "WHERE server_id = ?",
                (button_message_id, json.dumps(selected_tracks), server_id),
            )
            await db.commit()

    async def set_window_closed(
        self, server_id: int, *, closed_msg_id: int | None = None
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config "
                "SET signups_open = 0, signup_button_message_id = NULL, "
                "    signup_closed_message_id = ? "
                "WHERE server_id = ?",
                (closed_msg_id, server_id),
            )
            await db.commit()

    async def save_closed_message_id(self, server_id: int, msg_id: int | None) -> None:
        """Persist only the closed-status message ID without altering other fields."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config SET signup_closed_message_id = ? "
                "WHERE server_id = ?",
                (msg_id, server_id),
            )
            await db.commit()

    # Convenience aliases (FR-017)

    async def set_signups_open(
        self, server_id: int, button_message_id: int, selected_tracks: list[str]
    ) -> None:
        """Alias for set_window_open."""
        await self.set_window_open(server_id, button_message_id, selected_tracks)

    async def set_signups_closed(
        self, server_id: int, *, closed_msg_id: int | None = None
    ) -> None:
        """Alias for set_window_closed."""
        await self.set_window_closed(server_id, closed_msg_id=closed_msg_id)

    async def save_selected_tracks(self, server_id: int, tracks: list[str]) -> None:
        """Persist selected_tracks without changing the open/closed state."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config SET selected_tracks_json = ? WHERE server_id = ?",
                (json.dumps(tracks), server_id),
            )
            await db.commit()

    async def get_selected_tracks(self, server_id: int) -> list[str]:
        """Return the list of selected track IDs for the current signup window."""
        cfg = await self.get_config(server_id)
        return cfg.selected_tracks if cfg else []

    # ── SignupRecord CRUD ─────────────────────────────────────────────

    async def get_record(self, server_id: int, discord_user_id: str) -> SignupRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, discord_username, server_display_name, "
                "       nationality, platform, platform_id, availability_slot_ids, driver_type, "
                "       preferred_teams, preferred_teammate, lap_times_json, notes, signup_channel_id "
                "FROM signup_records WHERE server_id = ? AND discord_user_id = ?",
                (server_id, discord_user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_signup_record(row)

    async def save_record(self, record: SignupRecord) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_records
                    (server_id, discord_user_id, discord_username, server_display_name,
                     nationality, platform, platform_id, availability_slot_ids,
                     driver_type, preferred_teams, preferred_teammate, lap_times_json,
                     notes, signup_channel_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(server_id, discord_user_id) DO UPDATE SET
                    discord_username     = excluded.discord_username,
                    server_display_name  = excluded.server_display_name,
                    nationality          = excluded.nationality,
                    platform             = excluded.platform,
                    platform_id          = excluded.platform_id,
                    availability_slot_ids = excluded.availability_slot_ids,
                    driver_type          = excluded.driver_type,
                    preferred_teams      = excluded.preferred_teams,
                    preferred_teammate   = excluded.preferred_teammate,
                    lap_times_json       = excluded.lap_times_json,
                    notes                = excluded.notes,
                    signup_channel_id    = excluded.signup_channel_id,
                    updated_at           = excluded.updated_at
                """,
                (
                    record.server_id,
                    record.discord_user_id,
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
                ),
            )
            await db.commit()

    async def clear_record(self, server_id: int, discord_user_id: str) -> None:
        """Null out all signup fields for a former driver, retaining the row."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                UPDATE signup_records
                SET discord_username = NULL, server_display_name = NULL,
                    nationality = NULL, platform = NULL, platform_id = NULL,
                    availability_slot_ids = NULL, driver_type = NULL,
                    preferred_teams = NULL, preferred_teammate = NULL,
                    lap_times_json = NULL, total_lap_ms = NULL, notes = NULL,
                    updated_at = datetime('now')
                WHERE server_id = ? AND discord_user_id = ?
                """,
                (server_id, discord_user_id),
            )
            await db.commit()

    @staticmethod
    def _row_to_signup_record(row) -> SignupRecord:
        return SignupRecord(
            id=row["id"],
            server_id=row["server_id"],
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
        )

    # ── SignupWizardRecord CRUD ────────────────────────────────────────

    async def get_wizard(
        self, server_id: int, discord_user_id: str
    ) -> SignupWizardRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records WHERE server_id = ? AND discord_user_id = ?",
                (server_id, discord_user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_wizard_record(row)

    async def get_wizard_by_channel(
        self, server_id: int, channel_id: int
    ) -> SignupWizardRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records "
                "WHERE server_id = ? AND signup_channel_id = ?",
                (server_id, channel_id),
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
                    (server_id, discord_user_id, wizard_state, signup_channel_id,
                     config_snapshot_json, draft_answers_json, current_lap_track_index,
                     last_activity_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id, discord_user_id) DO UPDATE SET
                    wizard_state             = excluded.wizard_state,
                    signup_channel_id        = excluded.signup_channel_id,
                    config_snapshot_json     = excluded.config_snapshot_json,
                    draft_answers_json       = excluded.draft_answers_json,
                    current_lap_track_index  = excluded.current_lap_track_index,
                    last_activity_at         = excluded.last_activity_at
                """,
                (
                    wizard.server_id,
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

    async def delete_wizard(self, server_id: int, discord_user_id: str) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM signup_wizard_records WHERE server_id = ? AND discord_user_id = ?",
                (server_id, discord_user_id),
            )
            await db.commit()

    async def get_all_active_wizards(self, server_id: int) -> list[SignupWizardRecord]:
        """Return all wizard records not in UNENGAGED state for a server."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at "
                "FROM signup_wizard_records "
                "WHERE server_id = ? AND wizard_state != 'UNENGAGED'",
                (server_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_wizard_record(r) for r in rows]

    async def get_all_active_wizards_all_servers(self) -> list[SignupWizardRecord]:
        """Return all non-UNENGAGED wizard records across all servers (for restart recovery)."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, server_id, discord_user_id, wizard_state, signup_channel_id, "
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
            slots = [
                AvailabilitySlot(
                    id=s["id"],
                    server_id=s["server_id"],
                    slot_sequence_id=s["slot_sequence_id"],
                    day_of_week=s["day_of_week"],
                    time_hhmm=s["time_hhmm"],
                    display_label=AvailabilitySlot.make_label(s["day_of_week"], s["time_hhmm"]),
                )
                for s in d.get("slots", [])
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
            server_id=row["server_id"],
            discord_user_id=row["discord_user_id"],
            wizard_state=WizardState(row["wizard_state"]),
            signup_channel_id=row["signup_channel_id"],
            config_snapshot=snapshot,
            draft_answers=json.loads(row["draft_answers_json"] or "{}"),
            current_lap_track_index=row["current_lap_track_index"],
            last_activity_at=row["last_activity_at"],
        )

    # ── Config snapshot ───────────────────────────────────────────────

    async def capture_config_snapshot(self, server_id: int) -> ConfigSnapshot:
        """Capture a copy of current settings + slots + selected tracks for wizard isolation."""
        settings = await self.get_settings(server_id)
        slots = await self.get_slots(server_id)
        cfg = await self.get_config(server_id)
        return ConfigSnapshot(
            nationality_required=settings.nationality_required,
            time_type=settings.time_type,
            time_image_required=settings.time_image_required,
            selected_track_ids=cfg.selected_tracks if cfg else [],
            slots=list(slots),
        )

    @staticmethod
    def _snapshot_to_dict(snapshot: ConfigSnapshot) -> dict:
        return {
            "nationality_required": snapshot.nationality_required,
            "time_type": snapshot.time_type,
            "time_image_required": snapshot.time_image_required,
            "selected_track_ids": snapshot.selected_track_ids,
            "team_names": snapshot.team_names,
            "slots": [
                {
                    "id": s.id,
                    "server_id": s.server_id,
                    "slot_sequence_id": s.slot_sequence_id,
                    "day_of_week": s.day_of_week,
                    "time_hhmm": s.time_hhmm,
                }
                for s in snapshot.slots
            ],
        }
