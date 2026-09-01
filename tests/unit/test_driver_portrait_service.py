"""Unit tests for the driver portrait service.

Nothing here touches Discord or the network: a Member is a MagicMock carrying the three
attributes the service reads (`id`, `avatar`/`guild_avatar`, `display_avatar`), and
`display_avatar.read` is an AsyncMock returning bytes.
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.driver_portrait_service import (  # noqa: E402
    has_own_avatar,
    portrait_path,
    refresh_portraits,
    wrap_png,
)

SERVER_ID = 4242
NOW = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\nFAKEBYTES"

_MIGRATION = os.path.join(
    os.path.dirname(__file__), "..", "..", "src", "db", "migrations",
    "047_driver_portraits.sql",
)


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "portraits.db")
    async with aiosqlite.connect(path) as db:
        # Only the portrait table is needed; the ALTER TABLE half of 047 wants image_config,
        # which this service never reads.
        await db.execute(
            "CREATE TABLE driver_portraits ("
            " server_id INTEGER NOT NULL, discord_user_id TEXT NOT NULL,"
            " avatar_key TEXT NOT NULL, fetched_at TEXT NOT NULL,"
            " PRIMARY KEY (server_id, discord_user_id))"
        )
        await db.commit()
    return path


@pytest.fixture
def directory(tmp_path):
    d = tmp_path / "drivers"
    d.mkdir()
    return d


def _member(user_id: int, key: str = "hash1", *, generated: bool = False, data=PNG):
    member = MagicMock()
    member.id = user_id
    member.avatar = None if generated else MagicMock()
    member.guild_avatar = None
    asset = MagicMock()
    asset.key = key
    asset.with_format.return_value = asset
    asset.with_size.return_value = asset
    asset.read = AsyncMock(return_value=data)
    member.display_avatar = asset
    return member


async def _rows(db_path):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT discord_user_id, avatar_key, fetched_at FROM driver_portraits"
        )
        return {r["discord_user_id"]: (r["avatar_key"], r["fetched_at"]) for r in await cursor.fetchall()}


# ── The wrapper ───────────────────────────────────────────────────────────


def test_the_wrapper_is_a_square_svg_carrying_the_png():
    svg = wrap_png(PNG)

    assert svg.startswith("<svg")
    assert 'viewBox="0 0 128 128"' in svg
    assert base64.b64encode(PNG).decode() in svg
    # Both spellings, as svg_fill._set_href writes for every other asset.
    assert 'xlink:href="data:image/png;base64,' in svg
    assert ' href="data:image/png;base64,' in svg
    # The authoring rules forbid these even in a file the bot generates.
    assert "clipPath" not in svg and "filter" not in svg


def test_the_portrait_is_named_as_the_resolver_would_look_for_it():
    from utils.asset_resolver import filename_for

    assert portrait_path("/tmp/x", "12345").name == filename_for("12345") == "12345.svg"


# ── Which members are considered at all ───────────────────────────────────


def test_a_generated_avatar_is_not_a_portrait():
    assert has_own_avatar(_member(1)) is True
    assert has_own_avatar(_member(1, generated=True)) is False


def test_a_server_avatar_counts_even_where_the_account_has_none():
    member = _member(1, generated=True)
    member.guild_avatar = MagicMock()
    assert has_own_avatar(member) is True


# ── Fetching, and not fetching ────────────────────────────────────────────


async def test_a_first_fetch_writes_the_file_and_the_row(db_path, directory):
    member = _member(7, "abc")

    written = await refresh_portraits(db_path, SERVER_ID, [member], directory, now=NOW)

    assert written == 1
    assert (directory / "7.svg").is_file()
    assert base64.b64encode(PNG).decode() in (directory / "7.svg").read_text()
    assert await _rows(db_path) == {"7": ("abc", NOW.isoformat())}


async def test_an_unchanged_hash_downloads_nothing(db_path, directory):
    member = _member(7, "abc")
    await refresh_portraits(db_path, SERVER_ID, [member], directory, now=NOW)
    member.display_avatar.read.reset_mock()

    written = await refresh_portraits(db_path, SERVER_ID, [member], directory, now=NOW)

    assert written == 0
    member.display_avatar.read.assert_not_awaited()


async def test_a_changed_hash_refetches(db_path, directory):
    await refresh_portraits(db_path, SERVER_ID, [_member(7, "abc")], directory, now=NOW)

    later = _member(7, "def", data=b"NEWBYTES")
    written = await refresh_portraits(db_path, SERVER_ID, [later], directory, now=NOW)

    assert written == 1
    assert base64.b64encode(b"NEWBYTES").decode() in (directory / "7.svg").read_text()
    assert (await _rows(db_path))["7"][0] == "def"


async def test_a_file_the_league_supplied_is_never_touched(db_path, directory):
    # No row for it, so it is not ours: neither overwritten nor fetched over.
    (directory / "7.svg").write_text("<svg>the league's own</svg>")
    member = _member(7, "abc")

    written = await refresh_portraits(db_path, SERVER_ID, [member], directory, now=NOW)

    assert written == 0
    member.display_avatar.read.assert_not_awaited()
    assert (directory / "7.svg").read_text() == "<svg>the league's own</svg>"
    assert await _rows(db_path) == {}


async def test_a_generated_avatar_is_skipped_and_an_owned_file_removed(db_path, directory):
    await refresh_portraits(db_path, SERVER_ID, [_member(7, "abc")], directory, now=NOW)
    assert (directory / "7.svg").is_file()

    # The driver removes their avatar: the seat must revert to the class fallback.
    written = await refresh_portraits(
        db_path, SERVER_ID, [_member(7, generated=True)], directory, now=NOW
    )

    assert written == 0
    assert not (directory / "7.svg").exists()
    assert await _rows(db_path) == {}


async def test_a_generated_avatar_does_not_remove_a_file_the_league_supplied(db_path, directory):
    (directory / "7.svg").write_text("<svg>the league's own</svg>")

    await refresh_portraits(
        db_path, SERVER_ID, [_member(7, generated=True)], directory, now=NOW
    )

    assert (directory / "7.svg").read_text() == "<svg>the league's own</svg>"


async def test_a_missing_file_is_refetched_even_where_the_hash_matches(db_path, directory):
    await refresh_portraits(db_path, SERVER_ID, [_member(7, "abc")], directory, now=NOW)
    (directory / "7.svg").unlink()

    written = await refresh_portraits(
        db_path, SERVER_ID, [_member(7, "abc")], directory, now=NOW
    )

    assert written == 1
    assert (directory / "7.svg").is_file()


# ── Failure never reaches the render ──────────────────────────────────────


async def test_an_http_error_abandons_the_rest_of_the_batch(db_path, directory):
    """A 429 gets no backoff from discord.py, so the batch stops rather than hammering.

    Pinned deliberately: this is the kind of defensive branch that reads as redundant and
    gets tuned away. See the module docstring for why `get_from_cdn` gives us nothing.
    """
    response = MagicMock()
    response.status = 429
    failing = _member(1, "a")
    failing.display_avatar.read = AsyncMock(
        side_effect=discord.HTTPException(response, "rate limited")
    )
    others = [_member(n, "b") for n in (2, 3, 4)]

    written = await refresh_portraits(
        db_path, SERVER_ID, [failing] + others, directory,
        concurrency=1, budget_seconds=None, now=NOW,
    )

    assert written == 0
    assert await _rows(db_path) == {}
    for member in others:
        member.display_avatar.read.assert_not_awaited()


async def test_one_unreadable_avatar_does_not_stop_the_others(db_path, directory):
    # Anything that is not an HTTPException is that one driver's problem alone.
    failing = _member(1, "a")
    failing.display_avatar.read = AsyncMock(side_effect=ValueError("corrupt"))
    ok = _member(2, "b")

    written = await refresh_portraits(
        db_path, SERVER_ID, [failing, ok], directory, budget_seconds=None, now=NOW
    )

    assert written == 1
    assert not (directory / "1.svg").exists()
    assert (directory / "2.svg").is_file()


async def test_the_budget_returns_without_raising_and_writes_no_row(db_path, directory):
    slow = _member(1, "a")

    async def never():
        await asyncio.sleep(30)

    slow.display_avatar.read = AsyncMock(side_effect=never)

    written = await refresh_portraits(
        db_path, SERVER_ID, [slow], directory, budget_seconds=0.05, now=NOW
    )

    assert written == 0
    assert await _rows(db_path) == {}
    assert not (directory / "1.svg").exists()


async def test_no_partial_file_is_left_behind(db_path, directory):
    await refresh_portraits(db_path, SERVER_ID, [_member(7, "abc")], directory, now=NOW)

    assert sorted(p.name for p in directory.iterdir()) == ["7.svg"]
