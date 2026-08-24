"""The standings posting path (040, US2 and US5).

Covers ``specs/040-standings-image-generation/contracts/standings-posting.md`` — the only
posting in the bot where **one call posts two messages**, and the only one where the textual
fallback is finer-grained than the textual message it replaces.

The matrix that matters is the per-championship one. The textual standings are a single
message carrying both championships; the graphics are two. So a drivers graphic that will
not draw must fall back to the drivers section *alone* — falling back to the whole textual
message would print the constructors table a second time, beside the constructors graphic
that had just drawn it (FR-052, Constitution XIV.4 and XIV.7).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

pytestmark = pytest.mark.asyncio

DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"


def _bot(db_path="db", *, module=True, toggle=True, drivers_valid=True, constructors_valid=True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_images_enabled = AsyncMock(return_value=module)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"standings": toggle})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={
            DRIVERS: MagicMock(valid=drivers_valid),
            CONSTRUCTORS: MagicMock(valid=constructors_valid),
        }
    )
    bot.output_router.post_log = AsyncMock()
    return bot


def _guild():
    guild = MagicMock()
    guild.id = 1
    # No member is resolvable, so the name chain falls through to its later links rather
    # than to a mock's ``display_name`` — which would put a repr on the graphic.
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)
    guild.get_role.return_value = None
    return guild


def _channel(sent, order=None):
    channel = AsyncMock()

    async def send(content=None, **kwargs):
        if order is not None:
            order.append(f"posted {kwargs.get('file')}")
        sent.append((content, kwargs))
        message = MagicMock()
        message.id = 1000 + len(sent)
        return message

    async def fetch_message(message_id):
        previous = AsyncMock()

        async def delete():
            if order is not None:
                order.append(f"deleted {message_id}")

        previous.delete = delete
        return previous

    channel.send = send
    channel.fetch_message = fetch_message
    return channel


def _decision(*, posts=True, rejects=False, png=None, notices=(), problem=None):
    decision = MagicMock()
    decision.posts_image = posts
    decision.rejects = rejects
    decision.png_paths = [png] if png is not None else []
    decision.notices = list(notices)
    decision.problem = problem
    decision.caller_message = MagicMock(return_value="❌ the template is at fault")
    return decision


def _drawings():
    return (
        MagicMock(template_key=DRIVERS),
        MagicMock(template_key=CONSTRUCTORS),
    )


def _patched(render, *, previous_ids=None, stored=None):
    """Patch the module's collaborators: the drawings, the render, and the id columns."""
    from services import image_standings_post as m

    previous_ids = previous_ids or {}
    stored = stored if stored is not None else {}

    async def _get(db_path, division_id, round_id, championship="drivers"):
        return previous_ids.get(championship)

    async def _set(db_path, division_id, round_id, message_id, championship="drivers"):
        stored[championship] = message_id

    return [
        patch.object(m, "build_drawings", AsyncMock(return_value=_drawings())),
        patch.object(m, "render_png", render),
        patch(
            "services.results_post_service._get_standings_message_id",
            AsyncMock(side_effect=_get),
        ),
        patch(
            "services.results_post_service._set_standings_message_id",
            AsyncMock(side_effect=_set),
        ),
    ]


async def _try_post(bot, channel, render, *, origin=None, previous_ids=None, stored=None):
    from models.image_module import PostingOrigin
    from services.image_standings_post import try_post

    kwargs = dict(
        db_path="db",
        division_id=7,
        round_id=5,
        round_number=5,
        heading="**Season 3 Main Round 5 — Race**",
        label="_Provisional Results_",
        driver_snapshots=[],
        team_snapshots=[],
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="PROVISIONAL",
        division_name="Main",
    )
    if origin is not None:
        kwargs["origin"] = origin
    else:
        kwargs["origin"] = PostingOrigin.SCHEDULED

    patches = _patched(render, previous_ids=previous_ids, stored=stored)
    for p in patches:
        p.start()
    try:
        return await try_post(bot, _guild(), channel, **kwargs)
    finally:
        for p in patches:
            p.stop()


