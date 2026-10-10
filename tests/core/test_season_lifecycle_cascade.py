"""A season can be completed, and cancelling one cascades to what it holds.

`/season complete` gated on `rounds.finalized`: a column migration 019 added, migration 026 read
once to seed `result_status`, and nothing in `src/` has ever written. Every round therefore read
as outstanding however completely it was raced, the gate never lifted, and — because a server
holds one live season at a time — a league that had finished its championship could not begin the
next one. The only escape was `/season cancel`, which says a season should never have existed.

The lifecycle that replaces it: a round is finished when its results are FINAL or it is cancelled;
a division is finished when every one of its rounds is; a season may be completed when every
division is finished or cancelled. Cancelling runs the other way — a cancelled division takes its
unraced rounds with it, and a cancelled season takes its divisions.

Two things are deliberately *not* symmetrical, and are pinned here because a later reader might
reasonably tidy them away:

- POST_RACE_PENALTY is not finished. The penalties are settled but the appeals are still open and
  the results can change again. Only FINAL ends a round.
- A round already raced is never cancelled by a cascade. Cancelling it would say it never
  happened while its results sat in the archive contradicting that.

Issue #154.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from unittest.mock import MagicMock

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonService, refresh_division_status_on

SERVER_ID = 7654
ACTOR_ID = 999
ACTOR_NAME = "Race Director"

# The moment a cancellation is recorded at, pinned.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


async def _seed(db_path, *, divisions=("Div A",), rounds_per_division=2, season_status="ACTIVE"):
    """Seed one season with divisions and rounds. Returns (season_id, {name: (div_id, [round_ids])})."""
    await run_migrations(db_path)
    built: dict[str, tuple[int, list[int]]] = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', ?, 1)",
            (season_status,),
        )
        season_id = cur.lastrowid

        for tier, name in enumerate(divisions, start=1):
            # 'ACTIVE' explicitly: a division only reaches it via transition_to_active_on, which
            # has its own test below.
            cur = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, "
                "status, tier) VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
                (season_id, name, 10 + tier, 20 + tier, tier),
            )
            div_id = cur.lastrowid
            round_ids = []
            for i in range(1, rounds_per_division + 1):
                cur = await db.execute(
                    "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, "
                    "format) VALUES (?, ?, 'Bahrain', ?, 'NORMAL')",
                    (div_id, i, f"2026-0{i + 3}-01T12:00:00"),
                )
                round_ids.append(cur.lastrowid)
            built[name] = (div_id, round_ids)
        await db.commit()
    return season_id, built


async def _set_round_status(db_path, round_id, status):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = ? WHERE id = ?", (status, round_id))
        await db.commit()


async def _division_status(db_path, division_id):
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status FROM divisions WHERE id = ?", (division_id,))
        return (await cur.fetchone())["status"]


async def _round_status(db_path, round_id):
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
        return (await cur.fetchone())["status"]


async def _season_status(db_path, season_id):
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status FROM seasons WHERE id = ?", (season_id,))
        return (await cur.fetchone())["status"]


async def _cancel_round(db_path, round_id):
    """Cancel *round_id* with `cancel_round_on` on one connection, as the round's cancellation on
    the change queue does in its save, and commit it, which `cancel_round_on` does not. Gives the
    round's old status, or `None` where it moved nothing."""
    from leaguebot.core.services.season_service import cancel_round_on

    async with get_connection(db_path) as db:
        old = await cancel_round_on(
            db, round_id, actor_id=ACTOR_ID, actor_name=ACTOR_NAME, now=NOW
        )
        await db.commit()
    return old


async def _cancel_division(db_path, division_id):
    """Cancel *division_id* with `cancel_division_on` on one connection, as the division's
    cancellation on the change queue does in its save, and commit it. Moving the season on is
    the change's, not this form's."""
    from leaguebot.core.services.season_service import cancel_division_on

    async with get_connection(db_path) as db:
        called_off = await cancel_division_on(
            db, division_id, actor_id=ACTOR_ID, actor_name=ACTOR_NAME, now=NOW
        )
        await db.commit()
    return called_off


