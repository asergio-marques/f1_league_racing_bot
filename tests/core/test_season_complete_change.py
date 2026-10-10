"""Completing a season on the change queue (#439, slice 5).

`/season complete` finds the confirmed season at the press and asks the change queue to complete
it, answering at once with the job it begins with. The change is checked when asked and again
when it runs, in today's words: no season being raced, an amendment open, rounds outstanding or a
division unfinished; and, only when asked, any end of the season already in hand. Its jobs:
`settle` brings the divisions up to date (winding an ongoing season down first, where every
division is done); each division's final standings and final sheet; each real driver's roles
taken back; the signup window closed; the forecasts posted under test mode cleared; then one
save, `end`, records the history, runs the driver pass, switches test mode off and marks the
season completed, last; then each driver the pass reaches has their signup channel's notice
(`signup_notice`), its lock (`close_signup`) or their driver role (`take_driver_role`) as jobs of
their own, the portraits of the drivers deleted are discarded, and the saved test-mode state is
deleted. Every failure stops the queue; a Discard is named in the reply and the closing line.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`pending_completion_league`, with "now" pinned: every round final and both divisions finished,
results accepted for Pro's rounds 1 and 2 and Am's round 1, the members holding their division,
team and driver roles, Lewis a former driver. Today `/season complete` ends the season at the
press, catching what fails, so every test of the queue is marked to fail until the build; the
press's refusals in today's words pass as they stand.
"""
from __future__ import annotations

import importlib
import json
import re
import sqlite3
from contextlib import ExitStack
from pathlib import Path
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
    AM,
    AM_ROLE,
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
    TEST_DRIVER,
    UNASSIGNED,
    approved_unplaced,
    backup_saved,
    complete_season,
    driver_state,
    pending_completion_league,
    reply,
    round_id,
    season_end_changes,
    signing_up,
    window_open,
)

_XFAIL = "#439: /season complete still ends the season at the press, off the change queue"
_XFAIL_NOTICE = "#439: a signup channel's notice is not yet a job before its lock"
_XFAIL_IN_HAND = "#439: a completion is not yet refused while the season's end is in hand"

PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
COMPLETED = "✅ Season marked as complete."
SUCCESS = "Admin (`<@77>`) | /season complete | Success\n  season: Season #3"
INCOMPLETE = "Admin (`<@77>`) | /season complete | Incomplete"
REFUSAL = "⛔ `/season complete` refused for Admin (`<@77>`) — "
NOT_EVERYTHING = "⚠️ **Not everything could be done**"
ACK = (
    "⏳ Completing season 3. This message will be updated when it is done; if it takes longer, "
    "the log channel will say so. It begins with job #"
)
NO_SEASON = "❌ No season is being raced, so there is none to complete."
AMENDED = (
    "❌ Cannot complete season — round 2 of **Pro** is being amended in <#8200>. Finish or "
    "cancel it first: completing posts every division's final classification, which would carry "
    "its corrections before they are approved."
)
OUTSTANDING = (
    "❌ Cannot complete season — the following rounds are not yet finalised:\n"
    "• Pro — Round 3 (Silverstone)\n• Pro — Round 4 (Silverstone)"
)
UNFINISHED = (
    "❌ Cannot complete season — no round is outstanding, but these divisions have not finished: "
    "**Am**. Cancel a division that will never run, or report this."
)
PRO_NOT_POSTED = "**Pro** — its final classification could not be posted. No command posts it again."
ROLES_KEPT = (
    "<@101> — their roles of season 3 could not be taken back. Remove their division and team "
    "roles and the driver role by hand."
)
SETTLE_DISCARDED = "Nothing was completed: season 3 stands as it was. Run `/season complete` again."
WIND_DOWN_DISCARDED = (
    "Nothing was completed: the season could not be moved to pending completion. Run "
    "`/season complete` again."
)
END_DISCARDED = (
    "Season 3 was not completed: the save that records its end was discarded. Run "
    "`/season complete` again; its final classifications will be posted again."
)
BACKUP_KEPT = (
    "The saved test-mode state could not be deleted. It is deleted with the next `/test-mode "
    "toggle` that switches test mode off."
)
NOTICE_DISCARDED = f"<@{SIGNING_UP}> — their signup channel was closed without its notice"
SIGNUPS_CLOSED = "🔒 Signups have closed. This channel will be automatically deleted in 24 hours."
WINDOW_OPEN = "The signup window could not be closed. Close it with `/signup close`."
FORECASTS_KEPT = (
    "The forecasts posted under test mode could not be cleared. Delete them by hand from each "
    "forecast channel."
)
SIGNUP_KEPT = f"<@{SIGNING_UP}> — their signup channel could not be closed. Delete it by hand."
DRIVER_ROLE_KEPT = (
    f"<@{UNASSIGNED}> — the driver role could not be taken back. Remove it by hand."
)
PRO_SHEET_NOT_POSTED = (
    "**Pro** — its final classification (attendance sheet) could not be posted. No command "
    "posts it again."
)
#: 2.11's refusal, for each other kind of a season's end in hand.
IN_HAND = {
    SEASON_CANCEL_KIND: (
        {"season_id": SEASON_ID, "season_number": 3},
        "⏳ Season 3 is being cancelled (job #{job}), so this cannot be done until that is "
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
    "⏳ Season 3 is already being completed (job #{job}). If it has stopped, press Retry or "
    "Discard on its notice in the log channel."
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _asked(league: Any) -> Any:
    interaction = await complete_season(league)
    assert not league.errors, league.errors
    return interaction


async def _change(league: Any) -> dict[str, Any]:
    rows = await season_end_changes(league, SEASON_COMPLETE_KIND)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    """The completion's jobs, in order, each with its payload read back."""
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
    """Run the queue a job at a time until the completion's job *name* (for *key*) is done."""
    for _ in range(200):
        if any(_who(job) == (name, key) if key is not None else job["name"] == name
               for job in await _jobs(league) if job["done_at"]):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the completion's job {name} never finished: {await _jobs(league)}")


async def _stopped_at(league: Any) -> tuple[str, int | None] | None:
    job = await stopped_job(league.db_path)
    if job is None:
        return None
    job["payload"] = json.loads(job["payload"] or "{}")
    return _who(job)


async def _status(league: Any) -> str:
    return (await league.season())["status"]


async def _history(league: Any) -> list[tuple[str, str, int]]:
    rows = await league.rows(
        "SELECT discord_user_id, division_name, cancelled FROM driver_history_entries "
        "WHERE season_number = 3 ORDER BY discord_user_id, division_name"
    )
    return [(row["discord_user_id"], row["division_name"], row["cancelled"]) for row in rows]


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if "| /season complete | Success" in line]


def _closing_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league)
            if "| /season complete | Success" in line or INCOMPLETE in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if line.startswith("⛔ ")]