# ── Enablement ────────────────────────────────────────────────────────────


async def test_the_aspect_is_read_per_template_so_one_faulty_half_does_not_stop_the_other():
    from services.image_standings_post import standings_enabled

    bot = _bot(constructors_valid=False)
    assert await standings_enabled(bot, 1, DRIVERS) is True
    assert await standings_enabled(bot, 1, CONSTRUCTORS) is False


async def test_the_module_being_off_stands_the_whole_flow_aside():
    from services.image_standings_post import standings_enabled

    bot = _bot(module=False)
    assert await standings_enabled(bot, 1, DRIVERS) is False


async def test_the_toggle_being_off_stands_the_whole_flow_aside():
    from services.image_standings_post import standings_enabled

    assert await standings_enabled(_bot(toggle=False), 1, DRIVERS) is False


async def test_a_reader_that_raises_falls_back_rather_than_breaking_the_posting():
    from services.image_standings_post import standings_enabled

    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await standings_enabled(bot, 1, DRIVERS) is False


# ── The failure matrix ────────────────────────────────────────────────────


async def test_both_render_so_two_messages_are_posted_and_no_section_falls_back(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent = []

    outcome = await _try_post(
        _bot(), _channel(sent), AsyncMock(return_value=_decision(png=png))
    )

    assert outcome.applicable
    assert outcome.fallback_championships == []
    assert outcome.drivers.posted and outcome.constructors.posted
    assert len(sent) == 2, "the two championships are two messages"
    assert [kwargs["file"].filename for _content, kwargs in sent] == [
        "standings_drivers.png",
        "standings_constructors.png",
    ], "drivers first, constructors after"


async def test_the_drivers_failing_falls_back_to_the_drivers_section_alone(tmp_path):
    """The constructors graphic still draws, so its table must not be repeated as text."""
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent = []

    async def render(bot, server_id, drawing, origin):
        if drawing.template_key == DRIVERS:
            return _decision(posts=False, problem=MagicMock(detail="no rows"))
        return _decision(png=png)

    outcome = await _try_post(_bot(), _channel(sent), AsyncMock(side_effect=render))

    assert outcome.fallback_championships == ["drivers"]
    assert outcome.constructors.posted
    assert len(sent) == 1
    assert sent[0][1]["file"].filename == "standings_constructors.png"


async def test_the_constructors_failing_falls_back_to_the_constructors_section_alone(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent = []

    async def render(bot, server_id, drawing, origin):
        if drawing.template_key == CONSTRUCTORS:
            return _decision(posts=False, problem=MagicMock(detail="no rows"))
        return _decision(png=png)

    outcome = await _try_post(_bot(), _channel(sent), AsyncMock(side_effect=render))

    assert outcome.fallback_championships == ["constructors"]
    assert outcome.drivers.posted
    assert len(sent) == 1
    assert sent[0][1]["file"].filename == "standings_drivers.png"


async def test_both_failing_falls_back_to_both_sections_so_each_is_read_exactly_once():
    sent = []

    outcome = await _try_post(
        _bot(),
        _channel(sent),
        AsyncMock(return_value=_decision(posts=False, problem=MagicMock(detail="bad"))),
    )

    assert outcome.applicable, "the flow ran; it simply drew nothing"
    assert outcome.fallback_championships == ["drivers", "constructors"]
    assert sent == [], "no graphic was posted"


async def test_an_invalid_template_falls_that_championship_back_without_rendering(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent = []
    render = AsyncMock(return_value=_decision(png=png))

    outcome = await _try_post(
        _bot(constructors_valid=False), _channel(sent), render
    )

    assert outcome.fallback_championships == ["constructors"]
    assert outcome.drivers.posted
    assert render.await_count == 1, "the faulty template is never handed to the renderer"


async def test_a_commanded_failure_rejects_and_posts_nothing_at_all(tmp_path):
    from models.image_module import PostingOrigin

    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent = []

    async def render(bot, server_id, drawing, origin):
        if drawing.template_key == DRIVERS:
            return _decision(posts=False, rejects=True, problem=MagicMock(detail="bad"))
        return _decision(png=png)

    outcome = await _try_post(
        _bot(),
        _channel(sent),
        AsyncMock(side_effect=render),
        origin=PostingOrigin.COMMANDED,
    )

    assert outcome.rejects
    assert outcome.message == "❌ the template is at fault"
    assert sent == [], "a rejected command posts nothing, not even the sound championship"
    assert outcome.fallback_championships == [], "and asks the caller for no text either"


async def test_the_whole_flow_stands_aside_when_neither_template_is_wanted():
    sent = []
    render = AsyncMock()

    outcome = await _try_post(
        _bot(toggle=False), _channel(sent), render
    )

    assert not outcome.applicable, "the caller's untouched textual body must run"
    assert outcome.fallback_championships == []
    assert render.await_count == 0


async def test_no_bot_no_guild_and_no_channel_each_stand_the_flow_aside():
    from services.image_standings_post import StandingsPostOutcome, try_post

    for args in ((None, _guild(), MagicMock()), (_bot(), None, MagicMock()), (_bot(), _guild(), None)):
        outcome = await try_post(
            *args,
            db_path="db",
            division_id=7,
            round_id=5,
            round_number=5,
            heading="h",
            label="l",
            driver_snapshots=[],
            team_snapshots=[],
            reserve_user_ids=set(),
            show_reserves=False,
            result_status="PROVISIONAL",
            division_name="Main",
        )
        assert isinstance(outcome, StandingsPostOutcome)
        assert not outcome.applicable


# ── Ordering, identity and delivery ───────────────────────────────────────


async def test_each_replacement_is_produced_before_its_old_message_is_deleted(tmp_path):
    """FR-048. A render that fails must leave the league the standings it had."""
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent, order = [], []

    await _try_post(
        _bot(),
        _channel(sent, order),
        AsyncMock(return_value=_decision(png=png)),
        previous_ids={"drivers": 111, "constructors": 222},
    )

    posts = [i for i, step in enumerate(order) if step.startswith("posted")]
    deletes = [i for i, step in enumerate(order) if step.startswith("deleted")]
    assert len(posts) == 2 and len(deletes) == 2
    assert posts[0] < deletes[0], "the drivers replacement precedes its deletion"
    assert posts[1] < deletes[1], "and so does the constructors one"


async def test_the_two_message_ids_are_persisted_to_their_own_columns(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    sent, stored = [], {}

    await _try_post(
        _bot(), _channel(sent), AsyncMock(return_value=_decision(png=png)), stored=stored
    )

    assert set(stored) == {"drivers", "constructors"}
    assert stored["drivers"] != stored["constructors"]


async def test_a_discord_failure_falls_back_to_text_rather_than_retrying_the_image(tmp_path):
    """FR-056 — the generation succeeded; it is the delivery that did not."""
    import discord

    png = tmp_path / "s.png"
    png.write_bytes(b"x")

    channel = AsyncMock()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))

    outcome = await _try_post(
        _bot(), channel, AsyncMock(return_value=_decision(png=png))
    )

    assert outcome.fallback_championships == ["drivers", "constructors"]


async def test_a_resolution_fault_reports_once_and_falls_both_back():
    bot = _bot()
    sent = []

    from services import image_standings_post as m

    with patch.object(m, "build_drawings", AsyncMock(side_effect=RuntimeError("no calendar"))):
        outcome = await m.try_post(
            bot,
            _guild(),
            _channel(sent),
            db_path="db",
            division_id=7,
            round_id=5,
            round_number=5,
            heading="h",
            label="l",
            driver_snapshots=[],
            team_snapshots=[],
            reserve_user_ids=set(),
            show_reserves=False,
            result_status="PROVISIONAL",
            division_name="Main",
        )

    assert outcome.fallback_championships == ["drivers", "constructors"]
    assert sent == []
    bot.output_router.post_log.assert_awaited()


# ── Reporting (FR-053) ────────────────────────────────────────────────────


async def test_a_fault_is_reported_to_the_log_channel_naming_the_championship():
    bot = _bot()
    sent = []

    async def render(bot_, server_id, drawing, origin):
        if drawing.template_key == DRIVERS:
            return _decision(posts=False, problem=MagicMock(detail="a field is missing"))
        return _decision(posts=False, problem=MagicMock(detail="a field is missing"))

    await _try_post(bot, _channel(sent), AsyncMock(side_effect=render))

    logged = " ".join(str(call.args[1]) for call in bot.output_router.post_log.await_args_list)
    assert "drivers standings" in logged
    assert "constructors standings" in logged
    assert "Main round 5" in logged


async def test_nothing_is_reported_into_the_standings_channel(tmp_path):
    """Drivers read that channel; a template fault is the manager's business."""
    sent = []

    await _try_post(
        _bot(),
        _channel(sent),
        AsyncMock(return_value=_decision(posts=False, problem=MagicMock(detail="bad"))),
    )

    assert sent == []


async def test_notices_are_reported_alongside_a_graphic_that_did_draw(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    bot = _bot()
    notice = MagicMock(detail="a flag fell back")

    await _try_post(
        bot, _channel([]), AsyncMock(return_value=_decision(png=png, notices=[notice]))
    )

    logged = " ".join(str(call.args[1]) for call in bot.output_router.post_log.await_args_list)
    assert "a flag fell back" in logged
    assert "drivers standings" in logged


# ── build_drawings, run against a real database ───────────────────────────
#
# Every test above patches ``build_drawings``, which is exactly the shape of coverage that
# let two render bodies ship raising ``NameError`` on every call (see
# ``test_image_post_render_entry_points``). These run it for real, over migrated tables, so
# that a query naming a column that does not exist fails here.


async def _seed_league(tmp_path):
    """A season, a division, two teams with seats, two drivers, a run round and a next one."""
    from db.database import get_connection, run_migrations

    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 4)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, tier, mention_role_id) "
            "VALUES (?, 'Alpha', 1, 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid

        round_ids = []
        for number in (1, 2):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, result_status, "
                "track_name, scheduled_at) VALUES (?, ?, 'STANDARD', 'FINAL', "
                "'Silverstone', '2026-06-01T18:00:00')",
                (division_id, number),
            )
            round_ids.append(cursor.lastrowid)

        # Only the first round has been run.
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_ids[0], division_id),
        )
        session_id = cursor.lastrowid

        for team_name, role_id, drivers in (
            ("Vermilion", 900, [(11, 1), (12, 2)]),
            ("Cobalt", 901, [(13, 1)]),
        ):
            await db.execute(
                "INSERT INTO team_role_configs (server_id, team_name, role_id) "
                "VALUES (1, ?, ?)",
                (team_name, role_id),
            )
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, 2, 0)",
                (division_id, team_name),
            )
            instance_id = cursor.lastrowid
            for user_id, seat_number in drivers:
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (server_id, discord_user_id, "
                    "current_state) VALUES (1, ?, 'ACTIVE')",
                    (user_id,),
                )
                profile_id = cursor.lastrowid
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, "
                    "driver_profile_id) VALUES (?, ?, ?)",
                    (instance_id, seat_number, profile_id),
                )
                seat_id = cursor.lastrowid
                await db.execute(
                    "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                    "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                    (profile_id, season_id, division_id, seat_id),
                )
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                    "team_role_id, finishing_position, outcome, points_awarded) "
                    "VALUES (?, ?, ?, ?, 'CLASSIFIED', 10)",
                    (session_id, user_id, role_id, seat_number),
                )
        await db.commit()

    return db_path, division_id, round_ids


