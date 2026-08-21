"""Unit tests for lineup resolution — T014.

Covers:
  1. The five-link driver-name chain, in order, and its refusal to emit a mention.
  2. Seats drawn in ascending seat number, not joining order.
  3. An unoccupied seat resolved as unoccupied rather than omitted.
  4. Two teams normalising to one key — fatal, before any template is touched.
  5. The reserve block present only where the division fields a reserve driver.
  6. The nationality suppression flag.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_lineup_service import (
    LineupDataError,
    resolve_driver_name,
    resolve_drawing,
)


def _seat(number: int, uid: str | None = None, **extra):
    if uid is None:
        return NS(seat_number=number, discord_user_id=None)
    values = dict(
        discord_user_id=uid,
        server_display_name=None,
        discord_username=None,
        test_display_name=None,
        nationality="British",
    )
    values.update(extra)
    return NS(seat_number=number, **values)


def _team(name: str, seats, is_reserve: bool = False):
    return NS(name=name, is_reserve=is_reserve, seats=seats)


def _draw(teams, **kwargs):
    return resolve_drawing(division_name="Elite", teams=teams, **kwargs)


# ── The driver-name chain ─────────────────────────────────────────────────


def test_the_server_display_name_wins():
    assert (
        resolve_driver_name(
            discord_user_id="7",
            display_name="Nick",
            signup_display_name="Signup",
            signup_username="user",
            test_display_name="Test",
        )
        == "Nick"
    )


def test_the_chain_falls_through_in_order():
    assert (
        resolve_driver_name(
            discord_user_id="7", signup_display_name="Signup", signup_username="user"
        )
        == "Signup"
    )
    assert resolve_driver_name(discord_user_id="7", signup_username="user") == "user"
    assert resolve_driver_name(discord_user_id="7", test_display_name="Test") == "Test"


def test_the_chain_ends_at_the_user_id_and_never_at_nothing():
    """An image cannot carry a mention, so every driver is named — always."""
    assert resolve_driver_name(discord_user_id="7") == "7"


def test_a_blank_link_is_skipped():
    assert resolve_driver_name(discord_user_id="7", display_name="   ") == "7"


def test_no_resolved_name_ever_looks_like_a_mention():
    name = resolve_driver_name(discord_user_id="7")
    assert not name.startswith("<@")


# ── Seats ─────────────────────────────────────────────────────────────────


def test_seats_are_drawn_in_ascending_seat_number():
    """A reserve seat vacated and reused draws in its seat's place, not last."""
    team = _team("Red Bull", [_seat(3, "c"), _seat(1, "a"), _seat(2, "b")])
    seats = _draw([team]).teams[0].seats
    assert [s.seat_number for s in seats] == [1, 2, 3]


def test_an_unoccupied_seat_is_resolved_not_omitted():
    team = _team("Red Bull", [_seat(1, "a"), _seat(2)])
    seats = _draw([team]).teams[0].seats
    assert len(seats) == 2
    assert seats[1].occupied is False
    assert seats[1].driver_name == ""
    assert seats[1].flag_datum is None
    assert seats[1].portrait_datum is None


def test_a_team_that_seats_nobody_is_kept_with_every_seat_unoccupied():
    drawing = _draw([_team("Haas", [_seat(1), _seat(2)])])
    assert len(drawing.teams) == 1
    assert drawing.teams[0].occupied_count == 0


def test_the_portrait_is_keyed_on_the_user_id_not_the_name():
    seat = _draw([_team("Red Bull", [_seat(1, "424242", server_display_name="Max")])]).teams[0].seats[0]
    assert seat.portrait_datum == "424242"


# ── Ordinals (047) ────────────────────────────────────────────────────────


def test_a_team_takes_the_ordinal_of_its_position():
    """FR-006: the block a team fills is where it stands in the division's list."""
    drawing = _draw(
        [
            _team("Red Bull", [_seat(1, "a")]),
            _team("Haas", [_seat(1, "b")]),
            _team("Alpine", [_seat(1, "c")]),
        ]
    )
    assert [t.ordinal for t in drawing.teams] == [1, 2, 3]
    assert [t.display_name for t in drawing.teams] == ["Red Bull", "Haas", "Alpine"]


def test_the_name_is_carried_for_the_badge_and_not_for_a_field():
    """FR-026: the name reaches the module as a filename and in no other way."""
    drawing = _draw([_team("Force India (B)", [_seat(1, "a")])])
    assert drawing.teams[0].image_datum == "Force India (B)"
    assert drawing.teams[0].ordinal == 1


