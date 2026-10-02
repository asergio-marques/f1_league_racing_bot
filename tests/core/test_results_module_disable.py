"""Switching Results & Standings off — the most destructive toggle in the bot.

Issue #208. Disabling this module mid-season deletes that season's results entire and closes
every round still waiting on them. Two issues were filed against it being silent about that, and
both fixes live here.

**The cascade used to happen unannounced** (the silent half of issue #114): the reply named
results alone, and the league was told nothing about attendance going with it, its check-in and
attendance channels being cleared, or its being unable to come back while the season runs.

**A running season was silent for longer** (issue #167). Disabling mid-season destroys the
season's results, and a league with attendance *already off* was shown no warning at all before
it happened — the confirmation only appeared when there was a cascade to announce. It is now
owed to a running season in its own right, whatever attendance is doing.
`test_a_running_season_is_warned_even_with_attendance_already_off` is the regression test for
exactly that, and it is the case a reader re-coupling the two conditions would lose.

**The warning is built from what is actually at stake.** The two costs are independent: a league
can face a running season, a cascade, both, or — between seasons with attendance off — neither.
A fixed warning would either overstate or understate, and one that overstates is one nobody
reads.

**The switch-off is one save** (#439, defect 8). It is carried out on the change queue: the flag
goes down, the season's results are deleted and the rounds still awaiting results are closed in
the one save, so nothing the erasure disturbs can post again on its way out, and a stop can never
leave the flag down with rounds nothing can close. The messages are taken down after it, one job
each, and the reply that acknowledged the confirmation is updated with what went. A removal that
fails stops the queue until it is cleared (owner, 2026-10-02): the tests of a message left standing
discard it as a league admin would.
`test_the_flag_goes_down_in_the_save_that_erases_the_results` holds it. The paths that decide
whether to warn run on doubles and must never reach the queue.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.module_cog import (
    ModuleCog,
    _ConfirmDisableResultsView,
    _results_disable_warning,
)
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    league_double,
    member,
    member_interaction,
    queued_log_lines,
    run_queue,
    stopped_job,
    updated_reply,
)
from tests.support.teams import seed_team_instances

SERVER_ID = 12408
ACTOR_ID = 77
OTHER_ADMIN = 88
BOT_USER_ID = 7
RESULTS_CHANNEL_ID = 9001
STANDINGS_CHANNEL_ID = 9002
VERDICTS_CHANNEL_ID = 9003
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
#: What is not yet true of each test marked with it.
STOPS = "#439: a removal that fails does not yet stop the queue until it is discarded"


def _acknowledges(text: str) -> bool:
    """Whether *text* is the admin's acknowledgement: the switch-off under way, to be updated when
    it is done. The job number it also names is pinned in `test_change_queue.py`."""
    return (
        text.startswith("⏳ Turning Results & Standings off")
        and "This message will be updated when it is done" in text
        and "the log channel will say so." in text
    )


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------


def test_a_running_season_is_warned_about_its_results():
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "destroys this season's results" in warning
    assert "None of this can be undone" in warning


def test_the_season_warning_lists_everything_that_goes():
    """A manager weighing this needs the whole cost, not a summary — every one of these is
    something they might not have thought of."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    for cost in (
        "every classification recorded this season is deleted",
        "removed from its channel",
        "closed as final with no results",
        "no further results are collected",
        "verdict already announced is removed from the verdicts channel",
    ):
        assert cost in warning


def test_the_season_warning_says_what_survives():
    """Just as important: a manager who believed their points configurations were going
    too might not disable a module they needed to. The auto-sack and auto-reserve announcements
    are named because they share the verdicts channel with what does go (#189)."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "auto-sack and auto-reserve announcements" in warning
    assert "points configurations" in warning.lower()


def test_the_season_warning_says_it_cannot_be_switched_back():
    """Not until the season ends — which is the part that turns a reversible-sounding
    toggle into a decision."""
    warning = _results_disable_warning(season_active=True, attendance=False)

    assert "cannot be switched back on until" in warning


def test_the_cascade_is_announced_in_its_own_right():
    """The silent half of issue #114."""
    warning = _results_disable_warning(season_active=False, attendance=True)

    assert "Attendance goes with it" in warning


def test_a_league_facing_both_costs_is_told_both():
    """They are independent, and a manager told only one would be surprised by the other."""
    warning = _results_disable_warning(season_active=True, attendance=True)

    assert "destroys this season's results" in warning
    assert "Attendance goes with it" in warning