def _not_done(text: str) -> list[str]:
    """The bullets of the reply's "Not everything could be done" section, in order."""
    assert NOT_EVERYTHING in text, text
    section = text.split(NOT_EVERYTHING, 1)[1]
    return [line[2:] for line in section.splitlines() if line.startswith("• ")]


def _modules() -> list[Any]:
    found = []
    for name in ("leaguebot.__main__",
                 "leaguebot.core.services.season_service",
                 "leaguebot.core.services.season_end_service",
                 "leaguebot.core.services.season_lifecycle_service",
                 "leaguebot.core.services.season_end_changes",
                 "leaguebot.core.services.test_mode_service",
                 "leaguebot.core.services.test_roster_service",
                 "leaguebot.weather.services.forecast_cleanup_service",
                 "leaguebot.image.services.driver_portrait_service"):
        try:
            found.append(importlib.import_module(name))
        except ImportError:
            continue
    return found


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch *name* wherever core or the builder keeps it, and the change type's own reference."""
    stack = ExitStack()
    for module in _modules():
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _failing(name: str) -> ExitStack:
    """*name* raises, as a fault of the database would."""
    return _everywhere(name, AsyncMock(side_effect=RuntimeError("disk I/O error")))


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


def _backup_undeletable(monkeypatch: Any, league: Any, refusing: dict[str, bool]) -> None:
    """The saved test-mode state cannot be deleted while *refusing* says so."""
    from leaguebot.core.services.backup_service import backup_path

    saved = Path(backup_path(league.db_path))
    unlinking = Path.unlink

    def _unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if refusing["now"] and Path(self) == saved:
            raise PermissionError(13, "Permission denied", str(self))
        unlinking(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink)


