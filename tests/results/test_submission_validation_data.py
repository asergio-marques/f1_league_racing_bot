"""What the submission validator is handed before it validates anything.

Issue #208. Three helpers in `result_submission_service` feed every submission check, and none
of them was covered. They are small and they are read by nothing else, which is precisely why a
mistake in one surfaces as a validation result nobody can explain.

**A driver belongs to the division that seats them.** `_build_division_validation_data` walks
the division's teams and their seats to produce the set of drivers who may appear in a
submission, the team roles that may appear beside them — each resolved to the division's team it
names, which is what a result records (#375) — and the map from each driver to their team. A
driver missing from that set is rejected as not being in the division, so an empty seat and an
unseated driver are the same thing here — which is correct, and worth saying out loud.

**Reserves are in the division and are not in a team, both at once.** A reserve appears in the
driver set and in the team map like anyone else, and *also* in `reserve_driver_ids`, because
submission validation treats them differently — they may race for whichever team needed them.
Returning them in only one of the three would make them either unrecognised or indistinguishable
from a full-time driver, and both are wrong in ways that surface as a confusing refusal.

**A team without a Discord role is skipped entirely, along with its seats.** The role is what a
submission actually names — a team nobody can mention cannot be matched against anything in the
posted results, so its drivers are not validatable and are deliberately absent rather than
half-present. `test_a_team_without_a_role_contributes_no_drivers` is the one to read before
"fixing" that skip.

**A driver's team must agree across the sessions of one round.** `other_active_team_assignments`
is what the second session's validation reads to catch a disagreement the first already
recorded: a driver who qualified for one team and raced for another is a submission mistake, and
catching it needs the *other* sessions, never the one being submitted. Superseded submissions
are excluded — a resubmission supersedes rather than deletes, and reading a superseded row would
check a new submission against results that were themselves corrected.

**Where the first record wins.** Where two other sessions disagree with each other, the map
keeps the first — the check exists to report *a* disagreement, and reporting the later one would
be arbitrary in a different direction.

**A round's context is resolved through the division and the season.** Every posted result is
titled with a season number, a round number and a division name, and the chain is what makes a
round id enough to post with. A round with no season is an impossible state and raises rather
than posting a result titled after nothing.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.result_submission_service import (
    _build_division_validation_data,
    _get_round_context,
    _make_slug,
    other_active_team_assignments,
)
from tests.support.teams import seed_team_instances

SERVER_ID = 10208
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
PRO_ROLE = 3001
AM_ROLE = 3002
RESERVE_ROLE = 3003
#: Each division team's id, kept apart from the role numbers so a test cannot pass by
#: mistaking one for the other.
TEAM_IDS = {"Red": 1101, "Blue": 1102, "Reserves": 1103, "Ghost": 1104, "Unknown": 1105}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "validation_data") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 7, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro Division', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()
    return db_path


async def _seed_session(
    db_path: str,
    session_type: SessionType,
    rows: list[tuple[int, int]],
    *,
    status: str = "ACTIVE",
    table: str = "qualifying_session_results",
    round_id: int = ROUND_ID,
) -> int:
    """One session of *round_id* with *rows* of (driver_user_id, team_instance_id)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, ?, ?)",
            (round_id, DIVISION_ID, session_type.value, status),
        )
        session_id = cursor.lastrowid
        await seed_team_instances(db, DIVISION_ID, *{role for _driver, role in rows})
        for position, (driver, role) in enumerate(rows, start=1):
            await db.execute(
                f"INSERT INTO {table} (session_result_id, driver_user_id, team_instance_id, "
                f"finishing_position) VALUES (?, ?, ?, ?)",
                (session_id, driver, role, position),
            )
        await db.commit()
    return session_id


def _seat(user_id: int | None, number: int = 1) -> dict:
    return {
        "seat_number": number,
        "discord_user_id": str(user_id) if user_id is not None else None,
    }


def _team(name: str, seats: list[dict], *, reserve: bool = False) -> dict:
    return {"id": TEAM_IDS[name], "name": name, "is_reserve": reserve, "seats": seats}


def _bot(teams: list[dict], roles: dict[str, int | None]):
    bot = MagicMock()
    bot.team_service = MagicMock()
    bot.team_service.get_division_teams = AsyncMock(return_value=teams)
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[{"name": n, "role_id": r} for n, r in roles.items()]
    )
    return bot


async def _build(teams, roles):
    return await _build_division_validation_data(DIVISION_ID, _bot(teams, roles))


def _standard():
    teams = [
        _team("Red", [_seat(101, 1), _seat(102, 2)]),
        _team("Blue", [_seat(201, 1), _seat(202, 2)]),
        _team("Reserves", [_seat(301, 1)], reserve=True),
    ]
    roles = {"Red": PRO_ROLE, "Blue": AM_ROLE, "Reserves": RESERVE_ROLE}
    return teams, roles


# ---------------------------------------------------------------------------
# Who may appear in a submission
# ---------------------------------------------------------------------------


