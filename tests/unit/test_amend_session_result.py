"""Writing an amended classification over a round that has already reached FINAL.

Issue #208. `amend_session_result` was uncovered. It is what `/round results amend` calls once
the corrected paste has been validated, and it is destructive by design.

**Nothing is superseded; the classification being replaced is gone.** The session header is
updated in place and the driver rows are deleted outright, then re-inserted from the amendment.
That is why the command is a league admin's rather than a manager's (#116), and
`test_the_previous_classification_does_not_survive` states it as the outcome rather than leaving
it to a comment — a reader who assumed the old rows were kept for audit would build on a
history that does not exist.

**Only the amended session is touched.** A round has up to four sessions; amending the feature
race must leave the feature qualifying exactly as it was, and the delete is scoped by the
session's own result id for that reason. Qualifying and race rows live in different tables, and
each amendment clears only its own.

**An archived season refuses.** A COMPLETED season is the league's published record, and the
guard runs before anything is written.

**A driver who appears in an amended classification becomes a former driver.** The flag is what
keeps their profile when they are later sacked, because their name is now on a result — the same
rule a first submission applies, and an amendment that forgot it would let a sack delete a
profile a result points at.

**The points are re-applied from the configuration, and the posts replaced.** The parsed rows
carry no points; without `_apply_points_from_config` the amended session would score nothing.
With a guild, the round's final results and every later round's standings are reposted; without
one — a restart, the bot removed from the server — the standings are still recomputed, because
the database has to be right whether or not Discord can be told.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.result_submission_service import amend_session_result  # noqa: E402
from services.season_service import SeasonImmutableError  # noqa: E402

SERVER_ID = 12908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
AMENDER = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, name: str = "amend_result", season_status: str = "ACTIVE"):
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 6, '2026-01-01', ?)",
            (SEASON_ID, SERVER_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        race = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Old')",
            (ROUND_ID, DIVISION_ID),
        )
        for position, driver in enumerate((101, 102), start=1):
            await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, ?)",
                (race.lastrowid, driver, position),
            )
        quali = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', 'Old')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (quali.lastrowid,),
        )
        for profile_id, driver in ((31, 101), (32, 102), (33, 103)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "former_driver) VALUES (?, ?, 'ASSIGNED', 0)",
                (profile_id, str(driver)),
            )
        await db.commit()
    return db_path


def _race_row(driver: int, position: int, *, total_time: str = "1:30:00.000", **extra):
    row = {
        "driver_user_id": driver,
        "team_role_id": 3001,
        "position": position,
        "outcome": "CLASSIFIED",
        "total_time": total_time,
        "fastest_lap": "1:20.000",
    }
    row.update(extra)
    return row


def _bot(*, guild=True):
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=MagicMock() if guild else None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


async def _amend(
    db_path,
    rows,
    *,
    session_type=SessionType.FEATURE_RACE,
    bot=None,
    config_name="Standard",
    fl_override=None,
):
    bot = bot or _bot()
    with patch(
        "services.result_submission_service._apply_points_from_config", new=AsyncMock()
    ) as apply_points, patch(
        "services.results_post_service.delete_and_repost_final_results", new=AsyncMock()
    ) as repost, patch(
        "services.results_post_service.repost_subsequent_standings", new=AsyncMock()
    ) as subsequent, patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade:
        await amend_session_result(
            db_path,
            ROUND_ID,
            DIVISION_ID,
            session_type,
            rows,
            config_name,
            AMENDER,
            bot,
            fl_driver_override=fl_override,
        )
    return {
        "bot": bot,
        "apply_points": apply_points,
        "repost": repost,
        "subsequent": subsequent,
        "cascade": cascade,
    }


async def _race_drivers(db_path) -> list[tuple[int, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, finishing_position FROM race_session_results "
            "ORDER BY finishing_position"
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def _header(db_path, session_type: str = "FEATURE_RACE") -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT submitted_by, config_name, fl_driver_override, status "
            "FROM session_results WHERE round_id = ? AND session_type = ?",
            (ROUND_ID, session_type),
        )
        return dict(await cursor.fetchone())


# ---------------------------------------------------------------------------
# The classification
# ---------------------------------------------------------------------------


async def test_the_amended_classification_is_written(tmp_path):
    db_path = await _make_db(tmp_path)

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    assert await _race_drivers(db_path) == [(102, 1), (101, 2)]


async def test_the_previous_classification_does_not_survive(tmp_path):
    """Nothing is superseded: the rows are deleted outright. Stated as the outcome, because
    a reader assuming the old rows were kept would build on a history that does not
    exist."""
    db_path = await _make_db(tmp_path, name="amend_gone")

    await _amend(db_path, [_race_row(103, 1)])

    assert await _race_drivers(db_path) == [(103, 1)]


async def test_the_header_is_updated_in_place(tmp_path):
    """Not a new session row: the round still has one feature race, and it records who
    amended it and under which configuration."""
    db_path = await _make_db(tmp_path, name="amend_header")

    await _amend(db_path, [_race_row(101, 1)], config_name="Standard", fl_override=102)

    header = await _header(db_path)
    assert header["submitted_by"] == AMENDER
    assert header["config_name"] == "Standard"
    assert header["fl_driver_override"] == 102
    assert header["status"] == "ACTIVE"


async def test_another_session_of_the_round_is_untouched(tmp_path):
    """Amending the race must leave the qualifying exactly as it was."""
    db_path = await _make_db(tmp_path, name="amend_scope")

    await _amend(db_path, [_race_row(103, 1)])

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM qualifying_session_results")
        assert (await cursor.fetchone())[0] == 1
    assert (await _header(db_path, "FEATURE_QUALIFYING"))["config_name"] == "Old"


async def test_amending_qualifying_clears_only_qualifying_rows(tmp_path):
    """The two live in different tables, and each amendment clears only its own."""
    db_path = await _make_db(tmp_path, name="amend_quali")

    await _amend(
        db_path,
        [{"driver_user_id": 102, "team_role_id": 3001, "position": 1, "best_lap": "1:19.000"}],
        session_type=SessionType.FEATURE_QUALIFYING,
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, best_lap FROM qualifying_session_results"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(102, "1:19.000")]
    assert len(await _race_drivers(db_path)) == 2


async def test_a_row_without_a_position_takes_its_place_in_the_paste(tmp_path):
    """The paste is in finishing order, and a parser that left the position off must not
    put every driver in P0."""
    db_path = await _make_db(tmp_path, name="amend_implicit")
    rows = [
        {"driver_user_id": 102, "team_role_id": 3001, "total_time": "1:30:00.000"},
        {"driver_user_id": 101, "team_role_id": 3001, "total_time": "+5.000"},
    ]

    await _amend(db_path, rows)

    assert await _race_drivers(db_path) == [(102, 1), (101, 2)]


async def test_a_parsed_row_object_is_read_like_a_dict(tmp_path):
    """The command hands over parser dataclasses, other callers hand over dicts, and both
    have to write the same row."""
    db_path = await _make_db(tmp_path, name="amend_objects")
    row = SimpleNamespace(
        driver_user_id=103,
        team_role_id=3001,
        position=1,
        outcome="CLASSIFIED",
        tyre=None,
        best_lap=None,
        gap=None,
        total_time="1:30:00.000",
        fastest_lap=None,
        ingame_penalties=None,
        postrace_penalty="N/A",
        appeal_penalty="N/A",
    )

    await _amend(db_path, [row])

    assert await _race_drivers(db_path) == [(103, 1)]


async def test_a_session_the_round_does_not_have_is_refused(tmp_path):
    """Updating a header that does not exist would silently match nothing and write driver
    rows with no session to belong to."""
    db_path = await _make_db(tmp_path, name="amend_nosession")

    with pytest.raises(ValueError, match="No session_results row"):
        await _amend(db_path, [_race_row(101, 1)], session_type=SessionType.SPRINT_RACE)

    assert len(await _race_drivers(db_path)) == 2


async def test_an_archived_season_is_refused_before_anything_is_written(tmp_path):
    """A COMPLETED season is the league's published record."""
    db_path = await _make_db(tmp_path, name="amend_archived", season_status="COMPLETED")

    with pytest.raises(SeasonImmutableError):
        await _amend(db_path, [_race_row(103, 1)])

    assert await _race_drivers(db_path) == [(101, 1), (102, 2)]