async def _amendment_open(league: Any) -> None:
    await league.write(
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (?, 8200, '[\"FEATURE_RACE\"]', ?)",
        round_id(PRO, 2), league.clock.now.isoformat(),
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


# ── Defect 5: every failure stops the queue ─────────────────────────────────────────


async def test_a_final_classification_discord_refuses_stops_the_queue_and_the_season_is_not_completed_until_it_goes_through(
    tmp_path,
):
    league = await pending_completion_league(tmp_path)
    league.channel(PRO_CH.standings).send_fails = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("final_standings", PRO)
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []
    assert "is stopped at job #" in reply(interaction)

    league.channel(PRO_CH.standings).send_fails = None
    await retry_job(league.bot)
    assert league.texts(PRO_CH.standings)
    assert await _status(league) == "COMPLETED"
    assert len(_success_lines(league)) == 1


@pytest.mark.parametrize("which", ["standings", "attendance"])
async def test_a_final_classification_whose_channel_was_deleted_stops_the_queue_and_goes_through_once_it_is_set_again(
    tmp_path, which,
):
    league = await pending_completion_league(tmp_path, attendance=True)
    cid, job = ((PRO_CH.standings, "final_standings") if which == "standings"
                else (PRO_CH.attendance, "final_sheet"))
    league.remove_channel(cid)
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == (job, PRO)
    assert await _status(league) == "ACTIVE"

    league.restore_channel(cid)
    await retry_job(league.bot)
    assert league.texts(cid)
    assert await _status(league) == "COMPLETED"


def _names_for_deletion_by_hand(line: str, cid: int, ids: list[int]) -> bool:
    linked = all(re.search(rf"https?://\S+/{cid}/{mid}\b", line) for mid in ids)
    named = f"<#{cid}>" in line and all(str(mid) in line for mid in ids)
    return "by hand" in line and (linked or named)


@pytest.mark.parametrize("part", [False, True], ids=["nothing posted", "a part posted"])
async def test_a_discarded_final_classification_is_named_as_never_posted_and_the_line_says_incomplete(
    tmp_path, monkeypatch, part,
):
    league = await pending_completion_league(tmp_path, images=part)
    standings = league.channel(PRO_CH.standings)
    if part:
        from leaguebot.image.services import image_standings_post
        from leaguebot.image.services.image_standings_post import (
            FELL_BACK,
            ChampionshipOutcome,
            StandingsPostOutcome,
        )

        league.bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=True)
        monkeypatch.setattr(image_standings_post, "try_post", AsyncMock(
            side_effect=lambda *_args, **_kwargs: StandingsPostOutcome(
                drivers=ChampionshipOutcome(FELL_BACK),
                constructors=ChampionshipOutcome(FELL_BACK),
            )
        ))
        standings.fail_when = lambda content, _kwargs: "**Team Standings**" in (content or "")
    else:
        standings.send_fails = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("final_standings", PRO)
    standing = sorted(standings.messages)

    await discard_job(league.bot)

    assert await _status(league) == "COMPLETED"
    text = reply(interaction)
    assert COMPLETED in text
    bullets = _not_done(text)
    assert PRO_NOT_POSTED in bullets
    [line] = _closing_lines(league)
    assert INCOMPLETE in line
    assert f"  not done: {PRO_NOT_POSTED}" in line
    if part:
        assert standing
        assert any(_names_for_deletion_by_hand(b, PRO_CH.standings, standing) for b in bullets)
        assert any(_names_for_deletion_by_hand(each, PRO_CH.standings, standing)
                   for each in line.split("\n  not done: ")[1:])


async def test_a_signup_window_that_cannot_be_closed_stops_the_queue(tmp_path):
    league = await pending_completion_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("close_window", None)
    assert await _status(league) == "ACTIVE"
    assert await window_open(league)

    league.close_fails = None
    await retry_job(league.bot)
    assert not await window_open(league)
    assert await _status(league) == "COMPLETED"


@pytest.mark.parametrize("where", ["the flush", "the test drivers' deletion"])
async def test_test_mode_that_cannot_be_switched_off_stops_the_queue(tmp_path, monkeypatch, where):
    failing = {"now": where == "the flush"}
    _flush_failing(monkeypatch, failing)
    league = await pending_completion_league(tmp_path, test_mode=True)
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
    config = await league.rows("SELECT test_mode_active FROM server_configs")
    assert config[0]["test_mode_active"] == 1

    failing["now"] = False
    await retry_job(league.bot)
    assert await _status(league) == "COMPLETED"
    assert await driver_state(league, TEST_DRIVER) is None
    config = await league.rows("SELECT test_mode_active FROM server_configs")
    assert config[0]["test_mode_active"] == 0


@pytest.mark.xfail(strict=True, reason=_XFAIL)
async def test_a_role_discord_will_not_take_back_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    league = await pending_completion_league(tmp_path)
    league.revoke_fails[LEWIS] = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("revoke_roles", LEWIS)
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []

    del league.revoke_fails[LEWIS]
    await discard_job(league.bot)
    assert await _status(league) == "COMPLETED"
    assert ROLES_KEPT in _not_done(reply(interaction))
    [line] = _closing_lines(league)
    assert f"  not done: {ROLES_KEPT}" in line


async def test_a_signup_channel_the_driver_pass_cannot_close_stops_the_queue(tmp_path):
    league = await pending_completion_league(tmp_path)
    await signing_up(league)
    league.hold_fails[SIGNING_UP] = _forbidden()
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert SIGNING_UP not in league.locked
    assert await _status(league) == "COMPLETED"

    del league.hold_fails[SIGNING_UP]
    await retry_job(league.bot)
    assert [uid for uid, _notice in league.notices] == [SIGNING_UP]
    assert league.locked == [SIGNING_UP]
    assert len(_success_lines(league)) == 1


async def test_a_stop_part_way_is_finished_on_restart_and_posts_no_final_classification_twice(
    tmp_path,
):
    league = await pending_completion_league(tmp_path, attendance=True)
    await _asked(league)
    await _run_through(league, "revoke_roles", MAX)
    posted = {cid: len(league.texts(cid))
              for cid in (PRO_CH.standings, PRO_CH.attendance, AM_CH.standings, AM_CH.attendance)}
    assert all(posted.values()), posted

    await league.restart()
    await run_queue(league.bot)

    assert {cid: len(league.texts(cid)) for cid in posted} == posted
    for user_id, roles in ((LEWIS, {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}),
                           (MAX, {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}),
                           (CHARLES, {AM_ROLE, MCLAREN_ROLE, DRIVER_ROLE})):
        assert set(league.revoked[user_id]) == roles
    assert await _status(league) == "COMPLETED"
    assert len(_success_lines(league)) == 1


