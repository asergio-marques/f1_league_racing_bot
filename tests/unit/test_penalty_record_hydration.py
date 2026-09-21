"""Reading a round's decided verdicts back into staged form (#345).

The amendment replay shows a league manager what was decided for a round and lets them change
it. Nothing in the bot could do that: `StagedPenalty` was only ever built from a steward's typed
input, and the penalty review's own restart recovery deliberately reopens with an **empty**
staged list rather than re-hydrating one, warning the manager not to re-add what it cannot show.

Two things make the read harder than a select.

**The driver and the session are not stored on the record.** `penalty_records` and
`appeal_records` hold a reference to one driver's result row and nothing else identifying, so
both have to be recovered by join — the driver from the per-format table, the session from
`session_results` above it.

**A penalty row is written on both phases.** `apply_penalties` inserts into `penalty_records`
whichever phase it runs in, so an appeal leaves a row in *both* tables. Taking the appeals from
`appeal_records` and every `penalty_records` row as a report would therefore show the appeal
twice — once as itself and once as a report that was never made. The pairing is one-for-one, so
two identical penalties are not both swallowed by a single appeal.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.penalty_service import load_staged_from_records  # noqa: E402

ROUND_ID = 41
DIVISION_ID = 21
SEASON_ID = 11


async def _seed(tmp_path, name: str = "hydrate") -> tuple[str, dict]:
    """A round with a feature race and a feature qualifying, two drivers in each."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    ids: dict = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 4, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 2, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        race = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        for position, driver in enumerate((101, 102), start=1):
            cursor = await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, ?)",
                (race.lastrowid, driver, position),
            )
            ids[f"race_{driver}"] = cursor.lastrowid
        quali = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (quali.lastrowid,),
        )
        ids["qual_101"] = cursor.lastrowid
        await db.commit()
    return db_path, ids


async def _penalty(db_path, result_id, *, column="race_result_id", penalty_type="TIME",
                   seconds=5, description="Contact", justification="At fault"):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"INSERT INTO penalty_records ({column}, penalty_type, time_seconds, description, "
            "justification, applied_by, applied_at) VALUES (?, ?, ?, ?, ?, '77', "
            "'2026-02-02T00:00:00+00:00')",
            (result_id, penalty_type, seconds, description, justification),
        )
        await db.commit()
        return cursor.lastrowid


async def _appeal(db_path, result_id, *, column="race_result_id", penalty_type="TIME",
                  seconds=5, description="Contact", justification="At fault"):
    """Defaults match `_penalty`'s, because a real pair shares them.

    `finalize_appeals_review` copies one `StagedPenalty` into both tables, so the description
    and justification of a genuine penalty/appeal pair are identical. A helper that gave them
    different text would be testing a state the bot cannot produce.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"INSERT INTO appeal_records ({column}, status, penalty_type, time_seconds, "
            "description, justification, submitted_by, submitted_at) "
            "VALUES (?, 'UPHELD', ?, ?, ?, ?, '78', '2026-02-03T00:00:00+00:00')",
            (result_id, penalty_type, seconds, description, justification),
        )
        await db.commit()
        return cursor.lastrowid


async def test_a_report_comes_back_with_every_field_it_was_given(tmp_path):
    """The staged entry is what a steward would have typed to produce the record."""
    db_path, ids = await _seed(tmp_path, "hydrate_report")
    await _penalty(
        db_path, ids["race_101"], seconds=10,
        description="Contact at turn four.", justification="Wholly at fault.",
    )

    reports, appeals, pardons = await load_staged_from_records(db_path, ROUND_ID)

    assert (appeals, pardons) == ([], [])
    assert len(reports) == 1
    staged = reports[0]
    assert staged.driver_user_id == 101
    assert staged.session_type is SessionType.FEATURE_RACE
    assert staged.penalty_type == "TIME"
    assert staged.penalty_seconds == 10
    assert staged.description == "Contact at turn four."
    assert staged.justification == "Wholly at fault."


async def test_a_qualifying_report_recovers_its_own_session(tmp_path):
    """The session is read through `session_results`, not guessed from the column used."""
    db_path, ids = await _seed(tmp_path, "hydrate_quali")
    await _penalty(db_path, ids["qual_101"], column="qual_result_id", penalty_type="DSQ",
                   seconds=None)

    reports, _, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert len(reports) == 1
    assert reports[0].session_type is SessionType.FEATURE_QUALIFYING
    assert reports[0].penalty_type == "DSQ"
    assert reports[0].penalty_seconds is None


async def test_an_appeal_is_read_as_an_appeal_and_not_also_as_a_report(tmp_path):
    """`apply_penalties` writes a `penalty_records` row on the appeal phase too.

    Counting that row as a report as well would show the manager a decision nobody made.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_appeal")
    await _penalty(db_path, ids["race_101"], seconds=5)
    await _appeal(db_path, ids["race_101"], seconds=5)

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert reports == []
    assert len(appeals) == 1
    assert appeals[0].driver_user_id == 101