async def test_every_seated_driver_is_in_the_division():
    """The set is what a submission is checked against — a driver missing from it is
    refused as not being in this division."""
    data = await _build(*_standard())

    assert data.division_driver_ids == {101, 102, 201, 202, 301}


async def test_an_empty_seat_contributes_no_driver():
    """A team with a vacancy is ordinary mid-season, and a `None` in the driver set would
    make every submission fail an identity check against nobody."""
    teams = [_team("Red", [_seat(101, 1), _seat(None, 2)])]
    data = await _build(teams, {"Red": PRO_ROLE})

    assert data.division_driver_ids == {101}
    assert data.driver_team_map == {101: TEAM_IDS["Red"]}


async def test_a_driver_is_mapped_to_their_team():
    """The map is what turns "this driver raced for that team" into something checkable.
    It names the division's team, never its role, as a stored result does (#375)."""
    data = await _build(*_standard())

    assert data.driver_team_map[101] == TEAM_IDS["Red"]
    assert data.driver_team_map[201] == TEAM_IDS["Blue"]


async def test_seat_ids_are_read_as_numbers():
    """They are stored as text, and a submission carries an integer user id — comparing the
    two without the conversion silently matches nothing at all."""
    teams = [_team("Red", [_seat(101, 1)])]
    data = await _build(teams, {"Red": PRO_ROLE})

    assert 101 in data.division_driver_ids
    assert all(isinstance(uid, int) for uid in data.driver_team_map)


# ---------------------------------------------------------------------------
# Teams, roles and the reserve team
# ---------------------------------------------------------------------------


async def test_each_full_time_role_is_resolved_to_its_team():
    """The one place a role typed in a submission becomes the team a result records."""
    data = await _build(*_standard())

    assert data.team_of_role == {PRO_ROLE: TEAM_IDS["Red"], AM_ROLE: TEAM_IDS["Blue"]}


async def test_the_reserve_team_is_kept_apart_from_the_others():
    """A reserve races for whoever needed them, so their role is not one a submission may
    name as a driver's team — it is named separately and treated separately."""
    data = await _build(*_standard())

    assert data.reserve_team_role_id == RESERVE_ROLE
    assert RESERVE_ROLE not in data.team_of_role


async def test_a_reserve_driver_is_in_the_division_and_flagged_as_a_reserve():
    """All three at once: in the driver set, in the team map, and in the reserve set.
    Returning them in only one would make them either unrecognised or indistinguishable
    from a full-time driver, and both surface as a confusing refusal."""
    data = await _build(*_standard())

    assert 301 in data.division_driver_ids
    assert data.driver_team_map[301] == TEAM_IDS["Reserves"]
    assert data.reserve_driver_ids == {301}


async def test_a_division_with_no_reserve_team_reports_none():
    """Not every league runs reserves, and a falsy role id would be indistinguishable from
    one that exists."""
    teams = [_team("Red", [_seat(101, 1)])]
    data = await _build(teams, {"Red": PRO_ROLE})

    assert data.reserve_team_role_id is None
    assert data.reserve_driver_ids == set()


async def test_a_team_without_a_role_contributes_no_drivers():
    """The role is what a submission actually names. A team nobody can mention cannot be
    matched against anything in the posted results, so its drivers are not validatable and
    are deliberately absent rather than half-present."""
    teams = [_team("Red", [_seat(101, 1)]), _team("Ghost", [_seat(999, 1)])]
    data = await _build(teams, {"Red": PRO_ROLE, "Ghost": None})

    assert data.division_driver_ids == {101}
    assert 999 not in data.driver_team_map
    assert data.team_of_role == {PRO_ROLE: TEAM_IDS["Red"]}


async def test_a_team_the_server_does_not_know_is_skipped():
    """Team names are matched between the division's teams and the server's roles; a
    division team with no matching server row has no role and cannot be validated."""
    teams = [_team("Red", [_seat(101, 1)]), _team("Unknown", [_seat(999, 1)])]
    data = await _build(teams, {"Red": PRO_ROLE})

    assert data.division_driver_ids == {101}


async def test_a_division_with_no_teams_yields_nothing():
    """Which is how a submission against an unseeded division refuses every driver in it,
    rather than accepting all of them."""
    result = await _build([], {})

    assert result == (set(), {}, None, {}, set(), {}, {})


async def test_every_team_that_can_be_named_carries_its_name():
    """For the messages that name a driver's team."""
    data = await _build(*_standard())

    assert data.team_names == {
        TEAM_IDS["Red"]: "Red", TEAM_IDS["Blue"]: "Blue", TEAM_IDS["Reserves"]: "Reserves",
    }


# ---------------------------------------------------------------------------
# What the other sessions of the round already recorded
# ---------------------------------------------------------------------------


async def test_a_team_recorded_by_another_session_is_returned(tmp_path):
    """This is what the second session's validation reads to catch a driver who qualified
    for one team and raced for another."""
    db_path = await _make_db(tmp_path)
    await _seed_session(db_path, SessionType.FEATURE_QUALIFYING, [(101, PRO_ROLE)])

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {101: (PRO_ROLE, "FEATURE_QUALIFYING")}


