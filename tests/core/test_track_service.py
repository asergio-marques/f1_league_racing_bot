"""Unit tests for track_service — DB-backed track registry."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures — the registry the baseline seeds
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    """A connection to a migrated database, whose registry is the one every league starts
    with: 1 is Albert Park Circuit, 5 Jeddah Corniche Circuit, 13 Spa-Francorchamps."""
    from leaguebot.core.db.database import get_connection, run_migrations

    path = str(tmp_path / "tracks.db")
    await run_migrations(path)
    async with get_connection(path) as connection:
        yield connection


# ---------------------------------------------------------------------------
# get_all_tracks
# ---------------------------------------------------------------------------

class TestGetAllTracks:
    async def test_returns_every_row(self, db) -> None:
        from leaguebot.core.services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        (count,) = await (await db.execute("SELECT COUNT(*) FROM tracks")).fetchone()
        assert count > 0
        assert len(rows) == count

    async def test_ordered_by_id(self, db) -> None:
        from leaguebot.core.services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        ids = [r["id"] for r in rows]
        assert ids == sorted(ids)

    async def test_row_fields_present(self, db) -> None:
        from leaguebot.core.services.track_service import get_all_tracks
        rows = await get_all_tracks(db)
        row = rows[0]
        assert row["id"] == 1
        assert row["name"] == "Albert Park Circuit"
        assert row["gp_name"] == "Australian Grand Prix"
        assert row["country"] == "Australia"
        assert row["mu"] == pytest.approx(0.1)
        assert row["sigma"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# get_track_by_name
# ---------------------------------------------------------------------------

class TestGetTrackByName:
    async def test_found(self, db) -> None:
        from leaguebot.core.services.track_service import get_track_by_name
        row = await get_track_by_name(db, "Circuit de Spa-Francorchamps")
        assert row is not None
        assert row["id"] == 13
        assert row["mu"] == pytest.approx(0.3)

    async def test_not_found(self, db) -> None:
        from leaguebot.core.services.track_service import get_track_by_name
        row = await get_track_by_name(db, "Unknown Circuit")
        assert row is None


# ---------------------------------------------------------------------------
# resolve_track_name
# ---------------------------------------------------------------------------

JEDDAH = "Jeddah Corniche Circuit"  # id 5, one digit, so the label pads it


class TestResolveTrackName:
    """The forms `/round add track:` and `/round amend track:` actually receive.

    The autocomplete offers "05 - Jeddah Corniche Circuit" as the display label and the
    name as the value, so picking a suggestion sends the name. Typing or pasting the label — or
    editing a previous command in place — sends the label, which used to be rejected
    as an unknown track even though it is exactly what the bot had just displayed.
    """

    async def test_bare_id(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "5") == JEDDAH

    async def test_zero_padded_id_as_the_label_shows_it(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "05") == JEDDAH

    async def test_canonical_name(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, JEDDAH) == JEDDAH

    async def test_name_in_any_case(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "jEDDAH cORNICHE cIRCUIT") == JEDDAH

    async def test_the_autocomplete_label_with_an_en_dash(self, db) -> None:
        """The bug: the label the autocomplete itself displays was refused."""
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, f"05 \u2013 {JEDDAH}") == JEDDAH

    async def test_the_label_with_a_hyphen_a_keyboard_produces(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, f"5 - {JEDDAH}") == JEDDAH

    async def test_the_id_wins_over_a_mismatched_name_beside_it(self, db) -> None:
        """The id is authoritative; the text after the dash was only ever displayed."""
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, f"1 \u2013 {JEDDAH}") == "Albert Park Circuit"

    async def test_surrounding_whitespace_is_ignored(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, f"  {JEDDAH}  ") == JEDDAH

    async def test_unknown_name(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "Unknown Circuit") is None

    async def test_unknown_id(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "99") is None

    async def test_empty_is_no_track(self, db) -> None:
        from leaguebot.core.services.track_service import resolve_track_name
        assert await resolve_track_name(db, "   ") is None
