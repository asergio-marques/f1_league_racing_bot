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
import os
import sys

import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonService, SeasonImmutableError  # noqa: E402

SERVER_ID = 7654
ACTOR_ID = 999
ACTOR_NAME = "Race Director"


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
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', ?, 1)",
            (SERVER_ID, season_status),
        )
        season_id = cur.lastrowid

        for tier, name in enumerate(divisions, start=1):
            # 'ACTIVE' explicitly: a division only reaches it via transition_to_active, which
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
    await svc.refresh_division_status(div_id)

    assert await svc.all_divisions_finished(SERVER_ID) is True
    assert await svc.get_outstanding_rounds(SERVER_ID) == []


async def test_one_unfinalised_round_holds_the_season_open_and_is_named(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_ids[0], "FINAL")
    await svc.refresh_division_status(div_id)

    assert await svc.all_divisions_finished(SERVER_ID) is False
    outstanding = await svc.get_outstanding_rounds(SERVER_ID)
    assert [r["round_number"] for r in outstanding] == [2]
    assert outstanding[0]["division"] == "Div A"


async def test_post_race_penalty_does_not_count_as_finished(tmp_path) -> None:
    """Penalties settled, appeals still open — the results can change again."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=1)
    div_id, (round_id,) = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_id, "AWAITING_APPEAL_VERDICTS")
    await svc.refresh_division_status(div_id)

    assert await _division_status(db_path, div_id) == "ACTIVE"
    assert await svc.all_divisions_finished(SERVER_ID) is False
    assert [r["round_number"] for r in await svc.get_outstanding_rounds(SERVER_ID)] == [1]


async def test_a_cancelled_round_does_not_hold_the_season_open(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    await _set_round_status(db_path, round_ids[0], "FINAL")
    await svc.cancel_round(round_ids[1], SERVER_ID, ACTOR_ID, ACTOR_NAME)

    assert await _division_status(db_path, div_id) == "FINISHED"
    assert await svc.all_divisions_finished(SERVER_ID) is True
    assert await svc.get_outstanding_rounds(SERVER_ID) == []


async def test_a_cancelled_division_does_not_hold_the_season_open(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, divisions=("Div A", "Div B"))
    div_a, rounds_a = built["Div A"]
    div_b, _ = built["Div B"]
    svc = SeasonService(db_path)

    for rid in rounds_a:
        await _set_round_status(db_path, rid, "FINAL")
    await svc.refresh_division_status(div_a)
    await svc.cancel_division(div_b, SERVER_ID, ACTOR_ID, ACTOR_NAME)

    assert await svc.all_divisions_finished(SERVER_ID) is True
    assert await svc.get_outstanding_rounds(SERVER_ID) == []


# ---------------------------------------------------------------------------
# Division status
# ---------------------------------------------------------------------------

async def test_a_division_finishes_only_once_nothing_is_outstanding(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path)
    div_id, round_ids = built["Div A"]
    svc = SeasonService(db_path)

    assert await svc.refresh_division_status(div_id) is False
    await _set_round_status(db_path, round_ids[0], "FINAL")
    assert await svc.refresh_division_status(div_id) is False
    assert await _division_status(db_path, div_id) == "ACTIVE"

    await _set_round_status(db_path, round_ids[1], "FINAL")
    assert await svc.refresh_division_status(div_id) is True
    assert await _division_status(db_path, div_id) == "FINISHED"
    # idempotent: a second call reports it did nothing
    assert await svc.refresh_division_status(div_id) is False


@pytest.mark.parametrize("untouchable", ["SETUP", "CANCELLED"])
async def test_refresh_never_disturbs_a_setup_or_cancelled_division(tmp_path, untouchable) -> None:
    """Neither is a division that has *finished*, however empty its round list looks."""
    db_path = str(tmp_path / "bot.db")
    _, built = await _seed(db_path, rounds_per_division=0)
    div_id, _ = built["Div A"]
    svc = SeasonService(db_path)

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET status = ? WHERE id = ?", (untouchable, div_id)
        )
        await db.commit()

    assert await svc.refresh_division_status(div_id) is False
    assert await _division_status(db_path, div_id) == untouchable


async def test_activating_a_season_activates_its_divisions(tmp_path) -> None:
    """Nothing wrote 'ACTIVE' to a division before #154, so every one sat in SETUP for life."""
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), season_status="SETUP")
    div_a, _ = built["Div A"]
    div_b, _ = built["Div B"]
    svc = SeasonService(db_path)

    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'SETUP' WHERE season_id = ?", (season_id,))
        await db.execute("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", (div_b,))
        await db.commit()

    await svc.transition_to_active(season_id)

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
    svc = SeasonService(db_path)

    await _set_round_status(db_path, raced, "FINAL")
    await _set_round_status(db_path, in_appeals, "AWAITING_APPEAL_VERDICTS")

    await svc.cancel_division(div_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)

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
    svc = SeasonService(db_path)

    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET status = 'SETUP' WHERE id = ?", (div_id,))
        await db.commit()

    await svc.cancel_division(div_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)

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
    svc = SeasonService(db_path)

    await _set_round_status(db_path, a_raced, "FINAL")

    await svc.cancel_season_cascade(season_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)

    assert await _season_status(db_path, season_id) == "CANCELLED"
    assert await _division_status(db_path, div_a) == "CANCELLED"
    assert await _division_status(db_path, div_b) == "CANCELLED"
    assert await _round_status(db_path, a_raced) == "FINAL"
    assert await _round_status(db_path, a_unraced) == "CANCELLED"
    for rid in b_rounds:
        assert await _round_status(db_path, rid) == "CANCELLED"


