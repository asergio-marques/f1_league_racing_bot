"""SeasonService — season, division, round, and session management."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection, inserted_id, sole_row
from leaguebot.core.models.cancellation import SCOPE_ROUND, CancellationNotices
from leaguebot.core.models.division import Division, DivisionStatus
from leaguebot.core.models.refusal import Refused
from leaguebot.core.models.round import (
    ROUND_AWAITING_RESULTS_MODULE,
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    ROUND_TERMINAL,
    Round,
    RoundFormat,
    RoundStatus,
    read_scheduled_at,
    stored_scheduled_at,
)
from leaguebot.core.models.season import (
    Season,
    SeasonStage,
    SeasonStatus,
)
from leaguebot.core.models.session import Session, SessionType, SESSIONS_BY_FORMAT
from leaguebot.core.services.modules.image_drawing import Held
from leaguebot.core.utils.hooks import AwaitedHook
from leaguebot.core.utils.input_validator import NAME

#: Rendered from the model's sets so the queries below cannot drift from the rule they
#: encode. Interpolated rather than bound because they are our own enum values and the
#: count varies; nothing here comes from a user.
_TERMINAL_SQL = ", ".join(f"'{v}'" for v in sorted(ROUND_TERMINAL))
_CANCELLABLE_SQL = ", ".join(f"'{v}'" for v in sorted(ROUND_CANCELLABLE))
_AWAITING_RESULTS_MODULE_SQL = ", ".join(
    f"'{v}'" for v in sorted(ROUND_AWAITING_RESULTS_MODULE)
)
_RACED_AWAITING_VERDICTS_SQL = ", ".join(
    f"'{v}'" for v in sorted(ROUND_RACED_AWAITING_VERDICTS)
)

log = logging.getLogger(__name__)


def validate_division_name(name: str) -> str | None:
    """Why *name* cannot name a division, or None where it can.

    A division's name heads every posting and every graphic of the division, so it is held to
    the rules every name a league types is held to: no group mention, no emoji and no markup.
    Checked by each command that sets one, before its own test for a duplicate.
    """
    return NAME.check("division name", name).refusal


def validate_division_tier(
    tier: int, other_tiers: Iterable[int], *, within: str = "setup"
) -> str | None:
    """Why a division may not take *tier*, or None where it may.

    The core specification's rule: a division's tier is 1 or higher, and no two divisions of a
    season share one. *other_tiers* are the tiers the season's other divisions hold, so a
    division amended to keep its own tier is not refused. *within* names the whole in the
    taken-tier words — "setup", as `/division add` and `/division amend` say it, or "season", as
    `/division duplicate` has always said it. The reason carries no mark: each command adds its
    own.
    """
    if tier < 1:
        return "Tier must be 1 or higher."
    if tier in set(other_tiers):
        return f"A division with tier **{tier}** already exists in this {within}."
    return None


def division_amendment_changes_nothing(
    division: Division,
    *,
    new_name: str | None = None,
    tier: int | None = None,
    role_id: int | None = None,
) -> bool:
    """Whether a rename or amendment of *division* asks only for the values it already holds.

    `/division rename` to its own name, and `/division amend` with the values that stand, change
    nothing, and record that nothing was changed rather than writing an audit entry (the core
    specification's "The record of what changed"). The change's save asks this, of the division as
    it reads it on its own connection. A name in another case is a change: the name is shown as it
    is written. Where nothing is asked at all this answers False, that being the change's own
    refusal.
    """
    asked = {"name": new_name, "tier": tier, "mention_role_id": role_id}
    given = {field: value for field, value in asked.items() if value is not None}
    if not given:
        return False
    return all(getattr(division, field) == value for field, value in given.items())


class SeasonImmutableError(Exception):
    """Raised when a mutation is attempted on a COMPLETED (archived) season."""


@dataclass(frozen=True)
class SeasonCapacityCounts:
    """What the season review's capacity warnings ask of the image module
    (`SeasonService.capacity_counts`): the division holding the most rounds, the most
    seated drivers and the most race teams, each or None where the season holds no
    division, and the most sessions any round of the season is run over."""

    longest_calendar: Held | None
    most_drivers: Held | None
    most_teams: Held | None
    longest_sessions: int


class SeasonService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Season
    # ------------------------------------------------------------------

    async def get_confirmed_season(self) -> Season | None:
        """Return the ACTIVE season, or None.

        ACTIVE is every stage from the first confirmation of placements to the season's
        completion or cancellation: the three ongoing stages and Pending completion. Read
        ``Season.stage`` where the stage within ACTIVE matters.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, status, season_number, stage FROM seasons "
                "WHERE status = ?",
                (SeasonStatus.ACTIVE.value,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return _row_to_season(row)

    async def get_season_for_server(self) -> Season | None:
        """Return the most recent season regardless of status.

        Used by channel assignment commands that should work in any season state.
        Returns the season with the highest id for the server.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, status, season_number, stage FROM seasons "
                " ORDER BY id DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def get_setup_or_active_season(self) -> Season | None:
        """Return the live (SETUP or ACTIVE) season, or None.

        A server holds at most one, enforced by the partial unique index
        `idx_seasons_one_live`, so the two states cannot both be present and there is nothing to
        choose between. The ordering is stated anyway: this was a bare ``LIMIT 1`` over both
        states, which returned an uncontracted row wherever the invariant had been
        broken, and matching :meth:`get_previewable_season` keeps every reader of a
        league's current season agreeing on which one that is.
        """
        return await live_season(self._db_path)

    async def get_previewable_season(self) -> Season | None:
        """Return the season an `/images test` preview draws, or None.

        The server's one live season — SETUP or ACTIVE. A COMPLETED or CANCELLED season is
        never previewable: a preview is a check on what the league is running or about to
        run, and a server keeps its whole archive besides.

        A server holds at most one live season (`idx_seasons_one_live`), so the ACTIVE-before-SETUP
        ordering below no longer arbitrates anything and is kept as defence: it costs
        nothing, and it means this and every other reader of "the season of this server"
        answer the same row even on a database that predates the constraint.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, status, season_number, stage FROM seasons "
                "WHERE status IN ('ACTIVE', 'SETUP') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def previous_season_number_on(self, db: aiosqlite.Connection) -> int:
        """The highest season number the league has already committed, or 0, read on *db*, a
        connection the caller holds: a save numbers the season it inserts from what its own
        connection reads, never from a second one.

        "Committed" means the number has been issued and cannot be re-used — every status but
        SETUP. A season still in setup holds a provisional number and is excluded, so that a league
        drafting its next season does not push the count forward twice. A new season is numbered
        one above it. The highest number, not a count of seasons: a count agrees only while the
        numbers run from 1 without a gap, and falls behind the highest number the moment one goes
        missing, handing out a number already in use.
        """
        cursor = await db.execute(
            "SELECT MAX(season_number) AS highest FROM seasons WHERE status != 'SETUP'",
        )
        row = await cursor.fetchone()
        if row is None or row["highest"] is None:
            return 0
        return int(row["highest"])

    async def begin_setup_on(
        self, db: aiosqlite.Connection, *, start_date: date, game_edition: int
    ) -> Season:
        """Insert the SETUP season, in Configuration, on *db*, a connection the caller holds, and
        return it: numbered one above the highest the league has committed, read on *db* itself,
        so that the number and the row are one save's. Commits nothing."""
        season_number = await self.previous_season_number_on(db) + 1
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, game_edition, stage) "
            "VALUES (?, 'SETUP', ?, ?, ?)",
            (
                start_date.isoformat(),
                season_number,
                game_edition,
                SeasonStage.CONFIGURATION.value,
            ),
        )
        return Season(
            id=inserted_id(cursor),
            start_date=start_date,
            status=SeasonStatus.SETUP,
            season_number=season_number,
            game_edition=game_edition,
            stage=SeasonStage.CONFIGURATION,
        )

    async def add_division_on(
        self, db: aiosqlite.Connection, *, name: str, role_id: int, tier: int
    ) -> Division:
        """Insert a division, in Setup, into the season being set up, on *db*, a connection the
        caller holds, and return it. Commits nothing. Raises LookupError where no season is being
        set up, and the database's own error where the season already holds the name, in any
        ASCII case: the check that refuses a duplicate is the caller's, the schema the backstop."""
        cursor = await db.execute("SELECT id FROM seasons WHERE status = 'SETUP' LIMIT 1")
        season = await cursor.fetchone()
        if season is None:
            raise LookupError("no season is being set up")
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier) VALUES (?, ?, ?, ?)",
            (season["id"], name, role_id, tier),
        )
        return Division(
            id=inserted_id(cursor),
            season_id=season["id"],
            name=name,
            mention_role_id=role_id,
            status=DivisionStatus.SETUP.value,
            tier=tier,
        )

    async def get_setup_season(self) -> Season | None:
        """Return the SETUP season, with its game edition, or None."""
        async with get_connection(self._db_path) as db:
            return await self.get_setup_season_on(db)

    async def get_setup_season_on(self, db: aiosqlite.Connection) -> Season | None:
        """`get_setup_season` on *db*, a connection the caller holds."""
        cursor = await db.execute(
            "SELECT id, start_date, status, season_number, game_edition, stage FROM seasons "
            "WHERE status = 'SETUP' LIMIT 1",
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def has_existing_season(self) -> bool:
        """Return True if any season row exists (any status)."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons LIMIT 1",
            )
            row = await cursor.fetchone()
        return row is not None

    async def has_active_or_completed_season(self) -> bool:
        """Return True if an ACTIVE or COMPLETED season exists."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons WHERE status IN ('ACTIVE', 'COMPLETED') LIMIT 1",
            )
            row = await cursor.fetchone()
        return row is not None

    async def has_active_or_setup_season(self) -> bool:
        """Return True if an ACTIVE or SETUP season exists."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM seasons WHERE status IN ('ACTIVE', 'SETUP') LIMIT 1",
            )
            row = await cursor.fetchone()
        return row is not None

    async def count_completed_seasons(self) -> int:
        """Return the count of COMPLETED seasons."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(id) FROM seasons WHERE status = 'COMPLETED'",
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_stage(self, season_id: int) -> SeasonStage | None:
        """The lifecycle stage of *season_id*, or None where no such season exists."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            row = await cursor.fetchone()
        if row is None or row["stage"] is None:
            return None
        return SeasonStage(row["stage"])

    async def assert_season_mutable(self, season: "Season") -> None:
        """Raise SeasonImmutableError if *season* is COMPLETED or CANCELLED."""
        if season.status in (SeasonStatus.COMPLETED, SeasonStatus.CANCELLED):
            raise SeasonImmutableError(
                f"Season {season.season_number} is archived and cannot be modified."
            )

    async def validate_division_tiers(self, season_id: int) -> None:
        """Validate division tiers form a gapless sequence 1..N.

        Raises Refused, with a diagnostic message, if any tier is missing.
        Cancelled divisions are excluded from the check.
        """
        divisions = await self.get_divisions(season_id)
        active_divs = [d for d in divisions if d.status != "CANCELLED"]
        if not active_divs:
            return
        tiers = sorted(d.tier for d in active_divs)
        expected = list(range(1, len(tiers) + 1))
        if tiers != expected:
            existing = sorted(set(tiers))
            missing = sorted(set(expected) - set(tiers))
            raise Refused(
                f"Division tiers are not sequential. "
                f"Current tiers: {existing}. "
                f"Missing tier(s): {missing}."
            )

    async def get_last_scheduled_at(self) -> datetime | None:
        """Return the latest scheduled_at across all ACTIVE rounds for the active season."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT MAX(r.scheduled_at)
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.status = 'ACTIVE'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                """,
            )
            row = await cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return read_scheduled_at(row[0])

    async def get_outstanding_rounds(self) -> list[dict]:
        """Return division, round_number and track_name for every round still to be finalised.

        Cancelled rounds and cancelled divisions are excluded — neither is waiting on anybody.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                f"""
                SELECT d.name AS division, r.round_number, r.track_name
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.status    = 'ACTIVE'
                  AND r.status NOT IN ({_TERMINAL_SQL})
                  AND d.status   != 'CANCELLED'
                ORDER BY d.name, r.round_number
                """,
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    async def delete_season(
        db: aiosqlite.Connection,
        season_id: int,
        *,
        rounds_deleted: AwaitedHook,
        divisions_deleted: AwaitedHook,
        season_deleted: AwaitedHook,
        profiles_deleted: AwaitedHook,
    ) -> None:
        """Delete one season and everything under it, on the connection handed to it.

        What `/season abort` leaves of a season whose placements were never confirmed: nothing
        at all, its signups included.

        It writes on *db* and commits nothing, for the abort's one save to commit. Each module
        deletes its own rows of the season through a hook, on that connection: *rounds_deleted*
        with the season's round ids, then *divisions_deleted* with its division ids (neither
        when there are none), then *season_deleted* with the season id. Core then deletes its
        own rows: sessions, the season's test drivers, assignments, teams and seats, rounds,
        review prompts, divisions and the season.

        Raises ``ValueError`` for a season not in SETUP, before anything is deleted. Its number
        is committed from the moment it leaves SETUP, and that number is how a league names
        its own history: removing one would leave a gap nothing explains. A season id that names no
        season is not an error.
        """
        cursor = await db.execute(
            "SELECT status FROM seasons WHERE id = ?", (season_id,)
        )
        row = await cursor.fetchone()
        if row is not None and row["status"] != SeasonStatus.SETUP.value:
            raise ValueError(
                f"season {season_id} is {row['status']}; a season whose number is "
                "committed cannot be deleted"
            )

        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (season_id,)
        )
        division_rows = await cursor.fetchall()
        division_ids = [r[0] for r in division_rows]

        round_ids: list[int] = []
        if division_ids:
            ph = ",".join("?" * len(division_ids))
            cursor = await db.execute(
                f"SELECT id FROM rounds WHERE division_id IN ({ph})",
                division_ids,
            )
            round_ids = [r[0] for r in await cursor.fetchall()]

        if round_ids:
            await rounds_deleted.call(db, round_ids)
        if division_ids:
            await divisions_deleted.call(db, division_ids)
        await season_deleted.call(db, season_id)

        if round_ids:
            ph = ",".join("?" * len(round_ids))
            await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)

        # ── Driver/team children ────────────────────────────────────────
        if division_ids:
            ph = ",".join("?" * len(division_ids))

            # The season's fake (test-mode) drivers go with it, by the deletion test mode
            # itself takes: it lets go of every row holding them, in this season or any
            # other, and keeps their history.
            from leaguebot.core.services.seasons.season_lifecycle_service import delete_driver_profiles

            cursor = await db.execute(
                f"""
                SELECT DISTINCT dp.id
                FROM driver_profiles dp
                JOIN driver_season_assignments dsa ON dsa.driver_profile_id = dp.id
                WHERE dp.is_test_driver = 1
                  AND dsa.division_id IN ({ph})
                """,
                division_ids,
            )
            test_profile_ids = [r[0] for r in await cursor.fetchall()]
            await delete_driver_profiles(
                db, test_profile_ids, keep_history=True, profiles_deleted=profiles_deleted
            )

            await db.execute(f"DELETE FROM driver_season_assignments WHERE division_id IN ({ph})", division_ids)

            # team_seats → team_instances → divisions
            cursor = await db.execute(
                f"SELECT id FROM team_instances WHERE division_id IN ({ph})", division_ids
            )
            team_instance_ids = [r[0] for r in await cursor.fetchall()]
            if team_instance_ids:
                tiph = ",".join("?" * len(team_instance_ids))
                await db.execute(f"DELETE FROM team_seats WHERE team_instance_id IN ({tiph})", team_instance_ids)
            await db.execute(f"DELETE FROM team_instances WHERE division_id IN ({ph})", division_ids)
            await db.execute(f"DELETE FROM rounds WHERE division_id IN ({ph})", division_ids)

        await db.execute("DELETE FROM season_review_prompts WHERE season_id = ?", (season_id,))
        await db.execute("DELETE FROM divisions WHERE season_id = ?", (season_id,))
        await db.execute("DELETE FROM seasons WHERE id = ?", (season_id,))

    # ------------------------------------------------------------------
    # Division
    # ------------------------------------------------------------------

    async def get_previewable_divisions(
        self, *, timeout: float | None = None
    ) -> list[Division]:
        """The divisions of the season an `/images test` preview draws, in one connection.

        Exactly `get_previewable_season` followed by `get_divisions`, on one connection: the two
        called in turn take two checkouts of a connection, each with its `PRAGMA foreign_keys` and,
        when none is idle to be lent, an open and a close, on a path with three seconds to answer
        Discord in. Doing both on one connection halves that, and the second query is only reached
        when the first found a season.

        Empty where the server holds no previewable season, which is the same answer the
        pair gave and is why the preview's division parameter is optional.

        *timeout* is passed to the connection as its lock wait; pass
        `AUTOCOMPLETE_TIMEOUT_SECONDS` on the autocomplete path so a contended database
        gives up well inside Discord's budget rather than answering into a dead token.

        Both original methods are left untouched and still used elsewhere; this is an
        addition, not a replacement.
        """
        async with get_connection(self._db_path, timeout=timeout) as db:
            cursor = await db.execute(
                "SELECT id FROM seasons "
                "WHERE status IN ('ACTIVE', 'SETUP') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
            )
            season_row = await cursor.fetchone()
            if season_row is None:
                return []

            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, status, tier, "
                "lineup_channel_id, calendar_channel_id, lineup_message_id, "
                "calendar_message_id "
                "FROM divisions WHERE season_id = ? ORDER BY tier",
                (season_row[0],),
            )
            rows = await cursor.fetchall()
        return [_row_to_division(r) for r in rows]

    async def get_divisions(self, season_id: int) -> list[Division]:
        """Return all divisions for *season_id*."""
        async with get_connection(self._db_path) as db:
            return await self.get_divisions_on(db, season_id)

    async def get_divisions_on(self, db: aiosqlite.Connection, season_id: int) -> list[Division]:
        """`get_divisions` on *db*, a connection the caller holds: what a save has written there
        is read back with the rest."""
        return await season_divisions_on(db, season_id)

    async def division_ids_on(self, db: aiosqlite.Connection, season_id: int) -> list[int]:
        """The ids of every division of *season_id*, cancelled ones included, in id order, read on
        *db*: an order that does not move when a division's tier does."""
        return sorted(division.id for division in await self.get_divisions_on(db, season_id))

    async def get_division(self, division_id: int) -> Division | None:
        """Return the division *division_id*, or None where there is none."""
        async with get_connection(self._db_path) as db:
            return await division_on(db, division_id)

    async def capacity_counts(self, season_id: int) -> SeasonCapacityCounts:
        """What the season review's warnings ask of the image module: the division holding
        the most rounds, the most seated drivers (reserves among them) and the most race
        teams (the reserve team not among them), and the most sessions any round of the
        season is run over — four where any round is a sprint, two otherwise, never fewer
        even where the season holds no round at all.

        Every division count is None where the season holds no division: there is then
        nothing for the calendar, the sheet or the standings to be asked about, and the
        season review asks only the check-in call's sessions.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT d.name AS name, "
                "       (SELECT COUNT(*) FROM rounds r WHERE r.division_id = d.id) "
                "           AS rounds, "
                "       (SELECT COUNT(*) FROM driver_season_assignments dsa "
                "         WHERE dsa.division_id = d.id) AS drivers, "
                "       (SELECT COUNT(*) FROM team_instances ti "
                "         WHERE ti.division_id = d.id AND ti.is_reserve = 0) AS teams "
                "FROM divisions d WHERE d.season_id = ?",
                (season_id,),
            )
            divisions = await cursor.fetchall()

            cursor = await db.execute(
                "SELECT COUNT(*) AS sprints FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "WHERE d.season_id = ? AND r.format = ?",
                (season_id, RoundFormat.SPRINT.value),
            )
            sprint_row = await cursor.fetchone()

        longest_sessions = 4 if (sprint_row and (sprint_row["sprints"] or 0) > 0) else 2

        if not divisions:
            return SeasonCapacityCounts(
                longest_calendar=None, most_drivers=None, most_teams=None,
                longest_sessions=longest_sessions,
            )

        longest = max(divisions, key=lambda d: d["rounds"] or 0)
        most_seated = max(divisions, key=lambda d: d["drivers"] or 0)
        most_fielded = max(divisions, key=lambda d: d["teams"] or 0)

        return SeasonCapacityCounts(
            longest_calendar=Held(longest["name"], longest["rounds"] or 0),
            most_drivers=Held(most_seated["name"], most_seated["drivers"] or 0),
            most_teams=Held(most_fielded["name"], most_fielded["teams"] or 0),
            longest_sessions=longest_sessions,
        )

    @staticmethod
    async def delete_division_on(
        db: aiosqlite.Connection,
        division_id: int,
        *,
        rounds_deleted: AwaitedHook,
        divisions_deleted: AwaitedHook,
    ) -> None:
        """Delete a division with its rounds, teams and seats, on the connection handed to it.

        It writes on *db* and commits nothing, for the delete's one save to commit. Each module
        deletes its own rows through a hook on that connection: *rounds_deleted* with the
        division's round ids (none, no call), then *divisions_deleted* with the division's id.
        Core then deletes its own rows.

        Fails while a driver is placed in it: the seat the placement points at has no delete
        action, so removing the seats breaks the placement's foreign key and the error reaches
        the caller, whose save rolls all of it back. The check that refuses such a division
        first is the caller's, the schema the backstop.
        """
        cursor = await db.execute(
            "SELECT id FROM rounds WHERE division_id = ?", (division_id,)
        )
        round_rows = await cursor.fetchall()
        round_ids = [r[0] for r in round_rows]

        if round_ids:
            await rounds_deleted.call(db, round_ids)
        await divisions_deleted.call(db, [division_id])

        if round_ids:
            ph = ",".join("?" * len(round_ids))
            await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)
            await db.execute("DELETE FROM rounds WHERE division_id = ?", (division_id,))

        # team_seats → team_instances: no cascade, must be deleted manually
        await db.execute(
            "DELETE FROM team_seats WHERE team_instance_id IN "
            "(SELECT id FROM team_instances WHERE division_id = ?)",
            (division_id,),
        )
        await db.execute("DELETE FROM team_instances WHERE division_id = ?", (division_id,))
        # driver_season_assignments.division_id has no cascade
        await db.execute(
            "DELETE FROM driver_season_assignments WHERE division_id = ?", (division_id,)
        )
        await db.execute("DELETE FROM divisions WHERE id = ?", (division_id,))

    async def division_holds_a_placed_driver(self, division_id: int) -> bool:
        """Whether a driver is placed in *division_id*: whether a placement points at a seat of
        one of its teams, the placement the schema's foreign key keeps the seat for."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM driver_season_assignments dsa "
                "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                "JOIN team_instances ti ON ti.id = ts.team_instance_id "
                "WHERE ti.division_id = ? LIMIT 1",
                (division_id,),
            )
            return await cursor.fetchone() is not None

    async def duplicate_division_on(
        self,
        db: aiosqlite.Connection,
        source_id: int,
        *,
        name: str,
        role_id: int,
        tier: int,
        offset: timedelta,
    ) -> Division:
        """Copy the division *source_id* into a new division of the season being set up, on *db*,
        a connection the caller holds, and return the new one. Commits nothing.

        The division is inserted as `add_division_on` inserts one, then each round of the source
        is copied with its moment moved by *offset* and the copies are renumbered, so that one
        save holds the division and its calendar. The checks that refuse a name or a tier already
        held are the caller's, the schema the backstop.
        """
        source_rounds = await self.get_division_rounds_on(db, source_id)
        division = await self.add_division_on(db, name=name, role_id=role_id, tier=tier)
        for rnd in source_rounds:
            await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    division.id,
                    rnd.round_number,
                    rnd.format.value,
                    rnd.track_name,
                    stored_scheduled_at(rnd.scheduled_at + offset),
                ),
            )
        await renumber_rounds_on(db, division.id)
        return division

    # ------------------------------------------------------------------
    # Round
    # ------------------------------------------------------------------

    async def get_round(self, round_id: int) -> Round | None:
        """Return a single round by ID."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "status FROM rounds WHERE id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()
        return _row_to_round(row) if row else None

    async def get_division_rounds(self, division_id: int) -> list[Round]:
        """Return all rounds for *division_id* ordered by round_number."""
        async with get_connection(self._db_path) as db:
            return await self.get_division_rounds_on(db, division_id)

    async def get_division_rounds_on(
        self, db: aiosqlite.Connection, division_id: int
    ) -> list[Round]:
        """`get_division_rounds` on *db*, a connection the caller holds: what a save has written
        there is read back with the rest."""
        return await division_rounds_in_order_on(db, division_id)

    async def add_round_on(
        self,
        db: aiosqlite.Connection,
        division_id: int,
        *,
        fmt: RoundFormat,
        track_name: str | None,
        scheduled_at: datetime,
    ) -> int:
        """Insert a round into *division_id* on *db*, a connection the caller holds, and return
        its id. Commits nothing, and leaves the division's numbering to `renumber_rounds_on`:
        the round is numbered 0 until the caller renumbers."""
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, 0, ?, ?, ?)",
            (division_id, fmt.value, track_name, stored_scheduled_at(scheduled_at)),
        )
        return inserted_id(cursor)

    @staticmethod
    async def delete_round_on(
        db: aiosqlite.Connection, round_id: int, *, rounds_deleted: AwaitedHook
    ) -> None:
        """Delete a round and renumber siblings, on the connection handed to it.

        It writes on *db* and commits nothing, for the delete's one save to commit. Each module
        deletes its own rows of the round through *rounds_deleted*, on that connection; core
        then deletes the round's sessions and the round, and renumbers the division's remaining
        rounds. A fault part-way leaves, once the save is rolled back, the round, its siblings'
        numbers and the modules' rows as they were. A round that is not there is left alone,
        and no division is renumbered on its account.
        """
        cursor = await db.execute(
            "SELECT division_id FROM rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return
        division_id: int = row[0]

        await rounds_deleted.call(db, [round_id])
        await db.execute("DELETE FROM sessions WHERE round_id = ?", (round_id,))
        await db.execute("DELETE FROM rounds WHERE id = ?", (round_id,))
        await renumber_rounds_on(db, division_id)

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def get_sessions(self, round_id: int) -> list[Session]:
        """Return all sessions for *round_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, round_id, session_type "
                "FROM sessions WHERE round_id = ?",
                (round_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_session(r) for r in rows]


