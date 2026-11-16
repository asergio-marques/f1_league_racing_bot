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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from leaguebot.core.utils.hooks import AwaitedHook
from tests.support.change_queue import in_a_job
from tests.support.saves import create_image_defaults, set_field


pytestmark = pytest.mark.asyncio

DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"


def _drawn(tmp_path, championship: str) -> Path:
    """A stub PNG named as the render service would name it.

    The attachment a league receives is now the render's own filename — one naming rule,
    in ``leaguebot.image.utils.image_naming``, rather than a literal at each posting site. A stub whose
    name says nothing would let a posting attach the wrong championship's picture and
    still satisfy this file, so the stubs carry real names.
    """
    png = tmp_path / f"season3_division1_round4_standings_{championship}.png"
    png.write_bytes(b"x")
    return png


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


_TEMPLATE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates"
)


def _guild():
    guild = MagicMock()
    guild.id = 1
    # No member is resolvable, so the name chain falls through to its later links rather
    # than to a mock's ``display_name`` — which would put a repr on the graphic.
    guild.get_member.return_value = None
    guild.fetch_member = AsyncMock(return_value=None)
    guild.get_role.return_value = None
    return guild


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


def _drawing(*, module=True, toggle=True, drivers_valid=True, constructors_valid=True,
             post_log=None):
    """The image drawing, over stub services: the images module on or off, the standings toggle
    on or off (or an exception, for a toggle that cannot be read), and each championship's
    template valid or not. Its bot is never read here, `_draw` standing in for the resolving."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    modules = MagicMock()
    modules.is_images_enabled = AsyncMock(return_value=module)
    config = MagicMock()
    if isinstance(toggle, Exception):
        config.get_toggles = AsyncMock(side_effect=toggle)
    else:
        config.get_toggles = AsyncMock(return_value={"standings": toggle})
    validity = MagicMock()
    validity.template_reports = AsyncMock(return_value={
        DRIVERS: MagicMock(valid=drivers_valid, resolved_path=Path(f"{DRIVERS}.svg")),
        CONSTRUCTORS: MagicMock(
            valid=constructors_valid, resolved_path=Path(f"{CONSTRUCTORS}.svg")
        ),
    })
    return ImageDrawing(
        modules, config, validity, renderer=MagicMock(),
        post_log=post_log if post_log is not None else AsyncMock(), db_path="db",
    )


async def _draw(drawing, render, *, build=None):
    """Round 5 of Main drawn as the results module draws it: the gate asked for each template, the
    two championships composed (*build* stands in for the composer, by default giving two plain
    drawings) where either is wanted, and those wanted handed to `draw_standings`, each rendered by
    *render*, which is called as ``render(config, renderer, drawing)``. Not applicable, nothing
    composed, where neither is wanted."""
    from leaguebot.image.services.aspects import image_standings_post as m

    build = build if build is not None else AsyncMock(return_value=_drawings())
    templates = {"drivers": DRIVERS, "constructors": CONSTRUCTORS}
    wanted = {
        championship: await drawing.aspect_wanted("standings", key)
        for championship, key in templates.items()
    }
    if not any(wanted.values()):
        return m.StandingsDrawn()
    composed = await build()
    with patch.object(m, "render_png", render):
        return await drawing.draw_standings(
            {name: each for name, each in zip(templates, composed) if wanted[name]},
            subject="Main round 5",
        )


# ── The gate, asked for each template ─────────────────────────────────────


async def test_the_aspect_is_read_per_template_so_one_faulty_half_does_not_stop_the_other():
    """The standings gate is asked for each template: a sound drivers template is drawn while
    a faulty constructors template is not."""
    drawing = _drawing(constructors_valid=False)

    assert await drawing.aspect_wanted("standings", DRIVERS) is True
    assert await drawing.aspect_wanted("standings", CONSTRUCTORS) is False


async def test_the_module_being_off_stands_the_whole_flow_aside():
    """With the images module off, nothing of the standings is resolved or drawn: the drawing
    hands back not applicable and no line, so the standings' own text runs untouched."""
    build, render = AsyncMock(return_value=_drawings()), AsyncMock()

    drawn = await _draw(_drawing(module=False), render, build=build)

    assert not drawn.applicable and drawn.lines == ()
    assert build.await_count == 0 and render.await_count == 0


async def test_the_toggle_being_off_stands_the_whole_flow_aside():
    build, render = AsyncMock(return_value=_drawings()), AsyncMock()

    drawn = await _draw(_drawing(toggle=False), render, build=build)

    assert not drawn.applicable and drawn.lines == ()
    assert build.await_count == 0 and render.await_count == 0


async def test_a_reader_that_raises_falls_back_rather_than_breaking_the_posting():
    """A standings toggle that cannot be read answers text: not applicable, no line, nothing
    drawn, and nothing raised to the posting."""
    render = AsyncMock()

    drawn = await _draw(_drawing(toggle=RuntimeError("boom")), render)

    assert not drawn.applicable and drawn.lines == ()
    assert render.await_count == 0


