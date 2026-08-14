"""The weather posting path — T035, T036, T037, T047, T060, T061.

Covers the contract in
``specs/042-weather-image-generation/contracts/weather-posting.md`` and Constitution XIV.4,
XIV.7 and XIV.8.

The tests that matter most are the ordering ones. **Produce before destroy** (XIV.8) is what
stops a failed phase 3 render leaving a division with no forecast two hours before its race,
and the natural way to write a chain — delete the old, post the new — has that failure built
in. It was the shipped behaviour until this increment.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services import forecast_cleanup_service, phase2_service, phase3_service  # noqa: E402
from services.image_weather_post import (  # noqa: E402
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
    assert await weather_enabled(_bot(), 1, "weather_p2_template") is True


@pytest.mark.asyncio
async def test_the_module_being_off_falls_back_to_text():
    assert await weather_enabled(_bot(module_on=False), 1, "weather_p2_template") is False


@pytest.mark.asyncio
async def test_the_aspect_being_off_falls_back_to_text():
    assert await weather_enabled(_bot(aspect_on=False), 1, "weather_p2_template") is False


@pytest.mark.asyncio
async def test_an_invalid_template_falls_back_to_text():
    assert await weather_enabled(_bot(template_valid=False), 1, "weather_p3_template") is False


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
    assert await weather_enabled(bot, 1, "weather_p3_template") is True
    assert await weather_enabled(bot, 1, "weather_p3_sprint_template") is False


@pytest.mark.asyncio
async def test_a_reader_that_raises_never_breaks_the_posting():
    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    assert await weather_enabled(bot, 1, "weather_p1_template") is False


# ── 2. Produce before destroy (FR-045, XIV.8) ─────────────────────────────


def _cleanup_bot(*, send_fails=False, post_returns=MagicMock()):
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.output_router.post_forecast = AsyncMock(return_value=post_returns)
    return bot


@pytest.mark.asyncio
async def test_the_superseded_message_is_deleted_only_after_the_new_one_exists(monkeypatch):
    order: list[str] = []

    async def _store(*a, **k):
        order.append("store")

    async def _delete(*a, **k):
        order.append("delete")

    async def _post(div, text, server_id=0):
        order.append("post")
        return MagicMock()

    monkeypatch.setattr(forecast_cleanup_service, "store_forecast_message", _store)
    monkeypatch.setattr(forecast_cleanup_service, "delete_forecast_message", _delete)
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.output_router.post_forecast = _post

    await forecast_cleanup_service.post_phase_message(
        bot, round_id=1, division_id=1, server_id=1, channel_id=1,
        phase_number=2, text="forecast", supersedes=1,
    )
    assert order == ["post", "store", "delete"], order


@pytest.mark.asyncio
async def test_a_failed_post_deletes_nothing(monkeypatch):
    """The window this rule exists to close: the previous forecast must still stand."""
    deleted: list = []

    async def _delete(*a, **k):
        deleted.append(a)

    async def _post(div, text, server_id=0):
        return None  # the router's own failure signal

    monkeypatch.setattr(forecast_cleanup_service, "delete_forecast_message", _delete)
    monkeypatch.setattr(forecast_cleanup_service, "store_forecast_message", AsyncMock())
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.output_router.post_forecast = _post

    result = await forecast_cleanup_service.post_phase_message(
        bot, round_id=1, division_id=1, server_id=1, channel_id=1,
        phase_number=3, text="forecast", supersedes=2,
    )
    assert result is None
    assert deleted == []


@pytest.mark.asyncio
async def test_the_first_occasion_supersedes_nothing(monkeypatch):
    deleted: list = []
    monkeypatch.setattr(
        forecast_cleanup_service, "delete_forecast_message",
        AsyncMock(side_effect=lambda *a, **k: deleted.append(a)),
    )
    monkeypatch.setattr(forecast_cleanup_service, "store_forecast_message", AsyncMock())
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.output_router.post_forecast = AsyncMock(return_value=MagicMock())

    await forecast_cleanup_service.post_phase_message(
        bot, round_id=1, division_id=1, server_id=1, channel_id=1,
        phase_number=1, text="forecast",
    )
    assert deleted == []


# ── 3. The manner of a message is no part of the chain (FR-046) ───────────


def test_the_chain_names_a_phase_and_never_a_drawing():
    """FR-046 — each occasion reads which message stands, never how it was drawn."""
    signature = inspect.signature(forecast_cleanup_service.post_phase_message)
    assert signature.parameters["supersedes"].annotation in (int, "int | None")

    source = inspect.getsource(forecast_cleanup_service.post_phase_message)
    # The delete is driven by the phase number alone.
    assert "delete_forecast_message(round_id, division_id, supersedes, bot)" in source
    # Nothing about the superseded message's manner is consulted.
    for forbidden in ("was_image", "had_attachment", "is_graphic"):
        assert forbidden not in source


def test_both_manners_take_the_one_send_site():
    """A second send site is a second ordering, and two orderings drift."""
    for module in (phase2_service, phase3_service):
        source = inspect.getsource(module)
        assert source.count("post_phase_message(") == 1, module.__name__
        # The old delete-then-post pair must be gone from the phase services entirely.
        assert "delete_forecast_message" not in source, module.__name__


# ── 4. The graphic gates nothing (FR-051, XIV.7) ──────────────────────────


@pytest.mark.parametrize("module", [phase2_service, phase3_service])
def test_the_graphic_is_reached_after_the_draw_is_persisted(module):
    """XIV.7's precondition clause: a failed render must find the work already done."""
    source = inspect.getsource(module)
    persisted = source.index("db.commit()")
    attached = source.index("attach_forecast(")
    assert persisted < attached, (
        f"{module.__name__} reaches the graphic before persisting its draw"
    )