def test_two_teams_normalising_alike_are_no_longer_fatal():
    """They draw the same badge, which is a naming fault reported at `season review`.

    It was fatal while the normalised name *was* the template field and the two would
    have collided on it. A render is not the moment to discover a naming problem, and
    refusing the graphic would punish the wrong occasion.
    """
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Red  Bull!", [_seat(1, "b")])]
    )
    assert [t.ordinal for t in drawing.teams] == [1, 2]


def test_a_team_normalising_to_the_reserved_word_is_no_longer_fatal():
    drawing = _draw([_team("Reserve!", [_seat(1, "a")])])
    assert drawing.teams[0].ordinal == 1


def test_a_team_normalising_to_nothing_is_no_longer_fatal():
    drawing = _draw([_team("!!!", [_seat(1, "a")])])
    assert drawing.teams[0].ordinal == 1


# ── The reserve block ─────────────────────────────────────────────────────


def test_a_division_with_reserve_drivers_carries_the_block():
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [_seat(1, "r")], True)]
    )
    assert drawing.reserve is not None
    assert drawing.reserve.is_reserve is True


def test_a_division_with_no_reserve_driver_carries_no_block():
    """This is what removes `reserve_group` in its entirety (FR-004)."""
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [], True)]
    )
    assert drawing.reserve is None


def test_the_reserve_team_occupies_no_ordinal():
    """FR-005: it is a singleton and is never addressed as `team_<x>_`."""
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [_seat(1, "r")], True)]
    )
    assert [t.ordinal for t in drawing.teams] == [1]
    assert drawing.reserve.ordinal == 0


def test_the_reserve_does_not_consume_a_position():
    """A reserve listed first must not push the real teams along by one."""
    drawing = _draw(
        [
            _team("Reserve", [_seat(1, "r")], True),
            _team("Red Bull", [_seat(1, "a")]),
            _team("Haas", [_seat(1, "b")]),
        ]
    )
    assert [(t.ordinal, t.display_name) for t in drawing.teams] == [
        (1, "Red Bull"),
        (2, "Haas"),
    ]


# ── Nationality ───────────────────────────────────────────────────────────


def test_a_recorded_nationality_becomes_the_country_flag_datum():
    """The datum is the driver's country, not their nationality (044, US1).

    One flag directory serves a driver and a round alike, so both must ask it for
    the same filename.
    """
    seat = _draw([_team("Red Bull", [_seat(1, "a", nationality="Dutch")])]).teams[0].seats[0]
    assert seat.flag_datum == "Netherlands"


def test_the_country_datum_uses_the_track_registry_spelling():
    """``British`` yields the spelling ``tracks.country`` holds, so that a British
    driver and the British Grand Prix resolve one file (research R-001)."""
    seat = _draw([_team("Red Bull", [_seat(1, "a", nationality="British")])]).teams[0].seats[0]
    assert seat.flag_datum == "United Kingdom"


def test_other_is_a_datum_like_any_other():
    """``Other`` is not a country and gains none; it is carried through."""
    seat = _draw([_team("Red Bull", [_seat(1, "a", nationality="Other")])]).teams[0].seats[0]
    assert seat.flag_datum == "Other"
    assert seat.flag_missing is False


def test_a_missing_nationality_is_reportable_while_the_league_collects_it():
    seat = _draw([_team("Red Bull", [_seat(1, "a", nationality=None)])]).teams[0].seats[0]
    assert seat.flag_datum is None
    assert seat.flag_missing is True


def test_a_missing_nationality_is_not_reportable_when_collection_is_off():
    """XIV.4, v4.3.0 — a configured absence raises no notice."""
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a", nationality=None)])],
        nationality_collected=False,
    )
    assert drawing.teams[0].seats[0].flag_missing is False
    assert drawing.nationality_collected is False

# ── Overflow: the data measured against the template (047 FR-011, FR-012) ──

SVG_NS = "http://www.w3.org/2000/svg"


def _tpl(blocks: int, seats: int, reserve_slots: int = 2, *, groups: bool = True):
    from lxml import etree

    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")

    def node(field_id, tag="text"):
        child = etree.SubElement(root, f"{{{SVG_NS}}}{tag}")
        child.set("id", field_id)

    node("division_name")
    for block in range(1, blocks + 1):
        if groups:
            node(f"team_{block}_group", "g")
        node(f"team_{block}_name")
        node(f"team_{block}_image", "image")
        for seat in range(1, seats + 1):
            node(f"team_{block}_driver_{seat}_name")
            node(f"team_{block}_driver_{seat}_flag", "image")
            node(f"team_{block}_driver_{seat}_image", "image")
    node("reserve_group", "g")
    node("reserve_name")
    for slot in range(1, reserve_slots + 1):
        node(f"reserve_driver_{slot}_name")
        node(f"reserve_driver_{slot}_flag", "image")
        node(f"reserve_driver_{slot}_image", "image")
    return root