async def test_the_whole_flow_stands_aside_when_neither_template_is_wanted():
    """Both templates invalid: the drawing stands aside wholesale, so the caller's untouched
    textual body runs, and nothing is rendered."""
    render = AsyncMock()

    drawn = await _draw(_drawing(drivers_valid=False, constructors_valid=False), render)

    assert not drawn.applicable, "the caller's untouched textual body must run"
    assert drawn.lines == ()
    assert render.await_count == 0


# ── The failure matrix ────────────────────────────────────────────────────


async def test_both_render_so_two_messages_are_posted_and_no_section_falls_back(tmp_path):
    """Both championships draw: each has its picture, named as its render named it, and the
    drawing has nothing to report."""

    async def render(_config, _renderer, drawing):
        return _decision(
            png=_drawn(
                tmp_path,
                "drivers" if drawing.template_key == DRIVERS else "constructors",
            )
        )

    drawn = await _draw(_drawing(), AsyncMock(side_effect=render))

    assert drawn.applicable
    assert drawn.lines == ()
    assert [drawn.drivers.filename, drawn.constructors.filename] == [
        "season3_division1_round4_standings_drivers.png",
        "season3_division1_round4_standings_constructors.png",
    ], "each championship attached under its own render's name"


@pytest.mark.parametrize(
    ("failing_key", "failing", "surviving"),
    [(DRIVERS, "drivers", "constructors"), (CONSTRUCTORS, "constructors", "drivers")],
    ids=["drivers-failing", "constructors-failing"],
)
async def test_a_failing_graphic_falls_back_to_its_own_section_alone(
    tmp_path, failing_key, failing, surviving
):
    """The other graphic still draws, so its table must not be repeated as text: the failing
    championship has no picture, and one line headed by the toggle's name says why."""

    async def render(_config, _renderer, drawing):
        if drawing.template_key == failing_key:
            return _decision(posts=False, problem=MagicMock(detail="no rows"))
        return _decision(png=_drawn(tmp_path, surviving))

    drawn = await _draw(_drawing(), AsyncMock(side_effect=render))

    assert drawn.file_of(failing) is None
    assert (
        drawn.file_of(surviving).filename
        == f"season3_division1_round4_standings_{surviving}.png"
    )
    assert drawn.lines == (f"⚠️ Standings image — Main round 5 — {failing} standings: no rows",)


async def test_both_failing_falls_back_to_both_sections_so_each_is_read_exactly_once():
    """Neither renders: the flow ran (applicable) and drew nothing, so each championship goes
    out as its own text section."""
    drawn = await _draw(
        _drawing(),
        AsyncMock(return_value=_decision(posts=False, problem=MagicMock(detail="bad"))),
    )

    assert drawn.applicable, "the flow ran; it simply drew nothing"
    assert drawn.drivers is None and drawn.constructors is None


async def test_an_invalid_template_falls_that_championship_back_without_rendering(tmp_path):
    """The constructors template is invalid: only the drivers' drawing is rendered, and the
    constructors have no picture and no line."""
    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    render = AsyncMock(return_value=_decision(png=png))

    drawn = await _draw(_drawing(constructors_valid=False), render)

    assert drawn.drivers is not None and drawn.constructors is None
    assert drawn.lines == ()
    assert render.await_count == 1, "the faulty template is never handed to the renderer"
    assert render.await_args.args[2].template_key == DRIVERS


async def test_a_resolution_fault_reports_once_and_falls_both_back():
    """A fault resolving the data behind both championships is raised for the posting to name
    as the reason both texts stand; nothing is rendered and nothing reaches the log channel
    from the drawing."""
    post_log = AsyncMock()
    render = AsyncMock()

    with pytest.raises(RuntimeError, match="no calendar"):
        await _draw(
            _drawing(post_log=post_log), render,
            build=AsyncMock(side_effect=RuntimeError("no calendar")),
        )

    assert render.await_count == 0
    post_log.assert_not_awaited()


async def test_a_constructors_render_that_raises_discards_the_drivers_picture_already_drawn(
    tmp_path,
):
    """The drivers' picture is rendered, then the constructors' render raises (its directories
    cannot be resolved). The fault is raised for the posting to fall both back to text, and the
    drivers' PNG, which nothing will post, is discarded rather than left on disk."""
    png = _render_artifact(tmp_path, "standings_drivers_template")

    async def render(_config, _renderer, drawing):
        if drawing.template_key == DRIVERS:
            return _decision(png=png)
        raise RuntimeError("no team directory")

    with pytest.raises(RuntimeError, match="no team directory"):
        await _draw(_drawing(), AsyncMock(side_effect=render))

    assert not png.exists(), "the drivers' picture outlived a drawing that raised"


# ── Reporting (FR-053) ────────────────────────────────────────────────────