def test_a_league_facing_neither_cost_is_warned_about_nothing():
    """Between seasons with attendance off there is nothing at stake, and a warning
    nobody needs is a warning nobody reads."""
    assert _results_disable_warning(season_active=False, attendance=False) == ""


# ---------------------------------------------------------------------------
# Fixtures for the command
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, results: bool = True, attendance: bool = False) -> str:
    db_path = os.path.join(str(tmp_path), "results_disable.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, ?)",
            (int(results),),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, ?)",
            (1, int(attendance)),
        )
        await db.commit()
    return db_path


async def _season(db_path: str, *, rounds: int = 1, verdicts: int = 0) -> list[int]:
    """A running season of one division, every round waiting on results, each round's posted.

    Round *i* has two session results (the race's posted as message 1000+i in the results
    channel, the qualifying's not posted), standings posted as 2000+i and 3000+i in the standings
    channel, and *verdicts* penalties announced as 5000+100*i+k in the verdicts channel.
    """
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-01-01', 'ACTIVE', 1, 'ONGOING')"
        )
        season_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, tier, status) "
            "VALUES (?, 'Division 1', 555, 1, 'ACTIVE')",
            (season_id,),
        )
        division_id = cur.lastrowid
        await seed_team_instances(db, division_id, 7)
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, RESULTS_CHANNEL_ID, STANDINGS_CHANNEL_ID),
        )
        round_ids: list[int] = []
        for index in range(rounds):
            cur = await db.execute(
                "INSERT INTO rounds (division_id, round_number, track_name, scheduled_at, "
                "format, status) VALUES (?, ?, 'Bahrain', ?, 'NORMAL', 'AWAITING_RESULTS')",
                (division_id, index + 1, f"2026-0{index + 1}-01T12:00:00"),
            )
            round_id = cur.lastrowid
            round_ids.append(round_id)
            cur = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name, results_message_id) VALUES (?, ?, 'RACE', 'ACTIVE', '100%', ?)",
                (round_id, division_id, 1000 + index),
            )
            race_session_id = cur.lastrowid
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name) VALUES (?, ?, 'QUALIFYING', 'ACTIVE', '100%')",
                (round_id, division_id),
            )
            cur = await db.execute(
                "INSERT INTO race_session_results "
                "(session_result_id, driver_user_id, team_instance_id, finishing_position) "
                "VALUES (?, 42, 7, 1)",
                (race_session_id,),
            )
            race_result_id = cur.lastrowid
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id, "
                "constructor_standings_message_id) VALUES (?, ?, 42, 1, 25, ?, ?)",
                (round_id, division_id, 2000 + index, 3000 + index),
            )
            for k in range(verdicts):
                message_id = 5000 + 100 * index + k
                await db.execute(
                    "INSERT INTO penalty_records (race_result_id, penalty_type, description, "
                    "justification, applied_by, applied_at, announcement_message_id, "
                    "announcement_message_ids, announcement_channel_id) VALUES "
                    "(?, 'TIME', '5s', 'contact', 'steward', '2026-01-02T00:00:00', ?, ?, ?)",
                    (race_result_id, str(message_id), json.dumps([message_id]),
                     str(VERDICTS_CHANNEL_ID)),
                )
        await db.commit()
    return round_ids


def _every_link(rounds: int, verdicts: int) -> list[str]:
    """The link of every message `_season` posts."""
    links = []
    for index in range(rounds):
        links.append(_link(RESULTS_CHANNEL_ID, 1000 + index))
        links.append(_link(STANDINGS_CHANNEL_ID, 2000 + index))
        links.append(_link(STANDINGS_CHANNEL_ID, 3000 + index))
        links += [_link(VERDICTS_CHANNEL_ID, 5000 + 100 * index + k) for k in range(verdicts)]
    return links


def _link(channel_id: int, message_id: int) -> str:
    return f"https://discord.com/channels/{SERVER_ID}/{channel_id}/{message_id}"


