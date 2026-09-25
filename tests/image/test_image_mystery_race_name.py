"""A mystery round is a "Mystery Grand Prix" on every graphic that names it (2026-09-01).

The abbreviation "Mystery GP" was withdrawn: no other grand prix is abbreviated anywhere in
the module, and the check-in call sets the name beside the circuit name in full.

The literal has three homes — the check-in call, the calendar and the verdict — because each
lives with the resolution that draws it rather than in a shared constant, which is deliberate
(see the note on ``CALENDAR_CATALOGUE``). Three homes is three chances to drift, so this
pins them to one another.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_calendar_service import (  # noqa: E402
    MYSTERY_RACE_NAME as CALENDAR_MYSTERY_RACE_NAME,
)
from services.image_rsvp_service import (  # noqa: E402
    MYSTERY_RACE_NAME as RSVP_MYSTERY_RACE_NAME,
)

MYSTERY_GRAND_PRIX = "Mystery Grand Prix"


def test_the_check_in_call_names_the_grand_prix_in_full():
    assert RSVP_MYSTERY_RACE_NAME == MYSTERY_GRAND_PRIX


def test_the_calendar_names_the_grand_prix_in_full():
    assert CALENDAR_MYSTERY_RACE_NAME == MYSTERY_GRAND_PRIX


def test_the_verdict_names_the_grand_prix_in_full():
    """The verdict holds the literal inline, so it is read out of the source."""
    from services import image_verdict_post

    source = inspect.getsource(image_verdict_post)
    assert f'race_name = "{MYSTERY_GRAND_PRIX}"' in source
    assert "Mystery GP" not in source


def test_no_module_still_abbreviates_it():
    from services import image_calendar_service, image_rsvp_service

    for module in (image_rsvp_service, image_calendar_service):
        assert "Mystery GP" not in inspect.getsource(module)
