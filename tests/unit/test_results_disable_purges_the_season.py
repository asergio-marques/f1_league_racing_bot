"""Switching the results module off mid-season ends the season's rounds and erases its results.

Issue #167. `/module disable results` used to write the flag and nothing else, so every round
already sitting at AWAITING_RESULTS, AWAITING_REPORT_VERDICTS or AWAITING_APPEAL_VERDICTS stayed
there for ever: its division never finished, `/season complete` refused for the rest of the
season, and — because enabling is refused while a season is ACTIVE — the league could not undo
it either. `/season cancel` was the only way out.

Two things now happen instead, decided 2026-09-14:

- every round only the results module could have moved is closed as FINAL, so the divisions
  finish and the season can be completed; and
- the season's results are destroyed entire — classifications, standings, and the results and
  standings messages already posted — because a disabled module holds and shows nothing.

What survives is deliberate and pinned below: the points configurations, the season's own copy
of them, the division channel bindings, and the verdicts already announced, which carry no
message id and so cannot be taken back.

Every test that constructs the confirmation view is `async def`: apt's discord.py 2.5.0 calls
`asyncio.get_running_loop()` in `View.__init__` where the pinned 2.7.1 defers it.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from cogs.module_cog import ModuleCog, _ConfirmDisableResultsView  # noqa: E402
from services.season_service import SeasonService  # noqa: E402
from services.results_purge_service import purge_season_results  # noqa: E402

SERVER_ID = 5150
ACTOR_ID = 4242
ACTOR_NAME = "Admin"
BOT_USER_ID = 77
RESULTS_CHANNEL_ID = 9001
STANDINGS_CHANNEL_ID = 9002


# ---------------------------------------------------------------------------
# Discord stubs
# ---------------------------------------------------------------------------


class _FakeChannel:
    """A channel that records what was deleted from it.

    `_delete_with_continuations` fetches the anchor, walks `history` for bot-authored
    continuation chunks, then deletes. The history here is empty, which is the single-message
    case every posting under 2000 characters takes.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.deleted_messages: list[int] = []
        self.deleted = False

    async def fetch_message(self, message_id: int):
        message = MagicMock()
        message.id = message_id
        message.author.id = BOT_USER_ID

        async def _delete() -> None:
            self.deleted_messages.append(message_id)

        message.delete = _delete
        return message

    def history(self, **kwargs):
        async def _empty():
            return
            yield  # pragma: no cover - makes this an async generator

        return _empty()

    async def delete(self, reason: str | None = None) -> None:
        self.deleted = True


def _make_bot(db_path: str, *, guild: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.season_service = SeasonService(db_path)

    channels = {
        RESULTS_CHANNEL_ID: _FakeChannel(RESULTS_CHANNEL_ID),
        STANDINGS_CHANNEL_ID: _FakeChannel(STANDINGS_CHANNEL_ID),
    }
    bot.channels = channels
    if guild:
        fake_guild = MagicMock()
        fake_guild.get_channel = lambda cid: channels.get(cid)
        bot.get_guild = MagicMock(return_value=fake_guild)
    else:
        bot.get_guild = MagicMock(return_value=None)
    return bot


def _make_cog(db_path: str, *, guild: bool = True) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = _make_bot(db_path, guild=guild)
    return cog


def _make_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = ACTOR_NAME
    interaction.user.__str__ = lambda self: "admin#0001"  # type: ignore[assignment]
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def _seed(
    tmp_path,
    *,
    round_statuses=("AWAITING_RESULTS",),
    season_status: str = "ACTIVE",
    division_status: str = "ACTIVE",
    server_id: int = SERVER_ID,
    with_results_data: bool = True,
) -> tuple[str, int, list[int]]:
    """Seed a season with one division and one round per status. Returns (db, div_id, rounds)."""
    db_path = os.path.join(str(tmp_path), "purge.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (server_id,),
        )
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) "
            "VALUES (?, 1)",
            (1,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, 0)",
            (1,),
        )
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', ?, 1)",
            (season_status,),
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier, status) "
            "VALUES (?, 'Division 1', 555, 1, ?)",
            (season_id, division_status),
        )
        division_id = cur.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, RESULTS_CHANNEL_ID, STANDINGS_CHANNEL_ID),
        )
        # Configuration that must survive the purge.
        await db.execute(
            "INSERT INTO points_config_store (config_name) VALUES ('100%')"
        )
        await db.execute(
            "INSERT INTO season_points_entries "
            "(season_id, config_name, session_type, position, points) "
            "VALUES (?, '100%', 'RACE', 1, 25)",
            (season_id,),
        )

        round_ids: list[int] = []
        for number, status in enumerate(round_statuses, start=1):
            cur = await db.execute(
                "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, "
                "format, status) VALUES (?, ?, 'Bahrain', ?, 'NORMAL', ?)",
                (division_id, number, f"2026-0{number}-01T12:00:00", status),
            )
            round_ids.append(cur.lastrowid)
        await db.commit()

    if with_results_data:
        for index, round_id in enumerate(round_ids):
            await _seed_results_for_round(db_path, round_id, division_id, index)

    return db_path, division_id, round_ids


