"""A team's results stay with the team when its role is replaced (issue #375).

A team's Discord role is a property of the team, and one a league changes: `/team role` exists
so that a role deleted by mistake mid-season can be replaced. Results used to record the role
itself, so a replacement split the team in two — its earlier points under a role that no
longer mapped to anything, its later ones under the new — in the team standings, the posts
and the graphics alike.

A result records the division's team instead. The role typed in a submission is resolved to
that team once, when it is typed, and nothing recorded depends on the role afterwards.

Every test here runs the same season: a division of two teams, Ferrari and Rival, whose
first round is recorded while Ferrari holds one role and whose second is recorded after the
league has given Ferrari another.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from models.session_result import SessionResult  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    _build_division_validation_data,
    _row_dict_from_qualifying,
    _row_dict_from_race,
    other_active_team_assignments,
    save_session_result,
    validate_submission_block,
)
from services.standings_service import (  # noqa: E402
    compute_and_persist_round,
    compute_team_standings,
    previous_standing_positions,
)
from services.team_service import TeamService  # noqa: E402

DIVISION_ID = 37
OTHER_DIVISION_ID = 38
ROUNDS = (371, 372)

OLD_ROLE, NEW_ROLE, RIVAL_ROLE, RESERVE_ROLE = 7001, 7002, 7101, 7900
FERRARI_DRIVER, RIVAL_DRIVER = 101, 102


async def _league(tmp_path) -> tuple[str, dict[str, int]]:
    """The season, with nothing yet raced. Returns the database and each team's id."""
    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    teams: dict[str, int] = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        for division, name, tier in ((DIVISION_ID, "Pro", 1), (OTHER_DIVISION_ID, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, ?, ?, 555)",
                (division, name, tier),
            )
        for name, role, is_reserve in (
            ("Ferrari", OLD_ROLE, 0), ("Rival", RIVAL_ROLE, 0), ("Reserve", RESERVE_ROLE, 1),
        ):
            await db.execute(
                "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) VALUES (?, ?, ?, ?)",
                (name, name, -1 if is_reserve else 2, is_reserve),
            )
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
                (name, role),
            )
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, ?)",
                (DIVISION_ID, name, name, is_reserve),
            )
            teams[name] = cursor.lastrowid
        # The other division fields a Rival of its own, which a Pro result must never name.
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Rival', 'Rival', 2, 0)",
            (OTHER_DIVISION_ID,),
        )
        teams["Am Rival"] = cursor.lastrowid
        for user_id, team in ((FERRARI_DRIVER, "Ferrari"), (RIVAL_DRIVER, "Rival")):
            cursor = await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state) "
                "VALUES (?, 'ASSIGNED')",
                (str(user_id),),
            )
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, 1, ?)",
                (teams[team], cursor.lastrowid),
            )
        for number, round_id in enumerate(ROUNDS, start=1):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, ?, 'NORMAL', ?)",
                (round_id, DIVISION_ID, number, f"2026-0{number}-01T18:00:00"),
            )
        await db.commit()
    return db_path, teams


def _bot(db_path: str):
    return SimpleNamespace(db_path=db_path, team_service=TeamService(db_path))


async def _validate(
    db_path: str, lines: list[str], session_type=SessionType.FEATURE_RACE, *, other=None
):
    data = await _build_division_validation_data(DIVISION_ID, _bot(db_path))
    return validate_submission_block(
        lines,
        session_type,
        data.division_driver_ids,
        data.team_of_role,
        data.reserve_team_role_id,
        data.driver_team_map,
        data.reserve_driver_ids,
        other_active_assignments=other,
        team_names=data.team_names,
    )


