"""SignupModuleService — CRUD for signup module tables."""
from __future__ import annotations

import json
import logging

from db.database import get_connection
from models.signup_module import AvailabilitySlot, SignupModuleConfig, SignupModuleSettings

log = logging.getLogger(__name__)


class SignupModuleService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Config ────────────────────────────────────────────────────────

    async def get_config(self, server_id: int) -> SignupModuleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id, signup_channel_id, base_role_id, signed_up_role_id, "
                "       signups_open, signup_button_message_id, selected_tracks_json "
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
        )

    async def save_config(self, cfg: SignupModuleConfig) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_config
                    (server_id, signup_channel_id, base_role_id, signed_up_role_id,
                     signups_open, signup_button_message_id, selected_tracks_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    signup_channel_id        = excluded.signup_channel_id,
                    base_role_id             = excluded.base_role_id,
                    signed_up_role_id        = excluded.signed_up_role_id,
                    signups_open             = excluded.signups_open,
                    signup_button_message_id = excluded.signup_button_message_id,
                    selected_tracks_json     = excluded.selected_tracks_json
                """,
                (
                    cfg.server_id,
                    cfg.signup_channel_id,
                    cfg.base_role_id,
                    cfg.signed_up_role_id,
                    int(cfg.signups_open),
                    cfg.signup_button_message_id,
                    json.dumps(cfg.selected_tracks),
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
        """Return slots ordered by (day_of_week, time_hhmm) with 1-based slot_id."""
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
                slot_id=idx,
                day_of_week=row["day_of_week"],
                time_hhmm=row["time_hhmm"],
                display_label=AvailabilitySlot.make_label(row["day_of_week"], row["time_hhmm"]),
            )
            for idx, row in enumerate(rows, start=1)
        ]

    async def add_slot(self, server_id: int, day_of_week: int, time_hhmm: str) -> None:
        """Insert a slot; raises ValueError on duplicate."""
        async with get_connection(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO signup_availability_slots (server_id, day_of_week, time_hhmm) "
                    "VALUES (?, ?, ?)",
                    (server_id, day_of_week, time_hhmm),
                )
                await db.commit()
            except Exception as exc:
                if "UNIQUE constraint failed" in str(exc):
                    raise ValueError(
                        f"Slot already exists: day={day_of_week} time={time_hhmm}"
                    ) from exc
                raise

    async def remove_slot_by_rank(self, server_id: int, slot_id: int) -> bool:
        """Remove the slot at the given 1-based rank. Returns False if rank out of range."""
        slots = await self.get_slots(server_id)
        matches = [s for s in slots if s.slot_id == slot_id]
        if not matches:
            return False
        internal_id = matches[0].id
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM signup_availability_slots WHERE id = ?",
                (internal_id,),
            )
            await db.commit()
        return True

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
                "SET signups_open = 1, signup_button_message_id = ?, selected_tracks_json = ? "
                "WHERE server_id = ?",
                (button_message_id, json.dumps(selected_tracks), server_id),
            )
            await db.commit()

    async def set_window_closed(self, server_id: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE signup_module_config "
                "SET signups_open = 0, signup_button_message_id = NULL "
                "WHERE server_id = ?",
                (server_id,),
            )
            await db.commit()
