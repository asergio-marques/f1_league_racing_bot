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