class _FakeChannel:
    """A channel recording each message deleted from it.

    An id in `refuse`, or every id where `refuse_all` is set, will not delete, as for a bot that
    has lost its permissions there. `on_delete`, where set, is awaited before each deletion.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.deleted_messages: list[int] = []
        self.refuse: set[int] = set()
        self.refuse_all = False
        self.on_delete = None

    async def fetch_message(self, message_id: int):
        if message_id in self.deleted_messages:
            raise discord.NotFound(MagicMock(status=404), "Unknown Message")
        message = MagicMock()
        message.id = message_id
        message.author.id = BOT_USER_ID

        async def _delete() -> None:
            if self.on_delete is not None:
                await self.on_delete()
            if self.refuse_all or message_id in self.refuse:
                raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")
            self.deleted_messages.append(message_id)

        message.delete = _delete
        return message

    def get_partial_message(self, message_id: int):
        message = MagicMock()
        message.id = message_id

        async def _delete() -> None:
            await (await self.fetch_message(message_id)).delete()

        message.delete = _delete
        return message

    async def delete(self, reason: str | None = None) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_panel_to_refresh(monkeypatch):
    """The hub has no panel here; turning results off refreshes it as a change of its own."""
    from leaguebot.core.services import hub_service

    monkeypatch.setattr(hub_service, "refresh_panel", AsyncMock(return_value=None))


def _make_cog(
    db_path: str,
    *,
    results_enabled: bool = True,
    attendance_enabled: bool = False,
    season=None,
) -> ModuleCog:
    """A cog whose services are doubles, for the paths that decide whether to warn.

    Its `change_queue` is a double too: these paths must not reach it.
    """
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.change_queue = MagicMock()
    bot.change_queue.ask = AsyncMock()

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _queued_cog(db_path: str) -> ModuleCog:
    """A cog on the bot as the builder makes it: real services, router and change queue on
    *db_path*, "now" pinned, and the league's server with its results, standings and verdicts
    channels in `bot.channels`."""
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

    bot = league_double(db_path)
    bot.user.id = BOT_USER_ID
    bot.module_service = ModuleService(db_path)
    bot.season_service = SeasonService(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.channels = {
        cid: _FakeChannel(cid)
        for cid in (RESULTS_CHANNEL_ID, STANDINGS_CHANNEL_ID, VERDICTS_CHANNEL_ID)
    }
    server = MagicMock()
    server.id = SERVER_ID
    server.get_channel = MagicMock(side_effect=bot.channels.get)
    bot.get_guild = MagicMock(side_effect=lambda sid: server if sid == SERVER_ID else None)
    attach_queue(bot, db_path, now=NOW)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


async def _turn_off(cog: ModuleCog, *, cascade: bool = False):
    """Press the confirmation, as the admin who asked, and run the queue to the end."""
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, cascade_attendance=cascade)
    interaction = member_interaction(cog.bot, user=member(ACTOR_ID))
    await type(view).confirm(view, interaction, view.confirm)
    await run_queue(cog.bot)
    return interaction


async def _discard_every_stop(cog: ModuleCog) -> int:
    """Discard, as a league admin, every job that stops the queue, running the queue on after
    each; gives how many were discarded."""
    discarded = 0
    while await stopped_job(cog.bot.db_path) is not None and discarded < 200:
        await discard_job(cog.bot)
        discarded += 1
    return discarded


def _interaction(user_id: int = ACTOR_ID):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _view_of(interaction):
    for call in interaction.response.send_message.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


async def _lines(bot) -> list[str]:
    """Every log line written: those the log channel took, then those waiting on a retry."""
    return list(bot.log_channel.sent) + await queued_log_lines(bot.db_path)


async def _line_with(bot, part: str) -> str:
    found = [line for line in await _lines(bot) if part in line]
    assert len(found) == 1, (part, await _lines(bot))
    return found[0]


async def _flag(db_path: str) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config",
        )
        row = await cursor.fetchone()
    return row["module_enabled"] if row else None


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries ORDER BY id",
        )
        return [r["change_type"] for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# When the confirmation is owed
# ---------------------------------------------------------------------------


async def test_disabling_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path, results=False)
    cog = _make_cog(db_path, results_enabled=False)
    interaction = _interaction()

    await cog._disable_results(interaction)

    assert "already disabled" in _replied(interaction)
    assert await _audit_types(db_path) == []
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_a_league_with_nothing_at_stake_is_not_asked(tmp_path):
    """Between seasons with attendance off, there is nothing to warn about — and asking
    anyway is how a confirmation stops being read. The change is asked for at once, and the
    manager told it is under way."""
    db_path = await _make_db(tmp_path)
    cog = _queued_cog(db_path)
    interaction = member_interaction(cog.bot, user=member(ACTOR_ID))

    await cog._disable_results(interaction)

    assert _view_of(interaction) is None
    assert _acknowledges(acknowledgement(interaction))
    await run_queue(cog.bot)
    assert await _flag(db_path) == 0


async def test_a_running_season_is_warned_even_with_attendance_already_off(tmp_path):
    """Issue #167. The confirmation used to appear only where there was a cascade to
    announce, so a league with attendance off lost a season's results with no warning at
    all. A reader re-coupling the two conditions would lose this again."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, season=SimpleNamespace(id=3), attendance_enabled=False
    )
    interaction = _interaction()

    await cog._disable_results(interaction)

    assert _view_of(interaction) is not None
    assert await _flag(db_path) == 1  # nothing written yet
    cog.bot.change_queue.ask.assert_not_awaited()


