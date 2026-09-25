"""Fabricated outcomes for the `/images test` previews (045).

The fabrications are the part of a preview a reader cannot check by eye — a classification
looks plausible whether or not every driver appears exactly once. These tests pin the
invariants the spec states, so that "believable" is a property the code holds rather than
an impression the picture gives.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# The prose constants rescued from the withdrawn sample-data module (T002)
# ---------------------------------------------------------------------------

class TestVerdictTextConstants:
    """FR-032 — the wrapping of a steward's prose is the verdict graphic's whole difficulty."""

    def test_every_constant_carries_text(self):
        from leaguebot.image.services import image_preview_data as data

        for name in (
            "LONG_DRIVER_NAME",
            "VERDICT_TEXT_SHORT",
            "VERDICT_TEXT_FULL",
            "VERDICT_TEXT_OVER",
            "VERDICT_TEXT_HUGE",
            "VERDICT_TEXT_NOT_PROVIDED",
        ):
            value = getattr(data, name)
            assert isinstance(value, str)
            assert value.strip(), f"{name} is empty"

    def test_the_lengths_ascend(self):
        """Short < full < over. Each case must actually be the case it stands for."""
        from leaguebot.image.services import image_preview_data as data

        assert len(data.VERDICT_TEXT_SHORT) < len(data.VERDICT_TEXT_FULL)
        assert len(data.VERDICT_TEXT_FULL) < len(data.VERDICT_TEXT_OVER)

    def test_the_huge_text_exceeds_the_full_text_by_an_order_of_magnitude(self):
        """FR-032 — the floor, the cut and the notice are only reachable well past the box."""
        from leaguebot.image.services import image_preview_data as data

        assert len(data.VERDICT_TEXT_HUGE) > len(data.VERDICT_TEXT_FULL) * 10

    def test_the_huge_text_keeps_the_stewards_paragraph_breaks(self):
        """The graphic keeps them as the message does; a single run would not exercise that."""
        from leaguebot.image.services import image_preview_data as data

        assert "\n\n" in data.VERDICT_TEXT_HUGE

    def test_the_five_text_cases_are_distinct_and_ordered(self):
        from leaguebot.image.services import image_preview_data as data

        assert len(data.VERDICT_TEXT_CASES) == 5
        assert len(set(data.VERDICT_TEXT_CASES)) == 5
        assert data.VERDICT_TEXT_CASES[-1] == data.VERDICT_TEXT_NOT_PROVIDED

    def test_the_long_driver_name_is_long_enough_to_bound_a_field(self):
        """A name no league controls the length of. Thirty characters is already awkward."""
        from leaguebot.image.services import image_preview_data as data

        assert len(data.LONG_DRIVER_NAME) > 30


# ---------------------------------------------------------------------------
# The standings grid's scatter (feature/standings_position_highlight)
# ---------------------------------------------------------------------------

class TestStandingsScatter:
    """Every round of a preview grid holds a different classification.

    The builders number a field in the order they are handed it, so without a scatter the
    driver at the top of the list would win every round and the grid would draw one flat
    column — which tells a manager judging a template nothing about how it handles the
    varied grid a real season produces.
    """

    @staticmethod
    def _drivers(count: int):
        from types import SimpleNamespace

        return [
            SimpleNamespace(key=n, team_name="Team", seat_number=1)
            for n in range(1, count + 1)
        ]

    def test_every_driver_appears_exactly_once_in_every_round(self):
        from leaguebot.image.services.image_preview_data import _scattered

        drivers = self._drivers(20)
        for ordinal in range(1, 13):
            order = _scattered(drivers, ordinal, 0)
            assert sorted(d.key for d in order) == [d.key for d in drivers]

    def test_two_rounds_classify_the_field_differently(self):
        from leaguebot.image.services.image_preview_data import _scattered

        drivers = self._drivers(20)
        orders = {
            tuple(d.key for d in _scattered(drivers, ordinal, 0))
            for ordinal in range(1, 13)
        }
        assert len(orders) == 12

    def test_no_driver_marches_by_a_constant_from_round_to_round(self):
        """A fixed multiplier would shift the whole field alike — a rotation, which stripes.

        The grid is read as a picture, and a constant stride draws diagonal bands rather
        than a scatter.
        """
        from leaguebot.image.services.image_preview_data import _scattered

        drivers = self._drivers(20)
        places = [
            [d.key for d in _scattered(drivers, ordinal, 0)].index(1)
            for ordinal in range(1, 13)
        ]
        deltas = {b - a for a, b in zip(places, places[1:])}
        assert len(deltas) > 1

    def test_the_same_round_is_classified_the_same_way_twice(self):
        """Derived, never random — two renders of one round must be comparable."""
        from leaguebot.image.services.image_preview_data import _scattered

        drivers = self._drivers(20)
        first = [d.key for d in _scattered(drivers, 4, 1)]
        assert first == [d.key for d in _scattered(drivers, 4, 1)]

    def test_a_field_too_small_to_permute_is_handed_back_unchanged(self):
        from leaguebot.image.services.image_preview_data import _scattered

        for count in (0, 1, 2):
            drivers = self._drivers(count)
            assert _scattered(drivers, 3, 0) == drivers

    def test_the_fastest_lap_does_not_fall_in_the_same_place_every_round(self):
        """Pinned to one position it would only ever be seen over the same chip."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(20)
        results = fabricate_standings_round_results(
            list(range(1, 13)),
            {ordinal: "NORMAL" for ordinal in range(1, 13)},
            drivers,
            {"Team": 900},
        )
        places = {
            next(
                row.finishing_position
                for row in results[ordinal]["FEATURE_RACE"]
                if row.fastest_lap_bonus
            )
            for ordinal in range(1, 13)
        }
        assert len(places) > 1
        assert 1 in places, "a fastest lap must sometimes fall to the winner"

    def test_a_single_classification_keeps_the_second_place_it_always_had(self):
        """The results preview draws one race and needs no variation; it is untouched."""
        from leaguebot.image.services.image_preview_data import fabricate_race_rows

        rows = fabricate_race_rows(self._drivers(20), {"Team": 900}, {})
        holder = [row for row in rows if row.fastest_lap_bonus]
        assert [row.finishing_position for row in holder] == [2]

    def test_a_qualifying_field_large_enough_carries_a_disqualification(self):
        """#144 — DNF and DNS were fabricated already; DSQ was the one literal never drawn."""
        from leaguebot.core.models.session_result import OutcomeModifier
        from leaguebot.image.services.image_preview_data import fabricate_qualifying_rows

        rows = fabricate_qualifying_rows(self._drivers(20), {"Team": 900}, {})
        assert any(row.outcome is OutcomeModifier.DSQ for row in rows)
        # It sits beside the DNS, not on top of it.
        dsq = [row for row in rows if row.outcome is OutcomeModifier.DSQ]
        dns = [row for row in rows if row.outcome is OutcomeModifier.DNS]
        assert {row.finishing_position for row in dsq}.isdisjoint(
            {row.finishing_position for row in dns}
        )

    def test_a_race_field_large_enough_carries_a_disqualification(self):
        from leaguebot.core.models.session_result import OutcomeModifier
        from leaguebot.image.services.image_preview_data import fabricate_race_rows

        rows = fabricate_race_rows(self._drivers(20), {"Team": 900}, {})
        dsq = [row for row in rows if row.outcome is OutcomeModifier.DSQ]
        dnf = [row for row in rows if row.outcome is OutcomeModifier.DNF]
        assert dsq
        assert {row.finishing_position for row in dsq}.isdisjoint(
            {row.finishing_position for row in dnf}
        )

    def test_the_outcomes_stand_in_the_order_a_submission_must_keep(self):
        """A preview must not draw a classification the results module would refuse.

        ``validate_submission_block`` refuses a race whose rows do not run lead-lap, then
        lapped, then DNF, then DNS, then DSQ, and a qualifying whose rows do not run
        classified, then DNF, then DNS, then DSQ. The rule sits inside that function among
        checks needing a division, so it is stated here rather than called. A DSQ placed
        ahead of the lapped car and the DNF first shipped exactly that refusal (#144).
        """
        from leaguebot.core.models.session_result import OutcomeModifier
        from leaguebot.image.services.image_preview_data import fabricate_qualifying_rows, fabricate_race_rows

        def race_category(row) -> int:
            if row.outcome is OutcomeModifier.DSQ:
                return 4
            if row.outcome is OutcomeModifier.DNS:
                return 3
            if row.outcome is OutcomeModifier.DNF:
                return 2
            return 1 if row.laps_behind else 0

        qualifying_category = {
            OutcomeModifier.CLASSIFIED: 0,
            OutcomeModifier.DNF: 1,
            OutcomeModifier.DNS: 2,
            OutcomeModifier.DSQ: 3,
        }

        for count in range(1, 25):
            race = fabricate_race_rows(self._drivers(count), {"Team": 900}, {})
            race_order = [race_category(row) for row in race]
            assert race_order == sorted(race_order), f"race of {count}: {race_order}"

            qualifying = fabricate_qualifying_rows(self._drivers(count), {"Team": 900}, {})
            qualifying_order = [qualifying_category[row.outcome] for row in qualifying]
            assert qualifying_order == sorted(qualifying_order), (
                f"qualifying of {count}: {qualifying_order}"
            )

    def test_a_small_field_is_not_forced_to_carry_a_disqualification(self):
        """The spec's own qualifier: none of the cases are fabricated into existence."""
        from leaguebot.core.models.session_result import OutcomeModifier
        from leaguebot.image.services.image_preview_data import fabricate_qualifying_rows, fabricate_race_rows

        for count in (2, 3, 4):
            qualifying = fabricate_qualifying_rows(self._drivers(count), {"Team": 900}, {})
            race = fabricate_race_rows(self._drivers(count), {"Team": 900}, {})
            assert all(row.outcome is not OutcomeModifier.DSQ for row in qualifying)
            assert all(row.outcome is not OutcomeModifier.DSQ for row in race)