async def renumber_rounds_on(db: aiosqlite.Connection, division_id: int) -> None:
    """Rewrite ``round_number`` for all rounds in a division on *db*, committing nothing.

    Sorted ascending by ``scheduled_at``. A change's save calls it, so the renumbering outlasts
    nothing the save does not (a round amendment writes the round and renumbers in one save).
    """
    cursor = await db.execute(
        "SELECT id FROM rounds WHERE division_id = ? ORDER BY scheduled_at",
        (division_id,),
    )
    rows = await cursor.fetchall()
    for i, row in enumerate(rows, start=1):
        await db.execute(
            "UPDATE rounds SET round_number = ? WHERE id = ?",
            (i, row[0]),
        )


async def create_sessions_for_round_on(
    db: aiosqlite.Connection, round_id: int, fmt: RoundFormat
) -> list[Session]:
    """Make the sessions of *round_id* the ones *fmt* defines on *db*, committing nothing.

    The season's approval makes every round's sessions in the one save that also commits the
    placements, takes the points snapshot and moves the season to Ongoing, so the sessions are
    written on the connection that save holds and committed with it, or not at all.

    **Replaced, not added to.** A confirmation that writes the sessions and then fails before the
    season goes active leaves them behind, and were they added to, the next confirmation would write
    a full second set, every session of which the phases would forecast twice. However often a save
    is tried, the round holds one set: the one its format defines now. The old rows go with whatever
    phase data they held, so this is for a round no phase has been drawn for.
    """
    session_types: list[SessionType] = SESSIONS_BY_FORMAT.get(fmt, [])
    sessions: list[Session] = []
    await db.execute("DELETE FROM sessions WHERE round_id = ?", (round_id,))
    for st in session_types:
        cursor = await db.execute(
            "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
            (round_id, st.value),
        )
        sessions.append(Session(id=inserted_id(cursor), round_id=round_id, session_type=st))
    return sessions


