"""Where each results dialogue has got to, read from the database (#439, slice 6b, C43).

A first submission, a resubmission and an amendment each ask for a round's sessions one paste at a
time. Once each paste is a turn on the change queue, nothing waits in a coroutine for the next
paste: the turn reads which session it expects from what the dialogue has saved.

- **A first submission** expects the first of the round's sessions, in running order, with no
  `session_results` row (a session typed CANCELLED has its row too).
- **A resubmission** holds its pastes as drafts until the last swaps the results, so it expects the
  first session with no draft, whatever the round's results already hold.
- **An amendment** expects the first of its own `session_types` with no draft.
- **A session awaiting its configuration choice** is marked by a draft with `config_name` unset,
  and is expected again.

The functions are read inside each test, so the file collects before they are built. The drafts are
written straight into the tables the migrations declare.
"""
from __future__ import annotations

import os

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.models.points_config import SessionType

SEASON_1 = 1
PRO = 11
ROUND_ID = 21
AMEND_ID = 31
SUBMISSION_CHANNEL = 702
AMEND_CHANNEL = 705


async def _round(tmp_path, *, done: tuple[tuple[str, str], ...] = ()) -> str:
    """Division 11 (Pro) of season 1, its sprint round 3 at Silverstone awaiting results, with a
    submission channel. *done* gives the sessions already saved, each with its status."""
    db_path = os.path.join(str(tmp_path), "dialogue_progress.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_1,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (PRO, SEASON_1),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'SPRINT', "
            "'Silverstone', 'AWAITING_RESULTS')",
            (ROUND_ID, PRO),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
            "VALUES (?, ?, '2026-02-01T18:00:00+00:00')",
            (ROUND_ID, SUBMISSION_CHANNEL),
        )
        for session_type, status in done:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name) VALUES (?, ?, ?, ?, 'Standard')",
                (ROUND_ID, PRO, session_type, status),
            )
        await db.commit()
    return db_path


async def _resubmission_draft(db_path: str, session_type: str, *,
                              config_name: str | None = "Standard") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO resubmission_drafts (round_id, session_type, status, rows_json, "
            "config_name, submitted_by) VALUES (?, ?, 'ACTIVE', '[]', ?, 77)",
            (ROUND_ID, session_type, config_name),
        )
        await db.commit()


async def _amendment(db_path: str, session_types: list[str]) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (id, round_id, channel_id, session_types, "
            "created_at, started_by) VALUES (?, ?, ?, ?, '2026-02-03T18:00:00+00:00', 77)",
            (AMEND_ID, ROUND_ID, AMEND_CHANNEL,
             "[" + ", ".join(f'"{value}"' for value in session_types) + "]"),
        )
        await db.commit()


async def _amend_draft(db_path: str, session_type: str, *,
                       config_name: str | None = "Standard") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO amend_drafts (amend_id, session_type, rows_json, config_name) "
            "VALUES (?, ?, '[]', ?)",
            (AMEND_ID, session_type, config_name),
        )
        await db.commit()


async def test_the_expected_session_of_a_first_submission_is_the_first_without_results(tmp_path):
    """Round 3 is a sprint round. With nothing saved its first paste is Sprint Qualifying; with
    Sprint Qualifying saved and Sprint Race typed CANCELLED, it is Feature Qualifying; with all
    four saved, none is expected."""
    from leaguebot.results.services.result_submission_service import expected_session_on

    db_path = await _round(tmp_path)
    async with get_connection(db_path) as db:
        assert await expected_session_on(db, ROUND_ID) is SessionType.SPRINT_QUALIFYING

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_QUALIFYING', 'ACTIVE'), (?, ?, 'SPRINT_RACE', 'CANCELLED')",
            (ROUND_ID, PRO, ROUND_ID, PRO),
        )
        await db.commit()
        assert await expected_session_on(db, ROUND_ID) is SessionType.FEATURE_QUALIFYING

        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE'), (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, PRO, ROUND_ID, PRO),
        )
        await db.commit()
        assert await expected_session_on(db, ROUND_ID) is None


