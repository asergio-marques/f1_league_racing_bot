"""The writes of a season's approval and of the two cancellations, on the save they are handed
(#439, slices 4a and 4b).

The approval is one save on the change queue: the sessions of every round, the commitment of the
placements, the points snapshot and the season's move to Ongoing commit together or not at all. So
each of core's three writes has an `_on` form, writing on the connection it is handed and
committing nothing; the queue's step commits once, after the last of them. A round's or a
division's cancellation is one save in the same way, so each has its `_on` form too.

Each test writes, reads the write back on the same connection, and then rolls back: what it finds
afterwards on a fresh connection is what a failure later in the save would leave, which is nothing.

The functions are imported inside each test, so this file collects while they are unbuilt.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.models.session import SESSIONS_BY_FORMAT

SERVER_ID = 10439
SEASON_ID = 7
DIVISION_ID = 11
ROUND_ID = 21


async def _make_db(tmp_path, *, stage: str = "PLACEMENTS") -> str:
    """Season 3 (id 7) in *stage*, Pro with one round, and two placements not yet committed."""
    db_path = os.path.join(str(tmp_path), "on_forms.db")
    await run_migrations(db_path)
    status = "SETUP" if stage == "PLACEMENTS" else "ACTIVE"
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 3, '2026-11-01', ?, ?)",
            (SEASON_ID, status, stage),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Pro', 1, 801, 'SETUP')",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, '2026-12-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        for profile_id, user_id in ((1, "101"), (2, "102")):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, user_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, committed) VALUES (?, ?, ?, 0)",
                (profile_id, SEASON_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


async def _session_types(db, round_id: int = ROUND_ID) -> list[str]:
    cursor = await db.execute(
        "SELECT session_type FROM sessions WHERE round_id = ? ORDER BY id", (round_id,)
    )
    return [row["session_type"] for row in await cursor.fetchall()]


async def _committed(db) -> list[int]:
    cursor = await db.execute(
        "SELECT committed FROM driver_season_assignments WHERE season_id = ? "
        "ORDER BY driver_profile_id",
        (SEASON_ID,),
    )
    return [row["committed"] for row in await cursor.fetchall()]


async def _season(db) -> tuple[str, str]:
    cursor = await db.execute("SELECT status, stage FROM seasons WHERE id = ?", (SEASON_ID,))
    row = await cursor.fetchone()
    return row["status"], row["stage"]


async def test_sessions_are_replaced_not_added_on_the_save_handed(tmp_path):
    """Round 1 already holds a sprint's sessions, left by an earlier save. Making its sessions
    for a normal round, twice, on one connection leaves the normal round's set once; and nothing
    of it outlasts a rollback."""
    from leaguebot.core.services.season_service import create_sessions_for_round_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        for session_type in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]:
            await db.execute(
                "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                (ROUND_ID, session_type.value),
            )
        await db.commit()

    expected = [st.value for st in SESSIONS_BY_FORMAT[RoundFormat.NORMAL]]
    async with get_connection(db_path) as db:
        await create_sessions_for_round_on(db, ROUND_ID, RoundFormat.NORMAL)
        await create_sessions_for_round_on(db, ROUND_ID, RoundFormat.NORMAL)
        assert await _session_types(db) == expected
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _session_types(db) == [
            st.value for st in SESSIONS_BY_FORMAT[RoundFormat.SPRINT]
        ], "nothing is committed: the save's own commit is the queue's"


async def test_placements_are_committed_on_the_save_handed(tmp_path):
    """Lewis and Max are placed in Pro, neither committed. Committing the season's placements on
    a connection marks both committed and answers 2; nothing of it outlasts a rollback."""
    from leaguebot.core.services.season_service import commit_placements_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        assert await commit_placements_on(db, SEASON_ID) == 2
        assert await _committed(db) == [1, 1]
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _committed(db) == [0, 0]


@pytest.mark.parametrize(
    "stage,moves",
    [
        pytest.param("PLACEMENTS", True, id="in placements"),
        pytest.param("ONGOING", False, id="already ongoing"),
    ],
)
async def test_the_season_moves_to_ongoing_only_from_placements(tmp_path, stage, moves):
    """A season in Placements moves to Ongoing, its divisions active with it, and the call
    answers True; a season already Ongoing is left as it is and the call answers False. Nothing
    of it outlasts a rollback."""
    from leaguebot.core.services.season_service import transition_to_active_on

    db_path = await _make_db(tmp_path, stage=stage)
    async with get_connection(db_path) as db:
        before = await _season(db)
        assert await transition_to_active_on(db, SEASON_ID) is moves
        assert await _season(db) == ("ACTIVE", "ONGOING")
        cursor = await db.execute("SELECT status FROM divisions WHERE id = ?", (DIVISION_ID,))
        division_status = (await cursor.fetchone())["status"]
        assert division_status == ("ACTIVE" if moves else "SETUP")
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _season(db) == before


# The round and division cancels (#439, slice 4b) -------------------------------------------------

NOW = datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc)
ACTOR = {"actor_id": 77, "actor_name": "Admin", "now": NOW}


async def _ongoing_db(tmp_path, statuses, *, season_status: str = "ACTIVE") -> str:
    """Season 3 (id 7) ongoing, Pro (id 11) active alone, its rounds 1, 2, … (ids 21, 22, …) in
    *statuses*, in order. A season given as archived (`COMPLETED` or `CANCELLED`) carries that
    status as its stage too, as the schema requires of an archived season."""
    db_path = os.path.join(str(tmp_path), "cancel_on_forms.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (?, 3, '2026-11-01', ?, ?)",
            (SEASON_ID, season_status,
             "ONGOING" if season_status == "ACTIVE" else season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Pro', 1, 801, 'ACTIVE')",
            (DIVISION_ID, SEASON_ID),
        )
        for number, status in enumerate(statuses, start=1):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "status) VALUES (?, ?, ?, '2026-12-01T18:00:00+00:00', 'NORMAL', ?)",
                (ROUND_ID + number - 1, DIVISION_ID, number, status),
            )
        await db.commit()
    return db_path


async def _round_statuses(db) -> list[str]:
    cursor = await db.execute(
        "SELECT status FROM rounds WHERE division_id = ? ORDER BY round_number", (DIVISION_ID,)
    )
    return [row["status"] for row in await cursor.fetchall()]


async def _division_status(db) -> str:
    cursor = await db.execute("SELECT status FROM divisions WHERE id = ?", (DIVISION_ID,))
    return (await cursor.fetchone())["status"]


async def _round_audits(db) -> list[tuple[str, str]]:
    cursor = await db.execute(
        "SELECT old_value, new_value FROM audit_entries WHERE change_type = 'round.status' "
        "ORDER BY id"
    )
    return [(row["old_value"], row["new_value"]) for row in await cursor.fetchall()]


async def test_a_round_is_cancelled_on_the_save_handed_and_gives_its_old_status(tmp_path):
    """Pro's round 1 is final, round 2 waits for its results (no submission open) and round 3 is
    not run. Cancelling round 2 on a connection answers the status it was cancelled from,
    AWAITING_RESULTS; it reads CANCELLED there, with one round.status audit from AWAITING_RESULTS
    to CANCELLED, and Pro stays active, round 3 being still to run. Nothing of it outlasts a
    rollback."""
    from leaguebot.core.services.season_service import cancel_round_on

    db_path = await _ongoing_db(tmp_path, ("FINAL", "AWAITING_RESULTS", "NOT_RUN"))
    async with get_connection(db_path) as db:
        assert await cancel_round_on(db, ROUND_ID + 1, **ACTOR) == "AWAITING_RESULTS"
        assert await _round_statuses(db) == ["FINAL", "CANCELLED", "NOT_RUN"]
        assert await _round_audits(db) == [("AWAITING_RESULTS", "CANCELLED")]
        assert await _division_status(db) == "ACTIVE"
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _round_statuses(db) == ["FINAL", "AWAITING_RESULTS", "NOT_RUN"]
        assert await _round_audits(db) == []


@pytest.mark.parametrize(
    "status,season_status",
    [
        pytest.param("CANCELLED", "ACTIVE", id="already cancelled"),
        pytest.param("AWAITING_REPORT_VERDICTS", "ACTIVE", id="results entered"),
        pytest.param("FINAL", "ACTIVE", id="final"),
        pytest.param("NOT_RUN", "COMPLETED", id="archived season"),
    ],
)
async def test_a_round_no_longer_cancellable_is_left_and_gives_none(
    tmp_path, status, season_status
):
    """Pro's round 1 stands in a state it may not be cancelled from: already cancelled, its
    results entered, final, or not run in a season since archived. Cancelling it on a connection
    answers None and writes nothing: the round keeps its status and no audit is written."""
    from leaguebot.core.services.season_service import cancel_round_on

    db_path = await _ongoing_db(tmp_path, (status, "NOT_RUN"), season_status=season_status)
    async with get_connection(db_path) as db:
        assert await cancel_round_on(db, ROUND_ID, **ACTOR) is None
        assert await _round_statuses(db) == [status, "NOT_RUN"]
        assert await _round_audits(db) == []


async def test_cancelling_the_last_round_finishes_the_division_and_moves_the_season_on_in_the_same_save(
    tmp_path,
):
    """Pro, the season's only division, has round 1 final and round 2 not run. Cancelling round 2
    on a connection finishes Pro and moves the season to Pending completion on that same
    connection; a rollback leaves Pro active and the season ongoing."""
    from leaguebot.core.services.season_service import cancel_round_on

    db_path = await _ongoing_db(tmp_path, ("FINAL", "NOT_RUN"))
    async with get_connection(db_path) as db:
        assert await cancel_round_on(db, ROUND_ID + 1, **ACTOR) == "NOT_RUN"
        assert await _division_status(db) == "FINISHED"
        assert await _season(db) == ("ACTIVE", "PENDING_COMPLETION")
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _division_status(db) == "ACTIVE"
        assert await _season(db) == ("ACTIVE", "ONGOING")


async def test_a_division_is_cancelled_on_the_save_handed(tmp_path):
    """Pro's round 1 is final, round 2 waits for its results and round 3 is not run. Cancelling Pro
    on a connection answers the ids of rounds 2 and 3, the ones it called off; Pro and those two
    read CANCELLED there, round 1 stays final, and each round called off is audited from the
    status it was cancelled from. Nothing of it outlasts a rollback."""
    from leaguebot.core.services.season_service import cancel_division_on

    db_path = await _ongoing_db(tmp_path, ("FINAL", "AWAITING_RESULTS", "NOT_RUN"))
    async with get_connection(db_path) as db:
        assert await cancel_division_on(db, DIVISION_ID, **ACTOR) == [ROUND_ID + 1, ROUND_ID + 2]
        assert await _division_status(db) == "CANCELLED"
        assert await _round_statuses(db) == ["FINAL", "CANCELLED", "CANCELLED"]
        assert await _round_audits(db) == [
            ("AWAITING_RESULTS", "CANCELLED"), ("NOT_RUN", "CANCELLED"),
        ]
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _division_status(db) == "ACTIVE"
        assert await _round_statuses(db) == ["FINAL", "AWAITING_RESULTS", "NOT_RUN"]
        assert await _round_audits(db) == []


async def test_cancelling_a_division_on_the_save_handed_leaves_its_season_s_stage_alone(tmp_path):
    """Pro, the season's last running division, has round 1 final and round 2 not run. Cancelling
    Pro with the `_on` form alone leaves the season ongoing: moving it on is the division
    change's save's, and `/season cancel`'s cascade, which shares the form, must never leave the
    season it cancels at Pending completion."""
    from leaguebot.core.services.season_service import cancel_division_on

    db_path = await _ongoing_db(tmp_path, ("FINAL", "NOT_RUN"))
    async with get_connection(db_path) as db:
        await cancel_division_on(db, DIVISION_ID, **ACTOR)
        assert await _division_status(db) == "CANCELLED"
        assert await _season(db) == ("ACTIVE", "ONGOING")


async def _round_numbers(db) -> dict[int, int]:
    cursor = await db.execute(
        "SELECT id, round_number FROM rounds WHERE division_id = ?", (DIVISION_ID,)
    )
    return {row["id"]: row["round_number"] for row in await cursor.fetchall()}


async def test_renumbering_on_the_connection_handed_commits_nothing(tmp_path):
    """Pro's rounds 1, 2 and 3 (ids 21, 22, 23) are not run, and round 1 has been moved after
    round 3. Renumbering Pro on a connection numbers them by date: round 2 becomes 1, round 3
    becomes 2 and the moved round 3. Nothing of it outlasts a rollback."""
    from leaguebot.core.services.season_service import renumber_rounds_on

    db_path = await _ongoing_db(tmp_path, ("NOT_RUN", "NOT_RUN", "NOT_RUN"))
    async with get_connection(db_path) as db:
        for round_id, moment in (
            (ROUND_ID, "2026-12-20T18:00:00+00:00"),
            (ROUND_ID + 1, "2026-12-06T18:00:00+00:00"),
            (ROUND_ID + 2, "2026-12-13T18:00:00+00:00"),
        ):
            await db.execute(
                "UPDATE rounds SET scheduled_at = ? WHERE id = ?", (moment, round_id)
            )
        await db.commit()

    async with get_connection(db_path) as db:
        await renumber_rounds_on(db, DIVISION_ID)
        assert await _round_numbers(db) == {ROUND_ID: 3, ROUND_ID + 1: 1, ROUND_ID + 2: 2}
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _round_numbers(db) == {ROUND_ID: 1, ROUND_ID + 1: 2, ROUND_ID + 2: 3}


# ---------------------------------------------------------------------------
# A season's end (#439, slice 5)
#
# A season's completion, cancellation and abort are changes on the queue: each writes its record
# in one save, so every write it makes has a form taking the save's connection and committing
# nothing. As above, each test reads its writes back on the connection, then rolls it back and
# finds nothing written.
# ---------------------------------------------------------------------------

_XFAIL = "#439: the season's end has no form writing on the save it is handed"

ACTOR_ID = 999
ACTOR_NAME = "Race Director"

# The moment the season's end is recorded at, pinned.
END_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


async def _end_seed(db_path, *, season_status="ACTIVE", divisions=(("Div A", "ACTIVE"),)):
    """One season of *season_status* with *divisions*, each (name, status) holding two rounds
    not yet run. Returns (season_id, {name: (division_id, [round_ids])})."""
    await run_migrations(db_path)
    built: dict[str, tuple[int, list[int]]] = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        # A season set up has no number yet: it takes one when its placements are confirmed.
        number = 0 if season_status == "SETUP" else 1
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) VALUES ('2026-01-01', ?, ?)",
            (season_status, number),
        )
        season_id = cur.lastrowid
        for tier, (name, status) in enumerate(divisions, start=1):
            cur = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, "
                "status, tier) VALUES (?, ?, ?, ?, ?, ?)",
                (season_id, name, 10 + tier, 20 + tier, status, tier),
            )
            division_id = cur.lastrowid
            round_ids = []
            for i in (1, 2):
                cur = await db.execute(
                    "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, "
                    "format) VALUES (?, ?, 'Bahrain', ?, 'NORMAL')",
                    (division_id, i, f"2026-0{i + 3}-01T12:00:00"),
                )
                round_ids.append(cur.lastrowid)
            built[name] = (division_id, round_ids)
        await db.commit()
    return season_id, built


async def _one(db, sql, *args):
    cur = await db.execute(sql, args)
    return await cur.fetchone()


async def _season_status(db, season_id):
    return (await _one(db, "SELECT status FROM seasons WHERE id = ?", season_id))["status"]


async def _statuses(db, table, ids):
    marks = ",".join("?" * len(ids))
    cur = await db.execute(f"SELECT id, status FROM {table} WHERE id IN ({marks})", ids)
    return {row["id"]: row["status"] for row in await cur.fetchall()}


# ---------------------------------------------------------------------------
# The completion
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_a_season_is_completed_on_the_save_handed(tmp_path) -> None:
    from leaguebot.core.services.season_service import complete_season_on

    db_path = str(tmp_path / "bot.db")
    season_id, _ = await _end_seed(db_path)

    async with get_connection(db_path) as db:
        assert await complete_season_on(db, season_id) is True
        assert await _season_status(db, season_id) == "COMPLETED"
        await db.rollback()
        assert await _season_status(db, season_id) == "ACTIVE"


@pytest.mark.xfail(strict=True, reason=_XFAIL)
@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_a_season_no_longer_active_is_left_and_gives_false(tmp_path, status) -> None:
    from leaguebot.core.services.season_service import complete_season_on

    db_path = str(tmp_path / "bot.db")
    season_id, _ = await _end_seed(db_path, season_status=status)

    async with get_connection(db_path) as db:
        assert await complete_season_on(db, season_id) is False
        assert await _season_status(db, season_id) == status


ROUNDS_OUTSTANDING = (
    "❌ Cannot complete season — the following rounds are not yet finalised:\n"
    "• Div A — Round 2 (Bahrain)"
)
DIVISION_IN_SETUP = (
    "❌ Cannot complete season — no round is outstanding, but these divisions have not "
    "finished: **Div B**. Cancel a division that will never run, or report this."
)


@pytest.mark.xfail(strict=True, reason=_XFAIL)
@pytest.mark.parametrize(
    "case, expected",
    [
        ("rounds outstanding", ROUNDS_OUTSTANDING),
        ("a division in setup", DIVISION_IN_SETUP),
        ("none", None),
    ],
    ids=["rounds outstanding", "a division in setup", "none"],
)
async def test_the_completion_refusal_is_read_on_the_save_handed(tmp_path, case, expected) -> None:
    """Div A runs two rounds and Div B, set up and never run, holds none of its own. Each case
    makes its decisive writes on the save, uncommitted, so only a read on that save sees them.
    An ACTIVE division with nothing outstanding refuses nothing: the completion finishes it."""
    from leaguebot.core.services.season_service import completion_refusal_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(
        db_path, divisions=(("Div A", "ACTIVE"), ("Div B", "SETUP"))
    )
    (_, (r1, r2)) = built["Div A"]
    (div_b, b_rounds) = built["Div B"]

    async with get_connection(db_path) as db:
        await db.execute(
            f"DELETE FROM rounds WHERE id IN ({','.join('?' * len(b_rounds))})", b_rounds
        )
        await db.execute("UPDATE rounds SET status = 'FINAL' WHERE id = ?", (r1,))
        if case != "rounds outstanding":
            await db.execute("UPDATE rounds SET status = 'FINAL' WHERE id = ?", (r2,))
        if case == "none":
            await db.execute("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", (div_b,))

        assert await completion_refusal_on(db, season_id) == expected
        await db.rollback()


# ---------------------------------------------------------------------------
# The cancellation
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_a_season_is_marked_cancelled_on_the_save_handed_and_nothing_else(tmp_path) -> None:
    from leaguebot.core.services.season_service import cancel_season_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(db_path)
    division_id, round_ids = built["Div A"]

    async with get_connection(db_path) as db:
        assert await cancel_season_on(db, season_id) is True
        assert await _season_status(db, season_id) == "CANCELLED"
        assert await _statuses(db, "divisions", [division_id]) == {division_id: "ACTIVE"}
        assert await _statuses(db, "rounds", round_ids) == {r: "NOT_RUN" for r in round_ids}
        await db.rollback()
        assert await _season_status(db, season_id) == "ACTIVE"


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_a_season_s_divisions_are_cancelled_on_the_save_handed_leaving_its_row(
    tmp_path,
) -> None:
    """Pro runs two rounds, the first raced and final; Am was cancelled before. Only Pro is
    cancelled, with its round not yet run; the season's row is the cancellation's last save's."""
    from leaguebot.core.services.season_service import cancel_season_divisions_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(
        db_path, divisions=(("Pro", "ACTIVE"), ("Am", "CANCELLED"))
    )
    pro, (raced, unraced) = built["Pro"]
    am, am_rounds = built["Am"]

    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'FINAL' WHERE id = ?", (raced,))
        await db.commit()

        called_off = await cancel_season_divisions_on(
            db, season_id, actor_id=ACTOR_ID, actor_name=ACTOR_NAME, now=END_NOW
        )

        assert called_off == {pro: [unraced]}
        assert await _statuses(db, "divisions", [pro, am]) == {pro: "CANCELLED", am: "CANCELLED"}
        assert await _statuses(db, "rounds", [raced, unraced]) == {
            raced: "FINAL",
            unraced: "CANCELLED",
        }
        assert await _season_status(db, season_id) == "ACTIVE"
        await db.rollback()
        assert await _statuses(db, "divisions", [pro]) == {pro: "ACTIVE"}
        assert await _statuses(db, "rounds", [unraced]) == {unraced: "NOT_RUN"}


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_raced_rounds_are_closed_on_the_save_handed(tmp_path) -> None:
    """Round 1 is awaiting its report verdicts, round 2 not yet run. Round 1 is made final and
    audited at the moment handed; round 2 is left to the divisions' cancellation."""
    from leaguebot.core.services.season_service import close_raced_rounds_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(db_path)
    division_id, (awaiting, unraced) = built["Div A"]
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?", (awaiting,)
        )
        await db.commit()

    async with get_connection(db_path) as db:
        closed = await close_raced_rounds_on(
            db, season_id, actor_id=ACTOR_ID, actor_name=ACTOR_NAME, now=END_NOW
        )

        assert closed == [awaiting]
        assert await _statuses(db, "rounds", [awaiting, unraced]) == {
            awaiting: "FINAL",
            unraced: "NOT_RUN",
        }
        audit = await _one(
            db,
            "SELECT actor_id, division_id, old_value, new_value, timestamp FROM audit_entries "
            "WHERE change_type = 'round.status'",
        )
        assert dict(audit) == {
            "actor_id": ACTOR_ID,
            "division_id": division_id,
            "old_value": "AWAITING_REPORT_VERDICTS",
            "new_value": "FINAL",
            "timestamp": END_NOW.isoformat(),
        }
        await db.rollback()
        assert await _statuses(db, "rounds", [awaiting]) == {awaiting: "AWAITING_REPORT_VERDICTS"}


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_uncommitted_placements_are_discarded_on_the_save_handed(tmp_path) -> None:
    """Driver 1 holds a committed seat, driver 2 one placed but not yet committed. Driver 2's
    placement goes and their seat is freed; driver 1's stands."""
    from leaguebot.core.services.season_service import discard_uncommitted_placements_on

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(db_path)
    division_id, _ = built["Div A"]
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name) VALUES (?, 'ALP', 'Alpha')",
            (division_id,),
        )
        team = cur.lastrowid
        for profile, committed in ((1, 1), (2, 0)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(100 + profile)),
            )
            cur = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (team, profile, profile),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile, season_id, division_id, cur.lastrowid, committed),
            )
        await db.commit()

    async def _held(db):
        cur = await db.execute(
            "SELECT driver_profile_id FROM team_seats WHERE driver_profile_id IS NOT NULL "
            "ORDER BY driver_profile_id"
        )
        return [row[0] for row in await cur.fetchall()]

    async def _placed(db):
        cur = await db.execute(
            "SELECT driver_profile_id FROM driver_season_assignments ORDER BY driver_profile_id"
        )
        return [row[0] for row in await cur.fetchall()]

    async with get_connection(db_path) as db:
        assert await discard_uncommitted_placements_on(db, season_id) == 1
        assert await _held(db) == [1]
        assert await _placed(db) == [1]
        await db.rollback()
        assert await _held(db) == [1, 2]
        assert await _placed(db) == [1, 2]


# ---------------------------------------------------------------------------
# The abort
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_a_season_is_deleted_on_the_save_handed_and_nothing_committed(tmp_path) -> None:
    """A season being set up, its division and its rounds, deleted on the save; rolled back,
    every row of it is still there."""
    from leaguebot.core.services.season_service import SeasonService

    db_path = str(tmp_path / "bot.db")
    season_id, built = await _end_seed(
        db_path, season_status="SETUP", divisions=(("Pro", "SETUP"),)
    )
    division_id, round_ids = built["Pro"]

    async def _left(db):
        return (
            await _one(db, "SELECT COUNT(*) FROM seasons WHERE id = ?", season_id),
            await _one(db, "SELECT COUNT(*) FROM divisions WHERE id = ?", division_id),
            await _one(db, "SELECT COUNT(*) FROM rounds WHERE division_id = ?", division_id),
        )

    async with get_connection(db_path) as db:
        await SeasonService.delete_season(db, season_id)
        assert [row[0] for row in await _left(db)] == [0, 0, 0]
        await db.rollback()
        assert [row[0] for row in await _left(db)] == [1, 1, len(round_ids)]
