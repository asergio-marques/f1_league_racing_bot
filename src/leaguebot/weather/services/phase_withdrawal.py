"""Withdrawing a round's forecast phases on the save a change hands weather.

`/round amend` is a change on the change queue, and its one save writes the amended round and
everything the amendment withdraws. What it withdraws for weather is weather's own: each
withdrawn phase's flag, its results, and the session data it drew. Weather writes it, on the
connection the save hands it, and commits nothing, so it outlasts nothing the save does not.

Every statement is fixed SQL, each column named in its statement and nothing spliced in, so the
architecture rules that read the tables and columns a module writes can read them here.
"""

from __future__ import annotations

from collections.abc import Iterable

import aiosqlite


async def withdraw_phases_on(
    db: aiosqlite.Connection, round_id: int, phases: Iterable[int]
) -> None:
    """Mark each of *phases* of the round not performed on *db*, committing nothing.

    Only the phases given are touched, and a phase that still stands keeps its flag, which is
    what lets the rules refuse a later track change on the strength of a forecast that is
    genuinely still posted. Each withdrawn phase has its flag cleared and its results
    invalidated; Phase 2 chose the slot type and Phase 3 the slots, so each clears the session
    data of its own and a Phase 2 that stands keeps its choice even where Phase 3 is drawn again.
    """
    withdrawn = sorted(set(phases))
    for phase in withdrawn:
        if phase == 1:
            await db.execute("UPDATE rounds SET phase1_done = 0 WHERE id = ?", (round_id,))
        elif phase == 2:
            await db.execute("UPDATE rounds SET phase2_done = 0 WHERE id = ?", (round_id,))
        elif phase == 3:
            await db.execute("UPDATE rounds SET phase3_done = 0 WHERE id = ?", (round_id,))
        await db.execute(
            "UPDATE phase_results SET status = 'INVALIDATED' "
            "WHERE round_id = ? AND phase_number = ?",
            (round_id, phase),
        )
    if 2 in withdrawn:
        await db.execute(
            "UPDATE sessions SET phase2_slot_type = NULL WHERE round_id = ?", (round_id,)
        )
    if 3 in withdrawn:
        await db.execute(
            "UPDATE sessions SET phase3_slots = NULL WHERE round_id = ?", (round_id,)
        )