async def test_the_session_being_submitted_is_excluded(tmp_path):
    """Checking a submission against itself would find agreement every time and catch
    nothing — the whole check is a cross-session one."""
    db_path = await _make_db(tmp_path, name="validation_exclude")
    await _seed_session(db_path, SessionType.FEATURE_RACE, [(101, PRO_ROLE)],
                        table="race_session_results")

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {}


async def test_both_race_and_qualifying_results_are_read(tmp_path):
    """The two live in separate tables, and a check that read only one would miss half the
    sessions of every round."""
    db_path = await _make_db(tmp_path, name="validation_both")
    await _seed_session(db_path, SessionType.FEATURE_QUALIFYING, [(101, PRO_ROLE)])
    await _seed_session(
        db_path, SessionType.SPRINT_RACE, [(202, AM_ROLE)], table="race_session_results"
    )

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {
        101: (PRO_ROLE, "FEATURE_QUALIFYING"),
        202: (AM_ROLE, "SPRINT_RACE"),
    }


async def test_a_superseded_submission_is_not_read(tmp_path):
    """A resubmission supersedes rather than deletes. Reading a superseded row would check a
    new submission against results that were themselves corrected — and refuse it for
    disagreeing with the very mistake the correction fixed."""
    db_path = await _make_db(tmp_path, name="validation_superseded")
    await _seed_session(
        db_path, SessionType.FEATURE_QUALIFYING, [(101, AM_ROLE)], status="SUPERSEDED"
    )

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {}


async def test_another_rounds_sessions_are_not_read(tmp_path):
    """A driver may change team between rounds; that is a transfer, not a mistake."""
    db_path = await _make_db(tmp_path, name="validation_other_round")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (99, ?, 4, '2026-03-01T18:00:00+00:00', 'NORMAL')",
            (DIVISION_ID,),
        )
        await db.commit()
    await _seed_session(
        db_path, SessionType.FEATURE_QUALIFYING, [(101, AM_ROLE)], round_id=99
    )

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {}


async def test_the_first_record_of_a_driver_wins(tmp_path):
    """Where two other sessions disagree with each other the map keeps one of them: the
    check exists to report *a* disagreement, and preferring the later would be arbitrary in
    a different direction."""
    db_path = await _make_db(tmp_path, name="validation_first")
    await _seed_session(db_path, SessionType.FEATURE_QUALIFYING, [(101, PRO_ROLE)])
    await _seed_session(
        db_path, SessionType.SPRINT_RACE, [(101, AM_ROLE)], table="race_session_results"
    )

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.SPRINT_QUALIFYING
    )

    assert len(found) == 1
    assert found[101][0] in {PRO_ROLE, AM_ROLE}


async def test_a_round_with_no_other_sessions_yields_nothing(tmp_path):
    """The first submission of a round has nothing to disagree with, and must not be
    refused for it."""
    db_path = await _make_db(tmp_path, name="validation_empty")

    found = await other_active_team_assignments(
        db_path, ROUND_ID, SessionType.FEATURE_RACE
    )

    assert found == {}


# ---------------------------------------------------------------------------
# Resolving a round to the things a result is titled with
# ---------------------------------------------------------------------------


async def test_a_round_resolves_to_its_season_and_division(tmp_path):
    """Every posted result is titled with these, and the chain is what makes a round id
    enough to post with."""
    db_path = await _make_db(tmp_path, name="validation_context")

    ctx = await _get_round_context(db_path, ROUND_ID)

    assert ctx["season_number"] == 7
    assert ctx["round_number"] == 3
    assert ctx["division_name"] == "Pro Division"


async def test_a_round_resolves_to_what_resubmission_collects_with(tmp_path):
    """A resubmission needs the season to score against and the format to know which sessions
    to ask for. Neither was selected, so resubmitting raised before asking for anything
    (issue #210)."""
    db_path = await _make_db(tmp_path, name="validation_context_resubmit")

    ctx = await _get_round_context(db_path, ROUND_ID)

    assert ctx["season_id"] == SEASON_ID
    assert ctx["round_format"] == "NORMAL"


async def test_an_unknown_round_is_refused_rather_than_posted_empty(tmp_path):
    """A result titled after nothing is worse than a failure a maintainer can read."""
    db_path = await _make_db(tmp_path, name="validation_noround")

    with pytest.raises(ValueError, match="Round 404"):
        await _get_round_context(db_path, 404)


# ---------------------------------------------------------------------------
# The channel slug a division's name becomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Pro Division", "pro-division"),
        ("PRO", "pro"),
        ("Pro & Am", "pro--am"),
        ("Division #1", "division-1"),
        ("Am/Pro", "ampro"),
    ],
)
def test_a_division_name_becomes_a_channel_safe_slug(name, slug):
    """Discord rejects a channel name with punctuation in it, so a league naming a division
    the way a league does must not make the channel uncreatable."""
    assert _make_slug(name) == slug


def test_a_long_division_name_is_truncated():
    """Channel names have a ceiling, and a name that reaches it is a league's choice rather
    than a mistake to refuse."""
    assert len(_make_slug("A" * 60)) == 24