async def test_the_expected_session_of_a_resubmission_is_the_first_without_a_draft(tmp_path):
    """Round 3's four sessions are all saved, and a resubmission of it has a draft for Sprint
    Qualifying alone: the resubmission expects Sprint Race, the round's saved results counting
    for nothing; with all four drafted, none is expected."""
    from leaguebot.results.services.result_submission_service import (
        expected_resubmitted_session_on,
    )

    db_path = await _round(tmp_path, done=tuple(
        (session, "ACTIVE")
        for session in ("SPRINT_QUALIFYING", "SPRINT_RACE", "FEATURE_QUALIFYING", "FEATURE_RACE")
    ))
    async with get_connection(db_path) as db:
        assert await expected_resubmitted_session_on(db, ROUND_ID) is (
            SessionType.SPRINT_QUALIFYING
        )

    await _resubmission_draft(db_path, "SPRINT_QUALIFYING")
    async with get_connection(db_path) as db:
        assert await expected_resubmitted_session_on(db, ROUND_ID) is SessionType.SPRINT_RACE

    for session in ("SPRINT_RACE", "FEATURE_QUALIFYING", "FEATURE_RACE"):
        await _resubmission_draft(db_path, session)
    async with get_connection(db_path) as db:
        assert await expected_resubmitted_session_on(db, ROUND_ID) is None


async def test_the_expected_session_of_an_amendment_follows_its_session_types(tmp_path):
    """Round 3's four sessions are saved. An amendment re-enters its Sprint Race and Feature Race
    only: it expects Sprint Race first, then, with Sprint Race drafted, Feature Race — never a
    session it does not cover; with both drafted, none."""
    from leaguebot.results.services.result_submission_service import (
        expected_amended_session_on,
    )

    db_path = await _round(tmp_path, done=tuple(
        (session, "ACTIVE")
        for session in ("SPRINT_QUALIFYING", "SPRINT_RACE", "FEATURE_QUALIFYING", "FEATURE_RACE")
    ))
    await _amendment(db_path, ["SPRINT_RACE", "FEATURE_RACE"])
    async with get_connection(db_path) as db:
        assert await expected_amended_session_on(db, AMEND_ID) is SessionType.SPRINT_RACE

    await _amend_draft(db_path, "SPRINT_RACE")
    async with get_connection(db_path) as db:
        assert await expected_amended_session_on(db, AMEND_ID) is SessionType.FEATURE_RACE

    await _amend_draft(db_path, "FEATURE_RACE")
    async with get_connection(db_path) as db:
        assert await expected_amended_session_on(db, AMEND_ID) is None


async def test_a_session_awaiting_its_configuration_is_expected_again(tmp_path):
    """A resubmission's Sprint Qualifying and an amendment's Sprint Race were each pasted, but
    their points configuration is still to be chosen (the draft's configuration unset). Each
    dialogue expects that session again, not the next."""
    from leaguebot.results.services.result_submission_service import (
        expected_amended_session_on,
        expected_resubmitted_session_on,
    )

    db_path = await _round(tmp_path, done=tuple(
        (session, "ACTIVE")
        for session in ("SPRINT_QUALIFYING", "SPRINT_RACE", "FEATURE_QUALIFYING", "FEATURE_RACE")
    ))
    await _resubmission_draft(db_path, "SPRINT_QUALIFYING", config_name=None)
    await _amendment(db_path, ["SPRINT_RACE", "FEATURE_RACE"])
    await _amend_draft(db_path, "SPRINT_RACE", config_name=None)

    async with get_connection(db_path) as db:
        assert await expected_resubmitted_session_on(db, ROUND_ID) is (
            SessionType.SPRINT_QUALIFYING
        )
        assert await expected_amended_session_on(db, AMEND_ID) is SessionType.SPRINT_RACE