async def test_a_cascade_is_warned_about_between_seasons(tmp_path):
    """The other half of the same independence: no season running, but attendance is on."""
    db_path = await _make_db(tmp_path, attendance=True)
    cog = _make_cog(db_path, season=None, attendance_enabled=True)
    interaction = _interaction()

    await cog._disable_results(interaction)

    assert _view_of(interaction) is not None


async def test_nothing_is_written_until_the_league_confirms(tmp_path):
    """The warning is shown *before* anything irreversible, so a manager who closes the
    message loses nothing: no change is even asked for."""
    db_path = await _make_db(tmp_path, attendance=True)
    cog = _make_cog(db_path, season=SimpleNamespace(id=3), attendance_enabled=True)

    await cog._disable_results(_interaction())

    cog.bot.change_queue.ask.assert_not_awaited()
    assert await _audit_types(db_path) == []


# ---------------------------------------------------------------------------
# Applying the disable
# ---------------------------------------------------------------------------


async def test_the_flag_goes_down_in_the_save_that_erases_the_results(tmp_path):
    """So nothing the erasure disturbs can post again on its way out — the module-output
    rule, applied to the act of switching the module off. By the time the first message is
    taken down, the flag is down, the season's rows are gone and its rounds are closed: one
    save, so a stop can never leave one without the others."""
    db_path = await _make_db(tmp_path)
    (round_id,) = await _season(db_path, rounds=1)
    cog = _queued_cog(db_path)
    seen: list[tuple] = []

    async def _observe() -> None:
        if seen:
            return
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM session_results")
            sessions = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
            status = (await cursor.fetchone())[0]
        seen.append((await _flag(db_path), sessions, status))

    for channel in cog.bot.channels.values():
        channel.on_delete = _observe

    await _turn_off(cog)

    assert seen == [(0, 0, "FINAL")]


async def test_a_disable_between_seasons_still_takes_all_three_steps(tmp_path):
    """The erase and the closing of the rounds simply find nothing to do, which is why they
    are unconditional: the switch-off still lands, and the change finishes."""
    db_path = await _make_db(tmp_path)
    cog = _queued_cog(db_path)

    await _turn_off(cog)

    assert await _flag(db_path) == 0
    assert [row["state"] for row in await change_rows(db_path)
            if row["kind"] == "module.off:results"] == ["DONE"]
    line = await _line_with(cog.bot, "/module disable results")
    divider = "\n" + "\u2015" * 36
    assert line.removesuffix(divider).endswith("| /module disable results | Success")


async def test_a_purged_season_is_audited_separately(tmp_path):
    """The disable and the destruction are two different facts, and a league reading its
    log needs to see what was deleted as well as that the module went."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4)
    cog = _queued_cog(db_path)

    await _turn_off(cog)

    types = await _audit_types(db_path)
    assert "MODULE_DISABLE" in types
    assert "RESULTS_SEASON_PURGED" in types


async def test_a_disable_that_destroyed_nothing_writes_no_purge_entry(tmp_path):
    """Nothing was destroyed, so an entry saying so would be a record of an event that did
    not happen."""
    db_path = await _make_db(tmp_path)
    cog = _queued_cog(db_path)

    await _turn_off(cog)

    types = await _audit_types(db_path)
    assert "MODULE_DISABLE" in types
    assert "RESULTS_SEASON_PURGED" not in types


async def test_the_reply_counts_what_was_destroyed(tmp_path):
    """A manager who has just confirmed needs to see the scale of what happened."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog)

    replied = updated_reply(interaction)
    assert "This season's results are gone" in replied
    assert "8 session result(s)" in replied


async def test_the_reply_says_what_survived_the_purge(tmp_path):
    """The points configurations are kept, which a manager would otherwise go looking for. The
    verdicts are not: they go with the results (decided 2026-09-21, #189), and a reply still
    saying they remain would send the manager looking for them."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog)

    replied = updated_reply(interaction)
    assert "Verdicts already announced remain" not in replied
    assert "Points configurations and division channels are kept" in replied


async def test_the_reply_counts_the_verdicts_removed(tmp_path):
    """Beside the results and standings messages, so the manager sees the decisions went too;
    and the closing line, written once the removals were tried, counts them as well."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4, verdicts=5)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog)

    replied = updated_reply(interaction)
    assert "12 results and standings message(s) and 20 verdict(s) removed" in replied
    closing = await _line_with(cog.bot, "| Messages removed")
    assert "verdicts removed: 20" in closing