async def test_the_season_row_is_flipped_last(tmp_path) -> None:
    """cancel_round refuses a round whose season is already archived.

    So a cascade that flipped the season first would lock itself out of its own children. This
    pins the ordering by showing the refusal the wrong order would have run into.
    """
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    _, (round_id,) = built["Div A"]
    svc = SeasonService(db_path)

    await svc.cancel_season_cascade(season_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)
    assert await _round_status(db_path, round_id) == "CANCELLED"

    # the season is archived now, so the same call is refused from here on
    with pytest.raises(SeasonImmutableError):
        await svc.cancel_round(round_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)


async def test_cancelling_a_season_leaves_an_already_cancelled_division_alone(tmp_path) -> None:
    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_b, (b_round,) = built["Div B"]
    svc = SeasonService(db_path)

    await svc.cancel_division(div_b, SERVER_ID, ACTOR_ID, ACTOR_NAME)
    await svc.cancel_season_cascade(season_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)

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


@pytest.mark.parametrize(
    "season_status, division_status, expected",
    [
        ("ACTIVE", "SETUP", "ACTIVE"),
        ("COMPLETED", "SETUP", "FINISHED"),
        ("CANCELLED", "SETUP", "CANCELLED"),
        ("SETUP", "SETUP", "SETUP"),
        # cancelled first, whatever became of the season around it
        ("COMPLETED", "CANCELLED", "CANCELLED"),
    ],
)
async def test_the_backfill_reads_each_division_from_its_season(
    tmp_path, season_status, division_status, expected
) -> None:
    """Every existing row says 'SETUP', so its season is the only evidence of what it should say."""
    import os.path

    migrations_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "src", "db", "migrations"
    )
    db_path = str(tmp_path / "bot.db")

    files = sorted(f for f in os.listdir(migrations_dir) if f.endswith(".sql"))
    before_053 = [f for f in files if f < "053"]

    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT)"
        )
        for filename in before_053:
            with open(os.path.join(migrations_dir, filename), encoding="utf-8") as fh:
                await db.executescript(fh.read())
            await db.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, '2026-01-01')",
                (filename,),
            )
            await db.commit()

        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', ?, 1)",
            (SERVER_ID, season_status),
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Div A', 1, ?, 1)",
            (season_id, division_status),
        )
        division_id = cur.lastrowid
        await db.commit()

    await run_migrations(db_path)

    assert await _division_status(db_path, division_id) == expected


# ---------------------------------------------------------------------------
# The refusal `/season complete` gives
# ---------------------------------------------------------------------------

async def _run_season_complete(divisions, outstanding, all_done=False):
    """Drive the `/season complete` callback past its decorators and capture the reply."""
    import inspect
    from unittest.mock import AsyncMock, MagicMock, patch

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
    from cogs.season_cog import SeasonCog

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    svc = cog.bot.season_service
    svc.get_active_season = AsyncMock(return_value=MagicMock(id=1, season_number=1))
    svc.get_divisions = AsyncMock(return_value=divisions)
    svc.refresh_division_status = AsyncMock(return_value=False)
    svc.all_divisions_finished = AsyncMock(return_value=all_done)
    svc.get_outstanding_rounds = AsyncMock(return_value=outstanding)

    sent: list[str] = []
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock(side_effect=lambda msg, **kw: sent.append(msg))
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    raw = inspect.unwrap(SeasonCog.season_complete.callback)

    # The archival itself belongs to season_end_service and has its own tests; stub it so this
    # exercises the gate and nothing beyond it.
    with patch("services.season_end_service.execute_season_end", new=AsyncMock()) as archived:
        await raw(cog, interaction)
    return sent, svc, archived


async def test_the_refusal_names_the_outstanding_rounds() -> None:
    divisions = [MagicMock(id=1, name="Div A", status="ACTIVE")]
    sent, svc, archived = await _run_season_complete(
        divisions,
        [{"division": "Div A", "round_number": 2, "track_name": "Monza"}],
    )
    assert len(sent) == 1
    assert "Div A — Round 2" in sent[0]
    assert "Monza" in sent[0]
    # the stale-status reread happens before the gate is consulted
    svc.refresh_division_status.assert_awaited_once_with(1)
    archived.assert_not_awaited()


