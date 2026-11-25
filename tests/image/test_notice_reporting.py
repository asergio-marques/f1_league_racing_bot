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

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.image.models.image_module import RenderNotice
from leaguebot.image.services.preview.preview_runner import _log_notices
from leaguebot.image.services.render.image_render_service import (
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
    that — it is only the twenty-way repetition that has nothing to say. It names the field
    and not the template's internal key, which a league never reads."""
    lines = grouped_notice_lines(
        [_notice(detail="no flag for `portugal`", field_id="row_1_flag")]
    )

    assert any("`row_1_flag`" in line for line in lines)
    assert any("portugal" in line for line in lines)
    assert not any("standings_drivers" in line for line in lines), lines


def test_different_kinds_get_their_own_heading():
    """Each kind is headed in plain words, never by its code."""
    notices = [
        _notice(kind="ASSET_FALLBACK_USED", detail="a flag fell back"),
        _notice(kind="FONT_SUBSTITUTED", detail="Formula1 was unavailable"),
    ]

    lines = grouped_notice_lines(notices)
    headings = [line.strip() for line in lines if not line.strip().startswith("•")]

    assert len(headings) == 2, lines
    assert any(h.startswith("Artwork not supplied by the league") for h in headings)
    assert any(h.startswith("Font substituted") for h in headings)
    assert not any("ASSET_FALLBACK_USED" in line or "FONT_SUBSTITUTED" in line for line in lines)
    assert not any("[" in h for h in headings)


#: Every notice kind and the words its heading reads (owner, 2026-11-24). A true fallback and the
#: bot's own artwork share one neutral heading; each notice's own sentence says which.
KIND_HEADINGS = [
    ("FONT_SUBSTITUTED", "Font substituted"),
    ("FIELD_REDUCED", "Text set smaller"),
    ("ASSET_FALLBACK_USED", "Artwork not supplied by the league"),
    ("PACKAGED_ASSET_OFF_SHAPE", "Packaged artwork stretched"),
    ("OPTIONAL_FIELD_EMPTIED", "Field left empty"),
    ("CROP_POINT_OFF_CANVAS", "Crop point off the canvas"),
    ("TIER_COLOURS_NOT_APPLIED", "Tier colours not applied"),
]


def test_every_notice_kind_has_a_heading_case():
    """A kind added without words of its own would be headed by nothing a league reads."""
    from leaguebot.image.models import image_constants

    kinds = {
        value for name, value in vars(image_constants).items() if name.startswith("NOTICE_")
    }
    assert kinds == {kind for kind, _ in KIND_HEADINGS}


@pytest.mark.parametrize(
    ("kind", "words"),
    KIND_HEADINGS,
)
def test_each_kind_is_headed_in_plain_words(kind, words):
    lines = grouped_notice_lines([_notice(kind=kind, detail="something", field_id="f")])

    assert lines[0].strip().startswith(words), lines
    assert not any(kind in line for line in lines), lines


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


#: The member and the command a preview's log entry opens with.
NAMED = "Tester (<@1>)"
WHAT = "`/images test standings`"


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

    await _log_notices(
        bot.output_router.post_log, [("Standings", notices)], named=NAMED, what=WHAT
    )

    assert bot.output_router.post_log.await_count == 1


@pytest.mark.asyncio
async def test_the_posted_message_is_returned_so_a_caller_can_link_to_it():
    bot = _bot()

    message = await _log_notices(
        bot.output_router.post_log, [("Standings", [_notice()])], named=NAMED, what=WHAT
    )

    assert message.jump_url == "http://j/1"


@pytest.mark.asyncio
async def test_nothing_is_posted_and_none_returned_for_no_notices():
    bot = _bot()

    assert await _log_notices(
        bot.output_router.post_log, [("Standings", [])], named=NAMED, what=WHAT
    ) is None
    bot.output_router.post_log.assert_not_awaited()


# ── The standings path no longer floods the log ───────────────────────────


def test_the_standings_path_posts_one_grouped_message():
    """It used to post one Discord message per notice, which a twenty-driver championship
    turned into twenty. The standings' drawing now hands its notices back as the job's lines
    rather than posting them: twenty notices are one line, one grouped block naming what was
    drawn under the render's heading."""
    from leaguebot.image.services.aspects.image_standings_post import lines_of

    notices = [
        _notice(detail="no `marker` image for “gained”", field_id=f"row_{i}_marker")
        for i in range(20)
    ]
    decision = MagicMock(notices=notices, problem=None)

    lines = lines_of("drivers standings — Season 3", decision)

    assert len(lines) == 1
    assert lines[0].startswith("Image render notices — drivers standings — Season 3:")


@pytest.mark.xfail(strict=True, reason="#635: a drawing's notice block is not yet marked as one")
def test_the_standings_notice_block_is_marked_for_the_job_to_frame():
    """The job that drew the picture opens its notice block with the member and the change it
    ran for; the drawing marks the block so the job can tell it from a fault line."""
    from leaguebot.framework.models.change import NoticeBlock
    from leaguebot.image.services.aspects.image_standings_post import lines_of

    decision = MagicMock(notices=[_notice()], problem=None)

    (line,) = lines_of("drivers standings — Season 3", decision)

    assert isinstance(line, NoticeBlock)
