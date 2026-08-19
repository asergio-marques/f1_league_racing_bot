"""The fabricated league a season-less server draws (046).

Feature 045 refused every preview where there was no season. This suite covers what now
happens instead: a whole league invented in memory over the server's *own* configured
teams, flowed through all eleven builders unchanged.

Two things are asserted harder than the rest. That every builder produces **no problem
outcome** over an invented context — Rule XIV.3 applied to data the feature itself made up,
and the largest correctness risk in the feature. And that nothing is written: a preview
leaves the server's records exactly as it found them.

No Discord anywhere. The rasteriser is not invoked either; the builders are exercised to the
point of assembling their fill specs, which is where invented data would fail.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.image_constants import PREVIEW_KINDS  # noqa: E402
from services.image_config_service import ImageConfigService  # noqa: E402
from services.image_preview_league import build_fabricated_context  # noqa: E402
from services.image_preview_service import (  # noqa: E402
    REASON_NO_SERVER_TEAMS,
    PreviewRefused,
    resolve_context,
)
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 7373

#: Pinned alongside every seed. The fabricated calendar is dated relative to "now", so a
#: test that seeded the randomness but let the clock run would pass today and fail later.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

SEED = 20260819


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "fabricated.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_configs (server_id, interaction_role_id, "
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


async def _configure_teams(db_path, teams=(("Redline", 2), ("Bluewave", 2), ("Vertex", 2))):
    """The server's own team list — the one part of a fabricated league that is not made up."""
    async with get_connection(db_path) as db:
        for name, seats in teams:
            await db.execute(
                "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 0)",
                (SERVER_ID, name, seats),
            )
        # Every server carries the protected Reserve team, which is never fielded.
        await db.execute(
            "INSERT INTO default_teams (server_id, name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 4, 1)",
            (SERVER_ID,),
        )
        await db.commit()


async def _seed_season(db_path, status: str, number: int):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, ?, ?, ?)",
            (SERVER_ID, NOW.date().isoformat(), status, number),
        )
        await db.commit()


def _fabricate(bot, kind="calendar", *, seed=SEED):
    return build_fabricated_context(
        bot, SERVER_ID, kind=kind, rng=random.Random(seed), now=NOW
    )


# ── T018: the shape of an invented league ─────────────────────────────────