async def test_a_fault_is_reported_to_the_log_channel_naming_the_championship():
    """Each championship that was due and not drawn hands back a line headed by the toggle's
    name, naming the round and the championship, for the job to write; the drawing writes
    nothing to the log channel itself."""
    post_log = AsyncMock()

    drawn = await _draw(
        _drawing(post_log=post_log),
        AsyncMock(return_value=_decision(posts=False, problem=MagicMock(detail="a field is missing"))),
    )

    assert drawn.lines == (
        "⚠️ Standings image — Main round 5 — drivers standings: a field is missing",
        "⚠️ Standings image — Main round 5 — constructors standings: a field is missing",
    )
    post_log.assert_not_awaited()


async def test_notices_are_reported_alongside_a_graphic_that_did_draw(tmp_path):
    """The drivers' picture draws with a render notice: it keeps its picture, and the notice
    comes back as one line naming the round and the championship, nothing posted to the log
    channel by the drawing."""
    from leaguebot.image.models.image_module import RenderNotice

    png = tmp_path / "s.png"
    png.write_bytes(b"x")
    post_log = AsyncMock()
    notice = RenderNotice(
        image_type=DRIVERS, notice_kind="ASSET_FALLBACK_USED", detail="a flag fell back"
    )

    async def render(_config, _renderer, drawing):
        return _decision(png=png, notices=[notice] if drawing.template_key == DRIVERS else [])

    drawn = await _draw(_drawing(post_log=post_log), AsyncMock(side_effect=render))

    assert drawn.drivers is not None
    assert len(drawn.lines) == 1
    assert "a flag fell back" in drawn.lines[0]
    assert "Main round 5 — drivers standings" in drawn.lines[0]
    post_log.assert_not_awaited()


# ── build_drawings, run against a real database ───────────────────────────
#
# Every test above patches ``build_drawings``, which is exactly the shape of coverage that
# let two render bodies ship raising ``NameError`` on every call (see
# ``test_image_post_render_entry_points``). These run it for real, over migrated tables, so
# that a query naming a column that does not exist fails here.


async def _seed_league(tmp_path):
    """A season, a division, two teams with seats, two drivers, a run round and a next one."""
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 4)"
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
                "INSERT INTO rounds (division_id, round_number, format, status, "
                "track_name, scheduled_at) VALUES (?, ?, 'NORMAL', 'FINAL', "
                "'Silverstone', '2026-06-01T18:00:00+00:00')",
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
                "INSERT INTO team_role_configs (team_name, role_id, updated_at) "
                "VALUES (?, ?, '2031-03-04T05:06:07+00:00')",
                (team_name, role_id),
            )
            # The team takes its role's number, so the results below name it either way.
            cursor = await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, ?, 2, 0)",
                (role_id, division_id, team_name, team_name),
            )
            instance_id = cursor.lastrowid
            for user_id, seat_number in drivers:
                cursor = await db.execute(
                    "INSERT INTO driver_profiles (discord_user_id, "
                    "current_state) VALUES (?, 'ACTIVE')",
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
                    "team_instance_id, finishing_position, outcome, points_awarded) "
                    "VALUES (?, ?, ?, ?, 'CLASSIFIED', 10)",
                    (session_id, user_id, role_id, seat_number),
                )
        await db.commit()

    return db_path, division_id, round_ids


def _snapshots(round_id, division_id):
    from leaguebot.results.models.standings_snapshot import DriverStandingsSnapshot, TeamStandingsSnapshot

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
            team_instance_id=role_id,
            standing_position=i,
            total_points=50 - i,
            finish_counts={},
            first_finish_rounds={},
        )
        for i, role_id in enumerate((900, 901), start=1)
    ]
    return drivers, teams


def _hook(db_path):
    """The hook signup answers how a driver is named, as the builder signs it up."""
    from types import SimpleNamespace

    from tests.support.review_workflows import sign_up_driver_names_read

    holder = SimpleNamespace(db_path=db_path)
    sign_up_driver_names_read(holder)
    return holder.driver_names_read


async def test_build_drawings_resolves_both_championships_against_real_tables(tmp_path):
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, constructors = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, constructors = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    drivers, _constructors = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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
    assert leader.cells[1].sessions["feature_race_result"].text != ""
    assert leader.cells[2].sessions["feature_race_result"].text == "", (
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

    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import (
        ImageRenderService,
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings
    from leaguebot.image.services.aspects.image_standings_service import build_fill_spec
    from leaguebot.image.services.validity.image_validity_service import ImageValidityService

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    async def _true(*args, **kwargs):
        return True

    config_service = ImageConfigService(db_path)
    await create_image_defaults(config_service)
    validity_service = ImageValidityService(
        config_service, SimpleNamespace(is_images_enabled=_true), MagicMock()
    )
    bot = SimpleNamespace(
        db_path=db_path,
        image_config_service=config_service,
        image_validity_service=validity_service,
        image_render_service=ImageRenderService(config_service, validity_service),
    )

    drawings = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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

    config = await config_service.get_config()
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
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)

    bot = _bot(db_path)
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())

    _drivers, constructors = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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


# ── The branch inside produce_standings ───────────────────────────────────
#
# The image path is a guard clause in front of an untouched body. Where the flow does not
# run, everything below it behaves exactly as it did before 040; where it does, the textual
# message is composed of whichever championships did *not* draw.


