"""The error a results start-up recovery raises once it has gone through every round."""
from __future__ import annotations

from collections.abc import Sequence

__all__ = ["RecoveryFailed"]


class RecoveryFailed(Exception):
    """A start-up recovery of results could not recover some of its rounds.

    It is raised once, after the recovery has gone through every round, so a round that fails
    stops none of the others, and the start-up sweep reports the step as one that failed
    (architecture.md, "The sweep is core's own steps, then each enabled module's turn").
    `failures` maps the id of each round that failed to the error it failed with, in the order the
    recovery met them. `rounds_called` names those rounds as core names them, in the same order
    ("round 3 in **Pro**"; core's `RoundsFailed`), for the sweep's line to the log channel, which
    names them, and for this message, which the host's log carries; it is empty where the rounds
    were not named, and the message then names each by its id.
    """

    def __init__(
        self, failures: dict[int, BaseException], rounds_called: Sequence[str] = ()
    ) -> None:
        self.failures = failures
        self.rounds_called = tuple(rounds_called)
        names = self.rounds_called or [f"round {round_id}" for round_id in failures]
        super().__init__(
            "could not recover " + "; ".join(
                f"{name}: {error!r}" for name, error in zip(names, failures.values())
            )
        )