async def _seed_results_for_round(
    db_path: str, round_id: int, division_id: int, index: int
) -> None:
    """One raced round's worth of results: a session, a driver row, a verdict, standings."""
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "INSERT INTO session_results "
            "(round_id, division_id, session_type, status, config_name, results_message_id) "
            "VALUES (?, ?, 'RACE', 'ACTIVE', '100%', ?)",
            (round_id, division_id, 1000 + index),
        )
        session_result_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO race_session_results "
            "(session_result_id, driver_user_id, team_role_id, finishing_position) "
            "VALUES (?, 42, 7, 1)",
            (session_result_id,),
        )
        race_result_id = cur.lastrowid
        # Verdicts point into race_session_results with no ON DELETE CASCADE — the purge has to
        # take them first or the whole delete fails on the foreign key.
        await db.execute(
            "INSERT INTO penalty_records "
            "(race_result_id, penalty_type, description, justification, "
            " applied_by, applied_at) "
            "VALUES (?, 'TIME', '5s', 'contact', 'steward', '2026-01-02T00:00:00')",
            (race_result_id,),
        )
        await db.execute(
            "INSERT INTO appeal_records "
            "(race_result_id, penalty_type, description, justification, "
            " submitted_by, submitted_at) "
            "VALUES (?, 'TIME', '5s', 'appealed', 'driver', '2026-01-03T00:00:00')",
            (race_result_id,),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots "
            "(round_id, division_id, driver_user_id, standing_position, total_points, "
            " standings_message_id, constructor_standings_message_id) "
            "VALUES (?, ?, 42, 1, 25, ?, ?)",
            (round_id, division_id, 2000 + index, 3000 + index),
        )
        await db.execute(
            "INSERT INTO team_standings_snapshots "
            "(round_id, division_id, team_role_id, standing_position, total_points) "
            "VALUES (?, ?, 7, 1, 25)",
            (round_id, division_id),
        )
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, created_at, closed) "
            "VALUES (?, ?, '2026-01-01T00:00:00', 0)",
            (round_id, 8000 + index),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