async def _record(db_path: str, round_id: int, ferrari_role: int, *, ferrari_wins: bool) -> int:
    """Submit a race of *round_id* naming Ferrari by *ferrari_role*, and score it by hand."""
    ferrari = f"<@{FERRARI_DRIVER}>, <@&{ferrari_role}>"
    rival = f"<@{RIVAL_DRIVER}>, <@&{RIVAL_ROLE}>"
    first, second = (ferrari, rival) if ferrari_wins else (rival, ferrari)
    parsed = await _validate(
        db_path,
        [f"1, {first}, 46:23.569, 1:14.523, N/A", f"2, {second}, +5.000, 1:14.600, N/A"],
    )
    assert parsed and not isinstance(parsed[0], str), parsed
    session_id = await save_session_result(
        db_path, round_id, DIVISION_ID, SessionType.FEATURE_RACE, "ACTIVE", None, 77,
        [_row_dict_from_race(row) for row in parsed],
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE race_session_results SET points_awarded = "
            "CASE finishing_position WHEN 1 THEN 25 ELSE 18 END WHERE session_result_id = ?",
            (session_id,),
        )
        await db.commit()
    return session_id


async def _replace_ferrari_role(db_path: str) -> None:
    """What `/team role` records. Moving the drivers' Discord roles touches no result."""
    await PlacementService(db_path).set_team_role_config("Ferrari", NEW_ROLE)


async def _season_raced_across_a_role_change(tmp_path):
    db_path, teams = await _league(tmp_path)
    first = await _record(db_path, ROUNDS[0], OLD_ROLE, ferrari_wins=True)
    await compute_and_persist_round(db_path, ROUNDS[0], DIVISION_ID)
    await _replace_ferrari_role(db_path)
    second = await _record(db_path, ROUNDS[1], NEW_ROLE, ferrari_wins=False)
    await compute_and_persist_round(db_path, ROUNDS[1], DIVISION_ID)
    return db_path, teams, (first, second)


# ── The standings ─────────────────────────────────────────────────────────────


async def test_team_standings_keep_one_entry_across_a_role_change(tmp_path):
    """The defect itself: Ferrari once, holding both rounds' points, and no stray entry."""
    db_path, teams, _ = await _season_raced_across_a_role_change(tmp_path)

    snapshots = await compute_team_standings(db_path, DIVISION_ID, ROUNDS[1])

    points = {s.team_instance_id: s.total_points for s in snapshots}
    assert points == {teams["Ferrari"]: 25 + 18, teams["Rival"]: 18 + 25}


async def test_position_change_survives_a_role_change(tmp_path):
    """The movement arrow reads the previous round by the same key the current one carries."""
    db_path, teams, _ = await _season_raced_across_a_role_change(tmp_path)

    previous = await previous_standing_positions(
        db_path, DIVISION_ID, ROUNDS[1], teams=True
    )

    assert previous is not None
    assert teams["Ferrari"] in previous


# ── What a league reads ──────────────────────────────────────────────────────


async def test_results_post_names_a_team_after_its_role_is_replaced(tmp_path):
    """The first round, posted again after the change, still says Ferrari — never a mention
    of a role that may since have been deleted from the server."""
    from services.results_post_service import _load_driver_rows, post_session_results

    db_path, _teams, (first, _second) = await _season_raced_across_a_role_change(tmp_path)
    rows = await _load_driver_rows(db_path, first, SessionType.FEATURE_RACE)
    sent: list[str] = []

    async def send(content=None, **_kwargs):
        sent.append(content)
        return MagicMock(id=900 + len(sent))

    channel = MagicMock()
    channel.send = AsyncMock(side_effect=send)
    guild = MagicMock()
    guild.get_role.return_value = None
    session = SessionResult(
        id=first, round_id=ROUNDS[0], division_id=DIVISION_ID,
        session_type=SessionType.FEATURE_RACE, status="ACTIVE", config_name=None,
        submitted_by=77, submitted_at=None,
    )

    await post_session_results(
        db_path, session, rows, {FERRARI_DRIVER: 25, RIVAL_DRIVER: 18}, channel, guild,
        1, "Spa", "Provisional Results", is_sprint=False,
    )

    text = "\n".join(sent)
    assert "(Ferrari)" in text and "(Rival)" in text
    assert "<@&" not in text


