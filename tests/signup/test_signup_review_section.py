"""Signup's section of a season review, handed to core through the review sections hook (#283).

Its words are those the season cog wrote until the section moved into the module.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_NOT_YET = "#283: signup hands no review section; the season cog writes it"

_LINES = [
    "**Signup Config**",
    "  • Channel: <#900>",
    "  • Time type: Time Trial",
    "  • Time image: Required",
    "  • Nationality: Not required",
    "  • Available slots: Sunday 20:00",
    "",
]


def _service(*, configured: bool = True) -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(
        return_value=SimpleNamespace(signup_channel_id=900) if configured else None
    )
    service.get_settings = AsyncMock(return_value=SimpleNamespace(
        time_type="TIME_TRIAL", time_image_required=True, nationality_required=False
    ))
    service.get_slots = AsyncMock(
        return_value=[SimpleNamespace(display_label="Sunday 20:00")]
    )
    return service


def _modules(on: bool) -> MagicMock:
    modules = MagicMock()
    modules.is_signup_enabled = AsyncMock(return_value=on)
    return modules


async def _section(service, on: bool, review: str):
    from leaguebot.core.services.review_sections import Review
    from leaguebot.signup.services.signup_module_service import review_section

    return await review_section(service, _modules(on), review=Review[review], season_id=1,
                                division_ids=[10])


@pytest.mark.parametrize(("review", "note"), [
    pytest.param("PLACEMENTS", "Channel: <#900>",
                 marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
    pytest.param("CONFIGURATION", None, marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
])
async def test_signup_hands_its_section_to_each_review(review, note):
    """Signup on, its channel set, its settings and one slot: each review gets the Signup Config
    section; the placements review also gets the channel to show beside "Signup: ✅ Enabled" in
    its Modules section, which the configuration review does not show."""
    section = await _section(_service(), True, review)

    assert section.module == "signup"
    assert section.lines == _LINES
    assert section.modules_note == note
    assert dict(section.division_lines) == {}


@pytest.mark.xfail(strict=True, reason=_NOT_YET)
async def test_a_placements_review_with_no_signup_configuration_names_no_channel():
    """Signup on and never configured: no channel beside its line in the Modules section, and the
    Signup Config section says the channel is not configured."""
    section = await _section(_service(configured=False), True, "PLACEMENTS")

    assert section.modules_note is None
    assert section.lines[1] == "  • Channel: *(not configured)*"


@pytest.mark.parametrize("review", [
    pytest.param("PLACEMENTS", marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
    pytest.param("CONFIGURATION", marks=pytest.mark.xfail(strict=True, reason=_NOT_YET)),
])
async def test_signup_hands_nothing_while_it_is_off(review):
    """Signup off: no section, and nothing of signup's is read."""
    service = _service()

    assert await _section(service, False, review) is None
    service.get_config.assert_not_awaited()
