"""What survives rebuilding a season's setup snapshot.

Issue #208. `save_pending_snapshot` is how every `/round add`, `/division add` and `/round amend`
during setup persists the season being built — and it does so by **dropping every division,
team, seat and round and re-inserting them with new row ids**. The season row itself is kept
(issue #220). Anything hanging off the old ids is lost unless it is saved before the teardown and restored after it, keyed by division *name*
rather than by id.

The method's docstring carries an audited list of what that covers, dated 2026-09-04, and ends:

    A new per-division or per-season setting added anywhere in the bot belongs in the list
    above, or it will be silently destroyed by the next `/round add`.

**This file is that list, as tests.** One per item, so a setting added to the bot and not to the
carry-across fails here rather than in a league's season — which is the only place it would
otherwise show up, several commands later, with nothing to connect the loss to the command that
caused it.

Three things are deliberately *not* carried, and each is harmless for a stated reason:
`lineup_message_id` and `calendar_message_id` (nothing is posted for a season still in setup, so
they are null), `signup_division_config` (recreated with `INSERT OR IGNORE` whenever next
wanted), and `season_amendment_state` (an amendment runs against an approved season, which this
never rewrites). They are not tested for survival because they are not meant to survive.

**The season number is preserved across a rebuild**, not recomputed — a season being built has
already been given its number, and recomputing it every time a round was added would let it
drift as other seasons were archived alongside.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.round import RoundFormat  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 12508
START = date(2026, 1, 1)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "snapshot.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _division(name: str = "Division 1", *, rounds: int = 1, tier: int = 1) -> dict:
    return {
        "name": name,
        "role_id": 5000 + tier,
        "channel_id": 6000 + tier,
        "tier": tier,
        "rounds": [
            {
                "round_number": n,
                "format": RoundFormat.NORMAL,
                "track_name": "Silverstone Circuit",
                "scheduled_at": datetime.now(timezone.utc) + timedelta(days=7 * n),
            }
            for n in range(1, rounds + 1)
        ],
    }


async def _snapshot(service, *, existing: int = 0, divisions=None) -> tuple[int, int]:
    return await service.save_pending_snapshot(
        START,
        existing,
        divisions if divisions is not None else [_division()],
    )


async def _division_id(db_path: str, season_id: int, name: str = "Division 1") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ? AND name = ?", (season_id, name)
        )
        return (await cursor.fetchone())["id"]


async def _row(db_path: str, table: str, where: str, params: tuple):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT * FROM {table} WHERE {where}", params  # noqa: S608
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# The rebuild itself
# ---------------------------------------------------------------------------


async def test_a_first_snapshot_creates_the_season(tmp_path):
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)

    season_id, number = await _snapshot(service)

    assert season_id > 0
    assert number == 1
    assert await _division_id(db_path, season_id) > 0


async def test_a_rebuild_keeps_the_season_row(tmp_path):
    """One live season per server, and it keeps its id across a rebuild (issue #220).

    A season in setup is referred to by more than its divisions: its lifecycle stage, and
    from the Signups stage on the signups made to it. A rebuild that gave the season a new
    id would orphan them, so only what hangs beneath the season row is rebuilt.
    """
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET stage = 'SIGNUPS' WHERE id = ?", (first,))
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM seasons"
        )
        assert (await cursor.fetchone())["n"] == 1
    assert second == first
    assert (await service.get_stage(second)).value == "SIGNUPS"


async def test_the_season_number_survives_a_rebuild(tmp_path):
    """A season being built has already been given its number. Recomputing it on every
    `/round add` would let it drift as other seasons were archived alongside."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (90, 1, '2025-01-01', 'COMPLETED')"
        )
        await db.commit()
    service = SeasonService(db_path)

    first, first_number = await _snapshot(service)
    _, second_number = await _snapshot(service, existing=first)

    assert first_number == 2  # one persisted season already
    assert second_number == 2


async def test_a_first_snapshot_numbers_past_the_archived_seasons(tmp_path):
    """Persisted seasons have already used their number, so the next is one higher than
    the tally — a new season reusing a number would collide with a league's history."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        for season_id, status in ((90, "COMPLETED"), (91, "CANCELLED")):
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status) "
                "VALUES (?, ?, '2025-01-01', ?)",
                (season_id, season_id - 89, status),
            )
        await db.commit()

    _, number = await _snapshot(SeasonService(db_path))

    assert number == 3


async def test_the_rounds_are_rebuilt_from_the_pending_config(tmp_path):
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)

    season_id, _ = await _snapshot(service, divisions=[_division(rounds=3)])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM rounds WHERE division_id = ?",
            (await _division_id(db_path, season_id),),
        )
        assert (await cursor.fetchone())["n"] == 3


async def test_a_rebuild_does_not_duplicate_the_rounds(tmp_path):
    """Every round is dropped and re-inserted; a teardown that missed them would leave a
    division with two of everything after the second `/round add`."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service, divisions=[_division(rounds=2)])

    second, _ = await _snapshot(service, existing=first, divisions=[_division(rounds=2)])

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM rounds")
        assert (await cursor.fetchone())["n"] == 2


# ---------------------------------------------------------------------------
# The audited carry-across list, one test per item
# ---------------------------------------------------------------------------


