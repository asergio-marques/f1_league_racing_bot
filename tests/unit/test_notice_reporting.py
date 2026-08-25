"""How render notices are reported — grouping, subject, and the link back to the log.

Notices are a degradation audit (Constitution XIV.4, Principle V). Three things govern how
they surface, and each is pinned here:

* **Where.** The calculation log on every path; a command's own reply only for
  `/images test`. A channel drivers read, never (FR-032).
* **How.** Grouped by kind, with identical culprits counted rather than repeated. A render
  degrades the same way many times over — twenty drivers whose markers all come from one
  directory produce twenty identical notices — and a line apiece buries the one that
  differs among nineteen that do not.
* **What is kept.** A notice standing alone still names its field id; the block still names
  the subject a posting path was drawing. Grouping is presentation and must cost neither.

`_persist` is unaffected throughout: the audit trail keeps one row per notice.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_module import RenderNotice  # noqa: E402
from services.image_render_service import (  # noqa: E402
    ImageRenderService,
    grouped_notice_lines,
)


def _notice(kind="ASSET_FALLBACK_USED", detail="a flag fell back", field_id=None,
            image_type="standings_drivers"):
    return RenderNotice(
        image_type=image_type, notice_kind=kind, detail=detail, field_id=field_id
    )


# ── Grouping ──────────────────────────────────────────────────────────────


def test_identical_culprits_are_counted_rather_than_repeated():
    """The case this exists for: a twenty-driver standings image."""
    notices = [
        _notice(detail="no `marker` image for “gained”", field_id=f"row_{i}_marker")
        for i in range(20)
    ]

    lines = grouped_notice_lines(notices)

    assert len(lines) == 2, lines
    assert "×20" in lines[0]
    assert "(×20)" in lines[1]


def test_a_lone_notice_still_names_its_field():
    """XIV.4: a notice that stands alone says where it happened. Grouping must not cost
    that — it is only the twenty-way repetition that has nothing to say."""
    lines = grouped_notice_lines(
        [_notice(detail="no flag for `portugal`", field_id="row_1_flag")]
    )

    assert any("row_1_flag" in line for line in lines)
    assert any("portugal" in line for line in lines)


def test_different_kinds_get_their_own_heading():
    notices = [
        _notice(kind="ASSET_FALLBACK_USED", detail="a flag fell back"),
        _notice(kind="FONT_SUBSTITUTED", detail="Formula1 was unavailable"),
    ]

    lines = grouped_notice_lines(notices)
    headings = [line for line in lines if line.strip().startswith("[")]

    assert len(headings) == 2
    assert any("ASSET_FALLBACK_USED" in h for h in headings)
    assert any("FONT_SUBSTITUTED" in h for h in headings)


def test_one_kind_with_two_distinct_culprits_keeps_both():
    """Grouping collapses repetition, never distinct information."""
    notices = [
        _notice(detail="no `team` image for “Red Bull”", field_id="a"),
        _notice(detail="no `team` image for “Ferrari”", field_id="b"),
    ]

    lines = grouped_notice_lines(notices)

    assert any("Red Bull" in line for line in lines)
    assert any("Ferrari" in line for line in lines)


def test_the_twenty_driver_case_is_dramatically_shorter_than_a_line_apiece():
    notices = [
        _notice(detail="no `marker` image for “gained”", field_id=f"row_{i}_marker")
        for i in range(20)
    ] + [_notice(detail="no `team` image for “Red Bull”", field_id="t1")]

    assert len(grouped_notice_lines(notices)) < len(notices)


def test_no_notices_is_no_lines():
    assert grouped_notice_lines([]) == []


# ── The subject ───────────────────────────────────────────────────────────


def test_the_block_names_what_was_being_drawn():
    """The image type names the template; it does not name the championship or session a
    reader is looking for in a log of many."""
    text = ImageRenderService.format_notices(
        [_notice()], subject="drivers standings — Season 3"
    )

    assert "drivers standings — Season 3" in text


def test_the_block_stands_without_a_subject():
    text = ImageRenderService.format_notices([_notice()])

    assert text.startswith("Image render notices:")


# ── Reporting to the log ──────────────────────────────────────────────────


def _bot():
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=MagicMock(jump_url="http://j/1"))
    return bot


@pytest.mark.asyncio
async def test_twenty_notices_post_one_message_not_twenty():
    bot = _bot()
    notices = [
        _notice(detail="no `marker` image for “gained”", field_id=f"row_{i}_marker")
        for i in range(20)
    ]

    await ImageRenderService.report_notices(bot, 1, notices)

    assert bot.output_router.post_log.await_count == 1


@pytest.mark.asyncio
async def test_the_posted_message_is_returned_so_a_caller_can_link_to_it():
    bot = _bot()

    message = await ImageRenderService.report_notices(bot, 1, [_notice()])

    assert message.jump_url == "http://j/1"


@pytest.mark.asyncio
async def test_nothing_is_posted_and_none_returned_for_no_notices():
    bot = _bot()

    assert await ImageRenderService.report_notices(bot, 1, []) is None
    bot.output_router.post_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_log_failure_is_swallowed_and_reported_as_none():
    """A log-channel problem must never break the render that produced the notices."""
    bot = MagicMock()
    bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no permission"))

    assert await ImageRenderService.report_notices(bot, 1, [_notice()]) is None


# ── The standings path no longer floods the log ───────────────────────────


@pytest.mark.asyncio
async def test_the_standings_path_posts_one_grouped_message():
    """It used to post one Discord message per notice, which a twenty-driver championship
    turned into twenty."""
    from services.image_standings_post import report_notices

    bot = _bot()
    notices = [
        _notice(detail="no `marker` image for “gained”", field_id=f"row_{i}_marker")
        for i in range(20)
    ]

    await report_notices(bot, 1, "drivers standings — Season 3", notices)

    assert bot.output_router.post_log.await_count == 1
    posted = bot.output_router.post_log.await_args.args[1]
    assert "drivers standings — Season 3" in posted
