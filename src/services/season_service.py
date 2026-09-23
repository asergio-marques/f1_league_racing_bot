"""SeasonService — season, division, round, and session management."""

from __future__ import annotations

import logging
from datetime import date, datetime

import aiosqlite

from db.database import get_connection, inserted_id, sole_row
from models.division import Division
from models.round import (
    ROUND_AWAITING_RESULTS_MODULE,
    ROUND_CANCELLABLE,
    ROUND_RACED_AWAITING_VERDICTS,
    ROUND_TERMINAL,
    Round,
    RoundFormat,
    RoundStatus,
)
from models.season import (
    ALLOWED_STAGE_TRANSITIONS,
    InvalidStageTransition,
    Season,
    SeasonStage,
    SeasonStatus,
    status_of_stage,
)
from models.session import Session, SessionType, SESSIONS_BY_FORMAT
from utils.input_validator import NAME
from utils.league_bot import LeagueBot

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
    """Why *name* cannot name a division, or None where it can (#362).

    A division's name heads every posting and every graphic of the division, so it is held to
    the rules every name a league types is held to: no group mention, no emoji and no markup.
    Checked by each command that sets one, before its own test for a duplicate.
    """
    return NAME.check("division name", name).refusal


class SeasonImmutableError(Exception):
    """Raised when a mutation is attempted on a COMPLETED (archived) season."""