async def test_the_lineup_and_calendar_channels_survive(tmp_path):
    """They live directly on the divisions row, which the rebuild drops."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    div_id = await _division_id(db_path, first)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = 7001, calendar_channel_id = 7002 "
            "WHERE id = ?",
            (div_id,),
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    row = await _row(db_path, "divisions", "id = ?", (await _division_id(db_path, second),))
    assert row["lineup_channel_id"] == 7001
    assert row["calendar_channel_id"] == 7002


async def test_the_results_standings_and_penalty_channels_survive(tmp_path):
    """`division_results_config` is keyed on the division id the rebuild throws away."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id, penalty_channel_id) "
            "VALUES (?, 7101, 7102, 7103)",
            (await _division_id(db_path, first),),
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    row = await _row(
        db_path,
        "division_results_config",
        "division_id = ?",
        (await _division_id(db_path, second),),
    )
    assert row["results_channel_id"] == 7101
    assert row["standings_channel_id"] == 7102
    assert str(row["penalty_channel_id"]) == "7103"


async def test_the_reserves_in_standings_flag_survives(tmp_path):
    """A setting rather than a channel, and the one on that row most easily forgotten."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, reserves_in_standings) VALUES (?, 7101, 0)",
            (await _division_id(db_path, first),),
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    row = await _row(
        db_path,
        "division_results_config",
        "division_id = ?",
        (await _division_id(db_path, second),),
    )
    assert row["reserves_in_standings"] == 0


async def test_the_attendance_channels_survive(tmp_path):
    """`attendance_division_config` cascade-deletes with the old division row, so it is
    gone before anything else has a chance to notice."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config "
            "(division_id, rsvp_channel_id, attendance_channel_id) "
            "VALUES (?, '7201', '7202')",
            (await _division_id(db_path, first),),
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    row = await _row(
        db_path,
        "attendance_division_config",
        "division_id = ?",
        (await _division_id(db_path, second),),
    )
    assert str(row["rsvp_channel_id"]) == "7201"
    assert str(row["attendance_channel_id"]) == "7202"


async def test_the_attached_points_configurations_survive(tmp_path):
    """Season-level rather than per-division, and the one whose loss would be felt at
    approval rather than at the next round — the season would be refused for having no
    points attached, with nothing to say why."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, '100%')",
            (first,),
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ?", (second,)
        )
        assert [r["config_name"] for r in await cursor.fetchall()] == ["100%"]


async def test_a_division_s_settings_follow_its_name_not_its_id(tmp_path):
    """The ids all change, so the carry-across is keyed by name — which is what lets a
    division keep its channels through a rebuild at all."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(
        service, divisions=[_division("Premier", tier=1), _division("Challenger", tier=2)]
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = 7301 WHERE season_id = ? AND name = ?",
            (first, "Challenger"),
        )
        await db.commit()

    second, _ = await _snapshot(
        service,
        existing=first,
        divisions=[_division("Premier", tier=1), _division("Challenger", tier=2)],
    )

    premier = await _row(
        db_path, "divisions", "id = ?", (await _division_id(db_path, second, "Premier"),)
    )
    challenger = await _row(
        db_path, "divisions", "id = ?", (await _division_id(db_path, second, "Challenger"),)
    )
    assert challenger["lineup_channel_id"] == 7301
    assert premier["lineup_channel_id"] is None


async def test_a_division_renamed_loses_its_settings(tmp_path):
    """The consequence of keying by name, stated so it is a known cost rather than a
    surprise: a rename is indistinguishable from a removal and an addition."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service, divisions=[_division("Premier")])
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = 7401 WHERE season_id = ?", (first,)
        )
        await db.commit()

    second, _ = await _snapshot(service, existing=first, divisions=[_division("Renamed")])

    row = await _row(
        db_path, "divisions", "id = ?", (await _division_id(db_path, second, "Renamed"),)
    )
    assert row["lineup_channel_id"] is None


# ---------------------------------------------------------------------------
# Seated drivers
# ---------------------------------------------------------------------------


async def test_seated_drivers_are_held_for_restoration(tmp_path):
    """The teams they sat in do not exist yet — the caller re-seeds them from the server
    defaults once this returns — so the seats are held and replaced by
    `restore_driver_seats` rather than restored inline."""
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)
    div_id = await _division_id(db_path, first)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (1, '4242', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 2, 0)",
            (div_id,),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (100, 10, 1, 1)"
        )
        await db.commit()

    await _snapshot(service, existing=first)

    held = service._pending_driver_seats
    assert "Division 1" in held
    assert held["Division 1"][0]["team_name"] == "Alpha"
    assert held["Division 1"][0]["seat_number"] == 1


async def test_a_rebuild_with_nobody_seated_holds_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await _snapshot(service)

    await _snapshot(service, existing=first)

    assert service._pending_driver_seats == {}


async def test_a_first_snapshot_begins_in_the_stage_it_is_given(tmp_path):
    """`/season setup` begins a season in Configuration (issue #220)."""
    from models.season import SeasonStage

    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)

    season_id, _ = await service.save_pending_snapshot(
        START, 0, [], initial_stage=SeasonStage.CONFIGURATION
    )

    assert await service.get_stage(season_id) is SeasonStage.CONFIGURATION


async def test_a_rebuild_ignores_the_initial_stage(tmp_path):
    from models.season import SeasonStage

    db_path = await _make_db(tmp_path)
    service = SeasonService(db_path)
    first, _ = await service.save_pending_snapshot(
        START, 0, [], initial_stage=SeasonStage.CONFIGURATION
    )

    await service.save_pending_snapshot(
        START, first, [_division()], initial_stage=SeasonStage.PLACEMENTS
    )

    assert await service.get_stage(first) is SeasonStage.CONFIGURATION
