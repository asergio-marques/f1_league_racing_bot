"""Unit tests for season_points_service (T029)."""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from models.points_config import SessionType
from services import points_config_service, season_points_service
from services.season_points_service import (
    SeasonNotInSetupError,
    attach_config,
    get_season_points_view,
    validate_monotonic_ordering,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "sps_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        await db.commit()
    return path


async def _make_season(db_path: str, status: str = "SETUP") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', ?, 1)",
            (status,),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def _make_config_with_entries(db_path: str, config_name: str) -> None:
    """Create a server config with Feature Race P1=25, P2=18, P3=15."""
    await points_config_service.create_config(db_path, config_name=config_name)
    for pos, pts in [(1, 25), (2, 18), (3, 15)]:
        await points_config_service.set_session_points(
            db_path, config_name=config_name,
            session_type=SessionType.FEATURE_RACE, position=pos, points=pts,
        )


# ---------------------------------------------------------------------------
# attach_config — blocked outside SETUP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_config_blocked_outside_setup(db_path):
    season_id = await _make_season(db_path, status="ACTIVE")
    await _make_config_with_entries(db_path, "CFG")
    with pytest.raises(SeasonNotInSetupError):
        await attach_config(db_path, season_id=season_id, config_name="CFG", season_status="ACTIVE")


@pytest.mark.asyncio
async def test_attach_config_success_in_setup(db_path):
    season_id = await _make_season(db_path, status="SETUP")
    await _make_config_with_entries(db_path, "CFG")
    await attach_config(db_path, season_id=season_id, config_name="CFG", season_status="SETUP")
    names = await season_points_service.get_attached_config_names(db_path, season_id)
    assert "CFG" in names


@pytest.mark.asyncio
async def test_attach_config_rejects_a_name_not_in_the_store(db_path):
    """#132. A typo attached a phantom that only surfaced as silence at approval."""
    season_id = await _make_season(db_path, status="SETUP")
    await _make_config_with_entries(db_path, "Standard")

    with pytest.raises(points_config_service.ConfigNotFoundError):
        await attach_config(
            db_path, season_id=season_id, config_name="Standrad",
            season_status="SETUP",
        )

    assert await season_points_service.get_attached_config_names(db_path, season_id) == []


@pytest.mark.asyncio
async def test_attach_config_checks_the_season_status_before_the_name(db_path):
    """An active season is refused for being active, whatever was typed into it."""
    season_id = await _make_season(db_path, status="ACTIVE")

    with pytest.raises(SeasonNotInSetupError):
        await attach_config(
            db_path, season_id=season_id, config_name="Whatever",
            season_status="ACTIVE",
        )


# ---------------------------------------------------------------------------
# missing_attached_configs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_attached_configs_names_only_the_phantom(db_path):
    """What the approval gate and the review both report, so neither has to look itself."""
    season_id = await _make_season(db_path)
    await _make_config_with_entries(db_path, "REAL")
    await attach_config(
        db_path, season_id=season_id, config_name="REAL", season_status="SETUP"
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'PHANTOM')",
            (season_id,),
        )
        await db.commit()

    missing = await season_points_service.missing_attached_configs(
        db_path, season_id
    )

    assert missing == ["PHANTOM"]


@pytest.mark.asyncio
async def test_missing_attached_configs_is_empty_when_every_name_is_real(db_path):
    season_id = await _make_season(db_path)
    await _make_config_with_entries(db_path, "REAL")
    await attach_config(
        db_path, season_id=season_id, config_name="REAL", season_status="SETUP"
    )

    assert await season_points_service.missing_attached_configs(
        db_path, season_id
    ) == []


@pytest.mark.asyncio
async def test_missing_attached_configs_names_every_phantom_in_order(db_path):
    """One at a time would send a manager round the loop once per typo."""
    season_id = await _make_season(db_path)
    async with get_connection(db_path) as db:
        for name in ("ZULU", "ALPHA"):
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (season_id, name),
            )
        await db.commit()

    missing = await season_points_service.missing_attached_configs(
        db_path, season_id
    )

    assert missing == ["ALPHA", "ZULU"]


# ---------------------------------------------------------------------------
# validate_monotonic_ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_monotonic_valid(db_path):
    season_id = await _make_season(db_path)
    # Manually insert monotonic entries: P1=25, P2=18, P3=15
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 18), (3, 15)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'X', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert errors == []


@pytest.mark.asyncio
async def test_validate_monotonic_invalid(db_path):
    season_id = await _make_season(db_path)
    # P1=10, P2=18 — non-monotonic!
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 10), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'BAD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert len(errors) >= 1
    assert "BAD" in errors[0]


@pytest.mark.asyncio
async def test_validate_monotonic_equal_nonzero(db_path):
    season_id = await _make_season(db_path)
    # P1=25, P2=25 — equal non-zero counts as a violation
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 25)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'EQ', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert len(errors) >= 1
    assert "EQ" in errors[0]


@pytest.mark.asyncio
async def test_validate_monotonic_trailing_zeros_ok(db_path):
    season_id = await _make_season(db_path)
    # P1=0, P2=0 — equal zeros are NOT a violation
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 0), (2, 0)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'ZEROS', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    errors = await validate_monotonic_ordering(db_path, season_id)
    assert errors == []


