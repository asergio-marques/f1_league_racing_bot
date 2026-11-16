"""What core asks of the error a module's start-up step raises for some of its rounds."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

__all__ = ["RoundsFailed"]


@runtime_checkable
class RoundsFailed(Protocol):
    """An error that says which rounds a start-up step failed for.

    The start-up sweep names them in its line to the log channel (`start_up_failure_line`), asking
    for this attribute and never for a module's own error type. Each entry is a round named as core
    names one, "round 3 in **Pro**", or "a round no longer there" where its rows are gone
    (`season_service.round_called`). An error that carries none is reported as a step that failed
    as a whole.
    """

    rounds_called: Sequence[str]