async def commit_placements_on(db: aiosqlite.Connection, season_id: int) -> int:
    """Commit every placement of *season_id* not yet committed on *db*; returns how many.

    Committed as placements are confirmed, and nothing is committed here: the season's approval
    holds the save. A committed placement is part of the championship: it holds its roles, stands in
    its lineup, is called to check-in and scored in results and standings.
    """
    cursor = await db.execute(
        "UPDATE driver_season_assignments SET committed = 1 "
        "WHERE season_id = ? AND committed = 0",
        (season_id,),
    )
    return cursor.rowcount


async def transition_to_active_on(db: aiosqlite.Connection, season_id: int) -> bool:
    """Set a season ACTIVE, and its divisions with it, on *db*, committing nothing.

    Guarded: only a season still being set up, in Placements, moves; one already Ongoing is left
    as it is, and its divisions with it, and ``False`` is returned. The trigger
    ``seasons_stage_follow_status`` takes the stage to Ongoing with the status.

    The divisions move SETUP -> ACTIVE in the same save: a division left in setup would sit there
    for its whole life, and `/season cancel` would post its notice to none of them. A cancelled
    division is left alone: a division called off during setup does not start racing because the
    season did.
    """
    cursor = await db.execute(
        "UPDATE seasons SET status = ? WHERE id = ? AND status = 'SETUP' AND stage = 'PLACEMENTS'",
        (SeasonStatus.ACTIVE.value, season_id),
    )
    if cursor.rowcount == 0:
        return False
    await db.execute(
        "UPDATE divisions SET status = 'ACTIVE' WHERE season_id = ? AND status = 'SETUP'",
        (season_id,),
    )
    return True


