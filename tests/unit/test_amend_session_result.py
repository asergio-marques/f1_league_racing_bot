"""Writing an amended classification over a round that has already reached FINAL.

Issue #208. `amend_round_results` (once `amend_session_result`) was uncovered. It is what
`/round results amend` calls once the corrected pastes have been validated, and it is
destructive by design.

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
from services.result_submission_service import (  # noqa: E402
    AmendmentWouldOrphanVerdictError,
    AmendedSession,
    amend_round_results,
)
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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 6, '2026-01-01', ?)",
            (SEASON_ID, season_status),
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


@pytest.fixture(autouse=True)
def _no_standings_names():
    """The names a full tie is settled by are resolved through Discord, which these stub bots
    only pretend to reach; ordering by user id keeps the tests about the classification."""
    with patch(
        "services.results_post_service.standings_display_names", new=AsyncMock(return_value=None)
    ):
        yield


def _bot(*, guild=True, attendance=False):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.get_guild = MagicMock(return_value=MagicMock() if guild else None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    # The amendment reposts the attendance sheet where the module is on (#345); off by
    # default here so these tests stay about the results.
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    return bot


async def _amend(
    db_path,
    rows,
    *,
    session_type=SessionType.FEATURE_RACE,
    bot=None,
    config_name="Standard",
    fl_override=None,
    repost_faults=None,
    sessions=None,
):
    """*repost_faults* are the lines the cascade could not post (#237).

    Both repost functions return a list of faults rather than ``None``, so the stubs must
    too: the amendment now adds what comes back to the line it logs.
    """
    bot = bot or _bot()
    with patch(
        "services.result_submission_service._apply_points_from_config", new=AsyncMock()
    ) as apply_points, patch(
        "services.results_post_service.repost_round_results",
        new=AsyncMock(return_value=list(repost_faults or [])),
    ) as repost, patch(
        "services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=[]),
    ) as replay, patch(
        "services.result_submission_service._repost_attendance_after_amendment",
        new=AsyncMock(return_value=[]),
    ) as subsequent, patch(
        "services.standings_service.cascade_recompute_from_round", new=AsyncMock()
    ) as cascade:
        await amend_round_results(
            db_path,
            ROUND_ID,
            DIVISION_ID,
            sessions or [AmendedSession(session_type, rows, config_name, fl_override)],
            AMENDER,
            bot,
        )
    return {
        "bot": bot,
        "apply_points": apply_points,
        "repost": repost,
        "replay": replay,
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


async def _open_amendment_record(db_path) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, 7777, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00+00:00')",
            (ROUND_ID,),
        )
        await db.commit()


async def _former(db_path, profile_id: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT former_driver FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        return (await cursor.fetchone())[0]


async def test_reverting_unmarks_a_driver_the_amendment_made_a_former_driver(tmp_path):
    """#345: a driver pasted in by mistake, the amendment then cancelled or lapsed. The round
    is put back as it was, and so is whether they have ever raced."""
    from services.result_submission_service import revert_abandoned_amendment

    db_path = await _make_db(tmp_path, name="amend_former_reverted")
    await _open_amendment_record(db_path)
    await _amend(db_path, [_race_row(103, 1)])
    assert await _former(db_path, 33) == 1

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(db_path, ROUND_ID)

    assert await _former(db_path, 33) == 0


async def test_a_driver_with_a_result_elsewhere_stays_a_former_driver(tmp_path):
    """The mark stage one raised is taken back only where nothing else would raise it — here a
    race the driver ran in another division while the amendment stood open."""
    from services.result_submission_service import revert_abandoned_amendment

    db_path = await _make_db(tmp_path, name="amend_former_kept")
    await _open_amendment_record(db_path)
    await _amend(db_path, [_race_row(103, 1)])
    async with get_connection(db_path) as db:
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position, driver_profile_id) VALUES (?, 103, 3001, 1, 33)",
            (other.lastrowid,),
        )
        await db.commit()

    with patch("services.standings_service.cascade_recompute_from_round", new=AsyncMock()):
        await revert_abandoned_amendment(db_path, ROUND_ID)

    assert await _former(db_path, 33) == 1


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


async def test_stage_one_publishes_nothing(tmp_path):
    """**It records; it does not post** (#345).

    Posting between the stages was wrong three ways. The reports and appeals were not yet
    reviewed, so the table carried the sanctions — and the post-race DSQ marks — of the round
    being replaced. `repost_round_results` has no deletion of its own, so the posting *added* a
    message and orphaned the original Final Results above it, with its id overwritten and no
    path left that could remove it. And an amendment reverted before it completed left that
    provisional posting standing.

    So the league keeps reading the round it raced until the amendment is actually finished.
    """
    db_path = await _make_db(tmp_path, name="amend_repost")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    stubs["repost"].assert_not_awaited()
    stubs["replay"].assert_not_awaited()


async def test_stage_one_does_not_rebuild_the_division(tmp_path):
    """**The rebuild belongs to the final approval, and happens once.**

    Rebuilding here would publish a classification whose sanctions are still the old round's,
    reposting every round of the division to do it — and then throw all of it away and do it
    again when the appeals stage is approved minutes later.
    """
    db_path = await _make_db(tmp_path, name="amend_no_early_replay")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    stubs["replay"].assert_not_awaited()
    stubs["subsequent"].assert_not_awaited()


async def test_the_standings_are_recomputed_before_the_channels_are_rebuilt(tmp_path):
    """Or the rebuild reposts the championship the amendment has just corrected.

    The cascade used to be the *fallback* for having no guild; with the whole division being
    reposted it has to run first on the ordinary path too, so the messages drawn carry the new
    figures rather than the old ones.
    """
    db_path = await _make_db(tmp_path, name="amend_recompute_first")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    stubs["cascade"].assert_awaited_once()


async def test_without_a_guild_the_standings_are_still_recomputed(tmp_path):
    """The database has to be right whether or not Discord can be told."""
    db_path = await _make_db(tmp_path, name="amend_noguild")

    stubs = await _amend(db_path, [_race_row(101, 1)], bot=_bot(guild=False))

    stubs["cascade"].assert_awaited_once()
    stubs["repost"].assert_not_awaited()


async def test_the_amendment_is_logged(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_log")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    logged = str(stubs["bot"].output_router.post_log.await_args.args[0])
    assert "AMEND_STAGE_1 | Recorded" in logged
    assert f"<@{AMENDER}>" in logged
    assert "round: 3" in logged
    # Each session named with the configuration it was scored under — the one entry that
    # records it, now that the cog's `AMEND_SUCCESS` is gone.
    assert "FEATURE_RACE=Standard" in logged
    # Says where it has reached, not that it succeeded — nothing is published yet (#345).
    assert "Nothing is published until" in logged


# ---------------------------------------------------------------------------
# The amendment says what it could not repost (#237)
#
# The third caller of the cascade, and the third to throw away what it could not post. It
# has no interaction to answer, so the log channel is the only route — as in
# `apply_penalties`.
# ---------------------------------------------------------------------------

AMEND_FAULT = "**Alpha** \u2014 the results channel <#501> no longer exists."


def _amend_log(stubs) -> str:
    return "\n".join(
        str(c.args[0]) for c in stubs["bot"].output_router.post_log.await_args_list
    )


async def test_stage_one_does_not_claim_the_amendment_succeeded(tmp_path):
    """`RESULT_AMENDED` belongs to the final stage.

    Reporting success here would tell a manager the round was amended when its reports and
    appeals are still unreviewed and nothing has been published.
    """
    db_path = await _make_db(tmp_path, name="amend_complete")

    stubs = await _amend(db_path, [_race_row(101, 1)])

    logged = _amend_log(stubs)
    assert "RESULT_AMENDED" not in logged
    assert "AMEND_STAGE_1" in logged


async def test_the_championship_is_recalculated_even_with_no_guild(tmp_path):
    """The database has to be right whether or not Discord can be reached.

    Stage one publishes nothing either way now, so the two branches differ only in the line the
    league is left with — and the recalculation happens on both.
    """
    db_path = await _make_db(tmp_path, name="amend_no_guild")

    stubs = await _amend(db_path, [_race_row(101, 1)], bot=_bot(guild=False))

    stubs["cascade"].assert_awaited()
    assert "RESULT_AMENDED" not in _amend_log(stubs)
async def _verdict_rows(db_path, table: str, column: str = "race_result_id"):
    """Every row of *table*, as (id, the result row it points at), oldest first."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT id, {column} FROM {table} ORDER BY id")
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def _add_penalty(db_path, result_id: int, *, table: str = "penalty_records",
                       column: str = "race_result_id") -> int:
    """Record one verdict against *result_id*, as the penalty wizard would have."""
    async with get_connection(db_path) as db:
        if table == "penalty_records":
            cursor = await db.execute(
                f"INSERT INTO {table} ({column}, penalty_type, time_seconds, description, "
                "justification, applied_by, applied_at) "
                "VALUES (?, 'TIME', 5, 'Contact at turn 1', 'Wholly at fault', '9001', "
                "'2026-02-02T00:00:00+00:00')",
                (result_id,),
            )
        else:
            cursor = await db.execute(
                f"INSERT INTO {table} ({column}, status, penalty_type, time_seconds, "
                "description, justification, submitted_by, submitted_at) "
                "VALUES (?, 'UPHELD', 'TIME', 3, 'Appeal upheld', 'Evidence accepted', '9002', "
                "'2026-02-03T00:00:00+00:00')",
                (result_id,),
            )
        await db.commit()
        return cursor.lastrowid


async def _race_result_id(db_path, driver: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM race_session_results WHERE driver_user_id = ?", (driver,)
        )
        return (await cursor.fetchone())[0]


async def test_a_round_carrying_a_penalty_can_be_amended_at_all(tmp_path):
    """The defect itself: the delete was refused and nothing could be amended.

    Before the fix this raised `IntegrityError: FOREIGN KEY constraint failed` and the admin was
    told the amendment had failed for an internal reason, with no route by which they could ever
    succeed.
    """
    db_path = await _make_db(tmp_path, name="amend_with_penalty")
    await _add_penalty(db_path, await _race_result_id(db_path, 101))

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    assert await _race_drivers(db_path) == [(102, 1), (101, 2)]


async def test_the_verdict_follows_its_driver_to_the_new_row(tmp_path):
    """The audit is kept, and kept against the right driver.

    The row id changes — the old one is deleted — so the test asserts the verdict points at the
    row the *same driver* now holds, which is the whole of what re-pointing means.
    """
    db_path = await _make_db(tmp_path, name="amend_verdict_follows")
    verdict_id = await _add_penalty(db_path, await _race_result_id(db_path, 101))

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    rows = await _verdict_rows(db_path, "penalty_records")
    assert rows == [(verdict_id, await _race_result_id(db_path, 101))]


async def test_the_justification_survives_the_amendment(tmp_path):
    """Re-pointing keeps the record, not merely a row of the right shape.

    The justification and the applier are the only account a league has of why a driver lost
    places; an amendment correcting a lap time has no business discarding them.
    """
    db_path = await _make_db(tmp_path, name="amend_verdict_audit")
    await _add_penalty(db_path, await _race_result_id(db_path, 101))

    await _amend(db_path, [_race_row(101, 1), _race_row(102, 2)])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT justification, applied_by, time_seconds FROM penalty_records"
        )
        row = await cursor.fetchone()
    assert row["justification"] == "Wholly at fault"
    assert row["applied_by"] == "9001"
    assert row["time_seconds"] == 5