# ---------------------------------------------------------------------------
# The drivers in it
# ---------------------------------------------------------------------------


async def test_a_driver_in_the_amended_result_becomes_a_former_driver(tmp_path):
    """Their name is now on a result, so a later sack must keep their profile."""
    db_path = await _make_db(tmp_path, name="amend_former")

    await _amend(db_path, [_race_row(103, 1)])

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT former_driver FROM driver_profiles WHERE id = 33")
        assert (await cursor.fetchone())[0] == 1


async def test_the_result_row_is_linked_to_the_drivers_profile(tmp_path):
    """The stable link a standings snapshot survives a Discord account change by."""
    db_path = await _make_db(tmp_path, name="amend_profile")

    await _amend(db_path, [_race_row(103, 1)])

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT driver_profile_id FROM race_session_results")
        assert (await cursor.fetchone())[0] == 33


async def test_a_driver_with_no_profile_is_still_recorded(tmp_path):
    """An unregistered guest driver is ordinary in a league, and the result is theirs
    whether or not the bot knows them."""
    db_path = await _make_db(tmp_path, name="amend_noprofile")

    await _amend(db_path, [_race_row(999, 1)])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, driver_profile_id FROM race_session_results"
        )
        assert tuple(await cursor.fetchone()) == (999, None)