async def set_round_status_on(
    db: aiosqlite.Connection,
    round_id: int,
    status: RoundStatus,
    *,
    only_from: Iterable[str] | None = None,
) -> bool:
    """Set a round's status on *db*, committing nothing, and say whether it moved.

    The status alone: nothing else about the round is written, and this imports nothing of any
    module, so the results module sets a round's place in its review through core's own writer and
    the write is part of whatever save it is handed. What else moves with the status, a round's
    former drivers becoming marked as it turns FINAL, is the caller's: results' own paths (the
    appeals approval, an amendment, the revert) mark them through results' own recompute, and the
    two closers of core's that make a round FINAL hand the rounds to the hook a round made final
    calls (`rounds_made_final`), in the same save.

    The write is guarded, as every write of a round's status here is, so that a stale caller cannot
    drag a round backwards. With *only_from* the round must stand in one of those states
    (``ROUND_CANCELLABLE`` for a review opening, which a resubmission must not drag back from
    appeals); without it the round must not be terminal, since a settled round is never reopened. A
    round that fails the guard is left as it is and ``False`` is returned.
    """
    if only_from is None:
        guard = f"status NOT IN ({_TERMINAL_SQL})"
        params: tuple = (status.value, round_id)
    else:
        allowed = sorted(only_from)
        guard = f"status IN ({', '.join('?' for _ in allowed)})"
        params = (status.value, round_id, *allowed)
    cursor = await db.execute(
        f"UPDATE rounds SET status = ? WHERE id = ? AND {guard}", params
    )
    return cursor.rowcount > 0


