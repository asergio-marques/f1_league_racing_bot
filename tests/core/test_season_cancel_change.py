"""Cancelling a season on the change queue (#439, slice 5).

`/season cancel` checks its confirmation word and finds the confirmed season at the press, then
asks the change queue to cancel it, answering at once with the job it begins with. The change is
checked when asked and again when it runs, in today's words: no season being raced, the season no
longer ongoing, an amendment open, a submission holding accepted results; and, only when asked,
any end of the season already in hand. Its jobs: `unarm` removes the timed work of every round of
every division; `apply`, one save, records the cancellation (each empty open submission closed,
the uncommitted placements discarded, the raced rounds awaiting their verdicts made final, every
division not cancelled cancelled with its rounds that may still be cancelled), the season itself
left ongoing; each closed submission's channel deleted; for each division still running, its
check-in notice, each call taken down, its forecast and results notes and its calendar; each real
driver's roles taken back; the signup window closed; the forecasts posted under test mode cleared;
then one save, `end`, records the history marked cancelled, runs the driver pass, switches test
mode off and marks the season cancelled, last; then each driver the pass reaches has their signup
channel's notice, its lock or their driver role as jobs of their own, and the portraits of the
drivers deleted are discarded. The saved test-mode state is kept. Every failure stops the queue; a
Discard is named in the reply and beneath the closing line, which stays `| Success`.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`ongoing_league`, with "now" pinned: Pro's round 1 final, round 2 awaiting its verdicts, rounds 3
and 4 not run, round 3's check-in call standing and Pro's calendar posted; Am's four rounds not run
and its calendar never posted. Today `/season cancel` cancels the season at the press, catching
what fails, so every test of the queue is marked to fail until the build; the press's refusals in
today's words pass as they stand.
"""
from __future__ import annotations

import importlib
import json
import re
from contextlib import ExitStack
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    AM,
    AM_ROLE,
    CALL_MESSAGES,
    CHARLES,
    DIVISIONS,
    DRIVER_ROLE,
    FERRARI_ROLE,
    LEWIS,
    MAX,
    MCLAREN_ROLE,
    PRO,
    PRO_ROLE,
    SEASON_ABORT_KIND,
    SEASON_CANCEL_KIND,
    SEASON_COMPLETE_KIND,
    SEASON_ID,
    SIGNING_UP,
    SUBMISSION_CHANNEL,
    TEST_DRIVER,
    UNASSIGNED,
    WIND_DOWN_KIND,
    accept_session,
    approved_unplaced,
    backup_saved,
    cancel_season,
    complete_season,
    driver_state,
    ongoing_league,
    open_submission,
    profile_id,
    reply,
    round_id,
    season_end_changes,
    signing_up,
    window_open,
    wizard_state,
    wizards_recovered,
)


PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
R2, R3, R4 = round_id(PRO, 2), round_id(PRO, 3), round_id(PRO, 4)
#: The message Am's calendar was posted as, where `_am_calendar_posted` says so.
AM_CALENDAR_MESSAGE = 6002
CANCELLED = "✅ Season cancelled."
SUCCESS = "Admin (`<@77>`) | /season cancel | Success"
REFUSAL = "⛔ `/season cancel` refused for Admin (`<@77>`) — "
NOT_NOTIFIED = "⚠️ **Not notified**"
NOT_EVERYTHING = "⚠️ **Not everything could be done**"
ACK = (
    "⏳ Cancelling season 3. This message will be updated when it is done; if it takes longer, "
    "the log channel will say so. It begins with job #"
)
WORD = "❌ Type exactly `CONFIRM` in the `confirm` field to proceed."
NO_SEASON = (
    "❌ No season is being raced, so there is none to cancel. A season whose placements are yet "
    "to be confirmed is abandoned with `/season abort`."
)
NOT_ONGOING = (
    "❌ Every division of this season is done. Complete it with `/season complete` instead."
)
AMENDED = (
    "❌ Cannot cancel the season — round 2 of **Pro** is being amended in <#8200>. Finish or "
    "cancel it first: cancelling writes every driver's history from the standings, which would "
    "carry its corrections before they are approved."
)


def _accepted(number: int) -> str:
    return (f"❌ Cannot cancel the season — results have already been accepted in the submission "
            f"channel of round {number} of **Pro** (<#{SUBMISSION_CHANNEL}>), and cancelling "
            f"would lose them.")


UNARM_DISCARDED = "Nothing was cancelled: season 3 stands as it was. Run `/season cancel` again."
SAVE_DISCARDED = (
    "Nothing was cancelled, but the rounds of season 3 no longer have their timed work: run "
    "`/season cancel` again."
)
END_DISCARDED = (
    "Season 3's divisions and rounds are cancelled, but the season itself is not: the save that "
    "records its end was discarded. Run `/season cancel` again to finish it; if the season has "
    "since moved to pending completion, complete it with `/season complete`."
)
ROLES_KEPT = (
    "<@101> — their roles of season 3 could not be taken back. Remove their division and team "
    "roles and the driver role by hand."
)
NOTICE_DISCARDED = f"<@{SIGNING_UP}> — their signup channel was closed without its notice"
WINDOW_OPEN = "The signup window could not be closed. Close it with `/signup close`."
FORECASTS_KEPT = (
    "The forecasts posted under test mode could not be cleared. Delete them by hand from each "
    "forecast channel."
)
SIGNUP_KEPT = f"<@{SIGNING_UP}> — their signup channel could not be closed. Delete it by hand."
DRIVER_ROLE_KEPT = (
    f"<@{UNASSIGNED}> — the driver role could not be taken back. Remove it by hand."
)
CHECKIN_R3 = (
    "\n  check-in, Pro, Round 3 (Silverstone):"
    "\n    accepted: `<@101>`"
    "\n    tentative: none"
    "\n    declined: none"
    "\n    no answer: `<@102>`"
)
#: Each module's notice of a season's cancellation, in the season's words.
SEASON_CHECKIN = (
    "📢 **Season Cancelled**\nThe season has been cancelled. There are no further check-ins to "
    "answer."
)
SEASON_FORECAST = "📢 **Season Cancelled**\nNo further weather forecasts will be posted this season."
SEASON_RESULTS = "📢 **Season Cancelled**\nNo further results will be posted this season."
#: 2.11's refusal, for each other kind of a season's end in hand.
IN_HAND = {
    SEASON_COMPLETE_KIND: (
        {"season_id": SEASON_ID, "season_number": 3},
        "⏳ Season 3 is being completed (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel.",
    ),
    SEASON_ABORT_KIND: (
        {"season_id": SEASON_ID},
        "⏳ The season being set up is being aborted (job #{job}), so this cannot be done until "
        "that is finished. If it has stopped, press Retry or Discard on its notice in the log "
        "channel.",
    ),
}
ALREADY = (
    "⏳ Season 3 is already being cancelled (job #{job}). If it has stopped, press Retry or "
    "Discard on its notice in the log channel."
)
#: Every channel a division's notices and calendar are posted in.
_POSTS = tuple(cid for chans in (PRO_CH, AM_CH)
               for cid in (chans.checkin, chans.forecast, chans.results, chans.calendar))


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _asked(league: Any, **kwargs: Any) -> Any:
    interaction = await cancel_season(league, **kwargs)
    assert not league.errors, league.errors
    return interaction