def test_more_teams_than_blocks_is_fatal_and_names_the_teams():
    """FR-011. A count would not tell a manager which team vanished."""
    from services.image_lineup_service import build_fill_spec

    drawing = _draw([_team(f"Team {n}", [_seat(1, str(n))]) for n in range(1, 5)])
    with pytest.raises(LineupDataError) as exc:
        build_fill_spec(drawing, _tpl(blocks=2, seats=2))

    message = str(exc.value)
    assert "Team 3" in message and "Team 4" in message
    assert "2 team blocks" in message


def test_more_drivers_than_slots_is_fatal_and_names_the_drivers():
    """FR-012. The drivers, not the seats: a driver is what would be dropped."""
    from services.image_lineup_service import build_fill_spec

    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a"), _seat(2, "b"), _seat(3, "c")])],
        display_names={"a": "Ann", "b": "Ben", "c": "Cal"},
    )
    with pytest.raises(LineupDataError) as exc:
        build_fill_spec(drawing, _tpl(blocks=2, seats=2))

    assert "Cal" in str(exc.value)


def test_configured_seats_beyond_the_block_are_not_fatal_while_nobody_fills_them():
    """FR-012 and FR-018: only a driver who would be dropped is fatal.

    Three configured seats, two occupied, a block declaring two slots. Nobody is dropped,
    so the graphic draws — the empty third seat simply is not shown.
    """
    from services.image_lineup_service import build_fill_spec

    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a"), _seat(2, "b"), _seat(3)])],
        display_names={"a": "Ann", "b": "Ben"},
    )
    spec = build_fill_spec(drawing, _tpl(blocks=1, seats=2))

    assert spec.text["team_1_driver_1_name"] == "Ann"
    assert spec.text["team_1_driver_2_name"] == "Ben"


def test_a_division_of_fewer_teams_draws_without_error():
    """FR-016: the ordinary case of a league whose divisions differ in size."""
    from services.image_lineup_service import build_fill_spec

    drawing = _draw([_team("Red Bull", [_seat(1, "a")])])
    spec = build_fill_spec(drawing, _tpl(blocks=4, seats=2))

    assert spec.text["team_1_name"] == "Red Bull"
    for ordinal in (2, 3, 4):
        assert f"team_{ordinal}_group" in spec.remove


def test_a_division_fielding_no_team_at_all_draws():
    """FR-016 has no lower bound: every block goes, the reserve block alone remaining."""
    from services.image_lineup_service import build_fill_spec

    drawing = _draw([_team("Reserve", [_seat(1, "r")], True)])
    spec = build_fill_spec(drawing, _tpl(blocks=3, seats=2))

    for ordinal in (1, 2, 3):
        assert f"team_{ordinal}_group" in spec.remove
    assert "reserve_group" not in spec.remove


def test_the_team_count_is_carried_for_the_generic_guard():
    """A backstop behind the named check above."""
    from services.image_lineup_service import build_fill_spec

    drawing = _draw([_team("A", [_seat(1, "a")]), _team("B", [_seat(1, "b")])])
    spec = build_fill_spec(drawing, _tpl(blocks=4, seats=2))

    assert spec.row_count == 2


# ── Test mode and a season pending approval (047 FR-046 to FR-049) ─────────


def test_a_driver_created_by_test_mode_is_drawn_by_its_mock_name():
    """FR-047. Never an unoccupied seat, and at its team's ordinal."""
    from services.image_lineup_service import build_fill_spec

    seat = NS(
        seat_number=1,
        discord_user_id="900001",
        server_display_name=None,
        discord_username=None,
        test_display_name="Mock Driver",
        nationality=None,
    )
    drawing = _draw([_team("Haas", [seat])])
    spec = build_fill_spec(drawing, _tpl(blocks=2, seats=1))

    assert spec.text["team_1_driver_1_name"] == "Mock Driver"
    assert "team_1_driver_1_name" not in spec.empty_quietly


def test_a_division_seated_wholly_by_mock_drivers_has_seated_drivers():
    """FR-048: it must not fall into the `recruited nobody` branch."""
    def mock(n):
        return NS(
            seat_number=n,
            discord_user_id=f"90000{n}",
            server_display_name=None,
            discord_username=None,
            test_display_name=f"Mock {n}",
            nationality=None,
        )

    drawing = _draw([_team("Haas", [mock(1), mock(2)])])

    assert drawing.teams[0].occupied_count == 2
    assert [s.occupied for s in drawing.teams[0].seats] == [True, True]