# ---------------------------------------------------------------------------
# The standings preview's fabricated totals (#144)
# ---------------------------------------------------------------------------

class TestStandingsTotals:
    """A fixed ramp clamped at zero showed a tie or a nought only by accident of the field
    size — never on a normal-sized division. These totals place both deliberately instead.
    """

    def test_second_and_third_are_level_on_points(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        for count in (3, 4, 10, 20):
            totals = fabricate_standings_totals(count, leader=120)
            assert totals[1] == totals[2]

    def test_the_leader_stands_clear_of_the_tie(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        for count in (3, 4, 10, 20):
            totals = fabricate_standings_totals(count, leader=120)
            assert totals[0] > totals[1]

    def test_no_two_entries_are_level_but_the_placed_pair(self):
        """The #144 regression: a fixed clamp put a whole block of the field level on zero."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        for count in range(2, 31):
            totals = fabricate_standings_totals(count, leader=120)
            level_pairs = {
                (i, j)
                for i in range(count)
                for j in range(i + 1, count)
                if totals[i] == totals[j]
            }
            assert level_pairs <= {(1, 2)}

    def test_the_last_entry_holds_no_points(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        for count in (2, 4, 5, 10, 20):
            totals = fabricate_standings_totals(count, leader=120)
            assert totals[-1] == 0

    def test_a_field_of_three_keeps_the_tie_off_nought(self):
        """A tie *on* nought is the accidental case #144 reported, not the deliberate one."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        totals = fabricate_standings_totals(3, leader=120)
        assert totals[1] == totals[2] != 0

    def test_the_totals_never_rise_down_the_table(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        for count in range(1, 31):
            totals = fabricate_standings_totals(count, leader=120)
            assert totals == sorted(totals, reverse=True)

    def test_a_field_of_one_and_an_empty_field(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_totals

        assert fabricate_standings_totals(1, leader=120) == [120]
        assert fabricate_standings_totals(0, leader=120) == []


# ---------------------------------------------------------------------------
# The standings preview's fabricated previous positions (#144)
# ---------------------------------------------------------------------------

class TestStandingsPreviousPositions:
    """A preview stands against no real reference round, so movement was once omitted
    outright. A fabricated previous round is no different in kind from the current round
    the preview already invents, and is what lets the three markers be drawn at all.
    """

    def test_one_entry_gained_one_lost_one_held_position(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_previous_positions
        from leaguebot.results.services.standings_service import (
            MOVEMENT_GAINED,
            MOVEMENT_LOST,
            MOVEMENT_UNCHANGED,
            derive_movement,
        )

        keys = [10, 20, 30, 40]
        previous = fabricate_standings_previous_positions(keys)
        current = [(key, position, 0) for position, key in enumerate(keys, start=1)]

        movements = derive_movement(current, previous)

        directions = {m.direction for m in movements.values() if m is not None}
        assert directions == {MOVEMENT_GAINED, MOVEMENT_LOST, MOVEMENT_UNCHANGED}

    def test_no_entry_is_left_without_a_movement(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_previous_positions
        from leaguebot.results.services.standings_service import derive_movement

        keys = [10, 20, 30, 40]
        previous = fabricate_standings_previous_positions(keys)
        current = [(key, position, 0) for position, key in enumerate(keys, start=1)]

        movements = derive_movement(current, previous)

        assert all(movement is not None for movement in movements.values())

    def test_a_field_too_small_to_swap_holds_every_entry_unchanged(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_previous_positions
        from leaguebot.results.services.standings_service import MOVEMENT_UNCHANGED, derive_movement

        for count in (0, 1, 2):
            keys = list(range(10, 10 + count))
            previous = fabricate_standings_previous_positions(keys)
            current = [(key, position, 0) for position, key in enumerate(keys, start=1)]

            movements = derive_movement(current, previous)

            assert all(
                movement is None or movement.direction == MOVEMENT_UNCHANGED
                for movement in movements.values()
            )

    def test_a_newcomer_has_no_previous_position_to_have_moved_from(self):
        """The spec's "a driver whom the standings of the preceding round do not hold"."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_previous_positions
        from leaguebot.results.services.standings_service import derive_movement

        keys = [10, 20, 30, 40, 50]
        previous = fabricate_standings_previous_positions(keys, newcomers=frozenset({50}))
        current = [(key, position, 0) for position, key in enumerate(keys, start=1)]

        movements = derive_movement(current, previous)

        assert movements[50] is None
        assert all(movements[key] is not None for key in (10, 20, 30, 40))

    def test_the_same_field_produces_the_same_previous_positions_twice(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_previous_positions

        keys = [10, 20, 30, 40, 50]
        assert fabricate_standings_previous_positions(
            keys
        ) == fabricate_standings_previous_positions(keys)


# ---------------------------------------------------------------------------
# The standings preview's fabricated absence and stand-in (#144)
# ---------------------------------------------------------------------------

class TestStandingsSubstitution:
    """A driver absent from one round, a reserve standing in, and a car nobody drove — the
    spec's three cases, drawn over two *different* regular teams so a stand-in filling the
    very seat it vacates does not paper over the empty-car case with it.
    """

    #: Three regular teams and a reserve: the fixture every test in this class shares.
    TEAM_KEYS = {"Redline": 1, "Bluewave": 2, "Greenfield": 3, "Reserve": 4}

    @staticmethod
    def _drivers(team_layout):
        """*team_layout* is ``[(team_name, seat_count), ...]``, drivers numbered from 1."""
        from types import SimpleNamespace

        drivers = []
        key = 1
        for team_name, seat_count in team_layout:
            for seat_number in range(1, seat_count + 1):
                drivers.append(
                    SimpleNamespace(
                        key=key,
                        display_name=f"Driver {key}",
                        team_name=team_name,
                        team_key=team_name,
                        seat_number=seat_number,
                        nationality=None,
                    )
                )
                key += 1
        return drivers

    def test_the_absent_regular_is_not_scattered_into_the_substitution_round(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 1), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        regulars = drivers[:-1]
        absent = regulars[-2]  # Bluewave 2

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, self.TEAM_KEYS, reserve_driver=reserve,
        )

        keys_in_round = {
            row.driver_user_id for rows in results[1].values() for row in rows
        }
        assert absent.key not in keys_in_round

    def test_a_different_teams_car_is_left_with_nobody_in_it(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 1), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        regulars = drivers[:-1]
        undriven = regulars[-1]  # Greenfield 1

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, self.TEAM_KEYS, reserve_driver=reserve,
        )

        keys_in_round = {
            row.driver_user_id for rows in results[1].values() for row in rows
        }
        assert undriven.key not in keys_in_round

    def test_the_reserve_is_credited_to_the_absent_drivers_team_not_the_undriven_ones(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 1), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        absent = drivers[:-1][-2]  # Bluewave 2

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, self.TEAM_KEYS, reserve_driver=reserve,
        )

        substitute_team_ids = {
            row.team_instance_id
            for rows in results[1].values()
            for row in rows
            if row.driver_user_id == reserve.key
        }
        absent_team_id = self.TEAM_KEYS[absent.team_key or absent.team_name]
        assert substitute_team_ids == {absent_team_id}

    def test_a_round_before_or_after_the_substitution_is_untouched(self):
        """Confined to one round — both dropped drivers are back in every other one."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 1), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        regulars = drivers[:-1]
        absent, undriven = regulars[-2], regulars[-1]

        results = fabricate_standings_round_results(
            [1, 2], {1: "NORMAL", 2: "NORMAL"}, drivers,
            self.TEAM_KEYS, reserve_driver=reserve,
        )

        rows_in_round_2 = [row for rows in results[2].values() for row in rows]
        keys_in_round_2 = {row.driver_user_id for row in rows_in_round_2}
        assert absent.key in keys_in_round_2
        assert undriven.key in keys_in_round_2

        # The reserve still races the second round too, under their own reserve team —
        # only the substitution round credits them to somebody else's.
        reserve_team_id = self.TEAM_KEYS[reserve.team_key or reserve.team_name]
        reserve_rows = [row for row in rows_in_round_2 if row.driver_user_id == reserve.key]
        assert {row.team_instance_id for row in reserve_rows} == {reserve_team_id}

    def test_no_reserve_leaves_every_driver_in_place(self):
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers([("Redline", 2), ("Bluewave", 2)])

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, {"Redline": 1, "Bluewave": 2}
        )

        keys_in_round = {
            row.driver_user_id for rows in results[1].values() for row in rows
        }
        assert keys_in_round == {d.key for d in drivers}

    def test_a_field_too_small_carries_no_substitution(self):
        """The spec's own qualifier: nothing is fabricated into existence to reach a case."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers([("Redline", 1), ("Reserve", 1)])
        reserve = drivers[-1]

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, {"Redline": 1, "Reserve": 2},
            reserve_driver=reserve,
        )

        keys_in_round = {
            row.driver_user_id for rows in results[1].values() for row in rows
        }
        assert keys_in_round == {d.key for d in drivers}

    def test_a_field_of_two_seat_teams_carries_the_substitution(self):
        """The commonest division there is. Its two last regulars are teammates, and taking
        the pair of them skipped the substitution on it altogether.
        """
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 2), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        regulars = drivers[:-1]
        undriven = regulars[-1]  # Greenfield 2
        absent = regulars[-3]  # Bluewave 2, the last regular of another team

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, self.TEAM_KEYS, reserve_driver=reserve,
        )

        rows = [row for session in results[1].values() for row in session]
        assert undriven.key not in {row.driver_user_id for row in rows}
        assert absent.key not in {row.driver_user_id for row in rows}
        assert {
            row.team_instance_id for row in rows if row.driver_user_id == reserve.key
        } == {self.TEAM_KEYS["Bluewave"]}

    def test_the_team_short_of_a_car_scores_nothing_in_that_round(self):
        """The spec's "a team conferred no points in one of the rounds run", placed.

        Judged by the drawing's own rule, `highlight_for`, since what a manager sees of a
        round's points on the constructors grid is the highlight and nothing else. Left to
        the scatter it never arose on a field of five teams or fewer, where every finisher
        is in the points.
        """
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results
        from leaguebot.image.services.image_standings_service import highlight_for

        for team_count in range(3, 12):
            layout = [(f"Team {n}", 2) for n in range(team_count)] + [("Reserve", 1)]
            drivers = self._drivers(layout)
            team_keys = {name: n + 1 for n, (name, _) in enumerate(layout)}
            reserve = drivers[-1]
            short_team_id = team_keys[drivers[:-1][-1].team_name]

            for round_format in ("NORMAL", "SPRINT"):
                results = fabricate_standings_round_results(
                    [1], {1: round_format}, drivers, team_keys, reserve_driver=reserve,
                )
                rows = [
                    row
                    for session in results[1].values()
                    for row in session
                    if row.team_instance_id == short_team_id
                ]
                assert rows, f"{team_count} teams: the short team drove nothing at all"
                assert all(highlight_for(row) == (None, False) for row in rows), (
                    f"{team_count} teams, {round_format}: the short team was drawn scoring"
                )

    def test_the_team_short_of_a_car_scores_as_usual_in_every_other_round(self):
        """Confined to the one round, like the rest of the substitution."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers(
            [("Redline", 2), ("Bluewave", 2), ("Greenfield", 2), ("Reserve", 1)]
        )
        reserve = drivers[-1]
        plain = fabricate_standings_round_results(
            [2], {2: "NORMAL"}, drivers, self.TEAM_KEYS
        )
        substituted = fabricate_standings_round_results(
            [1, 2], {1: "NORMAL", 2: "NORMAL"}, drivers, self.TEAM_KEYS,
            reserve_driver=reserve,
        )

        def order(results):
            return {
                session: [row.driver_user_id for row in rows]
                for session, rows in results[2].items()
            }

        assert order(substituted) == order(plain)

    def test_only_one_regular_team_carries_no_substitution(self):
        """Both roles would fall on the same team, which the "different team" guard refuses."""
        from leaguebot.image.services.image_preview_data import fabricate_standings_round_results

        drivers = self._drivers([("Redline", 2), ("Reserve", 1)])
        reserve = drivers[-1]

        results = fabricate_standings_round_results(
            [1], {1: "NORMAL"}, drivers, {"Redline": 1, "Reserve": 2},
            reserve_driver=reserve,
        )

        keys_in_round = {
            row.driver_user_id for rows in results[1].values() for row in rows
        }
        assert keys_in_round == {d.key for d in drivers}


# ---------------------------------------------------------------------------
# The attendance sheet's totals and the marks they earn
# ---------------------------------------------------------------------------


class TestAttendanceFabrication:
    """A preview sheet must exercise both marks and the absence of one.

    Totals cut from one narrow band draw a column of identical numbers under a single mark —
    a picture that tells a manager judging the template nothing about the two marks, the
    unmarked row, or how the sheet orders a field that actually differs.
    """

    @staticmethod
    def _drivers(count: int):
        from types import SimpleNamespace

        return [
            SimpleNamespace(key=n, team_name="Team", seat_number=1)
            for n in range(1, count + 1)
        ]

    @staticmethod
    def _marks(records, limit):
        from leaguebot.image.services.image_attendance_service import mark_for

        return {mark_for(record.total, limit) for record in records}

    def _sheet(self, driver_count: int, round_count: int):
        from leaguebot.image.services.image_preview_data import (
            fabricate_attendance_limit,
            fabricate_attendance_records,
        )

        ordinals = list(range(1, round_count + 1))
        limit = fabricate_attendance_limit(ordinals)
        records = fabricate_attendance_records(
            self._drivers(driver_count), ordinals, limit
        )
        return records, limit

    def test_both_marks_and_an_unmarked_row_appear_together(self):
        from leaguebot.image.services.image_attendance_service import MARK_NEAR, MARK_REACHED

        records, limit = self._sheet(20, 12)

        assert self._marks(records, limit) == {MARK_REACHED, MARK_NEAR, None}

    def test_the_marks_survive_a_field_of_four_and_a_single_round_run(self):
        """A preview is asked for at round one as often as at round twelve."""
        from leaguebot.image.services.image_attendance_service import MARK_NEAR, MARK_REACHED

        for driver_count, round_count in ((4, 1), (4, 2), (20, 1), (20, 2), (20, 24)):
            records, limit = self._sheet(driver_count, round_count)
            assert self._marks(records, limit) == {MARK_REACHED, MARK_NEAR, None}, (
                f"{driver_count} drivers over {round_count} rounds"
            )

    def test_the_totals_are_not_all_alike(self):
        records, _limit = self._sheet(20, 12)

        assert len({record.total for record in records}) >= 10

    def test_a_total_never_disagrees_with_the_cells_beneath_it(self):
        """The mark is read off the total and the row off the cells: they are one number."""
        records, _limit = self._sheet(20, 12)

        for record in records:
            assert record.total == sum(
                value or 0 for value in record.round_points.values()
            )

    def test_no_round_confers_more_than_a_round_can(self):
        from leaguebot.image.services.image_preview_data import MAX_ROUND_PENALTY

        records, _limit = self._sheet(20, 12)
        values = {
            value or 0 for record in records for value in record.round_points.values()
        }

        assert values == set(range(MAX_ROUND_PENALTY + 1))

    def test_the_sanctioned_driver_has_reached_the_limit(self):
        """The annotation and the mark answer to the same number and must agree."""
        from leaguebot.image.services.image_attendance_service import MARK_REACHED, mark_for

        records, limit = self._sheet(20, 12)

        sanctioned = [record for record in records if record.sanctioned]
        assert sanctioned
        assert all(mark_for(r.total, limit) == MARK_REACHED for r in sanctioned)

    def test_one_driver_holds_nothing_at_all(self):
        records, _limit = self._sheet(20, 12)

        assert any(not record.round_points for record in records)

    def test_the_limit_falls_to_what_the_rounds_run_can_confer(self):
        """Ten points over one round run would leave every row unmarked."""
        from leaguebot.image.services.image_preview_data import (
            MAX_ROUND_PENALTY,
            NOMINAL_ATTENDANCE_LIMIT,
            fabricate_attendance_limit,
        )

        assert fabricate_attendance_limit([1]) == MAX_ROUND_PENALTY
        assert fabricate_attendance_limit([1, 2]) == 2 * MAX_ROUND_PENALTY
        assert fabricate_attendance_limit(range(1, 13)) == NOMINAL_ATTENDANCE_LIMIT

    def test_the_records_are_the_same_on_every_invocation(self):
        """A manager comparing two drawings needs the same numbers in both."""
        first, _limit = self._sheet(20, 12)
        second, _limit = self._sheet(20, 12)

        assert [(r.key, r.total, dict(r.round_points)) for r in first] == [
            (r.key, r.total, dict(r.round_points)) for r in second
        ]


class TestFabricatedTyreCompounds:
    """FR-031's reasoning, applied to the tyres: every icon judged in one picture.

    A preview exists so a manager can see how their template handles what the bot will
    actually draw. The compounds became a closed set the module ships at Constitution
    v7.8.0, so all five are now the bot's own artwork — and a compound the fabrication
    never deals is a compound a manager never sees before a real round draws it.
    """

    @staticmethod
    def _drivers(count: int):
        from types import SimpleNamespace

        return [
            SimpleNamespace(key=n, team_name="Team", seat_number=1)
            for n in range(1, count + 1)
        ]

    def _tyres(self, count: int):
        from leaguebot.image.services.image_preview_data import fabricate_qualifying_rows

        rows = fabricate_qualifying_rows(self._drivers(count), {"Team": 900}, {})
        return [row.tyre for row in rows]

    def test_all_five_compounds_are_drawn_on_a_field_of_six(self):
        """Six, because one row records none — the smallest field that can show them all.

        Dealt in turn rather than keyed on the position, which would skip whichever
        compounds no position happened to land on.
        """
        from leaguebot.image.utils.tyre_compound import TYRE_COMPOUNDS

        assert set(self._tyres(6)) == {None, *TYRE_COMPOUNDS}

    def test_one_row_records_no_compound_at_all(self):
        """The absent-datum case is drawn beside the five, being a state of its own.

        A qualifying submission does not oblige a tyre, and the field is declared
        `fallback_when_absent`, so a template is judged on how it draws that too.
        """
        assert self._tyres(6).count(None) == 1

    def test_every_compound_dealt_is_one_the_vocabulary_admits(self):
        """A fabricated value outside the set would draw the placeholder in a preview and
        so misreport the template a manager is judging."""
        from leaguebot.image.utils.tyre_compound import TYRE_COMPOUNDS

        assert {t for t in self._tyres(20) if t is not None} <= set(TYRE_COMPOUNDS)

    def test_a_field_too_small_to_show_them_all_still_deals_without_repeating(self):
        dealt = [t for t in self._tyres(4) if t is not None]
        assert len(dealt) == len(set(dealt))