class SeasonService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Season
    # ------------------------------------------------------------------

    async def create_season(self, start_date: date | None = None) -> Season:
        """Insert a new SETUP season and return it.

        Raises ``sqlite3.IntegrityError`` where the league already holds a live season:
        the schema permits one SETUP-or-ACTIVE row, and this method applies
        none of the checks `/season setup` makes before its own writes. Nothing in
        ``src/`` calls it — season setup writes its season through
        :meth:`sync_pending_config` — so any new caller wants those checks first.
        """
        if start_date is None:
            start_date = date.today()
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO seasons (start_date, status) VALUES (?, ?)",
                (start_date.isoformat(), SeasonStatus.SETUP.value),
            )
            await db.commit()
            season_id = inserted_id(cursor)

        return Season(
            id=season_id,
            start_date=start_date,
            status=SeasonStatus.SETUP,
        )

    async def get_confirmed_season(self) -> Season | None:
        """Return the ACTIVE season, or None.

        ACTIVE is every stage from the first confirmation of placements to the season's
        completion or cancellation: the three ongoing stages and Pending completion. This was
        ``get_active_season``, renamed once the specification came to call a season *active*
        from the moment it is set up (issue #220). Read ``Season.stage`` where the stage
        within ACTIVE matters.
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

        A server holds at most one, enforced by the partial unique index migration 049
        builds, so the two states cannot both be present and there is nothing to choose
        between. The ordering is stated anyway: this was a bare ``LIMIT 1`` over both
        states, which returned an uncontracted row wherever the invariant had been
        broken, and matching :meth:`get_previewable_season` keeps every reader of a
        league's current season agreeing on which one that is.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, status, season_number, stage FROM seasons "
                "WHERE status IN ('SETUP', 'ACTIVE') "
                "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_season(row)

    async def get_previewable_season(self) -> Season | None:
        """Return the season an `/images test` preview draws, or None.

        The server's one live season — SETUP or ACTIVE. A COMPLETED or CANCELLED season is
        never previewable: a preview is a check on what the league is running or about to
        run, and a server keeps its whole archive besides.

        A server holds at most one live season (migration 049), so the ACTIVE-before-SETUP
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

    async def get_previous_season_number(self) -> int:
        """The highest season number the league has already committed, or 0.

        "Committed" means the number has been issued and cannot be re-used — every status
        but SETUP. A season still in setup holds a provisional number and is excluded, so
        that a league drafting its next season does not push the count forward twice.

        A new season is numbered one above it. The highest number, not a count of seasons:
        a count agrees only while the numbers run from 1 without a gap, and falls behind
        the highest number the moment one goes missing, handing out a number already in
        use (issue #153).
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT MAX(season_number) AS highest FROM seasons "
                "WHERE status != 'SETUP'",
            )
            row = await cursor.fetchone()
        if row is None or row["highest"] is None:
            return 0
        return int(row["highest"])

    async def get_setup_season(self) -> Season | None:
        """Return the SETUP season, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, status, season_number, stage FROM seasons "
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

    async def complete_season(self, season_id: int) -> None:
        """Transition a season to COMPLETED (archive it in-place)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = 'COMPLETED' WHERE id = ?",
                (season_id,),
            )
            await db.commit()

    async def cancel_season(self, season_id: int) -> None:
        """Transition a season to CANCELLED (immutable, all data preserved)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = 'CANCELLED' WHERE id = ?",
                (season_id,),
            )
            await db.commit()

    async def get_stage(self, season_id: int) -> SeasonStage | None:
        """The lifecycle stage of *season_id*, or None where no such season exists."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            row = await cursor.fetchone()
        if row is None or row["stage"] is None:
            return None
        return SeasonStage(row["stage"])

    async def set_stage(self, season_id: int, stage: SeasonStage) -> None:
        """Move *season_id* to *stage*, carrying its coarse status with it.

        The move must be one ``ALLOWED_STAGE_TRANSITIONS`` permits from the stage the season
        stands in; anything else raises :class:`InvalidStageTransition` and writes nothing.
        The write is conditioned on the stage that was read, so two callers racing to move
        the same season cannot both succeed from a stage the first of them has already left.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT stage FROM seasons WHERE id = ?", (season_id,))
            row = await cursor.fetchone()
            if row is None or row["stage"] is None:
                raise InvalidStageTransition(f"season {season_id} does not exist")
            current = SeasonStage(row["stage"])
            if stage not in ALLOWED_STAGE_TRANSITIONS[current]:
                raise InvalidStageTransition(
                    f"season {season_id} cannot move from {current.value} to {stage.value}"
                )
            cursor = await db.execute(
                "UPDATE seasons SET status = ?, stage = ? WHERE id = ? AND stage = ?",
                (status_of_stage(stage).value, stage.value, season_id, current.value),
            )
            await db.commit()
            if cursor.rowcount == 0:
                raise InvalidStageTransition(
                    f"season {season_id} left {current.value} before it could move to "
                    f"{stage.value}"
                )

    async def assert_season_mutable(self, season: "Season") -> None:
        """Raise SeasonImmutableError if *season* is COMPLETED or CANCELLED."""
        if season.status in (SeasonStatus.COMPLETED, SeasonStatus.CANCELLED):
            raise SeasonImmutableError(
                f"Season {season.season_number} is archived and cannot be modified."
            )

    async def sync_pending_config(
        self,
        start_date: date,
        season_id: int,
        divisions: list[dict],
        game_edition: int = 0,
        initial_stage: SeasonStage | None = None,
    ) -> tuple[int, int, list[int]]:
        """Bring the SETUP season in the DB into line with the PendingConfig, in place.

        Returns ``(season_id, season_number, unseeded_division_ids)``: every division of the
        season that holds no team, which the caller then seeds, and no other. That is each
        division this call created, and any an earlier call created whose seeding never
        landed — seeding commits separately, after this, so a bot stopped between the two
        leaves a division with no teams. The rebuild healed that on the next command by
        re-seeding everything; this heals it by asking which divisions still need it.

        **Nothing is torn down** (issue #147). This replaced a snapshot that deleted every
        division, team, seat, round and driver assignment beneath the season and re-inserted
        them with new ids, carrying across by hand whatever it knew to save. Since issue #220
        divisions exist only in Placements, so that rebuild ran while the league was seating
        its grid: every `/round add` unseated every driver and seated them again from memory
        in a separate commit, and anything it did not know to save — a new setting, or a
        weather channel set straight to the DB after the PendingConfig was loaded — was
        silently destroyed. What this writes instead is only what the config holds and the DB
        does not:

        - **the season row**, when *season_id* is 0 and there is none yet. *initial_stage* is
          the stage it begins in; left unset it takes the default migration 057 gives a SETUP
          row. An existing season row is not written — no setup command changes its start
          date or game edition once it exists;
        - **a division named in the config and absent from the DB**, inserted. A division
          already in the DB is **never written**: its name, role, tier and channels are each
          owned by a command that writes them directly and reloads the PendingConfig, which is
          therefore never newer than the DB for them. One in the DB and missing from the
          config is left alone and logged, not deleted — removal has its own command;
        - **rounds**, per division, matched on (format, track, scheduled time). A round the DB
          holds and the config does not is deleted, one the config holds and the DB does not
          is inserted, and a round number is updated only where it moved. A division whose
          rounds already match is not written to. A round of a season in setup has nothing
          hanging off it — sessions, forecasts and results begin at approval — so deleting and
          inserting one to amend it loses nothing.

        Everything lands in one transaction and one commit. A setting added to a division
        or a season anywhere in the bot needs nothing here to survive a setup command.

        The alternative the issue first proposed — a registry of what to carry across, and a
        rebuild of only the division that changed — was set aside because the rebuild still
        passed every driver of that division through memory on every command.

        Raises ``ValueError`` where *season_id* names no season, or one no longer in SETUP.
        Sessions are not created here; approval creates them.
        """
        async with get_connection(self._db_path) as db:
            if season_id == 0:
                season_number = await self.get_previous_season_number() + 1
                cursor = await db.execute(
                    "INSERT INTO seasons "
                    "(start_date, status, season_number, game_edition, stage) "
                    "VALUES (?, 'SETUP', ?, ?, ?)",
                    (
                        start_date.isoformat(),
                        season_number,
                        game_edition,
                        initial_stage.value if initial_stage is not None else None,
                    ),
                )
                season_id = inserted_id(cursor)
            else:
                cursor = await db.execute(
                    "SELECT status, season_number FROM seasons WHERE id = ?", (season_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ValueError(f"season {season_id} does not exist")
                if row["status"] != SeasonStatus.SETUP.value:
                    raise ValueError(
                        f"season {season_id} is {row['status']}, not in setup; "
                        "its configuration can no longer be synced"
                    )
                season_number = row["season_number"]

            cursor = await db.execute(
                "SELECT id, name FROM divisions WHERE season_id = ?", (season_id,)
            )
            division_ids = {r["name"]: r["id"] for r in await cursor.fetchall()}

            wanted = {d["name"] for d in divisions}
            for name in sorted(set(division_ids) - wanted):
                log.warning(
                    "season %s: division %r is in the DB but not the pending config; "
                    "left as it stands",
                    season_id, name,
                )

            for div_data in divisions:
                div_id = division_ids.get(div_data["name"])
                if div_id is None:
                    cursor = await db.execute(
                        "INSERT INTO divisions "
                        "(season_id, name, mention_role_id, forecast_channel_id, tier) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            season_id,
                            div_data["name"],
                            div_data["role_id"],
                            div_data["channel_id"],
                            div_data.get("tier", 0),
                        ),
                    )
                    div_id = inserted_id(cursor)
                    division_ids[div_data["name"]] = div_id
                await _sync_division_rounds(db, div_id, div_data["rounds"])

            await db.commit()

            cursor = await db.execute(
                "SELECT d.id FROM divisions d WHERE d.season_id = ? AND NOT EXISTS "
                "(SELECT 1 FROM team_instances ti WHERE ti.division_id = d.id) "
                "ORDER BY d.id",
                (season_id,),
            )
            unseeded = [r["id"] for r in await cursor.fetchall()]

        return season_id, season_number, unseeded

    async def load_all_setup_seasons(self) -> list[dict]:
        """Return raw data for every SETUP-status season to rebuild PendingConfig on startup."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, start_date, season_number, game_edition "
                "FROM seasons WHERE status = 'SETUP'"
            )
            season_rows = await cursor.fetchall()

            result: list[dict] = []
            for s_row in season_rows:
                season_id = s_row["id"]

                cursor = await db.execute(
                    "SELECT id, name, mention_role_id, forecast_channel_id, tier "
                    "FROM divisions WHERE season_id = ?",
                    (season_id,),
                )
                div_rows = await cursor.fetchall()

                divisions: list[dict] = []
                for d_row in div_rows:
                    cursor2 = await db.execute(
                        "SELECT round_number, format, track_name, scheduled_at "
                        "FROM rounds WHERE division_id = ? ORDER BY round_number",
                        (d_row["id"],),
                    )
                    round_rows = await cursor2.fetchall()
                    rounds = [
                        {
                            "round_number": r["round_number"],
                            "format": RoundFormat(r["format"]),
                            "track_name": r["track_name"],
                            "scheduled_at": datetime.fromisoformat(r["scheduled_at"]),
                        }
                        for r in round_rows
                    ]
                    divisions.append({
                        "name": d_row["name"],
                        "role_id": d_row["mention_role_id"],
                        "channel_id": d_row["forecast_channel_id"],
                        "tier": d_row["tier"] if "tier" in d_row.keys() else 0,
                        "rounds": rounds,
                    })

                result.append({
                    "season_id": season_id,
                    "start_date": date.fromisoformat(s_row["start_date"]),
                    "season_number": s_row["season_number"] if "season_number" in s_row.keys() else 0,
                    "game_edition": s_row["game_edition"] if "game_edition" in s_row.keys() else 0,
                    "divisions": divisions,
                })

        return result

    async def validate_division_tiers(self, season_id: int) -> None:
        """Validate division tiers form a gapless sequence 1..N.

        Raises ValueError with a diagnostic message if any tier is missing.
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
            raise ValueError(
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
        return datetime.fromisoformat(row[0])

    async def all_phases_complete(self) -> bool:
        """True if every non-MYSTERY, non-CANCELLED round in the active season has all 3 phases done."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons   s ON s.id = d.season_id
                WHERE s.status    = 'ACTIVE'
                  AND r.format   != 'MYSTERY'
                  AND r.status   != 'CANCELLED'
                  AND d.status   != 'CANCELLED'
                  AND (r.phase1_done = 0 OR r.phase2_done = 0 OR r.phase3_done = 0)
                """,
            )
            row = await cursor.fetchone()
        return row is not None and row[0] == 0

    async def advance_to_pending_completion(self, season_id: int) -> bool:
        """Move *season_id* to Pending completion where every division is done (issue #220)."""
        from services.season_lifecycle_service import advance_to_pending_completion

        return await advance_to_pending_completion(self._db_path, season_id)

    async def wind_down_ongoing(self, bot: LeagueBot) -> bool:
        """Take a season whose every division is done out of the ongoing stages (issue #220).

        Its signup window closed, its pending placements turned down, and on to Pending
        completion. A wrapper, so that a command reaches it through the service it already
        holds; see :func:`services.season_lifecycle_service.wind_down_ongoing`.
        """
        from services.season_lifecycle_service import wind_down_ongoing

        return await wind_down_ongoing(bot)

    async def refresh_division_status(self, division_id: int) -> bool:
        """Move a division ACTIVE -> FINISHED once none of its rounds is outstanding.

        A round is outstanding until it reaches one of its two terminal states, FINAL or
        CANCELLED. Every other state is a round still waiting on somebody: for its date, for its
        results, for report verdicts, or for appeal verdicts.

        Division status is stored rather than derived, so that cancelling a division can record
        the fact and `/season cancel` can tell the running divisions apart from the called-off
        ones. Stored state can drift from the rounds it summarises, so this is called from every
        place a round's outcome settles — the appeals approval in result_submission_service and
        `cancel_round` below — and again from the `/season complete` gate, which cannot afford to
        strand a league on a stale row a second time (issue #154).

        The `status = 'ACTIVE'` guard is what makes it safe to call anywhere: a division still in
        SETUP has not started, and a CANCELLED one was called off deliberately. Neither is a
        division that has *finished*, and neither is ever touched here.

        Returns True if this call is what moved it.
        """
        async with get_connection(self._db_path) as db:
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
            await db.commit()
            moved = cursor.rowcount > 0
            cursor = await db.execute(
                "SELECT season_id FROM divisions WHERE id = ?", (division_id,)
            )
            season_row = await cursor.fetchone()

        # A division finishing may be the last one its season waited on (issue #220).
        if moved and season_row is not None:
            from services.season_lifecycle_service import advance_to_pending_completion

            await advance_to_pending_completion(self._db_path, season_row["season_id"])
        return moved

    async def end_rounds_awaiting_results(
        self,
        actor_id: int,
        actor_name: str,
    ) -> list[dict]:
        """Close every round of the active season that only the results module could move.

        Called when the results module is switched off part-way through a season. The three
        states in ``ROUND_AWAITING_RESULTS_MODULE`` each wait on a results command, so with the
        module gone nothing will ever move them: the division never finishes, `/season complete`
        refuses for the rest of the season, and — because enabling is refused while a season is
        active — the league cannot undo it either. That was issue #167, and it left `/season
        cancel` as the only way out.

        The rounds are closed as FINAL rather than CANCELLED. They were raced; it is their
        scoring that has been abandoned, and a cancelled round would tell the attendance module
        that nobody was expected to turn up.

        A NOT_RUN round is left where it is. It waits on the clock rather than on results, and
        ``run_result_submission_job`` closes it as FINAL at its own moment with the module off.

        Returns one dict per round closed — ``division``, ``round_number``, ``track_name`` and
        the ``status`` it was taken from — so the caller can report what it did.
        """
        from datetime import timezone

        now = datetime.now(timezone.utc).isoformat()

        async with get_connection(self._db_path) as db:
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

            from services.result_submission_service import (
                recompute_former_drivers_for_round,
            )

            for row in rows:
                await db.execute(
                    "UPDATE rounds SET status = ? WHERE id = ?",
                    (RoundStatus.FINAL.value, row["id"]),
                )
                # These rounds were raced and their results submitted; it is only the scoring
                # that has been abandoned. Closing them as FINAL is what makes those results
                # final, so it is here that their drivers become former drivers (#216) — the
                # first pass's own finaliser is a results command and will never run for them.
                #
                # **Usually this finds nothing**, and that is correct rather than wasteful:
                # disabling the module purges the season's results before reaching here, and a
                # driver whose results have been erased has raced nothing the bot still knows
                # of. It marks where the purge failed — `_apply_results_disable` catches that
                # and closes the rounds regardless, leaving the results standing — and where a
                # league disables between seasons with an older season's results intact.
                await recompute_former_drivers_for_round(db, row["id"])
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
                        now,
                    ),
                )
            await db.commit()

        # Each of those rounds may have been the last thing its division was waiting on, and a
        # division finishing is what lets `/season complete` run at all (issue #154).
        for division_id in sorted({row["division_id"] for row in rows}):
            await self.refresh_division_status(division_id)

        return [
            {
                "division": row["division"],
                "round_number": row["round_number"],
                "track_name": row["track_name"],
                "status": row["status"],
            }
            for row in rows
        ]

    async def close_raced_rounds_for_cancellation(
        self, season_id: int, actor_id: int, actor_name: str
    ) -> list[int]:
        """Close as FINAL every round of a season that was raced but whose verdicts are open.

        Called by `/season cancel`, **before** the driver pass, and by nothing else.

        A round at *awaiting report verdicts* or *awaiting appeal verdicts* has its results
        entered, so `ROUND_CANCELLABLE` excludes it and the cancellation cascade leaves it
        exactly where it is — "a cancellation shall never discard a result", and "a round
        further along shall keep its place and its results". But nothing else will move it
        either: the verdict commands it waits on are refused once the season is cancelled. It
        would sit in a non-terminal state for ever.

        That mattered little until the former-driver flag came to be set at the FINAL
        transition (#216). A driver whose only round is one of these is now flagless when the
        driver pass runs, and the pass deletes a flagless profile at Not Signed Up — NULLing
        the `driver_profile_id` on their result rows and destroying their history entries,
        which is the very discarding of a result the rule above forbids. Closing the rounds
        first marks their drivers, and the pass keeps them.

        Returns the ids closed, for the caller to report and for the tests to assert on.
        """
        from datetime import timezone
        from services.result_submission_service import (
            recompute_former_drivers_for_round,
        )

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self._db_path) as db:
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
                # The round's results are final as of the line above, so its drivers become
                # former drivers here — which is the whole point of closing them (#216).
                await recompute_former_drivers_for_round(db, row["id"])
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
                        now,
                    ),
                )
            await db.commit()

        return [row["id"] for row in rows]

    async def all_divisions_finished(self) -> bool:
        """True if every division of the active season is FINISHED or CANCELLED.

        This is the gate on completing a season. It asks about divisions, not rounds: a division
        is the unit a league finishes, and one that was cancelled never had to run its rounds at
        all. `get_outstanding_rounds` supplies the detail for the refusal.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*) FROM divisions d
                JOIN seasons s ON s.id = d.season_id
                WHERE s.status    = 'ACTIVE'
                  AND d.status NOT IN ('FINISHED', 'CANCELLED')
                """,
            )
            row = await cursor.fetchone()
        return row is not None and row[0] == 0

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

    async def discard_uncommitted_placements(self, season_id: int) -> int:
        """Delete every placement of *season_id* not yet committed, freeing its seat.

        Cancelling a season discards them (issue #220): the drivers placed stood outside the
        championship and earn no history of it. Returns how many were discarded.
        """
        async with get_connection(self._db_path) as db:
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
            await db.commit()
            return cursor.rowcount

    async def commit_placements(self, season_id: int) -> int:
        """Commit every placement of *season_id* not yet committed; returns how many.

        Called as placements are confirmed (issue #220). A committed placement is part of the
        championship: it holds its roles, stands in its lineup, is called to check-in and
        scored in results and standings.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE driver_season_assignments SET committed = 1 "
                "WHERE season_id = ? AND committed = 0",
                (season_id,),
            )
            await db.commit()
            return cursor.rowcount

    async def transition_to_active(self, season_id: int) -> None:
        """Set season status to ACTIVE, and its divisions with it.

        The divisions move SETUP -> ACTIVE in the same transaction. Until issue #154 nothing ever
        wrote 'ACTIVE' to a division at all — every insert path takes the schema default of
        'SETUP' and nothing moved it on — so every division sat in setup for its whole life and
        `/season cancel` posted its notice to none of them. A cancelled division is left alone: a
        division called off during setup does not start racing because the season did.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE seasons SET status = ? WHERE id = ?",
                (SeasonStatus.ACTIVE.value, season_id),
            )
            await db.execute(
                "UPDATE divisions SET status = 'ACTIVE' WHERE season_id = ? AND status = 'SETUP'",
                (season_id,),
            )
            await db.commit()

    async def delete_season(self, season_id: int) -> None:
        """FK-safe cascade delete of one season and all its child records.

        What `/season abort` leaves of a season whose placements were never confirmed: nothing
        at all, its signups included (issue #220).

        Raises ``ValueError`` for a season not in SETUP, before anything is deleted. Its number
        is committed from the moment it leaves SETUP, and that number is how a league names
        its own history: removing one would leave a gap nothing explains (issue #153). A season
        id that names no season is not an error.
        """
        async with get_connection(self._db_path) as db:
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

            # ── Results module: round-level children ────────────────────────
            if round_ids:
                ph = ",".join("?" * len(round_ids))
                await db.execute(f"DELETE FROM round_submission_channels WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM driver_standings_snapshots WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM team_standings_snapshots WHERE round_id IN ({ph})", round_ids)
                # Race and qualifying results hang from session_results, not a round
                cursor = await db.execute(
                    f"SELECT id FROM session_results WHERE round_id IN ({ph})", round_ids
                )
                session_result_ids = [r[0] for r in await cursor.fetchall()]
                if session_result_ids:
                    sph = ",".join("?" * len(session_result_ids))
                    await db.execute(f"DELETE FROM race_session_results WHERE session_result_id IN ({sph})", session_result_ids)
                    await db.execute(f"DELETE FROM qualifying_session_results WHERE session_result_id IN ({sph})", session_result_ids)
                await db.execute(f"DELETE FROM session_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM forecast_messages WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM phase_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)

            # ── Results module: season-level children ───────────────────────
            await db.execute("DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_amendment_state WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_fl WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_entries WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM season_points_links WHERE season_id = ?", (season_id,))

            # ── Driver/team children ────────────────────────────────────────
            if division_ids:
                ph = ",".join("?" * len(division_ids))

                # Collect fake (test-mode) driver profile IDs so we can delete
                # them after their FK references are cleared.
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

                await db.execute(f"DELETE FROM driver_season_assignments WHERE division_id IN ({ph})", division_ids)
                await db.execute(f"DELETE FROM division_results_config WHERE division_id IN ({ph})", division_ids)

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

                # Remove orphaned fake driver profiles (test-mode roster)
                if test_profile_ids:
                    tph = ",".join("?" * len(test_profile_ids))
                    await db.execute(f"DELETE FROM driver_profiles WHERE id IN ({tph})", test_profile_ids)

            await db.execute("DELETE FROM season_review_prompts WHERE season_id = ?", (season_id,))
            await db.execute("DELETE FROM divisions WHERE season_id = ?", (season_id,))
            # The season's signups, windows and signup configuration go with it by cascade.
            await db.execute("DELETE FROM seasons WHERE id = ?", (season_id,))
            await db.commit()

    # ------------------------------------------------------------------
    # Division
    # ------------------------------------------------------------------

    async def add_division(
        self,
        season_id: int,
        name: str,
        mention_role_id: int,
        forecast_channel_id: int | None = None,
        tier: int = 0,
    ) -> Division:
        """Insert a division and return it."""
        if tier != 0:
            if tier < 1:
                raise ValueError(f"Tier must be >= 1, got {tier}.")
            async with get_connection(self._db_path) as db:
                cursor = await db.execute(
                    "SELECT 1 FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
                if await cursor.fetchone():
                    raise ValueError(
                        f"A division with tier {tier} already exists in this season."
                    )
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO divisions
                    (season_id, name, mention_role_id, forecast_channel_id, tier)
                VALUES (?, ?, ?, ?, ?)
                """,
                (season_id, name, mention_role_id, forecast_channel_id, tier),
            )
            await db.commit()
            div_id = inserted_id(cursor)

        return Division(
            id=div_id,
            season_id=season_id,
            name=name,
            mention_role_id=mention_role_id,
            forecast_channel_id=forecast_channel_id,
            tier=tier,
        )

    async def get_previewable_divisions(
        self, *, timeout: float | None = None
    ) -> list[Division]:
        """The divisions of the season an `/images test` preview draws, in one connection.

        Exactly `get_previewable_season` followed by `get_divisions`, which is what the
        preview autocomplete used to call in turn — two `aiosqlite.connect` opens, two
        `PRAGMA foreign_keys`, two closes, on a path with three seconds to answer Discord in.
        Doing both on one connection halves that, and the second query is only reached when
        the first found a season.

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
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier, "
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
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier, "
                "lineup_channel_id, calendar_channel_id, lineup_message_id, "
                "calendar_message_id "
                "FROM divisions WHERE season_id = ? ORDER BY tier",
                (season_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_division(r) for r in rows]

    async def set_division_forecast_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Update divisions.forecast_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT forecast_channel_id FROM divisions WHERE id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?",
                (channel_id, division_id),
            )
            await db.commit()
        return old_id

    async def set_division_results_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.results_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id",
                (division_id, channel_id),
            )
            await db.commit()
        return old_id

    async def set_division_standings_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.standings_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, standings_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET standings_channel_id = excluded.standings_channel_id",
                (division_id, channel_id),
            )
            await db.commit()
        return old_id

    async def set_division_penalty_channel(
        self, division_id: int, channel_id: int | None
    ) -> int | None:
        """Upsert division_results_config.penalty_channel_id. Returns the previous value."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
            old_id: int | None = row[0] if row else None
            await db.execute(
                "INSERT INTO division_results_config (division_id, penalty_channel_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(division_id) DO UPDATE SET penalty_channel_id = excluded.penalty_channel_id",
                (division_id, channel_id),
            )
            await db.commit()
        return old_id

    async def get_divisions_with_results_config(
        self, season_id: int
    ) -> list[Division]:
        """Return divisions with results_channel_id and standings_channel_id populated
        via LEFT JOIN to division_results_config. Used by the approval gate."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT d.id, d.season_id, d.name, d.mention_role_id, d.forecast_channel_id,
                       d.status, d.tier,
                       d.lineup_channel_id, d.calendar_channel_id, d.lineup_message_id,
                       d.calendar_message_id,
                       drc.results_channel_id, drc.standings_channel_id,
                       drc.penalty_channel_id
                FROM divisions d
                LEFT JOIN division_results_config drc ON drc.division_id = d.id
                WHERE d.season_id = ?
                ORDER BY d.tier
                """,
                (season_id,),
            )
            rows = await cursor.fetchall()
        result: list[Division] = []
        for r in rows:
            div = _row_to_division(r)
            div.results_channel_id = r["results_channel_id"]
            div.standings_channel_id = r["standings_channel_id"]
            div.penalty_channel_id = r["penalty_channel_id"]
            result.append(div)
        return result

    async def rename_division(self, division_id: int, new_name: str) -> None:
        """Update a division's name."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE divisions SET name = ? WHERE id = ?",
                (new_name, division_id),
            )
            await db.commit()

    async def delete_division(self, division_id: int) -> None:
        """Cascade-delete a division and all its child rows."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM rounds WHERE division_id = ?", (division_id,)
            )
            round_rows = await cursor.fetchall()
            round_ids = [r[0] for r in round_rows]

            if round_ids:
                ph = ",".join("?" * len(round_ids))
                await db.execute(f"DELETE FROM forecast_messages WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM phase_results WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM sessions WHERE round_id IN ({ph})", round_ids)
                await db.execute(f"DELETE FROM rounds WHERE division_id = ?", (division_id,))

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
            await db.commit()

    async def _cancel_division_on(
        self,
        db,
        division_id: int,
        actor_id: int,
        actor_name: str,
        now: datetime,
    ) -> None:
        """Cancel a division and its unraced rounds on an already-open connection.

        Only rounds that may still be cancelled are — those not yet run, and those whose
        results have not been entered. Once results are in, the drivers have reports and appeals
        to lodge and calling the round off would take that from them, so it keeps its place and
        its results; the division around it is what was called off. `ROUND_CANCELLABLE` carries
        the rule, and `/round cancel` reads the same set, so one round and a whole division
        cannot disagree about what may be called off.
        """
        cursor = await db.execute(
            "SELECT status FROM divisions WHERE id = ?", (division_id,)
        )
        row = await cursor.fetchone()
        previous = row["status"] if row else "ACTIVE"

        cursor = await db.execute(
            f"""
            SELECT id FROM rounds
            WHERE division_id = ?
              AND status IN ({_CANCELLABLE_SQL})
            ORDER BY round_number
            """,
            (division_id,),
        )
        unraced = [r["id"] for r in await cursor.fetchall()]

        for round_id in unraced:
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
                    "round.status", "ACTIVE", "CANCELLED", now.isoformat(),
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

    async def cancel_division(
        self,
        division_id: int,
        actor_id: int,
        actor_name: str,
    ) -> None:
        """Mark a division CANCELLED, cancelling every round of it not yet raced.

        Before issue #154 this set the division's status alone: its rounds were unscheduled by the
        caller but kept saying ACTIVE for ever, so a cancelled division still read as one holding
        outstanding rounds.
        """
        from datetime import timezone
        now = datetime.now(timezone.utc)
        async with get_connection(self._db_path) as db:
            await self._cancel_division_on(
                db, division_id, actor_id, actor_name, now
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT season_id FROM divisions WHERE id = ?", (division_id,)
            )
            season_row = await cursor.fetchone()

        # Cancelling the last division still running leaves the season pending completion.
        if season_row is not None:
            from services.season_lifecycle_service import advance_to_pending_completion

            await advance_to_pending_completion(self._db_path, season_row["season_id"])

    async def cancel_season_cascade(
        self,
        season_id: int,
        actor_id: int,
        actor_name: str,
    ) -> None:
        """Cancel every division of a season, then the season itself.

        The order is deliberate and not merely tidy: `cancel_round` refuses to touch a round whose
        season is already COMPLETED or CANCELLED, so a season row flipped first would lock the
        cascade out of its own children. The season is therefore the last thing written, and the
        whole cascade shares one transaction so a failure part-way cannot leave a season standing
        over half-cancelled divisions.
        """
        from datetime import timezone
        now = datetime.now(timezone.utc)
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM divisions WHERE season_id = ? AND status != 'CANCELLED' ORDER BY tier",
                (season_id,),
            )
            division_ids = [r["id"] for r in await cursor.fetchall()]

            for division_id in division_ids:
                await self._cancel_division_on(
                    db, division_id, actor_id, actor_name, now
                )

            await db.execute(
                "UPDATE seasons SET status = 'CANCELLED' WHERE id = ?", (season_id,)
            )
            await db.commit()

    async def duplicate_division(
        self,
        division_id: int,
        name: str,
        role_id: int,
        forecast_channel_id: int | None = None,
        day_offset: int = 0,
        hour_offset: float = 0.0,
        tier: int = 0,
    ) -> Division:
        """Copy a division (and all its rounds with shifted datetimes) into a new division."""
        from datetime import timedelta
        src_rounds = await self.get_division_rounds(division_id)
        async with get_connection(self._db_path) as db:
            # Find the season_id of the source division
            cursor = await db.execute(
                "SELECT season_id FROM divisions WHERE id = ?", (division_id,)
            )
            row = await sole_row(cursor)
            season_id: int = row[0]

            if tier != 0:
                if tier < 1:
                    raise ValueError(f"Tier must be >= 1, got {tier}.")
                cursor = await db.execute(
                    "SELECT 1 FROM divisions WHERE season_id = ? AND tier = ?",
                    (season_id, tier),
                )
                if await cursor.fetchone():
                    raise ValueError(
                        f"A division with tier {tier} already exists in this season."
                    )

            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier)"
                " VALUES (?, ?, ?, ?, ?)",
                (season_id, name, role_id, forecast_channel_id, tier),
            )
            await db.commit()
            new_div_id = inserted_id(cursor)

            delta = timedelta(days=day_offset, hours=hour_offset)
            for rnd in src_rounds:
                new_dt = rnd.scheduled_at + delta
                await db.execute(
                    "INSERT INTO rounds"
                    " (division_id, round_number, format, track_name, scheduled_at,"
                    "  phase1_done, phase2_done, phase3_done)"
                    " VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
                    (
                        new_div_id,
                        rnd.round_number,  # will be renumbered next
                        rnd.format.value,
                        rnd.track_name,
                        new_dt.isoformat(),
                    ),
                )
            await db.commit()

        await self.renumber_rounds(new_div_id)

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, season_id, name, mention_role_id, forecast_channel_id, status, tier"
                " FROM divisions WHERE id = ?",
                (new_div_id,),
            )
            row = await sole_row(cursor)
        return _row_to_division(row)

    # ------------------------------------------------------------------
    # Round
    # ------------------------------------------------------------------

    async def add_round(
        self,
        division_id: int,
        round_number: int,
        fmt: RoundFormat,
        track_name: str | None,
        scheduled_at: datetime,
    ) -> Round:
        """Insert a round and return it."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO rounds
                    (division_id, round_number, format, track_name, scheduled_at,
                     phase1_done, phase2_done, phase3_done)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0)
                """,
                (
                    division_id,
                    round_number,
                    fmt.value,
                    track_name,
                    scheduled_at.isoformat(),
                ),
            )
            await db.commit()
            round_id = inserted_id(cursor)

        return Round(
            id=round_id,
            division_id=division_id,
            round_number=round_number,
            format=fmt,
            track_name=track_name,
            scheduled_at=scheduled_at,
        )

    async def get_round(self, round_id: int) -> Round | None:
        """Return a single round by ID."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "phase1_done, phase2_done, phase3_done, status FROM rounds WHERE id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()
        return _row_to_round(row) if row else None

    async def get_division_rounds(self, division_id: int) -> list[Round]:
        """Return all rounds for *division_id* ordered by round_number."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, division_id, round_number, format, track_name, scheduled_at, "
                "phase1_done, phase2_done, phase3_done, status FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_round(r) for r in rows]

    async def renumber_rounds(self, division_id: int) -> None:
        """Rewrite round_number for all rounds in a division, sorted ascending by scheduled_at."""
        async with get_connection(self._db_path) as db:
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
            await db.commit()

    async def delete_round(self, round_id: int) -> None:
        """Delete a round and renumber siblings."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT division_id FROM rounds WHERE id = ?", (round_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return
            division_id: int = row[0]

            await db.execute("DELETE FROM forecast_messages WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM phase_results WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM sessions WHERE round_id = ?", (round_id,))
            await db.execute("DELETE FROM rounds WHERE id = ?", (round_id,))
            await db.commit()

        await self.renumber_rounds(division_id)

    async def cancel_round(
        self,
        round_id: int,
        actor_id: int,
        actor_name: str,
    ) -> None:
        """Mark a round CANCELLED and write an audit entry."""
        from datetime import timezone
        now = datetime.now(timezone.utc)
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT r.division_id, s.status AS season_status
                FROM rounds r
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons s ON s.id = d.season_id
                WHERE r.id = ?
                """,
                (round_id,),
            )
            row = await cursor.fetchone()
            division_id = row["division_id"] if row else None

            if row and row["season_status"] in ("COMPLETED", "CANCELLED"):
                raise SeasonImmutableError(
                    f"Round {round_id} belongs to an archived season and cannot be cancelled."
                )

            await db.execute(
                "UPDATE rounds SET status = 'CANCELLED' WHERE id = ?",
                (round_id,),
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
                    "round.status",
                    "ACTIVE",
                    "CANCELLED",
                    now.isoformat(),
                ),
            )
            await db.commit()

        # Cancelling the last outstanding round is what finishes a division, so the division's
        # own status has to be reconsidered here as well as on a result being finalised.
        if division_id is not None:
            await self.refresh_division_status(division_id)

    async def update_round_field(self, round_id: int, field: str, value: object) -> None:
        """Generic field updater used by amendment_service."""
        allowed = {"track_name", "format", "scheduled_at", "phase1_done", "phase2_done", "phase3_done"}
        if field not in allowed:
            raise ValueError(f"Field {field!r} not updatable via this method")
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE rounds SET {field} = ? WHERE id = ?",  # noqa: S608
                (value, round_id),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def create_sessions_for_round(self, round_id: int, fmt: RoundFormat) -> list[Session]:
        """Make the sessions of *round_id* the ones *fmt* defines, replacing any it holds.

        **Replaced, not added to** (issue #408). A confirmation of placements that writes the
        sessions and then fails before the season goes active leaves them behind, and the next
        confirmation used to write a full second set, every session of which the phases then
        forecast twice. However often this is called, the round holds one set: the one its
        format defines now.

        The old rows go with whatever phase data they held, so this is for a round no phase has
        been drawn for. Its one caller is the confirmation, before anything is armed.
        """
        session_types: list[SessionType] = SESSIONS_BY_FORMAT.get(fmt, [])
        sessions: list[Session] = []

        async with get_connection(self._db_path) as db:
            await db.execute("DELETE FROM sessions WHERE round_id = ?", (round_id,))
            for st in session_types:
                cursor = await db.execute(
                    "INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                    (round_id, st.value),
                )
                sessions.append(
                    Session(id=inserted_id(cursor), round_id=round_id, session_type=st)
                )
            await db.commit()

        return sessions

    async def get_sessions(self, round_id: int) -> list[Session]:
        """Return all sessions for *round_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id, round_id, session_type, phase2_slot_type, phase3_slots "
                "FROM sessions WHERE round_id = ?",
                (round_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_session(r) for r in rows]

    async def update_session_phase2(self, session_id: int, slot_type: str) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = ? WHERE id = ?",
                (slot_type, session_id),
            )
            await db.commit()

    async def update_session_phase3(self, session_id: int, slots: list[str]) -> None:
        import json
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase3_slots = ? WHERE id = ?",
                (json.dumps(slots), session_id),
            )
            await db.commit()

    async def clear_session_phase_data(self, round_id: int) -> None:
        """Clear phase2 / phase3 data for all sessions in a round (used by amendments)."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = NULL, phase3_slots = NULL WHERE round_id = ?",
                (round_id,),
            )
            await db.commit()


async def _sync_division_rounds(db, division_id: int, rounds: list[dict]) -> None:
    """Make the rounds of *division_id* match *rounds*, writing only the difference.

    Part of :meth:`SeasonService.sync_pending_config`, on its connection and inside its
    transaction. A round is matched on its format, track and scheduled time, the scheduled
    time compared as the ISO string both sides store. Duplicates are matched one for one, so
    two identical rounds in the config need two in the DB.
    """
    cursor = await db.execute(
        "SELECT id, round_number, format, track_name, scheduled_at FROM rounds "
        "WHERE division_id = ? ORDER BY round_number, id",
        (division_id,),
    )
    held: dict[tuple, list[tuple[int, int]]] = {}
    for r in await cursor.fetchall():
        key = (r["format"], r["track_name"], r["scheduled_at"])
        held.setdefault(key, []).append((r["id"], r["round_number"]))

    for r in rounds:
        key = (r["format"].value, r["track_name"], r["scheduled_at"].isoformat())
        matches = held.get(key)
        if matches:
            round_id, round_number = matches.pop(0)
            if round_number != r["round_number"]:
                await db.execute(
                    "UPDATE rounds SET round_number = ? WHERE id = ?",
                    (r["round_number"], round_id),
                )
            continue
        await db.execute(
            "INSERT INTO rounds "
            "(division_id, round_number, format, track_name, "
            " scheduled_at, phase1_done, phase2_done, phase3_done) "
            "VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
            (division_id, r["round_number"], *key),
        )

    surplus = [round_id for matches in held.values() for round_id, _ in matches]
    if surplus:
        ph = ",".join("?" * len(surplus))
        await db.execute(f"DELETE FROM rounds WHERE id IN ({ph})", surplus)  # noqa: S608


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
        forecast_channel_id=row["forecast_channel_id"],
        status=row["status"],
        tier=row["tier"] if "tier" in keys else 0,
        lineup_channel_id=row["lineup_channel_id"] if "lineup_channel_id" in keys else None,
        calendar_channel_id=row["calendar_channel_id"] if "calendar_channel_id" in keys else None,
        lineup_message_id=row["lineup_message_id"] if "lineup_message_id" in keys else None,
        # Guarded like every other optional column, so a database that has not yet run
        # migration 040 still loads its divisions rather than raising.
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
        scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
        phase1_done=bool(row["phase1_done"]),
        phase2_done=bool(row["phase2_done"]),
        phase3_done=bool(row["phase3_done"]),
        status=row["status"],
    )


def _row_to_session(row: aiosqlite.Row) -> Session:
    import json

    slots_raw = row["phase3_slots"]
    return Session(
        id=row["id"],
        round_id=row["round_id"],
        session_type=SessionType(row["session_type"]),
        phase2_slot_type=row["phase2_slot_type"],
        phase3_slots=json.loads(slots_raw) if slots_raw else None,
    )
