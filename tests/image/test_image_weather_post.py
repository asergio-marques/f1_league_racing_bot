"""The weather posting path — T035, T036, T037, T047, T060, T061.

Covers the contract in
``specs/042-weather-image-generation/contracts/weather-posting.md`` and Constitution XIV.4,
XIV.7 and XIV.8.

**Produce before destroy** (XIV.8) is what stops a failed phase 3 render leaving a division
with no forecast two hours before its race. The forecast's post and the deletion of the one it
supersedes are now two jobs of the change queue (#439), so the ordering is pinned where those
jobs are, in ``tests/weather/test_forecast_changes.py``, and no longer by reading this path's
source.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.services.image_weather_post import (
    ForecastRender,
    describe,
    weather_enabled,
)


def _bot(*, module_on=True, aspect_on=True, template_valid=True):
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.module_service.is_images_enabled = AsyncMock(return_value=module_on)
    bot.image_config_service.get_toggles = AsyncMock(return_value={"weather": aspect_on})
    report = MagicMock()
    report.valid = template_valid
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={key: report for key in (
            "weather_p1_template",
            "weather_p2_template",
            "weather_p2_sprint_template",
            "weather_p3_template",
            "weather_p3_sprint_template",
            "weather_mystery_template",
        )}
    )
    return bot


# ── 1. Enablement (FR-050) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_graphic_draws_when_module_aspect_and_template_are_all_sound():
    assert await weather_enabled(_bot(), "weather_p2_template") is True


@pytest.mark.asyncio
async def test_the_module_being_off_falls_back_to_text():
    assert await weather_enabled(_bot(module_on=False), "weather_p2_template") is False


@pytest.mark.asyncio
async def test_the_aspect_being_off_falls_back_to_text():
    assert await weather_enabled(_bot(aspect_on=False), "weather_p2_template") is False


@pytest.mark.asyncio
async def test_an_invalid_template_falls_back_to_text():
    assert await weather_enabled(_bot(template_valid=False), "weather_p3_template") is False


@pytest.mark.asyncio
async def test_enablement_is_asked_per_template_not_per_aspect():
    """One of six being invalid must not stop the other five (XIV.4).

    A league whose sprint phase 3 file is too small still gets a picture for every round that
    is not a sprint.
    """
    bot = _bot()
    good, bad = MagicMock(), MagicMock()
    good.valid, bad.valid = True, False
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"weather_p3_template": good, "weather_p3_sprint_template": bad}
    )
    assert await weather_enabled(bot, "weather_p3_template") is True
    assert await weather_enabled(bot, "weather_p3_sprint_template") is False


@pytest.mark.asyncio
async def test_a_reader_that_raises_never_breaks_the_posting():
    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await weather_enabled(bot, "weather_p1_template") is False


# ── 6. What a report names (FR-059) ───────────────────────────────────────


def test_a_report_names_the_season_division_round_and_phase():
    what = describe(division_name="Division 1", round_number=4, phase=2, season_number=3)
    assert "season 3" in what
    assert "Division 1" in what
    assert "round 4" in what
    assert "phase 2" in what


def test_the_mystery_notice_is_named_as_itself_rather_than_a_phase():
    what = describe(division_name="D", round_number=1, phase=0, season_number=1)
    assert "mystery notice" in what
    assert "phase 0" not in what


# ── 7. The render result ──────────────────────────────────────────────────


def test_a_render_without_a_png_does_not_draw():
    assert ForecastRender().draws is False
    assert ForecastRender(problem="broken").draws is False


def test_a_render_with_a_png_draws():
    assert ForecastRender(png="x.png").draws is True


# ── 8. The fallback matrix (T037, FR-055 … FR-057) ────────────────────────


def _decision(*, posts_image=True, rejects=False, problem_detail=None, notices=()):
    decision = MagicMock()
    decision.posts_image = posts_image
    decision.rejects = rejects
    decision.notices = list(notices)
    if problem_detail is None:
        decision.problem = None
    else:
        decision.problem = MagicMock()
        decision.problem.detail = problem_detail
    decision.png_paths = ["/tmp/weather.png"]
    return decision


def _render_bot(decision):
    bot = MagicMock()
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())
    bot.image_render_service.render_for_posting = AsyncMock(return_value=decision)
    return bot


@pytest.mark.asyncio
async def test_a_scheduled_posting_falls_back_rather_than_rejecting():
    """FR-055 — nobody is at the keyboard, and the league still needs its forecast."""
    from leaguebot.image.models.image_module import PostingOrigin
    from leaguebot.image.services.image_weather_post import render_forecast

    bot = _render_bot(_decision(posts_image=False, problem_detail="template too small"))
    render = await render_forecast(
        bot, MagicMock(template_key="weather_p2_template"),
        origin=PostingOrigin.SCHEDULED,
    )
    assert render.draws is False
    assert render.rejects is False
    assert render.problem == "template too small"


@pytest.mark.asyncio
async def test_a_commanded_posting_rejects_rather_than_falling_back():
    """FR-056 — the one person able to fix the template is standing there."""
    from leaguebot.image.models.image_module import PostingOrigin
    from leaguebot.image.services.image_weather_post import render_forecast

    bot = _render_bot(_decision(rejects=True, problem_detail="missing track_name"))
    render = await render_forecast(
        bot, MagicMock(template_key="weather_p3_template"),
        origin=PostingOrigin.COMMANDED,
    )
    assert render.rejects is True
    assert render.draws is False
    assert render.problem == "missing track_name"


@pytest.mark.asyncio
async def test_a_resolution_fault_rejects_only_when_commanded():
    from leaguebot.image.models.image_module import PostingOrigin
    from leaguebot.image.services.image_weather_post import render_forecast

    bot = MagicMock()
    bot.image_config_service.get_config = AsyncMock(side_effect=RuntimeError("no config"))
    drawing = MagicMock(template_key="weather_p1_template")

    scheduled = await render_forecast(bot, drawing, origin=PostingOrigin.SCHEDULED)
    assert scheduled.draws is False and scheduled.rejects is False

    commanded = await render_forecast(bot, drawing, origin=PostingOrigin.COMMANDED)
    assert commanded.rejects is True


@pytest.mark.asyncio
async def test_a_successful_render_hands_back_the_png():
    from leaguebot.image.services.image_weather_post import render_forecast

    bot = _render_bot(_decision())
    render = await render_forecast(bot, MagicMock(template_key="weather_p1_template"))
    assert render.draws is True
    assert str(render.png) == "/tmp/weather.png"


# ── 9. Notices reach staff and no forecast channel (T060, T061, FR-059) ───


def _attach_bot(*, notices=(), problem=None, draws=True):
    bot = _bot()
    bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())
    bot.image_render_service.render_for_posting = AsyncMock(
        return_value=_decision(posts_image=draws, problem_detail=problem, notices=notices)
    )
    return bot


@pytest.mark.asyncio
async def test_a_substituted_asset_still_draws_and_reports_its_notice(monkeypatch, tmp_path):
    """FR-059 — the picture is posted and the substitution is reported to staff."""
    import leaguebot.image.services.image_weather_post as post

    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    reported: list = []
    monkeypatch.setattr(
        post, "report_notices",
        AsyncMock(side_effect=lambda bot, what, n: reported.append((what, list(n)))),
    )
    monkeypatch.setattr(post, "report", AsyncMock())
    drawing = MagicMock(
        template_key="weather_p2_template", division_name="Division 1",
        round_number="4", season_number="3", is_mystery=False,
    )
    monkeypatch.setattr(post, "build_drawing_for_round", AsyncMock(return_value=drawing))

    bot = _attach_bot(notices=["fallback used"])
    bot.image_render_service.render_for_posting.return_value.png_paths = [str(png)]

    result = await post.attach_forecast(bot, round_id=1, phase=2)

    assert result is not None, "a substituted asset must not stop the graphic"
    assert len(reported) == 1
    what, notices = reported[0]
    assert notices == ["fallback used"]
    # Season, division, round and phase — so a manager can find what it pertains to.
    for part in ("season 3", "Division 1", "round 4", "phase 2"):
        assert part in what, part


def test_no_notice_and_no_problem_reaches_a_forecast_channel():
    """SC-005 — this module reports through the log-channel reporters and nowhere else."""
    import inspect

    import leaguebot.image.services.image_weather_post as post

    source = inspect.getsource(post.attach_forecast)
    assert "report_notices(" in source and "report(" in source

    module_source = inspect.getsource(post)
    for forbidden in ("post_forecast", "forecast_channel_id", "channel.send"):
        assert forbidden not in module_source, forbidden


@pytest.mark.asyncio
async def test_a_problem_is_reported_and_the_text_stands_instead(monkeypatch):
    import leaguebot.image.services.image_weather_post as post

    reported: list = []
    monkeypatch.setattr(post, "report_notices", AsyncMock())
    monkeypatch.setattr(
        post, "report",
        AsyncMock(side_effect=lambda bot, what, detail: reported.append(detail)),
    )
    drawing = MagicMock(
        template_key="weather_p3_template", division_name="D",
        round_number="1", season_number="1", is_mystery=False,
    )
    monkeypatch.setattr(post, "build_drawing_for_round", AsyncMock(return_value=drawing))

    bot = _attach_bot(draws=False, problem="no fallback in that directory")
    result = await post.attach_forecast(bot, round_id=1, phase=3)

    assert result is None, "the caller must post the textual forecast"
    assert reported == ["no fallback in that directory"]


# ── 10. The mystery notice's posting (T047, FR-052, FR-053) ───────────────


def test_no_phase_is_armed_for_a_mystery_round():
    """FR-053 — nothing whatever is posted at the phase 2 and phase 3 horizons."""
    from leaguebot.core.models.round import RoundFormat
    from leaguebot.core.models.session import SESSIONS_BY_FORMAT

    assert SESSIONS_BY_FORMAT[RoundFormat.MYSTERY] == []


def test_the_mystery_drawing_takes_the_mystery_template():
    """Whatever phase it is asked for, a mystery round reaches its own type."""
    from leaguebot.image.services.image_weather_service import MYSTERY_TEMPLATE_KEY, resolve_drawing

    for phase in (1, 2, 3):
        drawing = resolve_drawing(
            phase=phase, division_name="D", round_number=1, round_format="MYSTERY"
        )
        assert drawing.template_key == MYSTERY_TEMPLATE_KEY
        assert drawing.sessions == []
