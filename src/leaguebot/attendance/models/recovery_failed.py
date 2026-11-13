"""The error an attendance start-up recovery raises once it has gone through every round."""
from __future__ import annotations

__all__ = ["RecoveryFailed"]


class RecoveryFailed(Exception):
    """A start-up recovery of attendance could not recover some of its rounds.

    It is raised once, after the recovery has gone through every round, so a round that fails
    stops none of the others, and the start-up sweep reports the step as one that failed
    (architecture.md, "The sweep is core's own steps, then each enabled module's turn").
    `failures` maps the id of each round that failed to the error it failed with, in the order the
    recovery met them.
    """

    def __init__(self, failures: dict[int, BaseException]) -> None:
        self.failures = failures
        super().__init__(
            "could not recover " + "; ".join(
                f"round {round_id}: {error!r}" for round_id, error in failures.items()
            )
        )
