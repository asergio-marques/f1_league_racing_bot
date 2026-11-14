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
#
# Judged through `attach_forecast`, which hands back the picture, or None, together with the log
# lines saying why a picture that was due was not drawn: a picture not due (the module or its
# weather aspect off) hands back no line (weather spec, "Image generation"; #287, B3).


def _values(description="Initial session forecast", rain_probability="30%", sessions=()):
    """What weather hands the picture: the phase's description, Phase 1's likelihood of rain as
    text, and each session's label, weather type, slot labels and their summary, in run order
    (#287, W288)."""
    from leaguebot.image.models.forecast_values import ForecastValues

    return ForecastValues(
        description=description, rain_probability=rain_probability, sessions=tuple(sessions),
    )


def _drawing(template_key="weather_p2_template", **facts):
    """A drawing of *template_key*, for Division 1's round 4 of season 3, phase 2."""
    named = {"division_name": "Division 1", "round_number": "4", "season_number": "3",
             "is_mystery": False, **facts}
    return MagicMock(template_key=template_key, **named)


async def _attach(bot, monkeypatch, drawing, *, phase=2, png=None):
    """`attach_forecast` for round 1's *phase*, its round read as *drawing* and, where *png* is
    given, its render drawing that file; the picture and the log lines it hands back."""
    import leaguebot.image.services.image_weather_post as post

    monkeypatch.setattr(post, "build_drawing_for_round", AsyncMock(return_value=drawing))
    if png is not None:
        monkeypatch.setattr(post, "render_forecast", AsyncMock(return_value=ForecastRender(png=png)))
    file, lines = await post.attach_forecast(bot, round_id=1, phase=phase, values=_values())
    return file, list(lines)


async def test_the_drawing_is_made_from_the_values_weather_hands_it(tmp_path):
    """Pro's round 3, a normal round with two sessions, has a standing Phase 1 draw of a 90%
    likelihood of rain and each session's Phase 2 type stored as sunny. Weather hands the picture
    of its Phase 3 forecast the values it composed: "Final session forecast", 30%, Qualifying
    mixed with Rain then Light Cloud, and Race wet with Rain. The drawing carries what was handed,
    in that order, and nothing read from weather's tables; the heading still names the round's
    division and number (#287, W288)."""
    from leaguebot.image.models.forecast_values import ForecastSession
    from leaguebot.image.services.image_weather_post import build_drawing_for_round
    from tests.support.season_league import PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, weather=True)
    round_ = round_id(PRO, 3)
    await league.write("UPDATE rounds SET format = 'NORMAL' WHERE id = ?", round_)
    await league.write(
        "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
        "VALUES (?, 1, '{\"rpc\": 0.9}', 'ACTIVE', '2026-11-20T00:00:00+00:00')", round_,
    )
    for session in ("SHORT_QUALIFYING", "LONG_RACE"):
        await league.write("INSERT INTO sessions (round_id, session_type, phase2_slot_type) "
                           "VALUES (?, ?, 'sunny')", round_, session)
    handed = _values("Final session forecast", "30%", (
        ForecastSession(label="Qualifying", weather_type="Mixed",
                        slots=("Rain", "Light Cloud"), summary="Rain → Light Cloud"),
        ForecastSession(label="Race", weather_type="Rain", slots=("Rain",), summary="Rain"),
    ))

    drawing = await build_drawing_for_round(league.bot, round_, 3, values=handed)

    assert drawing.phase_description == "Final session forecast"
    assert drawing.rain_probability == "30%"
    assert [(s.ordinal, s.name, s.weather_type, s.summary, [x.label for x in s.slots])
            for s in drawing.sessions] == [
        (1, "Qualifying", "Mixed", "Rain → Light Cloud", ["Rain", "Light Cloud"]),
        (2, "Race", "Rain", "Rain", ["Rain"]),
    ]
    assert drawing.round_number == "3" and drawing.division_name


async def test_the_graphic_draws_when_module_aspect_and_template_are_all_sound(
    monkeypatch, tmp_path,
):
    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    file, lines = await _attach(_bot(), monkeypatch, _drawing(), png=png)
    assert file is not None and lines == []


async def test_the_module_being_off_falls_back_to_text(monkeypatch):
    """No picture was due, so nothing is said of one."""
    assert await _attach(_bot(module_on=False), monkeypatch, _drawing()) == (None, [])


async def test_the_aspect_being_off_falls_back_to_text(monkeypatch):
    """No picture was due, so nothing is said of one."""
    assert await _attach(_bot(aspect_on=False), monkeypatch, _drawing()) == (None, [])


async def test_an_invalid_template_falls_back_to_text(monkeypatch):
    """A picture was due for Division 1's round 4, phase 3, but its template file is too small:
    the reason is handed back, naming the forecast as a league reads the round, never by its
    season (#287, B8)."""
    bot = _bot(template_valid=False)
    for report in (await bot.image_validity_service.template_reports()).values():
        report.reason = "the template file is too small"
    file, lines = await _attach(bot, monkeypatch, _drawing("weather_p3_template"), phase=3)
    assert file is None
    assert lines == [
        "⚠️ Weather image — round 4 in **Division 1**, phase 3: the template file is too small"
    ]