async def _seed(tmp_path, *, cancelled=False):
    """A database holding one season, division and round, ready to post standings for."""
    from leaguebot.core.db.database import get_connection, run_migrations

    db_path = str(tmp_path / "standings.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 2)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) "
            "VALUES (?, 'Beta', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, "
            "scheduled_at) VALUES (?, 3, 'NORMAL', ?, '2026-06-01T18:00:00+00:00')",
            # One chain now: a cancelled round is CANCELLED, and one that ran to the end of
            # its appeals is FINAL. The two used to be separate columns.
            (division_id, "CANCELLED" if cancelled else "FINAL"),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (round_id, division_id),
        )
        await db.commit()
    return db_path, division_id, round_id


async def _seed_previous(db_path, division_id, round_id, **ids):
    """Record the message each championship's standings stand as, e.g. ``drivers=4321``."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.publishing.results_post_service import (
        set_standings_message_id_on,
    )

    async with get_connection(db_path) as db:
        for championship, message_id in ids.items():
            await set_standings_message_id_on(
                db, division_id, round_id, message_id, championship
            )
        await db.commit()


def _standings_drawn(tmp_path, *, drivers=True, constructors=True, applicable=True, pngs=None):
    """What the image drawing hands the standings: nothing applicable, or a picture of each
    championship asked for (from *pngs* where given, else stub PNGs named as the render names
    them) and none for the others."""
    import discord

    from leaguebot.image.services.aspects.image_standings_post import StandingsDrawn

    if not applicable:
        return StandingsDrawn()
    pngs = pngs or {}
    files = {}
    for name, wanted in (("drivers", drivers), ("constructors", constructors)):
        if wanted:
            png = pngs.get(name) or _drawn(tmp_path, name)
            files[name] = discord.File(str(png), filename=png.name)
    return StandingsDrawn(
        applicable=True, drivers=files.get("drivers"), constructors=files.get("constructors"),
    )


def _poster_drawing(drawn=None, *, raises=None):
    """The image drawing the standings are handed, its gate answering yes for both templates, its
    `draw_standings` stubbed to hand back *drawn* (or raise *raises*), and its `discard_attachment`
    the real one."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    drawing = ImageDrawing(
        MagicMock(), MagicMock(), MagicMock(), renderer=MagicMock(), post_log=AsyncMock(),
        db_path="db",
    )
    drawing.aspect_wanted = AsyncMock(return_value=True)
    drawing.draw_standings = AsyncMock(return_value=drawn, side_effect=raises)
    return drawing


def _composer_stood_in():
    """The results module's composer of the two drawings stood in for: the gate is asked first and
    the drawings composed before the drawing, whose `draw_standings` is stubbed, is handed them."""
    return patch(
        "leaguebot.results.services.publishing.standings_drawing_composer.build_drawings",
        new=AsyncMock(return_value=(MagicMock(), MagicMock())),
    )


def _standings_channel(captured, pictures=None, *, order=None, refuse_uploads=False):
    """A standings channel recording each text it is sent in *captured* and each picture, with
    its message text, in *pictures*; each message numbered from 8801 in the order sent. With
    *refuse_uploads* Discord refuses every picture, and takes the text."""
    import itertools

    import discord

    numbers = itertools.count(8801)
    channel = AsyncMock()

    async def send(content=None, **kwargs):
        file = kwargs.get("file")
        if file is not None and refuse_uploads:
            raise discord.HTTPException(MagicMock(status=500, reason="Server Error"), "upload failed")
        if order is not None:
            order.append("posted")
        if file is None:
            captured.append(content)
        elif pictures is not None:
            pictures.append((content, file))
        message = MagicMock()
        message.id = next(numbers)
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


async def _post(db_path, division_id, round_id, captured, *, drawing=None, channel=None,
                pictures=None, occasion=None):
    """Round 3's standings put up by `produce_standings` with *drawing* in scope, in *channel*
    or one recording each text in *captured* and each picture in *pictures*, giving the tables
    it reports."""
    from leaguebot.results.services.publishing.results_post_service import produce_standings

    if channel is None:
        channel = _standings_channel(captured, pictures)

    guild = MagicMock()
    guild.id = 1
    guild.get_member.return_value = None
    guild.get_role.return_value = None

    kwargs = {} if occasion is None else {"occasion": occasion}
    with in_a_job(), _composer_stood_in():
        return await produce_standings(
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
            drawing=drawing,
            **kwargs,
            driver_names_read=AwaitedHook(),
        )