async def test_an_appeal_verdict_is_re_pointed_too(tmp_path):
    """`appeal_records` carries the same reference and was refused by the same constraint."""
    db_path = await _make_db(tmp_path, name="amend_appeal_verdict")
    verdict_id = await _add_penalty(
        db_path, await _race_result_id(db_path, 102), table="appeal_records"
    )

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    rows = await _verdict_rows(db_path, "appeal_records")
    assert rows == [(verdict_id, await _race_result_id(db_path, 102))]


async def test_several_verdicts_on_one_driver_are_all_re_pointed(tmp_path):
    """A driver may collect more than one verdict in a session.

    The wizard stages one record per incident, not per driver, so a mapping that kept a single id
    per driver would silently drop all but the last.
    """
    db_path = await _make_db(tmp_path, name="amend_two_verdicts")
    first = await _add_penalty(db_path, await _race_result_id(db_path, 101))
    second = await _add_penalty(db_path, await _race_result_id(db_path, 101))

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    new_id = await _race_result_id(db_path, 101)
    assert await _verdict_rows(db_path, "penalty_records") == [(first, new_id), (second, new_id)]


async def test_a_qualifying_verdict_is_re_pointed_on_its_own_column(tmp_path):
    """Qualifying verdicts use `qual_result_id`, and the qualifying branch had the same defect."""
    db_path = await _make_db(tmp_path, name="amend_qual_verdict")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM qualifying_session_results WHERE driver_user_id = 101"
        )
        qual_row_id = (await cursor.fetchone())[0]
    verdict_id = await _add_penalty(db_path, qual_row_id, column="qual_result_id")

    await _amend(
        db_path,
        [{"driver_user_id": 101, "team_role_id": 3001, "position": 1,
          "outcome": "CLASSIFIED", "best_lap": "1:20.000"}],
        session_type=SessionType.FEATURE_QUALIFYING,
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM qualifying_session_results WHERE driver_user_id = 101"
        )
        new_qual_id = (await cursor.fetchone())[0]
    assert await _verdict_rows(db_path, "penalty_records", "qual_result_id") == [
        (verdict_id, new_qual_id)
    ]