async def test_results_graphic_names_a_team_after_its_role_is_replaced(tmp_path):
    """The graphic finds the name and badge from the team, not by asking Discord about a role."""
    from services.image_results_post import _team_names

    db_path, teams, _ = await _season_raced_across_a_role_change(tmp_path)
    guild = MagicMock()
    guild.get_role.return_value = None

    names = await _team_names(_bot(db_path), guild, DIVISION_ID, [teams["Ferrari"]])

    assert names == {teams["Ferrari"]: "Ferrari"}


# ── The submission ───────────────────────────────────────────────────────────


async def test_submission_resolves_a_role_to_the_division_team(tmp_path):
    db_path, teams = await _league(tmp_path)

    parsed = await _validate(
        db_path,
        [
            f"1, <@{FERRARI_DRIVER}>, <@&{OLD_ROLE}>, 46:23.569, 1:14.523, N/A",
            f"2, <@{RIVAL_DRIVER}>, <@&{RIVAL_ROLE}>, +5.000, 1:14.600, N/A",
        ],
    )

    # Rival is Pro's own, never the team of the same name in Am.
    assert [row.team_instance_id for row in parsed] == [teams["Ferrari"], teams["Rival"]]


async def test_submission_rejects_a_role_of_no_team_in_the_division(tmp_path):
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path,
        [
            f"1, <@{FERRARI_DRIVER}>, <@&{OLD_ROLE}>, 46:23.569, 1:14.523, N/A",
            f"2, <@{RIVAL_DRIVER}>, <@&4242>, +5.000, 1:14.600, N/A",
        ],
    )

    assert "Row 2: <@&4242> is not a valid team role for this division." in errors


async def test_a_replaced_role_no_longer_names_the_team(tmp_path):
    """Resolved once, when typed: the old role names nothing after the change."""
    db_path, _teams = await _league(tmp_path)
    await _replace_ferrari_role(db_path)

    errors = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, <@&{OLD_ROLE}>, 46:23.569, 1:14.523, N/A"]
    )

    assert f"Row 1: <@&{OLD_ROLE}> is not a valid team role for this division." in errors


async def test_a_refusal_names_the_team_a_driver_is_seated_in(tmp_path):
    """By name, while echoing the role that was typed."""
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, <@&{RIVAL_ROLE}>, 46:23.569, 1:14.523, N/A"]
    )

    assert errors == [
        f"Row 1: driver <@{FERRARI_DRIVER}> submitted as <@&{RIVAL_ROLE}> "
        "but is assigned to **Ferrari**."
    ]


async def test_a_refusal_names_the_team_an_earlier_session_recorded_across_a_role_change(
    tmp_path,
):
    """Qualifying stood under Ferrari's old role. Once the role is replaced, that role names
    nothing — the refusal names the team the session recorded, which is Ferrari still."""
    db_path, _teams = await _league(tmp_path)
    parsed = await _validate(
        db_path,
        [f"1, <@{FERRARI_DRIVER}>, <@&{OLD_ROLE}>, Soft, 1:23.456, N/A"],
        SessionType.FEATURE_QUALIFYING,
    )
    await save_session_result(
        db_path, ROUNDS[0], DIVISION_ID, SessionType.FEATURE_QUALIFYING, "ACTIVE", None, 77,
        [_row_dict_from_qualifying(row) for row in parsed],
    )
    await _replace_ferrari_role(db_path)
    other = await other_active_team_assignments(
        db_path, ROUNDS[0], SessionType.FEATURE_RACE
    )

    errors = await _validate(
        db_path,
        [f"1, <@{FERRARI_DRIVER}>, <@&{RIVAL_ROLE}>, 46:23.569, 1:14.523, N/A"],
        other=other,
    )

    assert (
        f"Row 1: driver <@{FERRARI_DRIVER}> was recorded under **Ferrari** in Feature "
        f"Qualifying of this round, but is submitted here as <@&{RIVAL_ROLE}>."
    ) in errors
