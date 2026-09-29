"""The tier a division may take — `season_service.validate_division_tier` (#482).

The rule, the core specification's: a division's tier is 1 or higher, and no two divisions of a
season share one. `/division add` and `/division duplicate` each wrote it in the cog, and
`/division amend` not at all, so an amended division could take tier 0 or another division's
tier. One pure function now holds it, beside `validate_division_name`, and all three commands
use it. Its words are `/division add`'s of today, and `/division amend` refuses with them;
`/division duplicate` keeps its own taken-tier words ("... already exists in this season."),
which no league sees change. What each command replies is pinned by the command's own tests.

Pure tests: no database, no Discord. The caller hands it the tiers the season's *other*
divisions hold, which is how a division amended to keep its own tier is not refused.
"""
from __future__ import annotations

import pytest

_TIER = pytest.mark.xfail(
    strict=True, reason="#482: the division tier rule is not yet a function of its own"
)


def _rule():
    from leaguebot.core.services.season_service import validate_division_tier

    return validate_division_tier


@_TIER
@pytest.mark.parametrize("tier", [0, -1], ids=["zero", "negative"])
def test_a_tier_below_one_is_refused(tier):
    assert _rule()(tier, [2, 3]) == "Tier must be 1 or higher."


@_TIER
def test_a_tier_another_division_holds_is_refused():
    assert _rule()(2, [1, 2]) == "A division with tier **2** already exists in this setup."


@_TIER
def test_a_free_tier_is_allowed():
    assert _rule()(3, [1, 2]) is None


@_TIER
def test_the_first_division_of_a_season_may_take_any_tier_from_one():
    assert _rule()(1, []) is None


@_TIER
def test_a_division_keeping_its_own_tier_is_allowed():
    """`/division amend` of the tier-2 division to tier 2 passes the others' tiers, 1 and 3."""
    assert _rule()(2, [1, 3]) is None
