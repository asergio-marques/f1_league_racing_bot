"""The season a review described, and whether it is still that season at the button.

`/season review` fingerprints everything it reports; the Approve button refuses unless the
season still fingerprints the same. That is what lets the approval trust the review's own
render instead of drawing every graphic a second time.

**Every area gets a named case.** An area quietly dropped from the digest is a change that
slips through with nothing failing, so each is changed in turn and asserted to be both
detected *and named*. That is the whole value of the per-area digest over one hash, and it
is the test that stops the coverage rotting.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.season_fingerprint_service import (  # noqa: E402
    AREA_LABELS,
    SeasonFingerprint,
    take_fingerprint,
)

SERVER_ID = 8811


@pytest.fixture
async def season(tmp_path):
    """A configured server with one season, one division, a team, a seat and a round."""
    path = str(tmp_path / "fingerprint.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, 'Pro', 10, 'ACTIVE', 1)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Redline', 2, 0)",
            (division_id,),
        )
        team_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
            (team_id,),
        )
        seat_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
            "former_driver, is_test_driver) VALUES (?, '123', 'ASSIGNED', 0, 0)",
            (SERVER_ID,),
        )
        profile_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
            (profile_id, season_id, division_id, seat_id),
        )
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, 1, 'NORMAL', 'Hungaroring', '2026-06-14T18:00:00')",
            (division_id,),
        )
        await db.execute("INSERT INTO image_config (server_id) VALUES (?)", (SERVER_ID,))
        await db.commit()

    bot = SimpleNamespace(db_path=path, image_config_service=ImageConfigService(path))
    return SimpleNamespace(
        bot=bot, path=path, season_id=season_id, division_id=division_id
    )


async def _take(season) -> SeasonFingerprint:
    return await take_fingerprint(season.bot, SERVER_ID, season.season_id)


async def _change(season, sql: str, *params) -> None:
    async with get_connection(season.path) as db:
        await db.execute(sql, params)
        await db.commit()


# ── The fingerprint itself ────────────────────────────────────────────────


async def test_every_area_is_covered(season):
    """A missing area is a change that would slip through unnoticed."""
    fingerprint = await _take(season)

    assert set(fingerprint.areas) == set(AREA_LABELS)


async def test_an_unchanged_season_fingerprints_the_same_twice(season):
    """Taken twice with nothing between, it must not report a change nobody made."""
    first = await _take(season)
    second = await _take(season)

    assert first.differs_from(second) == []


async def test_a_fingerprint_that_could_not_be_taken_differs_from_everything(season):
    """A fault refuses an approval rather than waving it through."""
    empty = SeasonFingerprint({})

    assert empty.differs_from(await _take(season))


async def test_an_unreadable_season_yields_an_empty_fingerprint():
    """Same reasoning, reached through the real failure path."""
    bot = SimpleNamespace(db_path="/nonexistent/nowhere.db", image_config_service=None)

    assert (await take_fingerprint(bot, SERVER_ID, 1)).areas == {}


# ── One case per area ─────────────────────────────────────────────────────


async def _assert_only(season, before, area: str, sql: str, *params):
    """Change one thing and assert exactly its area is named."""
    await _change(season, sql, *params)

    changed = before.differs_from(await _take(season))

    assert AREA_LABELS[area] in changed, f"{area} went undetected"
    assert changed == [AREA_LABELS[area]], f"{area} also disturbed {changed}"


async def test_the_season_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "season",
        "UPDATE seasons SET game_edition = 26 WHERE id = ?", season.season_id,
    )


async def test_the_divisions_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "divisions",
        "UPDATE divisions SET name = 'Elite' WHERE id = ?", season.division_id,
    )


async def test_the_rounds_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "rounds",
        "UPDATE rounds SET scheduled_at = '2026-06-21T18:00:00' WHERE division_id = ?",
        season.division_id,
    )


async def test_the_teams_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "teams",
        "UPDATE team_instances SET max_seats = 3 WHERE division_id = ?",
        season.division_id,
    )


async def test_the_drivers_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "drivers",
        "UPDATE driver_season_assignments SET team_seat_id = NULL WHERE season_id = ?",
        season.season_id,
    )


async def test_the_channels_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "channels",
        "UPDATE divisions SET calendar_channel_id = 999 WHERE id = ?",
        season.division_id,
    )


async def test_the_modules_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "modules",
        "UPDATE server_configs SET weather_module_enabled = 1 WHERE server_id = ?",
        SERVER_ID,
    )


async def test_the_points_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "points",
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
        season.season_id,
    )


async def test_the_signup_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "signup",
        "INSERT INTO signup_module_config (server_id, signup_channel_id) VALUES (?, 77)",
        SERVER_ID,
    )


async def test_the_attendance_area(season):
    """Changed on an existing row, so the module switch it shares with `modules` is not
    also disturbed — inserting the row would move both, correctly but less precisely."""
    await _change(
        season,
        "INSERT INTO attendance_config (server_id, rsvp_notice_days) VALUES (?, 5)",
        SERVER_ID,
    )
    before = await _take(season)
    await _assert_only(
        season, before, "attendance",
        "UPDATE attendance_config SET rsvp_notice_days = 9 WHERE server_id = ?",
        SERVER_ID,
    )


async def test_the_weather_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "weather",
        "INSERT INTO weather_pipeline_config (server_id, phase_1_days) VALUES (?, 7)",
        SERVER_ID,
    )


async def test_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "UPDATE image_config SET date_format = 'YYYY_MM_DD' WHERE server_id = ?",
        SERVER_ID,
    )


async def test_an_aspect_toggle_is_part_of_the_images_area(season):
    before = await _take(season)
    await _assert_only(
        season, before, "images",
        "INSERT INTO image_aspect_toggles (server_id, aspect, enabled) "
        "VALUES (?, 'calendar', 1)",
        SERVER_ID,
    )


# ── The artwork on disk ───────────────────────────────────────────────────
#
# The one area a database read cannot see. A template edited or deleted between the review
# and the button would otherwise pass, which is exactly what the withdrawn render caught.


@pytest.fixture
async def with_templates(season, tmp_path):
    """Point the season at a template folder inside the project root."""
    import shutil
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    folder = root / "resources" / "_test_fingerprint_templates"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for name in os.listdir(root / "resources" / "defaults" / "templates"):
        shutil.copy(root / "resources" / "defaults" / "templates" / name, folder)

    await _change(
        season,
        "UPDATE image_config SET template_directory = ? WHERE server_id = ?",
        "resources/_test_fingerprint_templates",
        SERVER_ID,
    )
    yield season, folder
    shutil.rmtree(folder, ignore_errors=True)


async def test_a_template_edited_on_disk_is_noticed(with_templates):
    """No column changed; only the file did."""
    season, folder = with_templates
    before = await _take(season)

    target = folder / "calendar_template.svg"
    target.write_text(target.read_text(encoding="utf-8") + "<!-- edited -->", encoding="utf-8")

    changed = before.differs_from(await _take(season))

    assert changed == [AREA_LABELS["artwork"]]


async def test_a_template_deleted_is_noticed(with_templates):
    season, folder = with_templates
    before = await _take(season)

    (folder / "lineup_template.svg").unlink()

    assert AREA_LABELS["artwork"] in before.differs_from(await _take(season))


async def test_artwork_added_to_an_asset_folder_is_noticed(season, tmp_path):
    """A flag dropped in between review and approval changes what a graphic draws."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    folder = root / "resources" / "_test_fingerprint_flags"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        await _change(
            season,
            "UPDATE image_config SET flag_directory = ? WHERE server_id = ?",
            "resources/_test_fingerprint_flags",
            SERVER_ID,
        )
        before = await _take(season)

        (folder / "palestine.svg").write_text("<svg/>", encoding="utf-8")

        assert AREA_LABELS["artwork"] in before.differs_from(await _take(season))
    finally:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


# ── Several changes at once ───────────────────────────────────────────────


async def test_two_changed_areas_are_both_named(season):
    before = await _take(season)

    await _change(
        season, "UPDATE divisions SET name = 'Elite' WHERE id = ?", season.division_id
    )
    await _change(
        season,
        "UPDATE rounds SET track_name = 'Albert Park' WHERE division_id = ?",
        season.division_id,
    )

    changed = before.differs_from(await _take(season))

    assert AREA_LABELS["divisions"] in changed
    assert AREA_LABELS["rounds"] in changed
