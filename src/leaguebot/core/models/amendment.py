"""What the modules answer core when a round is amended: the plain data of its hooks, with no
Discord and no database.

`RoundArming` is a module's answer to the round armed again (`round_armed`): the steps it plans
and the timed work it asks the scheduler to arm, which core's arming carries out. The jobs are the
module's own (`ForecastJobs`, `CheckInJobs`), core holding no switch and no rule of theirs: a
module off, or a round it has no timed work for, answers none.
"""

from __future__ import annotations

from dataclasses import dataclass

from leaguebot.core.models.change import PlannedStep
from leaguebot.core.models.round import CheckInJobs, ForecastJobs


@dataclass(frozen=True)
class RoundArming:
    """A module's answer to the round armed again: the *steps* it plans for the amendment's queue,
    in order, and the *jobs* it asks the scheduler to arm, or None where it asks for none."""

    steps: tuple[PlannedStep, ...] = ()
    jobs: ForecastJobs | CheckInJobs | None = None