async def test_no_bot_leaves_the_textual_body_exactly_as_it_was(tmp_path):
    """No image drawing in scope: the pre-040 shape, one message carrying both championships."""
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    await _post(db_path, division_id, round_id, captured)

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_the_flow_standing_aside_leaves_the_textual_body_exactly_as_it_was(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    drawing = _poster_drawing(_standings_drawn(tmp_path, applicable=False))
    await _post(db_path, division_id, round_id, captured, drawing=drawing)

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_both_graphics_posting_leaves_no_textual_message_at_all(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []
    pictures: list = []

    drawing = _poster_drawing(_standings_drawn(tmp_path))
    await _post(db_path, division_id, round_id, captured, drawing=drawing, pictures=pictures)

    assert captured == []
    assert [file.filename for _content, file in pictures] == [
        "season3_division1_round4_standings_drivers.png",
        "season3_division1_round4_standings_constructors.png",
    ], "the drivers first, the constructors after, each a message of its own"


async def test_one_championship_falling_back_posts_that_section_and_no_other(tmp_path):
    """FR-052 — never repeat what the surviving graphic already drew."""
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []
    pictures: list = []

    drawing = _poster_drawing(_standings_drawn(tmp_path, drivers=False))
    await _post(db_path, division_id, round_id, captured, drawing=drawing, pictures=pictures)

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" not in captured[0]
    assert len(pictures) == 1


async def test_both_falling_back_posts_each_championship_exactly_once(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    drawing = _poster_drawing(_standings_drawn(tmp_path, drivers=False, constructors=False))
    await _post(db_path, division_id, round_id, captured, drawing=drawing)

    assert len(captured) == 2
    assert sum("Driver Standings" in c for c in captured) == 1
    assert sum("Team Standings" in c for c in captured) == 1


async def test_a_cancelled_round_never_enters_the_image_branch(tmp_path):
    """FR-050 — the standings toggle notwithstanding."""
    db_path, division_id, round_id = await _seed(tmp_path, cancelled=True)
    captured: list[str] = []
    drawing = _poster_drawing(_standings_drawn(tmp_path))

    await _post(db_path, division_id, round_id, captured, drawing=drawing)

    assert drawing.draw_standings.await_count == 0
    assert len(captured) == 1, "the textual standings are untouched by the toggle"


async def test_the_image_path_raising_never_costs_the_league_its_standings(tmp_path):
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []

    drawing = _poster_drawing(raises=RuntimeError("boom"))
    await _post(db_path, division_id, round_id, captured, drawing=drawing)

    assert len(captured) == 1
    assert "Driver Standings" in captured[0]
    assert "Team Standings" in captured[0]


async def test_each_picture_is_reported_as_a_table_with_the_message_it_replaces(tmp_path):
    """Round 3's standings stand as 4321 (drivers) and 4322 (constructors) and are drawn as two
    pictures, posted as 8801 and 8802. `produce_standings` posts no text and reports each
    picture as a table of its own, drawn, with the message it replaces, so that the change
    queue saves the new id with its mark and deletes the old by a job of its own."""
    from leaguebot.results.services.publishing.results_post_service import PostedTable

    db_path, division_id, round_id = await _seed(tmp_path)
    await _seed_previous(db_path, division_id, round_id, drivers=4321, constructors=4322)
    captured: list[str] = []

    tables = await _post(
        db_path, division_id, round_id, captured, drawing=_poster_drawing(_standings_drawn(tmp_path)),
    )

    assert captured == []
    assert tables == [
        PostedTable([8801], [4321], drawn=True, championship="drivers"),
        PostedTable([8802], [4322], drawn=True, championship="constructors"),
    ]


async def test_each_replacement_is_produced_before_its_old_message_is_deleted(tmp_path):
    """FR-048. A render that fails must leave the league the standings it had.

    The poster deletes nothing (#439 6b): it posts each picture and reports the message it
    replaces, which the change queue deletes by a job of its own once the picture stands. It
    saves nothing itself either: the ids recorded before the posting are still recorded."""
    from leaguebot.results.services.publishing.results_post_service import (
        get_standings_message_id,
    )

    db_path, division_id, round_id = await _seed(tmp_path)
    await _seed_previous(db_path, division_id, round_id, drivers=111, constructors=222)
    order: list[str] = []
    channel = _standings_channel([], [], order=order)

    await _post(
        db_path, division_id, round_id, [], channel=channel,
        drawing=_poster_drawing(_standings_drawn(tmp_path)),
    )

    assert order == ["posted", "posted"], "the poster deleted the message it replaces"
    assert await get_standings_message_id(db_path, division_id, round_id, "drivers") == 111
    assert await get_standings_message_id(db_path, division_id, round_id, "constructors") == 222


async def test_a_discord_failure_falls_back_to_text_rather_than_retrying_the_image(tmp_path):
    """FR-056 — the generation succeeded; it is the delivery that did not. Discord refuses both
    pictures: each championship goes out as its own text section in the same try, and each
    table carries the refusal for the job's line."""
    db_path, division_id, round_id = await _seed(tmp_path)
    captured: list[str] = []
    channel = _standings_channel(captured, refuse_uploads=True)

    tables = await _post(
        db_path, division_id, round_id, captured, channel=channel,
        drawing=_poster_drawing(_standings_drawn(tmp_path)),
    )

    assert sum("Driver Standings" in c for c in captured) == 1
    assert sum("Team Standings" in c for c in captured) == 1
    assert not any(table.drawn for table in tables)
    assert {table.championship for table in tables if table.fault} == {"drivers", "constructors"}
    assert all("Discord refused the picture" in table.fault for table in tables if table.fault)


async def test_a_textual_fallback_reports_the_message_it_replaces_and_deletes_nothing(
    tmp_path,
):
    """Round 3's drivers' standings stand as message 4321. The drivers' picture falls back to
    text while the constructors' picture draws: the table goes out as message 9999 and is
    reported with 4321 as the message it replaces, which is left standing and its id left
    recorded, for the change queue's own jobs to save the new id and delete the old once the new
    one stands (FR-048)."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.models.standings_snapshot import DriverStandingsSnapshot
    from leaguebot.results.services.publishing.results_post_service import (
        PostedTable,
        get_standings_message_id,
        produce_standings,
        set_standings_message_id_on,
    )

    db_path, division_id, round_id = await _seed(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, "
            "driver_user_id, standing_position, total_points, finish_counts, "
            "first_finish_rounds) VALUES (?, ?, 55, 1, 25, '{}', '{}')",
            (round_id, division_id),
        )
        await set_standings_message_id_on(db, division_id, round_id, 4321, "drivers")
        await db.commit()

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

    with in_a_job(), _composer_stood_in():
        tables = await produce_standings(
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
            drawing=_poster_drawing(_standings_drawn(tmp_path, drivers=False)),
            driver_names_read=AwaitedHook(),
        )

    assert "deleted" not in order
    assert PostedTable([9999], [4321], championship="drivers") in tables
    assert await get_standings_message_id(db_path, division_id, round_id) == 4321


# ── The rendered files do not outlive the posting attempt ─────────────────


def _render_artifact(tmp_path, name):
    """A PNG sitting where `render` puts one, so the ownership guard recognises it."""
    directory = tmp_path / f"f1bot_render_{name}"
    directory.mkdir()
    png = directory / f"{name}.png"
    png.write_bytes(b"\x89PNG")
    return png


async def test_both_championship_files_are_gone_once_they_have_posted(tmp_path):
    """Two renders per round, and neither is read again after its own send."""
    db_path, division_id, round_id = await _seed(tmp_path)
    pngs = {"drivers": _render_artifact(tmp_path, "standings_drivers_template"),
            "constructors": _render_artifact(tmp_path, "standings_constructors_template")}

    await _post(
        db_path, division_id, round_id, [],
        drawing=_poster_drawing(_standings_drawn(tmp_path, pngs=pngs)),
    )

    for png in pngs.values():
        assert not png.exists(), f"{png.name} outlived its posting"
        assert not png.parent.exists()


async def test_the_rendered_file_is_gone_when_the_send_fails(tmp_path):
    """The textual standings fall back; the picture is not kept for a retry (FR-056)."""
    db_path, division_id, round_id = await _seed(tmp_path)
    pngs = {"drivers": _render_artifact(tmp_path, "standings_drivers_template"),
            "constructors": _render_artifact(tmp_path, "standings_constructors_template")}

    await _post(
        db_path, division_id, round_id, [],
        channel=_standings_channel([], refuse_uploads=True),
        drawing=_poster_drawing(_standings_drawn(tmp_path, pngs=pngs)),
    )

    for png in pngs.values():
        assert not png.exists(), "a failed upload must not strand the picture"


# ── The highlight chips, through the posting path's own drawings ──────────


def _chip_faults(result) -> list[str]:
    """Whatever the fill could not resolve that names a highlight chip."""
    return [
        fault
        for fault in result.unresolved
        if "_background" in fault or "_fastest_lap" in fault
    ]


async def _highlighted_svg(tmp_path):
    """The posting path's real drawings, filled onto the shipped templates.

    No rasteriser is touched, so this runs in CI: `build_drawings` reads the database and
    `build_fill_spec` resolves the chips against the packaged artwork. The pixels are checked
    separately by the marked test below.

    **The marker directory is pinned to the packaged one, and must stay pinned** (2026-09-02).
    The defaults give every asset class the directory migration 043 set, which is
    `resources/league/<class>` — gitignored, unversioned, and holding whatever artwork the
    machine running the suite happens to carry. This fixture claims above to resolve "against
    the packaged artwork", and until it was pinned that claim was true only on a host whose
    league folder was empty: a league that had drawn three of its own marks, which the README
    explicitly invites, turned the marked test below red with a plate colour of its own. The
    `rasteriser` marker keeps that test out of CI, so nothing else would ever have caught it.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.image.models.image_constants import packaged_directory_for
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import resolve_configured_directories
    from leaguebot.results.services.publishing.standings_drawing_composer import build_drawings
    from leaguebot.image.services.aspects.image_standings_service import build_fill_spec
    from leaguebot.image.utils.svg_document import load_svg
    from leaguebot.image.utils.svg_fill import fill

    db_path, division_id, round_ids = await _seed_league(tmp_path)
    # The seed classifies a race but awards no fastest lap and runs no qualifying. Add both,
    # so round 1 holds a cell carrying all three marks at once — a win from pole with the
    # fastest lap, which is the busiest a cell gets and the case the layering exists for.
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE race_session_results SET fastest_lap_bonus = 1 WHERE driver_user_id = 11"
        )
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE')",
            (round_ids[0], division_id),
        )
        qualifying_id = cursor.lastrowid
        for user_id, role_id, position in ((11, 900, 1), (12, 900, 2), (13, 901, 1)):
            await db.execute(
                "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, points_awarded) "
                "VALUES (?, ?, ?, ?, 'CLASSIFIED', 3)",
                (qualifying_id, user_id, role_id, position),
            )
        await db.commit()

    driver_snaps, team_snaps = _snapshots(round_ids[0], division_id)
    bot = _bot(db_path)
    config_service = ImageConfigService(db_path)
    await create_image_defaults(config_service)
    # See the docstring: the default points into the league's own gitignored folder.
    await set_field(config_service, "marker_directory", packaged_directory_for("marker"))
    bot.image_config_service = config_service

    drawings = await build_drawings(
        _guild(),
        driver_names_read=_hook(db_path),
        db_path=db_path,
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

    config = await config_service.get_config()
    out = {}
    for drawing in drawings:
        directories, _faults = resolve_configured_directories(
            config,
            (("marker", "marker_directory"),),
            image_type=drawing.template_key,
        )
        doc = load_svg(os.path.join(_TEMPLATE_DIR, f"{drawing.template_key}.svg"))
        root = doc.root if hasattr(doc, "root") else doc
        spec = build_fill_spec(drawing, root, asset_directories=directories)
        out[drawing.template_key] = (root, spec, fill(spec))
    return out


async def test_the_winner_is_given_the_first_place_chip(tmp_path):
    """End to end within CI: a real classification reaches the packaged artwork."""
    root, spec, result = (await _highlighted_svg(tmp_path))["standings_drivers_template"]
    assert _chip_faults(result) == [], _chip_faults(result)

    assert spec.image_data["row_1_round_1_feature_race_background"] == (
        "marker",
        "race_p1",
    )
    assert spec.image_data["row_1_round_1_feature_race_fastest_lap"] == (
        "marker",
        "race_fastest_lap",
    )


async def test_the_chip_slot_ends_up_pointing_at_the_packaged_file(tmp_path):
    """The datum is resolved through the class's directory, not by a path built here."""
    from leaguebot.image.utils.svg_document import FieldIndex

    root, _spec, _result = (await _highlighted_svg(tmp_path))["standings_drivers_template"]
    slot = FieldIndex(root).resolve("row_1_round_1_feature_race_background")
    href = slot.get("href") or slot.get("{http://www.w3.org/1999/xlink}href")
    assert href and href.endswith("markers/race_p1.svg"), href


async def test_a_cell_that_earns_nothing_is_left_without_an_href(tmp_path):
    """Round 2 has not been run, so its slots must still draw nothing."""
    from leaguebot.image.utils.svg_document import FieldIndex

    root, _spec, _result = (await _highlighted_svg(tmp_path))["standings_drivers_template"]
    slot = FieldIndex(root).resolve("row_1_round_2_feature_race_background")
    assert slot is not None, "the slot was removed; it should simply have been left alone"
    assert not slot.get("href")
    assert not slot.get("{http://www.w3.org/1999/xlink}href")


async def test_the_constructors_cars_carry_the_chips_too(tmp_path):
    root, spec, result = (await _highlighted_svg(tmp_path))["standings_constructors_template"]
    assert _chip_faults(result) == [], _chip_faults(result)
    assert spec.image_data["row_1_round_1_driver_1_feature_race_background"] == (
        "marker",
        "race_p1",
    )


@pytest.mark.rasteriser
async def test_the_three_marks_reach_the_raster_in_their_own_corners(tmp_path):
    """Rule XIV.14 — the marks are verified as pixels, never as markup.

    The tests above prove the right assets were chosen and the hrefs anchored. Only the raster
    proves they were *drawn where the artwork says*: an href the rasteriser cannot follow, a
    slot authored after its text, or a `preserveAspectRatio` that letterboxes instead of
    stretching, all leave correct-looking markup and a wrong picture. So does a triangle that
    did not move when its file said it should.

    Every coordinate is read out of the template rather than assumed, and the samples are taken
    well clear of the glyph, whose width depends on which font the host resolved.
    """
    from PIL import Image  # noqa: PLC0415

    from leaguebot.image.services.render.image_render_service import rasterise
    from leaguebot.image.utils.svg_document import FieldIndex, canvas_of
    from leaguebot.image.utils.svg_fill import fill as fill_spec_onto

    root, spec, _ = (await _highlighted_svg(tmp_path))["standings_drivers_template"]
    chip = FieldIndex(root).resolve("row_1_round_1_feature_race_background")
    left, top = float(chip.get("x")), float(chip.get("y"))
    width, height = float(chip.get("width")), float(chip.get("height"))

    result = fill_spec_onto(spec)
    png = rasterise(result.svg, tmp_path / "standings.png", result.canvas or canvas_of(root))
    image = Image.open(png).convert("RGB")

    def near(pixel, expected, tolerance=6):
        return all(abs(a - b) <= tolerance for a, b in zip(pixel, expected))

    # The plate and the mark are one step apart in **lightness**, within one hue: a winner who
    # also took pole shows the mark merging into the plate, which is what a matched pair should
    # do, while the same gold mark over a *bronze* plate stays plainly gold. Lightness is what
    # the eye reads an edge from at this size — an earlier palette separated mark from plate by
    # hue alone and the gold mark looked like a stain on the bronze plate.
    plate_gold = (0xE6, 0xC5, 0x5A)
    purple = (0xA0, 0x20, 0xF0)
    mark_gold = (0xC9, 0xA2, 0x27)

    # The p1 plate, sampled low and central — clear of the text and of both corner marks.
    assert image.getpixel((int(left + width / 2), int(top + height) - 3)) == plate_gold

    # The fastest lap is a triangle in the **top-left**, moved there so the qualifying mark
    # can have the corner nearest the raised figure it stands for.
    assert image.getpixel((int(left) + 3, int(top) + 2)) == purple

    # The qualifying mark is a triangle in the **top-right**, drawn over the plate a shade
    # darker than it — enough to be seen, little enough to merge where the two agree.
    corner = image.getpixel((int(left + width) - 3, int(top) + 2))
    assert near(corner, mark_gold), f"top-right corner was {corner}, not the qualifying mark"

    # Two rows below the chip is the plain row band, which no mark reaches.
    below = image.getpixel((int(left) + 6, int(top + height) + 8))
    assert not any(near(below, c) for c in (plate_gold, purple, mark_gold))


# ── The season's two boundaries ───────────────────────────────────────────
#
# The opening and final classifications are the same two graphics under a different
# heading. What differs at the posting site is that they carry no message text and take
# part in no replacement — see `leaguebot.core.models.classification_occasion`.


async def _boundary(tmp_path, occasion, **previous):
    """Round 3's division's two standings pictures posted for *occasion*, with *previous* ids
    recorded on the round beforehand; the tables reported and the pictures sent."""
    db_path, division_id, round_id = await _seed(tmp_path)
    if previous:
        await _seed_previous(db_path, division_id, round_id, **previous)
    pictures: list = []
    tables = await _post(
        db_path, division_id, round_id, [], pictures=pictures, occasion=occasion,
        drawing=_poster_drawing(_standings_drawn(tmp_path)),
    )
    return (db_path, division_id, round_id), tables, pictures


@pytest.mark.parametrize("occasion_name", ["SEASON_OPENING", "SEASON_FINAL"])
async def test_an_opening_posting_carries_no_message_text(tmp_path, occasion_name):
    """The phrase is drawn on the sheet; a heading above it would say it twice. The
    season's final classification is drawn the same way."""
    from leaguebot.core.models.classification_occasion import ClassificationOccasion

    _where, _tables, pictures = await _boundary(
        tmp_path, getattr(ClassificationOccasion, occasion_name)
    )

    assert len(pictures) == 2
    assert [content for content, _file in pictures] == [None, None]
    assert all(file is not None for _content, file in pictures)


async def test_an_ordinary_posting_still_carries_its_heading_and_label(tmp_path):
    """The occasion that predates the enum is untouched by it: each picture goes out under the
    round's heading and the lifecycle label."""
    db_path, division_id, round_id = await _seed(tmp_path)
    pictures: list = []

    await _post(
        db_path, division_id, round_id, [], pictures=pictures,
        drawing=_poster_drawing(_standings_drawn(tmp_path)),
    )

    contents = [content for content, _file in pictures]
    assert len(contents) == 2 and contents[0] == contents[1]
    assert contents[0].startswith("**Season 2 Beta Round 3 — ")
    assert contents[0].endswith("**\nFinal Results")


@pytest.mark.parametrize("occasion_name", ["SEASON_OPENING", "SEASON_FINAL"])
async def test_a_boundary_posting_neither_reads_nor_writes_the_round_s_slot(
    tmp_path, occasion_name
):
    """It has no round, so no snapshot row carries an id for it — and none is written.

    Writing one would key the season's opening or closing word to a round it does not
    belong to, and reading one would have it delete that round's own standings.
    """
    from leaguebot.core.models.classification_occasion import ClassificationOccasion
    from leaguebot.results.services.publishing.results_post_service import (
        get_standings_message_id,
    )

    (db_path, division_id, round_id), tables, pictures = await _boundary(
        tmp_path,
        getattr(ClassificationOccasion, occasion_name),
        drivers=4242,
        constructors=4343,
    )

    assert all(table.old == [] for table in tables), "and must replace nothing it did not post"
    assert len(pictures) == 2
    assert await get_standings_message_id(db_path, division_id, round_id, "drivers") == 4242, (
        "a boundary posting must claim no round's slot"
    )
    assert await get_standings_message_id(db_path, division_id, round_id, "constructors") == 4343