async def _refresh(db_path, division_id):
    """Refresh *division_id* with `refresh_division_status_on` on one connection, committed, as
    every caller's save does. Gives whether it moved the division."""
    async with get_connection(db_path) as db:
        moved = await refresh_division_status_on(db, division_id)
        await db.commit()
    return moved


async def _every_division_done(db_path):
    """Whether every division of the season being raced is finished or cancelled, read from the
    divisions' own statuses: the rule the completion reads, without asking any service."""
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM divisions d JOIN seasons s ON s.id = d.season_id "
            "WHERE s.status = 'ACTIVE' AND d.status NOT IN ('FINISHED', 'CANCELLED')"
        )
        (open_divisions,) = await cur.fetchone()
    return open_divisions == 0


async def _cancel_season(db_path, season_id):
    """Cancel *season_id* as the season's cancellation on the change queue records it, on one
    connection: every division not yet cancelled with its rounds (`cancel_season_divisions_on`),
    then the season's own row (`cancel_season_on`), last, committed once."""
    from leaguebot.core.services.season_service import (
        cancel_season_divisions_on,
        cancel_season_on,
    )

    async with get_connection(db_path) as db:
        await cancel_season_divisions_on(
            db, season_id, actor_id=ACTOR_ID, actor_name=ACTOR_NAME, now=NOW
        )
        await cancel_season_on(db, season_id)
        await db.commit()


# ---------------------------------------------------------------------------
# The gate on completing a season
# ---------------------------------------------------------------------------

async def test_a_fully_raced_season_can_be_completed(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    for rid in round_ids:
        await _set_round_status(db_path, rid, "FINAL")
    await _refresh(db_path, div_id)

    assert await _every_division_done(db_path) is True
    assert await svc.get_outstanding_rounds() == []


async def test_one_unfinalised_round_holds_the_season_open_and_is_named(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_ids[0], "FINAL")
    await _refresh(db_path, div_id)

    assert await _every_division_done(db_path) is False
    outstanding = await svc.get_outstanding_rounds()
    assert [r["round_number"] for r in outstanding] == [2]
    assert outstanding[0]["division"] == "Div A"


async def test_post_race_penalty_does_not_count_as_finished(tmp_path) -> None:
    """Penalties settled, appeals still open — the results can change again."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, (round_id,) = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_id, "AWAITING_APPEAL_VERDICTS")
    await _refresh(db_path, div_id)

    assert await _division_status(db_path, div_id) == "ACTIVE"
    assert await _every_division_done(db_path) is False
    assert [r["round_number"] for r in await svc.get_outstanding_rounds()] == [1]


async def test_a_cancelled_round_does_not_hold_the_season_open(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_ids[0], "FINAL")
    await _cancel_round(db_path, round_ids[1])

    assert await _division_status(db_path, div_id) == "FINISHED"
    assert await _every_division_done(db_path) is True
    assert await svc.get_outstanding_rounds() == []


async def test_a_cancelled_division_does_not_hold_the_season_open(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, divisions=("Div A", "Div B"))
    div_a, rounds_a = built["Div A"]
    div_b, _ = built["Div B"]
    svc = SeasonService(db_path)

    for rid in rounds_a:
        await _set_round_status(db_path, rid, "FINAL")
    await _refresh(db_path, div_a)
    await _cancel_division(db_path, div_b)

    assert await _every_division_done(db_path) is True
    assert await svc.get_outstanding_rounds() == []


# ---------------------------------------------------------------------------
# Division status
# ---------------------------------------------------------------------------

async def test_a_division_finishes_only_once_nothing_is_outstanding(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]

    assert await _refresh(db_path, div_id) is False
    await _set_round_status(db_path, round_ids[0], "FINAL")
    assert await _refresh(db_path, div_id) is False
    assert await _division_status(db_path, div_id) == "ACTIVE"

    await _set_round_status(db_path, round_ids[1], "FINAL")
    assert await _refresh(db_path, div_id) is True
    assert await _division_status(db_path, div_id) == "FINISHED"
    # idempotent: a second call reports it did nothing
    assert await _refresh(db_path, div_id) is False


@pytest.mark.parametrize("untouchable", ["SETUP", "CANCELLED"])
async def test_refresh_never_disturbs_a_setup_or_cancelled_division(tmp_path, untouchable) -> None:
    """Neither is a division that has *finished*, however empty its round list looks."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=0)
    div_id, _ = built["Div A"]

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET status = ? WHERE id = ?", (untouchable, div_id)
        )
        await db.commit()

    assert await _refresh(db_path, div_id) is False
    assert await _division_status(db_path, div_id) == untouchable