async def test_amending_one_session_leaves_the_other_sessions_verdicts_alone(tmp_path):
    """The scope is the session, as the delete's is.

    A round has up to four sessions; amending the feature race must not disturb a verdict
    recorded against the feature qualifying.
    """
    db_path = await _make_db(tmp_path, name="amend_scoped_verdict")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM qualifying_session_results WHERE driver_user_id = 101"
        )
        qual_row_id = (await cursor.fetchone())[0]
    qual_verdict = await _add_penalty(db_path, qual_row_id, column="qual_result_id")

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    assert await _verdict_rows(db_path, "penalty_records", "qual_result_id") == [
        (qual_verdict, qual_row_id)
    ]


async def test_an_amendment_dropping_a_driver_who_carries_a_verdict_is_refused(tmp_path):
    """Refused by name, rather than failing on a constraint the admin cannot read.

    Deleting the verdict would discard the audit and orphaning it would leave a row nothing can
    ever find, every read of these tables being by the foreign key. Refusing is the conservative
    answer until the specification states one, and the message names the driver so the admin can
    act on it.
    """
    db_path = await _make_db(tmp_path, name="amend_orphan_refused")
    await _add_penalty(db_path, await _race_result_id(db_path, 102))

    with pytest.raises(AmendmentWouldOrphanVerdictError) as excinfo:
        await _amend(db_path, [_race_row(101, 1)])

    assert "<@102>" in str(excinfo.value)


