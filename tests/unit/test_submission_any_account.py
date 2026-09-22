"""A pasted result may name a driver by any account they have held (issue #243).

The paste paths — the first submission, a resubmission and an amendment — hand
`validate_submission_block` a map of past accounts to current ones, and it moves each row onto
the current account before checking anything. A row is therefore validated against the seat
the driver holds now, and stored under the account they use now. The FL override is mapped
the same way by each caller, and `other_active_team_assignments` reads a session stored under
an old account as the same driver.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    ParsedQualifyingRow,
    current_accounts,
    extract_current_fl_override,
    other_active_team_assignments,
    validate_submission_block,
)
from tests.support.teams import seed_team_instances  # noqa: E402

PAST, NOW, OTHER = 100, 150, 200
TEAM_A, TEAM_B = 300, 400


def _validate(lines: list[str]):
    return validate_submission_block(
        lines,
        session_type=SessionType.FEATURE_QUALIFYING,
        division_driver_ids={NOW, OTHER},
        team_of_role={TEAM_A: TEAM_A, TEAM_B: TEAM_B},
        reserve_team_role_id=None,
        driver_team_map={NOW: TEAM_A, OTHER: TEAM_B},
        current_of={PAST: NOW},
    )


def test_a_row_naming_a_past_account_validates_for_the_current_one():
    result = _validate([
        f"1, <@{PAST}>, <@&{TEAM_A}>, Soft, 1:23.456, N/A",
        f"2, <@{OTHER}>, <@&{TEAM_B}>, Soft, 1:24.000, +0:00.544",
    ])

    assert all(isinstance(r, ParsedQualifyingRow) for r in result)
    assert [r.driver_user_id for r in result] == [NOW, OTHER]


def test_one_driver_named_by_both_accounts_is_the_same_driver_twice():
    """E5."""
    result = _validate([
        f"1, <@{PAST}>, <@&{TEAM_A}>, Soft, 1:23.456, N/A",
        f"2, <@{NOW}>, <@&{TEAM_A}>, Soft, 1:24.000, +0:00.544",
    ])

    assert all(isinstance(r, str) for r in result)
    assert any("appears more than once" in r for r in result)


SERVER_ID = 2434


async def _db_with_a_session_under_the_past_account(tmp_path) -> tuple[str, int]:
    db_path = os.path.join(str(tmp_path), "submission.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-09-17', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (1, 1, 'Pro', 1001, 1)"
        )
        await seed_team_instances(db, 1, TEAM_A, TEAM_B)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', '2026-09-20T18:00:00')"
        )
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
            "VALUES (1, 1, 1, 'FEATURE_QUALIFYING', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position) VALUES (1, ?, ?, 1)",
            (PAST, TEAM_A),
        )
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'ASSIGNED')",
            (str(PAST),),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
            (str(NOW), cursor.lastrowid),
        )
        await db.commit()
    return db_path, 1


async def test_another_session_under_a_past_account_reads_as_the_current_one(tmp_path):
    """E36: the round's qualifying stands under PAST; the race is checked against it as NOW."""
    db_path, round_id = await _db_with_a_session_under_the_past_account(tmp_path)

    assignments = await other_active_team_assignments(
        db_path, round_id, SessionType.FEATURE_RACE
    )

    assert assignments == {NOW: (TEAM_A, "FEATURE_QUALIFYING")}


async def test_current_accounts_maps_the_leagues_past_accounts(tmp_path):
    db_path, _ = await _db_with_a_session_under_the_past_account(tmp_path)

    assert await current_accounts(db_path) == {PAST: NOW}


def test_an_fl_override_naming_a_past_account_names_the_current_one():
    """E37: `FL: <@PAST>` over rows the validator moves onto NOW."""
    lines = [f"FL: <@{PAST}>", "row"]

    override, rest = extract_current_fl_override(lines, SessionType.FEATURE_RACE, {PAST: NOW})

    assert (override, rest) == (NOW, ["row"])


def test_a_qualifying_paste_carries_no_override_to_map():
    lines = [f"FL: <@{PAST}>"]
    assert extract_current_fl_override(
        lines, SessionType.FEATURE_QUALIFYING, {PAST: NOW}
    ) == (None, lines)