async def test_a_lapped_driver_is_stored_as_laps_behind(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_lapped")

    await _amend(db_path, [_race_row(101, 1), _race_row(102, 2, total_time="+1 Lap")])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT laps_behind, base_time_ms FROM race_session_results "
            "WHERE driver_user_id = 102"
        )
        assert tuple(await cursor.fetchone()) == (1, None)


async def test_a_gap_is_resolved_against_the_winners_time(tmp_path):
    """A delta is meaningless stored on its own; the base time is what penalties are later
    added to."""
    db_path = await _make_db(tmp_path, name="amend_delta")

    await _amend(
        db_path,
        [_race_row(101, 1, total_time="1:00.000"), _race_row(102, 2, total_time="+5.000")],
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, base_time_ms FROM race_session_results "
            "ORDER BY finishing_position"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(101, 60000), (102, 65000)]


async def test_a_retirement_has_no_base_time(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_dnf")

    await _amend(
        db_path, [_race_row(101, 1), _race_row(102, 2, outcome="DNF", total_time="DNF")]
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT outcome, base_time_ms FROM race_session_results WHERE driver_user_id = 102"
        )
        assert tuple(await cursor.fetchone()) == ("DNF", None)


# ---------------------------------------------------------------------------
# Points, posts and the log
# ---------------------------------------------------------------------------


async def test_the_points_are_re_applied_from_the_configuration(tmp_path):
    """The parsed rows carry no points; without this the amended session scores nothing."""
    db_path = await _make_db(tmp_path, name="amend_points")

    stubs = await _amend(db_path, [_race_row(101, 1)], config_name="Standard")

    stubs["apply_points"].assert_awaited_once()
    args = stubs["apply_points"].await_args.args
    assert args[2] == SEASON_ID
    assert args[3] == "Standard"


async def test_the_round_and_every_later_standing_are_reposted(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_repost")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    stubs["repost"].assert_awaited_once()
    assert stubs["repost"].await_args.kwargs["label"] == "Final Results"
    stubs["subsequent"].assert_awaited_once()
    stubs["cascade"].assert_not_awaited()


async def test_without_a_guild_the_standings_are_still_recomputed(tmp_path):
    """The database has to be right whether or not Discord can be told."""
    db_path = await _make_db(tmp_path, name="amend_noguild")

    stubs = await _amend(db_path, [_race_row(101, 1)], bot=_bot(guild=False))

    stubs["cascade"].assert_awaited_once()
    stubs["repost"].assert_not_awaited()


async def test_the_amendment_is_logged(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_log")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    logged = str(stubs["bot"].output_router.post_log.await_args.args[1])
    assert "RESULT_AMENDED" in logged
    assert f"<@{AMENDER}>" in logged
    assert "round: 3" in logged
    assert "FEATURE_RACE" in logged
