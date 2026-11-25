"""What a preview is handed by the modules whose data it draws.

A preview draws a picture with the league's own values where a league has them, and invents the
rest. What another module owns is not image's to fetch: image declares here what it asks, and the
module implements it, handed to image by the builder as `bot.preview_sources`
(docs/design/architecture.md, "How modules and core fit together"). A method hands back a worked
answer, never a row or a service of the module's: image does no arithmetic over what a module
decides.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Collection, Mapping, Protocol, Sequence

from leaguebot.image.models.results_drawing import ResultsDrawing
from leaguebot.image.models.standings_drawing import RoundHeading, StandingsDrawing


class SampleSession(Protocol):
    """A session a round is run over, as the module that owns the sessions names it."""

    @property
    def value(self) -> str: ...

    @property
    def is_qualifying(self) -> bool: ...


class PreviewSources(Protocol):
    """What the previews ask of the modules that own what they draw."""

    async def check_in_deadline(self, scheduled_at: datetime) -> datetime:
        """The moment the check-in of a round run at *scheduled_at* locks, as the league has
        configured it: the stored deadline's hours before the round, else the module's own
        default, whether or not the module is on."""
        ...

    def attendance_sheet_order(
        self,
        totals: Mapping[int, int],
        seats: Mapping[int, tuple[int, str]],
        names: Mapping[int, str],
    ) -> list[int]:
        """The keys of *totals* in the order the attendance sheet lists its rows: most points
        first, drivers level on points by team, then by name, as the module's textual sheet after
        a round lists them. *seats* maps a driver to ``(team rank, team name)``, rank 0 a named
        team and 1 the reserve team; a driver absent from it holds no seat. *names* is by the
        same keys."""
        ...

    def sample_sessions(self, round_format: str) -> Sequence[SampleSession]:
        """The sessions a round of *round_format* is run over for results, in the order they
        are run."""
        ...

    def tyre_compounds(self) -> Sequence[str]:
        """The tyre compounds a qualifying result may record."""
        ...

    def qualifying_row(
        self,
        *,
        position: int,
        driver_user_id: int,
        team_instance_id: int,
        outcome: str,
        tyre: str | None,
        best_lap: str | None,
        points_awarded: int,
    ) -> Any:
        """One row of a sample qualifying classification, as the module records one."""
        ...

    def race_row(
        self,
        *,
        position: int,
        driver_user_id: int,
        team_instance_id: int,
        outcome: str,
        base_time_ms: int | None,
        laps_behind: int | None,
        ingame_time_penalties_ms: int,
        fastest_lap: str | None,
        fastest_lap_bonus: int,
        points_awarded: int,
    ) -> Any:
        """One row of a sample race classification, as the module records one."""
        ...

    def results_drawing(
        self,
        *,
        session: SampleSession,
        is_sprint: bool,
        result_status: str | None,
        division_name: str,
        round_number: str | int,
        race_name: str,
        driver_rows: Sequence[Any],
        points_map: Mapping[int, int],
        driver_names: Mapping[int, str],
        team_names: Mapping[int, str],
        team_keys: Mapping[int, str],
        nationalities: Mapping[int, str | None],
        division_tier: str | int | None,
        season_number: str | int | None,
        fastest_lap_colour: str | None,
        nationality_collected: bool,
    ) -> ResultsDrawing:
        """What a session's results graphic draws, composed as a posting composes it from the
        sample classification and the names handed in."""
        ...

    def standings_drawings(
        self,
        *,
        drivers: Sequence[Any],
        racing_teams: Sequence[Any],
        reserve_team_names: Collection[str],
        names: Mapping[int, str],
        flags: Mapping[int, str | None],
        team_key_of: Mapping[str, int],
        reserve_driver: Any,
        driver_points: Mapping[int, int],
        team_points: Mapping[int, int],
        round_session_results: Mapping[int, Mapping[str, Sequence[Any]]],
        rounds: Sequence[RoundHeading],
        previous_positions: Callable[..., dict[int, int]],
        shared: Mapping[str, Any],
    ) -> tuple[StandingsDrawing, StandingsDrawing]:
        """Both championships' graphics as they would stand over the sample totals and grid,
        ordered, gapped and moved by the module's own rules: the drivers', then the
        constructors'. *rounds* heads the grid and *shared* carries what both drawings
        share: the division's and the round's names, the status, the tier and season, the race
        and whether nationality is collected. *previous_positions* names a fictitious reference round for the
        movements, given the keys in standing order and the keys new to the classification."""
        ...

    def sanction_text(self, penalty_type: str | None, time_seconds: int | None) -> str:
        """A sanction as the announcement words it, from the record's own two fields."""
        ...