async def test_a_refused_amendment_changes_nothing_at_all(tmp_path):
    """The whole transaction is abandoned, so the league keeps the round it raced.

    A refusal that had already deleted the driver rows would be far worse than the defect it
    replaces.
    """
    db_path = await _make_db(tmp_path, name="amend_orphan_intact")
    verdict_id = await _add_penalty(db_path, await _race_result_id(db_path, 102))
    before = await _race_result_id(db_path, 102)

    with pytest.raises(AmendmentWouldOrphanVerdictError):
        await _amend(db_path, [_race_row(101, 1)])

    assert await _race_drivers(db_path) == [(101, 1), (102, 2)]
    assert await _verdict_rows(db_path, "penalty_records") == [(verdict_id, before)]


async def test_an_amendment_with_no_verdicts_is_unaffected(tmp_path):
    """The ordinary case keeps working, so the fix cannot be read as a special path."""
    db_path = await _make_db(tmp_path, name="amend_no_verdicts")

    await _amend(db_path, [_race_row(102, 1), _race_row(101, 2)])

    assert await _race_drivers(db_path) == [(102, 1), (101, 2)]
    assert await _verdict_rows(db_path, "penalty_records") == []


async def test_a_verdict_follows_a_driver_who_has_changed_account(tmp_path):
    """**Both sides are read under the driver's current account** (#243, #345).

    A pasted classification is normalised onto the current account before anything is stored,
    while the row a verdict points at holds whichever account the driver raced under. Comparing
    them raw made the session permanently un-amendable the moment somebody changed account: the
    amendment was refused, and the refusal named a driver the classification already carried.
    """
    db_path = await _make_db(tmp_path, name="amend_account_change")
    # Driver 101 races on under a new account, 201. The schema's own triggers keep
    # `driver_accounts`, so the change of account is the one write needed.
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET discord_user_id = '201' WHERE id = 31")
        await db.commit()
    verdict_id = await _add_penalty(db_path, await _race_result_id(db_path, 101))

    # The paste names them by the account they use now, as the validator leaves it.
    await _amend(db_path, [_race_row(201, 1), _race_row(102, 2)])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM race_session_results WHERE driver_user_id = 201"
        )
        new_row_id = (await cursor.fetchone())[0]
    assert await _verdict_rows(db_path, "penalty_records") == [(verdict_id, new_row_id)]



