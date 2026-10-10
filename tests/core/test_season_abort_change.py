"""Aborting a season on the change queue (#439, slice 5).

`/season abort` checks its confirmation word and finds the season being set up at the press, then
asks the change queue to abort it, answering at once with the job it begins with. The change is
checked when asked and again when it runs, in today's words: no season set up, or one past its
placements' confirmation; and, only when asked, any end of the season already in hand. Its jobs:
`close_window` cancels the signup close timer and closes the window; the forecasts posted under
test mode are cleared; then one save, `end`, runs the driver pass without writing any history,
switches test mode off and deletes the season, last; then each driver the pass reaches has their
signup channel's notice, its lock or their driver role as jobs of their own, the portraits of the
drivers deleted are discarded, and the setup the bot holds in memory is let go. The saved
test-mode state is kept. Every failure stops the queue; a Discard is named in the reply and
beneath the closing line, which stays `| Success` with the drivers returned and deleted.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`setup_league`, with "now" pinned: season 3 (id 7) in Placements, Lewis, Max and Charles placed
and not yet confirmed, the setup held in memory. Today `/season abort` aborts the season at the
press, catching what fails, so every test of the queue is marked to fail until the build; the
press's refusals in today's words pass as they stand.
"""
from __future__ import annotations

import importlib
import json
import re
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection
from tests.support.change_queue import (
    acknowledgement,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CHARLES,
    DRIVER_ROLE,
    LEWIS,
    MAX,
    SEASON_ABORT_KIND,
    SEASON_CANCEL_KIND,
    SEASON_COMPLETE_KIND,
    SEASON_ID,
    SIGNING_UP,
    TEST_DRIVER,
    UNASSIGNED,
    abort_season,
    approved_unplaced,
    backup_saved,
    driver_state,
    reply,
    season_end_changes,
    setup_league,
    signing_up,
    window_open,
)


ABORTED = (
    "✅ The season has been aborted. Nothing of it remains, and a new season may be set up with "
    "`/season setup`."
)
SUCCESS = "Admin (`<@77>`) | /season abort | Success"
REFUSAL = "⛔ `/season abort` refused for Admin (`<@77>`) — "
NOT_EVERYTHING = "⚠️ **Not everything could be done**"
ACK = (
    "⏳ Aborting the season being set up. This message will be updated when it is done; if it "
    "takes longer, the log channel will say so. It begins with job #"
)
WORD = "❌ Type exactly `CONFIRM` in the `confirm` field to proceed."
ONLY_BEFORE = (
    "❌ `/season abort` is available only before a season's placements are first confirmed. An "
    "ongoing season is cancelled with `/season cancel`."
)
END_DISCARDED = "Nothing was aborted: the season stands as it was. Run `/season abort` again."
NOT_FORGOTTEN = (
    "The setup the bot held in memory could not be let go of: `/round amend` may refuse this "
    "season until the bot restarts."
)
NOTICE_DISCARDED = f"<@{SIGNING_UP}> — their signup channel was closed without its notice"
WINDOW_OPEN = "The signup window could not be closed. Close it with `/signup close`."
FORECASTS_KEPT = (
    "The forecasts posted under test mode could not be cleared. Delete them by hand from each "
    "forecast channel."
)
SIGNUP_KEPT = f"<@{SIGNING_UP}> — their signup channel could not be closed. Delete it by hand."
DRIVER_ROLE_KEPT = f"<@{LEWIS}> — the driver role could not be taken back. Remove it by hand."
#: 2.11's refusal, for each other kind of a season's end in hand.
IN_HAND = {
    SEASON_COMPLETE_KIND: (
        {"season_id": SEASON_ID, "season_number": 3},
        "⏳ Season 3 is being completed (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel.",
    ),
    SEASON_CANCEL_KIND: (
        {"season_id": SEASON_ID, "season_number": 3},
        "⏳ Season 3 is being cancelled (job #{job}), so this cannot be done until that is "
        "finished. If it has stopped, press Retry or Discard on its notice in the log channel.",
    ),
}
ALREADY = (
    "⏳ The season being set up is already being aborted (job #{job}). If it has stopped, press "
    "Retry or Discard on its notice in the log channel."
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _asked(league: Any, **kwargs: Any) -> Any:
    interaction = await abort_season(league, **kwargs)
    assert not league.errors, league.errors
    return interaction


async def _change(league: Any) -> dict[str, Any]:
    rows = await season_end_changes(league, SEASON_ABORT_KIND)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    """The abort's jobs, in order, each with its payload read back."""
    jobs = await step_rows(league.db_path, (await _change(league))["id"])
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


def _who(job: dict[str, Any]) -> tuple[str, int | None]:
    key = job["payload"].get("user_id")
    return job["name"], int(key) if key is not None else None


async def _names(league: Any) -> list[str]:
    return [job["name"] for job in await _jobs(league)]


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the abort's job *name* is done."""
    for _ in range(200):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the abort's job {name} never finished: {await _jobs(league)}")


async def _stopped_at(league: Any) -> tuple[str, int | None] | None:
    job = await stopped_job(league.db_path)
    if job is None:
        return None
    job["payload"] = json.loads(job["payload"] or "{}")
    return _who(job)


async def _season_stands(league: Any) -> bool:
    return bool(await league.rows("SELECT id FROM seasons WHERE id = ?", SEASON_ID))


async def _test_mode(league: Any) -> int:
    return (await league.rows("SELECT test_mode_active FROM server_configs"))[0][
        "test_mode_active"
    ]


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if SUCCESS in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if line.startswith("⛔ ")]


