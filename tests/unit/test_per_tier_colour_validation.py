"""The per-tier colour shortfall, and the three surfaces that must report it (051).

A league that turns per-tier colours on and marks a template with a slot has told the bot
that slot matters. Leaving it unset for a tier would draw that tier in whatever the template
happened to be authored in — silently, and differently from its siblings. So it is a
shortfall, and it is reported wherever a manager looks at their configuration.

**The point of this file is that there is one implementation, not three.** `/season review`,
`/images config view` and the aspect toggle all read the same `AspectStatus` list — a fact
`season_cog` records in as many words at its `_build_image_review_section` — and
`test_the_shortfall_reaches_every_surface` is what stops that quietly becoming untrue.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_module import (  # noqa: E402
    STATE_DISABLED,
    STATE_ENABLED,
    STATE_ENABLED_INVALID,
    ValidityReport,
)
from services.image_validity_service import (  # noqa: E402
    build_aspect_statuses,
    colour_shortfall,
)

CALENDAR = "calendar_template"


def _report(key=CALENDAR, *, valid=True, slots=frozenset()):
    return ValidityReport(
        template_key=key,
        resolved_path=None,
        valid=valid,
        depth_checked=1,
        colour_slots=frozenset(slots),
    )


# ── The rule itself ───────────────────────────────────────────────────────

def test_a_template_marking_no_slots_demands_nothing():
    """Optional on all fifteen kinds, mandatory on none — measured per template."""
    assert colour_shortfall({CALENDAR: _report()}, {}, ["Division 1"]) == {}


def test_a_marked_slot_no_tier_has_set_is_a_shortfall():
    shortfall = colour_shortfall(
        {CALENDAR: _report(slots={"accent"})}, {}, ["Division 1"]
    )
    assert list(shortfall) == [CALENDAR]
    assert "`accent`" in shortfall[CALENDAR][0]
    assert "Division 1" in shortfall[CALENDAR][0]


def test_a_slot_set_for_every_tier_is_no_shortfall():
    palettes = {"division_1": {"accent": "#3DD6F5"}, "division_2": {"accent": "#A78BFA"}}
    assert colour_shortfall(
        {CALENDAR: _report(slots={"accent"})}, palettes, ["Division 1", "Division 2"]
    ) == {}


def test_a_slot_set_for_only_one_tier_names_the_tier_that_lacks_it():
    shortfall = colour_shortfall(
        {CALENDAR: _report(slots={"accent"})},
        {"division_1": {"accent": "#3DD6F5"}},
        ["Division 1", "Division 2"],
    )
    assert len(shortfall[CALENDAR]) == 1
    assert "Division 2" in shortfall[CALENDAR][0]


def test_every_missing_pair_is_named_rather_than_counted():
    """A manager fixing them one review at a time is a manager the check is failing."""
    shortfall = colour_shortfall(
        {CALENDAR: _report(slots={"accent", "wash"})},
        {},
        ["Division 1", "Division 2"],
    )
    assert len(shortfall[CALENDAR]) == 4


def test_a_league_with_no_divisions_is_short_of_nothing():
    """There is no tier to want a colour, so demanding one would block the unfixable."""
    assert colour_shortfall({CALENDAR: _report(slots={"accent"})}, {}, []) == {}


def test_an_invalid_template_is_not_also_reported_for_colour():
    """It already has a reason; a second line about colours would bury it."""
    assert colour_shortfall(
        {CALENDAR: _report(valid=False, slots={"accent"})}, {}, ["Division 1"]
    ) == {}


def test_a_tier_is_matched_by_its_normalised_name():
    assert colour_shortfall(
        {CALENDAR: _report(slots={"accent"})},
        {"division_1": {"accent": "#3DD6F5"}},
        ["Division 1"],
    ) == {}


# ── How it reaches an aspect ──────────────────────────────────────────────

def test_a_shortfall_blocks_the_aspect_that_would_draw_it():
    statuses = build_aspect_statuses(
        {"calendar": True},
        {CALENDAR: _report(slots={"accent"})},
        colour_shortfall={CALENDAR: ["`accent` is not set for **Division 1**"]},
    )
    calendar = next(s for s in statuses if s.aspect == "calendar")
    assert calendar.state == STATE_ENABLED_INVALID
    assert any("accent" in reason for reason in calendar.blocking_reasons)


def test_the_blocking_reason_names_the_command_that_fixes_it():
    statuses = build_aspect_statuses(
        {"calendar": True},
        {CALENDAR: _report(slots={"accent"})},
        colour_shortfall={CALENDAR: ["`accent` is not set for **Division 1**"]},
    )
    reason = next(
        r for r in next(s for s in statuses if s.aspect == "calendar").blocking_reasons
        if "accent" in r
    )
    assert "per-tier-set-colour" in reason


def test_no_shortfall_leaves_the_aspect_enabled():
    statuses = build_aspect_statuses(
        {"calendar": True}, {CALENDAR: _report(slots={"accent"})}, colour_shortfall={}
    )
    assert next(s for s in statuses if s.aspect == "calendar").state == STATE_ENABLED


def test_a_disabled_aspect_still_says_what_it_would_meet():
    """The promise a disabled row makes is what the enabled row would actually say."""
    statuses = build_aspect_statuses(
        {"calendar": False},
        {CALENDAR: _report(slots={"accent"})},
        colour_shortfall={CALENDAR: ["`accent` is not set for **Division 1**"]},
    )
    calendar = next(s for s in statuses if s.aspect == "calendar")
    assert calendar.state == STATE_DISABLED
    assert any("accent" in reason for reason in calendar.disabled_reasons)


def test_a_shortfall_does_not_leak_into_another_aspect():
    statuses = build_aspect_statuses(
        {"calendar": True, "lineup": True},
        {CALENDAR: _report(slots={"accent"}), "lineup_template": _report("lineup_template")},
        colour_shortfall={CALENDAR: ["`accent` is not set for **Division 1**"]},
    )
    lineup = next(s for s in statuses if s.aspect == "lineup")
    assert lineup.blocking_reasons == []


# ── Off by default ────────────────────────────────────────────────────────

async def test_nothing_is_demanded_while_the_feature_is_off():
    """And nothing is read either — an inert feature costs no queries."""
    config = MagicMock(per_tier_colour_enabled=False)
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value=config)
    config_service.get_all_tier_colours = AsyncMock()
    config_service.season_division_names = AsyncMock()

    from services.image_validity_service import ImageValidityService

    service = ImageValidityService(config_service, MagicMock())
    assert await service.colour_shortfall(1, {CALENDAR: _report(slots={"accent"})}) == {}
    config_service.get_all_tier_colours.assert_not_awaited()
    config_service.season_division_names.assert_not_awaited()


async def test_the_shortfall_is_computed_once_the_feature_is_on():
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value=MagicMock(per_tier_colour_enabled=True)
    )
    config_service.get_all_tier_colours = AsyncMock(return_value={})
    config_service.season_division_names = AsyncMock(return_value=["Division 1"])

    from services.image_validity_service import ImageValidityService

    service = ImageValidityService(config_service, MagicMock())
    shortfall = await service.colour_shortfall(1, {CALENDAR: _report(slots={"accent"})})
    assert CALENDAR in shortfall


# ── One rule, three surfaces ──────────────────────────────────────────────

def test_the_shortfall_reaches_every_surface():
    """`/images config view`, `/season review` and the aspect toggle read one list.

    All three render `AspectStatus`, so this asserts the property that makes that safe:
    the reason appears in the rolled-up status, which is the only thing any of them sees.
    Were a surface to grow its own copy of the rule, this would still pass — so it is
    paired with `test_the_aspect_toggle_passes_the_shortfall_through`, which pins the one
    caller that builds its own list.
    """
    shortfall = {CALENDAR: ["`accent` is not set for **Division 1**"]}
    statuses = build_aspect_statuses(
        {"calendar": True}, {CALENDAR: _report(slots={"accent"})},
        colour_shortfall=shortfall,
    )
    calendar = next(s for s in statuses if s.aspect == "calendar")

    # `AspectStatus.reasons` is what both report surfaces render, and says so itself.
    assert any("accent" in reason for reason in calendar.reasons)


def test_the_aspect_toggle_passes_the_shortfall_through():
    """The toggle path that builds its own status list must pass the shortfall too.

    There are two of these and only one is at risk. `_aspect_blocking_reasons` reads
    `aspect_statuses`, which computes the shortfall itself; `_aspect_blocking_reasons_if_enabled`
    overrides the stored toggle and so builds its own list, which is the one that could
    silently omit it and let an aspect be switched on over an unset colour.

    Asserted against the source, in the manner of
    `test_every_asset_class_has_a_command_of_its_own`: the alternative is a live gateway.
    """
    import inspect

    from cogs.image_cog import ImageCog

    assert "colour_shortfall=" in inspect.getsource(
        ImageCog._aspect_blocking_reasons_if_enabled
    )
    # The other reads `aspect_statuses`, which carries it already — pinned so that a
    # refactor which stops it doing so is caught here rather than in production.
    assert "aspect_statuses(" in inspect.getsource(ImageCog._aspect_blocking_reasons)


# ── The season approval gate ──────────────────────────────────────────────

async def test_the_approval_gate_flattens_the_same_shortfall():
    """`/season approve` reads `colour_shortfall`, not a rule of its own."""
    from cogs.season_cog import SeasonCog

    cog = MagicMock(spec=SeasonCog)
    cog.bot = MagicMock()
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(
        return_value={CALENDAR: ["`accent` is not set for **Division 1**"]}
    )
    problems = await SeasonCog._colour_shortfall_problems(cog, 1)
    assert problems == [f"`{CALENDAR}` — `accent` is not set for **Division 1**"]


async def test_the_approval_gate_is_silent_when_nothing_is_short():
    from cogs.season_cog import SeasonCog

    cog = MagicMock(spec=SeasonCog)
    cog.bot = MagicMock()
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(return_value={})
    assert await SeasonCog._colour_shortfall_problems(cog, 1) == []


async def test_a_reader_fault_never_refuses_a_season():
    """The rule of `_team_name_problems`: a check that cannot answer must not block."""
    from cogs.season_cog import SeasonCog

    cog = MagicMock(spec=SeasonCog)
    cog.bot = MagicMock()
    cog.bot.image_validity_service.colour_shortfall = AsyncMock(
        side_effect=RuntimeError("db is gone")
    )
    assert await SeasonCog._colour_shortfall_problems(cog, 1) == []


def test_approval_is_gated_on_it():
    """The gate must actually be wired into `_do_approve`, not merely available."""
    import inspect

    from cogs.season_cog import SeasonCog

    source = inspect.getsource(SeasonCog)
    assert "_colour_shortfall_problems(cfg.server_id)" in source
    assert "Season cannot be approved" in source
