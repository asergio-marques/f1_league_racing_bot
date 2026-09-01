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
from types import SimpleNamespace
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


# ── The pre-render gate ───────────────────────────────────────────────────


def _config(**overrides):
    values = {
        "use_pfp": True,
        "pfp_prerender": True,
        "driver_image_directory": "resources/league/drivers",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _gate(monkeypatch, config, directory, members=None):
    """Run refresh_before_render with refresh_portraits captured rather than performed."""
    from services import driver_portrait_service as m

    calls = []

    async def _fake(db_path, server_id, members, directory, **kwargs):
        calls.append((server_id, list(members), directory))
        return len(list(members))

    monkeypatch.setattr(m, "refresh_portraits", _fake)
    bot = MagicMock()
    bot.db_path = ":memory:"
    written = await m.refresh_before_render(
        bot, SERVER_ID, members if members is not None else [_member(1)],
        config=config, directory=directory,
    )
    return written, calls


async def test_the_gate_refreshes_when_the_league_asked_for_it(monkeypatch, directory):
    written, calls = await _gate(monkeypatch, _config(), directory)

    assert written == 1
    assert calls and calls[0][0] == SERVER_ID and calls[0][2] == directory


async def test_the_gate_is_shut_while_portraits_are_disabled(monkeypatch, directory):
    written, calls = await _gate(monkeypatch, _config(use_pfp=False), directory)
    assert (written, calls) == (0, [])


async def test_the_gate_is_shut_while_prerender_updates_are_disabled(monkeypatch, directory):
    written, calls = await _gate(monkeypatch, _config(pfp_prerender=False), directory)
    assert (written, calls) == (0, [])


async def test_the_gate_is_shut_where_the_directory_was_rejected(monkeypatch):
    # The render reports the rejected directory itself, in terms a manager can act on.
    written, calls = await _gate(monkeypatch, _config(), None)
    assert (written, calls) == (0, [])


async def test_the_gate_is_shut_where_no_seat_has_a_member(monkeypatch, directory):
    written, calls = await _gate(monkeypatch, _config(), directory, members=[])
    assert (written, calls) == (0, [])


async def test_the_gate_never_raises_on_a_configuration_predating_the_feature(
    monkeypatch, directory
):
    # A config object carrying neither field reads as off rather than exploding: this
    # function is called from inside a render and must never be the thing that fails it.
    written, calls = await _gate(monkeypatch, SimpleNamespace(), directory)
    assert (written, calls) == (0, [])


# ── The daily refresh ─────────────────────────────────────────────────────


async def _season_db(tmp_path, *, uids=("11", "22"), test_uid="99", status="ACTIVE"):
    """A database carrying just the four tables `assigned_driver_ids` joins."""
    path = str(tmp_path / "season.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE seasons (id INTEGER PRIMARY KEY, server_id INTEGER, status TEXT)")
        await db.execute(
            "CREATE TABLE driver_profiles (id INTEGER PRIMARY KEY, discord_user_id TEXT,"
            " is_test_driver INTEGER DEFAULT 0)"
        )
        await db.execute(
            "CREATE TABLE driver_season_assignments (driver_profile_id INTEGER, season_id INTEGER)"
        )
        await db.execute(
            "CREATE TABLE driver_portraits (server_id INTEGER NOT NULL,"
            " discord_user_id TEXT NOT NULL, avatar_key TEXT NOT NULL,"
            " fetched_at TEXT NOT NULL, PRIMARY KEY (server_id, discord_user_id))"
        )
        await db.execute("INSERT INTO seasons VALUES (1, ?, ?)", (SERVER_ID, status))
        pid = 0
        for uid in uids:
            pid += 1
            await db.execute("INSERT INTO driver_profiles VALUES (?, ?, 0)", (pid, uid))
            await db.execute("INSERT INTO driver_season_assignments VALUES (?, 1)", (pid,))
        if test_uid:
            pid += 1
            await db.execute("INSERT INTO driver_profiles VALUES (?, ?, 1)", (pid, test_uid))
            await db.execute("INSERT INTO driver_season_assignments VALUES (?, 1)", (pid,))
        await db.commit()
    return path


async def test_assigned_driver_ids_skips_test_drivers_and_sorts(tmp_path):
    from services.driver_portrait_service import assigned_driver_ids

    path = await _season_db(tmp_path, uids=("22", "11"))

    # Sorted rather than in insertion order: a run cut short must resume predictably.
    assert await assigned_driver_ids(path, SERVER_ID) == ["11", "22"]


async def test_assigned_driver_ids_ignores_a_season_that_is_not_active(tmp_path):
    from services.driver_portrait_service import assigned_driver_ids

    path = await _season_db(tmp_path, status="COMPLETED")

    assert await assigned_driver_ids(path, SERVER_ID) == []


def _daily_bot(db_path, directory, **config_overrides):
    values = {"use_pfp": True, "pfp_daily": True,
              "driver_image_directory": str(directory)}
    values.update(config_overrides)
    bot = MagicMock()
    bot.db_path = db_path
    bot.image_config_service.get_config = AsyncMock(return_value=SimpleNamespace(**values))
    guild = MagicMock()
    members = {11: _member(11, "k11"), 22: _member(22, "k22")}
    guild.get_member.side_effect = members.get
    bot.get_guild.return_value = guild
    return bot


async def test_the_daily_refresh_writes_every_seated_driver(tmp_path, monkeypatch):
    from services import driver_portrait_service as m

    directory = tmp_path / "drivers"
    directory.mkdir()
    path = await _season_db(tmp_path)
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", tmp_path)

    written = await m.run_daily_refresh(_daily_bot(path, directory), SERVER_ID, now=NOW)

    assert written == 2
    assert sorted(p.name for p in directory.iterdir()) == ["11.svg", "22.svg"]


async def test_the_daily_refresh_stands_aside_when_not_asked_for(tmp_path, monkeypatch):
    from services import driver_portrait_service as m

    directory = tmp_path / "drivers"
    directory.mkdir()
    path = await _season_db(tmp_path)
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", tmp_path)

    assert await m.run_daily_refresh(
        _daily_bot(path, directory, pfp_daily=False), SERVER_ID, now=NOW
    ) == 0
    assert await m.run_daily_refresh(
        _daily_bot(path, directory, use_pfp=False), SERVER_ID, now=NOW
    ) == 0
    assert list(directory.iterdir()) == []


async def test_the_daily_refresh_swallows_a_failure_rather_than_stopping_the_job(tmp_path):
    from services import driver_portrait_service as m

    bot = MagicMock()
    bot.db_path = str(tmp_path / "missing.db")
    bot.image_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))

    # An APScheduler job that raises is logged into a void the league never reads.
    assert await m.run_daily_refresh(bot, SERVER_ID, now=NOW) == 0