def _not_done(text: str) -> list[str]:
    """The bullets of the reply's "Not everything could be done" section, in order."""
    assert NOT_EVERYTHING in text, text
    section = text.split(NOT_EVERYTHING, 1)[1]
    return [line.strip()[2:] for line in section.splitlines() if line.strip().startswith("• ")]


def _logged(text: str) -> str:
    """*text* as the log channel carries it: every mention in backticks, naming without
    notifying."""
    return re.sub(r"(<@&?\d+>)", r"`\1`", text)


def _modules() -> list[Any]:
    found = []
    for name in ("leaguebot.__main__",
                 "leaguebot.core.services.season_service",
                 "leaguebot.core.services.season_end_service",
                 "leaguebot.core.services.season_lifecycle_service",
                 "leaguebot.core.services.season_end_changes",
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


def _deletion_failing() -> Any:
    """`SeasonService.delete_season` raises, as a fault of the database would."""
    from leaguebot.core.services.season_service import SeasonService

    return patch.object(SeasonService, "delete_season",
                        AsyncMock(side_effect=RuntimeError("disk I/O error")))


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


async def _confirmed(league: Any) -> None:
    """The season's placements were confirmed: it is ongoing."""
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING' WHERE id = ?",
                       SEASON_ID)


def _forbidden() -> discord.HTTPException:
    return http_error(discord.Forbidden, status=403, text="Missing Permissions")


async def _driver_role_held(league: Any) -> None:
    """The league's driver role (820) is set, and Lewis, Max, Charles and the test driver hold
    it on the server, as approved drivers do before their placements are confirmed."""
    await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
    for user_id in (LEWIS, MAX, CHARLES, TEST_DRIVER):
        league.roles_held.setdefault(user_id, set()).add(DRIVER_ROLE)


# ── Defect 5: every failure stops the queue ─────────────────────────────────────────


async def test_a_signup_window_that_cannot_be_closed_stops_the_queue(tmp_path):
    league = await setup_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("close_window", None)
    assert await _season_stands(league)
    assert await window_open(league)

    league.close_fails = None
    await retry_job(league.bot)
    assert not await window_open(league)
    assert not await _season_stands(league)


@pytest.mark.parametrize("where", ["the flush", "the test drivers' deletion"])
async def test_test_mode_that_cannot_be_switched_off_stops_the_queue(tmp_path, monkeypatch, where):
    failing = {"now": where == "the flush"}
    _flush_failing(monkeypatch, failing)
    league = await setup_league(tmp_path, test_mode=True)
    await _asked(league)

    if where == "the flush":
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("flush_forecasts", None)
    else:
        with _failing("clear_all_test_drivers_on"):
            await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    assert await _season_stands(league)
    assert await driver_state(league, TEST_DRIVER) == "ASSIGNED"
    assert await _test_mode(league) == 1

    failing["now"] = False
    await retry_job(league.bot)
    assert not await _season_stands(league)
    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0


async def test_a_close_timer_that_cannot_be_cancelled_stops_the_queue(tmp_path):
    league = await setup_league(tmp_path, signups_open=True)
    scheduler = league.bot.scheduler_service
    scheduler.cancel_signup_close_timer = MagicMock(side_effect=RuntimeError("job store locked"))
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("close_window", None)
    assert await _season_stands(league)
    assert await window_open(league)

    scheduler.cancel_signup_close_timer = MagicMock()
    await retry_job(league.bot)
    scheduler.cancel_signup_close_timer.assert_called_once()
    assert not await window_open(league)
    assert not await _season_stands(league)