async def test_activating_a_season_activates_its_divisions(tmp_path) -> None:
    """Nothing wrote 'ACTIVE' to a division before #154, so every one sat in SETUP for life."""
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), season_status="SETUP")
    div_a, _ = built["Div A"]
    div_b, _ = built["Div B"]

    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'PLACEMENTS' WHERE id = ?", (season_id,))
        await db.execute("UPDATE divisions SET status = 'SETUP' WHERE season_id = ?", (season_id,))
        await db.execute("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", (div_b,))
        await db.commit()

    from leaguebot.core.services.season_service import transition_to_active_on

    async with get_connection(db_path) as db:
        assert await transition_to_active_on(db, season_id) is True
        await db.commit()

    assert await _season_status(db_path, season_id) == "ACTIVE"
    assert await _division_status(db_path, div_a) == "ACTIVE"
    # a division called off during setup does not start racing because the season did
    assert await _division_status(db_path, div_b) == "CANCELLED"


# ---------------------------------------------------------------------------
# Cascades
# ---------------------------------------------------------------------------

async def test_cancelling_a_division_takes_its_unraced_rounds(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=3)
    div_id, (raced, in_appeals, unraced) = built["Div A"]

    await _set_round_status(db_path, raced, "FINAL")
    await _set_round_status(db_path, in_appeals, "AWAITING_APPEAL_VERDICTS")

    await _cancel_division(db_path, div_id)

    assert await _division_status(db_path, div_id) == "CANCELLED"
    assert await _round_status(db_path, unraced) == "CANCELLED"
    # both raced rounds keep their status and their results
    assert await _round_status(db_path, raced) == "FINAL"
    assert await _round_status(db_path, in_appeals) == "AWAITING_APPEAL_VERDICTS"