def _snapshots(round_id, division_id):
    from models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

    drivers = [
        DriverStandingsSnapshot(
            id=i,
            round_id=round_id,
            division_id=division_id,
            driver_user_id=user_id,
            standing_position=i,
            total_points=30 - i,
            finish_counts={},
            first_finish_rounds={},
        )
        for i, user_id in enumerate((11, 12, 13), start=1)
    ]
    teams = [
        TeamStandingsSnapshot(
            id=i,
            round_id=round_id,
            division_id=division_id,
            team_role_id=role_id,
            standing_position=i,
            total_points=50 - i,
            finish_counts={},
            first_finish_rounds={},
        )
        for i, role_id in enumerate((900, 901), start=1)
    ]
    return drivers, teams


async def test_build_drawings_resolves_both_championships_against_real_tables(tmp_path):
    from services.image_standings_post import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, constructors = await build_drawings(
        bot,
        _guild(),
        db_path=db_path,
        server_id=1,
        division_id=division_id,
        round_id=round_ids[0],
        round_number=1,
        driver_snapshots=driver_snaps,
        team_snapshots=team_snaps,
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="FINAL",
        division_name="Alpha",
        season_number=4,
    )

    assert drivers.template_key == DRIVERS
    assert constructors.template_key == CONSTRUCTORS
    assert drivers.entry_count == 3
    assert constructors.entry_count == 2

    # The grid is the division's own calendar, both rounds, run or not.
    assert [heading.number for heading in drivers.rounds] == ["1", "2"]
    assert [heading.track for heading in drivers.rounds] == ["Silverstone", "Silverstone"]