class TestTheFabricatedLeague:
    async def test_it_is_marked_as_fabricated(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        assert context.fabricated_league is True
        assert context.season_pending_approval is False

    async def test_the_teams_are_the_servers_own(self, bot, db_path):
        """FR-011. The one part not invented — a lineup template names real teams."""
        await _configure_teams(db_path)

        context = await _fabricate(bot, "lineup")

        assert sorted(t.name for t in context.teams) == ["Bluewave", "Redline", "Vertex"]

    async def test_the_reserve_team_is_excluded(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot, "lineup")

        assert all(not t.is_reserve for t in context.teams)
        assert "Reserve" not in {t.name for t in context.teams}

    async def test_each_team_carries_its_configured_seat_count(self, bot, db_path):
        await _configure_teams(db_path, (("Solo", 1), ("Trio", 3)))

        context = await _fabricate(bot, "lineup")

        seats = {t.name: len(t.seats) for t in context.teams}
        assert seats == {"Solo": 1, "Trio": 3}

    async def test_every_seat_is_filled(self, bot, db_path):
        """FR-019 — an empty grid would tell a manager nothing."""
        await _configure_teams(db_path)

        context = await _fabricate(bot, "lineup")

        for team in context.teams:
            for seat in team.seats:
                assert seat.server_display_name
        assert len(context.drivers) == 6
        assert all(d.fabricated for d in context.drivers)

    async def test_the_division_is_invented(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        assert context.division_name
        assert 1 <= context.division_tier <= 5

    async def test_the_calendar_holds_more_than_one_format(self, bot, db_path):
        """FR-015 — a manager must be able to judge every format marker in one picture."""
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        assert len(context.rounds) > 1
        assert len({r.format for r in context.rounds}) > 1

    async def test_the_rounds_are_numbered_from_one(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        numbers = [r.round_number for r in context.rounds]
        assert numbers == list(range(1, len(numbers) + 1))

    async def test_a_mystery_round_conceals_its_track(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        for entry in context.rounds:
            if entry.format == "MYSTERY":
                assert entry.track_name is None

    async def test_the_nationality_switch_is_honoured(self, bot, db_path):
        """FR-020 — no nationality invented where the league collects none."""
        await _configure_teams(db_path)
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required) "
                "VALUES (?, 0)",
                (SERVER_ID,),
            )
            await db.commit()

        context = await _fabricate(bot, "lineup")

        assert context.nationality_collected is False
        assert all(d.nationality is None for d in context.drivers)


# ── T017 companion: the season number ─────────────────────────────────────


class TestTheSeasonNumber:
    async def test_a_server_that_has_never_held_a_season_draws_season_one(
        self, bot, db_path
    ):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        assert context.season_number == 1

    async def test_it_counts_on_from_the_last_committed_season(self, bot, db_path):
        """FR-010 — one higher than the highest number already committed."""
        await _configure_teams(db_path)
        for number in (1, 2, 3, 4):
            await _seed_season(db_path, "COMPLETED", number)

        context = await _fabricate(bot)

        assert context.season_number == 5


# ── T019: the round suits the kind ────────────────────────────────────────


class TestTheRoundSuitsTheKind:
    """FR-017. A preview of an invented league is never refused for the format of a round
    the feature itself invented — so this is asserted over many seeds, not one."""

    @pytest.mark.parametrize("seed", range(25))
    async def test_the_mystery_notice_always_gets_a_mystery_round(
        self, bot, db_path, seed
    ):
        await _configure_teams(db_path)

        context = await _fabricate(bot, "weather-mystery", seed=seed)

        assert context.round is not None
        assert context.round.format == "MYSTERY"

    @pytest.mark.parametrize("seed", range(25))
    @pytest.mark.parametrize("kind", ["weather-p1", "weather-p2", "weather-p3"])
    async def test_a_forecast_never_gets_a_mystery_round(self, bot, db_path, seed, kind):
        await _configure_teams(db_path)

        context = await _fabricate(bot, kind, seed=seed)

        assert context.round is not None
        assert context.round.format != "MYSTERY"

    async def test_the_two_kinds_that_take_no_round_get_none(self, bot, db_path):
        await _configure_teams(db_path)

        for kind in ("calendar", "lineup"):
            context = await _fabricate(bot, kind)
            assert context.round is None, kind

    async def test_the_round_drawn_is_one_of_the_calendars_own(self, bot, db_path):
        """FR-016 — and carries the number it holds there."""
        await _configure_teams(db_path)

        context = await _fabricate(bot, "results")

        assert context.round in context.rounds
        assert context.rounds[context.round.round_number - 1] is context.round


# ── T020: randomised afresh ───────────────────────────────────────────────


class TestFreshnessPerInvocation:
    async def test_two_unseeded_invocations_differ(self, bot, db_path):
        """FR-014, SC-007. Deliberately unseeded — the point is the absence of a seed."""
        await _configure_teams(db_path)

        runs = [
            await build_fabricated_context(bot, SERVER_ID, kind="results")
            for _ in range(6)
        ]

        # Any one of these could coincide by chance; all six agreeing could not.
        divisions = {r.division_name for r in runs}
        calendars = {tuple(x.track_name for x in r.rounds) for r in runs}
        drivers = {tuple(d.display_name for d in r.drivers) for r in runs}
        assert len(divisions) > 1 or len(calendars) > 1
        assert len(calendars) > 1
        assert len(drivers) > 1

    async def test_the_team_names_agree_across_invocations(self, bot, db_path):
        """They are read, not invented, so they are the one thing that must not vary."""
        await _configure_teams(db_path)

        runs = [
            await build_fabricated_context(bot, SERVER_ID, kind="lineup")
            for _ in range(4)
        ]

        names = {tuple(sorted(t.name for t in r.teams)) for r in runs}
        assert names == {("Bluewave", "Redline", "Vertex")}

    async def test_a_seed_makes_it_reproducible(self, bot, db_path):
        """The seam the rest of this suite depends on."""
        await _configure_teams(db_path)

        first = await _fabricate(bot, "results")
        second = await _fabricate(bot, "results")

        assert first.division_name == second.division_name
        assert [r.track_name for r in first.rounds] == [
            r.track_name for r in second.rounds
        ]
        assert [d.display_name for d in first.drivers] == [
            d.display_name for d in second.drivers
        ]


# ── T021: the track is a real one ─────────────────────────────────────────


class TestTheTracksAreReal:
    async def test_every_fabricated_track_is_one_the_bot_carries(self, bot, db_path):
        """FR-018 — an invented name would miss both its image and its flag, and the
        fallback report would say nothing about the league's own configuration."""
        await _configure_teams(db_path)
        async with get_connection(db_path) as db:
            rows = await (await db.execute("SELECT name FROM tracks")).fetchall()
        known = {row["name"] for row in rows}

        context = await _fabricate(bot)

        drawn = {r.track_name for r in context.rounds if r.track_name is not None}
        assert drawn
        assert drawn <= known

    async def test_no_track_is_drawn_twice(self, bot, db_path):
        await _configure_teams(db_path)

        context = await _fabricate(bot)

        drawn = [r.track_name for r in context.rounds if r.track_name is not None]
        assert len(drawn) == len(set(drawn))

    async def test_the_country_resolves_for_a_fabricated_round(self, bot, db_path):
        """Which is the whole reason for using real names."""
        from services.image_preview_service import _country_of

        await _configure_teams(db_path)
        context = await _fabricate(bot, "rsvp")

        assert await _country_of(bot, context.round) is not None


# ── T022: the team-list split ─────────────────────────────────────────────


class TestABareServerSplitsSixFromFive:
    """FR-012. The correction made on 2026-08-19: six kinds draw, five refuse.

    `rsvp` draws no roster and `verdict` does, which is the pair a reading of feature 045
    gets wrong.
    """

    @pytest.mark.parametrize(
        "kind", ["lineup", "results", "standings", "attendance", "verdict"]
    )
    async def test_the_roster_drawing_kinds_are_refused(self, bot, db_path, kind):
        with pytest.raises(PreviewRefused) as caught:
            await _fabricate(bot, kind)

        assert caught.value.reason == REASON_NO_SERVER_TEAMS
        assert "/team add" in caught.value.message

    @pytest.mark.parametrize(
        "kind",
        [
            "calendar",
            "rsvp",
            "weather-p1",
            "weather-p2",
            "weather-p3",
            "weather-mystery",
        ],
    )
    async def test_the_roster_free_kinds_still_draw(self, bot, db_path, kind):
        context = await _fabricate(bot, kind)

        assert context.fabricated_league is True
        assert context.teams == []

    async def test_the_refusal_is_distinct_from_a_division_holding_no_team(self, bot):
        """SC-003 — a manager must be able to tell the two apart from the message."""
        from services.image_preview_service import REASON_NO_TEAMS

        assert REASON_NO_SERVER_TEAMS != REASON_NO_TEAMS

    async def test_only_the_reserve_team_counts_as_bare(self, bot, db_path):
        await _configure_teams(db_path, ())

        with pytest.raises(PreviewRefused) as caught:
            await _fabricate(bot, "lineup")
        assert caught.value.reason == REASON_NO_SERVER_TEAMS


# ── T030: every builder survives an invented league ───────────────────────


class TestEveryBuilderDrawsAFabricatedLeague:
    """Rule XIV.3 applied to data the feature itself invented.

    Every mandatory field of all eleven catalogues must be resolvable from a context with
    no database row behind it. A miss shows up as the render abandoning, so this asserts
    the builders assemble at all — which is where invented data fails, before any
    rasterising.
    """

    @pytest.mark.parametrize("kind", sorted(PREVIEW_KINDS))
    async def test_the_builder_assembles(self, bot, db_path, kind):
        from services import image_preview_service as svc

        await _configure_teams(db_path)
        context = await _fabricate(bot, kind)

        builders = {
            "calendar": svc.build_calendar_preview,
            "lineup": svc.build_lineup_preview,
            "results": svc.build_results_preview,
            "standings": svc.build_standings_preview,
            "attendance": svc.build_attendance_preview,
            "verdict": svc.build_verdict_preview,
            "rsvp": svc.build_rsvp_preview,
        }
        if kind.startswith("weather-"):
            phase = {"weather-p1": 1, "weather-p2": 2, "weather-p3": 3}.get(kind, 0)
            requests = await svc.build_weather_preview(bot, context, phase=phase)
        else:
            requests = await builders[kind](bot, context)

        assert requests, kind
        for label, template_key, spec_builder in requests:
            assert label
            assert template_key
            assert callable(spec_builder)


# ── T024: nothing is written ──────────────────────────────────────────────


async def _snapshot(db_path) -> dict[str, list]:
    async with get_connection(db_path) as db:
        tables = await (
            await db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ).fetchall()
        state = {}
        for row in tables:
            name = row["name"]
            rows = await (await db.execute(f"SELECT * FROM {name}")).fetchall()
            state[name] = [tuple(r) for r in rows]
    return state


class TestNothingIsWritten:
    async def test_running_every_preview_leaves_the_records_untouched(self, bot, db_path):
        """FR-025, SC-009. Asserted over every table rather than the ones suspected."""
        await _configure_teams(db_path)
        before = await _snapshot(db_path)

        for kind in sorted(PREVIEW_KINDS):
            await _fabricate(bot, kind)

        assert await _snapshot(db_path) == before

    async def test_no_season_row_is_created(self, bot, db_path):
        await _configure_teams(db_path)

        await _fabricate(bot, "results")

        async with get_connection(db_path) as db:
            count = await (
                await db.execute("SELECT COUNT(*) AS c FROM seasons")
            ).fetchone()
        assert count["c"] == 0


# ── T023: the parameters ──────────────────────────────────────────────────


class TestParametersOnASeasonLessServer:
    """FR-021, FR-022. Both parameters are optional, and disregarded where nothing exists
    to resolve them against."""

    async def test_omitting_both_is_accepted(self, bot, db_path):
        await _configure_teams(db_path)

        context = await resolve_context(bot, SERVER_ID, None, kind="results")

        assert context.fabricated_league is True

    async def test_a_supplied_division_is_disregarded(self, bot, db_path):
        await _configure_teams(db_path)

        context = await resolve_context(
            bot, SERVER_ID, "No Such Division", kind="results"
        )

        assert context.fabricated_league is True
        assert context.division_name != "No Such Division"

    async def test_a_supplied_round_is_disregarded(self, bot, db_path):
        await _configure_teams(db_path)

        context = await resolve_context(
            bot, SERVER_ID, "Whatever", round_number=999, kind="results"
        )

        assert context.fabricated_league is True
        assert context.round.round_number != 999
