"""The calendar template's capacity — a refusal when a round is added, a warning at review.

Issue #208. The same overflow is reported two ways at two moments, and the difference is the
whole point of having both.

**Adding a round that outgrows the template is refused outright**, with the round not added.
`/round add` knows exactly which division it is touching, so the comparison is certain and
Constitution XIV.12 requires overflow to be rejected at the earliest moment it can be detected,
with the change unapplied.

**Season review can only warn** (Constitution XIV.9). It compares against the greatest round
count any division holds, but which division is actually drawn is decided later — so a season
that would draw perfectly could be refused on a division that never gets drawn. Reporting it as
a failure there would block a league for a fault it may not have.
`test_the_review_warns_where_the_add_refuses` is the pair that keeps the two apart.

**The guard never raises for its own reasons.** A fault in the check must not block a round —
only a genuine over-capacity may. So a missing template, an invalid one, an unreadable SVG and
an exception anywhere inside all return "no problem", and the round proceeds. That is the safe
direction: a league whose template cannot be read still gets its round, and finds out when the
graphic is drawn.

**It counts rounds, not drivers.** `placement_service._guard_image_capacity` counts seated
drivers against the lineup and attendance templates; a calendar's collection is rounds. The two
guard different commands and must not be merged (research.md § R3) — and this file exists partly
so a later reader sees why there are two.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 11908


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(
    *,
    images_enabled: bool = True,
    aspect_enabled: bool = True,
    report=...,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.module_service = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=images_enabled)
    bot.image_config_service = MagicMock()
    bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=aspect_enabled)

    resolved = (
        SimpleNamespace(valid=True, resolved_path="/tmp/calendar.svg")
        if report is ...
        else report
    )
    bot.image_validity_service = MagicMock()
    bot.image_validity_service.template_reports = AsyncMock(
        return_value={"calendar_template": resolved}
    )

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _capacity(value: int | None, *, load_error: Exception | None = None):
    """Patch the SVG load and the catalogue's capacity reading."""
    catalogue = MagicMock()
    catalogue.capacity = MagicMock(return_value=value)
    return (
        patch(
            "utils.svg_document.load_svg",
            new=MagicMock(side_effect=load_error, return_value=MagicMock()),
        ),
        patch("models.image_catalogues.catalogue_for", new=MagicMock(return_value=catalogue)),
    )


async def _overflow(cog, would_hold: int, capacity_value, **kwargs):
    load, catalogue = _capacity(capacity_value, **kwargs)
    with load, catalogue:
        return await cog._calendar_round_overflow(SERVER_ID, would_hold)


# ---------------------------------------------------------------------------
# When the guard applies at all
# ---------------------------------------------------------------------------


async def test_a_round_within_capacity_is_not_refused():
    cog = _make_cog()

    assert await _overflow(cog, 20, 24) is None


async def test_a_round_exactly_at_capacity_is_not_refused():
    """The comparison is `>`, so a template drawing twenty-four rounds takes twenty-four."""
    cog = _make_cog()

    assert await _overflow(cog, 24, 24) is None


async def test_a_round_past_capacity_is_refused():
    cog = _make_cog()

    message = await _overflow(cog, 25, 24)

    assert message is not None
    assert "25 rounds" in message
    assert "draws 24" in message


async def test_the_refusal_says_the_round_was_not_added():
    """XIV.12 requires the change unapplied, and a manager needs to know the calendar is
    unchanged rather than half-extended."""
    cog = _make_cog()

    message = await _overflow(cog, 25, 24)

    assert "**not** added" in message


async def test_the_refusal_names_both_ways_out():
    """Enlarging the template and turning the aspect off are genuinely different
    decisions, and a league may reasonably take either."""
    cog = _make_cog()

    message = await _overflow(cog, 25, 24)

    assert "Enlarge the template" in message
    assert "/images config toggle" in message


# ---------------------------------------------------------------------------
# Where the guard stands aside
# ---------------------------------------------------------------------------


async def test_the_guard_is_inert_while_the_image_module_is_off():
    """No graphic will be drawn, so there is no capacity to outgrow."""
    cog = _make_cog(images_enabled=False)

    assert await _overflow(cog, 100, 24) is None


async def test_the_guard_is_inert_while_the_calendar_aspect_is_off():
    """A league can run the image module and draw no calendar — the aspect is what decides
    whether this template is used at all."""
    cog = _make_cog(aspect_enabled=False)

    assert await _overflow(cog, 100, 24) is None


async def test_a_league_with_no_calendar_template_is_not_refused():
    cog = _make_cog(report=None)

    assert await _overflow(cog, 100, 24) is None


async def test_an_invalid_template_is_not_used_to_refuse():
    """Its capacity cannot be trusted, and refusing a round on an unreadable number would
    block a league for a fault in their artwork rather than their calendar."""
    cog = _make_cog(report=SimpleNamespace(valid=False, resolved_path="/tmp/bad.svg"))

    assert await _overflow(cog, 100, 24) is None


async def test_a_template_reporting_no_capacity_is_not_used_to_refuse():
    """`None` or zero means the reading failed rather than that the template draws
    nothing — a template that genuinely drew no rounds would be invalid."""
    cog = _make_cog()

    assert await _overflow(cog, 100, None) is None
    assert await _overflow(cog, 100, 0) is None