async def test_a_drivers_row_names_the_team_its_own_driver_sits_in(tmp_path):
    """The two graphics key their team names differently — by driver, and by role."""
    from services.image_standings_post import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, constructors = await build_drawings(
        bot,
        _guild(),
        db_path=db_path,
        server_id=1,
        division_id=division_id,
        round_id=round_ids[0],
        round_number=1,
        driver_snapshots=driver_snaps,
        team_snapshots=team_snaps,
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="FINAL",
        division_name="Alpha",
    )

    assert [entry.team_name for entry in drivers.entries] == [
        "Vermilion",
        "Vermilion",
        "Cobalt",
    ]
    assert [entry.team_name for entry in constructors.entries] == [
        "Vermilion",
        "Cobalt",
    ]


async def test_the_run_round_fills_its_cells_and_the_unrun_one_empties_them(tmp_path):
    from services.image_standings_post import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, _constructors = await build_drawings(
        bot,
        _guild(),
        db_path=db_path,
        server_id=1,
        division_id=division_id,
        round_id=round_ids[0],
        round_number=1,
        driver_snapshots=driver_snaps,
        team_snapshots=team_snaps,
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="FINAL",
        division_name="Alpha",
    )

    leader = drivers.entries[0]
    assert leader.cells[1].sessions["feature_race_result"] != ""
    assert leader.cells[2].sessions["feature_race_result"] == "", (
        "a round the division has not run empties its cells rather than dashing them"
    )