@pytest.mark.xfail(strict=True, reason=STOPS)
async def test_the_reply_links_every_message_left_standing(tmp_path):
    """**What the bot could not remove is named, with a link** (decided 2026-09-21, #189).

    Its record went with the season, so the reply and the log channel's closing line are the
    only places left that can tell a manager where it is. Each of the two refused removals stops
    the queue until a league admin discards it.
    """
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4)
    cog = _queued_cog(db_path)
    cog.bot.channels[RESULTS_CHANNEL_ID].refuse = {1000, 1001}
    links = [_link(RESULTS_CHANNEL_ID, 1000), _link(RESULTS_CHANNEL_ID, 1001)]

    interaction = await _turn_off(cog)
    assert await _discard_every_stop(cog) == 2

    replied = updated_reply(interaction)
    assert "2 message(s) could not be removed" in replied
    closing = await _line_with(cog.bot, "| Messages removed")
    for link in links:
        assert link in replied
        assert link in closing


async def test_nothing_left_standing_says_nothing_of_it(tmp_path):
    """A warning over nothing would send a manager looking for messages that are gone."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog)

    assert "could not be removed" not in updated_reply(interaction)
    assert "left standing" not in await _line_with(cog.bot, "| Messages removed")


@pytest.mark.xfail(strict=True, reason=STOPS)
async def test_a_long_list_is_split_across_replies(tmp_path):
    """A season's worth of links outruns Discord's 2,000 characters, and one reply that long
    would be refused outright — the manager would learn nothing at all. The update of the
    acknowledgement carries the first part, and follow-ups the rest. Every removal is refused,
    and each stops the queue until a league admin discards it."""
    db_path = await _make_db(tmp_path)
    await _season(db_path, rounds=4, verdicts=12)
    cog = _queued_cog(db_path)
    for channel in cog.bot.channels.values():
        channel.refuse_all = True
    links = _every_link(4, 12)

    interaction = await _turn_off(cog)
    assert await _discard_every_stop(cog) > 1
    assert await stopped_job(db_path) is None

    sent = [
        str(call.args[0] if call.args else call.kwargs.get("content", ""))
        for call in interaction.edit_original_response.await_args_list
        + interaction.followup.send.await_args_list
    ]
    assert len(links) == 60
    assert interaction.followup.send.await_count >= 1
    assert all(len(chunk) <= 2000 for chunk in sent)
    assert all(link in "\n".join(sent) for link in links)


async def test_a_disable_between_seasons_reports_no_destruction(tmp_path):
    """There was none, and a count of zeroes would read as something having gone wrong."""
    db_path = await _make_db(tmp_path)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog)

    replied = updated_reply(interaction)
    assert "✅ Results & Standings module disabled." in replied
    assert "results are gone" not in replied


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


async def test_the_cascade_disables_attendance_and_says_so(tmp_path):
    db_path = await _make_db(tmp_path, attendance=True)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog, cascade=True)

    replied = updated_reply(interaction)
    assert "Attendance module disabled with it" in replied
    assert "check-in and attendance channels have been cleared" in replied


async def test_the_cascade_says_what_attendance_keeps(tmp_path):
    """Its timings, penalties and thresholds survive — so a league re-enabling it later
    does not have to configure it again."""
    db_path = await _make_db(tmp_path, attendance=True)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog, cascade=True)

    assert "timings, penalties and thresholds are kept" in updated_reply(interaction)


async def test_no_cascade_where_attendance_is_already_off(tmp_path):
    """Asked for, but there is nothing to cascade to — and claiming otherwise would tell a
    league a module went off that was never on."""
    db_path = await _make_db(tmp_path, attendance=False)
    cog = _queued_cog(db_path)

    interaction = await _turn_off(cog, cascade=True)

    replied = updated_reply(interaction)
    assert "✅ Results & Standings module disabled." in replied
    assert "Attendance module disabled with it" not in replied


# ---------------------------------------------------------------------------
# The confirmation view
# ---------------------------------------------------------------------------


async def test_only_the_admin_who_asked_may_confirm(tmp_path):
    """A second admin pressing another's confirmation would destroy a season's results
    they had not been asked about."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, cascade_attendance=False)
    interaction = _interaction(user_id=OTHER_ADMIN)

    await type(view).confirm(view, interaction, MagicMock())

    assert await _flag(db_path) == 1
    cog.bot.change_queue.ask.assert_not_awaited()