@pytest.mark.parametrize("module", [phase2_service, phase3_service])
def test_the_calculation_log_is_written_whatever_the_graphic_does(module):
    """FR-033 — the log channel stays textual in its entirety."""
    source = inspect.getsource(module)
    assert "post_log(" in source
    assert source.index("attach_forecast(") < source.index("post_log(")


# ── 5. A transport failure retries as text (FR-057) ───────────────────────


def test_the_text_is_what_is_enqueued_never_the_image():
    source = inspect.getsource(forecast_cleanup_service.post_phase_message)
    assert "content=text" in source
    assert "attachment" not in source.split("retry_service.enqueue")[1][:400]


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
    from models.image_module import PostingOrigin
    from services.image_weather_post import render_forecast

    bot = _render_bot(_decision(posts_image=False, problem_detail="template too small"))
    render = await render_forecast(
        bot, 1, MagicMock(template_key="weather_p2_template"),
        origin=PostingOrigin.SCHEDULED,
    )
    assert render.draws is False
    assert render.rejects is False
    assert render.problem == "template too small"


@pytest.mark.asyncio
async def test_a_commanded_posting_rejects_rather_than_falling_back():
    """FR-056 — the one person able to fix the template is standing there."""
    from models.image_module import PostingOrigin
    from services.image_weather_post import render_forecast

    bot = _render_bot(_decision(rejects=True, problem_detail="missing track_name"))
    render = await render_forecast(
        bot, 1, MagicMock(template_key="weather_p3_template"),
        origin=PostingOrigin.COMMANDED,
    )
    assert render.rejects is True
    assert render.draws is False
    assert render.problem == "missing track_name"


@pytest.mark.asyncio
async def test_a_resolution_fault_rejects_only_when_commanded():
    from models.image_module import PostingOrigin
    from services.image_weather_post import render_forecast

    bot = MagicMock()
    bot.image_config_service.get_config = AsyncMock(side_effect=RuntimeError("no config"))
    drawing = MagicMock(template_key="weather_p1_template")

    scheduled = await render_forecast(bot, 1, drawing, origin=PostingOrigin.SCHEDULED)
    assert scheduled.draws is False and scheduled.rejects is False

    commanded = await render_forecast(bot, 1, drawing, origin=PostingOrigin.COMMANDED)
    assert commanded.rejects is True


