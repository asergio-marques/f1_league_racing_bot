"""Withdrawing a round's forecast phases on the save it is handed (#439, slice 4b, amendment A).

`/round amend` becomes a change on the queue, whose one save writes the amended round and
everything the amendment withdraws. What it withdraws for weather — each phase's flag, its
results, and the session data it drew — is weather's own, so weather writes it, through
`withdraw_phases_on`, on the connection the save hands it, committing nothing.

Each test writes, reads the write back on the same connection, and then rolls back: what it finds
afterwards on a fresh connection is what a failure later in the save would leave, which is nothing.

The function is imported inside each test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import os

from leaguebot.core.db.database import get_connection, run_migrations

ROUND_ID = 31
OTHER_ROUND_ID = 32


async def _make_db(tmp_path) -> str:
    """Pro's rounds 1 (id 31) and 2 (id 32), each with all three phases performed: each phase's
    flag set, an ACTIVE result for each, and its sprint race and feature race with their Phase 2
    slot type and Phase 3 slots drawn."""
    db_path = os.path.join(str(tmp_path), "phase_withdrawal.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 3, '2026-11-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (1, 1, 'Pro', 1, 801)"
        )
        for number, round_id in enumerate((ROUND_ID, OTHER_ROUND_ID), start=1):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, phase1_done, phase2_done, phase3_done) "
                "VALUES (?, 1, ?, 'SPRINT', 'Monza', '2026-12-01T18:00:00+00:00', 1, 1, 1)",
                (round_id, number),
            )
            for phase in (1, 2, 3):
                await db.execute(
                    "INSERT INTO phase_results (round_id, phase_number, payload, status, "
                    "created_at) VALUES (?, ?, '{}', 'ACTIVE', '2026-11-20T00:00:00+00:00')",
                    (round_id, phase),
                )
            for session_type in ("LONG_SPRINT_RACE", "LONG_FEATURE_RACE"):
                await db.execute(
                    "INSERT INTO sessions (round_id, session_type, phase2_slot_type, "
                    "phase3_slots) VALUES (?, ?, 'mixed', '[\"Rain\"]')",
                    (round_id, session_type),
                )
        await db.commit()
    return db_path


async def _state(db, round_id: int = ROUND_ID) -> dict:
    """The round's three flags, its results' statuses by phase, and its sessions' drawn data."""
    cursor = await db.execute(
        "SELECT phase1_done, phase2_done, phase3_done FROM rounds WHERE id = ?", (round_id,)
    )
    flags = tuple(await cursor.fetchone())
    cursor = await db.execute(
        "SELECT phase_number, status FROM phase_results WHERE round_id = ? ORDER BY phase_number",
        (round_id,),
    )
    results = {row["phase_number"]: row["status"] for row in await cursor.fetchall()}
    cursor = await db.execute(
        "SELECT phase2_slot_type, phase3_slots FROM sessions WHERE round_id = ? ORDER BY id",
        (round_id,),
    )
    sessions = [tuple(row) for row in await cursor.fetchall()]
    return {"flags": flags, "results": results, "sessions": sessions}


PERFORMED = {
    "flags": (1, 1, 1),
    "results": {1: "ACTIVE", 2: "ACTIVE", 3: "ACTIVE"},
    "sessions": [("mixed", '["Rain"]'), ("mixed", '["Rain"]')],
}


async def test_withdrawing_phases_writes_only_those_phases_on_the_connection_handed(tmp_path):
    """Round 1 has all three phases performed. Withdrawing Phases 1 and 3 on a connection clears
    their two flags and invalidates their two results, and clears each session's Phase 3 slots;
    Phase 2 keeps its flag, its result and each session's slot type, and round 2 is untouched.
    Nothing of it outlasts a rollback."""
    from leaguebot.weather.services.phase_withdrawal import withdraw_phases_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await withdraw_phases_on(db, ROUND_ID, [1, 3])
        assert await _state(db) == {
            "flags": (0, 1, 0),
            "results": {1: "INVALIDATED", 2: "ACTIVE", 3: "INVALIDATED"},
            "sessions": [("mixed", None), ("mixed", None)],
        }
        assert await _state(db, OTHER_ROUND_ID) == PERFORMED
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _state(db) == PERFORMED


async def test_withdrawing_no_phase_writes_nothing(tmp_path):
    """Round 1 has all three phases performed. Withdrawing no phase on a connection changes no
    row: every flag, result and session stays as it was."""
    from leaguebot.weather.services.phase_withdrawal import withdraw_phases_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        before = db.total_changes
        await withdraw_phases_on(db, ROUND_ID, [])
        assert db.total_changes == before
        assert await _state(db) == PERFORMED
