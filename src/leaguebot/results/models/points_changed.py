"""What approving a points amendment changed, value by value (#439).

Formed by `season_points_service.install_staged_points_on` from the season's points as they
stood and the staged table that replaced them, and read by the approval for the success line it
writes and the audit record it keeps: only the values that changed are named, a value staged
unchanged is not.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from leaguebot.results.models.points_config import SessionType


@dataclass(frozen=True)
class ValueChange:
    """One value of a points table: a position's points, a fastest-lap bonus or a limit."""

    config_name: str
    session_type: str
    what: str
    before: int | None
    after: int | None

    def line(self) -> str:
        session = SessionType(self.session_type).label()
        return (
            f"{self.config_name}, {session}, {self.what}: "
            f"{_shown(self.before)} → {_shown(self.after)}"
        )

    def record(self, value: int | None) -> dict[str, str | int | None]:
        return {
            "config": self.config_name, "session": self.session_type, "value": self.what,
            "points": value,
        }


def _shown(value: int | None) -> str:
    return "none" if value is None else str(value)


@dataclass(frozen=True)
class PointsChanged:
    """Every value an approval changed, in table order: by configuration and session, each
    position then the session's fastest-lap bonus and its position limit."""

    changes: tuple[ValueChange, ...] = ()

    def lines(self) -> Iterator[str]:
        """One line for each value changed, as "Standard, Feature Race, P1: 25 → 26"."""
        return (change.line() for change in self.changes)

    def before(self) -> list[dict[str, str | int | None]]:
        """Each value changed as it stood, for the audit record."""
        return [change.record(change.before) for change in self.changes]

    def after(self) -> list[dict[str, str | int | None]]:
        """Each value changed as it now stands, for the audit record."""
        return [change.record(change.after) for change in self.changes]

    def __bool__(self) -> bool:
        return bool(self.changes)