async def test_a_signup_channel_the_driver_pass_cannot_close_stops_the_queue(tmp_path):
    league = await setup_league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = _forbidden()
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert SIGNING_UP not in league.locked
    assert not await _season_stands(league)

    del league.hold_fails[SIGNING_UP]
    await retry_job(league.bot)
    assert [uid for uid, _notice in league.notices] == [SIGNING_UP]
    assert league.locked == [SIGNING_UP]
    assert len(_success_lines(league)) == 1


async def test_the_driver_pass_test_mode_and_the_season_s_deletion_are_saved_together(tmp_path):
    league = await setup_league(tmp_path, test_mode=True)
    await _asked(league)

    with _deletion_failing():
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    for user_id in (LEWIS, MAX, TEST_DRIVER):
        assert await driver_state(league, user_id) == "ASSIGNED"
    assert await _test_mode(league) == 1
    assert await _season_stands(league)

    await retry_job(league.bot)
    assert await driver_state(league, LEWIS) is None
    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0
    assert not await _season_stands(league)


async def test_a_stop_part_way_is_finished_on_restart(tmp_path):
    league = await setup_league(tmp_path, signups_open=True, test_mode=True)
    await _asked(league)
    await _run_through(league, "close_window")
    assert not await window_open(league)
    assert await _season_stands(league)

    await league.restart()
    await run_queue(league.bot)

    assert not await _season_stands(league)
    assert await _test_mode(league) == 0
    assert len(_success_lines(league)) == 1


async def test_the_admin_is_told_at_once_naming_the_job_and_the_reply_is_updated(tmp_path):
    league = await setup_league(tmp_path)
    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    interaction.response.defer.assert_not_awaited()
    assert await _season_stands(league)

    await run_queue(league.bot)
    assert ABORTED in reply(interaction)
    assert not await _season_stands(league)


@pytest.mark.parametrize("how", [
    pytest.param("waiting", id="waiting behind a stopped job"),
    pytest.param("stopped", id="stopped at its own job"),
])
async def test_a_second_abort_is_refused_at_once_naming_the_job(tmp_path, how):
    league = await setup_league(tmp_path, signups_open=True)
    if how == "waiting":
        await _seed_change(league, "hub.refresh", {}, stopped=True)
        await _asked(league)
        job = next(j["id"] for j in await _jobs(league) if j["done_at"] is None)
    else:
        league.close_fails = RuntimeError("the window could not be recorded closed")
        await _asked(league)
        await run_queue(league.bot)
        job = (await stopped_job(league.db_path))["id"]
    refusals = len(_refusal_lines(league))

    second = await _asked(league)

    assert reply(second) == ALREADY.format(job=job)
    assert len(_refusal_lines(league)) == refusals + 1
    assert len(await season_end_changes(league)) == 1


@pytest.mark.parametrize("kind", [SEASON_COMPLETE_KIND, SEASON_CANCEL_KIND])
@pytest.mark.parametrize("stopped", [False, True], ids=["waiting", "stopped"])
async def test_an_abort_is_refused_at_once_while_another_end_of_the_season_is_in_hand(
    tmp_path, kind, stopped,
):
    league = await setup_league(tmp_path)
    payload, text = IN_HAND[kind]
    job = await _seed_change(league, kind, payload, stopped=stopped)

    interaction = await _asked(league)

    assert reply(interaction) == text.format(job=job)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league, SEASON_ABORT_KIND) == []
    assert await _season_stands(league)


# ── The checks ──────────────────────────────────────────────────────────────────────


async def _nothing(_league: Any) -> None:
    return None


async def _no_season(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'COMPLETED', stage = 'COMPLETED' WHERE id = ?",
                       SEASON_ID)


def _active_at(stage: str) -> Any:
    """The season's placements were confirmed and it stands at *stage*."""
    async def _set(league: Any) -> None:
        await league.write("UPDATE seasons SET status = 'ACTIVE', stage = ? WHERE id = ?",
                           stage, SEASON_ID)
    return _set