# ---------------------------------------------------------------------------
# Several sessions in one amendment (#345, decided 2026-09-21)
# ---------------------------------------------------------------------------


def _quali_row(driver: int, position: int, best_lap: str = "1:19.000") -> dict:
    return {"driver_user_id": driver, "team_role_id": 3001, "position": position,
            "best_lap": best_lap}


async def _quali_drivers(db_path) -> list[tuple[int, str]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, best_lap FROM qualifying_session_results "
            "ORDER BY finishing_position"
        )
        return [tuple(r) for r in await cursor.fetchall()]


async def test_several_sessions_are_written_in_one_amendment(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_two_sessions")

    stubs = await _amend(db_path, None, sessions=[
        AmendedSession(SessionType.FEATURE_QUALIFYING,
                       [_quali_row(102, 1), _quali_row(101, 2, "1:19.500")], "Standard"),
        AmendedSession(SessionType.FEATURE_RACE,
                       [_race_row(102, 1), _race_row(101, 2)], "Standard"),
    ])

    assert await _quali_drivers(db_path) == [(102, "1:19.000"), (101, "1:19.500")]
    assert await _race_drivers(db_path) == [(102, 1), (101, 2)]
    # Points once per session, the standings once for the lot.
    assert stubs["apply_points"].await_count == 2
    stubs["cascade"].assert_awaited_once()
    assert "sessions: FEATURE_QUALIFYING=Standard, FEATURE_RACE=Standard" in _amend_log(stubs)


async def test_a_refusal_in_one_session_writes_none_of_them(tmp_path):
    """**One transaction for every session.** The round is never left holding some sessions
    corrected and others not — which a refusal part-way through would otherwise do."""
    db_path = await _make_db(tmp_path, name="amend_two_refused")
    await _add_penalty(db_path, await _race_result_id(db_path, 102))

    with pytest.raises(AmendmentWouldOrphanVerdictError):
        await _amend(db_path, None, sessions=[
            AmendedSession(SessionType.FEATURE_QUALIFYING, [_quali_row(102, 1)], "Standard"),
            # Driver 102 carries a verdict in the race and is left out of it.
            AmendedSession(SessionType.FEATURE_RACE, [_race_row(101, 1)], "Standard"),
        ])

    assert await _quali_drivers(db_path) == [(101, None)]
    assert await _race_drivers(db_path) == [(101, 1), (102, 2)]