async def test_cancelling_a_division_audits_its_real_previous_status(tmp_path) -> None:
    """The audit row hardcoded "ACTIVE" as the old value whatever the division actually said."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, _ = built["Div A"]

    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'SETUP' WHERE id = ?", (div_id,))
        await db.commit()

    await _cancel_division(db_path, div_id)

    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT old_value, new_value FROM audit_entries "
            "WHERE division_id = ? AND change_type = 'division.status'",
            (div_id,),
        )
        row = await cur.fetchone()
    assert (row["old_value"], row["new_value"]) == ("SETUP", "CANCELLED")


async def test_cancelling_a_season_cascades_to_divisions_and_unraced_rounds(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=2)
    div_a, (a_raced, a_unraced) = built["Div A"]
    div_b, b_rounds = built["Div B"]

    await _set_round_status(db_path, a_raced, "FINAL")

    await _cancel_season(db_path, season_id)

    assert await _season_status(db_path, season_id) == "CANCELLED"
    assert await _division_status(db_path, div_a) == "CANCELLED"
    assert await _division_status(db_path, div_b) == "CANCELLED"
    assert await _round_status(db_path, a_raced) == "FINAL"
    assert await _round_status(db_path, a_unraced) == "CANCELLED"
    for rid in b_rounds:
        assert await _round_status(db_path, rid) == "CANCELLED"


async def test_the_season_row_is_flipped_last(tmp_path) -> None:
    """cancel_round_on leaves alone a round whose season is already archived, giving `None`.

    So a cancellation that flipped the season first would lock itself out of its own children.
    This pins the ordering: Div A's one round is cancelled with its division, the season's row
    is written after it, and once the season is archived the same round cancelled again moves
    nothing.
    """
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    _, (round_id,) = built["Div A"]

    await _cancel_season(db_path, season_id)
    assert await _round_status(db_path, round_id) == "CANCELLED"

    # the season is archived now, so the same call moves nothing from here on
    assert await _cancel_round(db_path, round_id) is None
    assert await _round_status(db_path, round_id) == "CANCELLED"


async def test_cancelling_a_season_never_moves_it_to_pending_completion(tmp_path) -> None:
    """Season 1 is ongoing; Div A has finished, both its rounds final, and Div B, its last
    running division, has two rounds not run. Cancelling the season cancels Div B and its rounds
    on the way, the last division to be done, and the season ends CANCELLED without ever having
    been moved to Pending completion on the way (#439, slice 4b). The division's own
    cancellation moves its season on in its save; the season's shares the division's write and
    must not. Every stage the season is written is caught by a trigger of the test's own, since
    the season's final stage follows its status whatever came before."""
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=2)
    div_a, a_rounds = built["Div A"]
    div_b, _ = built["Div B"]
    for rid in a_rounds:
        await _set_round_status(db_path, rid, "FINAL")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", (div_a,))
        await db.execute("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", (season_id,))
        await db.execute("CREATE TABLE stages_written (stage TEXT)")
        await db.execute(
            "CREATE TRIGGER catch_stages AFTER UPDATE OF stage ON seasons "
            "BEGIN INSERT INTO stages_written (stage) VALUES (NEW.stage); END"
        )
        await db.commit()

    await _cancel_season(db_path, season_id)

    assert await _division_status(db_path, div_b) == "CANCELLED"
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status, stage FROM seasons WHERE id = ?", (season_id,))
        row = await cur.fetchone()
        written = [r["stage"] for r in await (
            await db.execute("SELECT stage FROM stages_written ORDER BY rowid")
        ).fetchall()]
    assert (row["status"], row["stage"]) == ("CANCELLED", "CANCELLED")
    assert written, "the trigger caught no stage at all, so it proves nothing"
    assert "PENDING_COMPLETION" not in written


async def test_cancelling_a_season_audits_each_round_with_the_status_it_was_cancelled_from(
    tmp_path,
) -> None:
    """Season 1's Div A has round 1 waiting for its results, its race time passed and no
    submission open, and round 2 not run. Cancelling the season cancels both, and each
    round.status audit reads from the status that round really had to CANCELLED, never from
    "ACTIVE", which is no round status at all (#439, slice 4b, A10)."""
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=2)
    _, (awaiting, not_run) = built["Div A"]
    await _set_round_status(db_path, awaiting, "AWAITING_RESULTS")

    await _cancel_season(db_path, season_id)

    assert await _round_status(db_path, awaiting) == "CANCELLED"
    assert await _round_status(db_path, not_run) == "CANCELLED"
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT old_value, new_value FROM audit_entries WHERE change_type = 'round.status'"
        )
        audits = sorted((r["old_value"], r["new_value"]) for r in await cur.fetchall())
    assert audits == [("AWAITING_RESULTS", "CANCELLED"), ("NOT_RUN", "CANCELLED")]