# ---------------------------------------------------------------------------
# get_season_points_view — trailing-zero collapse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_season_points_view_trailing_zero_collapse(db_path):
    season_id = await _make_season(db_path)
    # P1=25, P2=18, P3=0, P4=0
    async with get_connection(db_path) as db:
        for pos, pts in [(1, 25), (2, 18), (3, 0), (4, 0)]:
            await db.execute(
                "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
                "VALUES (?, 'TRAIL', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()

    view = await get_season_points_view(db_path, season_id, "TRAIL")
    entries = view["FEATURE_RACE"]["entries"]
    # Should be: [("1", 25), ("2", 18), ("3+", 0)]
    labels = [label for label, _pts in entries]
    assert "1" in labels
    assert "2" in labels
    assert "3+" in labels
    # P4 should NOT appear separately since P3 was the first trailing zero
    assert "4" not in labels
    assert "4+" not in labels


# ---------------------------------------------------------------------------
# validate_attached_config_ordering
#
# The check that can speak before a season has been approved once. The tests above
# seed `season_points_entries` by hand, which is why they never noticed the gate was
# reading a table nothing had written yet (#131); these seed the configs a league
# actually builds and let the check find them for itself.
# ---------------------------------------------------------------------------


async def _make_bad_config(db_path: str, config_name: str) -> None:
    """A config a manager could build today: first place worth less than second."""
    await points_config_service.create_config(db_path, config_name=config_name)
    for pos, pts in [(1, 10), (2, 25)]:
        await points_config_service.set_session_points(
            db_path, config_name=config_name,
            session_type=SessionType.FEATURE_RACE, position=pos, points=pts,
        )


@pytest.mark.asyncio
async def test_attached_ordering_passes_a_well_built_table(db_path):
    season_id = await _make_season(db_path)
    await _make_config_with_entries(db_path, "GOOD")
    await attach_config(db_path, season_id=season_id, config_name="GOOD", season_status="SETUP")

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert errors == []


@pytest.mark.asyncio
async def test_attached_ordering_catches_a_table_the_season_has_not_copied_yet(db_path):
    """The regression. Nothing has written `season_points_entries` and the fault is found."""
    season_id = await _make_season(db_path)
    await _make_bad_config(db_path, "BAD")
    await attach_config(db_path, season_id=season_id, config_name="BAD", season_status="SETUP")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM season_points_entries WHERE season_id = ?", (season_id,)
        )
        assert (await cursor.fetchone())["n"] == 0, "the season's own copy must still be empty"

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert len(errors) == 1
    assert "BAD" in errors[0]
    assert "FEATURE_RACE" in errors[0]


@pytest.mark.asyncio
async def test_attached_ordering_ignores_a_config_the_season_never_attached(db_path):
    """A broken config sitting in the server's store is not this season's problem."""
    season_id = await _make_season(db_path)
    await _make_config_with_entries(db_path, "GOOD")
    await _make_bad_config(db_path, "BAD")
    await attach_config(db_path, season_id=season_id, config_name="GOOD", season_status="SETUP")

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert errors == []


@pytest.mark.asyncio
async def test_attached_ordering_skips_a_name_attached_but_never_created(db_path):
    """A phantom link is reported by `missing_attached_configs`, not by the ordering check.

    The link is seeded by hand because `attach_config` refuses to make one now (#132). It
    can still be reached by a database written before that fix, or by a config removed from
    under an approved season, so the ordering check must keep tolerating it — and stay
    silent, because the fault is "there is no such config", not "its table is out of order".
    """
    season_id = await _make_season(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'TYPO')",
            (season_id,),
        )
        await db.commit()

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert errors == []


@pytest.mark.asyncio
async def test_attached_ordering_judges_each_session_type_on_its_own(db_path):
    """A league scoring qualifying differently is not compared across the two tables."""
    season_id = await _make_season(db_path)
    await points_config_service.create_config(db_path, config_name="MIXED")
    for pos, pts in [(1, 25), (2, 18)]:
        await points_config_service.set_session_points(
            db_path, config_name="MIXED",
            session_type=SessionType.FEATURE_RACE, position=pos, points=pts,
        )
    for pos, pts in [(1, 1), (2, 3)]:
        await points_config_service.set_session_points(
            db_path, config_name="MIXED",
            session_type=SessionType.FEATURE_QUALIFYING, position=pos, points=pts,
        )
    await attach_config(db_path, season_id=season_id, config_name="MIXED", season_status="SETUP")

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert len(errors) == 1
    assert "FEATURE_QUALIFYING" in errors[0]


@pytest.mark.asyncio
async def test_attached_ordering_reports_every_attached_config(db_path):
    """Two broken tables are two errors, so one fix does not hide the other."""
    season_id = await _make_season(db_path)
    await _make_bad_config(db_path, "BAD-ONE")
    await _make_bad_config(db_path, "BAD-TWO")
    for name in ("BAD-ONE", "BAD-TWO"):
        await attach_config(db_path, season_id=season_id, config_name=name, season_status="SETUP")

    errors = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )

    assert len(errors) == 2
    assert any("BAD-ONE" in e for e in errors)
    assert any("BAD-TWO" in e for e in errors)


@pytest.mark.asyncio
async def test_attached_ordering_and_the_season_copy_word_a_fault_the_same_way(db_path):
    """One rule, one sentence — whichever of the two checks found it."""
    season_id = await _make_season(db_path)
    await _make_bad_config(db_path, "SAME")
    await attach_config(db_path, season_id=season_id, config_name="SAME", season_status="SETUP")
    await season_points_service.snapshot_configs_to_season(db_path, season_id)

    from_source = await season_points_service.validate_attached_config_ordering(
        db_path, season_id
    )
    from_snapshot = await validate_monotonic_ordering(db_path, season_id)

    assert from_source == from_snapshot