async def _round_status(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
        return (await cur.fetchone())["status"]


async def _division_status(db_path: str, division_id: int) -> str:
    async with get_connection(db_path) as db:
        cur = await db.execute("SELECT status FROM divisions WHERE id = ?", (division_id,))
        return (await cur.fetchone())["status"]


async def _count(db_path: str, table: str) -> int:
    async with get_connection(db_path) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
        return (await cur.fetchone())[0]


async def _disable(cog: ModuleCog) -> MagicMock:
    """Run the disable through the confirmation, as a league manager would."""
    interaction = _make_interaction()
    await cog._apply_results_disable(interaction, cascade_attendance=False)
    return interaction


# ---------------------------------------------------------------------------
# The rounds are closed, so the season can be completed — the #167 regression
# ---------------------------------------------------------------------------


async def test_each_state_only_results_could_move_is_closed(tmp_path) -> None:
    """The three awaiting states, the whole of the dead end."""
    for status in (
        "AWAITING_RESULTS",
        "AWAITING_REPORT_VERDICTS",
        "AWAITING_APPEAL_VERDICTS",
    ):
        directory = tmp_path / status
        directory.mkdir()
        db_path, division_id, (round_id,) = await _seed(
            directory, round_statuses=(status,)
        )
        await _disable(_make_cog(db_path))

        assert await _round_status(db_path, round_id) == "FINAL", status
        assert await _division_status(db_path, division_id) == "FINISHED", status
        assert await SeasonService(db_path).all_divisions_finished() is True, status


async def test_a_round_still_waiting_on_the_clock_is_left_alone(tmp_path) -> None:
    """NOT_RUN waits on its own moment, not on results.

    `run_result_submission_job` closes it as FINAL when that moment arrives with the module
    off, and it still has its weather and its check-in to run first. Closing it here would end
    rounds that have not been raced.
    """
    db_path, division_id, (not_run, awaiting) = await _seed(
        tmp_path, round_statuses=("NOT_RUN", "AWAITING_RESULTS")
    )
    await _disable(_make_cog(db_path))

    assert await _round_status(db_path, not_run) == "NOT_RUN"
    assert await _round_status(db_path, awaiting) == "FINAL"
    # The division is still open, because the NOT_RUN round has yet to happen.
    assert await _division_status(db_path, division_id) == "ACTIVE"


async def test_a_cancelled_round_is_left_alone(tmp_path) -> None:
    db_path, _, (cancelled,) = await _seed(tmp_path, round_statuses=("CANCELLED",))
    await _disable(_make_cog(db_path))

    assert await _round_status(db_path, cancelled) == "CANCELLED"


async def test_a_cancelled_division_s_rounds_are_left_alone(tmp_path) -> None:
    """A cancelled division was called off; the disable has no business reopening its books."""
    db_path, _, (round_id,) = await _seed(
        tmp_path, round_statuses=("AWAITING_RESULTS",), division_status="CANCELLED"
    )
    await _disable(_make_cog(db_path))

    assert await _round_status(db_path, round_id) == "AWAITING_RESULTS"


async def test_a_season_not_yet_running_has_nothing_closed_or_deleted(tmp_path) -> None:
    """Between seasons the disable is the cheap thing it always was."""
    db_path, _, (round_id,) = await _seed(
        tmp_path, round_statuses=("AWAITING_RESULTS",), season_status="SETUP"
    )
    await _disable(_make_cog(db_path))

    assert await _round_status(db_path, round_id) == "AWAITING_RESULTS"
    assert await _count(db_path, "session_results") == 1


async def test_every_closed_round_is_audited(tmp_path) -> None:
    db_path, _, _ = await _seed(
        tmp_path, round_statuses=("AWAITING_RESULTS", "AWAITING_APPEAL_VERDICTS")
    )
    await _disable(_make_cog(db_path))

    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT old_value, new_value FROM audit_entries "
            "WHERE change_type = 'round.status' ORDER BY old_value"
        )
        rows = [dict(r) for r in await cur.fetchall()]

    assert [r["old_value"] for r in rows] == [
        "AWAITING_APPEAL_VERDICTS",
        "AWAITING_RESULTS",
    ]
    assert {r["new_value"] for r in rows} == {"FINAL"}


async def test_the_purge_is_audited_with_what_it_took(tmp_path) -> None:
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    await _disable(_make_cog(db_path))

    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT new_value FROM audit_entries WHERE change_type = 'RESULTS_SEASON_PURGED'"
        )
        row = await cur.fetchone()

    assert row is not None
    recorded = json.loads(row["new_value"])
    assert recorded["sessions"] == 1
    assert recorded["rounds_closed"] == 1


# ---------------------------------------------------------------------------
# The results are erased
# ---------------------------------------------------------------------------


async def test_every_result_of_the_season_is_deleted(tmp_path) -> None:
    db_path, _, _ = await _seed(
        tmp_path, round_statuses=("AWAITING_RESULTS", "AWAITING_APPEAL_VERDICTS")
    )
    await _disable(_make_cog(db_path))

    for table in (
        "session_results",
        "race_session_results",
        "penalty_records",
        "appeal_records",
        "driver_standings_snapshots",
        "team_standings_snapshots",
        "round_submission_channels",
    ):
        assert await _count(db_path, table) == 0, table


async def test_a_verdict_does_not_block_the_delete(tmp_path) -> None:
    """penalty_records points into race_session_results with no ON DELETE CASCADE.

    With `PRAGMA foreign_keys` ON, deleting the session's results while a verdict still points
    into them fails the whole transaction — so the verdicts go first. Pinned because the
    ordering looks arbitrary and reads as tidy-up-able.
    """
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_APPEAL_VERDICTS",))
    assert await _count(db_path, "penalty_records") == 1

    report = await purge_season_results(db_path, _make_bot(db_path))

    assert report["sessions"] == 1
    assert await _count(db_path, "penalty_records") == 0
    assert await _count(db_path, "appeal_records") == 0