async def test_a_report_and_an_appeal_on_one_driver_both_come_back(tmp_path):
    """A driver penalised in review and again on appeal carries two distinct sanctions.

    They differ in seconds here, so nothing pairs them — the report survives as a report.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_both")
    await _penalty(db_path, ids["race_101"], seconds=5)
    await _penalty(db_path, ids["race_101"], seconds=3)
    await _appeal(db_path, ids["race_101"], seconds=3)

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert [r.penalty_seconds for r in reports] == [5]
    assert [a.penalty_seconds for a in appeals] == [3]


async def test_two_identical_reports_are_not_both_claimed_by_one_appeal(tmp_path):
    """The pairing is one-for-one.

    Two drivers given the same sanction, one of whom appealed, must not lose the other's report
    to a match on shape alone — which is why the appeal accounts for one row and not for every
    row that looks like it.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_pairing")
    await _penalty(db_path, ids["race_101"], seconds=5)
    await _penalty(db_path, ids["race_101"], seconds=5)
    await _appeal(db_path, ids["race_101"], seconds=5)

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert len(reports) == 1
    assert len(appeals) == 1


async def test_a_pardon_comes_back_staged(tmp_path):
    """Pardons are amendable in the replay's report stage, so they hydrate with the rest."""
    db_path, _ = await _seed(tmp_path, "hydrate_pardon")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, former_driver) "
            "VALUES (31, '101', 'ASSIGNED', 0)"
        )
        cursor = await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (?, ?, 31, 'NO_RSVP')",
            (ROUND_ID, DIVISION_ID),
        )
        attendance_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (?, 'NO_RSVP', 'Away with work', 77, "
            "'2026-02-02T00:00:00+00:00')",
            (attendance_id,),
        )
        await db.commit()

    _, _, pardons = await load_staged_from_records(db_path, ROUND_ID)

    assert len(pardons) == 1
    assert pardons[0].driver_user_id == 101
    assert pardons[0].driver_profile_id == 31
    assert pardons[0].attendance_id == attendance_id
    assert pardons[0].pardon_type == "NO_RSVP"
    assert pardons[0].justification == "Away with work"
    assert pardons[0].grantor_id == 77
    # Kept whole, so an amendment writing it out again keeps when it was granted (#345).
    assert pardons[0].granted_at == "2026-02-02T00:00:00+00:00"


async def test_a_round_that_was_never_penalised_hydrates_to_nothing(tmp_path):
    """The ordinary case, so an empty result cannot be read as a failure to find anything."""
    db_path, _ = await _seed(tmp_path, "hydrate_empty")

    assert await load_staged_from_records(db_path, ROUND_ID) == ([], [], [])


async def test_another_rounds_verdicts_are_not_picked_up(tmp_path):
    """The scope is the round, reached through `session_results`."""
    db_path, ids = await _seed(tmp_path, "hydrate_scope")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (99, ?, 3, '2026-03-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (DIVISION_ID,),
        )
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (99, ?, 'FEATURE_RACE', 'ACTIVE')",
            (DIVISION_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (other.lastrowid,),
        )
        other_row = cursor.lastrowid
        await db.commit()
    await _penalty(db_path, other_row)
    await _penalty(db_path, ids["race_102"], seconds=7)

    reports, _, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert [(r.driver_user_id, r.penalty_seconds) for r in reports] == [(102, 7)]