@pytest.mark.rasteriser
async def test_the_posting_paths_own_drawings_reach_a_png(tmp_path):
    """Rule XIV.14 — the check is against the raster, never the SVG.

    The preview path is rasterised elsewhere. This takes the **posting** path's own
    resolver — real snapshots, the division's real calendar, real seats — through the
    shipped templates to two real PNGs, which is the pipeline a league actually gets.
    """
    import struct
    from types import SimpleNamespace

    from services.image_config_service import ImageConfigService
    from services.image_render_service import (
        ImageRenderService,
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from services.image_standings_post import build_drawings
    from services.image_standings_service import build_fill_spec
    from services.image_validity_service import ImageValidityService

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    async def _true(*args, **kwargs):
        return True

    config_service = ImageConfigService(db_path)
    await config_service.create_with_defaults(1)
    validity_service = ImageValidityService(
        config_service, SimpleNamespace(is_images_enabled=_true)
    )
    bot = SimpleNamespace(
        db_path=db_path,
        image_config_service=config_service,
        image_validity_service=validity_service,
        image_render_service=ImageRenderService(
            db_path, config_service, validity_service
        ),
    )

    drawings = await build_drawings(
        bot,
        _guild(),
        db_path=db_path,
        server_id=1,
        division_id=division_id,
        round_id=round_ids[0],
        round_number=1,
        driver_snapshots=driver_snaps,
        team_snapshots=team_snaps,
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="FINAL",
        division_name="Alpha",
        season_number=4,
    )

    config = await config_service.get_config(1)
    for drawing in drawings:
        directories, faults = resolve_configured_directories(
            config,
            (
                ("team", "team_image_directory"),
                ("flag", "flag_directory"),
                ("track", "track_image_directory"),
                ("marker", "marker_directory"),
            ),
            image_type=drawing.template_key,
        )
        outcome = await bot.image_render_service.render(
            1,
            drawing.template_key,
            spec_builder_with_faults(build_fill_spec, drawing, directories, faults),
            output_dir=tmp_path,
        )
        assert outcome.problem is None, (drawing.template_key, outcome.problem)
        assert outcome.png_paths, drawing.template_key

        png = outcome.png_paths[0]
        with open(png, "rb") as handle:
            header = handle.read(24)
        assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{drawing.template_key} is not a PNG"
        width, height = struct.unpack(">II", header[16:24])
        assert width > 0 and height > 0


async def test_the_constructors_cars_are_allocated_from_the_divisions_own_seats(tmp_path):
    from services.image_standings_post import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    _drivers, constructors = await build_drawings(
        bot,
        _guild(),
        db_path=db_path,
        server_id=1,
        division_id=division_id,
        round_id=round_ids[0],
        round_number=1,
        driver_snapshots=driver_snaps,
        team_snapshots=team_snaps,
        reserve_user_ids=set(),
        show_reserves=False,
        result_status="FINAL",
        division_name="Alpha",
    )

    vermilion = constructors.entries[0]
    assert sorted(vermilion.cells[1].cars) == [1, 2], "both seats drove"
    cobalt = constructors.entries[1]
    assert sorted(cobalt.cells[1].cars) == [1], "one seat drove"
    assert constructors.team_seat_counts["Vermilion"] == 2


# ── The branch inside post_standings ──────────────────────────────────────
#
# The image path is a guard clause in front of an untouched body. Where the flow does not
# run, everything below it behaves exactly as it did before 040; where it does, the textual
# message is composed of whichever championships did *not* draw.


async def _seed(tmp_path, *, cancelled=False):
    """A database holding one season, division and round, ready to post standings for."""
    from db.database import get_connection, run_migrations

    db_path = str(tmp_path / "standings.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Beta', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, result_status, "
            "scheduled_at, status) VALUES (?, 3, 'STANDARD', 'FINAL', "
            "'2026-06-01T18:00:00', ?)",
            (division_id, "CANCELLED" if cancelled else "ACTIVE"),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.commit()
    return db_path, division_id, round_id


def _outcome(*, applicable=True, rejects=False, fallbacks=()):
    from services.image_standings_post import StandingsPostOutcome

    outcome = MagicMock(spec=StandingsPostOutcome)
    outcome.applicable = applicable
    outcome.rejects = rejects
    outcome.fallback_championships = list(fallbacks)
    outcome.message = "❌ broken" if rejects else None
    return outcome


async def _post(db_path, division_id, round_id, captured, *, bot=None, channel=None):
    from services.results_post_service import post_standings

    if channel is None:
        channel = AsyncMock()

        async def send(content=None, **kwargs):
            captured.append(content)
            message = MagicMock()
            message.id = 8800 + len(captured)
            return message

        channel.send = send

    guild = MagicMock()
    guild.id = 1
    guild.get_member.return_value = None
    guild.get_role.return_value = None

    await post_standings(
        db_path=db_path,
        division_id=division_id,
        round_id=round_id,
        round_number=3,
        track_name="Silverstone",
        standings_channel=channel,
        driver_snapshots=[],
        team_snapshots=[],
        guild=guild,
        show_reserves=False,
        label="Final Results",
        bot=bot,
    )


async def test_no_bot_leaves_the_textual_body_exactly_as_it_was(tmp_path):
    """The pre-040 shape: one message carrying both championships."""
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    await _post(db_path, division_id, round_id, captured)

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_the_flow_standing_aside_leaves_the_textual_body_exactly_as_it_was(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(applicable=False)),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_both_graphics_posting_leaves_no_textual_message_at_all(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(fallbacks=())),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert captured == []


async def test_one_championship_falling_back_posts_that_section_and_no_other(tmp_path):
    """FR-052 — never repeat what the surviving graphic already drew."""
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(fallbacks=("drivers",))),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" not in captured[0]


async def test_both_falling_back_posts_each_championship_exactly_once(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(fallbacks=("drivers", "constructors"))),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert len(captured) == 2
    assert sum("Driver Standings" in c for c in captured) == 1
    assert sum("Team Standings" in c for c in captured) == 1


async def test_a_rejected_commanded_posting_posts_nothing_at_all(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(rejects=True)),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert captured == []


async def test_a_cancelled_round_never_enters_the_image_branch(tmp_path):
    """FR-050 — the standings toggle notwithstanding."""
    db_path, division_id, round_id = await _seed(tmp_path, cancelled=True)
    captured: list[str] = []
    spy = AsyncMock(return_value=_outcome(fallbacks=()))

    with patch("services.image_standings_post.try_post", spy):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert spy.await_count == 0
    assert len(captured) == 1, "the textual standings are untouched by the toggle"


async def test_the_image_path_raising_never_costs_the_league_its_standings(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await _post(db_path, division_id, round_id, captured, bot=MagicMock())

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_a_textual_fallback_is_posted_before_the_message_it_replaces_is_deleted(
    tmp_path,
):
    """FR-048 holds for a textual replacement as much as for a graphic."""
    from db.database import get_connection
    from models.standings_snapshot import DriverStandingsSnapshot
    from services.results_post_service import (
        _get_standings_message_id,
        _set_standings_message_id,
        post_standings,
    )

    db_path, division_id, round_id = await _seed(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, "
            "driver_user_id, standing_position, total_points, finish_counts, "
            "first_finish_rounds) VALUES (?, ?, 55, 1, 25, '{}', '{}')",
            (round_id, division_id),
        )
        await db.commit()
    await _set_standings_message_id(db_path, division_id, round_id, 4321, "drivers")

    order: list[str] = []
    channel = AsyncMock()

    async def send(content=None, **kwargs):
        order.append("posted")
        message = MagicMock()
        message.id = 9999
        return message

    async def fetch_message(message_id):
        previous = AsyncMock()
        previous.author.id = 99

        async def delete():
            order.append("deleted")

        previous.delete = delete
        return previous

    def history(*args, **kwargs):
        """No continuation chunks — this message fitted in one."""

        async def _empty():
            return
            yield  # pragma: no cover — makes this a generator

        return _empty()

    channel.send = send
    channel.fetch_message = fetch_message
    channel.history = history

    guild = MagicMock()
    guild.id = 1
    guild.get_member.return_value = None
    guild.get_role.return_value = None

    snapshot = DriverStandingsSnapshot(
        id=1,
        round_id=round_id,
        division_id=division_id,
        driver_user_id=55,
        standing_position=1,
        total_points=25,
        finish_counts={},
        first_finish_rounds={},
    )

    with patch(
        "services.image_standings_post.try_post",
        AsyncMock(return_value=_outcome(fallbacks=("drivers",))),
    ):
        await post_standings(
            db_path=db_path,
            division_id=division_id,
            round_id=round_id,
            round_number=3,
            track_name="Silverstone",
            standings_channel=channel,
            driver_snapshots=[snapshot],
            team_snapshots=[],
            guild=guild,
            show_reserves=False,
            label="Final Results",
            bot=MagicMock(),
        )

    assert order == ["posted", "deleted"]
    assert (
        await _get_standings_message_id(db_path, division_id, round_id, "drivers") == 9999
    )