async def test_enablement_is_asked_per_template_not_per_aspect(monkeypatch, tmp_path):
    """One of six being invalid must not stop the other five (XIV.4).

    A league whose sprint phase 3 file is too small still gets a picture for every round that
    is not a sprint.
    """
    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    bot = _bot()
    good, bad = MagicMock(valid=True), MagicMock(valid=False, reason="too small")
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"weather_p3_template": good, "weather_p3_sprint_template": bad}
    )
    drawn, _ = await _attach(bot, monkeypatch, _drawing("weather_p3_template"), phase=3, png=png)
    assert drawn is not None
    sprint, lines = await _attach(
        bot, monkeypatch, _drawing("weather_p3_sprint_template"), phase=3, png=png,
    )
    assert sprint is None and "too small" in "\n".join(lines)


@pytest.mark.parametrize(
    ("round_reads", "named"),
    [
        (True, "round 4 in **Division 1**, phase 2"),
        (False, "phase 2"),
    ],
    ids=["round-readable", "round-unreadable"],
)
async def test_a_reader_that_raises_never_breaks_the_posting(monkeypatch, round_reads, named):
    """The image settings' switch reader raises "boom" for a phase 2 forecast. The text stands,
    and the fault is handed back for the log channel, naming the forecast where its round can be
    read, as a league reads it (round 4 in **Division 1**), and only its phase where reading the
    round raises too: naming in a log line never fails the line (#287, B3, B8)."""
    import leaguebot.image.services.image_weather_post as post

    bot = _bot()
    bot.image_config_service.get_toggles = AsyncMock(side_effect=RuntimeError("boom"))
    reads = (AsyncMock(return_value=_drawing()) if round_reads
             else AsyncMock(side_effect=RuntimeError("no round")))
    monkeypatch.setattr(post, "build_drawing_for_round", reads)

    file, lines = await post.attach_forecast(bot, round_id=1, phase=2, values=_values())

    [line] = lines
    assert file is None and "boom" in line
    assert line.startswith(f"⚠️ Weather image — {named}: "), line


# ── 6. What a report names (FR-059) ───────────────────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason="#287: a picture's line still calls a mystery round's notice the mystery notice",
)
def test_the_mystery_notice_is_named_as_itself_rather_than_a_phase():
    """A mystery round's notice is named in every message as the mystery round notice, never as
    phase 0 and never as the "mystery notice" (weather spec, "Mystery rounds"; #287, B12)."""
    what = describe(division_name="D", round_number=1, phase=0)
    assert what == "round 1 in **D**, the mystery round notice"
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


async def test_a_substituted_asset_still_draws_and_reports_its_notice(monkeypatch, tmp_path):
    """FR-059 — the picture is handed back, and the substitution with it, as a line for the log
    channel naming what it pertains to: Division 1's round 4, phase 2, as a league reads the
    round, never by its season (#287, B8)."""
    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    from leaguebot.image.models.image_module import RenderNotice

    bot = _attach_bot(notices=[RenderNotice(
        image_type="weather_p2_template", notice_kind="ASSET_FALLBACK_USED",
        detail="fallback used", field_id="weather_icon",
    )])
    bot.image_render_service.render_for_posting.return_value.png_paths = [str(png)]

    result, lines = await _attach(bot, monkeypatch, _drawing())

    assert result is not None, "a substituted asset must not stop the graphic"
    said = "\n".join(lines)
    assert "fallback used" in said
    # The round as a league reads it, and the phase — so a manager can find what it pertains to.
    assert "Image render notices — round 4 in **Division 1**, phase 2:" in said, said
    assert "season" not in said, said


async def test_attach_forecast_hands_render_for_posting_no_bot(monkeypatch, tmp_path):
    """Division 1's round 4, phase 2, drawn with a substituted weather icon: the render gives one
    notice. The render is handed no bot, so it reports nothing itself, and the notice comes back
    once, as a line for the post job to write to the log channel (#287, B3)."""
    png = tmp_path / "w.png"
    png.write_bytes(b"x")
    from leaguebot.image.models.image_module import RenderNotice

    bot = _attach_bot(notices=[RenderNotice(
        image_type="weather_p2_template", notice_kind="ASSET_FALLBACK_USED",
        detail="fallback used", field_id="weather_icon",
    )])
    bot.image_render_service.render_for_posting.return_value.png_paths = [str(png)]

    result, lines = await _attach(bot, monkeypatch, _drawing())

    rendered = bot.image_render_service.render_for_posting
    assert rendered.await_count == 1
    assert rendered.await_args.kwargs.get("bot") is None
    assert result is not None
    assert "\n".join(lines).count("fallback used") == 1


def test_no_notice_and_no_problem_reaches_a_forecast_channel():
    """SC-005 — this module posts nowhere: what it reports, it hands back to the post job, whose
    lines go to the log channel."""
    import inspect

    import leaguebot.image.services.image_weather_post as post

    module_source = inspect.getsource(post)
    for forbidden in ("post_forecast", "forecast_channel_id", "channel.send"):
        assert forbidden not in module_source, forbidden


async def test_a_problem_is_reported_and_the_text_stands_instead(monkeypatch):
    bot = _attach_bot(draws=False, problem="no fallback in that directory")
    result, lines = await _attach(bot, monkeypatch, _drawing("weather_p3_template"), phase=3)

    assert result is None, "the caller must post the textual forecast"
    assert "no fallback in that directory" in "\n".join(lines)


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