async def test_the_pairing_holds_because_both_rows_come_from_one_staged_appeal(tmp_path):
    """Why matching on shape is sound rather than lucky.

    `finalize_appeals_review` runs `apply_penalties(..., _phase="APPEAL")`, which inserts a
    `penalty_records` row, and then writes the `appeal_records` row from the **same**
    `StagedPenalty` — the driver, the session, the type and the seconds are copied from one
    object into both tables in one pass. There is no route by which the pair can disagree, so
    an appeal always accounts for exactly the penalty row it created.

    Pinned because the pairing would otherwise look like a guess: a reader who assumed the two
    rows were written independently would be right to distrust it, and might "fix" it into
    something that double-counts.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_pair_source")
    # Exactly what that code path produces: one penalty row and one appeal row of one shape.
    await _penalty(db_path, ids["race_101"], penalty_type="TIME", seconds=8)
    await _appeal(db_path, ids["race_101"], penalty_type="TIME", seconds=8)

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert reports == []
    assert len(appeals) == 1
    assert appeals[0].penalty_seconds == 8


async def test_a_report_of_a_different_shape_is_never_claimed_by_an_appeal(tmp_path):
    """A genuine report survives beside an appeal, which is the failure mode that matters.

    Losing a report to an over-eager match would silently drop a steward's decision from the
    stage that is meant to show it back.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_pair_distinct")
    await _penalty(db_path, ids["race_101"], penalty_type="TIME", seconds=8)
    await _penalty(db_path, ids["race_102"], penalty_type="DSQ", seconds=None)
    await _appeal(db_path, ids["race_101"], penalty_type="TIME", seconds=8)

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert [(r.driver_user_id, r.penalty_type) for r in reports] == [(102, "DSQ")]
    assert [(a.driver_user_id, a.penalty_type) for a in appeals] == [(101, "TIME")]


async def test_a_report_and_an_appeal_of_the_same_size_for_different_incidents_both_survive(
    tmp_path,
):
    """Shape must include the text, or a genuine report is lost (#345, found in review).

    A driver given a 5 s report for one incident and, separately, a 5 s appeal for another in
    the same session produces two `penalty_records` rows and one `appeal_record`. Pairing on the
    driver, session, type and seconds alone had the appeal claim the *report*, which then
    vanished from the review stage — the manager could neither see it nor edit it, and approving
    wrote it out of the round's record.

    The pair written by `finalize_appeals_review` always agrees on the text too, both rows being
    copied from one `StagedPenalty`, so including it separates these without breaking that.
    """
    db_path, ids = await _seed(tmp_path, "hydrate_same_size")
    await _penalty(
        db_path, ids["race_101"], seconds=5,
        description="Contact at turn one", justification="Wholly at fault",
    )
    await _appeal(
        db_path, ids["race_101"], seconds=5,
        description="Track limits, lap 12", justification="Appeal upheld",
    )

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert [r.description for r in reports] == ["Contact at turn one"]
    assert [a.description for a in appeals] == ["Track limits, lap 12"]


async def test_a_decision_comes_back_with_its_author_and_its_time(tmp_path):
    """**Who decided it and when survive an amendment** (#345). The report and appeal stages
    write a session's decisions out again; without these, every kept verdict would name the
    admin who amended the round, at the moment they did."""
    db_path, ids = await _seed(tmp_path, "hydrate_provenance")
    await _penalty(db_path, ids["race_101"], description="Report", justification="R")
    await _appeal(db_path, ids["race_102"], description="Appeal", justification="A")

    reports, appeals, _ = await load_staged_from_records(db_path, ROUND_ID)

    assert (reports[0].decided_by, reports[0].decided_at) == (
        "77", "2026-02-02T00:00:00+00:00"
    )
    assert (appeals[0].decided_by, appeals[0].decided_at) == (
        "78", "2026-02-03T00:00:00+00:00"
    )
