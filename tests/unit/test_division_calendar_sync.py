"""`/division calendar-sync`: posting a division's calendar again, on request.

Issue #208. The command was uncovered. It exists for the calendar that went missing or went
stale — a channel purged, a round amended — and it refreshes whichever form the league's
configuration calls for: the graphic where the image module and the `calendar` aspect are on,
the text otherwise.

**It is gated on no module.** The calendar is a core output, and a league that runs no optional
module at all still has one.

**It is a commanded posting, so a render fault refuses** (Constitution XIV.7). The person at the
keyboard is the one able to fix the template, so they are told what is wrong and nothing is
posted — and the refusal says that the previous calendar still stands, because a manager who
has just been refused will otherwise assume the channel is now empty.

**The reply says which form it posted in.** A manager expecting a graphic and getting text
needs to know that is what happened, rather than finding out by looking at the channel.

**The season number it draws with is read from an attribute the season does not have.** The
command passes `getattr(season, "number", None)`; the `Season` model's field is
`season_number`. So a resynced graphic is drawn with no season number, where the copy shown at
`/season placements-review` carries it. `test_the_season_number_is_not_passed_today` pins that as it stands
— the same is true of the calendar approval posts, which pass none at all — and it is
recorded as issue #213 rather than corrected here, since #208 is a coverage change.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from models.season import Season, SeasonStage, SeasonStatus  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 13208


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _season(stage: SeasonStage = SeasonStage.ONGOING):
    """The season the command finds. A stage is given because the command now reads one.

    Nothing in production hands out a stageless season — the schema's triggers fill the
    column on every insert — so a `Season()` left at the dataclass default would be a
    fixture the bot never produces, and would refuse for a reason no league can meet.
    """
    return Season(
        id=1,
        start_date=date(2026, 1, 1),
        status=SeasonStatus.ACTIVE,
        season_number=7,
        stage=stage,
    )


def _division(*, name="Pro", calendar=700):
    return SimpleNamespace(id=11, name=name, tier=1, calendar_channel_id=calendar)


_UNSET = object()


def _make_cog(*, season=_UNSET, divisions=None):
    bot = MagicMock()
    bot.db_path = "/tmp/not-read.db"
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=_season() if season is _UNSET else season
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=["round"])
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _posting(*, problem=None, image=False, notices=()):
    return SimpleNamespace(problem=problem, posted_as_image=image, notices=list(notices))


async def _sync(cog, interaction, *, name="Pro", posting=None):
    with patch(
        "services.calendar_post_service.tracks_by_name", new=AsyncMock(return_value={})
    ), patch(
        "services.calendar_post_service.post_division_calendar",
        new=AsyncMock(return_value=posting or _posting()),
    ) as post:
        await undecorate(SeasonCog.division_calendar_sync)(cog, interaction, name)
    return post


def _replied(interaction) -> str:
    return "\n".join(
        str(c.args[0]) for c in interaction.followup.send.await_args_list if c.args
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_the_calendar_is_reposted(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    post = await _sync(cog, interaction)

    post.assert_awaited_once()
    assert "Calendar for **Pro** reposted as text" in _replied(interaction)


async def test_it_is_a_commanded_posting(tmp_path):
    """So a render fault refuses rather than quietly substituting text (XIV.7)."""
    cog = _make_cog()

    post = await _sync(cog, _interaction())

    assert post.await_args.kwargs["commanded"] is True


async def test_the_reply_says_it_was_posted_as_an_image(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction, posting=_posting(image=True))

    assert "reposted as image" in _replied(interaction)


async def test_a_refused_render_posts_nothing_and_says_the_old_calendar_stands(tmp_path):
    """A manager who has just been refused will otherwise assume the channel is empty."""
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction, posting=_posting(problem="the template has no rows"))

    replied = _replied(interaction)
    assert "was not posted — the template has no rows" in replied
    assert "previous calendar still stands" in replied
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_render_notices_are_shown_and_logged(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction, posting=_posting(notices=["round 12 did not fit"]))

    assert "round 12 did not fit" in _replied(interaction)
    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "notice: round 12 did not fit" in logged


async def test_a_successful_sync_is_logged(tmp_path):
    cog = _make_cog()

    await _sync(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division calendar-sync | Success" in logged
    assert "posted as: text" in logged


async def test_a_server_with_no_season_is_refused(tmp_path):
    cog = _make_cog(season=None)
    interaction = _interaction()

    post = await _sync(cog, interaction)

    # The shared gate's wording (issue #224): the same branch a server whose only
    # season is completed or cancelled reaches, because an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)
    post.assert_not_awaited()


async def test_an_unknown_division_is_refused(tmp_path):
    cog = _make_cog()
    interaction = _interaction()

    post = await _sync(cog, interaction, name="Rookie")

    assert "Division **Rookie** not found" in _replied(interaction)
    post.assert_not_awaited()


async def test_the_division_is_matched_regardless_of_case(tmp_path):
    cog = _make_cog()

    post = await _sync(cog, _interaction(), name="pRO")

    post.assert_awaited_once()


async def test_a_division_with_no_calendar_channel_says_how_to_set_one(tmp_path):
    cog = _make_cog(divisions=[_division(calendar=None)])
    interaction = _interaction()

    post = await _sync(cog, interaction)

    assert "/division calendar-channel" in _replied(interaction)
    post.assert_not_awaited()


async def test_the_command_defers_before_working(tmp_path):
    """A render can take seconds on the Pi."""
    cog = _make_cog()
    interaction = _interaction()

    await _sync(cog, interaction)

    interaction.response.defer.assert_awaited_once()


async def test_the_season_number_is_not_passed_today(tmp_path):
    """**A defect, pinned as it stands.** The command reads `season.number`; the model's
    field is `season_number`, so a resynced graphic draws with no season number. Written to
    fail when that is corrected, so whoever corrects it moves the assertion with the code."""
    assert not hasattr(_season(), "number")
    cog = _make_cog()

    post = await _sync(cog, _interaction())

    assert post.await_args.kwargs["season_number"] is None