#: Each refusal at the press: what sets it up, the confirmation word, and today's reply.
_PRESS_REFUSALS = {
    "the word": (_nothing, "confirm", WORD),
    "no season": (_no_season, "CONFIRM", ONLY_BEFORE),
    "an ongoing season": (_confirmed, "CONFIRM", ONLY_BEFORE),
    "a season taking signups while ongoing": (_active_at("ONGOING_SIGNUPS"), "CONFIRM",
                                              ONLY_BEFORE),
    "a season placing drivers while ongoing": (_active_at("ONGOING_PLACEMENTS"), "CONFIRM",
                                               ONLY_BEFORE),
    "a season pending completion": (_active_at("PENDING_COMPLETION"), "CONFIRM", ONLY_BEFORE),
}


@pytest.mark.parametrize("case", sorted(_PRESS_REFUSALS))
async def test_the_command_is_refused_at_once_in_today_s_words(tmp_path, case):
    setup, word, text = _PRESS_REFUSALS[case]
    league = await setup_league(tmp_path)
    await setup(league)

    interaction = await _asked(league, confirm=word)

    assert reply(interaction) == text
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league) == []
    assert await _season_stands(league)
    assert await driver_state(league, LEWIS) == "ASSIGNED"


async def test_a_season_confirmed_while_the_abort_waited_is_refused_when_it_runs(tmp_path):
    league = await setup_league(tmp_path)
    interaction = await _asked(league)
    await _confirmed(league)

    await run_queue(league.bot)

    assert ONLY_BEFORE in reply(interaction)
    assert ABORTED not in reply(interaction)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert (await _change(league))["state"] == "REFUSED"
    assert await stopped_job(league.db_path) is None
    assert await _season_stands(league)
    assert await driver_state(league, LEWIS) == "ASSIGNED"


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_jobs_run_in_order(tmp_path):
    league = await setup_league(tmp_path, signups_open=True, test_mode=True)
    await signing_up(league)
    await _asked(league)

    await run_queue(league.bot)

    names = await _names(league)
    assert names[:3] == ["close_window", "flush_forecasts", "end"]
    assert names[-3:] == ["discard_portraits", "forget_setup", "close"]
    assert "signup_notice" in names[3:-3]
    assert set(names[3:-3]) <= {"signup_notice", "close_signup", "take_driver_role"}
    assert "discard_backup" not in names
    assert not await _season_stands(league)


@pytest.mark.parametrize("stage", ["CONFIGURATION", "WAITING", "SIGNUPS", "PLACEMENTS"])
async def test_the_season_is_deleted_with_every_record_of_it_and_takes_no_number(tmp_path, stage):
    league = await setup_league(tmp_path, stage=stage, signups_open=stage == "SIGNUPS")
    await _asked(league)

    await run_queue(league.bot)

    assert not await window_open(league)

    assert await league.rows("SELECT * FROM seasons") == []
    assert await league.rows("SELECT * FROM divisions WHERE season_id = ?", SEASON_ID) == []
    assert await league.rows("SELECT * FROM rounds") == []
    assert await league.rows("SELECT * FROM signup_records WHERE season_id = ?", SEASON_ID) == []
    assert await league.rows(
        "SELECT * FROM driver_season_assignments WHERE season_id = ?", SEASON_ID
    ) == []
    assert (await _change(league))["state"] == "DONE"


async def test_the_drivers_return_to_not_signed_up_with_no_history_written(tmp_path):
    league = await setup_league(tmp_path)
    await league.write("UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?",
                       str(LEWIS))
    await _asked(league)

    await run_queue(league.bot)

    assert await driver_state(league, LEWIS) == "NOT_SIGNED_UP"
    assert await driver_state(league, MAX) is None
    assert await league.rows("SELECT * FROM driver_history_entries") == []
    assert await league.rows("SELECT * FROM audit_entries WHERE change_type = 'DRIVER_PASS'")
    assert (await _change(league))["state"] == "DONE"


async def test_every_real_driver_returned_loses_the_driver_role_and_a_test_driver_is_passed_over(
    tmp_path,
):
    league = await setup_league(tmp_path, test_mode=True)
    await _driver_role_held(league)
    await approved_unplaced(league)
    await _asked(league)

    await run_queue(league.bot)

    taken = sorted(uid for name, uid in map(_who, await _jobs(league))
                   if name == "take_driver_role")
    assert taken == [LEWIS, MAX, CHARLES, UNASSIGNED]
    assert league.revoked == {uid: [DRIVER_ROLE] for uid in (LEWIS, MAX, CHARLES, UNASSIGNED)}
    assert not await _season_stands(league)