async def test_an_unreadable_template_does_not_block_the_round(caplog):
    """The safe direction: a league whose template cannot be read still gets its round,
    and finds out when the graphic is drawn."""
    cog = _make_cog()

    with caplog.at_level("ERROR"):
        result = await _overflow(cog, 100, 24, load_error=OSError("no such file"))

    assert result is None
    assert "capacity guard could not run" in caplog.text


async def test_a_failure_reading_the_reports_does_not_block_the_round(caplog):
    cog = _make_cog()
    cog.bot.image_validity_service.template_reports = AsyncMock(
        side_effect=RuntimeError("template service down")
    )

    with caplog.at_level("ERROR"):
        result = await _overflow(cog, 100, 24)

    assert result is None


# ---------------------------------------------------------------------------
# The refusal and the warning are different things
# ---------------------------------------------------------------------------


async def _make_season_db(tmp_path, *, rounds_per_division: dict[str, int]) -> str:
    db_path = os.path.join(str(tmp_path), "calendar_capacity.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (1, ?, 1, '2026-01-01', 'SETUP')",
            (SERVER_ID,),
        )
        for index, (name, count) in enumerate(rounds_per_division.items(), start=1):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, ?, ?, ?)",
                (index, name, index, 500 + index),
            )
            for number in range(1, count + 1):
                await db.execute(
                    "INSERT INTO rounds (division_id, round_number, format, track_name, "
                    "scheduled_at) VALUES (?, ?, 'NORMAL', 'Silverstone Circuit', "
                    "'2026-06-01')",
                    (index, number),
                )
        await db.commit()
    return db_path


async def _warning(cog, db_path, capacity_value):
    cog.bot.db_path = db_path
    cog.bot.image_config_service.get_config = AsyncMock(return_value=MagicMock())
    load, catalogue = _capacity(capacity_value)
    with load, catalogue:
        return await cog._calendar_capacity_warning(SERVER_ID, 1)


async def test_the_review_warns_where_the_add_refuses(tmp_path):
    """`/round add` knows which division it is touching, so it refuses. Season review can
    only compare against the greatest round count any division holds — and which division
    is drawn is decided later, so a season that would draw perfectly could be refused on a
    division that never gets drawn.

    The two therefore return different kinds of thing for the same overflow: a refusal
    message, and a warning line. A reader merging them would either refuse a season
    wrongly or let a round through that cannot be drawn."""
    cog = _make_cog()
    db_path = await _make_season_db(tmp_path, rounds_per_division={"Division 1": 25})

    refusal = await _overflow(cog, 25, 24)
    warning = await _warning(cog, db_path, 24)

    assert isinstance(refusal, str)
    assert "not** added" in refusal
    assert isinstance(warning, list)
    assert "⚠️" in warning[0]
    assert "not** added" not in warning[0]


async def test_the_warning_names_the_division_that_overflows(tmp_path):
    """A season of eight divisions needs to know which one to shorten."""
    cog = _make_cog()
    db_path = await _make_season_db(
        tmp_path, rounds_per_division={"Division 1": 10, "Premier": 25}
    )

    warning = await _warning(cog, db_path, 24)

    assert "Premier" in warning[0]
    assert "holds 25" in warning[0]


async def test_the_warning_says_what_will_happen_instead(tmp_path):
    """The calendar is posted as text rather than not at all, which is the difference
    between a degraded season and a broken one."""
    cog = _make_cog()
    db_path = await _make_season_db(tmp_path, rounds_per_division={"Division 1": 25})

    warning = await _warning(cog, db_path, 24)

    assert "posted as text" in warning[0]
    assert "Enlarge the template" in warning[0]


async def test_a_season_within_capacity_earns_no_warning(tmp_path):
    cog = _make_cog()
    db_path = await _make_season_db(tmp_path, rounds_per_division={"Division 1": 20})

    assert await _warning(cog, db_path, 24) == []


async def test_the_most_demanding_division_is_the_one_compared(tmp_path):
    """Only the greatest round count can be judged here, because which division gets drawn
    is decided later — so a season passes only if its longest calendar fits."""
    cog = _make_cog()
    db_path = await _make_season_db(
        tmp_path, rounds_per_division={"Short": 5, "Long": 25}
    )

    warning = await _warning(cog, db_path, 24)

    assert "Long" in warning[0]


async def test_a_season_with_no_divisions_earns_no_warning(tmp_path):
    cog = _make_cog()
    db_path = await _make_season_db(tmp_path, rounds_per_division={})

    assert await _warning(cog, db_path, 24) == []


async def test_a_review_never_fails_on_the_capacity_check(tmp_path, caplog):
    """Constitution XIV.9 — the review is the report a manager approves from, and losing
    it to a template fault would leave them unable to see anything at all."""
    cog = _make_cog()
    db_path = await _make_season_db(tmp_path, rounds_per_division={"Division 1": 25})
    cog.bot.image_config_service.get_config = AsyncMock(
        side_effect=RuntimeError("image service down")
    )
    cog.bot.db_path = db_path

    with caplog.at_level("ERROR"):
        result = await cog._calendar_capacity_warning(SERVER_ID, 1)

    assert result == []