async def test_cancelling_a_season_leaves_an_already_cancelled_division_alone(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_b, (b_round,) = built["Div B"]

    await _cancel_division(db_path, div_b)
    await _cancel_season(db_path, season_id)

    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM audit_entries "
            "WHERE division_id = ? AND change_type = 'division.status'",
            (div_b,),
        )
        (audited,) = await cur.fetchone()
    assert audited == 1, "the second pass should not re-cancel or re-audit it"
    assert await _round_status(db_path, b_round) == "CANCELLED"


# ---------------------------------------------------------------------------
# Migration 053
# ---------------------------------------------------------------------------

async def test_the_division_status_check_is_enforced_again(tmp_path) -> None:
    """007 constrained the column; 009's table rebuild silently dropped the CHECK."""
    import sqlite3

    db_path = str(tmp_path / "bot.db")
    season_id, _ = await _seed(db_path, rounds_per_division=0)

    async with get_connection(db_path) as db:
        for good in ("SETUP", "ACTIVE", "FINISHED", "CANCELLED"):
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, ?, 1, ?, 9)",
                (season_id, f"ok-{good}", good),
            )
        with pytest.raises(sqlite3.IntegrityError):
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, 'bogus', 1, 'NONSENSE', 9)",
                (season_id,),
            )


async def test_the_dead_finalized_column_is_gone(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        cur = await db.execute("PRAGMA table_info(rounds)")
        columns = {row["name"] for row in await cur.fetchall()}
    assert "finalized" not in columns
    assert "result_status" not in columns, "merged into the single status chain by 053"
    assert "status" in columns


# ---------------------------------------------------------------------------
# Driver history records a cancellation
# ---------------------------------------------------------------------------
#
# A cancelled division is league history — it happened, and the drivers raced in it. What the
# record must not do is read as a division that ran to its end. The flag hangs off the division
# because cancellation only ever reaches a driver through one: cancelling a season cancels each
# of its divisions, and cancelling a division cancels each of its rounds not yet run.


#: Discord ids are allocated in order rather than hashed from the name: `hash()` is salted per
#: process, so a hashed id would differ between runs and between hosts.
_NEXT_DISCORD_ID = itertools.count(500000)


async def _seat_driver(db_path, division_id, season_id, *, name, is_test=0):
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver) VALUES (?, 'ASSIGNED', ?)",
            (str(next(_NEXT_DISCORD_ID)), is_test),
        )
        profile_id = cur.lastrowid
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, division_id, season_id) "
            "VALUES (?, ?, ?)",
            (profile_id, division_id, season_id),
        )
        await db.commit()
    return profile_id


async def _history(db_path):
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT division_name, cancelled FROM driver_history_entries ORDER BY division_name"
        )
        return [(r["division_name"], r["cancelled"]) for r in await cur.fetchall()]


async def test_history_marks_a_cancelled_division_and_not_a_finished_one(tmp_path) -> None:
    from leaguebot.core.services.season_end_service import write_driver_history_entries_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_a, (a_round,) = built["Div A"]
    div_b, _ = built["Div B"]

    await _seat_driver(db_path, div_a, season_id, name="alice")
    await _seat_driver(db_path, div_b, season_id, name="bob")

    await _set_round_status(db_path, a_round, "FINAL")
    await _refresh(db_path, div_a)
    await _cancel_division(db_path, div_b)

    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(db, season_id, 1)
        await db.commit()

    assert await _history(db_path) == [("Div A", 0), ("Div B", 1)]


async def test_a_test_driver_gets_a_history_entry_like_anybody_else(tmp_path) -> None:
    """Mock drivers are drivers, artificially injected — they are not filtered out."""
    from leaguebot.core.services.season_end_service import write_driver_history_entries_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    div_id, _ = built["Div A"]

    await _seat_driver(db_path, div_id, season_id, name="real", is_test=0)
    await _seat_driver(db_path, div_id, season_id, name="mock", is_test=1)

    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(db, season_id, 1)
        await db.commit()

    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM driver_history_entries")
        (written,) = await cur.fetchone()
    assert written == 2, "both drivers should be recorded"


