"""Every preview reaches a PNG (045, T039).

Rule XIV.14: a generated image is verified as a PNG, never as the filled SVG in a browser —
the two disagree on exactly the things worth checking. These tests take each kind through
the whole pipeline and assert a raster comes out with real dimensions.

Whether the picture *looks* right is a judgement no assertion makes. That is done by hand
against the PNG, per quickstart.md, and is deliberately not a task here.
"""
from __future__ import annotations

import os
import struct
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_render_service import ImageRenderService  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    build_attendance_preview,
    build_calendar_preview,
    build_lineup_preview,
    build_results_preview,
    build_rsvp_preview,
    build_standings_preview,
    build_verdict_preview,
    build_weather_preview,
    resolve_context,
)
from services.image_validity_service import ImageValidityService  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 9494
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
TRACK = "Albert Park Circuit"

#: The teams `resources/defaults/templates/lineup_template.svg` names. Every one must be fielded or
#: the render refuses, naming the teams it could not find — which is the check working.
PACKAGED_TEAMS = (
    "Apex Racing",
    "Aurora Racing",
    "Basalt Motorsport",
    "Halcyon GP",
    "Ironclad Racing",
    "Kestrel GP",
    "Meridian GP",
    "Nimbus Racing",
    "Nordwind Motorsport",
    "Solstice Motorsport",
    "Vanguard Racing",
)

#: The rasteriser is a separate program no package declaration installs. Where it is absent
#: the pipeline cannot be exercised at all, and skipping is the honest outcome — the render
#: service refuses in exactly the same way, which is covered elsewhere. The marker also
#: takes these out of CI, which does not install Inkscape; see tests/conftest.py.
requires_rasteriser = pytest.mark.rasteriser


def png_size(path) -> tuple[int, int]:
    """Width and height from the PNG header, without a decoding dependency."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", header[16:24])


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "render.db")
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
    validity_service = ImageValidityService(
        config_service, SimpleNamespace(is_images_enabled=_true)
    )
    return SimpleNamespace(
        db_path=db_path,
        season_service=SeasonService(db_path),
        image_config_service=config_service,
        image_validity_service=validity_service,
        image_render_service=ImageRenderService(
            db_path, config_service, validity_service
        ),
        attendance_service=SimpleNamespace(get_division_config=_none),
    )


async def _none(*args, **kwargs):
    return None


async def _true(*args, **kwargs):
    return True


@pytest.fixture
async def league(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, 'ACTIVE', 1)",
            (SERVER_ID, NOW.date().isoformat()),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Premier', 1, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        for number, fmt in ((1, "NORMAL"), (2, "SPRINT"), (3, "MYSTERY")):
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (
                    division_id,
                    number,
                    fmt,
                    None if fmt == "MYSTERY" else TRACK,
                    (NOW + timedelta(days=14 * number)).isoformat(),
                ),
            )

        user_id = 9_500_000
        # The packaged lineup template names its fields after these eleven teams, and
        # demands all of them: a lineup template is authored against a league's own team
        # list, which is exactly why SC-004 wants the preview drawn against real teams.
        # Seeding the packaged names is what lets the shipped template render at all.
        # The template also declares reserve slots and treats the first as mandatory, so
        # the reserve team is seated alongside them.
        for team_name in PACKAGED_TEAMS + ("Reserve",):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, 2, ?)",
                (division_id, team_name, int(team_name == "Reserve")),
            )
            team_id = cursor.lastrowid
            for seat_number in (1, 2):
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
                seat_id = cursor.lastrowid
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (server_id, discord_user_id, "
                    "current_state) VALUES (?, ?, 'ACTIVE')",
                    (SERVER_ID, user_id),
                )
                profile_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO signup_records (server_id, discord_user_id, "
                    "server_display_name, discord_username, nationality) "
                    "VALUES (?, ?, ?, 'd', 'British')",
                    (SERVER_ID, str(user_id), f"{team_name} {seat_number}"),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                    "division_id, current_position, current_points, points_gap_to_first, "
                    "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                    (profile_id, season_id, division_id, seat_id),
                )
                user_id += 1
        await db.commit()
    return division_id


#: kind -> (resolve kwargs, builder). The eleven, as the contract lists them.
KINDS = {
    "calendar": (dict(require_rounds=True), lambda b, c: build_calendar_preview(b, c)),
    "lineup": (dict(require_teams=True), lambda b, c: build_lineup_preview(b, c)),
    "results": (
        dict(round_number=1, require_teams=True),
        lambda b, c: build_results_preview(b, c),
    ),
    "standings": (
        dict(round_number=1, require_teams=True),
        lambda b, c: build_standings_preview(b, c),
    ),
    "attendance": (
        dict(round_number=1, require_teams=True),
        lambda b, c: build_attendance_preview(b, c),
    ),
    "rsvp": (dict(round_number=1), lambda b, c: build_rsvp_preview(b, c)),
    "verdict": (
        dict(round_number=1, require_teams=True),
        lambda b, c: build_verdict_preview(b, c),
    ),
    "weather-p1": (
        dict(round_number=1, require_mystery=False),
        lambda b, c: build_weather_preview(b, c, phase=1),
    ),
    "weather-p2": (
        dict(round_number=1, require_mystery=False),
        lambda b, c: build_weather_preview(b, c, phase=2),
    ),
    "weather-p3": (
        dict(round_number=1, require_mystery=False),
        lambda b, c: build_weather_preview(b, c, phase=3),
    ),
    "weather-mystery": (
        dict(round_number=3, require_mystery=True),
        lambda b, c: build_weather_preview(b, c, phase=0),
    ),
}


@requires_rasteriser
@pytest.mark.parametrize("kind", sorted(KINDS))
async def test_every_preview_reaches_a_png(bot, league, kind, tmp_path):
    """Rule XIV.14 — the check is against the raster, never the SVG."""
    kwargs, build = KINDS[kind]
    context = await resolve_context(bot, SERVER_ID, "Premier", **kwargs)

    requests = await build(bot, context)
    assert requests, f"{kind} produced no picture request"

    for label, template_key, spec_builder in requests:
        outcome = await bot.image_render_service.render(
            SERVER_ID, template_key, spec_builder, output_dir=tmp_path
        )
        assert outcome.problem is None, f"{kind} / {label}: {outcome.problem}"
        assert outcome.png_paths, f"{kind} / {label} produced no file"
        for path in outcome.png_paths:
            width, height = png_size(path)
            assert width > 0 and height > 0, f"{kind} / {label} rastered to nothing"


async def test_the_eleven_kinds_are_all_covered():
    """A kind added to the command surface without a raster check would slip through."""
    from cogs.image_cog import ImageCog

    assert {c.name for c in ImageCog.test.commands} == set(KINDS)


@requires_rasteriser
async def test_the_standings_preview_draws_the_whole_grid(bot, league, tmp_path):
    """Both championships, round columns included, reach a picture (FR-025, FR-026).

    Regression coverage for the round grid: the templates declare a session cell per round
    per row, and a car per round per row on the constructors graphic, and both must resolve
    over the league's own calendar and drivers for either graphic to render at all.
    """
    context = await resolve_context(
        bot, SERVER_ID, "Premier", round_number=1, require_teams=True
    )

    requests = await build_standings_preview(bot, context)
    assert len(requests) == 2, "both championships are drawn"

    for label, template_key, spec_builder in requests:
        outcome = await bot.image_render_service.render(
            SERVER_ID, template_key, spec_builder, output_dir=tmp_path
        )
        assert outcome.problem is None, f"{label}: {outcome.problem}"
        assert outcome.png_paths, f"{label} produced no file"