async def test_the_posted_results_and_standings_are_unposted(tmp_path) -> None:
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    await _disable(cog)

    results_channel = cog.bot.channels[RESULTS_CHANNEL_ID]
    standings_channel = cog.bot.channels[STANDINGS_CHANNEL_ID]
    assert results_channel.deleted_messages == [1000]
    assert sorted(standings_channel.deleted_messages) == [2000, 3000]


async def test_an_open_submission_channel_is_closed(tmp_path) -> None:
    """A wizard left running would go on collecting results into a module that is off."""
    db_path, _, (round_id,) = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    submission_channel = _FakeChannel(8000)
    cog.bot.channels[8000] = submission_channel

    report = await purge_season_results(db_path, cog.bot)

    assert report["submission_channels"] == 1
    assert submission_channel.deleted is True


async def test_a_guild_out_of_cache_still_erases_the_rows(tmp_path) -> None:
    """A bot that cannot reach the messages is not a reason to half-erase the season."""
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    await _disable(_make_cog(db_path, guild=False))

    assert await _count(db_path, "session_results") == 0
    assert await _count(db_path, "driver_standings_snapshots") == 0


async def test_a_missing_channel_does_not_abort_the_purge(tmp_path) -> None:
    """A results channel deleted by hand must not leave the standings behind."""
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    del cog.bot.channels[RESULTS_CHANNEL_ID]

    await _disable(cog)

    assert await _count(db_path, "session_results") == 0
    assert cog.bot.channels[STANDINGS_CHANNEL_ID].deleted_messages != []


async def test_the_configuration_survives(tmp_path) -> None:
    """Settings are not output. The bot has always promised these come back untouched."""
    db_path, division_id, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    await _disable(_make_cog(db_path))

    assert await _count(db_path, "points_config_store") == 1
    assert await _count(db_path, "season_points_entries") == 1
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT results_channel_id, standings_channel_id FROM division_results_config "
            "WHERE division_id = ?",
            (division_id,),
        )
        row = await cur.fetchone()
    assert (row["results_channel_id"], row["standings_channel_id"]) == (
        RESULTS_CHANNEL_ID,
        STANDINGS_CHANNEL_ID,
    )


# ---------------------------------------------------------------------------
# The league is told first
# ---------------------------------------------------------------------------


async def test_a_running_season_is_confirmed_even_with_attendance_off(tmp_path) -> None:
    """The half that was silent: a league without attendance got no warning at all."""
    db_path, _, (round_id,) = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    interaction = _make_interaction()

    await cog._disable_results(interaction)

    warning = interaction.response.send_message.await_args.args[0]
    assert "destroys this season's results" in warning
    assert "none of this can be undone" in warning.lower()
    view = interaction.response.send_message.await_args.kwargs["view"]
    assert isinstance(view, _ConfirmDisableResultsView)
    # Nothing written until the button is pressed.
    assert await _round_status(db_path, round_id) == "AWAITING_RESULTS"
    assert await _count(db_path, "session_results") == 1


async def test_the_warning_says_verdicts_cannot_be_taken_back(tmp_path) -> None:
    """They record the channel they went to but never a message id, so nothing can delete them."""
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    interaction = _make_interaction()

    await _make_cog(db_path)._disable_results(interaction)

    assert "verdicts channel" in interaction.response.send_message.await_args.args[0]


async def test_confirming_erases_the_season(tmp_path) -> None:
    db_path, _, (round_id,) = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    view = _ConfirmDisableResultsView(
        cog, ACTOR_ID, cascade_attendance=False
    )

    await view.confirm.callback(_make_interaction())

    assert await _round_status(db_path, round_id) == "FINAL"
    assert await _count(db_path, "session_results") == 0


async def test_cancelling_erases_nothing(tmp_path) -> None:
    db_path, _, (round_id,) = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    cog = _make_cog(db_path)
    view = _ConfirmDisableResultsView(
        cog, ACTOR_ID, cascade_attendance=False
    )
    interaction = _make_interaction()

    await view.cancel.callback(interaction)

    assert await _round_status(db_path, round_id) == "AWAITING_RESULTS"
    assert await _count(db_path, "session_results") == 1
    assert "nothing was deleted" in interaction.response.send_message.await_args.args[0]


async def test_the_reply_names_what_was_destroyed(tmp_path) -> None:
    db_path, _, _ = await _seed(tmp_path, round_statuses=("AWAITING_RESULTS",))
    interaction = await _disable(_make_cog(db_path))

    reply = interaction.followup.send.await_args.args[0]
    assert "This season's results are gone" in reply
    assert "1 round(s) closed with no results" in reply