async def test_cancelling_a_season_records_its_drivers_as_cancelled(tmp_path) -> None:
    """A cancelled season used to leave no trace in anybody's history at all."""
    from leaguebot.core.services.season_end_service import write_driver_history_entries_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_a, _ = built["Div A"]
    div_b, _ = built["Div B"]

    await _seat_driver(db_path, div_a, season_id, name="alice")
    await _seat_driver(db_path, div_b, season_id, name="bob")

    # The flag is forced rather than read from the divisions, so the history is marked cancelled
    # whichever of them the cancellation has reached when it is written.
    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(db, season_id, 1, force_cancelled=True)
        await db.commit()
    await _cancel_season(db_path, season_id)

    assert await _history(db_path) == [("Div A", 1), ("Div B", 1)]


async def test_the_flag_defaults_to_not_cancelled(tmp_path) -> None:
    """Migration 053 needs no backfill: the table is empty everywhere (see the file's comment)."""
    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        cur = await db.execute("PRAGMA table_info(driver_history_entries)")
        cols = {r["name"]: r for r in await cur.fetchall()}
    assert "cancelled" in cols
    assert cols["cancelled"]["dflt_value"] == "0"
    assert cols["cancelled"]["notnull"] == 1


async def test_writing_the_history_twice_adds_nothing(tmp_path) -> None:
    """The history written a second time for the same season adds no second set.

    The unique index from migration 053 plus `INSERT OR IGNORE` is what keeps a second write of
    a season's history from appending a set that nothing could tell apart from the first — this
    is the test the migration comment names.
    """
    from leaguebot.core.services.season_end_service import write_driver_history_entries_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    div_id, _ = built["Div A"]
    await _seat_driver(db_path, div_id, season_id, name="alice")

    async def write_once():
        async with get_connection(db_path) as db:
            await write_driver_history_entries_on(db, season_id, 1)
            await db.commit()

    await write_once()
    first = await _history(db_path)
    await write_once()

    assert await _history(db_path) == first == [("Div A", 0)]


async def test_a_driver_moved_between_divisions_keeps_an_entry_for_each(tmp_path) -> None:
    """The unique key includes the division, so two divisions in one season is not a duplicate."""
    from leaguebot.core.services.season_end_service import write_driver_history_entries_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_a, _ = built["Div A"]
    div_b, _ = built["Div B"]

    profile_id = await _seat_driver(db_path, div_a, season_id, name="alice")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, division_id, season_id) "
            "VALUES (?, ?, ?)",
            (profile_id, div_b, season_id),
        )
        await db.commit()

    async with get_connection(db_path) as db:
        await write_driver_history_entries_on(db, season_id, 1)
        await db.commit()

    assert await _history(db_path) == [("Div A", 0), ("Div B", 0)]


# ---------------------------------------------------------------------------
# The one clock-driven transition
# ---------------------------------------------------------------------------
#
# Every other transition happens because somebody did something. This one happens because the
# round's moment arrived, so it rides the results-submission job, which fires at exactly that
# moment for every round whatever its format and whatever the modules.


async def _run_the_round_job(db_path, round_id, *, results_enabled):
    """Fire the round's scheduled job with the results module on or off.

    The job goes on to build a submission channel against a real guild, which these tests do not
    have, so anything after the transition is allowed to fail: the state is committed before that
    point and is what is being asserted. Written this way rather than by patching the world,
    because patching would have to know the job's shape and would stop testing it the moment that
    changed.
    """
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.results.services.result_submission_service import run_result_submission_job

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)

    with contextlib.suppress(Exception):
        await run_result_submission_job(round_id, bot)