async def _change(league: Any) -> dict[str, Any]:
    rows = await season_end_changes(league, SEASON_CANCEL_KIND)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    """The cancellation's jobs, in order, each with its payload read back."""
    jobs = await step_rows(league.db_path, (await _change(league))["id"])
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


def _who(job: dict[str, Any]) -> tuple[str, int | None]:
    payload = job["payload"]
    key = payload.get("user_id", payload.get("division_id"))
    return job["name"], int(key) if key is not None else None


async def _names(league: Any) -> list[tuple[str, int | None]]:
    return [_who(job) for job in await _jobs(league)]


async def _run_through(league: Any, name: str, key: int | None = None) -> None:
    """Run the queue a job at a time until the cancellation's job *name* (for *key*) is done."""
    for _ in range(200):
        if any(_who(job) == (name, key) if key is not None else job["name"] == name
               for job in await _jobs(league) if job["done_at"]):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the cancellation's job {name} never finished: {await _jobs(league)}")


async def _stopped_at(league: Any) -> tuple[str, int | None] | None:
    job = await stopped_job(league.db_path)
    if job is None:
        return None
    job["payload"] = json.loads(job["payload"] or "{}")
    return _who(job)


async def _stopped_name(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


async def _status(league: Any) -> str:
    return (await league.season())["status"]


async def _divisions(league: Any) -> dict[int, str]:
    rows = await league.rows("SELECT id, status FROM divisions WHERE season_id = ?", SEASON_ID)
    return {row["id"]: row["status"] for row in rows}


async def _rounds(league: Any) -> dict[int, str]:
    rows = await league.rows("SELECT id, status FROM rounds WHERE division_id IN (?, ?)", PRO, AM)
    return {row["id"]: row["status"] for row in rows}


async def _history(league: Any) -> list[tuple[str, str, int]]:
    rows = await league.rows(
        "SELECT discord_user_id, division_name, cancelled FROM driver_history_entries "
        "WHERE season_number = 3 ORDER BY discord_user_id, division_name"
    )
    return [(row["discord_user_id"], row["division_name"], row["cancelled"]) for row in rows]


async def _test_mode(league: Any) -> int:
    return (await league.rows("SELECT test_mode_active FROM server_configs"))[0][
        "test_mode_active"
    ]


async def _closed(league: Any) -> dict[int, int]:
    rows = await league.rows("SELECT round_id, closed FROM round_submission_channels")
    return {row["round_id"]: row["closed"] for row in rows}


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if SUCCESS in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if line.startswith("⛔ ")]


def _bullets(text: str, heading: str) -> list[str]:
    """The bullets of the reply's section under *heading*, in order, up to the next heading."""
    assert heading in text, text
    section = text.split(heading, 1)[1].split("\n⚠️ ", 1)[0]
    return [line.strip()[2:] for line in section.splitlines() if line.strip().startswith("• ")]


def _not_notified(text: str) -> list[str]:
    return _bullets(text, NOT_NOTIFIED)


def _not_done(text: str) -> list[str]:
    return _bullets(text, NOT_EVERYTHING)


def _logged(text: str) -> str:
    """*text* as the log channel carries it: every mention in backticks, naming without
    notifying."""
    return re.sub(r"(<@&?\d+>)", r"`\1`", text)


def _posted(league: Any) -> list[list[str]]:
    return [league.texts(cid) for cid in _POSTS]


def _modules() -> list[Any]:
    found = []
    for name in ("leaguebot.__main__",
                 "leaguebot.core.services.season_service",
                 "leaguebot.core.services.season_end_service",
                 "leaguebot.core.services.season_lifecycle_service",
                 "leaguebot.core.services.season_end_changes",
                 "leaguebot.core.services.cancellation_changes",
                 "leaguebot.core.services.test_mode_service",
                 "leaguebot.core.services.test_roster_service",
                 "leaguebot.weather.services.forecast_cleanup_service"):
        try:
            found.append(importlib.import_module(name))
        except ImportError:
            continue
    return found


def _failing(name: str) -> ExitStack:
    """*name* raises, as a fault of the database would, wherever core or the change type keeps
    it."""
    stack = ExitStack()
    new = AsyncMock(side_effect=RuntimeError("disk I/O error"))
    for module in _modules():
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _flush_failing(monkeypatch: Any, failing: dict[str, bool]) -> None:
    """Weather's flush of the forecasts posted under test mode raises while *failing* says so;
    patched before the league is built, so the builder's hook reaches it however it binds it."""
    from leaguebot.weather.services import forecast_cleanup_service

    flushing = forecast_cleanup_service.flush_pending_deletions

    async def _flush(bot: Any) -> None:
        if failing["now"]:
            raise RuntimeError("the forecasts could not be read")
        await flushing(bot)

    for module in _modules():
        if hasattr(module, "flush_pending_deletions"):
            monkeypatch.setattr(module, "flush_pending_deletions", _flush)


