"""Which forecasts a round has had drawn: weather's own record of it.

`rounds.phase1_done` to `phase3_done` are the weather module's own columns on core's `rounds`
table (architecture.md, "Each table is read and written by its owner alone"). Core does not carry
them on its `Round`: where it must judge by them, as an amendment does of the round it changes,
it asks the weather module through its `phases_drawn_read` hook, and this is the reader the
module answers with.
"""

from __future__ import annotations

from collections.abc import Sequence

import aiosqlite

from leaguebot.core.db.database import tuple_rows_in


async def phases_drawn_on(
    db: aiosqlite.Connection, round_ids: Sequence[int]
) -> dict[int, dict[int, bool]]:
    """Whether each of Phases 1 to 3 has been drawn, by phase number, for each round in
    *round_ids*, by round id; a round that does not exist is left out.

    Reads on the connection it is handed, so that an amendment's save sees what that save has
    written and not yet committed. Reads only.
    """
    rows = await tuple_rows_in(
        db,
        "SELECT id, phase1_done, phase2_done, phase3_done FROM rounds WHERE id IN ({marks})",
        round_ids,
    )
    return {
        int(row[0]): {1: bool(row[1]), 2: bool(row[2]), 3: bool(row[3])} for row in rows
    }
