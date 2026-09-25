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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType
from leaguebot.core.models.session_result import SessionResult
from leaguebot.core.services.placement_service import PlacementService
from leaguebot.results.services.result_submission_service import (
    _build_division_validation_data,
    _row_dict_from_qualifying,
    _row_dict_from_race,
    other_active_team_assignments,
    save_session_result,
    validate_submission_block,
)
from leaguebot.results.services.standings_service import (
    compute_and_persist_round,
    compute_team_standings,
    previous_standing_positions,
)
from leaguebot.core.services.team_service import TeamService

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
        team_of_shorthand=data.team_of_shorthand,
    )


async def _record(db_path: str, round_id: int, *, ferrari_wins: bool) -> int:
    """Submit a race of *round_id*, naming each team by its shorthand, and score it by hand."""
    ferrari = f"<@{FERRARI_DRIVER}>, Ferrari"
    rival = f"<@{RIVAL_DRIVER}>, Rival"
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
    first = await _record(db_path, ROUNDS[0], ferrari_wins=True)
    await compute_and_persist_round(db_path, ROUNDS[0], DIVISION_ID)
    await _replace_ferrari_role(db_path)
    second = await _record(db_path, ROUNDS[1], ferrari_wins=False)
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
    from leaguebot.results.services.results_post_service import _load_driver_rows, post_session_results

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
    from leaguebot.image.services.image_results_post import _team_names

    db_path, teams, _ = await _season_raced_across_a_role_change(tmp_path)
    guild = MagicMock()
    guild.get_role.return_value = None

    names = await _team_names(_bot(db_path), guild, DIVISION_ID, [teams["Ferrari"]])

    assert names == {teams["Ferrari"]: "Ferrari"}


# ── The submission ───────────────────────────────────────────────────────────


async def test_submission_resolves_a_shorthand_to_the_division_team(tmp_path):
    db_path, teams = await _league(tmp_path)

    parsed = await _validate(
        db_path,
        [
            f"1, <@{FERRARI_DRIVER}>, Ferrari, 46:23.569, 1:14.523, N/A",
            f"2, <@{RIVAL_DRIVER}>, Rival, +5.000, 1:14.600, N/A",
        ],
    )

    # Rival is Pro's own, never the team of the same name in Am.
    assert [row.team_instance_id for row in parsed] == [teams["Ferrari"], teams["Rival"]]


async def test_submission_rejects_a_shorthand_of_no_team_in_the_division(tmp_path):
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path,
        [
            f"1, <@{FERRARI_DRIVER}>, Ferrari, 46:23.569, 1:14.523, N/A",
            f"2, <@{RIVAL_DRIVER}>, Ghost, +5.000, 1:14.600, N/A",
        ],
    )

    assert "Row 2: No team of this division has the shorthand `Ghost`." in errors


async def test_submission_refuses_a_role_mention_in_the_team_column(tmp_path):
    """A team is typed by its shorthand alone; its role is only for mentioning it (#381,
    decided 2026-09-22). A mention of the team's own role is refused, saying so."""
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, <@&{OLD_ROLE}>, 46:23.569, 1:14.523, N/A"]
    )

    assert (
        f"Row 1: <@&{OLD_ROLE}> is a role, and a role does not name a team. "
        "Name a team by its shorthand."
    ) in errors


async def test_the_shorthand_still_names_the_team_after_its_role_is_replaced(tmp_path):
    db_path, teams = await _league(tmp_path)
    await _replace_ferrari_role(db_path)

    parsed = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, Ferrari, 46:23.569, 1:14.523, N/A"]
    )

    assert [row.team_instance_id for row in parsed] == [teams["Ferrari"]]


async def test_a_refusal_names_the_team_a_driver_is_seated_in(tmp_path):
    """By name, while echoing the shorthand that was typed."""
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, Rival, 46:23.569, 1:14.523, N/A"]
    )

    assert errors == [
        f"Row 1: driver <@{FERRARI_DRIVER}> submitted as Rival "
        "but is assigned to **Ferrari**."
    ]


async def test_a_refusal_names_the_team_an_earlier_session_recorded_across_a_role_change(
    tmp_path,
):
    """Qualifying was recorded while Ferrari held its old role. Once the role is replaced,
    the refusal still names the team the session recorded, which is Ferrari."""
    db_path, _teams = await _league(tmp_path)
    parsed = await _validate(
        db_path,
        [f"1, <@{FERRARI_DRIVER}>, Ferrari, Soft, 1:23.456, N/A"],
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
        [f"1, <@{FERRARI_DRIVER}>, Rival, 46:23.569, 1:14.523, N/A"],
        other=other,
    )

    assert (
        f"Row 1: driver <@{FERRARI_DRIVER}> was recorded under **Ferrari** in Feature "
        f"Qualifying of this round, but is submitted here as Rival."
    ) in errors


# ── A team is typed by its shorthand (#381) ────────────────────────────────


async def test_submission_names_a_team_by_its_shorthand(tmp_path):
    """A team is typed by its shorthand, in any case (#381)."""
    db_path, teams = await _league(tmp_path)

    parsed = await _validate(
        db_path,
        [
            f"1, <@{FERRARI_DRIVER}>, FERRARI, 46:23.569, 1:14.523, N/A",
            f"2, <@{RIVAL_DRIVER}>, rival, +5.000, 1:14.600, N/A",
        ],
    )

    assert [row.team_instance_id for row in parsed] == [teams["Ferrari"], teams["Rival"]]


async def test_submission_refuses_the_shorthand_of_a_team_with_no_role(tmp_path):
    """Decided 2026-09-22, and reconfirmed when the role stopped naming a team: a team with no
    role stays out of a submission."""
    db_path, _teams = await _league(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM team_role_configs WHERE team_name = 'Ferrari'")
        await db.commit()

    errors = await _validate(
        db_path, [f"1, <@{RIVAL_DRIVER}>, Ferrari, 46:23.569, 1:14.523, N/A"]
    )

    assert any("No team of this division has the shorthand `Ferrari`" in e for e in errors)


async def test_submission_refuses_the_reserve_team_by_its_shorthand(tmp_path):
    """A reserve stands in for a team's car and is recorded under that team."""
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{RIVAL_DRIVER}>, Reserve, 46:23.569, 1:14.523, N/A"]
    )

    assert any("`Reserve`" in e for e in errors)


@pytest.mark.parametrize(
    "typed,said",
    [
        ("@everyone", "`@everyone` is not a team"),
        ("@here", "`@here` is not a team"),
        ("<@102>", "<@102> is a member, not a team"),
        ("123456789012345678", "is a Discord ID, not a team"),
    ],
)
async def test_submission_refuses_everyone_here_and_a_user_mention_as_a_team(
    tmp_path, typed, said
):
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{RIVAL_DRIVER}>, {typed}, 46:23.569, 1:14.523, N/A"]
    )

    assert any(e.startswith("Row 1: ") and said in e for e in errors)


async def test_an_empty_team_column_is_refused(tmp_path):
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(db_path, [f"1, <@{RIVAL_DRIVER}>, , 46:23.569, 1:14.523, N/A"])

    assert errors == ["Row 1: Team must be named, by its shorthand."]


async def test_a_refusal_echoes_the_shorthand_as_typed(tmp_path):
    db_path, _teams = await _league(tmp_path)

    errors = await _validate(
        db_path, [f"1, <@{FERRARI_DRIVER}>, rival, 46:23.569, 1:14.523, N/A"]
    )

    assert errors == [
        f"Row 1: driver <@{FERRARI_DRIVER}> submitted as rival but is assigned to **Ferrari**."
    ]