async def cancel_round_on(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
) -> str | None:
    """Mark a round CANCELLED on *db*, committing nothing.

    The round is cancelled only while it may be: its status in ``ROUND_CANCELLABLE`` and its
    season not archived. The write is guarded on that status, so a round that moved on since the
    caller looked is left alone; ``/round cancel``'s check judges the same rule when it is asked
    and when it runs, and this is the save's own backstop.

    The ``round.status`` audit records the status the round was really cancelled from, written
    on *db* in the plain-string form ``end_rounds_awaiting_results_on`` writes its own in.
    Cancelling the last outstanding round is what finishes a division, so the division is
    refreshed here too, and the season moved on where that was the last it waited on, all in the
    caller's save.

    Returns the status the round was cancelled from, or ``None`` where nothing was moved.
    """
    cursor = await db.execute(
        """
        SELECT r.division_id, r.status, s.status AS season_status
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        JOIN seasons s ON s.id = d.season_id
        WHERE r.id = ?
        """,
        (round_id,),
    )
    row = await cursor.fetchone()
    if row is None or row["season_status"] in ("COMPLETED", "CANCELLED"):
        return None
    previous: str = row["status"]
    division_id: int = row["division_id"]

    cursor = await db.execute(
        f"UPDATE rounds SET status = 'CANCELLED' WHERE id = ? AND status IN ({_CANCELLABLE_SQL})",
        (round_id,),
    )
    if cursor.rowcount == 0:
        return None
    await db.execute(
        """
        INSERT INTO audit_entries
            (actor_id, actor_name, division_id, change_type,
             old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id, actor_name, division_id,
            "round.status", previous, "CANCELLED", now.isoformat(),
        ),
    )
    await refresh_division_status_on(db, division_id)
    return previous


@dataclass(frozen=True)
class RoundEndedNotHeld:
    """What ending a round as not held gave: *previous*, the status it ended from, *target*, the
    round's target as the notices hook was handed it, and *notices*, what each subscriber to
    `cancellation_notices` planned for it, in the order they signed up."""

    previous: str
    target: dict[str, Any]
    notices: list[CancellationNotices]


async def end_round_not_held_on(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
    cancellation_notices: AwaitedHook,
) -> RoundEndedNotHeld | None:
    """End a round CANCELLED because every session was entered as not held, committing nothing.

    A round whose every session is entered as CANCELLED takes no results and no review, so it
    never becomes final; ending it cancelled is what lets its division finish and its season be
    completed. It is not a round called off: ``rounds.not_held`` is set, and is the one thing that
    tells the two apart, for the check-in call and the forecasts of such a round come down a day
    after its start as any raced round's do, where a round called off keeps them (attendance and
    weather read the column; this is the only writer of it).

    Only a round awaiting its results (a first submission) or its report verdicts (a
    resubmission) is moved; the write is guarded on that status, so a round that moved on since
    the caller looked is left alone. The ``round.status`` audit and the division's refresh, the
    season moved on where this was the last round it waited on, are `cancel_round_on`'s, all
    written on *db* in the caller's save.

    Where it moved the round it calls *cancellation_notices*, the hook each module's own notice
    of a round called off is planned through, on *db*, for the round's scope and the round
    (``round_id``, ``division_id``, ``division_name``, ``season_id``, ``season_number``,
    ``round_number`` and ``track_name``), as the three cancellations call it, and gives what its
    subscribers planned beside the status the round ended from and that same target. The caller turns the notices
    into jobs; this plans none.

    Returns None where nothing was moved, and calls no subscriber.
    """
    cursor = await db.execute(
        "SELECT r.division_id, r.status FROM rounds r WHERE r.id = ?", (round_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    previous: str = row["status"]
    division_id: int = row["division_id"]
    cursor = await db.execute(
        "UPDATE rounds SET status = 'CANCELLED', not_held = 1 WHERE id = ? "
        "AND status IN ('AWAITING_RESULTS', 'AWAITING_REPORT_VERDICTS')",
        (round_id,),
    )
    if cursor.rowcount == 0:
        return None
    await db.execute(
        """
        INSERT INTO audit_entries
            (actor_id, actor_name, division_id, change_type,
             old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id, actor_name, division_id,
            "round.status", previous, "CANCELLED", now.isoformat(),
        ),
    )
    await refresh_division_status_on(db, division_id)
    cursor = await db.execute(
        "SELECT r.division_id, r.round_number, r.track_name, d.name AS division_name, "
        "d.season_id, s.season_number FROM rounds r JOIN divisions d ON d.id = r.division_id "
        "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
        (round_id,),
    )
    ended = await sole_row(cursor)
    target = {
        "round_id": round_id, "division_id": int(ended["division_id"]),
        "division_name": str(ended["division_name"]), "season_id": int(ended["season_id"]),
        "season_number": int(ended["season_number"]), "round_number": int(ended["round_number"]),
        "track_name": ended["track_name"],
    }
    notices = await cancellation_notices.call(db, SCOPE_ROUND, target)
    return RoundEndedNotHeld(previous, target, notices)


async def cancel_division_on(
    db: aiosqlite.Connection,
    division_id: int,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
) -> list[int]:
    """Cancel a division and its unraced rounds on *db*, committing nothing.

    Only rounds that may still be cancelled are — those not yet run, and those whose
    results have not been entered. Once results are in, the drivers have reports and appeals
    to lodge and calling the round off would take that from them, so it keeps its place and
    its results; the division around it is what was called off. `ROUND_CANCELLABLE` carries
    the rule, and `/round cancel` reads the same set, so one round and a whole division
    cannot disagree about what may be called off.

    Returns the ids of the rounds called off, in round order. The season is left as it is: moving
    it on where this was its last running division is the division's cancellation's, in its own
    save, because `/season cancel` shares this function and must never leave the season it
    cancels at Pending completion.
    """
    cursor = await db.execute(
        "SELECT status FROM divisions WHERE id = ?", (division_id,)
    )
    row = await cursor.fetchone()
    previous = row["status"] if row else "ACTIVE"

    cursor = await db.execute(
        f"""
        SELECT id, status FROM rounds
        WHERE division_id = ?
          AND status IN ({_CANCELLABLE_SQL})
        ORDER BY round_number
        """,
        (division_id,),
    )
    unraced = [(r["id"], r["status"]) for r in await cursor.fetchall()]

    for round_id, round_status in unraced:
        await db.execute(
            "UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", (round_id,)
        )
        await db.execute(
            """
            INSERT INTO audit_entries
                (actor_id, actor_name, division_id, change_type,
                 old_value, new_value, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id, actor_name, division_id,
                "round.status", round_status, "CANCELLED", now.isoformat(),
            ),
        )

    await db.execute(
        "UPDATE divisions SET status = 'CANCELLED' WHERE id = ?",
        (division_id,),
    )
    await db.execute(
        """
        INSERT INTO audit_entries
            (actor_id, actor_name, division_id, change_type,
             old_value, new_value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id,
            actor_name,
            division_id,
            "division.status",
            previous,
            "CANCELLED",
            now.isoformat(),
        ),
    )
    return [round_id for round_id, _ in unraced]


async def refresh_division_status_on(db: aiosqlite.Connection, division_id: int) -> bool:
    """Move a division ACTIVE -> FINISHED once none of its rounds is outstanding, committing nothing.

    A round is outstanding until it reaches one of its two terminal states, FINAL or CANCELLED.
    Every other state is a round still waiting on somebody: for its date, for its results, for
    report verdicts, or for appeal verdicts.

    Division status is stored rather than derived, so that cancelling a division can record the
    fact and `/season cancel` can tell the running divisions apart from the called-off ones.
    Stored state can drift from the rounds it summarises, so this is called from every place a
    round's outcome settles — a round made FINAL or CANCELLED, by core's closers (all but
    `cancel_division_on`, which cancels the division itself, and `close_raced_rounds_on`, whose
    season's cancellation then cancels the divisions) or by the results module's own changes (the
    appeals approval, a round that starts with results off) — and again from the completion's first
    save, which cannot afford to strand a league on a stale row.

    The `status = 'ACTIVE'` guard is what makes it safe to call anywhere: a division still in
    SETUP has not started, and a CANCELLED one was called off deliberately. Neither is a division
    that has *finished*, and neither is ever touched here.

    A division finishing may be the last one its season waited on, so where it moves the
    season is moved on in the same save, on the same connection.

    Returns True if this call is what moved it.
    """
    cursor = await db.execute(
        f"""
        SELECT COUNT(*) FROM rounds
        WHERE division_id = ?
          AND status NOT IN ({_TERMINAL_SQL})
        """,
        (division_id,),
    )
    row = await cursor.fetchone()
    if row is None or row[0] != 0:
        return False

    cursor = await db.execute(
        "UPDATE divisions SET status = 'FINISHED' WHERE id = ? AND status = 'ACTIVE'",
        (division_id,),
    )
    moved = cursor.rowcount > 0
    if not moved:
        return False
    cursor = await db.execute("SELECT season_id FROM divisions WHERE id = ?", (division_id,))
    season_row = await cursor.fetchone()
    if season_row is not None:
        from leaguebot.core.services.seasons.season_lifecycle_service import (
            advance_to_pending_completion_on,
        )

        await advance_to_pending_completion_on(db, season_row["season_id"])
    return True


