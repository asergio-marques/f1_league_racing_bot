"""The copy `/division duplicate` makes, through the change queue.

Issue #208. `duplicate_division` was uncovered at the service level — the cogs that call it were
tested with the service stubbed, which left the SQL itself unread. Since #283 the copy is the one
save of the change type `core.division_duplicate`, so it is read here as a league manager meets
it: the command asked, the queue run, and the rows the save left.

**A duplicated division copies the calendar, shifted, and nothing else.** Every round is copied
with the same format and track and its time moved by the offset; the phase flags start at zero,
because a copy of a round whose phase 1 has run is a new round nobody has forecast. The source is
left as it was. The copy is renumbered by date.

**The copy is one save.** The division, its rounds, their renumbering, its teams and its audit
entry are written together, so a fault part-way leaves no division and no round behind, and never
a division with its rounds and no teams.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection
from tests.support.setup_rounds import ask_duplicate, round_moments, setup_league

#: Pro's rounds: a normal round at Silverstone on 1 March and a sprint round at Monza on 8 March
#: 2026, both at 18:00 UTC.
_PRO_ROUNDS = (
    ("NORMAL", "Silverstone", datetime(2026, 3, 1, 18, tzinfo=timezone.utc)),
    ("SPRINT", "Monza", datetime(2026, 3, 8, 18, tzinfo=timezone.utc)),
)


async def _league(tmp_path, rounds=_PRO_ROUNDS):
    """`setup_league`'s league, season #1 being set up in Placements and holding Pro (tier 1, role
    10) with *rounds*."""
    return await setup_league(tmp_path, divisions=(("Pro", 1, 10, rounds),))


async def test_the_copys_phase_flags_start_clear(tmp_path):
    """A copy of a round whose phase 1 has run is a new round nobody has forecast. Season #1 is
    being set up, in Placements, holding Pro with its two rounds, each with its first two phases
    marked done. The manager runs `/division duplicate` of Pro as Am at tier 2 and the queue runs
    it: none of Am's rounds has a phase marked done."""
    from tests.support.change_queue import run_queue

    league = await _league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE rounds SET phase1_done = 1, phase2_done = 1")
        await db.commit()

    await ask_duplicate(league)
    await run_queue(league.bot)

    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT r.phase1_done, r.phase2_done, r.phase3_done FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE d.name = 'Am'"
        )
        assert [tuple(row) for row in await cursor.fetchall()] == [(0, 0, 0), (0, 0, 0)]


async def test_the_source_is_untouched(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro with its rounds on 1 and 8 March. The
    manager runs `/division duplicate` of Pro as Am at tier 2, shifted by seven days, and the queue
    runs it: Pro's rounds stand at their own moments, numbered as they were."""
    from tests.support.change_queue import run_queue

    league = await _league(tmp_path)

    await ask_duplicate(league, day_offset=7)
    await run_queue(league.bot)

    assert await round_moments(league.db_path, "Pro") == [
        (number, fmt, track, at) for number, (fmt, track, at) in enumerate(_PRO_ROUNDS, 1)
    ]


#: Faults part-way through a duplicate: a trigger refusing the copy's rounds as they are
#: inserted, after the division is, or refusing any write of a round's number.
FAIL_COPYING_ROUNDS = (
    "CREATE TRIGGER fail_copying BEFORE INSERT ON rounds "
    "BEGIN SELECT RAISE(ABORT, 'duplicate failed'); END"
)
FAIL_RENUMBERING = (
    "CREATE TRIGGER fail_renumbering BEFORE UPDATE OF round_number ON rounds "
    "BEGIN SELECT RAISE(ABORT, 'duplicate failed'); END"
)


@pytest.mark.parametrize("fault", [
    pytest.param(FAIL_COPYING_ROUNDS, id="copying-the-rounds"),
    pytest.param(FAIL_RENUMBERING, id="renumbering"),
    pytest.param(None, id="writing-the-audit-entry"),
])
async def test_a_duplicate_that_fails_part_way_leaves_nothing_behind(tmp_path, fault):
    """Season #1 is being set up, in Placements, holding Pro (tier 1) with its two rounds. The
    manager runs `/division duplicate` of Pro as Am at tier 2, and as the queue runs it the
    database fails once the division is inserted: as the rounds are copied, as they are
    renumbered, or as the audit entry is written, after the teams are seeded. It is one save, so
    nothing of it is kept: Pro is the season's only division, its two rounds the only rounds, no
    team is seeded and no audit entry written. The league's lane stops at the job, as any failed
    job stops it. Once a league admin discards the stopped job, the manager's reply says that
    nothing was duplicated and to run the command again, and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said

    league = await _league(tmp_path)
    asked = await ask_duplicate(league)
    if fault is None:
        with fault_in("leaguebot.core.services.change_queue.record_change_on"):
            await run_queue(league.bot)
    else:
        async with get_connection(league.db_path) as db:
            await db.execute(fault)
            await db.commit()
        await run_queue(league.bot)

    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT name FROM divisions")
        assert [row[0] for row in await cursor.fetchall()] == ["Pro"]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "WHERE d.name = 'Pro'"
        )
        pro_rounds = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM rounds")
        assert (await cursor.fetchone())[0] == pro_rounds == 2
        cursor = await db.execute("SELECT COUNT(*) FROM team_instances")
        assert (await cursor.fetchone())[0] == 0
    assert await audit_rows(league.db_path) == []
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was duplicated: no division was created. Run `/division duplicate` again."
    )
    assert await stopped_job(league.db_path) is None


async def test_a_division_with_no_rounds_duplicates_empty(tmp_path):
    """Season #1 is being set up, in Placements, holding Pro with no rounds. The manager runs
    `/division duplicate` of Pro as Am at tier 2, shifted by a day, and the queue runs it: Am
    stands with no rounds, and the reply says so beneath the divisions, with no warning."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league = await _league(tmp_path, rounds=())

    asked = await ask_duplicate(league, day_offset=1)
    await run_queue(league.bot)

    assert await round_moments(league.db_path, "Am") == []
    assert last_said(asked) == (
        "✅ Division **Am** (Tier 2) created from **Pro** (offset: +1d +0.0h).\n\n"
        "**Divisions:**\n  📂 **Pro** (Tier 1) | <@&10>\n  📂 **Am** (Tier 2) | <@&556>\n\n"
        "**Am rounds:**\n*(no rounds)*"
    )
