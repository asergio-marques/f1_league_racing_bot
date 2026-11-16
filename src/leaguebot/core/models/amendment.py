"""What the modules answer core when a round is amended: the plain data of its hooks, with no
Discord and no database.

`RoundArming` is a module's answer to the round armed again (`round_armed`): the steps it plans
and the timed work it asks the scheduler to arm, which core's arming carries out. The jobs are the
module's own (`ForecastJobs`, `CheckInJobs`), core holding no switch and no rule of theirs: a
module off, or a round it has no timed work for, answers none.

`AmendmentPart` is a module's answer to the amendment judged (`amendment_judged`): what it refuses
of the amendment, warns of, tells the confirmation, and keeps for the save.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from leaguebot.framework.models.change import PlannedStep
from leaguebot.core.models.round import CheckInJobs, ForecastJobs


@dataclass(frozen=True)
class RoundArming:
    """A module's answer to the round armed again: the *steps* it plans for the amendment's queue,
    in order, and the *jobs* it asks the scheduler to arm, or None where it asks for none."""

    steps: tuple[PlannedStep, ...] = ()
    jobs: ForecastJobs | CheckInJobs | None = None


@dataclass(frozen=True)
class AmendmentPart:
    """A module's part of an amendment's judgement.

    *refusals* each refuse the whole amendment; *warnings* and *notes* are what the confirmation
    tells the manager it costs, the notes first; *kept* is what the module keeps for the
    amendment's save (`round_amended`), by the name that save's subscriber reads it under. Two
    modules keeping one name is a fault.
    """

    refusals: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    kept: Mapping[str, Any] = field(default_factory=dict)