async def test_the_admin_is_told_at_once_naming_the_job_and_the_reply_is_updated_with_the_outcome(
    tmp_path,
):
    league = await pending_completion_league(tmp_path)
    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    interaction.response.defer.assert_not_awaited()
    assert await _status(league) == "ACTIVE"

    await run_queue(league.bot)
    assert COMPLETED in reply(interaction)


async def test_no_success_is_reported_while_a_job_stands_stopped(tmp_path):
    league = await pending_completion_league(tmp_path, attendance=True)
    league.channel(PRO_CH.attendance).send_fails = _forbidden()
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == ("final_sheet", PRO)
    assert _success_lines(league) == []
    assert COMPLETED not in reply(interaction)
    assert "is stopped at job #" in reply(interaction)


async def test_the_history_the_driver_pass_and_the_archive_are_saved_together_or_not_at_all(
    tmp_path,
):
    league = await pending_completion_league(tmp_path)
    await _asked(league)

    with _failing("complete_season_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    assert await _history(league) == []
    for user_id in (LEWIS, MAX, CHARLES):
        assert await driver_state(league, user_id) == "ASSIGNED"
    assert await _status(league) == "ACTIVE"

    await retry_job(league.bot)
    assert ("101", "Pro", 0) in await _history(league)
    assert await driver_state(league, LEWIS) == "NOT_SIGNED_UP"
    assert await driver_state(league, MAX) is None
    assert await _status(league) == "COMPLETED"


@pytest.mark.parametrize("how", [
    pytest.param("waiting", id="waiting behind a stopped job"),
    pytest.param("stopped", id="stopped at its final standings"),
])
async def test_a_second_completion_is_refused_at_once_naming_the_job(tmp_path, how):
    league = await pending_completion_league(tmp_path)
    if how == "waiting":
        await _seed_change(league, "hub.refresh", {}, stopped=True)
        await _asked(league)
        job = next(j["id"] for j in await _jobs(league) if j["done_at"] is None)
    else:
        league.channel(PRO_CH.standings).send_fails = _forbidden()
        await _asked(league)
        await run_queue(league.bot)
        job = (await stopped_job(league.db_path))["id"]
    refusals = len(_refusal_lines(league))

    second = await _asked(league)

    assert reply(second) == ALREADY.format(job=job)
    assert len(_refusal_lines(league)) == refusals + 1
    assert len(await season_end_changes(league)) == 1


@pytest.mark.parametrize("kind", [SEASON_CANCEL_KIND, SEASON_ABORT_KIND])
@pytest.mark.parametrize("stopped", [False, True], ids=["waiting", "stopped"])
@pytest.mark.xfail(strict=True, reason=_XFAIL_IN_HAND)
async def test_a_completion_is_refused_at_once_while_another_end_of_the_season_is_in_hand(
    tmp_path, kind, stopped,
):
    league = await pending_completion_league(tmp_path)
    payload, text = IN_HAND[kind]
    job = await _seed_change(league, kind, payload, stopped=stopped)

    interaction = await _asked(league)

    assert reply(interaction) == text.format(job=job)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league, SEASON_COMPLETE_KIND) == []
    assert await _status(league) == "ACTIVE"


# ── The checks ──────────────────────────────────────────────────────────────────────


