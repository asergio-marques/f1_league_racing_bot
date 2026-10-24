"""The two rules the image settings take from the cog (#439, slice 7, plan 2.10).

`daily_portraits_refusal` and `aspect_blocking_reasons` move out of `ImageCog` into
`image/services/image_settings.py` as plain functions handed the services they read, so that a
settings change's check can ask them without reaching through the bot (architecture.md,
"Composition").
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


from leaguebot.core.db.database import get_connection
from tests.support.change_queue import maybe_await
from tests.support.settings_league import settings_league


async def test_daily_portraits_refusal_reads_the_module_it_is_handed():
    """Asked with a module service saying images are off, the rule refuses in today's words, the
    form's naming it a form and the confirmation's not, having asked the service it was handed;
    asked with one saying images are on, portraits obtained from Discord and daily updates off,
    it has nothing to refuse."""
    from leaguebot.image.services.image_settings import daily_portraits_refusal

    config = MagicMock()
    config.get_config = AsyncMock(return_value=SimpleNamespace(use_pfp=True, pfp_daily=False))
    off = MagicMock()
    off.is_images_enabled = AsyncMock(return_value=False)
    on = MagicMock()
    on.is_images_enabled = AsyncMock(return_value=True)

    assert await maybe_await(daily_portraits_refusal(modules=off, config=config, form=True)) == (
        "❌ The Image module was switched off while this form was open. Nothing was stored."
    )
    assert await maybe_await(daily_portraits_refusal(modules=off, config=config, form=False)) == (
        "❌ The Image module was switched off while this was open. Nothing was stored."
    )
    off.is_images_enabled.assert_awaited()
    assert await maybe_await(daily_portraits_refusal(modules=on, config=config, form=False)) is None


async def test_aspect_blocking_reasons_overrides_the_toggle_for_the_question(tmp_path, monkeypatch):
    """The calendar switched off and its template pointed at a file that is not there: the
    league's own statuses report nothing blocking the calendar, it being off, but the rule,
    asking as though it were switched on, names the drawing that cannot be found, and leaves the
    switch off. With the shipped template it names nothing."""
    import leaguebot.image.services.image_render_service as render
    import leaguebot.image.services.image_settings as image_settings
    from leaguebot.image.services.image_settings import aspect_blocking_reasons

    monkeypatch.setattr(render, "converter_available", lambda: True)
    monkeypatch.setattr(image_settings, "converter_available", lambda: True, raising=False)
    league = await settings_league(tmp_path, modules=("images",))
    config, validity = league.bot.image_config_service, league.bot.image_validity_service

    assert await aspect_blocking_reasons("calendar", config=config, validity=validity) == []

    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE image_config SET calendar_template = 'missing.svg'")
        await db.commit()

    statuses = {status.aspect: status for status in await validity.aspect_statuses()}
    assert statuses["calendar"].blocking_reasons == []
    reasons = await aspect_blocking_reasons("calendar", config=config, validity=validity)
    assert len(reasons) == 1
    assert reasons[0].startswith("Calendar: the drawing file can't be found")
    assert await config.is_aspect_enabled("calendar") is False
