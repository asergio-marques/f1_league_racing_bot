"""Writing a session's results, a round's replacement or a session's points for a test that seeds
them, on a connection opened and committed here.

The results module writes these on a connection it is handed and commits nothing
(`_save_session_result_in_tx`, `replace_round_results_on`, `_apply_points_from_config_on`): the
submission's and the resubmission's saves are the change queue's (#439, slice 6b). A test that
only needs the rows in place calls these, which keep the call the committing forms had.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services import result_submission_service as service


async def save_session_result(
    db_path: str,
    round_id: int,
    division_id: int,
    session_type: SessionType,
    status: str,
    config_name: str | None,
    submitted_by: int | None,
    driver_rows: list[dict],
    fl_driver_override: int | None = None,
) -> int:
    """Save one session and its driver rows, committed; gives the session result's id."""
    async with get_connection(db_path) as db:
        session_result_id = await service._save_session_result_in_tx(
            db, round_id, division_id, session_type, status, config_name,
            submitted_by, driver_rows, fl_driver_override,
        )
        await db.commit()
    return session_result_id


async def replace_round_results(
    db_path: str,
    round_id: int,
    division_id: int,
    season_id: int,
    collected: list[service.CollectedSession],
) -> None:
    """Replace every session of a round with *collected*, in one transaction: all of it lands or,
    where anything raises, none of it."""
    async with get_connection(db_path) as db:
        try:
            await service.replace_round_results_on(
                db, round_id, division_id, season_id, collected
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


async def apply_points_from_config(
    db_path: str,
    session_result_id: int,
    season_id: int,
    config_name: str,
    session_type: SessionType,
) -> None:
    """Score a saved session from the season's points configuration, committed."""
    async with get_connection(db_path) as db:
        if await service._apply_points_from_config_on(
            db, session_result_id, season_id, config_name, session_type
        ):
            await db.commit()
