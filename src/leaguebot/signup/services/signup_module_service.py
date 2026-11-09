"""SignupModuleService — CRUD for signup module tables."""
from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection, inserted_id
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.pack_service import CLEARED_SIGNUP_CHANNEL_ID, CLEARED_WIZARDS
from leaguebot.signup.models.signup_module import (
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

    async def get_config(self) -> SignupModuleConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signup_channel_id, "
                "       signups_open, signup_button_message_id, selected_tracks_json, "
                "       signup_closed_message_id, close_at "
                "FROM signup_module_config",
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SignupModuleConfig(
            signup_channel_id=row["signup_channel_id"],
            signups_open=bool(row["signups_open"]),
            signup_button_message_id=row["signup_button_message_id"],
            selected_tracks=json.loads(row["selected_tracks_json"] or "[]"),
            signup_closed_message_id=row["signup_closed_message_id"],
            close_at=row["close_at"],
        )

    async def drivers_mid_signup(
        self, states: Iterable[DriverState]
    ) -> list[tuple[str, DriverState, int | None]]:
        """Each driver whose state is one of *states*, oldest profile first, as their account, their
        state and their signup channel.

        A driver with no wizard record, or one whose channel was never made, has no channel (None).
        `/signup close` lists them for its confirmation, so a manager can follow the link and
        nudge someone to finish before closing.
        """
        wanted = tuple(state.value for state in states)
        if not wanted:
            return []
        placeholders = ",".join("?" for _ in wanted)
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                f"SELECT p.discord_user_id, p.current_state, w.signup_channel_id "
                f"FROM driver_profiles p "
                f"LEFT JOIN signup_wizard_records w ON w.discord_user_id = p.discord_user_id "
                f"WHERE p.current_state IN ({placeholders}) ORDER BY p.id",
                wanted,
            )
            rows = await cursor.fetchall()
        return [
            (row["discord_user_id"], DriverState(row["current_state"]), row["signup_channel_id"])
            for row in rows
        ]

    async def save_config(self, cfg: SignupModuleConfig) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO signup_module_config
                    (id, signup_channel_id,
                     signups_open, signup_button_message_id, selected_tracks_json,
                     signup_closed_message_id, close_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    signup_channel_id          = excluded.signup_channel_id,
                    signups_open               = excluded.signups_open,
                    signup_button_message_id   = excluded.signup_button_message_id,
                    selected_tracks_json       = excluded.selected_tracks_json,
                    signup_closed_message_id   = excluded.signup_closed_message_id,
                    close_at                   = excluded.close_at
                """,
                (
                    1,
                    cfg.signup_channel_id,
                    int(cfg.signups_open),
                    cfg.signup_button_message_id,
                    json.dumps(cfg.selected_tracks),
                    cfg.signup_closed_message_id,
                    cfg.close_at,
                ),
            )
            await db.commit()

    async def delete_config_on(self, db: aiosqlite.Connection) -> None:
        """Forget the signup channel and the window state on *db*, committing nothing, so that
        the module's switch-off saves it together with the flag coming down.

        The row holds what names a Discord object — the channel, the button and the
        closed-status message — and the window's state. A channel can be deleted or given
        another job while the module is off, so the disable clears it (constitution,
        Principle X, rules 3 and 6). The time slots and the three question settings are not
        in this row, and no key joins their tables to it, so nothing cascades: they stand
        again when the module is next enabled, as the signup specification requires
        (issue #127).
        """
        await db.execute("DELETE FROM signup_module_config")

    async def move_base_role_overwrite(
        self, guild: discord.Guild, old_role_id: int | None, new_role: discord.Role
    ) -> None:
        """Carry the signup channel's base-role overwrite from *old_role_id* to *new_role*.

        The base role is the league's (issue #276), but who may see the signup channel is
        this module's: the channel is visible to the base role and to nobody else, so a new
        role without the overwrite would leave members unable to see where they are told to
        sign up. The old role's overwrite goes in the same breath, or it keeps access it is
        no longer entitled to. Re-setting the same role touches only the grant: a "remove
        then add" on one role would briefly revoke it, and outright if the grant failed.

        Nothing is done without a signup channel, which exists only while the module is
        enabled. A failure is logged and never raised: Discord refuses an overwrite for
        reasons that have nothing to do with the setting, and the role the league chose is
        still the right one to have stored. The channel is repairable by hand.
        """
        cfg = await self.get_config()
        if cfg is None or cfg.signup_channel_id is None:
            return
        channel = guild.get_channel(cfg.signup_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            if old_role_id and old_role_id != new_role.id:
                old_role = guild.get_role(old_role_id)
                if old_role is not None:
                    await channel.set_permissions(old_role, overwrite=None)
            await channel.set_permissions(
                new_role,
                view_channel=True,
                send_messages=False,
                use_application_commands=True,
            )
        except Exception:  # noqa: BLE001 — see the docstring
            log.warning("move_base_role_overwrite: could not update the signup channel", exc_info=True)

    # ── Settings ──────────────────────────────────────────────────────

    async def get_settings(self) -> SignupModuleSettings:
        """Return settings row; if missing, return defaults."""
        async with get_connection(self._db_path) as db:
            return await settings_on(db)

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
            return await slots_on(db)

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
            rows = list(await cursor.fetchall())
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

    async def set_window_open_on(
        self, db: aiosqlite.Connection, button_message_id: int, selected_tracks: list[str]
    ) -> None:
        """Open the window, and record it as a window of the server's active season, on the
        caller's connection, committing nothing: the Sign Up button's message is kept, the
        previous closed notice forgotten.

        The record is what a signup made through this window is kept against (issue #220):
        the tracks it asked times for, and later its close time. A server with no active
        season records none — `/signup open` refuses there, so only a caller outside that
        command reaches this without one.
        """
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

    async def set_window_closed_on(
        self, db: aiosqlite.Connection, *, closed_msg_id: int | None = None
    ) -> None:
        """Record the window closed on the caller's connection, committing nothing: the Sign Up
        button's id and the close time are cleared, *closed_msg_id* is kept so the next opening
        deletes the notice, and the window's record is stamped closed."""
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

    async def set_close_at(self, close_at_iso: str | None) -> None:
        """Persist (or clear) the auto-close ISO 8601 UTC timestamp."""
        async with get_connection(self._db_path) as db:
            await self.set_close_at_on(db, close_at_iso)
            await db.commit()

    async def set_close_at_on(
        self, db: aiosqlite.Connection, close_at_iso: str | None
    ) -> None:
        """Persist (or clear) the auto-close time on the caller's connection, committing nothing."""
        await db.execute(
            "UPDATE signup_module_config SET close_at = ?",
            (close_at_iso,),
        )
        await db.execute(
            "UPDATE signup_windows SET close_at = ? "
            "WHERE closed_at IS NULL",
            (close_at_iso,),
        )

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

    async def get_record_on(
        self, db: aiosqlite.Connection, discord_user_id: str
    ) -> SignupRecord | None:
        """The driver's latest signup, read on the connection handed (a save's)."""
        cursor = await db.execute(
            f"SELECT {self._RECORD_COLUMNS} FROM signup_records WHERE discord_user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (discord_user_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._row_to_signup_record(row)

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

    async def save_record_on(self, db: aiosqlite.Connection, record: SignupRecord) -> int:
        """Store *record* on the connection handed, returning its id; commits nothing.

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
        new_id = inserted_id(cursor)
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
                "       last_activity_at, correction_offered_at "
                "FROM signup_wizard_records WHERE discord_user_id = ?",
                (discord_user_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_wizard_record(row)

    async def get_wizard_on(
        self, db: aiosqlite.Connection, discord_user_id: str
    ) -> SignupWizardRecord | None:
        """The driver's wizard, read on the connection handed (a save's)."""
        cursor = await db.execute(
            "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
            "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
            "       last_activity_at, correction_offered_at "
            "FROM signup_wizard_records WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else self._row_to_wizard_record(row)

    async def get_wizard_by_channel(
        self, channel_id: int
    ) -> SignupWizardRecord | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at, correction_offered_at "
                "FROM signup_wizard_records "
                "WHERE signup_channel_id = ?",
                (channel_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_wizard_record(row)

    async def save_wizard_on(self, db: aiosqlite.Connection, wizard: SignupWizardRecord) -> None:
        """Save the wizard's record on the connection handed, inserting it or updating the one
        the driver holds; commits nothing: the caller's save does.
        """
        snapshot_json = (
            json.dumps(self._snapshot_to_dict(wizard.config_snapshot))
            if wizard.config_snapshot else None
        )
        await self.save_wizard_row_on(
            db, discord_user_id=wizard.discord_user_id,
            wizard_state=wizard.wizard_state.value,
            signup_channel_id=wizard.signup_channel_id, snapshot_json=snapshot_json,
            draft_answers=wizard.draft_answers,
            current_lap_track_index=wizard.current_lap_track_index,
            last_activity_at=wizard.last_activity_at,
        )

    async def save_wizard_row_on(
        self,
        db: aiosqlite.Connection,
        *,
        discord_user_id: str,
        wizard_state: str,
        signup_channel_id: int | None,
        snapshot_json: str | None,
        draft_answers: dict[str, Any],
        current_lap_track_index: int,
        last_activity_at: str | None,
    ) -> None:
        """Insert or update a driver's wizard row on the connection handed; commits nothing.

        What `save_wizard_on` writes, for a change that saves the row in the same save as a job's
        mark. *snapshot_json* is the configuration as `_snapshot_to_dict` freezes it, already
        encoded, so that a job which made it can hand it on in its result.
        """
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
                discord_user_id,
                wizard_state,
                signup_channel_id,
                snapshot_json,
                json.dumps(draft_answers),
                current_lap_track_index,
                last_activity_at,
            ),
        )

    async def mark_approved_on(self, db: aiosqlite.Connection, discord_user_id: str) -> None:
        """Mark the latest signup of *discord_user_id* approved on the connection handed;
        commits nothing: the approval's save does."""
        await db.execute(
            "UPDATE signup_records SET approved = 1 WHERE id = ("
            "  SELECT id FROM signup_records WHERE discord_user_id = ?"
            "  ORDER BY id DESC LIMIT 1)",
            (discord_user_id,),
        )

    async def set_correction_offered_at_on(
        self, db: aiosqlite.Connection, discord_user_id: str, offered_at: str | None
    ) -> None:
        """Record when the correction choices were posted for a driver, on the connection handed;
        commits nothing. The five minutes a manager has to choose run from this moment. None
        clears it, once no correction is awaited."""
        await db.execute(
            "UPDATE signup_wizard_records SET correction_offered_at = ? "
            "WHERE discord_user_id = ?",
            (offered_at, discord_user_id),
        )

    async def set_total_lap_ms_on(
        self, db: aiosqlite.Connection, discord_user_id: str, total_ms: int | None
    ) -> None:
        """Store *total_ms*, the sum of a driver's signup lap times, on their latest signup, on
        the connection handed; commits nothing. The seeding value a placement is made from."""
        await db.execute(
            "UPDATE signup_records SET total_lap_ms = ? WHERE id = ("
            "  SELECT id FROM signup_records WHERE discord_user_id = ?"
            "  ORDER BY id DESC LIMIT 1)",
            (total_ms, discord_user_id),
        )

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
        the league's, so it follows the driver to their new current account. An abandoned
        draft the new account holds (a signup never begun) is deleted in the same save, which
        it would otherwise collide with.
        """
        async with get_connection(self._db_path) as db:
            await self.delete_wizard_on(db, to_account)
            await db.execute(
                "UPDATE signup_wizard_records SET discord_user_id = ? "
                "WHERE discord_user_id = ?",
                (to_account, from_account),
            )
            await db.commit()

    async def delete_wizard_on(self, db: aiosqlite.Connection, discord_user_id: str) -> None:
        """Delete a driver's wizard record on the connection handed; commits nothing.

        For a change whose job records the deletion of the driver's channel in the save that
        marks the job done, and for `rekey_wizard`, which clears the account a record moves to.
        """
        await db.execute(
            "DELETE FROM signup_wizard_records WHERE discord_user_id = ?",
            (discord_user_id,),
        )

    async def end_wizard_on(self, db: aiosqlite.Connection, discord_user_id: str) -> None:
        """Mark a driver's signup over on the connection handed; commits nothing.

        The wizard is unengaged, so a restart's recovery never re-arms or expires it. The signup
        channel is kept: the channel's deletion still reads it. A driver with no wizard record is
        nothing to end.
        """
        await db.execute(
            "UPDATE signup_wizard_records SET wizard_state = 'UNENGAGED' "
            "WHERE discord_user_id = ?",
            (discord_user_id,),
        )

    async def get_all_active_wizards(self) -> list[SignupWizardRecord]:
        """Return all wizard records not in UNENGAGED state for a server."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, discord_user_id, wizard_state, signup_channel_id, "
                "       config_snapshot_json, draft_answers_json, current_lap_track_index, "
                "       last_activity_at, correction_offered_at "
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
                "       last_activity_at, correction_offered_at "
                "FROM signup_wizard_records WHERE wizard_state != 'UNENGAGED'"
            )
            rows = await cursor.fetchall()
        return [self._row_to_wizard_record(r) for r in rows]

    @staticmethod
    def _row_to_wizard_record(row) -> SignupWizardRecord:
        from leaguebot.signup.models.signup_module import ConfigSnapshot, AvailabilitySlot
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
                team_shorthands=d.get("team_shorthands", {}),
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
            correction_offered_at=row["correction_offered_at"],
        )

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
            "team_shorthands": snapshot.team_shorthands,
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


async def settings_on(db: aiosqlite.Connection) -> SignupModuleSettings:
    """The module's settings, read on *db*; the defaults where none are saved."""
    cursor = await db.execute(
        "SELECT nationality_required, time_type, time_image_required FROM signup_module_settings"
    )
    row = await cursor.fetchone()
    if row is None:
        return SignupModuleSettings(
            nationality_required=True, time_type="TIME_TRIAL", time_image_required=True
        )
    return SignupModuleSettings(
        nationality_required=bool(row["nationality_required"]),
        time_type=row["time_type"],
        time_image_required=bool(row["time_image_required"]),
    )


async def slots_on(db: aiosqlite.Connection) -> list[AvailabilitySlot]:
    """The availability slots, read on *db*, ordered Monday to Sunday and by time, numbered 1..N
    in that order (see `SignupModuleService.get_slots`)."""
    cursor = await db.execute(
        "SELECT id, day_of_week, time_hhmm FROM signup_availability_slots "
        "ORDER BY day_of_week ASC, time_hhmm ASC"
    )
    return [
        AvailabilitySlot(
            id=row["id"],
            slot_id=AvailabilitySlot.make_slot_id(row["day_of_week"], row["time_hhmm"]),
            slot_sequence_id=i,
            day_of_week=row["day_of_week"],
            time_hhmm=row["time_hhmm"],
            display_label=AvailabilitySlot.make_label(row["day_of_week"], row["time_hhmm"]),
        )
        for i, row in enumerate(await cursor.fetchall(), start=1)
    ]


async def signup_enabled_on(db: aiosqlite.Connection) -> bool:
    """Whether the signup module is on, read on *db*, as `ModuleService.is_signup_enabled`
    reads it."""
    cursor = await db.execute("SELECT signup_module_enabled FROM server_configs")
    row = await cursor.fetchone()
    return row is not None and bool(row["signup_module_enabled"])


async def configuration_confirmed_on(
    db: aiosqlite.Connection, season_id: int, now: datetime
) -> None:
    """Keep the signup configuration *season_id* is confirmed with, on *db*, committing nothing:
    the module's settings and its availability slots, stamped *now*.

    Core hands it the season's id and the queue's clock as the season's configuration is
    confirmed, in the confirmation's own save, so that the season moves on with its signup
    settings kept or not at all (architecture.md, "How modules and core fit together"). From that
    moment the module's settings are fixed until the season ends, so the snapshot is exactly what
    every signup of the season answered. Called again for the season it replaces the snapshot,
    never duplicating it. While the signup module is off it keeps nothing, the hook being called
    whatever the modules' switches say.
    """
    if not await signup_enabled_on(db):
        return
    settings = await settings_on(db)
    slots = [
        {"slot_id": slot.slot_id, "day_of_week": slot.day_of_week, "time_hhmm": slot.time_hhmm}
        for slot in await slots_on(db)
    ]
    await db.execute(
        "INSERT INTO season_signup_config "
        "(season_id, nationality_required, time_type, time_image_required, slots_json, "
        "captured_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(season_id) DO UPDATE SET "
        "nationality_required = excluded.nationality_required, "
        "time_type = excluded.time_type, "
        "time_image_required = excluded.time_image_required, "
        "slots_json = excluded.slots_json, captured_at = excluded.captured_at",
        (
            season_id,
            int(settings.nationality_required),
            settings.time_type,
            int(settings.time_image_required),
            json.dumps(slots),
            now.isoformat(),
        ),
    )


async def season_deleted_on(db: aiosqlite.Connection, season_id: int) -> None:
    """Delete signup's rows of *season_id* on *db*, committing nothing: the season's sign-up
    records, its windows and its configuration.

    Core hands it the season id as it deletes the season, in the delete's own save, so that the
    module's rows go with the season or not at all (architecture.md, "The database"). The records
    go before the windows they point at.
    """
    await db.execute("DELETE FROM signup_records WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM signup_windows WHERE season_id = ?", (season_id,))
    await db.execute("DELETE FROM season_signup_config WHERE season_id = ?", (season_id,))


async def bot_packed_on(db: aiosqlite.Connection) -> dict[str, Any]:
    """Clear signup's rows tied to the server the bot leaves, on *db*, committing nothing: every
    wizard record, and the signup channel, the Sign Up button's message and the closed post's
    message in the configuration, if it has one.

    Core hands it the pack's connection, so that signup's rows go with the rest of the pack or
    not at all (architecture.md, "The database"). The module stays enabled with nothing
    configured, which is the state `/module enable signup` itself leaves it in. It hands back what
    the reply and the audit entry report: how many wizards it cleared, and the signup channel's
    id as it stood, None where there was no configuration.
    """
    row = await (await db.execute("SELECT signup_channel_id FROM signup_module_config")).fetchone()
    wizards = (await db.execute("DELETE FROM signup_wizard_records")).rowcount
    await db.execute(
        "UPDATE signup_module_config SET signup_channel_id = NULL, "
        "signup_button_message_id = NULL, signup_closed_message_id = NULL"
    )
    return {
        CLEARED_WIZARDS: wizards,
        CLEARED_SIGNUP_CHANNEL_ID: None if row is None else row["signup_channel_id"],
    }
