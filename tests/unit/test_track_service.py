"""Unit tests for track_service — DB-backed track registry."""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures — in-memory aiosqlite database with tracks table
# ---------------------------------------------------------------------------

@pytest.fixture
async def db():
    """An in-memory aiosqlite DB with a seeded tracks table."""
    import aiosqlite
    async with aiosqlite.connect(":memory:") as connection:
        connection.row_factory = aiosqlite.Row
        await connection.execute(
            """
            CREATE TABLE tracks (
                id      INTEGER PRIMARY KEY NOT NULL,
                name    TEXT    NOT NULL UNIQUE,
                gp_name TEXT    NOT NULL,
                location TEXT   NOT NULL,
                country  TEXT   NOT NULL,
                mu      REAL    NOT NULL,
                sigma   REAL    NOT NULL
            )
            """
        )
        await connection.executemany(
            "INSERT INTO tracks (id, name, gp_name, location, country, mu, sigma) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (5, "Belgium", "Belgian Grand Prix", "Spa-Francorchamps", "Belgium", 0.38, 0.12),
                (3, "Australia", "Australian Grand Prix", "Melbourne", "Australia", 0.15, 0.07),
            ],
        )
        await connection.commit()
        yield connection


# ---------------------------------------------------------------------------
# get_all_tracks
# ---------------------------------------------------------------------------

class TestGetAllTracks:
    async def test_returns_rows(self, db) -> None:
        from services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        assert len(rows) == 2

    async def test_ordered_by_id(self, db) -> None:
        from services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)

    async def test_row_fields_present(self, db) -> None:
        from services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        row = rows[0]
        assert row["id"] == 3
        assert row["name"] == "Australia"
        assert row["gp_name"] == "Australian Grand Prix"
        assert row["mu"] == pytest.approx(0.15)
        assert row["sigma"] == pytest.approx(0.07)


# ---------------------------------------------------------------------------
# get_track_by_name
# ---------------------------------------------------------------------------

class TestGetTrackByName:
    async def test_found(self, db) -> None:
        from services.track_service import get_track_by_name
        row = await get_track_by_name(db, "Belgium")
        assert row is not None
        assert row["id"] == 5
        assert row["mu"] == pytest.approx(0.38)

    async def test_not_found(self, db) -> None:
        from services.track_service import get_track_by_name
        row = await get_track_by_name(db, "Unknown Circuit")
        assert row is None