def _unarming_fails(league: Any) -> None:
    league.bot.scheduler_service.cancel_round = MagicMock(
        side_effect=RuntimeError("the job store is locked")
    )


def _unarming_works(league: Any) -> None:
    league.bot.scheduler_service.cancel_round = MagicMock(side_effect=league.unarmed.append)


async def _formers(league: Any, *user_ids: int) -> None:
    """Each of *user_ids* is a former driver, so that the driver pass keeps them."""
    for user_id in user_ids:
        await league.write(
            "UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?", str(user_id)
        )


async def _awaiting_results(league: Any, rid: int) -> None:
    await league.write(
        "UPDATE rounds SET status = 'AWAITING_RESULTS', scheduled_at = ? WHERE id = ?",
        (league.clock.now - timedelta(hours=2)).replace(tzinfo=None).isoformat(), rid,
    )


async def _empty_submission(league: Any) -> None:
    """Pro's round 3 waits for its results, its submission open in 690 with nothing accepted."""
    await _awaiting_results(league, R3)
    await open_submission(league, R3)


async def _am_calendar_posted(league: Any) -> None:
    await league.write("UPDATE divisions SET calendar_message_id = ? WHERE id = ?",
                       str(AM_CALENDAR_MESSAGE), AM)
    league.channel(AM_CH.calendar).seed(AM_CALENDAR_MESSAGE, "Am's calendar")


async def _amendment_open(league: Any) -> None:
    await league.write(
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, 8200, '[\"FEATURE_RACE\"]', ?)",
        R2, league.clock.now.isoformat(),
    )


async def _seed_change(league: Any, kind: str, payload: dict[str, Any], *,
                       stopped: bool) -> int:
    """A change of *kind* with *payload* waiting on the queue, its first job not done, stopping
    the queue where *stopped*; behind an earlier change of three jobs long done, so that the
    job's number and its change's id differ. Gives the job's number."""
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('hub.refresh', 'hub.refresh', '{}', 'BOT', 'DONE', 'refreshing the hub')"
        )
        for position in range(3):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, done_at) "
                "VALUES (?, ?, 'refresh', '2026-10-05T11:00:00+00:00')",
                (cursor.lastrowid, position),
            )
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'MEMBER', 'QUEUED', 'a change of the test')",
            (kind, f"{kind}:{json.dumps(payload, sort_keys=True)}", json.dumps(payload)),
        )
        change_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, tries, "
            "failing_since, last_failure) VALUES (?, 0, 'first', '{}', ?, ?, ?)",
            (change_id, 1 if stopped else 0,
             "2026-10-05T12:00:00+00:00" if stopped else None,
             "OperationalError" if stopped else None),
        )
        job = cursor.lastrowid
        await db.commit()
    assert job is not None and job != change_id
    return job


def _forbidden() -> discord.HTTPException:
    return http_error(discord.Forbidden, status=403, text="Missing Permissions")


# ── Defect 5, and the cancellation's windows ────────────────────────────────────────


async def test_a_signup_window_that_cannot_be_closed_stops_the_queue(tmp_path):
    league = await ongoing_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("close_window", None)
    assert await _status(league) == "ACTIVE"
    assert await window_open(league)
    assert await _history(league) == []

    league.close_fails = None
    await retry_job(league.bot)
    assert not await window_open(league)
    assert await _status(league) == "CANCELLED"


@pytest.mark.parametrize("where", ["the flush", "the test drivers' deletion"])
async def test_test_mode_that_cannot_be_switched_off_stops_the_queue(tmp_path, monkeypatch, where):
    failing = {"now": where == "the flush"}
    _flush_failing(monkeypatch, failing)
    league = await ongoing_league(tmp_path, test_mode=True)
    await _asked(league)

    if where == "the flush":
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("flush_forecasts", None)
    else:
        with _failing("clear_all_test_drivers_on"):
            await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []
    assert await driver_state(league, TEST_DRIVER) == "ASSIGNED"
    assert await _test_mode(league) == 1

    failing["now"] = False
    await retry_job(league.bot)
    assert await _status(league) == "CANCELLED"
    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0


async def test_a_role_discord_will_not_take_back_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True, held=True)
    league.revoke_fails[LEWIS] = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("revoke_roles", LEWIS)
    assert league.texts(PRO_CH.checkin)
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []

    del league.revoke_fails[LEWIS]
    await discard_job(league.bot)
    assert await _status(league) == "CANCELLED"
    assert CANCELLED in reply(interaction)
    assert ROLES_KEPT in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {_logged(ROLES_KEPT)}" in line


async def test_a_notice_discord_refuses_stops_the_queue_and_once_discarded_is_named_not_notified(
    tmp_path,
):
    league = await ongoing_league(tmp_path, weather=True, held=True)
    league.channel(PRO_CH.forecast).send_fails = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("notify_forecast", PRO)
    assert league.revoked == {}
    assert await _status(league) == "ACTIVE"

    await discard_job(league.bot)

    named = ("**Pro** — forecast channel: the notice could not be posted, and a league admin "
             "discarded it")
    assert CANCELLED in reply(interaction)
    assert named in _not_notified(reply(interaction))
    assert len(league.texts(PRO_CH.results)) == 1
    [line] = _success_lines(league)
    assert f"\n  not notified: {named}" in line
    assert await _status(league) == "CANCELLED"