async def test_the_daily_refresh_stands_aside_where_the_guild_is_unreachable(tmp_path, monkeypatch):
    from services import driver_portrait_service as m

    directory = tmp_path / "drivers"
    directory.mkdir()
    path = await _season_db(tmp_path)
    monkeypatch.setattr("utils.paths.PROJECT_ROOT", tmp_path)
    bot = _daily_bot(path, directory)
    bot.get_guild.return_value = None

    assert await m.run_daily_refresh(bot, SERVER_ID, now=NOW) == 0


# ── The assumption the whole wrapper rests on ─────────────────────────────


@pytest.mark.rasteriser
def test_inkscape_draws_a_wrapped_portrait_referenced_as_an_external_file(tmp_path):
    """A data-URI PNG, nested one level inside an externally referenced SVG, actually draws.

    This is the single assumption the portrait design rests on, and nothing else verifies
    it. A graphic references `<discord id>.svg` as a `file://` URI, and *that* file carries
    the PNG as `data:image/png;base64,...`. Inkscape resolves a broken link silently -- it
    exits 0 and prints nothing, drawing a blank -- so a failure here would reach a league as
    a lineup full of holes with no error anywhere.

    CI cannot run this: Inkscape is too heavy for a hosted runner, so both jobs deselect the
    `rasteriser` marker. It is run by hand on a host that has it.

    The test draws the same scene twice, once against a wrapped portrait and once against a
    reference that does not resolve, and requires the two to differ. Comparing against a
    known-bad render rather than asserting "no error" is deliberate: no error is exactly
    what a silent failure produces.
    """
    import subprocess

    from services.image_render_service import find_converter

    # A red square, so a drawn portrait is unmistakably different from a blank one.
    red_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="
    )
    portrait = tmp_path / "12345.svg"
    portrait.write_text(wrap_png(red_png), encoding="utf-8")

    def _draw(name: str, href: str) -> bytes:
        source = tmp_path / f"{name}.svg"
        source.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" width="64" height="64">'
            '<rect width="64" height="64" fill="#ffffff"/>'
            f'<image x="0" y="0" width="64" height="64" xlink:href="{href}"/></svg>',
            encoding="utf-8",
        )
        out = tmp_path / f"{name}.png"
        subprocess.run(
            [
                find_converter(),
                str(source),
                "--export-type=png",
                f"--export-filename={out}",
                "--export-width=64",
                "--export-height=64",
            ],
            check=True,
            capture_output=True,
        )
        return out.read_bytes()

    drawn = _draw("with_portrait", portrait.as_uri())
    blank = _draw("without_portrait", "file:///no/such/portrait.svg")

    assert drawn != blank, "the wrapped portrait drew nothing at all"


@pytest.mark.rasteriser
def test_a_wrapped_portrait_survives_the_whole_fill_and_render_path(tmp_path):
    """End to end through `svg_fill`, which is what a real lineup actually does.

    The unit above proves Inkscape can follow the two hops. This proves the module hands it
    the right thing: the portrait is resolved by `resolve_asset`, anchored to a `file://`
    URI by `_set_href`, and passes `_unreachable_links` -- which is fatal on a link it
    cannot follow, and is the check that would catch a wrapper written wrongly.
    """
    import subprocess

    from services.image_render_service import find_converter
    from utils.svg_fill import FillSpec, fill
    from utils.svg_document import parse_svg_bytes

    drivers = tmp_path / "drivers"
    drivers.mkdir()
    red_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg=="
    )
    (drivers / "12345.svg").write_text(wrap_png(red_png), encoding="utf-8")

    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">'
        b'<image id="team_1_driver_1_image" width="64" height="64"/></svg>'
    )
    result = fill(
        FillSpec(
            root=root,
            image_type="lineup_template",
            image_data={"team_1_driver_1_image": ("driver", "12345")},
            asset_directories={"driver": drivers},
        )
    )

    assert result.unresolved == []
    href = root.find(".//{http://www.w3.org/2000/svg}image").get("href")
    assert href.startswith("file:///") and href.endswith("12345.svg")

    source = tmp_path / "filled.svg"
    source.write_bytes(result.svg)
    out = tmp_path / "filled.png"
    subprocess.run(
        [
            find_converter(), str(source), "--export-type=png",
            f"--export-filename={out}", "--export-width=64", "--export-height=64",
        ],
        check=True,
        capture_output=True,
    )
    assert out.is_file() and out.stat().st_size > 0
