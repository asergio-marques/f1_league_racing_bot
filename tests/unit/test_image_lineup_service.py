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


# ── Keys ──────────────────────────────────────────────────────────────────


def test_the_key_is_the_normalised_team_name():
    drawing = _draw([_team("Force India (B)", [_seat(1, "a")])])
    assert drawing.teams[0].key == "force_india_b"


def test_two_teams_normalising_alike_are_fatal():
    with pytest.raises(LineupDataError) as exc:
        _draw([_team("Red Bull", [_seat(1, "a")]), _team("Red  Bull!", [_seat(1, "b")])])
    assert "red_bull" in str(exc.value)


def test_a_team_normalising_to_the_reserved_word_is_fatal():
    with pytest.raises(LineupDataError, match="reserved"):
        _draw([_team("Reserve!", [_seat(1, "a")])])


def test_a_team_normalising_to_nothing_is_fatal():
    with pytest.raises(LineupDataError, match="empty identifier"):
        _draw([_team("!!!", [_seat(1, "a")])])


# ── The reserve block ─────────────────────────────────────────────────────


def test_a_division_with_reserve_drivers_carries_the_block():
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [_seat(1, "r")], True)]
    )
    assert drawing.reserve is not None
    assert drawing.reserve.key == "reserve"


def test_a_division_with_no_reserve_driver_carries_no_block():
    """This is what removes `reserve_group` in its entirety (FR-004)."""
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [], True)]
    )
    assert drawing.reserve is None


def test_the_reserve_team_is_never_among_the_keyed_teams():
    drawing = _draw(
        [_team("Red Bull", [_seat(1, "a")]), _team("Reserve", [_seat(1, "r")], True)]
    )
    assert [t.key for t in drawing.teams] == ["red_bull"]


# ── Nationality ───────────────────────────────────────────────────────────


def test_a_recorded_nationality_becomes_the_flag_datum():
    seat = _draw([_team("Red Bull", [_seat(1, "a", nationality="Dutch")])]).teams[0].seats[0]
    assert seat.flag_datum == "Dutch"


def test_other_is_a_datum_like_any_other():
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


# ── The binding ───────────────────────────────────────────────────────────


def test_the_drawing_yields_a_binding_matching_its_teams():
    drawing = _draw(
        [
            _team("Red Bull", [_seat(1, "a"), _seat(2)]),
            _team("Haas", [_seat(1)]),
            _team("Reserve", [_seat(1, "r")], True),
        ]
    )
    binding = drawing.binding()
    assert binding.team_keys == ("red_bull", "haas")
    assert binding.seats == {"red_bull": 2, "haas": 1}
