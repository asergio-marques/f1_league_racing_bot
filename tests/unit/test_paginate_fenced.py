"""Splitting a fenced listing across Discord's 2000-character message limit.

Discord answers an oversized message body with a 400 rather than truncating it, so a
listing that outgrows one message loses the *whole* listing, not its tail. `/test-mode
roster list` reached that at a little over twenty drivers and replied nothing at all.

Splitting has to happen on whole lines: a break inside a row, or inside the ``` fence,
renders as garbage rather than as a table.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.message_builder import DISCORD_MESSAGE_LIMIT, paginate_fenced  # noqa: E402

HEADER = "**Fake Driver Roster — Challenger**"
FOOTER = (
    "Copy mention strings above when submitting results in the format:\n"
    "`Position, <@user_id>, <@&role_id>, ...`"
)


def _roster(count: int) -> list[str]:
    """A roster body shaped like the real one — the header rows and *count* drivers."""
    body = [f"{'Name':<20} {'Mention':<30} {'Team':<20} Nationality", "-" * 86]
    for i in range(count):
        body.append(
            f"{'Driver ' + str(i):<20} {'<@90000000000000' + str(1000 + i) + '>':<30} "
            f"{'Aston Martin':<20} Palestinian"
        )
    return body


@pytest.mark.parametrize("count", [0, 1, 5, 21, 23, 60, 200])
def test_every_page_fits_the_limit(count):
    pages = paginate_fenced(HEADER, _roster(count), footer=FOOTER)
    assert pages, "at least one page is always returned"
    for page in pages:
        assert len(page) <= DISCORD_MESSAGE_LIMIT, f"page of {len(page)} chars"


@pytest.mark.parametrize("count", [1, 23, 60])
def test_no_row_is_lost_or_duplicated(count):
    body = _roster(count)
    pages = paginate_fenced(HEADER, body, footer=FOOTER)

    emitted: list[str] = []
    for page in pages:
        inner = page.split("```")[1]
        emitted.extend(line for line in inner.split("\n") if line)

    assert emitted == body


@pytest.mark.parametrize("count", [1, 23, 60])
def test_every_page_is_a_closed_fence(count):
    """An unbalanced fence renders the rest of the message as code, or not as code."""
    for page in paginate_fenced(HEADER, _roster(count), footer=FOOTER):
        assert page.count("```") == 2


@pytest.mark.parametrize("count", [1, 23, 60])
def test_the_header_repeats_so_a_page_stands_alone(count):
    for page in paginate_fenced(HEADER, _roster(count), footer=FOOTER):
        assert page.startswith(HEADER)


def test_the_footer_lands_on_the_last_page_only():
    """It explains what to do with the listing as a whole, not with one page of it."""
    pages = paginate_fenced(HEADER, _roster(60), footer=FOOTER)
    assert len(pages) > 1, "this roster must actually span several pages"
    assert pages[-1].endswith(FOOTER)
    assert not any(FOOTER in page for page in pages[:-1])


def test_a_roster_that_fits_is_left_as_one_page():
    """The common case must not be split for no reason."""
    pages = paginate_fenced(HEADER, _roster(5), footer=FOOTER)
    assert len(pages) == 1


def test_the_reported_roster_no_longer_overflows():
    """The size that produced the 400: 23 drivers in one division."""
    pages = paginate_fenced(HEADER, _roster(23), footer=FOOTER)
    assert len(pages) > 1
    assert all(len(page) <= DISCORD_MESSAGE_LIMIT for page in pages)


def test_a_single_unsplittable_line_is_emitted_rather_than_dropped():
    """One mangled row beats losing the listing; Discord truncates it, we do not."""
    monster = "x" * (DISCORD_MESSAGE_LIMIT * 2)
    pages = paginate_fenced(HEADER, ["short", monster, "also short"])
    assert any(monster in page for page in pages)


def test_an_empty_body_still_returns_a_sendable_page():
    pages = paginate_fenced(HEADER, [], footer=FOOTER)
    assert len(pages) == 1
    assert pages[0].startswith(HEADER)
    assert pages[0].count("```") == 2


def test_a_page_break_never_falls_inside_a_row():
    """Rows are atomic — every emitted line is one of the ones handed in."""
    body = _roster(60)
    for page in paginate_fenced(HEADER, body, footer=FOOTER):
        inner = page.split("```")[1]
        for line in (item for item in inner.split("\n") if item):
            assert line in body
