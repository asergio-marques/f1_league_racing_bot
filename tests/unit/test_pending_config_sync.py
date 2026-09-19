"""A season-setup command amends the season in place, and writes only what changed.

Issue #147. Every `/round add`, `/round amend`, round import and `/division add` used to delete
the whole SETUP season beneath its row — divisions, teams, seats, rounds, assignments — and
re-insert it with new ids, carrying across by hand whatever it knew to save. Since issue #220
divisions exist only in Placements, so that rebuild ran over the grid being placed.

`SeasonService.sync_pending_config` replaced it. These pin the properties that make it safe:
nothing beneath the season changes id, an unchanged config writes nothing, a division already in
the DB is never written, and the rounds of a division are reconciled one for one.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.round import RoundFormat  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

START = date(2026, 1, 1)
BASE = datetime(2026, 3, 1, 18, 0, tzinfo=timezone.utc)

#: Every table a setup command could write beneath a season.
_WATCHED = (
    "seasons",
    "divisions",
    "rounds",
    "team_instances",
    "team_seats",
    "driver_season_assignments",
    "division_results_config",
    "attendance_division_config",
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "sync.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 900, 100, 101)"
        )
        await db.commit()
    return path


def _round(n: int, *, days: int | None = None, track: str = "Silverstone Circuit",
           fmt: RoundFormat = RoundFormat.NORMAL) -> dict:
    return {
        "round_number": n,
        "format": fmt,
        "track_name": track,
        "scheduled_at": BASE + timedelta(days=7 * (n if days is None else days)),
    }


def _division(name: str, tier: int, rounds: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "role_id": 5000 + tier,
        "channel_id": None,
        "tier": tier,
        "rounds": rounds if rounds is not None else [_round(n) for n in (1, 2, 3)],
    }


async def _sync(svc, season_id: int, divisions: list[dict], **kwargs):
    return await svc.sync_pending_config(START, season_id, divisions, **kwargs)


async def _seed_teams_and_seat(db_path: str, division_ids: list[int]) -> None:
    """One two-seat team per division and a real driver in seat 1, as placements leave it."""
    async with get_connection(db_path) as db:
        for i, div_id in enumerate(division_ids):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Redline', 2, 0)",
                (div_id,),
            )
            team_id = cursor.lastrowid
            seat_ids = []
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
                seat_ids.append(cursor.lastrowid)
            cursor = await db.execute(
                "INSERT INTO driver_profiles "
                "(discord_user_id, current_state, former_driver, is_test_driver) "
                "VALUES (?, 'ASSIGNED', 0, 0)",
                (str(100000000000000000 + i),),
            )
            profile_id = cursor.lastrowid
            await db.execute(
                "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
                (profile_id, seat_ids[0]),
            )
            cursor = await db.execute(
                "SELECT season_id FROM divisions WHERE id = ?", (div_id,)
            )
            season_id = (await cursor.fetchone())["season_id"]
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, ?)",
                (profile_id, season_id, div_id, seat_ids[0]),
            )
        await db.commit()


async def _dump(db_path: str, table: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT * FROM {table} ORDER BY rowid")  # noqa: S608
        return [dict(r) for r in await cursor.fetchall()]


async def _count_writes(db_path: str) -> None:
    """Install triggers that count every insert, update and delete on the watched tables."""
    async with get_connection(db_path) as db:
        await db.execute("CREATE TABLE write_log (tbl TEXT, op TEXT)")
        for table in _WATCHED:
            for op in ("INSERT", "UPDATE", "DELETE"):
                await db.execute(
                    f"CREATE TRIGGER count_{table}_{op.lower()} AFTER {op} ON {table} "
                    f"BEGIN INSERT INTO write_log VALUES ('{table}', '{op}'); END"
                )
        await db.commit()


async def _writes(db_path: str) -> list[tuple[str, str]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT tbl, op FROM write_log ORDER BY rowid")
        return [(r["tbl"], r["op"]) for r in await cursor.fetchall()]


async def _rounds(db_path: str, division_id: int) -> list[tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT round_number, format, track_name, scheduled_at FROM rounds "
            "WHERE division_id = ? ORDER BY round_number",
            (division_id,),
        )
        return [tuple(r) for r in await cursor.fetchall()]


async def _two_division_season(db_path: str):
    svc = SeasonService(db_path)
    divisions = [_division("Pro", 1), _division("Am", 2)]
    season_id, _, new_ids = await _sync(svc, 0, divisions)
    await _seed_teams_and_seat(db_path, new_ids)
    return svc, season_id, divisions, new_ids


# ---------------------------------------------------------------------------
# Creating the season
# ---------------------------------------------------------------------------


async def test_a_first_sync_creates_the_season_and_returns_every_division(db_path):
    svc = SeasonService(db_path)
    season_id, number, new_ids = await _sync(
        svc, 0, [_division("Pro", 1), _division("Am", 2)],
        initial_stage=SeasonStage.CONFIGURATION,
    )
    seasons = await _dump(db_path, "seasons")
    assert [s["id"] for s in seasons] == [season_id]
    assert seasons[0]["stage"] == "CONFIGURATION"
    assert number == 1
    assert len(new_ids) == 2


async def test_a_season_with_no_divisions_is_just_its_row(db_path):
    """`/season setup` syncs an empty configuration: the season row and nothing else."""
    svc = SeasonService(db_path)
    season_id, _, new_ids = await _sync(svc, 0, [])
    assert new_ids == []
    assert await _dump(db_path, "divisions") == []


# ---------------------------------------------------------------------------
# Nothing beneath the season changes id
# ---------------------------------------------------------------------------


async def test_a_round_add_keeps_every_division_team_seat_and_assignment(db_path):
    svc, season_id, divisions, _ = await _two_division_season(db_path)
    before = {t: await _dump(db_path, t) for t in
              ("divisions", "team_instances", "team_seats", "driver_season_assignments")}

    divisions[0]["rounds"].append(_round(4))
    _, _, new_ids = await _sync(svc, season_id, divisions)

    assert new_ids == []
    for table, rows in before.items():
        assert await _dump(db_path, table) == rows, f"{table} was rewritten"


async def test_rounds_of_an_untouched_division_keep_their_ids(db_path):
    svc, season_id, divisions, (pro_id, am_id) = await _two_division_season(db_path)
    am_before = [r for r in await _dump(db_path, "rounds") if r["division_id"] == am_id]

    divisions[0]["rounds"].append(_round(4))
    await _sync(svc, season_id, divisions)

    am_after = [r for r in await _dump(db_path, "rounds") if r["division_id"] == am_id]
    assert am_after == am_before


async def test_a_placed_driver_is_untouched_by_a_setup_command(db_path):
    svc, season_id, divisions, _ = await _two_division_season(db_path)
    await _count_writes(db_path)

    divisions[1]["rounds"].append(_round(4))
    await _sync(svc, season_id, divisions)

    touched = {table for table, _ in await _writes(db_path)}
    assert touched == {"rounds"}


# ---------------------------------------------------------------------------
# Only the difference is written
# ---------------------------------------------------------------------------


async def test_an_unchanged_config_writes_nothing(db_path):
    svc, season_id, divisions, _ = await _two_division_season(db_path)
    await _count_writes(db_path)

    await _sync(svc, season_id, divisions)

    assert await _writes(db_path) == []


async def test_a_round_added_at_the_end_is_one_insert(db_path):
    svc, season_id, divisions, _ = await _two_division_season(db_path)
    await _count_writes(db_path)

    divisions[0]["rounds"].append(_round(4))
    await _sync(svc, season_id, divisions)

    assert await _writes(db_path) == [("rounds", "INSERT")]


async def test_the_writes_do_not_grow_with_the_number_of_divisions(db_path):
    """The cost the issue measured was multiplied by the division count. No longer."""
    svc = SeasonService(db_path)
    divisions = [_division(f"Tier {t}", t) for t in range(1, 7)]
    season_id, _, new_ids = await _sync(svc, 0, divisions)
    await _seed_teams_and_seat(db_path, new_ids)
    await _count_writes(db_path)

    divisions[5]["rounds"].append(_round(4))
    await _sync(svc, season_id, divisions)

    assert await _writes(db_path) == [("rounds", "INSERT")]


# ---------------------------------------------------------------------------
# Divisions
# ---------------------------------------------------------------------------


async def test_a_new_division_is_the_only_one_returned_for_seeding(db_path):
    svc, season_id, divisions, old_ids = await _two_division_season(db_path)

    divisions.append(_division("Rookie", 3))
    _, _, new_ids = await _sync(svc, season_id, divisions)

    assert len(new_ids) == 1
    assert new_ids[0] not in old_ids
    rows = {d["id"]: d["name"] for d in await _dump(db_path, "divisions")}
    assert rows[new_ids[0]] == "Rookie"


async def test_a_division_whose_seeding_was_interrupted_is_returned_again(db_path):
    """Seeding commits after the sync. A bot stopped between the two leaves a division with
    no teams, and the next setup command must seed it — the rebuild did, by re-seeding all."""
    svc = SeasonService(db_path)
    divisions = [_division("Pro", 1), _division("Am", 2)]
    season_id, _, (pro_id, am_id) = await _sync(svc, 0, divisions)
    await _seed_teams_and_seat(db_path, [pro_id])  # Am's seeding never landed

    divisions[0]["rounds"].append(_round(4))
    _, _, unseeded = await _sync(svc, season_id, divisions)

    assert unseeded == [am_id]


async def test_a_division_missing_from_the_config_is_left_alone(db_path):
    svc, season_id, divisions, (pro_id, am_id) = await _two_division_season(db_path)
    before = await _dump(db_path, "divisions")
    rounds_before = await _rounds(db_path, am_id)

    await _sync(svc, season_id, [divisions[0]])

    assert await _dump(db_path, "divisions") == before
    assert await _rounds(db_path, am_id) == rounds_before


async def test_a_division_already_in_the_db_is_never_written(db_path):
    """Its fields are owned by the commands that write them straight to the DB.

    `/division channel weather` sets the forecast channel without touching the pending
    config. The rebuild re-inserted the division from that config, so the channel was lost to
    the next `/round add`. The sync leaves the row as the DB holds it, whatever the config says.
    """
    svc, season_id, divisions, (pro_id, _) = await _two_division_season(db_path)
    await svc.set_division_forecast_channel(pro_id, 4242)

    divisions[0]["rounds"].append(_round(4))
    divisions[0]["channel_id"] = None
    divisions[0]["role_id"] = 1
    await _sync(svc, season_id, divisions)

    row = next(d for d in await _dump(db_path, "divisions") if d["id"] == pro_id)
    assert row["forecast_channel_id"] == 4242
    assert row["mention_role_id"] == 5001


# ---------------------------------------------------------------------------
# Rounds
# ---------------------------------------------------------------------------


async def test_a_round_added_in_the_middle_renumbers_only_the_rounds_after_it(db_path):
    svc = SeasonService(db_path)
    rounds = [_round(1, days=1), _round(2, days=2), _round(3, days=4), _round(4, days=5)]
    season_id, _, (div_id,) = await _sync(svc, 0, [_division("Pro", 1, rounds)])
    await _count_writes(db_path)

    rounds.insert(2, _round(3, days=3, track="Monza"))
    for n, r in enumerate(rounds, start=1):
        r["round_number"] = n
    await _sync(svc, season_id, [_division("Pro", 1, rounds)])

    assert sorted(await _writes(db_path)) == sorted(
        [("rounds", "INSERT"), ("rounds", "UPDATE"), ("rounds", "UPDATE")]
    )
    assert [r[0] for r in await _rounds(db_path, div_id)] == [1, 2, 3, 4, 5]
    assert (await _rounds(db_path, div_id))[2][2] == "Monza"


async def test_an_amended_round_is_replaced_and_the_rest_keep_their_ids(db_path):
    svc = SeasonService(db_path)
    rounds = [_round(1), _round(2), _round(3)]
    season_id, _, (div_id,) = await _sync(svc, 0, [_division("Pro", 1, rounds)])
    ids_before = {r["round_number"]: r["id"] for r in await _dump(db_path, "rounds")}

    rounds[1] = {**rounds[1], "track_name": "Monza"}
    await _sync(svc, season_id, [_division("Pro", 1, rounds)])

    after = {r["round_number"]: r for r in await _dump(db_path, "rounds")}
    assert after[1]["id"] == ids_before[1]
    assert after[3]["id"] == ids_before[3]
    assert after[2]["track_name"] == "Monza"
    assert len(after) == 3


async def test_a_round_dropped_from_the_config_is_deleted(db_path):
    svc = SeasonService(db_path)
    rounds = [_round(1), _round(2), _round(3)]
    season_id, _, (div_id,) = await _sync(svc, 0, [_division("Pro", 1, rounds)])

    kept = [rounds[0], {**rounds[2], "round_number": 2}]
    await _sync(svc, season_id, [_division("Pro", 1, kept)])

    held = await _rounds(db_path, div_id)
    assert [r[0] for r in held] == [1, 2]
    assert held[1][3] == rounds[2]["scheduled_at"].isoformat()


async def test_a_mystery_round_with_no_track_is_matched(db_path):
    svc = SeasonService(db_path)
    rounds = [{**_round(1), "format": RoundFormat.MYSTERY, "track_name": None}]
    season_id, _, _ = await _sync(svc, 0, [_division("Pro", 1, rounds)])
    await _count_writes(db_path)

    await _sync(svc, season_id, [_division("Pro", 1, rounds)])

    assert await _writes(db_path) == []


async def test_identical_rounds_are_matched_one_for_one(db_path):
    svc = SeasonService(db_path)
    twin = _round(1)
    season_id, _, (div_id,) = await _sync(svc, 0, [_division("Pro", 1, [twin])])

    await _sync(svc, season_id, [_division("Pro", 1, [twin, {**twin, "round_number": 2}])])

    assert len(await _rounds(db_path, div_id)) == 2


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_sync_refuses_a_season_not_in_setup(db_path):
    svc = SeasonService(db_path)
    season_id, _, _ = await _sync(svc, 0, [], initial_stage=SeasonStage.CONFIGURATION)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING' WHERE id = ?",
            (season_id,),
        )
        await db.commit()

    with pytest.raises(ValueError, match="not in setup"):
        await _sync(svc, season_id, [_division("Pro", 1)])
    assert await _dump(db_path, "divisions") == []


async def test_sync_refuses_a_season_that_does_not_exist(db_path):
    with pytest.raises(ValueError, match="does not exist"):
        await _sync(SeasonService(db_path), 999, [_division("Pro", 1)])


# ---------------------------------------------------------------------------
# A new season's number
# ---------------------------------------------------------------------------


async def test_a_new_season_on_an_empty_league_is_number_one(db_path):
    _, season_number, _ = await _sync(SeasonService(db_path), 0, [])

    assert season_number == 1


async def test_a_new_season_follows_the_latest_number_across_a_gap(db_path):
    """Issue #153. The number is the latest one plus one, not the count plus one. With a gap
    in the history a count falls below the highest number in use, and the next season would
    be issued a number another already holds."""
    async with get_connection(db_path) as db:
        for number in (1, 3):
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number) "
                "VALUES ('2025-01-01', 'COMPLETED', ?)",
                (number,),
            )
        await db.commit()

    _, season_number, _ = await _sync(SeasonService(db_path), 0, [])

    assert season_number == 4


# ---------------------------------------------------------------------------
# The cog seeds teams for new divisions only
# ---------------------------------------------------------------------------


async def test_a_setup_command_seeds_teams_only_for_a_division_without_them(db_path):
    """Re-seeding every division on every command re-created teams that already existed."""
    from unittest.mock import AsyncMock, MagicMock

    from cogs.season_cog import PendingConfig, PendingDivision, SeasonCog

    async def seed(division_id):
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Redline', 2, 0)",
                (division_id,),
            )
            await db.commit()

    bot = MagicMock()
    bot.season_service = SeasonService(db_path)
    bot.team_service.seed_division_teams = AsyncMock(side_effect=seed)
    cog = SeasonCog(bot)

    pro = PendingDivision(name="Pro", role_id=1, channel_id=None, tier=1, rounds=[_round(1)])
    cfg = PendingConfig(start_date=START, divisions=[pro])
    await cog._snapshot_pending(cfg)
    assert bot.team_service.seed_division_teams.await_count == 1

    bot.team_service.seed_division_teams.reset_mock()
    pro.rounds.append(_round(2))
    await cog._snapshot_pending(cfg)
    bot.team_service.seed_division_teams.assert_not_awaited()

    cfg.divisions.append(
        PendingDivision(name="Am", role_id=2, channel_id=None, tier=2, rounds=[])
    )
    await cog._snapshot_pending(cfg)
    (call,) = bot.team_service.seed_division_teams.await_args_list
    rows = {d["name"]: d["id"] for d in await _dump(db_path, "divisions")}
    assert call.args == (rows["Am"],)