async def test_the_refusal_never_names_nothing() -> None:
    """A refusal listing no rounds is the dead end #154 put leagues in.

    If the gate ever fails with nothing outstanding, the division holding the season open must
    be named — otherwise the league is told it cannot continue and not told why.
    """
    divisions = [
        MagicMock(id=1, name="Div A", status="FINISHED"),
        MagicMock(id=2, name="Div B", status="SETUP"),
    ]
    sent, _, archived = await _run_season_complete(divisions, [])
    assert len(sent) == 1
    assert "Div B" in sent[0]
    archived.assert_not_awaited()
    assert "Div A" not in sent[0], "a finished division is not holding anything open"


async def test_a_finished_season_is_not_refused() -> None:
    divisions = [MagicMock(id=1, name="Div A", status="FINISHED")]
    sent, _, archived = await _run_season_complete(divisions, [], all_done=True)
    assert sent == [], "nothing should be refused when every division has finished"
    archived.assert_awaited_once()


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
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
            "is_test_driver) VALUES (?, ?, 'ASSIGNED', ?)",
            (SERVER_ID, str(next(_NEXT_DISCORD_ID)), is_test),
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
    from unittest.mock import AsyncMock, MagicMock
    from services.season_end_service import _write_driver_history_entries

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_a, (a_round,) = built["Div A"]
    div_b, _ = built["Div B"]
    svc = SeasonService(db_path)

    await _seat_driver(db_path, div_a, season_id, name="alice")
    await _seat_driver(db_path, div_b, season_id, name="bob")

    await _set_round_status(db_path, a_round, "FINAL")
    await svc.refresh_division_status(div_a)
    await svc.cancel_division(div_b, SERVER_ID, ACTOR_ID, ACTOR_NAME)

    bot = MagicMock()
    bot.db_path = db_path
    season = MagicMock(id=season_id, season_number=1)
    await _write_driver_history_entries(season, bot)

    assert await _history(db_path) == [("Div A", 0), ("Div B", 1)]


async def test_a_test_driver_gets_a_history_entry_like_anybody_else(tmp_path) -> None:
    """Mock drivers are drivers, artificially injected — they are not filtered out."""
    from unittest.mock import MagicMock
    from services.season_end_service import _write_driver_history_entries

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    div_id, _ = built["Div A"]

    await _seat_driver(db_path, div_id, season_id, name="real", is_test=0)
    await _seat_driver(db_path, div_id, season_id, name="mock", is_test=1)

    bot = MagicMock()
    bot.db_path = db_path
    await _write_driver_history_entries(MagicMock(id=season_id, season_number=1), bot)

    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM driver_history_entries")
        (written,) = await cur.fetchone()
    assert written == 2, "both drivers should be recorded"


async def test_cancelling_a_season_records_its_drivers_as_cancelled(tmp_path) -> None:
    """A cancelled season used to leave no trace in anybody's history at all."""
    from unittest.mock import MagicMock
    from services.season_end_service import _write_driver_history_entries

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, divisions=("Div A", "Div B"), rounds_per_division=1)
    div_a, _ = built["Div A"]
    div_b, _ = built["Div B"]
    svc = SeasonService(db_path)

    await _seat_driver(db_path, div_a, season_id, name="alice")
    await _seat_driver(db_path, div_b, season_id, name="bob")

    bot = MagicMock()
    bot.db_path = db_path

    # `/season cancel` writes history first, while the season is still ACTIVE and the command is
    # still retryable, so the flag is forced rather than read from divisions not yet cascaded.
    await _write_driver_history_entries(
        MagicMock(id=season_id, season_number=1), bot, force_cancelled=True
    )
    await svc.cancel_season_cascade(season_id, SERVER_ID, ACTOR_ID, ACTOR_NAME)

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
    """Ending a season is several writes with no transaction, and its own row flips last.

    So a process that dies part-way leaves the season ACTIVE with history already written, and
    the retry a league is told to run would append a second set that nothing could tell apart.
    The unique index from migration 053 plus `INSERT OR IGNORE` is what stands in for the
    atomicity the sequence does not have — this is the test the migration comment names.
    """
    from unittest.mock import MagicMock
    from services.season_end_service import _write_driver_history_entries

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _seed(db_path, rounds_per_division=1)
    div_id, _ = built["Div A"]
    await _seat_driver(db_path, div_id, season_id, name="alice")

    bot = MagicMock()
    bot.db_path = db_path
    season = MagicMock(id=season_id, season_number=1)

    await _write_driver_history_entries(season, bot)
    first = await _history(db_path)
    await _write_driver_history_entries(season, bot)

    assert await _history(db_path) == first == [("Div A", 0)]


async def test_a_driver_moved_between_divisions_keeps_an_entry_for_each(tmp_path) -> None:
    """The unique key includes the division, so two divisions in one season is not a duplicate."""
    from unittest.mock import MagicMock
    from services.season_end_service import _write_driver_history_entries

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

    bot = MagicMock()
    bot.db_path = db_path
    await _write_driver_history_entries(MagicMock(id=season_id, season_number=1), bot)

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

    from services.result_submission_service import run_result_submission_job

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
    assert await SeasonService(db_path).all_divisions_finished(SERVER_ID) is True


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