async def end_rounds_awaiting_results_on(
    db: aiosqlite.Connection,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
    rounds_made_final: AwaitedHook,
) -> list[dict]:
    """Close every round of the active season that only the results module could move.

    Called when the results module is switched off part-way through a season. The three
    states in ``ROUND_AWAITING_RESULTS_MODULE`` each wait on a results command, so with the
    module gone nothing will ever move them: the division never finishes, `/season complete`
    refuses for the rest of the season, and — because enabling is refused while a season is
    active — the league cannot undo it either, leaving `/season cancel` as the only way out.

    The rounds are closed as FINAL rather than CANCELLED. They were raced; it is their
    scoring that has been abandoned, and a cancelled round would tell the attendance module
    that nobody was expected to turn up.

    A NOT_RUN round is left where it is. It waits on the clock rather than on results, and
    the round's start closes it as FINAL at its own moment with the module off.

    Everything is written on *db*, the rounds, the ``round.status`` audit and each division the
    closing finishes, with the season it was the last of; nothing is committed, so that the caller
    saves it with the switch-off. Nothing here opens a connection of its own or awaits Discord.

    The rounds are then handed, once, with *rounds_made_final*, the hook a round made final
    calls, on *db* and in the same save: the modules sign their parts up to it, and results'
    marks the drivers who raced them as former drivers, the first pass's own finaliser
    being a results command that will never run for them. **Usually that marks nobody**, and
    that is correct rather than wasteful: turning the module off erases the season's results in
    the same save, before reaching here, and a driver whose results have been erased has raced
    nothing the bot still knows of. It marks where a league disables between seasons with an
    older season's results intact, and where the rounds are closed on their own. No round made
    CANCELLED is handed to it, and a round already final is not.

    Returns one dict per round closed — ``division``, ``round_number``, ``track_name`` and
    the ``status`` it was taken from — so the caller can report what it did.
    """
    stamp = now.isoformat()
    cursor = await db.execute(
        f"""
        SELECT r.id, r.round_number, r.track_name, r.status,
               d.id AS division_id, d.name AS division
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        JOIN seasons   s ON s.id = d.season_id
        WHERE s.status    = 'ACTIVE'
          AND d.status   != 'CANCELLED'
          AND r.status IN ({_AWAITING_RESULTS_MODULE_SQL})
        ORDER BY d.name, r.round_number
        """,
    )
    rows = [dict(r) for r in await cursor.fetchall()]

    for row in rows:
        await db.execute(
            "UPDATE rounds SET status = ? WHERE id = ?",
            (RoundStatus.FINAL.value, row["id"]),
        )
        await db.execute(
            """
            INSERT INTO audit_entries
                (actor_id, actor_name, division_id, change_type,
                 old_value, new_value, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor_id,
                actor_name,
                row["division_id"],
                "round.status",
                row["status"],
                RoundStatus.FINAL.value,
                stamp,
            ),
        )

    if rows:
        await rounds_made_final.call(db, [row["id"] for row in rows])

    # Each of those rounds may have been the last thing its division was waiting on, and a
    # division finishing is what lets `/season complete` run at all.
    for division_id in sorted({row["division_id"] for row in rows}):
        await refresh_division_status_on(db, division_id)

    return [
        {
            "division": row["division"],
            "round_number": row["round_number"],
            "track_name": row["track_name"],
            "status": row["status"],
        }
        for row in rows
    ]


async def live_season(db_path: str) -> Season | None:
    """The live (SETUP or ACTIVE) season of the league database at *db_path*, or None.

    The query behind :meth:`SeasonService.get_setup_or_active_season`, for a reader that has the
    database and no service: a queued change's check reads the season through it.
    """
    async with get_connection(db_path) as db:
        return await live_season_on(db)


async def live_season_on(db: aiosqlite.Connection) -> Season | None:
    """`live_season` on *db*, a connection the caller holds: what a save has written there is
    read back with the rest. For a module planning inside its own save that needs the live
    season's stage (architecture.md, "The database"). It writes nothing and commits nothing."""
    cursor = await db.execute(
        "SELECT id, start_date, status, season_number, stage FROM seasons "
        "WHERE status IN ('SETUP', 'ACTIVE') "
        "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_season(row)


async def season_exists(db_path: str, season_id: int) -> bool:
    """Whether the season *season_id* can be read from the league database at *db_path*, for a
    reader that checks it before writing anything."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,))
        return await cursor.fetchone() is not None


async def division_first_round(db_path: str, division_id: int) -> Round | None:
    """*division_id*'s first round by round number, or None where it has none.

    For a module reading core's tables only through core's services (architecture.md, "How
    modules and core fit together"), where the caller has the database path and no
    `SeasonService` of its own: results' and attendance's opening posts are drawn against this
    round, since every cell of the grid both sheets draw is keyed by a round and this one's is
    empty, which is exactly what an opening posting should show.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
            "status FROM rounds "
            "WHERE division_id = ? ORDER BY round_number LIMIT 1",
            (division_id,),
        )
        row = await cursor.fetchone()
    return _row_to_round(row) if row else None


async def division_rounds_on(db: aiosqlite.Connection, division_id: int) -> list[int]:
    """*division_id*'s round ids, newest round number first, read on *db*.

    For a hook subscriber handed the completion's own connection (architecture.md, "The
    database"): a query joining another owner's table is split at the table line, each half
    asked of its owner on the connection the asker was handed, so both halves read inside the
    same save. Results' `final_round_on` takes this half — core's own `rounds` — and asks its
    own `session_results` for the first of these ids that the division actually raced, rather
    than joining the two tables in one query.
    """
    cursor = await db.execute(
        "SELECT id FROM rounds WHERE division_id = ? ORDER BY round_number DESC",
        (division_id,),
    )
    return [int(row["id"]) for row in await cursor.fetchall()]


async def season_divisions_on(db: aiosqlite.Connection, season_id: int) -> list[Division]:
    """*season_id*'s divisions in tier order, read on *db*; empty for none.

    `SeasonService.get_divisions_on` as a function, for a module reading core's tables only
    through core's code (architecture.md, "The database") that has the connection of its own
    save and no service. It writes nothing and commits nothing.
    """
    cursor = await db.execute(
        "SELECT id, season_id, name, mention_role_id, status, tier, "
        "lineup_channel_id, calendar_channel_id, lineup_message_id, "
        "calendar_message_id "
        "FROM divisions WHERE season_id = ? ORDER BY tier",
        (season_id,),
    )
    return [_row_to_division(r) for r in await cursor.fetchall()]


async def division_rounds_in_order_on(db: aiosqlite.Connection, division_id: int) -> list[Round]:
    """*division_id*'s rounds in round order, with their status, read on *db*; empty for none.

    `SeasonService.get_division_rounds_on` as a function, for a module reading core's tables only
    through core's services (architecture.md, "The database") that has the connection of its own
    save and no service: what it asks of the rounds (which have results, which are cancelled) it
    reads off the rounds this returns, rather than join `rounds` in a query of its own. It writes
    nothing and commits nothing.
    """
    cursor = await db.execute(
        "SELECT id, division_id, round_number, format, track_name, scheduled_at, status "
        "FROM rounds WHERE division_id = ? ORDER BY round_number",
        (division_id,),
    )
    return [_row_to_round(r) for r in await cursor.fetchall()]


async def rounds_from_on(
    db: aiosqlite.Connection, division_id: int, from_round_id: int
) -> list[Round]:
    """*division_id*'s rounds from *from_round_id* forward, in round order, the round named
    included, read on *db*; empty where *from_round_id* is no round.

    For a module reading core's tables only through core's services (architecture.md, "The
    database"): what it asks of the rounds after one (which are awaiting appeals, which are
    cancelled) it reads off the status each carries, rather than join `rounds` in a query of its
    own. It writes nothing and commits nothing.
    """
    cursor = await db.execute(
        "SELECT id, division_id, round_number, format, track_name, scheduled_at, status "
        "FROM rounds WHERE division_id = ? "
        "AND round_number >= (SELECT round_number FROM rounds WHERE id = ?) "
        "ORDER BY round_number",
        (division_id, from_round_id),
    )
    return [_row_to_round(r) for r in await cursor.fetchall()]


async def division_on(db: aiosqlite.Connection, division_id: int) -> Division | None:
    """The division *division_id*, or None where there is none, read on *db*.

    For a module reading core's tables only through core's services (architecture.md, "The
    database"), handed the connection of its own save or read: what it asks of `divisions` (its
    season, its name) comes from here rather than from a query of its own. It writes nothing and
    commits nothing.
    """
    cursor = await db.execute(
        "SELECT id, season_id, name, mention_role_id, status, tier, "
        "lineup_channel_id, calendar_channel_id, lineup_message_id, "
        "calendar_message_id "
        "FROM divisions WHERE id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    return _row_to_division(row) if row is not None else None


async def round_by_id(db_path: str, division_id: int, round_id: int) -> Round | None:
    """*round_id* of *division_id*, or None where it is not (any longer) that division's.

    For a module reading core's tables only through core's services (architecture.md, "How
    modules and core fit together"): results' and attendance's final posts are drawn against the
    round their own read of the final round hands back, and read it here rather than `rounds`
    itself.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
            "status FROM rounds "
            "WHERE id = ? AND division_id = ?",
            (round_id, division_id),
        )
        row = await cursor.fetchone()
    return _row_to_round(row) if row else None


async def round_called(db_path: str, round_id: int) -> str:
    """How a league reads *round_id*: "round 3 in **Pro**", or "a round no longer there".

    For the refusal of a Discard, which names the round whose job would be dropped. It reads the
    round at the press, and a round deleted meanwhile is named so rather than failing the refusal.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.round_number, d.name FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return "a round no longer there"
    return f"round {row['round_number']} in **{row['name']}**"


@dataclass(frozen=True)
class RoundInDivision:
    """A round with the division it belongs to, as core's own read of both gives it."""

    round_id: int
    division_id: int
    division_name: str
    round_number: int
    status: str
    scheduled_at: datetime


async def round_in_division(db_path: str, round_id: int) -> RoundInDivision | None:
    """*round_id* with its division, or None where the round is no longer there.

    For a module reading core's tables only through core's services (architecture.md, "How
    modules and core fit together"), where the caller has the database path and no `SeasonService`
    of its own: results' restart recovery of a submission channel finds its own row for the round
    and asks core what the round is.
    """
    async with get_connection(db_path) as db:
        facts = await round_facts_on(db, round_id)
    if facts is None:
        return None
    return RoundInDivision(
        round_id=facts.round_id, division_id=facts.division_id,
        division_name=facts.division_name, round_number=facts.round_number,
        status=facts.status.value, scheduled_at=facts.scheduled_at,
    )


@dataclass(frozen=True)
class RoundFacts:
    """A round with its division and season, as core's own read of the three gives it."""

    round_id: int
    round_number: int
    status: RoundStatus
    format: RoundFormat
    track_name: str | None
    scheduled_at: datetime
    not_held: bool
    division_id: int
    division_name: str
    division_tier: int
    division_status: DivisionStatus
    season_id: int
    season_number: int
    season_status: SeasonStatus


async def round_facts_on(db: aiosqlite.Connection, round_id: int) -> RoundFacts | None:
    """*round_id*'s facts with its division's and its season's, or None where the round is no
    longer there, read on *db*.

    For a module reading core's tables only through core's services (architecture.md, "The
    database"), handed the connection of its own save: a query of its own table that joined the
    round, its division and its season is split at the table line, and this is the core half, read
    inside the same save, so a round that save has just changed is read as changed. It writes
    nothing and commits nothing.
    """
    cursor = await db.execute(
        "SELECT r.id, r.round_number, r.status, r.format, r.track_name, r.scheduled_at, "
        "r.not_held, d.id AS division_id, d.name AS division_name, d.tier AS division_tier, d.status AS division_status, "
        "s.id AS season_id, s.season_number, s.status AS season_status "
        "FROM rounds r JOIN divisions d ON d.id = r.division_id "
        "JOIN seasons s ON s.id = d.season_id WHERE r.id = ?",
        (round_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return RoundFacts(
        round_id=int(row["id"]), round_number=int(row["round_number"]),
        status=RoundStatus(row["status"]), format=RoundFormat(row["format"]),
        track_name=row["track_name"], scheduled_at=read_scheduled_at(row["scheduled_at"]),
        not_held=bool(row["not_held"]), division_id=int(row["division_id"]),
        division_name=str(row["division_name"]), division_tier=int(row["division_tier"]),
        division_status=DivisionStatus(row["division_status"]), season_id=int(row["season_id"]), season_number=int(row["season_number"]),
        season_status=SeasonStatus(row["season_status"]),
    )


async def final_round_ids_among_on(
    db: aiosqlite.Connection, round_ids: Iterable[int]
) -> set[int]:
    """Those of *round_ids* whose round is FINAL, read on *db*; the empty set for none.

    The core half of a module's query that joined its own rows to `rounds` to keep those of final
    rounds: the module reads its own table for the round ids it holds, asks this of the ids, and
    keeps what comes back, on the connection of the save it is in. It writes nothing and commits
    nothing.
    """
    ids = sorted(set(round_ids))
    if not ids:
        return set()
    marks = ", ".join("?" for _ in ids)
    cursor = await db.execute(
        f"SELECT id FROM rounds WHERE status = ? AND id IN ({marks})",  # noqa: S608
        (RoundStatus.FINAL.value, *ids),
    )
    return {int(row["id"]) for row in await cursor.fetchall()}


async def rounds_still_to_run_by(db_path: str, now: datetime) -> list[Round]:
    """Each round of the ACTIVE season still to be run (NOT_RUN) whose start is at or before
    *now*, earliest first.

    For a module catching up what came due while the bot was down: results' round starts. *now* is
    handed in by the caller, the change queue's clock at start-up, so that "passed" is the bot's
    moment and never the host's. The stored moments are ISO 8601 text in UTC written with their
    offset (`stored_scheduled_at`), so text order is time order and the comparison is made in SQL.
    A cancelled round is no longer to be run, and a round of a division that was cancelled is
    cancelled with it.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.division_id, r.round_number, r.format, r.track_name, r.scheduled_at, "
            "r.status FROM rounds r JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            "WHERE s.status = 'ACTIVE' AND d.status != 'CANCELLED' AND r.status = ? "
            "AND r.scheduled_at <= ? "
            "ORDER BY r.scheduled_at, r.id",
            (RoundStatus.NOT_RUN.value, stored_scheduled_at(now)),
        )
        return [_row_to_round(row) for row in await cursor.fetchall()]


# ------------------------------------------------------------------
# Row mappers
# ------------------------------------------------------------------

def _row_to_season(row: aiosqlite.Row) -> Season:
    return Season(
        id=row["id"],
        start_date=date.fromisoformat(row["start_date"]),
        status=SeasonStatus(row["status"]),
        season_number=row["season_number"] if "season_number" in row.keys() else 0,
        game_edition=row["game_edition"] if "game_edition" in row.keys() else 0,
        stage=(
            SeasonStage(row["stage"])
            if "stage" in row.keys() and row["stage"] is not None
            else None
        ),
    )


def _row_to_division(row: aiosqlite.Row) -> Division:
    keys = row.keys()
    return Division(
        id=row["id"],
        season_id=row["season_id"],
        name=row["name"],
        mention_role_id=row["mention_role_id"],
        status=row["status"],
        tier=row["tier"] if "tier" in keys else 0,
        lineup_channel_id=row["lineup_channel_id"] if "lineup_channel_id" in keys else None,
        calendar_channel_id=row["calendar_channel_id"] if "calendar_channel_id" in keys else None,
        lineup_message_id=row["lineup_message_id"] if "lineup_message_id" in keys else None,
        # Guarded like every other optional column.
        calendar_message_id=(
            row["calendar_message_id"] if "calendar_message_id" in keys else None
        ),
    )


def _row_to_round(row: aiosqlite.Row) -> Round:
    return Round(
        id=row["id"],
        division_id=row["division_id"],
        round_number=row["round_number"],
        format=RoundFormat(row["format"]),
        track_name=row["track_name"],
        scheduled_at=read_scheduled_at(row["scheduled_at"]),
        status=row["status"],
    )


def _row_to_session(row: aiosqlite.Row) -> Session:
    return Session(
        id=row["id"],
        round_id=row["round_id"],
        session_type=SessionType(row["session_type"]),
    )


async def complete_season_on(db: aiosqlite.Connection, season_id: int) -> bool:
    """Archive *season_id* in place as COMPLETED on *db*, committing nothing.

    Guarded on ``ACTIVE``: a season already completed or cancelled is left, and False is given.
    A season's completion writes this last in the save that records its end, so that the
    archive never stands without the history and the driver pass written beside it.
    """
    cursor = await db.execute(
        "UPDATE seasons SET status = 'COMPLETED' WHERE id = ? AND status = 'ACTIVE'",
        (season_id,),
    )
    return cursor.rowcount > 0


async def outstanding_rounds_on(db: aiosqlite.Connection, season_id: int) -> list[dict]:
    """Division, round number and track of every round of *season_id* still to be finalised.

    Cancelled rounds and the rounds of a cancelled division are excluded: neither is waiting on
    anybody.
    """
    cursor = await db.execute(
        f"""
        SELECT d.name AS division, r.round_number, r.track_name
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        WHERE d.season_id = ?
          AND r.status NOT IN ({_TERMINAL_SQL})
          AND d.status != 'CANCELLED'
        ORDER BY d.name, r.round_number
        """,
        (season_id,),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def completion_refusal_on(db: aiosqlite.Connection, season_id: int) -> str | None:
    """Why *season_id* cannot be completed yet, in the words the league is told, else None.

    Read on *db*, so that a completion's save judges the divisions it has just refreshed.

    - Rounds still to be finalised are named, the first twenty.
    - Otherwise a division neither finished nor cancelled is named: a refusal naming nothing
      would leave the league stranded, with nothing to act on. A division still ``ACTIVE`` with
      nothing outstanding refuses nothing, the completion's refresh finishing it.
    """
    pending = await outstanding_rounds_on(db, season_id)
    if pending:
        lines = "\n".join(
            f"• {r['division']} — Round {r['round_number']}"
            + (f" ({r['track_name']})" if r.get("track_name") else "")
            for r in pending[:20]
        )
        return (
            "❌ Cannot complete season — the following rounds are not yet "
            f"finalised:\n{lines}"
        )
    cursor = await db.execute(
        "SELECT name FROM divisions WHERE season_id = ? "
        "AND status NOT IN ('FINISHED', 'CANCELLED', 'ACTIVE') ORDER BY tier, id",
        (season_id,),
    )
    unfinished = ", ".join(f"**{row['name']}**" for row in await cursor.fetchall())
    if not unfinished:
        return None
    return (
        "❌ Cannot complete season — no round is outstanding, but these "
        f"divisions have not finished: {unfinished}. Cancel a division that will "
        "never run, or report this."
    )


async def cancel_season_on(db: aiosqlite.Connection, season_id: int) -> bool:
    """Mark *season_id* CANCELLED on *db*, committing nothing, and nothing else of it.

    Guarded on ``ACTIVE``: a season already completed or cancelled is left, and False is given.
    A season's cancellation writes this after :func:`cancel_season_divisions_on`, never before:
    ``cancel_round_on`` refuses a round whose season is already COMPLETED or CANCELLED, so a
    season row flipped first would lock a write beneath it out of its own children.
    """
    cursor = await db.execute(
        "UPDATE seasons SET status = 'CANCELLED' WHERE id = ? AND status = 'ACTIVE'",
        (season_id,),
    )
    return cursor.rowcount > 0


async def cancel_season_divisions_on(
    db: aiosqlite.Connection,
    season_id: int,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
) -> dict[int, list[int]]:
    """Cancel every division of *season_id* not yet cancelled, with its rounds, on *db*.

    Each goes through :func:`cancel_division_on`, so the rounds called off and the real status
    each was called off from are audited as a division's cancellation audits them. Committing
    nothing, and leaving the season's own row alone: that is :func:`cancel_season_on`'s, written
    last. Gives each division cancelled by this call, in tier order, with the ids of its rounds
    called off.
    """
    cursor = await db.execute(
        "SELECT id FROM divisions WHERE season_id = ? AND status != 'CANCELLED' ORDER BY tier",
        (season_id,),
    )
    division_ids = [row["id"] for row in await cursor.fetchall()]
    return {
        division_id: await cancel_division_on(
            db, division_id, actor_id=actor_id, actor_name=actor_name, now=now
        )
        for division_id in division_ids
    }


async def close_raced_rounds_on(
    db: aiosqlite.Connection,
    season_id: int,
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
    rounds_made_final: AwaitedHook,
) -> list[int]:
    """Close as FINAL every round of *season_id* raced but whose verdicts are open, on *db*.

    A round at *awaiting report verdicts* or *awaiting appeal verdicts* has its results
    entered, so ``ROUND_CANCELLABLE`` excludes it and a cancellation leaves it exactly where it
    is: "a cancellation shall never discard a result". But nothing else will move it either,
    the verdict commands it waits on being refused once the season is cancelled.

    The former-driver flag is set at the FINAL transition. A driver whose only round is
    one of these is flagless when the driver pass runs, and the pass deletes a flagless profile
    at Not Signed Up, which would discard the results this rule forbids discarding. Closing the
    rounds first marks their drivers, and the pass keeps them: the rounds closed are handed,
    once, with *rounds_made_final*, the hook a round made final calls, on *db* and in the same
    save, and results' part of it marks their drivers (a round with no results marks nobody).
    The rounds the cancellation calls off are never handed to it.

    Committing nothing. Gives the ids closed, for the caller to report.
    """
    cursor = await db.execute(
        f"""
        SELECT r.id, r.status, d.id AS division_id
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        WHERE d.season_id = ?
          AND r.status IN ({_RACED_AWAITING_VERDICTS_SQL})
        ORDER BY r.id
        """,
        (season_id,),
    )
    rows = [dict(r) for r in await cursor.fetchall()]

    for row in rows:
        await db.execute(
            "UPDATE rounds SET status = ? WHERE id = ?",
            (RoundStatus.FINAL.value, row["id"]),
        )
        await db.execute(
            """
            INSERT INTO audit_entries
                (actor_id, actor_name, division_id, change_type,
                 old_value, new_value, timestamp)
            VALUES (?, ?, ?, 'round.status', ?, ?, ?)
            """,
            (
                actor_id,
                actor_name,
                row["division_id"],
                row["status"],
                RoundStatus.FINAL.value,
                now.isoformat(),
            ),
        )
    if rows:
        await rounds_made_final.call(db, [row["id"] for row in rows])
    return [row["id"] for row in rows]


async def discard_uncommitted_placements_on(db: aiosqlite.Connection, season_id: int) -> int:
    """Delete every placement of *season_id* not yet committed, freeing its seat, on *db*.

    Cancelling a season discards them: the drivers placed stood outside the championship and earn no
    history of it. Committing nothing. Gives how many were discarded.
    """
    await db.execute(
        "UPDATE team_seats SET driver_profile_id = NULL WHERE id IN ("
        "  SELECT team_seat_id FROM driver_season_assignments "
        "  WHERE season_id = ? AND committed = 0 AND team_seat_id IS NOT NULL)",
        (season_id,),
    )
    cursor = await db.execute(
        "DELETE FROM driver_season_assignments WHERE season_id = ? AND committed = 0",
        (season_id,),
    )
    return cursor.rowcount