async def test_the_round_begins_awaiting_results_when_its_moment_arrives(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, (round_id,) = built["Div A"]

    assert await _round_status(db_path, round_id) == "NOT_RUN"
    await _run_the_round_job(db_path, round_id, results_enabled=True)

    assert await _round_status(db_path, round_id) == "AWAITING_RESULTS"
    # still outstanding: somebody has to enter the results
    assert await _division_status(db_path, div_id) == "ACTIVE"


async def test_without_the_results_module_the_round_ends_when_its_moment_arrives(tmp_path) -> None:
    """A league that does not run results has nothing to await, so the round ends there.

    Without this the round would sit outstanding for ever and its season could never be
    completed — issue #154 over again for every league that does not run the module.
    """
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, (round_id,) = built["Div A"]

    await _run_the_round_job(db_path, round_id, results_enabled=False)

    assert await _round_status(db_path, round_id) == "FINAL"
    # and the division finishes with it, so the season can be completed
    assert await _division_status(db_path, div_id) == "FINISHED"
    assert await _every_division_done(db_path) is True


async def test_the_moment_arriving_does_not_disturb_a_round_already_under_way(tmp_path) -> None:
    """The job can fire again — on a restart replaying missed work — and must not rewind."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    _, (round_id,) = built["Div A"]

    await _set_round_status(db_path, round_id, "AWAITING_APPEAL_VERDICTS")
    await _run_the_round_job(db_path, round_id, results_enabled=True)
    assert await _round_status(db_path, round_id) == "AWAITING_APPEAL_VERDICTS"

    await _set_round_status(db_path, round_id, "CANCELLED")
    await _run_the_round_job(db_path, round_id, results_enabled=False)
    assert await _round_status(db_path, round_id) == "CANCELLED"


_XFAIL_ROUND_JOB = "#439: the results-off round job still saves in two commits and winds the season down itself"


async def test_without_the_results_module_the_round_and_its_division_are_saved_together(
    tmp_path, monkeypatch
) -> None:
    """The round's move off Not run and its division's refresh are one save (#439, slice 5).

    Today the round's move commits first and the refresh commits on its own, so a refresh that
    fails leaves the round final over a division never told. Both forms of the refresh are made
    to raise, today's method and the on-connection form the job saves through, so the test reads
    the same before and after the change.
    """
    from leaguebot.core.services import season_service
    from leaguebot.results.services import result_submission_service

    async def refused(*args, **kwargs):
        raise RuntimeError("the division could not be refreshed")

    monkeypatch.setattr(
        season_service.SeasonService, "refresh_division_status", refused, raising=False
    )
    monkeypatch.setattr(season_service, "refresh_division_status_on", refused)
    monkeypatch.setattr(
        result_submission_service, "refresh_division_status_on", refused, raising=False
    )

    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, (round_id,) = built["Div A"]

    await _run_the_round_job(db_path, round_id, results_enabled=False)

    assert await _round_status(db_path, round_id) == "NOT_RUN"
    assert await _division_status(db_path, div_id) == "ACTIVE"


@pytest.mark.xfail(strict=True, reason=_XFAIL_ROUND_JOB)
async def test_without_the_results_module_the_wind_down_is_asked_of_the_queue(tmp_path) -> None:
    """The season's wind-down is asked of the change queue as the bot's, not run in-process
    (#439, slice 5). Today the job calls `wind_down_ongoing` itself and logs its failure."""
    import contextlib
    from unittest.mock import AsyncMock

    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.results.services.result_submission_service import run_result_submission_job

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    _, (round_id,) = built["Div A"]
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = ?", (season_id,)
        )
        await db.commit()

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=False)
    bot.change_queue.ask = AsyncMock(return_value=1)
    with contextlib.suppress(Exception):
        await run_result_submission_job(round_id, bot)

    assert await _round_status(db_path, round_id) == "FINAL"
    bot.change_queue.ask.assert_awaited_once()
    call = bot.change_queue.ask.await_args
    kind = call.args[0] if call.args else call.kwargs["kind"]
    assert kind == "season.wind_down"
    assert call.kwargs["origin"] is ChangeOrigin.BOT
