"""Results' share of what the image module's previews draw
(the protocol is `image/services/preview/preview_sources.py`).

Built once by the entry point, which hands it to the image module beside attendance's. A preview
draws the league's own division and invents the outcome a league cannot configure in advance;
the rows of a classification, the sessions a round is run over, the tyre compounds, the order
a standings table stands in and its gaps and movements are the results module's rules, so the
preview asks for them here and holds none of its own. It reads nothing and writes nothing.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Collection, Mapping, Sequence

from leaguebot.core.models.round import RoundFormat
from leaguebot.image.models.results_drawing import ResultsDrawing
from leaguebot.image.models.standings_drawing import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
    RoundHeading,
    StandingsDrawing,
)
from leaguebot.image.utils.tyre_compound import TYRE_COMPOUNDS
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.models.session_result import (
    OutcomeModifier,
    QualifyingSessionResult,
    RaceSessionResult,
)
from leaguebot.results.services.publishing import results_drawing_composer, standings_drawing_composer
from leaguebot.results.services.review.verdict_announcement_service import describe_penalty
from leaguebot.results.services.standings import standings_service
from leaguebot.results.services.submission.result_submission_service import get_sessions_for_format


class ResultsPreviewSources:
    """The sessions, rows, compounds and standings the image module's previews draw."""

    def sample_sessions(self, round_format: str) -> Sequence:
        try:
            fmt = RoundFormat(round_format)
        except ValueError:
            fmt = RoundFormat.NORMAL
        return get_sessions_for_format(fmt)

    def tyre_compounds(self) -> Sequence[str]:
        return TYRE_COMPOUNDS

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
    ) -> QualifyingSessionResult:
        return QualifyingSessionResult(
            id=position,
            session_result_id=1,
            driver_user_id=driver_user_id,
            team_instance_id=team_instance_id,
            finishing_position=position,
            outcome=OutcomeModifier(outcome),
            tyre=tyre,
            best_lap=best_lap,
            points_awarded=points_awarded,
        )

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
    ) -> RaceSessionResult:
        return RaceSessionResult(
            id=position,
            session_result_id=1,
            driver_user_id=driver_user_id,
            team_instance_id=team_instance_id,
            finishing_position=position,
            outcome=OutcomeModifier(outcome),
            base_time_ms=base_time_ms,
            laps_behind=laps_behind,
            ingame_time_penalties_ms=ingame_time_penalties_ms,
            postrace_time_penalties_ms=0,
            appeal_time_penalties_ms=0,
            fastest_lap=fastest_lap,
            fastest_lap_bonus=fastest_lap_bonus,
            points_awarded=points_awarded,
        )

    def results_drawing(
        self,
        *,
        session: Any,
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
        return results_drawing_composer.resolve_drawing(
            session_type=SessionType(session.value),
            is_sprint=is_sprint,
            result_status=result_status,
            division_name=division_name,
            round_number=round_number,
            race_name=race_name,
            driver_rows=driver_rows,
            points_map=points_map,
            driver_names=driver_names,
            team_names=team_names,
            team_keys=team_keys,
            nationalities=nationalities,
            division_tier=division_tier,
            season_number=season_number,
            fastest_lap_colour=fastest_lap_colour,
            nationality_collected=nationality_collected,
        )

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
        # The countback's raw material, read off the grid drawn, so the pair the totals leave
        # level is separated by what a reader can see beneath them.
        number_of = {heading.ordinal: int(heading.number) for heading in rounds}
        session_rows = [
            (row, session, number_of[ordinal])
            for ordinal, sessions in round_session_results.items()
            for session, rows in sessions.items()
            for row in rows
        ]
        driver_counts, driver_firsts = standings_service.tally_feature_finishes(
            (row.driver_user_id, session, row.outcome, row.finishing_position, number)
            for row, session, number in session_rows
        )
        team_counts, team_firsts = standings_service.tally_feature_finishes(
            (row.team_instance_id, session, row.outcome, row.finishing_position, number)
            for row, session, number in session_rows
        )
        participants = {row.driver_user_id for row, _, _ in session_rows}

        ordered_drivers = standings_service.order_drivers(
            dict(driver_points),
            points=dict(driver_points),
            finish_counts=driver_counts,
            first_finish_rounds=driver_firsts,
            participants=participants,
            seats={
                d.key: (1 if (d.team_key or d.team_name) in reserve_team_names else 0, d.team_name)
                for d in drivers
            },
            names=dict(names),
        )
        driver_snapshots = [
            SimpleNamespace(
                driver_user_id=key,
                standing_position=position,
                total_points=driver_points[key],
                finish_counts=dict(driver_counts.get(key, {})),
                first_finish_rounds=dict(driver_firsts.get(key, {})),
                race_participant=key in participants,
            )
            for position, key in enumerate(ordered_drivers, start=1)
        ]

        racing_teams = [team for team in racing_teams if team.name in team_key_of]
        ordered_teams = standings_service.order_teams(
            dict(team_points),
            points=dict(team_points),
            finish_counts=team_counts,
            first_finish_rounds=team_firsts,
            team_meta={
                team_key_of[team.name]: (0, getattr(team, "full_name", "") or team.name)
                for team in racing_teams
            },
        )
        team_snapshots = [
            SimpleNamespace(
                team_instance_id=key,
                standing_position=position,
                total_points=team_points[key],
                finish_counts=dict(team_counts.get(key, {})),
                first_finish_rounds=dict(team_firsts.get(key, {})),
            )
            for position, key in enumerate(ordered_teams, start=1)
        ]

        driver_standing = [
            (x.driver_user_id, x.standing_position, x.total_points) for x in driver_snapshots
        ]
        team_standing = [
            (x.team_instance_id, x.standing_position, x.total_points) for x in team_snapshots
        ]
        driver_gaps = standings_service.derive_gaps(driver_standing)
        team_gaps = standings_service.derive_gaps(team_standing)
        driver_movements = standings_service.derive_movement(
            driver_standing,
            previous_positions(
                [x.driver_user_id for x in driver_snapshots],
                # The reserve joined the classification this round, as a reserve does.
                newcomers=frozenset({reserve_driver.key}) if reserve_driver else frozenset(),
            ),
        )
        team_movements = standings_service.derive_movement(
            team_standing,
            previous_positions([x.team_instance_id for x in team_snapshots]),
        )

        team_seat_assignments = {
            team_key_of[team.name]: {
                d.key: d.seat_number for d in drivers if (d.team_key or d.team_name) == team.name
            }
            for team in racing_teams
        }
        team_seat_counts = {
            team_key_of[team.name]: int(getattr(team, "max_seats", 0) or 0)
            for team in racing_teams
        }
        team_names = {
            team_key_of[t.name]: (getattr(t, "full_name", "") or t.name) for t in racing_teams
        }

        drivers_drawing = standings_drawing_composer.resolve_drawing(
            template_key=DRIVERS_TEMPLATE_KEY,
            snapshots=driver_snapshots,
            display_names=names,
            team_names={d.key: d.team_name for d in drivers},
            team_keys={d.key: (d.team_key or d.team_name) for d in drivers},
            movements=driver_movements,
            gaps=driver_gaps,
            nationalities=flags,
            reserve_user_ids={reserve_driver.key} if reserve_driver else set(),
            show_reserves=reserve_driver is not None,
            rounds=rounds,
            round_session_results=round_session_results,
            **shared,
        )
        constructors_drawing = standings_drawing_composer.resolve_drawing(
            template_key=CONSTRUCTORS_TEMPLATE_KEY,
            snapshots=team_snapshots,
            display_names=team_names,
            team_names=team_names,
            team_keys={team_key_of[t.name]: t.name for t in racing_teams},
            movements=team_movements,
            gaps=team_gaps,
            team_seat_assignments=team_seat_assignments,
            team_seat_counts=team_seat_counts,
            driver_display_names=names,
            rounds=rounds,
            round_session_results=round_session_results,
            **shared,
        )
        return drivers_drawing, constructors_drawing

    def sanction_text(self, penalty_type: str | None, time_seconds: int | None) -> str:
        return describe_penalty(penalty_type, time_seconds)
