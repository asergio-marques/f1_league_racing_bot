"""A mandatory field inside a removed group must not be demanded.

Constitution XIV.2 is explicit: where a removable group is declared, it "MUST be removed in
its entirety wherever the rules would have the field emptied or removed", and for a
mandatory group "its removal when the data are empty is the ordinary behaviour of a group
and is not a failure".

`_verify_against_data` subtracted `spec.remove` from the mandatory sweep, but that list
carries only the ids of the nodes removed — a group's **children** keep ids of their own and
stayed in the sweep. Every lineup for a division fielding no reserve driver was therefore
abandoned with "no value could be determined for `reserve_driver_1_name`", against the
shipped template, on the posting path as much as on the preview.

Nothing else caught it: the lineup suites use templates whose reserve block differs, and the
preview suites stop at assembling the fill spec, one step before this check.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_lineup_service import build_fill_spec, resolve_drawing  # noqa: E402
from services.image_render_service import (  # noqa: E402
    _removed_field_ids,
    _verify_against_data,
)
from utils.svg_document import FieldIndex  # noqa: E402

LINEUP_TEMPLATE = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "defaults", "templates", "lineup_template.svg"
)

#: The eleven teams the shipped lineup template names its fields after. A division whose
#: teams do not match is a different fault, reported before this one.
SHIPPED_TEAMS = (
    "Apex Racing",
    "Aurora Racing",
    "Basalt Motorsport",
    "Halcyon GP",
    "Ironclad Racing",
    "Kestrel GP",
    "Meridian GP",
    "Nimbus Racing",
    "Nordwind Motorsport",
    "Solstice Motorsport",
    "Vanguard Racing",
)


def _seat(seat_number, *, occupied, uid=0):
    """One seat. *seat_number* is the seat's number **within its team** — 1 or 2 — while
    *uid* only has to be unique across the division."""
    if not occupied:
        return SimpleNamespace(
            seat_number=seat_number,
            discord_user_id=None,
            profile_id=None,
            server_display_name=None,
            discord_username=None,
            test_display_name=None,
            nationality=None,
        )
    return SimpleNamespace(
        seat_number=seat_number,
        discord_user_id=9000 + uid,
        profile_id=9000 + uid,
        server_display_name=f"Driver {uid}",
        discord_username="driver",
        test_display_name=None,
        nationality="British",
    )


def _division(reserve_seats):
    teams = []
    for index, name in enumerate(SHIPPED_TEAMS):
        teams.append(
            SimpleNamespace(
                name=name,
                is_reserve=False,
                seats=[
                    _seat(s, occupied=True, uid=index * 2 + s) for s in (1, 2)
                ],
            )
        )
    teams.append(SimpleNamespace(name="Reserve", is_reserve=True, seats=reserve_seats))
    return teams


def _verify(teams):
    root = etree.parse(LINEUP_TEMPLATE).getroot()
    drawing = resolve_drawing(
        division_name="Premier",
        division_tier=1,
        season_number=1,
        teams=teams,
        display_names={},
        nationality_collected=True,
    )
    spec = build_fill_spec(drawing, root, asset_directories={})
    return root, spec, _verify_against_data(root, spec, "lineup_template")


class TestARemovedGroupTakesItsFieldsWithIt:
    def test_a_division_fielding_no_reserve_driver_renders(self):
        """`seed_division_teams` creates no seats for a reserve team, so this is the
        shape every newly-created division has."""
        _root, spec, problem = _verify(_division([]))

        assert "reserve_group" in spec.remove
        assert problem is None

    def test_a_reserve_team_with_empty_seats_renders(self):
        _root, _spec, problem = _verify(
            _division([_seat(s, occupied=False) for s in (1, 2)])
        )

        assert problem is None

    def test_a_division_with_no_reserve_team_at_all_renders(self):
        """What a fabricated league builds — it draws only the fielded teams."""
        teams = [t for t in _division([]) if not t.is_reserve]

        root = etree.parse(LINEUP_TEMPLATE).getroot()
        drawing = resolve_drawing(
            division_name="Premier",
            division_tier=1,
            season_number=1,
            teams=teams,
            display_names={},
            nationality_collected=True,
        )
        spec = build_fill_spec(drawing, root, asset_directories={})

        assert _verify_against_data(root, spec, "lineup_template") is None

    def test_a_fielded_reserve_driver_is_still_drawn(self):
        """The group is only removed when the reserve fields nobody — the check must not
        have been loosened into never demanding the field at all."""
        _root, spec, problem = _verify(_division([_seat(1, occupied=True, uid=500)]))

        assert "reserve_group" not in spec.remove
        assert problem is None
        assert "reserve_driver_1_name" in spec.text


class TestRemovedFieldIds:
    def test_it_gathers_the_children_of_a_removed_group(self):
        root = etree.parse(LINEUP_TEMPLATE).getroot()
        index = FieldIndex(root)

        gone = _removed_field_ids(index, ["reserve_group"])

        assert "reserve_group" in gone
        assert "reserve_driver_1_name" in gone

    def test_a_name_the_template_does_not_declare_is_kept_as_given(self):
        """A removal of something absent must not raise, and must not sweep anything in."""
        root = etree.parse(LINEUP_TEMPLATE).getroot()
        index = FieldIndex(root)

        assert _removed_field_ids(index, ["not_a_field"]) == {"not_a_field"}

    def test_nothing_removed_gathers_nothing(self):
        root = etree.parse(LINEUP_TEMPLATE).getroot()

        assert _removed_field_ids(FieldIndex(root), []) == set()

    def test_it_does_not_reach_outside_what_was_removed(self):
        """Removing the reserve block must not excuse a team's own fields."""
        root = etree.parse(LINEUP_TEMPLATE).getroot()
        index = FieldIndex(root)

        gone = _removed_field_ids(index, ["reserve_group"])

        assert "team_apex_racing_name" not in gone
        assert "division_name" not in gone
