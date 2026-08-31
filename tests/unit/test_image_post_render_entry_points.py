"""Every post module's render entry point, executed rather than replaced.

Each of the six suites beside this one monkeypatches its module's ``render_png`` /
``render_sheet`` / ``render_forecast`` / ``render_verdict`` before exercising the posting
around it. That is the right shape for testing a *posting*, but it means the render bodies
themselves were never run by anything: two of them referred to names that do not exist —
``template_key`` in ``image_results_post`` and ``LINEUP_TEMPLATE_KEY`` in
``image_lineup_post`` — and raised ``NameError`` on every call for six days, each caught by
a ``try``/``except`` that reported a fault and fell back to text. A league saw a graphic
that never drew and no reason it could act on.

These tests patch nothing inside the module under test. They stand up the two collaborators
a render body reaches — the config reader and the render service — and let the body run, so
that an unresolvable name fails here rather than in a league's channel.

The ``image_type`` each body passes to ``resolve_configured_directories`` is asserted, not
merely its resolvability: a name that exists but names the wrong image type would satisfy
"it did not raise" while still mislabelling every rejected directory in the log.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

pytestmark = pytest.mark.asyncio


def _config():
    """An image configuration naming every asset directory a render body may resolve."""
    return SimpleNamespace(
        track_image_directory="resources/defaults/tracks",
        team_image_directory="resources/defaults/teams",
        flag_directory="resources/defaults/flags",
        driver_image_directory="resources/defaults/drivers",
        marker_directory="resources/defaults/markers",
        weather_icon_directory="resources/defaults/weather",
        tyre_directory="resources/defaults/tyres",
        standings_highlight_directory="resources/defaults/standings-highlights",
        date_format="D_MONTH_YYYY",
        time_format="24H",
        time_zone="UTC",
    )


def _bot_with_decision(tmp_path):
    """A bot whose render service records its call and claims the image was produced.

    ``render_for_posting`` is a mock, so the spec builder handed to it is never invoked —
    the drawing never has to be a real one, and the only code that runs is the render body
    under test.
    """
    from services.image_render_service import POST_IMAGE, PostingDecision

    png = tmp_path / "drawn.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    bot = MagicMock()
    bot.image_config_service.get_config = AsyncMock(return_value=_config())
    bot.image_render_service.render_for_posting = AsyncMock(
        return_value=PostingDecision(action=POST_IMAGE, png_paths=[png])
    )
    bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    return bot, png


def _capture_image_type(monkeypatch):
    """Record the ``image_type`` each render body labels its directory resolution with."""
    from services import image_render_service

    seen: dict[str, object] = {}
    real = image_render_service.resolve_configured_directories

    def _spy(config, pairs, *, image_type="unknown"):
        seen["image_type"] = image_type
        seen["classes"] = [asset_class for asset_class, _column in pairs]
        return real(config, pairs, image_type=image_type)

    monkeypatch.setattr(
        image_render_service, "resolve_configured_directories", _spy
    )
    return seen


# ── The two that were broken ──────────────────────────────────────────────


async def test_the_results_render_body_runs_and_labels_itself_with_its_template(
    tmp_path, monkeypatch
):
    """``render_png`` referred to ``template_key``, a local of ``try_post``, not of itself."""
    from models.image_module import PostingOrigin
    from services import image_results_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)
    drawing = SimpleNamespace(
        template_key="results_race_template",
        session_name="Feature Race",
        season_number="3",
        division_tier="1",
        division_name="Elite",
        round_number="4",
    )

    decision = await image_results_post.render_png(
        bot, 1, drawing, PostingOrigin.SCHEDULED
    )

    assert decision.posts_image
    assert decision.png_paths == [png]
    assert seen["image_type"] == "results_race_template"
    assert seen["classes"] == ["team", "flag", "tyre"]
    bot.image_render_service.render_for_posting.assert_awaited_once()
    assert (
        bot.image_render_service.render_for_posting.await_args.args[1]
        == "results_race_template"
    )
    # The **session's** name, not the template's: one template draws four sessions.
    assert (
        bot.image_render_service.render_for_posting.await_args.kwargs["filename_stem"]
        == "season3_division1_round4_feature_race_results"
    )


async def test_the_results_render_body_labels_qualifying_as_qualifying(
    tmp_path, monkeypatch
):
    """The label follows the drawing, so the two results templates cannot be confused."""
    from models.image_module import PostingOrigin
    from services import image_results_post

    bot, _png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)
    drawing = SimpleNamespace(
        template_key="results_qualifying_template",
        session_name="Sprint Qualifying",
        season_number="3",
        division_tier="1",
        division_name="Elite",
        round_number="4",
    )

    await image_results_post.render_png(bot, 1, drawing, PostingOrigin.SCHEDULED)

    assert seen["image_type"] == "results_qualifying_template"
    assert (
        bot.image_render_service.render_for_posting.await_args.kwargs["filename_stem"]
        == "season3_division1_round4_sprint_qualifying_results"
    )


async def test_the_lineup_render_body_runs_and_labels_itself_with_its_template(
    tmp_path, monkeypatch
):
    """``render_png`` referred to ``LINEUP_TEMPLATE_KEY``, which was defined nowhere."""
    from models.image_module import PostingOrigin
    from services import image_lineup_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)

    # The drawing is not what is under test here; the body around it is.
    monkeypatch.setattr(
        image_lineup_post,
        "build_drawing",
        AsyncMock(return_value=({"name": "Main"}, SimpleNamespace())),
    )

    decision = await image_lineup_post.render_png(
        bot, 1, MagicMock(), 7, PostingOrigin.SCHEDULED
    )

    assert decision.posts_image
    assert decision.png_paths == [png]
    assert seen["image_type"] == "lineup_template"
    assert seen["classes"] == ["team", "flag", "driver"]
    assert (
        bot.image_render_service.render_for_posting.await_args.args[1]
        == "lineup_template"
    )


# ── The four that were sound, kept honest ─────────────────────────────────


async def test_the_attendance_render_body_runs_and_labels_itself(tmp_path, monkeypatch):
    from services import image_attendance_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)

    render = await image_attendance_post.render_sheet(bot, 1, SimpleNamespace())

    assert render.draws, render.problem
    assert render.png == png
    assert seen["image_type"] == "attendance_template"


async def test_the_verdict_render_body_runs_and_labels_itself(tmp_path, monkeypatch):
    from services import image_verdict_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)

    render = await image_verdict_post.render_verdict(bot, 1, SimpleNamespace())

    assert render.draws, render.problem
    assert render.png == png
    assert seen["image_type"] == "verdicts_template"


async def test_the_weather_render_body_runs_and_labels_itself(tmp_path, monkeypatch):
    from services import image_weather_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)
    drawing = SimpleNamespace(
        template_key="weather_p2_sprint_template",
        season_number="3",
        division_tier="2",
        division_name="Academy",
        round_number="7",
    )

    render = await image_weather_post.render_forecast(bot, 1, drawing)

    assert render.draws, render.problem
    assert render.png == png
    assert seen["image_type"] == "weather_p2_sprint_template"
    assert (
        bot.image_render_service.render_for_posting.await_args.kwargs["filename_stem"]
        == "season3_division2_round7_weather_p2_sprint"
    )


async def test_the_rsvp_render_body_runs_and_labels_itself(tmp_path, monkeypatch):
    from services import image_rsvp_post

    bot, png = _bot_with_decision(tmp_path)
    seen = _capture_image_type(monkeypatch)
    report = MagicMock()
    report.valid = True
    bot.image_config_service.get_toggles = AsyncMock(return_value={"rsvp": True})
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"rsvp_template": report}
    )

    attachment = await image_rsvp_post.try_attach(
        bot,
        1,
        division_name="Main",
        round_number=4,
        round_format="STANDARD",
        scheduled_at=datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc),
        track_name="Silverstone",
        country_name="United Kingdom",
        season_number=3,
        deadline_hours=24,
    )

    assert attachment is not None
    assert seen["image_type"] == "rsvp_template"
    assert seen["classes"] == ["track", "flag"]


# ── The guard against this recurring ──────────────────────────────────────


async def test_no_render_body_resolves_directories_under_the_default_label():
    """``image_type`` defaults to ``"unknown"``; no caller may leave it there.

    The default exists for a caller outside a posting path. A render body that took it
    would log every rejected directory without saying which graphic was being drawn — the
    exact fault ``resolve_configured_directories`` was given the parameter to end.
    """
    import ast
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2] / "src" / "services"
    offenders = []
    for path in sorted(root.glob("image_*_post.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "resolve_configured_directories":
                continue
            if not any(kw.arg == "image_type" for kw in node.keywords):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == [], (
        "these render bodies resolve directories without naming their image type: "
        + ", ".join(offenders)
    )
