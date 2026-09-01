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
        from services import image_preview_data as data

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
        from services import image_preview_data as data

        assert len(data.VERDICT_TEXT_SHORT) < len(data.VERDICT_TEXT_FULL)
        assert len(data.VERDICT_TEXT_FULL) < len(data.VERDICT_TEXT_OVER)

    def test_the_huge_text_exceeds_the_full_text_by_an_order_of_magnitude(self):
        """FR-032 — the floor, the cut and the notice are only reachable well past the box."""
        from services import image_preview_data as data

        assert len(data.VERDICT_TEXT_HUGE) > len(data.VERDICT_TEXT_FULL) * 10

    def test_the_huge_text_keeps_the_stewards_paragraph_breaks(self):
        """The graphic keeps them as the message does; a single run would not exercise that."""
        from services import image_preview_data as data

        assert "\n\n" in data.VERDICT_TEXT_HUGE

    def test_the_five_text_cases_are_distinct_and_ordered(self):
        from services import image_preview_data as data

        assert len(data.VERDICT_TEXT_CASES) == 5
        assert len(set(data.VERDICT_TEXT_CASES)) == 5
        assert data.VERDICT_TEXT_CASES[-1] == data.VERDICT_TEXT_NOT_PROVIDED

    def test_the_long_driver_name_is_long_enough_to_bound_a_field(self):
        """A name no league controls the length of. Thirty characters is already awkward."""
        from services import image_preview_data as data

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
        from services.image_preview_data import _scattered

        drivers = self._drivers(20)
        for ordinal in range(1, 13):
            order = _scattered(drivers, ordinal, 0)
            assert sorted(d.key for d in order) == [d.key for d in drivers]

    def test_two_rounds_classify_the_field_differently(self):
        from services.image_preview_data import _scattered

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
        from services.image_preview_data import _scattered

        drivers = self._drivers(20)
        places = [
            [d.key for d in _scattered(drivers, ordinal, 0)].index(1)
            for ordinal in range(1, 13)
        ]
        deltas = {b - a for a, b in zip(places, places[1:])}
        assert len(deltas) > 1

    def test_the_same_round_is_classified_the_same_way_twice(self):
        """Derived, never random — two renders of one round must be comparable."""
        from services.image_preview_data import _scattered

        drivers = self._drivers(20)
        first = [d.key for d in _scattered(drivers, 4, 1)]
        assert first == [d.key for d in _scattered(drivers, 4, 1)]

    def test_a_field_too_small_to_permute_is_handed_back_unchanged(self):
        from services.image_preview_data import _scattered

        for count in (0, 1, 2):
            drivers = self._drivers(count)
            assert _scattered(drivers, 3, 0) == drivers

    def test_the_fastest_lap_does_not_fall_in_the_same_place_every_round(self):
        """Pinned to one position it would only ever be seen over the same chip."""
        from services.image_preview_data import fabricate_standings_round_results

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
        from services.image_preview_data import fabricate_race_rows

        rows = fabricate_race_rows(self._drivers(20), {"Team": 900}, {})
        holder = [row for row in rows if row.fastest_lap_bonus]
        assert [row.finishing_position for row in holder] == [2]


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
        from services.image_attendance_service import mark_for

        return {mark_for(record.total, limit) for record in records}

    def _sheet(self, driver_count: int, round_count: int):
        from services.image_preview_data import (
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
        from services.image_attendance_service import MARK_NEAR, MARK_REACHED

        records, limit = self._sheet(20, 12)

        assert self._marks(records, limit) == {MARK_REACHED, MARK_NEAR, None}

    def test_the_marks_survive_a_field_of_four_and_a_single_round_run(self):
        """A preview is asked for at round one as often as at round twelve."""
        from services.image_attendance_service import MARK_NEAR, MARK_REACHED

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
        from services.image_preview_data import MAX_ROUND_PENALTY

        records, _limit = self._sheet(20, 12)
        values = {
            value or 0 for record in records for value in record.round_points.values()
        }

        assert values == set(range(MAX_ROUND_PENALTY + 1))

    def test_the_sanctioned_driver_has_reached_the_limit(self):
        """The annotation and the mark answer to the same number and must agree."""
        from services.image_attendance_service import MARK_REACHED, mark_for

        records, limit = self._sheet(20, 12)

        sanctioned = [record for record in records if record.sanctioned]
        assert sanctioned
        assert all(mark_for(r.total, limit) == MARK_REACHED for r in sanctioned)

    def test_one_driver_holds_nothing_at_all(self):
        records, _limit = self._sheet(20, 12)

        assert any(not record.round_points for record in records)

    def test_the_limit_falls_to_what_the_rounds_run_can_confer(self):
        """Ten points over one round run would leave every row unmarked."""
        from services.image_preview_data import (
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