async def _no_season(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", SEASON_ID)


async def _rounds_outstanding(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", SEASON_ID)
    await league.write("UPDATE divisions SET status = 'ACTIVE' WHERE id = ?", PRO)
    await league.write("UPDATE rounds SET status = 'NOT_RUN' WHERE id IN (?, ?)",
                       round_id(PRO, 3), round_id(PRO, 4))


async def _division_in_setup(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", SEASON_ID)
    await league.write("UPDATE divisions SET status = 'SETUP' WHERE id = ?", AM)


#: Each refusal at the press: what sets it up, and today's reply.
_PRESS_REFUSALS = {
    "no season": (_no_season, NO_SEASON),
    "an amendment open": (_amendment_open, AMENDED),
    "rounds outstanding": (_rounds_outstanding, OUTSTANDING),
    "a division unfinished": (_division_in_setup, UNFINISHED),
}


@pytest.mark.parametrize("case", sorted(_PRESS_REFUSALS))
async def test_the_command_is_refused_at_once_in_today_s_words(tmp_path, case):
    setup, text = _PRESS_REFUSALS[case]
    league = await pending_completion_league(tmp_path)
    await setup(league)
    before = await league.season()

    interaction = await _asked(league)

    assert reply(interaction) == text
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await season_end_changes(league) == []
    assert (await league.season())["status"] == before["status"]
    assert await _history(league) == []


async def test_the_list_of_outstanding_rounds_stops_at_twenty(tmp_path):
    league = await pending_completion_league(tmp_path)
    await league.write("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", SEASON_ID)
    await league.write("UPDATE divisions SET status = 'ACTIVE' WHERE id = ?", PRO)
    for number in range(5, 35):
        await league.write(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at,"
            " status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', ?, 'NOT_RUN')",
            1000 + number, PRO, number,
            league.clock.now.replace(tzinfo=None).isoformat(),
        )

    interaction = await _asked(league)

    text = reply(interaction)
    assert len([line for line in text.splitlines() if line.startswith("• ")]) == 20
    assert "• Pro — Round 24 (Silverstone)" in text
    assert "Round 25" not in text
    assert await season_end_changes(league) == []


@pytest.mark.parametrize("case", ["an amendment opened", "already completed"])
async def test_a_refusal_found_when_the_completion_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, case,
):
    league = await pending_completion_league(tmp_path)
    interaction = await _asked(league)
    if case == "an amendment opened":
        await _amendment_open(league)
        text, status = AMENDED, "ACTIVE"
    else:
        await league.write("UPDATE seasons SET status = 'COMPLETED' WHERE id = ?", SEASON_ID)
        text, status = NO_SEASON, "COMPLETED"

    await run_queue(league.bot)

    assert text in reply(interaction)
    assert len(_refusal_lines(league)) == 1
    assert _refusal_lines(league)[0].startswith(REFUSAL)
    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "REFUSED"
    assert await _status(league) == status
    assert await _history(league) == []
    assert league.texts(PRO_CH.standings) == []
    assert league.revoked == {}


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_jobs_run_in_order(tmp_path):
    league = await pending_completion_league(tmp_path, attendance=True, test_mode=True)
    await _asked(league)

    await run_queue(league.bot)

    names = [name for name, _key in await _names(league)]
    jobs = [pair for pair in await _names(league) if pair[0] != "close_window"]
    assert jobs[:5] == [("settle", None), ("final_standings", PRO), ("final_sheet", PRO),
                        ("final_standings", AM), ("final_sheet", AM)]
    assert jobs[5:8] == [("revoke_roles", LEWIS), ("revoke_roles", MAX),
                         ("revoke_roles", CHARLES)]
    assert jobs[8:10] == [("flush_forecasts", None), ("end", None)]
    assert names[-3:] == ["discard_portraits", "discard_backup", "close"]
    between = names[names.index("end") + 1:names.index("discard_portraits")]
    assert set(between) <= {"signup_notice", "close_signup", "take_driver_role"}
    assert ("revoke_roles", TEST_DRIVER) not in jobs
    assert await _status(league) == "COMPLETED"


async def test_a_stale_division_is_finished_in_the_first_save_and_the_season_moved_on(tmp_path):
    league = await pending_completion_league(tmp_path, stage="ONGOING")
    await league.write("UPDATE divisions SET status = 'ACTIVE' WHERE id = ?", AM)
    await _asked(league)

    await _run_through(league, "settle")
    divisions = {row["id"]: row["status"]
                 for row in await league.rows("SELECT id, status FROM divisions")}
    assert divisions == {PRO: "FINISHED", AM: "FINISHED"}
    assert (await league.season())["stage"] == "PENDING_COMPLETION"
    assert await _status(league) == "ACTIVE"

    await run_queue(league.bot)
    assert await _status(league) == "COMPLETED"


async def test_a_season_in_an_ongoing_stage_with_every_division_done_is_wound_down_first(tmp_path):
    league = await pending_completion_league(tmp_path, stage="ONGOING_PLACEMENTS")
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id IN "
        "(SELECT id FROM driver_profiles WHERE discord_user_id = ?)", str(MAX),
    )
    await _asked(league)

    await run_queue(league.bot)

    names = [name for name, _key in await _names(league)]
    assert names[:4] == ["settle", "wind_down", "turn_down", "settle"]
    pending = await league.rows(
        "SELECT * FROM driver_season_assignments WHERE season_id = ? AND committed = 0", SEASON_ID
    )
    assert pending == []
    assert await _status(league) == "COMPLETED"


async def test_a_discarded_wind_down_completes_nothing(tmp_path):
    league = await pending_completion_league(tmp_path, stage="ONGOING_PLACEMENTS")
    interaction = await _asked(league)

    with _failing("turn_down_pending_placements_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("turn_down", None)
        await discard_job(league.bot)

    assert WIND_DOWN_DISCARDED in reply(interaction)
    assert _closing_lines(league) == []
    assert await _status(league) == "ACTIVE"
    assert (await league.season())["stage"] == "ONGOING_PLACEMENTS"
    assert await _history(league) == []


async def test_a_discarded_first_save_completes_nothing(tmp_path):
    league = await pending_completion_league(tmp_path)
    interaction = await _asked(league)
    league.bot.scheduler_service.cancel_season_end = MagicMock(
        side_effect=RuntimeError("the job store could not be read")
    )

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("settle", None)
    await discard_job(league.bot)

    assert SETTLE_DISCARDED in reply(interaction)
    assert _closing_lines(league) == []
    assert await _status(league) == "ACTIVE"
    assert league.texts(PRO_CH.standings) == []
    assert league.revoked == {}


async def test_a_discarded_end_save_completes_nothing_and_says_to_run_it_again(tmp_path):
    league = await pending_completion_league(tmp_path)
    interaction = await _asked(league)

    with _failing("complete_season_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
        await discard_job(league.bot)

    assert END_DISCARDED in reply(interaction)
    assert _closing_lines(league) == []
    assert await _status(league) == "ACTIVE"
    assert await _history(league) == []
    assert await driver_state(league, MAX) == "ASSIGNED"


async def test_each_division_s_final_classification_is_drawn_against_its_last_round_with_results(
    tmp_path,
):
    league = await pending_completion_league(tmp_path)
    await _asked(league)

    await run_queue(league.bot)

    rounds = {job["payload"]["division_id"]: job["payload"]["round_id"]
              for job in await _jobs(league) if job["name"] == "final_standings"}
    assert rounds == {PRO: round_id(PRO, 2), AM: round_id(AM, 1)}
    assert league.texts(PRO_CH.standings) and league.texts(AM_CH.standings)


async def test_a_division_that_ran_no_round_gets_no_final_classification(tmp_path):
    league = await pending_completion_league(tmp_path)
    await league.write("DELETE FROM driver_standings_snapshots WHERE division_id = ?", AM)
    await league.write("DELETE FROM session_results WHERE division_id = ?", AM)
    await _asked(league)

    await run_queue(league.bot)

    assert ("final_standings", AM) not in await _names(league)
    assert ("final_standings", PRO) in await _names(league)
    assert league.texts(AM_CH.standings) == []
    assert AM_ROLE in league.revoked[CHARLES]
    assert await _status(league) == "COMPLETED"


async def test_a_cancelled_division_gets_no_final_classification(tmp_path):
    league = await pending_completion_league(tmp_path, attendance=True)
    await league.write("UPDATE divisions SET status = 'CANCELLED' WHERE id = ?", AM)
    await league.write("UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?",
                       str(CHARLES))
    await _asked(league)

    await run_queue(league.bot)

    names = await _names(league)
    assert ("final_standings", AM) not in names and ("final_sheet", AM) not in names
    assert league.texts(AM_CH.standings) == [] and league.texts(AM_CH.attendance) == []
    assert {AM_ROLE, MCLAREN_ROLE, DRIVER_ROLE} <= set(league.revoked[CHARLES])
    assert ("103", "Am", 1) in await _history(league)
    assert await _status(league) == "COMPLETED"


@pytest.mark.parametrize("module", ["results", "attendance"])
async def test_a_module_switched_off_drops_its_final_posting(tmp_path, module):
    league = await pending_completion_league(tmp_path, attendance=True)
    await _asked(league)
    await league.switch(module, False)

    await run_queue(league.bot)

    channels = ((PRO_CH.standings, AM_CH.standings) if module == "results"
                else (PRO_CH.attendance, AM_CH.attendance))
    assert [league.texts(cid) for cid in channels] == [[], []]
    assert await stopped_job(league.db_path) is None
    assert await _status(league) == "COMPLETED"


async def test_a_retried_final_classification_is_posted_as_text(tmp_path):
    league = await pending_completion_league(tmp_path, images=True)
    standings = league.channel(PRO_CH.standings)
    standings.send_fails = _forbidden()
    await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("final_standings", PRO)

    standings.send_fails = None
    await retry_job(league.bot)

    assert standings.texts
    assert all(not files for files in standings.files)
    assert await _status(league) == "COMPLETED"


async def test_the_final_classification_is_posted_while_the_season_is_still_active(tmp_path):
    league = await pending_completion_league(tmp_path)
    seen: list[str] = []

    def _status_now(_content: Any, _kwargs: Any) -> bool:
        with sqlite3.connect(league.db_path) as db:
            seen.append(db.execute("SELECT status FROM seasons WHERE id = ?",
                                   (SEASON_ID,)).fetchone()[0])
        return False

    league.channel(PRO_CH.standings).fail_when = _status_now
    await _asked(league)

    await run_queue(league.bot)

    assert seen and set(seen) == {"ACTIVE"}
    assert await _status(league) == "COMPLETED"


async def test_every_real_driver_of_the_season_loses_their_roles_and_a_test_driver_is_passed_over(
    tmp_path,
):
    league = await pending_completion_league(tmp_path, test_mode=True)
    await _asked(league)

    await run_queue(league.bot)

    assert set(league.revoked[LEWIS]) == {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}
    assert set(league.revoked[MAX]) == {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}
    assert set(league.revoked[CHARLES]) == {AM_ROLE, MCLAREN_ROLE, DRIVER_ROLE}
    assert TEST_DRIVER not in league.revoked
    assert ("revoke_roles", TEST_DRIVER) not in await _names(league)


async def test_a_driver_approved_but_never_placed_loses_the_driver_role(tmp_path):
    league = await pending_completion_league(tmp_path)
    await approved_unplaced(league)
    await _asked(league)

    await run_queue(league.bot)

    assert ("take_driver_role", UNASSIGNED) in await _names(league)
    assert league.revoked[UNASSIGNED] == [DRIVER_ROLE]
    assert await driver_state(league, UNASSIGNED) is None
    assert await _status(league) == "COMPLETED"


async def test_a_driver_who_left_the_server_and_a_role_deleted_are_passed_over(tmp_path):
    league = await pending_completion_league(tmp_path)
    league.absent.add(MAX)
    league.roles_gone.add(FERRARI_ROLE)
    await _asked(league)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert MAX not in league.revoked
    assert set(league.revoked[LEWIS]) == {PRO_ROLE, DRIVER_ROLE}
    assert await _status(league) == "COMPLETED"
    assert len(_success_lines(league)) == 1


async def test_the_signup_window_is_closed_as_the_season_ends_before_the_driver_pass(tmp_path):
    league = await pending_completion_league(tmp_path, signups_open=True)
    await _asked(league)

    await _run_through(league, "close_window")
    assert not await window_open(league)
    assert await _history(league) == []
    assert await driver_state(league, MAX) == "ASSIGNED"
    assert await _status(league) == "ACTIVE"

    await run_queue(league.bot)
    assert await _status(league) == "COMPLETED"


async def test_the_driver_pass_returns_the_drivers_and_deletes_those_who_never_raced(tmp_path):
    league = await pending_completion_league(tmp_path)
    await _asked(league)

    await run_queue(league.bot)

    assert await driver_state(league, LEWIS) == "NOT_SIGNED_UP"
    assert await driver_state(league, MAX) is None
    assert await league.rows(
        "SELECT * FROM driver_season_assignments WHERE driver_profile_id = ?", MAX - 60
    ) == []
    assert not [row for row in await _history(league) if row[0] == str(MAX)]
    assert await league.rows("SELECT * FROM signup_records WHERE discord_user_id = ?", str(MAX))
    assert await league.rows("SELECT * FROM audit_entries WHERE change_type = 'DRIVER_PASS'")
    assert await _status(league) == "COMPLETED"


async def test_a_history_entry_is_written_for_every_division_each_driver_took_part_in(tmp_path):
    league = await pending_completion_league(tmp_path)
    await league.write("UPDATE driver_profiles SET former_driver = 1 WHERE discord_user_id = ?",
                       str(CHARLES))
    await _asked(league)

    await run_queue(league.bot)

    history = await _history(league)
    assert ("101", "Pro", 0) in history
    assert ("103", "Am", 0) in history
    assert (await _change(league))["state"] == "DONE"


async def test_test_mode_is_switched_off_in_the_save_before_the_season_is_archived(
    tmp_path, monkeypatch,
):
    failing = {"now": False}
    _flush_failing(monkeypatch, failing)
    league = await pending_completion_league(tmp_path, test_mode=True)
    await _asked(league)

    with _failing("complete_season_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == ("end", None)
    assert await driver_state(league, TEST_DRIVER) == "ASSIGNED"
    config = await league.rows("SELECT test_mode_active FROM server_configs")
    assert config[0]["test_mode_active"] == 1
    assert backup_saved(league)

    await retry_job(league.bot)

    names = [name for name, _key in await _names(league)]
    assert names.index("flush_forecasts") < names.index("end") < names.index("discard_backup")
    assert await driver_state(league, TEST_DRIVER) is None
    assert [row for row in await _history(league) if row[0] == str(TEST_DRIVER)]
    config = await league.rows("SELECT test_mode_active FROM server_configs")
    assert config[0]["test_mode_active"] == 0
    assert await _status(league) == "COMPLETED"
    assert not backup_saved(league)


async def test_the_portraits_of_the_drivers_deleted_are_discarded_after_the_save(
    tmp_path, monkeypatch,
):
    from leaguebot.image.services import driver_portrait_service

    calls: list[tuple[str, str]] = []
    holder: dict[str, str] = {}

    async def _discard(_bot: Any, accounts: Any) -> None:
        with sqlite3.connect(holder["db"]) as db:
            status = db.execute("SELECT status FROM seasons WHERE id = ?",
                                (SEASON_ID,)).fetchone()[0]
        calls.append((status, repr(accounts)))

    importlib.import_module("leaguebot.__main__")
    for module in _modules():
        if hasattr(module, "discard_portraits"):
            monkeypatch.setattr(module, "discard_portraits", _discard)
    monkeypatch.setattr(driver_portrait_service, "discard_portraits", _discard)
    league = await pending_completion_league(tmp_path)
    holder["db"] = league.db_path
    await _asked(league)

    await run_queue(league.bot)

    assert len(calls) == 1
    status, accounts = calls[0]
    assert status == "COMPLETED"
    assert str(MAX) in accounts and str(CHARLES) in accounts
    names = [name for name, _key in await _names(league)]
    assert names.index("end") < names.index("discard_portraits")


async def test_one_success_line_names_the_admin_and_the_season_after_the_last_job(tmp_path):
    league = await pending_completion_league(tmp_path)
    await _asked(league)

    await run_queue(league.bot)

    lines = _success_lines(league)
    assert len(lines) == 1
    assert lines[0].startswith(SUCCESS)
    assert "not done:" not in lines[0]
    jobs = await _jobs(league)
    assert jobs[-1]["name"] == "close" and all(job["done_at"] for job in jobs)


async def test_the_season_is_archived_audited_and_the_server_holds_no_active_season(tmp_path):
    league = await pending_completion_league(tmp_path)
    config = await league.rows("SELECT * FROM server_configs")
    await _asked(league)

    await run_queue(league.bot)

    assert await _status(league) == "COMPLETED"
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'SEASON_COMPLETED'"
    )
    assert len(await league.rows("SELECT * FROM divisions WHERE season_id = ?", SEASON_ID)) == 2
    assert len(await league.rows("SELECT * FROM rounds WHERE division_id IN (?, ?)", PRO, AM)) == 8
    assert await league.rows("SELECT * FROM seasons WHERE status = 'ACTIVE'") == []
    assert await league.rows("SELECT * FROM server_configs") == config


async def test_a_season_end_job_left_by_an_older_version_is_removed(tmp_path):
    league = await pending_completion_league(tmp_path)
    await _asked(league)
    assert len(await season_end_changes(league, SEASON_COMPLETE_KIND)) == 1
    league.bot.scheduler_service.cancel_season_end.assert_not_called()

    await run_queue(league.bot)

    league.bot.scheduler_service.cancel_season_end.assert_called_once()


async def test_a_backup_that_cannot_be_deleted_stops_the_queue_after_the_season_is_archived(
    tmp_path, monkeypatch,
):
    league = await pending_completion_league(tmp_path, test_mode=True)
    refusing = {"now": True}
    _backup_undeletable(monkeypatch, league, refusing)
    await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == ("discard_backup", None)
    assert await _status(league) == "COMPLETED"
    assert backup_saved(league)

    refusing["now"] = False
    await retry_job(league.bot)
    assert not backup_saved(league)
    assert len(_success_lines(league)) == 1


async def test_a_discarded_backup_deletion_is_named_with_the_toggle_that_deletes_it(
    tmp_path, monkeypatch,
):
    league = await pending_completion_league(tmp_path, test_mode=True)
    _backup_undeletable(monkeypatch, league, {"now": True})
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("discard_backup", None)

    await discard_job(league.bot)

    assert COMPLETED in reply(interaction)
    assert BACKUP_KEPT in _not_done(reply(interaction))
    [line] = _closing_lines(league)
    assert INCOMPLETE in line and f"  not done: {BACKUP_KEPT}" in line
    assert backup_saved(league)


async def _window_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await pending_completion_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    return league


async def _flush_refused(tmp_path: Any, monkeypatch: Any) -> Any:
    _flush_failing(monkeypatch, {"now": True})
    return await pending_completion_league(tmp_path, test_mode=True)


async def _lock_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await pending_completion_league(tmp_path)
    await signing_up(league)
    league.lock_fails[SIGNING_UP] = _forbidden()
    return league


async def _role_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await pending_completion_league(tmp_path)
    await approved_unplaced(league)
    league.revoke_fails[UNASSIGNED] = _forbidden()
    return league


async def _sheet_refused(tmp_path: Any, _monkeypatch: Any) -> Any:
    league = await pending_completion_league(tmp_path, attendance=True)
    league.channel(PRO_CH.attendance).send_fails = _forbidden()
    return league


#: Each job a league admin may discard: the league that makes it fail, where the queue stops,
#: and what the reply and the line say was not done.
_DISCARDS = {
    "close_window": (_window_refused, ("close_window", None), WINDOW_OPEN),
    "flush_forecasts": (_flush_refused, ("flush_forecasts", None), FORECASTS_KEPT),
    "close_signup": (_lock_refused, ("close_signup", SIGNING_UP), SIGNUP_KEPT),
    "take_driver_role": (_role_refused, ("take_driver_role", UNASSIGNED), DRIVER_ROLE_KEPT),
    "final_sheet": (_sheet_refused, ("final_sheet", PRO), PRO_SHEET_NOT_POSTED),
}


@pytest.mark.parametrize("job", sorted(_DISCARDS))
async def test_a_discarded_job_is_named_with_what_to_do_by_hand_and_the_line_says_incomplete(
    tmp_path, monkeypatch, job,
):
    build, where, text = _DISCARDS[job]
    league = await build(tmp_path, monkeypatch)
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == where

    await discard_job(league.bot)

    assert COMPLETED in reply(interaction)
    assert text in _not_done(reply(interaction))
    [line] = _closing_lines(league)
    assert INCOMPLETE in line
    assert f"  not done: {text}" in line
    assert await _status(league) == "COMPLETED"


# ── A signup channel's notice, before its lock (F2) ─────────────────────────────────


async def test_a_driver_returned_by_the_window_s_close_is_told_before_their_channel_is_locked(
    tmp_path,
):
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.hold_fails[SIGNING_UP] = _forbidden()
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)
    assert league.locked == []

    del league.hold_fails[SIGNING_UP]
    await retry_job(league.bot)

    names = await _names(league)
    assert names.index(("signup_notice", SIGNING_UP)) < names.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP][-2:] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert await _status(league) == "COMPLETED"


@pytest.mark.xfail(strict=True, reason=_XFAIL_NOTICE)
async def test_a_discarded_signup_notice_still_closes_the_channel_and_the_outcome_says_so(
    tmp_path,
):
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.hold_fails[SIGNING_UP] = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("signup_notice", SIGNING_UP)

    await discard_job(league.bot)

    assert league.locked == [SIGNING_UP]
    assert league.notices == []
    assert NOTICE_DISCARDED in _not_done(reply(interaction))
    [line] = _closing_lines(league)
    assert f"  not done: {NOTICE_DISCARDED}" in line
    assert await _status(league) == "COMPLETED"