@pytest.mark.asyncio
async def test_a_successful_render_hands_back_the_png():
    from services.image_weather_post import render_forecast

    bot = _render_bot(_decision())
    render = await render_forecast(bot, 1, MagicMock(template_key="weather_p1_template"))
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
    import services.image_weather_post as post

    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    reported: list = []
    monkeypatch.setattr(
        post, "report_notices",
        AsyncMock(side_effect=lambda bot, sid, what, n: reported.append((what, list(n)))),
    )
    monkeypatch.setattr(post, "report", AsyncMock())
    drawing = MagicMock(
        template_key="weather_p2_template", division_name="Division 1",
        round_number="4", season_number="3", is_mystery=False,
    )
    monkeypatch.setattr(post, "build_drawing_for_round", AsyncMock(return_value=drawing))

    bot = _attach_bot(notices=["fallback used"])
    bot.image_render_service.render_for_posting.return_value.png_paths = [str(png)]

    result = await post.attach_forecast(bot, round_id=1, phase=2, server_id=1)

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

    import services.image_weather_post as post

    source = inspect.getsource(post.attach_forecast)
    assert "report_notices(" in source and "report(" in source

    module_source = inspect.getsource(post)
    for forbidden in ("post_forecast", "forecast_channel_id", "channel.send"):
        assert forbidden not in module_source, forbidden


@pytest.mark.asyncio
async def test_a_problem_is_reported_and_the_text_stands_instead(monkeypatch):
    import services.image_weather_post as post

    reported: list = []
    monkeypatch.setattr(post, "report_notices", AsyncMock())
    monkeypatch.setattr(
        post, "report",
        AsyncMock(side_effect=lambda bot, sid, what, detail: reported.append(detail)),
    )
    drawing = MagicMock(
        template_key="weather_p3_template", division_name="D",
        round_number="1", season_number="1", is_mystery=False,
    )
    monkeypatch.setattr(post, "build_drawing_for_round", AsyncMock(return_value=drawing))

    bot = _attach_bot(draws=False, problem="no fallback in that directory")
    result = await post.attach_forecast(bot, round_id=1, phase=3, server_id=1)

    assert result is None, "the caller must post the textual forecast"
    assert reported == ["no fallback in that directory"]


# ── 10. The mystery notice's posting (T047, FR-052, FR-053) ───────────────


def test_the_mystery_notice_rides_on_a_message_carrying_no_mention():
    """FR-052 — its textual counterpart tags nobody, and neither does the graphic's."""
    import inspect

    from services import mystery_notice_service

    source = inspect.getsource(mystery_notice_service.run_mystery_notice)
    assert 'attachment_text=""' in source, "the notice must carry no role mention"
    assert "mention_role_id" not in source


def test_the_mystery_notice_supersedes_nothing():
    """It is the only weather posting such a round makes."""
    import inspect

    from services import mystery_notice_service

    source = inspect.getsource(mystery_notice_service.run_mystery_notice)
    assert "supersedes" not in source
    assert "phase_number=0" in source


def test_no_phase_is_armed_for_a_mystery_round():
    """FR-053 — nothing whatever is posted at the phase 2 and phase 3 horizons."""
    from models.round import RoundFormat
    from models.session import SESSIONS_BY_FORMAT

    assert SESSIONS_BY_FORMAT[RoundFormat.MYSTERY] == []


def test_the_mystery_drawing_takes_the_mystery_template():
    """Whatever phase it is asked for, a mystery round reaches its own type."""
    from services.image_weather_service import MYSTERY_TEMPLATE_KEY, resolve_drawing

    for phase in (1, 2, 3):
        drawing = resolve_drawing(
            phase=phase, division_name="D", round_number=1, round_format="MYSTERY"
        )
        assert drawing.template_key == MYSTERY_TEMPLATE_KEY
        assert drawing.sessions == []
