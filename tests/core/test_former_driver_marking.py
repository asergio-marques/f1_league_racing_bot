"""When a driver becomes — and stops being — a former driver.

Issue #216. Nothing tested *when* the flag was set, and the code and the core specification
disagreed three ways: the flag went up the moment a classification was pasted in, it went up for
a driver whose only entry was ``DNS``, and nothing but an abandoned amendment ever took it down.

The rule is the core specification's **Leaving the league**: a driver has raced a round only
once that round is FINAL and only by its final results, a did-not-start entry does not count,
and an amendment leaving a driver no longer having raced clears the flag unless another final
round marks them.

**Why the flag matters.** It is read in exactly one behavioural place —
``run_driver_pass``, which at season end deletes every non-test profile sitting at NOT_SIGNED_UP
*without* it. A flag wrongly set keeps a profile that should have been deleted; a flag wrongly
clear deletes one a result points at. The tests here are about which of the two a league gets.

``recompute_former_drivers_for_round`` is the whole mechanism. These tests drive it directly,
against hand-built rounds, so that each rule is pinned by itself; the call sites that invoke it
are covered where they live.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.result_submission_service import (
    recompute_former_drivers_for_round,
)
from tests.support.teams import seed_team_instances

SEASON_ID = 1
DIVISION_ID = 11
OTHER_DIVISION_ID = 12


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "former_driver") -> str:
    """A season with two divisions and three drivers, nobody yet a former driver."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 6, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for division_id, name_, tier in (
            (DIVISION_ID, "Pro", 1),
            (OTHER_DIVISION_ID, "Am", 2),
        ):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name_, tier),
            )
            await seed_team_instances(db, division_id, 3001)
        for profile_id, driver in ((31, 101), (32, 102), (33, 103)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "former_driver) VALUES (?, ?, 'ASSIGNED', 0)",
                (profile_id, str(driver)),
            )
        await db.commit()
    return db_path


async def _add_round(
    db_path: str,
    round_id: int,
    *,
    status: str,
    division_id: int = DIVISION_ID,
    round_number: int = 3,
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (round_id, division_id, round_number, status),
        )
        await db.commit()


async def _add_race(
    db_path: str,
    round_id: int,
    entries: list[tuple[int, int, str]],
    *,
    division_id: int = DIVISION_ID,
    session_type: str = "FEATURE_RACE",
    session_status: str = "ACTIVE",
) -> None:
    """Give the round a race session. ``entries`` is (profile_id, driver_user_id, outcome)."""
    async with get_connection(db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, ?, ?, 'Standard')",
            (round_id, division_id, session_type, session_status),
        )
        for position, (profile_id, driver, outcome) in enumerate(entries, start=1):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, driver_profile_id) "
                "VALUES (?, ?, 3001, ?, ?, ?)",
                (session.lastrowid, driver, position, outcome, profile_id),
            )
        await db.commit()


async def _add_qualifying(
    db_path: str,
    round_id: int,
    entries: list[tuple[int, int, str]],
    *,
    division_id: int = DIVISION_ID,
) -> None:
    async with get_connection(db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', 'Standard')",
            (round_id, division_id),
        )
        for position, (profile_id, driver, outcome) in enumerate(entries, start=1):
            await db.execute(
                "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, driver_profile_id) "
                "VALUES (?, ?, 3001, ?, ?, ?)",
                (session.lastrowid, driver, position, outcome, profile_id),
            )
        await db.commit()


async def _recompute(db_path: str, round_id: int, *, also_consider=None) -> None:
    async with get_connection(db_path) as db:
        await recompute_former_drivers_for_round(db, round_id, also_consider=also_consider)
        await db.commit()