async def test_test_mode_is_switched_off_and_its_saved_state_kept(tmp_path):
    league = await setup_league(tmp_path, test_mode=True)
    await _asked(league)

    await run_queue(league.bot)

    assert await driver_state(league, TEST_DRIVER) is None
    assert await _test_mode(league) == 0
    assert backup_saved(league)
    assert "discard_backup" not in await _names(league)
    assert not await _season_stands(league)


async def test_the_setup_held_in_memory_is_let_go_after_the_save(tmp_path):
    league = await setup_league(tmp_path)
    await _asked(league)

    await _run_through(league, "end")
    assert not await _season_stands(league)
    assert league.cog._get_pending() is not None

    await run_queue(league.bot)
    assert league.cog._get_pending() is None
    names = await _names(league)
    assert names.index("end") < names.index("forget_setup")


async def test_a_discarded_save_aborts_nothing(tmp_path):
    league = await setup_league(tmp_path, test_mode=True)
    interaction = await _asked(league)

    with _deletion_failing():
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
        await discard_job(league.bot)

    assert END_DISCARDED in reply(interaction)
    assert ABORTED not in reply(interaction)
    assert _success_lines(league) == []
    assert await _season_stands(league)
    assert await driver_state(league, LEWIS) == "ASSIGNED"
    assert await driver_state(league, TEST_DRIVER) == "ASSIGNED"
    assert await _test_mode(league) == 1
    assert league.cog._get_pending() is not None


async def test_one_success_line_records_the_drivers_returned_and_deleted(tmp_path):
    league = await setup_league(tmp_path)
    await league.write("UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?",
                       str(LEWIS))
    await _asked(league)

    await run_queue(league.bot)

    [line] = _success_lines(league)
    assert line.startswith(SUCCESS)
    assert "\n  drivers returned to Not Signed Up: 3" in line
    assert "\n  drivers deleted: 2" in line
    assert "not done:" not in line
    jobs = await _jobs(league)
    assert jobs[-1]["name"] == "close" and all(job["done_at"] for job in jobs)


async def test_a_discarded_setup_release_is_named(tmp_path):
    league = await setup_league(tmp_path)
    league.cog.clear_pending = MagicMock(side_effect=RuntimeError("the cog is reloading"))
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("forget_setup", None)
    assert not await _season_stands(league)

    await discard_job(league.bot)

    assert ABORTED in reply(interaction)
    assert NOT_FORGOTTEN in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {NOT_FORGOTTEN}" in line


async def _window_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await setup_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    return league


async def _flush_refused(tmp_path: Any, monkeypatch: Any) -> Any:
    _flush_failing(monkeypatch, {"now": True})
    return await setup_league(tmp_path, test_mode=True)


async def _lock_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await setup_league(tmp_path)
    await signing_up(league)
    league.lock_fails[SIGNING_UP] = _forbidden()
    return league


async def _role_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await setup_league(tmp_path)
    await _driver_role_held(league)
    league.revoke_fails[LEWIS] = _forbidden()
    return league


#: Each job a league admin may discard: the league that makes it fail, where the queue stops,
#: and what the reply and the line say was not done.
_DISCARDS = {
    "close_window": (_window_refused, ("close_window", None), WINDOW_OPEN),
    "flush_forecasts": (_flush_refused, ("flush_forecasts", None), FORECASTS_KEPT),
    "close_signup": (_lock_refused, ("close_signup", SIGNING_UP), SIGNUP_KEPT),
    "take_driver_role": (_role_refused, ("take_driver_role", LEWIS), DRIVER_ROLE_KEPT),
}


@pytest.mark.parametrize("job", sorted(_DISCARDS))
async def test_a_discarded_job_is_named_with_what_to_do_by_hand(tmp_path, monkeypatch, job):
    build, where, text = _DISCARDS[job]
    league = await build(tmp_path, monkeypatch)
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == where

    await discard_job(league.bot)

    assert ABORTED in reply(interaction)
    assert text in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {_logged(text)}" in line
    assert not await _season_stands(league)


# ── A signup channel's notice, before its lock (F2) ─────────────────────────────────


async def test_a_discarded_signup_notice_still_closes_the_channel_and_the_outcome_says_so(
    tmp_path,
):
    league = await setup_league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert league.locked == []

    await discard_job(league.bot)

    assert league.locked == [SIGNING_UP]
    assert league.notices == []
    assert ABORTED in reply(interaction)
    assert NOTICE_DISCARDED in _not_done(reply(interaction))
    [line] = _success_lines(league)
    assert f"  not done: {_logged(NOTICE_DISCARDED)}" in line
    assert not await _season_stands(league)
