"""The restart notice names a driver by the account they use now (issue #243, E38)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from bot import staged_penalties_warning  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 2435


async def test_a_penalty_applied_under_a_past_account_names_the_current_one(tmp_path):
    db_path = os.path.join(str(tmp_path), "warning.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
            "VALUES (?, '8101', 'ASSIGNED')",
            (SERVER_ID,),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = '8102' WHERE id = ?",
            (cursor.lastrowid,),
        )
        await db.commit()

    text = await staged_penalties_warning(db_path, SERVER_ID, [
        {"session_type": "FEATURE_RACE", "penalty_type": "TIME", "penalty_seconds": 5,
         "driver_user_id": 8101},
        {"session_type": "FEATURE_RACE", "penalty_type": "DSQ", "driver_user_id": 9999},
    ])

    assert "• <@8102> | Feature Race | **+5s**" in text
    assert "• <@9999> | Feature Race | **DSQ**" in text
    assert "<@8101>" not in text