async def _former(db_path: str, profile_id: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT former_driver FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        return (await cursor.fetchone())["former_driver"]


async def _set_former(db_path: str, profile_id: int, value: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET former_driver = ? WHERE id = ?", (value, profile_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# A round that is not yet final marks nobody
# ---------------------------------------------------------------------------


async def test_a_provisional_round_marks_nobody(tmp_path):
    """Results pasted in but not yet settled leave the flag alone.

    The heart of #216. The round is still awaiting its verdicts, so the classification is
    provisional: a driver in it may yet be taken out by a resubmission or an appeal, and the
    spec marks nobody until the round is final.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="AWAITING_RESULTS")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "CLASSIFIED")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 0
    assert await _former(db_path, 32) == 0


async def test_a_round_awaiting_appeal_verdicts_marks_nobody(tmp_path):
    """The last provisional state counts as provisional too."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="AWAITING_APPEAL_VERDICTS")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 0


# ---------------------------------------------------------------------------
# A final round marks the drivers who raced it
# ---------------------------------------------------------------------------


async def test_a_final_round_marks_the_drivers_who_raced(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "CLASSIFIED")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 1


async def test_a_driver_of_another_round_is_left_alone(tmp_path):
    """The recompute is scoped to the round it is given.

    Driver 33 raced nothing here and is named nowhere in round 21. Their flag is not the
    business of this call, whichever way it happens to stand.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])
    await _set_former(db_path, 33, 1)

    await _recompute(db_path, 21)

    assert await _former(db_path, 33) == 1, "a driver outside the round was disturbed"


async def test_marking_is_idempotent(tmp_path):
    """Running it twice changes nothing the first run did not."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])

    await _recompute(db_path, 21)
    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1


async def test_a_final_qualifying_result_marks_a_driver(tmp_path):
    """Both result tables count — a round need not have had its race for the flag to go up."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_qualifying(db_path, 21, [(31, 101, "CLASSIFIED")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1


# ---------------------------------------------------------------------------
# Did not start
# ---------------------------------------------------------------------------


async def test_a_driver_who_only_did_not_start_is_not_marked(tmp_path):
    """A DNS entry is not having raced, however final the round.

    The spec is explicit: "A driver whose only entries in a round are did-not-start entries has
    not raced that round." They were on the entry list and never took the start.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "DNS")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0, "a did-not-start entry marked a driver"


async def test_a_driver_who_retired_or_was_disqualified_is_marked(tmp_path):
    """Only DNS excludes (decided 2026-09-21).

    A driver who retired took the start, and one disqualified was racing to be thrown out of
    it. Both raced; the spec excludes the did-not-start entry alone. Were this to read "only a
    classified finish counts", a driver who retired from every round of a season would be
    deleted at its end with their results still on the board.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "DNF"), (32, 102, "DSQ")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1, "a retirement did not count as having raced"
    assert await _former(db_path, 32) == 1, "a disqualification did not count as having raced"


async def test_a_driver_who_did_not_start_one_session_but_raced_another_is_marked(tmp_path):
    """"Only entries" means all of them. Qualifying DNS, then raced — they raced the round."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_qualifying(db_path, 21, [(31, 101, "DNS")])
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------


async def test_a_driver_struck_from_the_round_is_cleared(tmp_path):
    """An amendment that takes a driver out takes their flag with it.

    Driver 32 was in the round and is no longer. Nothing else marks them, so they stop being a
    former driver and their profile becomes deletable again — which is the whole point of the
    flag being two-way.

    The caller names them through ``also_consider``, because by the time the recompute runs the
    round's results no longer mention them at all.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "CLASSIFIED")])
    await _recompute(db_path, 21)
    assert await _former(db_path, 32) == 1

    # The amendment: the session is rewritten without driver 32.
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM race_session_results WHERE driver_profile_id = 32",
        )
        await db.commit()

    await _recompute(db_path, 21, also_consider=[31, 32])

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0


async def test_a_driver_struck_out_is_missed_without_being_named(tmp_path):
    """Why ``also_consider`` exists, stated as an outcome.

    Without it the recompute sees only the round as it now stands, and a driver removed
    outright is in no result to be found by. This is the shape of the bug #216 reported as
    "never cleared", and it is the amendment path's job not to walk into it.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "CLASSIFIED")])
    await _recompute(db_path, 21)

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM race_session_results WHERE driver_profile_id = 32")
        await db.commit()

    await _recompute(db_path, 21)  # nobody named

    assert await _former(db_path, 32) == 1, (
        "a driver in no result was somehow reconsidered — has the scoping changed?"
    )


async def test_an_amendment_leaving_a_driver_only_dns_clears_the_flag(tmp_path):
    """Corrected to a did-not-start, and so no longer having raced."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "CLASSIFIED")])
    await _recompute(db_path, 21)
    assert await _former(db_path, 32) == 1

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE race_session_results SET outcome = 'DNS' WHERE driver_profile_id = 32",
        )
        await db.commit()

    await _recompute(db_path, 21)

    assert await _former(db_path, 32) == 0


async def test_a_driver_marked_by_another_final_round_keeps_the_flag(tmp_path):
    """"Unless another final round marks them" — the spec's own qualifier.

    Struck from round 21, but round 22 is final and they raced it. Their results still stand
    somewhere, so the profile is still owed.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL", round_number=3)
    await _add_round(db_path, 22, status="FINAL", round_number=4)
    await _add_race(db_path, 21, [(32, 102, "CLASSIFIED")])
    await _add_race(db_path, 22, [(32, 102, "CLASSIFIED")])
    await _recompute(db_path, 21)
    assert await _former(db_path, 32) == 1

    async with get_connection(db_path) as db:
        session = await db.execute(
            "SELECT id FROM session_results WHERE round_id = 21"
        )
        await db.execute(
            "DELETE FROM race_session_results WHERE session_result_id = ?",
            ((await session.fetchone())["id"],),
        )
        await db.commit()

    await _recompute(db_path, 21, also_consider=[32])

    assert await _former(db_path, 32) == 1, "a driver racing elsewhere lost their flag"


async def test_a_provisional_round_elsewhere_does_not_keep_the_flag(tmp_path):
    """The other round has to be final too.

    Round 22 has the driver's results but is still awaiting verdicts, so it marks nobody — and
    cannot keep alive a flag round 21 no longer justifies.
    """
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL", round_number=3)
    await _add_round(
        db_path, 22, status="AWAITING_RESULTS", division_id=OTHER_DIVISION_ID, round_number=1
    )
    await _add_race(db_path, 21, [(32, 102, "DNS")])
    await _add_race(db_path, 22, [(32, 102, "CLASSIFIED")], division_id=OTHER_DIVISION_ID)
    await _set_former(db_path, 32, 1)

    await _recompute(db_path, 21)

    assert await _former(db_path, 32) == 0


async def test_a_superseded_session_does_not_mark(tmp_path):
    """Only the live classification counts. A session no longer ACTIVE is not the round's
    results, and a driver it alone names has not raced."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])
    await _add_race(
        db_path, 21, [(32, 102, "CLASSIFIED")],
        session_type="SPRINT_RACE", session_status="SUPERSEDED",
    )

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0


async def test_a_round_with_no_results_changes_nothing(tmp_path):
    """Nothing to recompute from, and nobody disturbed."""
    db_path = await _make_db(tmp_path)
    await _add_round(db_path, 21, status="FINAL")
    await _set_former(db_path, 31, 1)

    await _recompute(db_path, 21)

    assert await _former(db_path, 31) == 1


# ---------------------------------------------------------------------------
# Closing a season's rounds when the results module is switched off
# ---------------------------------------------------------------------------


async def test_closing_rounds_with_the_module_off_marks_who_raced_them(tmp_path):
    """`end_rounds_awaiting_results` is the other way a round becomes final (#167, #216).

    A round waiting on a results command will never get one once the module is off, so it is
    closed as FINAL — it was raced, and only its scoring is abandoned. That makes its results
    final, so its drivers become former drivers here; the results finaliser is itself a results
    command and will never run for them.

    In the ordinary disable the purge has already erased these results and this finds nothing.
    It earns its place where the purge failed — `_apply_results_disable` closes the rounds
    regardless — which is the case seeded here.
    """
    from leaguebot.core.services.season_service import SeasonService

    db_path = await _make_db(tmp_path, name="module_off")
    await _add_round(db_path, 21, status="AWAITING_APPEAL_VERDICTS")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "DNS")])
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (77, 900, 100, 101)"
        )
        await db.commit()

    await SeasonService(db_path).end_rounds_awaiting_results(5, "Admin")

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0


# ---------------------------------------------------------------------------
# Cancelling a season closes the rounds it may not cancel
# ---------------------------------------------------------------------------


async def test_cancelling_a_season_closes_a_round_whose_verdicts_are_open(tmp_path):
    """The rounds a cancellation may not call off are made final instead (#216).

    `ROUND_CANCELLABLE` stops at *awaiting results*: a round further along has its results
    entered, and "a cancellation shall never discard a result". So the cascade leaves it where
    it is — and the verdict commands it waits on are refused once the season is cancelled, so
    nothing else would ever move it.

    That is only a tidiness problem until the flag is set at the FINAL transition. Then a
    driver whose only round is one of these is flagless when the driver pass runs, and the pass
    deletes them — taking their results and their history with them. Closing the rounds first
    is what keeps them.
    """
    from leaguebot.core.services.season_service import SeasonService

    db_path = await _make_db(tmp_path, name="cancel_verdicts")
    await _add_round(db_path, 21, status="AWAITING_REPORT_VERDICTS", round_number=3)
    await _add_round(db_path, 22, status="AWAITING_APPEAL_VERDICTS", round_number=4)
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED"), (32, 102, "DNS")])
    await _add_race(db_path, 22, [(33, 103, "CLASSIFIED")])

    closed = await SeasonService(db_path).close_raced_rounds_for_cancellation(
        SEASON_ID, 5, "Admin"
    )

    assert closed == [21, 22]
    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 33) == 1
    assert await _former(db_path, 32) == 0, "a did-not-start entry marked a driver"


async def test_a_driver_of_a_closed_round_survives_the_driver_pass(tmp_path):
    """The harm the close prevents, stated end to end (#216).

    The driver pass deletes a flagless profile at Not Signed Up, and
    ``delete_driver_profiles`` NULLs the ``driver_profile_id`` on their result rows and
    destroys their history entries. For a driver whose only round was left at a verdict state
    by a cancellation, that is a result discarded — which "a cancellation shall never discard a
    result" forbids. Closing the round first is what keeps them.
    """
    from leaguebot.core.services.season_lifecycle_service import run_driver_pass
    from leaguebot.core.services.season_service import SeasonService

    db_path = await _make_db(tmp_path, name="cancel_pass")
    await _add_round(db_path, 21, status="AWAITING_REPORT_VERDICTS")
    await _add_race(db_path, 21, [(31, 101, "CLASSIFIED")])
    # Sacked mid-season: back at Not Signed Up, and so in the pass's sights.
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = 31"
        )
        await db.commit()

    await SeasonService(db_path).close_raced_rounds_for_cancellation(
        SEASON_ID, 5, "Admin"
    )
    await run_driver_pass(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM driver_profiles WHERE id = 31")
        assert (await cursor.fetchone())["n"] == 1, "a driver who raced was deleted"
        cursor = await db.execute(
            "SELECT driver_profile_id FROM race_session_results WHERE driver_user_id = 101"
        )
        assert (await cursor.fetchone())["driver_profile_id"] == 31, "a result was orphaned"


async def test_cancelling_a_season_leaves_an_unraced_round_to_the_cascade(tmp_path):
    """A round with no results entered is the cascade's to cancel, not this function's.

    Closing it as FINAL would say it was raced when it was not, and would tell the attendance
    module to expect a turnout for a round that never happened.
    """
    from leaguebot.core.services.season_service import SeasonService

    db_path = await _make_db(tmp_path, name="cancel_unraced")
    await _add_round(db_path, 21, status="AWAITING_RESULTS", round_number=3)
    await _add_round(db_path, 22, status="NOT_RUN", round_number=4)

    closed = await SeasonService(db_path).close_raced_rounds_for_cancellation(
        SEASON_ID, 5, "Admin"
    )

    assert closed == []
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, status FROM rounds ORDER BY id")
        assert [(r["id"], r["status"]) for r in await cursor.fetchall()] == [
            (21, "AWAITING_RESULTS"),
            (22, "NOT_RUN"),
        ]


async def test_cancelling_a_season_leaves_another_seasons_rounds_alone(tmp_path):
    """Scoped by season, so cancelling one never closes a round of another."""
    from leaguebot.core.services.season_service import SeasonService

    db_path = await _make_db(tmp_path, name="cancel_other_season")
    await _add_round(db_path, 21, status="AWAITING_REPORT_VERDICTS", round_number=3)
    async with get_connection(db_path) as db:
        # COMPLETED, not a second ACTIVE — a server holds at most one live season, and the
        # partial unique index enforces it. An archived season is the realistic neighbour
        # anyway: it is last season's rounds this must not reach back into.
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (2, 7, '2025-01-01', 'COMPLETED')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (99, 2, 'Pro', 1, 556)"
        )
        await db.commit()
    await _add_round(
        db_path, 31, status="AWAITING_REPORT_VERDICTS", division_id=99, round_number=1
    )

    closed = await SeasonService(db_path).close_raced_rounds_for_cancellation(
        SEASON_ID, 5, "Admin"
    )

    assert closed == [21]
