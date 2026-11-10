"""How core gathers the modules' sections of a season review (#283).

Each module signs its section up to the hook a review gathers its lines through, and core lays
what they hand back out in the order they signed up, which the builder's order makes the review's.
"""

from __future__ import annotations

import pytest

from leaguebot.core.utils.hooks import AwaitedHook

_NOT_YET = "#283: core has no review sections hook nor a reader laying its sections out"


def _hook(*handed_back):
    """A review sections hook whose subscribers hand back *handed_back*, in that order, each
    recording what it was handed."""
    hook = AwaitedHook()
    hook.handed = []
    for each in handed_back:
        async def subscriber(*, review, season_id, division_ids, each=each):
            hook.handed.append((review, season_id, list(division_ids)))
            return each
        hook.subscribe(subscriber)
    return hook


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_the_sections_are_laid_out_in_the_review_s_order():
    """Signup, attendance, results and weather signed up in that order, and a fifth subscriber
    handing nothing: the review reads signup's, attendance's, results' then weather's, each
    subscriber handed the review, the season and the divisions. With weather signed up before
    signup, and only those two handing a section, weather's then signup's: core keeps the order
    they signed up in and sorts by no module's name."""
    from leaguebot.core.services.review_sections import Review, ReviewSection, read_review_sections

    hook = _hook(*(ReviewSection(module=module, lines=[f"**{module}**"])
                   for module in ("signup", "attendance", "results", "weather")), None)

    sections = await read_review_sections(hook, Review.PLACEMENTS, season_id=3,
                                          division_ids=[30])

    assert [section.module for section in sections] == [
        "signup", "attendance", "results", "weather"
    ]
    assert hook.handed == [(Review.PLACEMENTS, 3, [30])] * 5

    hook = _hook(ReviewSection(module="weather", lines=["**weather**"]), None,
                 ReviewSection(module="signup", lines=["**signup**"]))

    sections = await read_review_sections(hook, Review.CONFIGURATION, season_id=3,
                                          division_ids=[])

    assert [section.module for section in sections] == ["weather", "signup"]


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_a_section_that_cannot_be_read_fails_the_review_as_today():
    """A subscriber raising while its section is read: the raise reaches the review command's
    failure path, as a signup, attendance, weather or points reader's raise does today. Core
    adds no catch of its own and no stand-aside."""
    from leaguebot.core.services.review_sections import Review, read_review_sections

    hook = AwaitedHook()

    async def unreadable(**_handed):
        raise RuntimeError("the section's table could not be read")

    hook.subscribe(unreadable)

    with pytest.raises(RuntimeError, match="could not be read"):
        await read_review_sections(hook, Review.PLACEMENTS, season_id=3, division_ids=[])