async def test_a_notice_channel_deleted_stops_the_queue_and_goes_through_once_it_is_set_again(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    league.remove_channel(PRO_CH.checkin)
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("notify_checkin", PRO)
    assert await _status(league) == "ACTIVE"

    league.restore_channel(PRO_CH.checkin)
    await retry_job(league.bot)

    assert len(league.texts(PRO_CH.checkin)) == 1
    assert CANCELLED in reply(interaction)
    assert NOT_NOTIFIED not in reply(interaction)
    assert await _status(league) == "CANCELLED"


async def test_a_call_message_discord_will_not_delete_stops_the_queue_keeping_the_call_s_record(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_name(league) == "take_down_call"
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1

    league.undeletable.clear()
    await retry_job(league.bot)

    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert CANCELLED in reply(interaction)
    assert await _status(league) == "CANCELLED"


async def test_a_calendar_discord_refuses_stops_the_queue_and_puts_nothing_on_the_old_retry_queue(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    league.channel(PRO_CH.calendar).send_fails = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == ("post_calendar", PRO)
    assert await league.rows("SELECT * FROM pending_messages") == []
    assert _success_lines(league) == []

    league.channel(PRO_CH.calendar).send_fails = None
    await retry_job(league.bot)
    assert len(league.texts(PRO_CH.calendar)) == 1
    assert CANCELLED in reply(interaction)


async def test_a_submission_channel_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _empty_submission(league)
    league.channel(SUBMISSION_CHANNEL).delete_fails = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_name(league) == "delete_channel"
    assert await _closed(league) == {R3: 1}
    assert await _divisions(league) == {PRO: "CANCELLED", AM: "CANCELLED"}
    assert await _status(league) == "ACTIVE"

    await discard_job(league.bot)

    named = (f"**Pro** — results submission channel of round 3: it could not be deleted, and a "
             f"league admin discarded it; delete <#{SUBMISSION_CHANNEL}> by hand")
    assert CANCELLED in reply(interaction)
    assert named in _not_notified(reply(interaction))
    [line] = _success_lines(league)
    assert f"\n  not notified: {named}" in line
    assert await _status(league) == "CANCELLED"


async def test_a_stop_part_way_is_finished_on_restart_telling_nobody_twice(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, attendance=True)
    await _asked(league)
    await _run_through(league, "notify_checkin", AM)

    await league.restart()
    await run_queue(league.bot)

    for chans in (PRO_CH, AM_CH):
        assert len(league.texts(chans.checkin)) == 1
        assert len(league.texts(chans.forecast)) == 1
        assert len(league.texts(chans.results)) == 1
    assert len(league.texts(PRO_CH.calendar)) == 1
    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert await _status(league) == "CANCELLED"
    assert len(_success_lines(league)) == 1


async def test_the_admin_is_told_at_once_naming_the_job_and_the_reply_is_updated(tmp_path):
    league = await ongoing_league(tmp_path)
    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    interaction.response.defer.assert_not_awaited()
    assert await _status(league) == "ACTIVE"
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}

    await run_queue(league.bot)
    assert CANCELLED in reply(interaction)


async def test_nothing_is_announced_until_the_cancellation_is_recorded(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, attendance=True, held=True)
    await _asked(league)

    with _failing("cancel_season_divisions_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("apply", None)

    assert _posted(league) == [[]] * len(_POSTS)
    assert set(CALL_MESSAGES) <= set(league.channel(PRO_CH.checkin).messages)
    assert league.revoked == {}
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert await _history(league) == []


async def test_the_history_the_driver_pass_test_mode_and_the_season_s_cancellation_are_saved_together(
    tmp_path,
):
    league = await ongoing_league(tmp_path, test_mode=True)
    await _formers(league, LEWIS)
    await _asked(league)

    with _failing("cancel_season_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    assert await _divisions(league) == {PRO: "CANCELLED", AM: "CANCELLED"}
    assert await _history(league) == []
    for user_id in (LEWIS, MAX, CHARLES, TEST_DRIVER):
        assert await driver_state(league, user_id) == "ASSIGNED"
    assert await _test_mode(league) == 1
    assert await _status(league) == "ACTIVE"

    await retry_job(league.bot)
    assert ("101", "Pro", 1) in await _history(league)
    assert await driver_state(league, LEWIS) == "NOT_SIGNED_UP"
    assert await driver_state(league, MAX) is None
    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0
    assert await _status(league) == "CANCELLED"


async def test_a_session_accepted_after_the_check_is_refused_by_the_first_save_writing_nothing(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    await _empty_submission(league)
    interaction = await _asked(league)
    _unarming_fails(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("unarm", None)
    await accept_session(league, R3)
    before = await _rounds(league)

    _unarming_works(league)
    await retry_job(league.bot)

    assert _accepted(3) in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert await _rounds(league) == before
    assert await _closed(league) == {R3: 0}
    assert [cid for kind, cid, _ in league.events if kind == "delete_channel"] == []
    assert _posted(league) == [[]] * len(_POSTS)
    assert await _status(league) == "ACTIVE"


@pytest.mark.parametrize("how", [
    pytest.param("waiting", id="waiting behind a stopped job"),
    pytest.param("stopped", id="stopped at its own job"),
])
async def test_a_second_cancellation_is_refused_at_once_naming_the_job(tmp_path, how):
    league = await ongoing_league(tmp_path)
    if how == "waiting":
        await _seed_change(league, "hub.refresh", {}, stopped=True)
        await _asked(league)
        job = next(j["id"] for j in await _jobs(league) if j["done_at"] is None)
    else:
        _unarming_fails(league)
        await _asked(league)
        await run_queue(league.bot)
        job = (await stopped_job(league.db_path))["id"]
    refusals = len(_refusal_lines(league))

    second = await _asked(league)

    assert reply(second) == ALREADY.format(job=job)
    assert len(_refusal_lines(league)) == refusals + 1
    assert len(await season_end_changes(league)) == 1


# ── The checks ──────────────────────────────────────────────────────────────────────


async def _nothing(_league: Any) -> None:
    return None


async def _no_season(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", SEASON_ID)


async def _pending_completion(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?", SEASON_ID)


async def _session_accepted(league: Any) -> None:
    await _empty_submission(league)
    await accept_session(league, R3)


async def _round_in_review(league: Any) -> None:
    await open_submission(league, R2)
    await league.write(
        "UPDATE round_submission_channels SET in_penalty_review = 1 WHERE round_id = ?", R2
    )
    await accept_session(league, R2)


#: Each refusal at the press: what sets it up, the confirmation word, and today's reply.
_PRESS_REFUSALS = {
    "the word": (_nothing, "confirm", WORD),
    "no season": (_no_season, "CONFIRM", NO_SEASON),
    "pending completion": (_pending_completion, "CONFIRM", NOT_ONGOING),
    "an amendment open": (_amendment_open, "CONFIRM", AMENDED),
    "an accepted submission": (_session_accepted, "CONFIRM", _accepted(3)),
    "a round in its review": (_round_in_review, "CONFIRM", _accepted(2)),
}


@pytest.mark.parametrize("case", sorted(_PRESS_REFUSALS))
async def test_the_command_is_refused_at_once_in_today_s_words(tmp_path, case):
    setup, word, text = _PRESS_REFUSALS[case]
    league = await ongoing_league(tmp_path, attendance=True)
    await setup(league)
    rounds = await _rounds(league)

    interaction = await _asked(league, confirm=word)

    assert reply(interaction) == text
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league) == []
    assert league.unarmed == []
    assert await _rounds(league) == rounds
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert _posted(league) == [[]] * len(_POSTS)
    assert await _history(league) == []


async def _season_moved_on(league: Any) -> None:
    await _pending_completion(league)


async def _accept_r3(league: Any) -> None:
    await accept_session(league, R3)


#: Each refusal found when the cancellation runs: what changes while it waits, and the reply.
_RUN_REFUSALS = {
    "an amendment opened": (_amendment_open, AMENDED),
    "the season moved to pending completion": (_season_moved_on, NOT_ONGOING),
    "a session accepted": (_accept_r3, _accepted(3)),
}


@pytest.mark.parametrize("case", sorted(_RUN_REFUSALS))
async def test_a_refusal_found_when_the_cancellation_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, case,
):
    change, text = _RUN_REFUSALS[case]
    league = await ongoing_league(tmp_path, attendance=True)
    await _empty_submission(league)
    interaction = await _asked(league)
    await change(league)

    await run_queue(league.bot)

    assert text in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "REFUSED"
    assert league.unarmed == []
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert await _closed(league) == {R3: 0}
    assert _posted(league) == [[]] * len(_POSTS)
    assert await _status(league) == "ACTIVE"


@pytest.mark.parametrize("kind", [SEASON_COMPLETE_KIND, SEASON_ABORT_KIND])
@pytest.mark.parametrize("stopped", [False, True], ids=["waiting", "stopped"])
async def test_a_cancellation_is_refused_at_once_while_another_end_of_the_season_is_in_hand(
    tmp_path, kind, stopped,
):
    league = await ongoing_league(tmp_path)
    payload, text = IN_HAND[kind]
    job = await _seed_change(league, kind, payload, stopped=stopped)

    interaction = await _asked(league)

    assert reply(interaction) == text.format(job=job)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league, SEASON_CANCEL_KIND) == []
    assert league.unarmed == []
    assert await _status(league) == "ACTIVE"


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_jobs_run_in_order(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, attendance=True, test_mode=True,
                                  held=True)
    await _am_calendar_posted(league)
    await _empty_submission(league)
    await _asked(league)

    await run_queue(league.bot)

    names = [name for name, _key in await _names(league)]
    jobs = [pair for pair in await _names(league) if pair[0] != "close_window"]
    assert [name for name, _key in jobs[:3]] == ["unarm", "apply", "delete_channel"]
    assert jobs[3:12] == [
        ("notify_checkin", PRO), ("take_down_call", PRO), ("notify_forecast", PRO),
        ("notify_results", PRO), ("post_calendar", PRO),
        ("notify_checkin", AM), ("notify_forecast", AM), ("notify_results", AM),
        ("post_calendar", AM),
    ]
    assert jobs[12:15] == [("revoke_roles", LEWIS), ("revoke_roles", MAX),
                           ("revoke_roles", CHARLES)]
    assert jobs[15:17] == [("flush_forecasts", None), ("end", None)]
    assert names[-2:] == ["discard_portraits", "close"]
    between = names[names.index("end") + 1:names.index("discard_portraits")]
    assert set(between) <= {"signup_notice", "close_signup", "take_driver_role"}
    assert "discard_backup" not in names
    assert await _status(league) == "CANCELLED"


async def test_every_round_of_every_division_loses_its_timed_work_first(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _run_through(league, "unarm")
    assert sorted(league.unarmed) == sorted(await _rounds(league))
    league.bot.scheduler_service.cancel_season_end.assert_called_once()
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}

    await run_queue(league.bot)
    assert (await _jobs(league))[0]["name"] == "unarm"
    assert await _status(league) == "CANCELLED"


async def test_every_division_still_running_is_told_in_the_season_s_words(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, attendance=True)
    await _asked(league)

    await run_queue(league.bot)

    for chans in (PRO_CH, AM_CH):
        [checkin] = league.texts(chans.checkin)
        assert checkin.endswith(SEASON_CHECKIN)
        assert league.texts(chans.forecast) == [SEASON_FORECAST]
        assert league.texts(chans.results) == [SEASON_RESULTS]
    assert await _status(league) == "CANCELLED"


async def _am_cancelled(league: Any) -> None:
    await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", AM)


async def _am_finished(league: Any) -> None:
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)


@pytest.mark.parametrize("how", ["cancelled", "finished"])
async def test_a_division_already_cancelled_or_finished_is_not_told(tmp_path, how):
    league = await ongoing_league(tmp_path, weather=True, attendance=True)
    await _am_calendar_posted(league)
    await (_am_cancelled if how == "cancelled" else _am_finished)(league)
    await _asked(league)

    await run_queue(league.bot)

    assert [league.texts(cid) for cid in (AM_CH.checkin, AM_CH.forecast, AM_CH.results,
                                          AM_CH.calendar)] == [[], [], [], []]
    assert not [pair for pair in await _names(league)
                if pair[1] == AM and pair[0] != "revoke_roles"]
    assert league.texts(PRO_CH.checkin) and league.texts(PRO_CH.calendar)
    assert await _status(league) == "CANCELLED"


async def test_the_rounds_called_off_are_drawn_cancelled_on_each_calendar(tmp_path):
    league = await ongoing_league(tmp_path)
    await _am_calendar_posted(league)
    await _asked(league)

    await run_queue(league.bot)

    [pro] = league.texts(PRO_CH.calendar)
    [am] = league.texts(AM_CH.calendar)
    assert pro.count("— Cancelled") == 2
    assert am.count("— Cancelled") == 4
    assert await _status(league) == "CANCELLED"


async def test_a_calendar_never_posted_is_left_alone(tmp_path):
    league = await ongoing_league(tmp_path)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert league.texts(AM_CH.calendar) == []
    assert len(league.texts(PRO_CH.calendar)) == 1
    assert "calendar" not in reply(interaction)
    assert await _status(league) == "CANCELLED"


async def test_each_call_of_a_round_called_off_comes_down_and_its_check_in_is_written_beneath_the_line(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    await _asked(league)

    await run_queue(league.bot)

    assert [name for name, _key in await _names(league)].count("take_down_call") == 1
    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE division_id = ?", PRO) == []
    [line] = _success_lines(league)
    assert CHECKIN_R3 in line


async def test_an_empty_open_submission_is_closed_in_the_first_save_and_its_channel_deleted_first_after_it(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    await _empty_submission(league)
    await _asked(league)

    await _run_through(league, "apply")
    assert await _closed(league) == {R3: 1}
    assert (await _rounds(league))[R3] == "CANCELLED"

    await run_queue(league.bot)
    names = [name for name, _key in await _names(league)]
    assert names[names.index("apply") + 1] == "delete_channel"
    first_send = next(i for i, event in enumerate(league.events) if event[0] == "send")
    deleted = [i for i, event in enumerate(league.events)
               if event[0] == "delete_channel" and event[1] == SUBMISSION_CHANNEL]
    assert len(deleted) == 1 and deleted[0] < first_send


async def test_uncommitted_placements_are_discarded(tmp_path):
    league = await ongoing_league(tmp_path)
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id = ?",
        profile_id(MAX),
    )
    await _asked(league)

    await _run_through(league, "apply")

    assert await league.rows(
        "SELECT * FROM driver_season_assignments WHERE driver_profile_id = ?", profile_id(MAX)
    ) == []
    assert await league.rows(
        "SELECT * FROM team_seats WHERE driver_profile_id = ?", profile_id(MAX)
    ) == []
    assert await _status(league) == "ACTIVE"


async def test_a_raced_round_awaiting_verdicts_is_made_final_before_the_driver_pass(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _run_through(league, "apply")
    assert (await _rounds(league))[R2] == "FINAL"
    assert not [job for job in await _jobs(league) if job["name"] == "end" and job["done_at"]]
    assert await driver_state(league, MAX) == "ASSIGNED"

    await run_queue(league.bot)
    assert (await _rounds(league))[R2] == "FINAL"
    assert await _status(league) == "CANCELLED"


async def test_each_round_called_off_is_audited_with_the_status_it_was_cancelled_from(tmp_path):
    league = await ongoing_league(tmp_path)
    await _awaiting_results(league, R4)
    await _asked(league)

    await run_queue(league.bot)

    audits = await league.rows(
        "SELECT division_id, old_value FROM audit_entries WHERE change_type = 'round.status' "
        "AND new_value = 'CANCELLED' ORDER BY id"
    )
    assert sorted(row["old_value"] for row in audits if row["division_id"] == PRO) == [
        "AWAITING_RESULTS", "NOT_RUN",
    ]
    assert [row["old_value"] for row in audits if row["division_id"] == AM] == ["NOT_RUN"] * 4


async def test_every_driver_gets_a_history_entry_marked_cancelled(tmp_path):
    league = await ongoing_league(tmp_path)
    await _formers(league, LEWIS, CHARLES)
    await _asked(league)

    await run_queue(league.bot)

    history = await _history(league)
    assert ("101", "Pro", 1) in history and ("103", "Am", 1) in history
    assert all(cancelled == 1 for _user, _division, cancelled in history)
    assert (await _change(league))["state"] == "DONE"


async def test_the_driver_pass_returns_the_drivers_and_deletes_those_who_never_raced(tmp_path):
    league = await ongoing_league(tmp_path)
    await _formers(league, LEWIS)
    await _asked(league)

    await run_queue(league.bot)

    assert await driver_state(league, LEWIS) == "NOT_SIGNED_UP"
    assert await driver_state(league, MAX) is None
    assert not [row for row in await _history(league) if row[0] == str(MAX)]
    assert await league.rows("SELECT * FROM signup_records WHERE discord_user_id = ?", str(MAX))
    assert await league.rows("SELECT * FROM audit_entries WHERE change_type = 'DRIVER_PASS'")
    assert (await _change(league))["state"] == "DONE"
    assert await _status(league) == "CANCELLED"


async def test_test_mode_is_switched_off_and_its_saved_state_kept(tmp_path):
    league = await ongoing_league(tmp_path, test_mode=True)
    await _asked(league)

    await run_queue(league.bot)

    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0
    assert backup_saved(league)
    assert "discard_backup" not in [name for name, _key in await _names(league)]
    assert await _status(league) == "CANCELLED"


async def test_the_roles_are_taken_back_after_the_notices(tmp_path):
    league = await ongoing_league(tmp_path, weather=True, attendance=True, held=True)
    await _am_calendar_posted(league)
    await _asked(league)

    await run_queue(league.bot)

    notices = [i for i, event in enumerate(league.events)
               if event[0] == "send" and event[1] in _POSTS]
    revokes = [i for i, event in enumerate(league.events) if event[0] == "revoke"]
    assert notices and revokes
    assert max(notices) < min(revokes)
    for user_id, roles in ((LEWIS, {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}),
                           (MAX, {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}),
                           (CHARLES, {AM_ROLE, MCLAREN_ROLE, DRIVER_ROLE})):
        assert set(league.revoked[user_id]) == roles


async def test_a_discarded_unarming_cancels_nothing(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    _unarming_fails(league)
    rounds = await _rounds(league)
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("unarm", None)
    await discard_job(league.bot)

    assert UNARM_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert await _rounds(league) == rounds
    assert _success_lines(league) == []
    assert _posted(league) == [[]] * len(_POSTS)
    assert await _status(league) == "ACTIVE"


async def test_a_discarded_first_save_cancels_nothing_and_says_the_timed_work_is_gone(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    rounds = await _rounds(league)
    interaction = await _asked(league)

    with _failing("cancel_season_divisions_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("apply", None)
        await discard_job(league.bot)

    assert SAVE_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert sorted(league.unarmed) == sorted(rounds)
    assert await _divisions(league) == {PRO: "ACTIVE", AM: "ACTIVE"}
    assert await _rounds(league) == rounds
    assert _success_lines(league) == []
    assert _posted(league) == [[]] * len(_POSTS)
    assert await _status(league) == "ACTIVE"


@pytest.mark.parametrize("then", ["run again", "wound down meanwhile"])
async def test_a_discarded_last_save_says_to_run_the_command_again_and_running_it_again_finishes_it(
    tmp_path, then,
):
    league = await ongoing_league(tmp_path, weather=True, attendance=True)
    await _formers(league, LEWIS, CHARLES)
    interaction = await _asked(league)
    with _failing("cancel_season_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
        await discard_job(league.bot)

    assert END_DISCARDED in reply(interaction)
    assert await _divisions(league) == {PRO: "CANCELLED", AM: "CANCELLED"}
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []
    posted = _posted(league)

    if then == "run again":
        again = await _asked(league)
        await run_queue(league.bot)
        assert CANCELLED in reply(again)
        assert await _status(league) == "CANCELLED"
    else:
        await _pending_completion(league)
        again = await _asked(league)
        assert reply(again) == NOT_ONGOING
        completing = await complete_season(league)
        assert not league.errors, league.errors
        await run_queue(league.bot)
        assert "✅ Season marked as complete." in reply(completing)
        assert await _status(league) == "COMPLETED"
    history = await _history(league)
    assert ("101", "Pro", 1) in history and ("103", "Am", 1) in history
    assert all(cancelled == 1 for _user, _division, cancelled in history)
    assert _posted(league) == posted


async def test_one_success_line_records_the_cancellation_after_the_last_job(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    league.channel(PRO_CH.calendar).send_fails = _forbidden()
    await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_calendar", PRO)
    assert _success_lines(league) == []

    league.channel(PRO_CH.calendar).send_fails = None
    await retry_job(league.bot)

    [line] = _success_lines(league)
    assert line.startswith(SUCCESS)
    assert CHECKIN_R3 in line
    assert "not done:" not in line and "not notified:" not in line
    jobs = await _jobs(league)
    assert jobs[-1]["name"] == "close" and all(job["done_at"] for job in jobs)


async def test_the_season_is_recorded_cancelled_last_audited_and_never_moved_to_pending_completion(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _run_through(league, "apply")
    assert (await league.season())["stage"] == "ONGOING"
    assert await _status(league) == "ACTIVE"

    await run_queue(league.bot)

    season = await league.season()
    assert season["status"] == "CANCELLED" and season["stage"] != "PENDING_COMPLETION"
    assert await _divisions(league) == {PRO: "CANCELLED", AM: "CANCELLED"}
    assert await league.rows("SELECT * FROM audit_entries WHERE change_type = 'SEASON_CANCELLED'")
    assert [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN_KIND] == []
    assert await league.rows("SELECT * FROM seasons WHERE status = 'ACTIVE'") == []


async def test_a_cancellation_with_signups_open_leaves_the_stage_alone_until_the_season_is_recorded(
    tmp_path,
):
    """The season ongoing with its signup window open, and a driver still filling in the wizard,
    whom the window's close returns. The queue stops at that driver's notice, which Discord
    refuses, after the window is closed and before the season's end is saved: the season is still
    ongoing with signups open, never moved on to Pending completion, where it would stand refused
    its own cancellation. Retried, the season is recorded cancelled."""
    league = await ongoing_league(tmp_path, signups_open=True)
    await league.write("UPDATE seasons SET stage = 'ONGOING_SIGNUPS' WHERE id = ?", SEASON_ID)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.hold_fails[SIGNING_UP] = _forbidden()
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert not await window_open(league)
    season = await league.season()
    assert (season["status"], season["stage"]) == ("ACTIVE", "ONGOING_SIGNUPS")

    del league.hold_fails[SIGNING_UP]
    await retry_job(league.bot)
    assert await _status(league) == "CANCELLED"
    assert league.locked == [SIGNING_UP]


async def test_a_module_turned_off_before_its_notice_drops_the_notice(tmp_path):
    league = await ongoing_league(tmp_path, weather=True)
    interaction = await _asked(league)
    await _run_through(league, "apply")
    await league.switch("weather", False)

    await run_queue(league.bot)

    assert league.texts(PRO_CH.forecast) == [] and league.texts(AM_CH.forecast) == []
    assert "forecast" not in reply(interaction)
    assert len(league.texts(PRO_CH.results)) == 1 and len(league.texts(AM_CH.results)) == 1
    assert await _status(league) == "CANCELLED"


async def test_a_channel_never_set_and_a_calendar_posted_as_text_are_named_per_division(
    tmp_path, monkeypatch,
):
    from leaguebot.core.services import calendar_post_service

    league = await ongoing_league(tmp_path, weather=True, attendance=True, images=True)
    await league.write("UPDATE divisions SET forecast_channel_id = NULL WHERE id = ?", PRO)
    await league.write("UPDATE divisions SET calendar_message_id = NULL WHERE id = ?", PRO)
    await _am_calendar_posted(league)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    monkeypatch.setattr(calendar_post_service, "render_calendar_image",
                        AsyncMock(side_effect=RuntimeError("the template has no rows")))
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert all(job["done_at"] for job in await _jobs(league))
    unset = "**Pro** — forecast channel: no channel is set"
    fell_back = ("**Am** — calendar: posted as text, as the picture could not be drawn (the "
                 "template has no rows)")
    bullets = _not_notified(reply(interaction))
    assert unset in bullets and fell_back in bullets
    [line] = _success_lines(league)
    assert f"\n  not notified: {unset}" in line
    assert f"\n  not notified: {fell_back}" in line
    assert await _status(league) == "CANCELLED"


async def test_a_driver_approved_but_never_placed_loses_the_driver_role(tmp_path):
    league = await ongoing_league(tmp_path, held=True)
    await approved_unplaced(league)
    await _asked(league)

    await run_queue(league.bot)

    assert ("take_driver_role", UNASSIGNED) in await _names(league)
    assert league.revoked[UNASSIGNED] == [DRIVER_ROLE]
    assert await driver_state(league, UNASSIGNED) is None
    assert await _status(league) == "CANCELLED"


async def _window_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await ongoing_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    return league


async def _flush_refused(tmp_path: Any, monkeypatch: Any) -> Any:
    _flush_failing(monkeypatch, {"now": True})
    return await ongoing_league(tmp_path, test_mode=True)


async def _lock_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await ongoing_league(tmp_path)
    await signing_up(league)
    league.lock_fails[SIGNING_UP] = _forbidden()
    return league


async def _role_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await ongoing_league(tmp_path, held=True)
    await approved_unplaced(league)
    league.revoke_fails[UNASSIGNED] = _forbidden()
    return league


#: Each job a league admin may discard: the league that makes it fail, where the queue stops,
#: and what the reply and the line say was not done.
_DISCARDS = {
    "close_window": (_window_refused, ("close_window", None), WINDOW_OPEN),
    "flush_forecasts": (_flush_refused, ("flush_forecasts", None), FORECASTS_KEPT),
    "close_signup": (_lock_refused, ("close_signup", SIGNING_UP), SIGNUP_KEPT),
    "take_driver_role": (_role_refused, ("take_driver_role", UNASSIGNED), DRIVER_ROLE_KEPT),
}


@pytest.mark.parametrize("job", sorted(_DISCARDS))
async def test_a_discarded_job_is_named_with_what_to_do_by_hand_beneath_the_success_line(
    tmp_path, monkeypatch, job,
):
    build, where, text = _DISCARDS[job]
    league = await build(tmp_path, monkeypatch)
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == where

    await discard_job(league.bot)

    assert CANCELLED in reply(interaction)
    assert text in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {_logged(text)}" in line
    assert await _status(league) == "CANCELLED"


# ── A signup channel's notice, before its lock (F2) ─────────────────────────────────


async def test_a_discarded_signup_notice_still_closes_the_channel_and_the_outcome_says_so(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert league.locked == []

    await discard_job(league.bot)

    assert league.locked == [SIGNING_UP]
    assert league.notices == []
    assert NOTICE_DISCARDED in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {_logged(NOTICE_DISCARDED)}" in line
    assert await _status(league) == "CANCELLED"


# ── A signup's end, marked in the save that ends it (amendment K2) ───────────────────

#: How a driver a discarded window close returned is named, in the reply and the line.
RETURNED_NOT_TOLD = (
    f"<@{SIGNING_UP}> was returned to Not Signed Up when signups closed, but was not told and "
    "their channel was not closed: tell them and delete it by hand."
)


@pytest.mark.xfail(
    strict=True,
    reason="#439 slice 5: a discarded window close forgets the drivers it returned",
)
async def test_a_discarded_window_close_names_and_forgets_the_drivers_it_returned(tmp_path):
    """The season ongoing with its signup window open and driver 105 still filling in the
    wizard. The window's close returns 105, then recording the window closed fails, and a league
    admin discards `close_window`. 105 is never told: the reply and the closing line name them
    as returned but not told, their channel to delete by hand; no `signup_notice` is planned for
    them, and the mark is taken, so no later close finds them."""
    league = await ongoing_league(tmp_path, signups_open=True)
    await league.write("UPDATE seasons SET stage = 'ONGOING_SIGNUPS' WHERE id = ?", SEASON_ID)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.close_fails = RuntimeError("the window could not be recorded closed")
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("close_window", None)

    await discard_job(league.bot)

    assert RETURNED_NOT_TOLD in reply(interaction)
    [line] = _success_lines(league)
    assert _logged(RETURNED_NOT_TOLD) in line
    assert ("signup_notice", SIGNING_UP) not in await _names(league)
    assert league.notices == []
    assert await _status(league) == "CANCELLED"
    assert await league.bot.signup_module_service.owed_closing_notices() == []


@pytest.mark.xfail(
    strict=True, reason="#439 slice 5: the end's driver pass leaves a signup's wizard engaged"
)
async def test_the_end_save_marks_an_in_progress_signup_over(tmp_path):
    """Driver 105 is part-way through the wizard (Pending Signup Completion, their wizard
    collecting their notes) when the cancellation's `end` runs its driver pass. Their wizard is
    unengaged in that save, before any job of theirs runs, and a restart then arms no
    inactivity job for them and expires nothing."""
    league = await ongoing_league(tmp_path)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NOTES' "
        "WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    await _asked(league)

    await _run_through(league, "end")

    assert await driver_state(league, SIGNING_UP) is None
    assert await wizard_state(league, SIGNING_UP) == "UNENGAGED"
    await run_queue(league.bot)
    assert await wizards_recovered(league) == {"armed": [], "expired": []}
