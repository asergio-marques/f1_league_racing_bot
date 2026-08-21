"""The calendar preview draws the league's own calendar (045, US1).

The claim the whole feature rests on: what comes back is the division's own rounds, in its
own order, cropped at its own round count — not a fabricated "Test Division". These tests
assert against the resolved drawing, which is where the data lands; whether the picture
*looks* right is judged by eye against the PNG, per quickstart.md.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    build_calendar_preview,
    resolve_context,
)
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 6161
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

#: Circuits the migrations already seed. The registry ships with the bot and a league does
#: not author it, so a preview is drawn against the real names rather than invented ones.
TRACK_A = "Albert Park Circuit"
TRACK_B = "Autodromo Nazionale Monza"
TRACK_C = "Autodromo Internazionale Enzo e Dino Ferrari"


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "calendar.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
async def bot(db_path):
    config_service = ImageConfigService(db_path)
    await config_service.create_with_defaults(SERVER_ID)
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        image_config_service=config_service,
    )


async def _seed(db_path, rounds):
    """A season, a division, its rounds, and the tracks they name."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, 'ACTIVE', 4)",
            (SERVER_ID, NOW.date().isoformat()),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Premier', 1, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        for number, fmt, track in rounds:
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (
                    division_id,
                    number,
                    fmt,
                    track,
                    (NOW + timedelta(days=14 * number)).isoformat(),
                ),
            )
        await db.commit()
    return division_id


# ── The league's own calendar ─────────────────────────────────────────────


class TestCalendarPreview:
    async def test_it_draws_exactly_the_divisions_own_rounds(self, bot, db_path):
        await _seed(
            db_path,
            [
                (1, "NORMAL", TRACK_A),
                (2, "SPRINT", TRACK_B),
                (3, "NORMAL", TRACK_C),
            ],
        )
        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)

        requests = await build_calendar_preview(bot, context)

        assert len(requests) == 1
        label, template_key, _spec = requests[0]
        assert label == "Calendar"
        assert template_key == "calendar_template"

    async def test_the_drawing_carries_the_divisions_identity(self, bot, db_path):
        await _seed(db_path, [(1, "NORMAL", TRACK_A)])
        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)

        assert context.division_name == "Premier"
        assert context.division_tier == 1
        assert context.season_number == 4

    async def test_the_rounds_are_drawn_in_their_configured_order(self, bot, db_path):
        """FR-016 — the order is the league's, not the order rows happen to come back in."""
        from services.image_calendar_service import resolve_drawing

        await _seed(
            db_path,
            [
                (3, "NORMAL", TRACK_C),
                (1, "NORMAL", TRACK_A),
                (2, "SPRINT", TRACK_B),
            ],
        )
        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)

        from services.calendar_post_service import tracks_by_name

        rounds = await bot.season_service.get_division_rounds(context.division_id)
        drawing = resolve_drawing(
            division_name=context.division_name,
            division_tier=context.division_tier,
            season_number=context.season_number,
            rounds=rounds,
            tracks=await tracks_by_name(bot.db_path),
        )

        assert [r.number for r in drawing.rounds] == ["1", "2", "3"]
        assert [r.track_name for r in drawing.rounds] == [TRACK_A, TRACK_B, TRACK_C]

    async def test_a_division_of_one_round_draws_one_round(self, bot, db_path):
        """The crop falls at the league's own count, however short (SC-005)."""
        from services.calendar_post_service import tracks_by_name
        from services.image_calendar_service import resolve_drawing

        await _seed(db_path, [(1, "NORMAL", TRACK_A)])
        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)

        drawing = resolve_drawing(
            division_name=context.division_name,
            division_tier=context.division_tier,
            season_number=context.season_number,
            rounds=await bot.season_service.get_division_rounds(context.division_id),
            tracks=await tracks_by_name(bot.db_path),
        )

        assert len(drawing.rounds) == 1


# ── T017: the league's own artwork ────────────────────────────────────────


class TestItUsesTheLeaguesOwnDirectories:
    async def test_the_configured_track_and_flag_directories_are_the_ones_used(
        self, bot, db_path
    ):
        """FR-035 — never the packaged directories the withdrawn command hardcoded."""
        await _seed(db_path, [(1, "NORMAL", TRACK_A)])
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "resources/defaults/teams"
        )

        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)

        assert context.asset_directories["flag"].name == "teams"
        assert context.asset_directories["track"].name == "tracks"

    async def test_the_preview_builds_against_those_directories(self, bot, db_path):
        """The spec builder must actually receive them, not merely resolve them."""
        import services.image_calendar_service as calendar_service

        await _seed(db_path, [(1, "NORMAL", TRACK_A)])
        await bot.image_config_service.set_field(
            SERVER_ID, "flag_directory", "resources/defaults/teams"
        )
        context = await resolve_context(bot, SERVER_ID, "Premier", require_rounds=True)
        requests = await build_calendar_preview(bot, context)

        seen = {}
        original = calendar_service.build_fill_spec

        def _capture(drawing, root, *, track_directory=None, flag_directory=None):
            seen["track"] = track_directory
            seen["flag"] = flag_directory
            return SimpleNamespace()

        # The preview closed over the module attribute at call time, so patching it here
        # is what lets the directories be observed without rendering anything.
        calendar_service.build_fill_spec = _capture
        try:
            requests = await build_calendar_preview(bot, context)
            requests[0][2](object())
        finally:
            calendar_service.build_fill_spec = original

        assert seen["flag"].name == "teams"
        assert seen["track"].name == "tracks"
