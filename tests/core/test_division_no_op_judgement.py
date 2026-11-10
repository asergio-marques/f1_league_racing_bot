"""Whether a division rename or amendment changes nothing — `season_service` (#482).

The core specification's "The record of what changed": a command that changes nothing because
nothing was asked of it records that nothing was changed. `/division rename` to the name that
stands, and `/division amend` with the values that stand, write no audit entry, reply that nothing
changed and record it. Whether they do is judged by a pure function beside the other division
rules in `season_service`, from the division as the change's save reads it.

Pure tests: no database, no Discord.
"""
from __future__ import annotations

import dataclasses

import pytest



def _division(**overrides):
    from leaguebot.core.models.division import Division

    fields = {
        "id": 7,
        "season_id": 1,
        "name": "Pro",
        "mention_role_id": 900,
        "tier": 1,
    }
    # The forecast channel is weather's own column, which core's Division stops carrying (#283);
    # until it does, the field is required, so it is handed only where Division declares it.
    if "forecast_channel_id" in {f.name for f in dataclasses.fields(Division)}:
        fields["forecast_channel_id"] = None
    fields.update(overrides)
    return Division(**fields)


@pytest.mark.parametrize(
    "asked",
    [
        pytest.param({"new_name": "Pro"}, id="renamed_to_its_own_name"),
        pytest.param({"tier": 1}, id="its_own_tier"),
        pytest.param({"role_id": 900}, id="its_own_role"),
        pytest.param({"new_name": "Pro", "tier": 1, "role_id": 900}, id="every_value_as_it_stands"),
    ],
)
def test_a_division_amendment_to_the_values_that_stand_changes_nothing(asked):
    """`/division rename` to its own name is `new_name` alone; `/division amend` any of the three."""
    from leaguebot.core.services.season_service import division_amendment_changes_nothing

    assert division_amendment_changes_nothing(_division(), **asked)


@pytest.mark.parametrize(
    "asked",
    [
        pytest.param({"new_name": "Elite"}, id="another_name"),
        pytest.param({"new_name": "PRO"}, id="its_name_in_another_case"),
        pytest.param({"tier": 2}, id="another_tier"),
        pytest.param({"role_id": 901}, id="another_role"),
        pytest.param({"new_name": "Pro", "tier": 2}, id="one_value_as_it_stands_and_one_changed"),
    ],
)
def test_a_division_amendment_with_any_value_changed_changes_something(asked):
    """A change of case is a change: the name is shown as it is written."""
    from leaguebot.core.services.season_service import division_amendment_changes_nothing

    assert not division_amendment_changes_nothing(_division(), **asked)
