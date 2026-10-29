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
results accepted for Pro's rounds 1 and 2 and Am's round 1, the members holding their division, team
and driver roles, Lewis a former driver.
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
    change_rows,
    discard_job,
    http_error,
    member_interaction,
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
    SIGNUP_CHANNEL_BASE,
    TEST_DRIVER,
    UNASSIGNED,
    approved_unplaced,
    backup_saved,
    complete_season,
    driver_state,
    pending_completion_league,
    posted_forecast,
    reply,
    round_id,
    season_end_changes,
    signing_up,
    window_open,
    wizard_state,
    wizards_recovered,
)


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
WINDOW_OPEN = (
    "The signup window could not be closed. Close it with `/signup close`. "
    "Where a close time is set, run `/signup close-time cancel` first, and where a season's end "
    "or wind-down is still on the queue, let it finish first, as `/signup close` is refused "
    "until then."
)
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
    """Weather's reading of the forecasts posted under test mode (`forecast_deletes`, which
    plans a delete job for each), which the season end's `flush_forecasts` job makes, raises
    while *failing* says so; patched before the league is built, in every module that holds
    it, so the builder's hook reaches it however it binds it."""
    from leaguebot.weather.services import forecast_changes, forecast_cleanup_service

    reading = forecast_cleanup_service.forecast_deletes
    raising = _raising_while(failing, reading)
    for module in [*_modules(), forecast_changes]:
        if getattr(module, "forecast_deletes", None) is reading:
            monkeypatch.setattr(module, "forecast_deletes", raising)


def _raising_while(failing: dict[str, bool], reading: Any) -> Any:
    """*reading*, raising as a fault of the database would while *failing* says so."""

    async def _read(*args: Any, **kwargs: Any) -> Any:
        if failing["now"]:
            raise RuntimeError("the forecasts could not be read")
        return await reading(*args, **kwargs)

    return _read


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
    """The signup window is open and the completion queued; recording the window closed fails.
    The queue stops at `post_closed`, whose record writes the window's close, with the season
    still active and the window open; retried, the window closes and the season completes."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_closed", None)
    assert await _status(league) == "ACTIVE"
    assert await window_open(league)

    league.close_fails = None
    await retry_job(league.bot)
    assert not await window_open(league)
    assert await _status(league) == "COMPLETED"


@pytest.mark.parametrize("where", [
    "the flush", "the test drivers' deletion",
])
async def test_test_mode_that_cannot_be_switched_off_stops_the_queue(tmp_path, monkeypatch, where):
    failing = {"now": where == "the flush"}
    if where == "the flush":
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
    assert f"  not done: {_logged(ROLES_KEPT)}" in line


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
    """The signup window open and the completion queued. Run as far as `post_closed`, whose
    record writes the window's close, the window is closed while the season is still active,
    no history written and Max still assigned: the driver pass has not yet run. Driver 105,
    still filling in the wizard, is returned by the close. The window's close is audited once,
    by the system, and its one "🔒 Signups closed as the season ended" line written in that
    post's record: it names no member, and states the one driver returned beneath it (signup
    :67). The rest of the queue completes the season."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    await _asked(league)

    await _run_through(league, "post_closed")
    assert await _window_close_audits(league) == [
        ("SIGNUP_SEASON_END_CLOSE", "open", "closed", "system")
    ]
    [line] = await _closed_lines(league)
    assert "<@" not in line, line
    assert any("returned" in text and "1" in text for text in line.splitlines()[1:]), line
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


async def test_test_mode_is_switched_off_in_the_save_before_the_season_is_archived(tmp_path):
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
    "post_closed": (_window_refused, ("post_closed", None), WINDOW_OPEN),
    "flush_forecasts": (_flush_refused, ("flush_forecasts", None), FORECASTS_KEPT),
    "close_signup": (_lock_refused, ("close_signup", SIGNING_UP), SIGNUP_KEPT),
    "take_driver_role": (_role_refused, ("take_driver_role", UNASSIGNED), DRIVER_ROLE_KEPT),
    "final_sheet": (_sheet_refused, ("final_sheet", PRO), PRO_SHEET_NOT_POSTED),
}


@pytest.mark.parametrize("job", [
    job for job in sorted(_DISCARDS)
])
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
    assert f"  not done: {_logged(text)}" in line
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
    assert f"  not done: {_logged(NOTICE_DISCARDED)}" in line
    assert await _status(league) == "COMPLETED"


def _close_recording(service: Any) -> str:
    """The name of the form through which the signup service records the window closed:
    `set_window_closed_on`, which `post_closed`'s record calls on its save's connection from
    C66 on, or until then the committing `set_window_closed`."""
    return ("set_window_closed_on" if hasattr(service, "set_window_closed_on")
            else "set_window_closed")


def _closes_then_fails_once(league: Any) -> None:
    """Recording the window closed goes through, then fails once, after the close has returned
    its drivers: as an audit that cannot be written would make it."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    failing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        closed = await closing(*args, **kwargs)
        if failing["now"]:
            failing["now"] = False
            raise RuntimeError("disk I/O error")
        return closed

    setattr(service, name, _set_window_closed)


async def test_a_window_close_that_stopped_after_returning_its_drivers_still_tells_and_closes_them(
    tmp_path,
):
    """The signup window open with a driver still filling in the wizard, and the completion
    queued. The window's close returns the driver; `post_closed`'s record then writes the window
    closed and fails, so its save is undone: the queue stops at `post_closed` with the window
    still open and the driver Not Signed Up, told nothing. Retried, the window is recorded
    closed, the driver's notice is posted before their channel's lock, and the season is
    completed."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _closes_then_fails_once(league)
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_closed", None)
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)
    assert league.notices == [] and league.locked == []

    await retry_job(league.bot)

    assert not await window_open(league)
    names = await _names(league)
    assert names.index(("signup_notice", SIGNING_UP)) < names.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert await _status(league) == "COMPLETED"


class _Killed(BaseException):
    """The bot's process killed: no handler of the bot's sees it, nothing more is saved."""


def _killed_before_the_window_is_recorded_closed(league: Any) -> None:
    """The process is killed as the window's close comes to record the window closed: once,
    after the close has returned its drivers, their transitions committed, and before the job's
    mark or any result of its is saved. The bot started again closes as it should."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    killing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        if killing["now"]:
            killing["now"] = False
            raise _Killed()
        return await closing(*args, **kwargs)

    setattr(service, name, _set_window_closed)


async def test_a_window_close_cut_off_by_a_kill_after_returning_its_drivers_still_tells_and_closes_them_at_start(
    tmp_path,
):
    """The signup window open with a driver still filling in the wizard, and the completion
    queued. The bot is killed in the window's close, after the driver is returned to Not Signed
    Up, as `post_closed` comes to record the window closed and before its mark is saved. When
    the bot starts again, the close is finished as for a recorded stop: the driver it had
    returned is told, then their channel closed, and the season is completed."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _killed_before_the_window_is_recorded_closed(league)
    await _asked(league)

    with pytest.raises(_Killed):
        await run_queue(league.bot)
    [closed] = [job for job in await _jobs(league) if job["name"] == "post_closed"]
    assert closed["done_at"] is None and closed["result"] is None
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)

    await league.restart()
    await run_queue(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    names = await _names(league)
    assert names.index(("signup_notice", SIGNING_UP)) < names.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert not await window_open(league)
    assert await _status(league) == "COMPLETED"


@pytest.mark.parametrize("failures", [1, 2], ids=["fails once", "fails again on the retry"])
async def test_a_window_close_whose_closing_record_fails_keeps_its_drivers_for_the_retry(
    tmp_path, failures,
):
    """The signup window open with driver 105 still filling in the wizard, and the completion
    queued. The close returns 105, then recording the window closed fails: the queue stops at
    `post_closed`, with the window still open and 105 Not Signed Up, told nothing. Where the
    first Retry fails the same way, it stops there again. Once the record goes through, 105 is told, then
    their channel closed, each once, and the season is completed."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    league.close_fails = RuntimeError("the window could not be recorded closed")
    await _asked(league)

    await run_queue(league.bot)
    for attempt in range(failures):
        if attempt:
            await retry_job(league.bot)
        assert await _stopped_at(league) == ("post_closed", None)
        assert await window_open(league)
        assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
        assert league.notices == [] and league.locked == []

    league.close_fails = None
    await retry_job(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    names = await _names(league)
    assert names.count(("signup_notice", SIGNING_UP)) == 1
    assert names.count(("close_signup", SIGNING_UP)) == 1
    assert names.index(("signup_notice", SIGNING_UP)) < names.index(("close_signup", SIGNING_UP))
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert not await window_open(league)
    assert await _status(league) == "COMPLETED"


# ── A signup's end, marked in the save that ends it (amendment K2) ───────────────────

#: How a driver returned and left untold would be named, in the reply and the line: never for a
#: discarded closed notice, which drops only that post (b15-2).
RETURNED_NOT_TOLD = (
    f"<@{SIGNING_UP}> was returned to Not Signed Up when signups closed, but was not told and "
    "their channel was not closed: tell them and delete it by hand."
)
#: The head of the line a season end's close writes, with its audit, in `post_closed`'s record.
CLOSED_AS_THE_SEASON_ENDED = "🔒 Signups closed as the season ended"


async def _window_close_audits(league: Any) -> list[tuple[str, str, str, str]]:
    """Each audit record of the window's close as the season ends, with who it names."""
    rows = await league.rows(
        "SELECT change_type, old_value, new_value, actor_name FROM audit_entries "
        "WHERE change_type = ?",
        "SIGNUP_SEASON_END_CLOSE",
    )
    return [(row["change_type"], row["old_value"], row["new_value"], row["actor_name"])
            for row in rows]


async def _closed_lines(league: Any) -> list[str]:
    """Each line, sent or still queued, saying signups closed as the season ended."""
    from tests.support.change_queue import queued_log_lines

    lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
    return [line for line in lines if CLOSED_AS_THE_SEASON_ENDED in line]


#: How a driver the season's end cannot tell for want of a signup channel is named.
NO_CHANNEL = f"<@{SIGNING_UP}> had no signup channel left to tell"


async def _owed(league: Any) -> list[str]:
    """The accounts still owed their closing notice, as signup keeps them."""
    return await league.bot.signup_module_service.owed_closing_notices()


async def _mid_wizard(league: Any, user_id: int = SIGNING_UP, *,
                      state: str = "PENDING_SIGNUP_COMPLETION") -> None:
    """`signing_up`'s driver at *state*, their wizard collecting their notes: a wizard state the
    signup module reads back, as a restart does."""
    await signing_up(league, user_id, state=state)
    await league.write(
        "UPDATE signup_wizard_records SET wizard_state = 'COLLECTING_NOTES' "
        "WHERE discord_user_id = ?",
        str(user_id),
    )


def _killed_after_the_window_is_recorded_closed(league: Any) -> None:
    """The process is killed straight after the window's close has recorded the window closed:
    once, before the close's audit is written and before the job's mark or any result of its is
    saved. The bot started again closes as it should."""
    service = league.bot.signup_module_service
    name = _close_recording(service)
    closing = getattr(service, name)
    killing = {"now": True}

    async def _set_window_closed(*args: Any, **kwargs: Any) -> Any:
        closed = await closing(*args, **kwargs)
        if killing["now"]:
            killing["now"] = False
            raise _Killed()
        return closed

    setattr(service, name, _set_window_closed)


async def test_a_window_close_cut_off_by_a_kill_after_the_window_is_recorded_closed_still_tells_and_closes_its_drivers_at_start(
    tmp_path,
):
    """The signup window open with driver 105 still filling in the wizard, and the completion
    queued. The bot is killed in `post_closed`'s record straight after it writes the window
    closed, before its save commits: the window is still open. When the bot starts again, the
    close is not dropped as no longer due: 105 is told once, then their channel locked, the
    season completed, and 105 is owed nothing more."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _killed_after_the_window_is_recorded_closed(league)
    await _asked(league)

    with pytest.raises(_Killed):
        await run_queue(league.bot)
    [closed] = [job for job in await _jobs(league) if job["name"] == "post_closed"]
    assert closed["done_at"] is None and closed["result"] is None
    assert await driver_state(league, SIGNING_UP) == "NOT_SIGNED_UP"
    assert await window_open(league)

    await league.restart()
    await run_queue(league.bot)

    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert not await window_open(league)
    assert await _status(league) == "COMPLETED"
    assert await _owed(league) == []


@pytest.mark.parametrize("refused", ["the record", "the post"])
async def test_a_discarded_closed_notice_still_tells_and_closes_the_drivers_the_close_returned(
    tmp_path, refused,
):
    """The signup window open with driver 105 still filling in the wizard. The window's close
    returns 105, then `post_closed` fails: where *refused* is "the record", recording the window
    closed fails; where "the post", Discord refuses the "Signups are now closed" post itself. A
    league admin discards it. Only that post is dropped (b15-2): where its record failed, the
    "Signups are now closed" message already sent is left standing; where Discord refused it,
    none stands. 105 is still told, then their channel locked, by their own jobs; the
    reply and the closing line say the window was not closed, to be closed with `/signup close`,
    and never name 105 as returned but not told; the window is still recorded open, with no
    audit of its close and no "🔒 Signups closed" line, the season completed, and 105 is owed
    nothing more. The closing line reads "| Incomplete" for the window alone, and nothing not
    done, there or in the reply, names 105."""
    from tests.support.season_league import SIGNUP_CHANNEL

    league = await pending_completion_league(tmp_path, signups_open=True)
    await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    if refused == "the record":
        league.close_fails = RuntimeError("the window could not be recorded closed")
    else:
        league.channel(SIGNUP_CHANNEL).send_fails = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("post_closed", None)
    assert league.notices == [] and league.locked == []

    await discard_job(league.bot)

    closed_notices = [message for message in league.channel(SIGNUP_CHANNEL).messages.values()
                      if "Signups are now closed" in str(message.content)]
    # Where the post went out and only its record failed, it is left standing (the owner:
    # "Leave it standing"); where Discord refused it, none stands.
    assert len(closed_notices) == (1 if refused == "the record" else 0)
    assert league.notices == [(SIGNING_UP, SIGNUPS_CLOSED)]
    assert league.locked == [SIGNING_UP]
    assert [(kind, uid) for kind, uid, _n in league.events
            if kind in ("notice", "lock") and uid == SIGNING_UP] == [
        ("notice", SIGNING_UP), ("lock", SIGNING_UP),
    ]
    assert WINDOW_OPEN in _not_done(reply(interaction))
    assert RETURNED_NOT_TOLD not in reply(interaction)
    # The window alone is not done, in the reply as in the closing line, which reads
    # "| Incomplete".
    assert _not_done(reply(interaction)) == [WINDOW_OPEN]
    [line] = _closing_lines(league)
    assert f"  not done: {_logged(WINDOW_OPEN)}" in line
    assert _logged(RETURNED_NOT_TOLD) not in line
    assert line.startswith(INCOMPLETE)
    not_done = [each.split("  not done: ", 1)[1] for each in line.splitlines()
                if each.startswith("  not done: ")]
    assert not_done == [_logged(WINDOW_OPEN)]
    assert await window_open(league)
    assert await _window_close_audits(league) == []
    assert await _closed_lines(league) == []
    assert await _status(league) == "COMPLETED"
    assert await _owed(league) == []


async def _withdrew_with_the_notice_refused(league: Any, user_id: int = SIGNING_UP) -> Any:
    """Driver *user_id*, part-way through the wizard, presses Cancel Signup in their signup
    channel, on a view handed the change queue, and Discord refuses the closing notice there.
    Once the queue has run they are Not Signed Up (the league's `signup.end` done), and the turns
    in their channel stop at `signup_notice`: nothing posted, their typing not revoked, no
    deletion armed (C76, decision 25). Gives the channel."""
    from leaguebot.signup.services.signup_views import WithdrawButtonView

    await _mid_wizard(league, user_id)
    kept = MagicMock(spec=discord.TextChannel)
    kept.id = SIGNUP_CHANNEL_BASE + user_id
    kept.mention = f"<#{kept.id}>"
    kept.send = AsyncMock(side_effect=_forbidden())
    kept.set_permissions = AsyncMock(return_value=None)
    league.channels[kept.id] = kept
    # Today's withdrawal off the queue, a stand-in that does nothing, so that only what the
    # press asks of the queue shows.
    league.bot.wizard_service.withdraw = AsyncMock(return_value=None)
    press = member_interaction(league.bot, user=league._member(user_id))
    press.guild = league.guild
    press.channel_id = kept.id
    press.channel = kept
    await WithdrawButtonView(str(user_id), league.bot.change_queue).withdraw_button.callback(press)
    await run_queue(league.bot)
    job = await stopped_job(league.db_path, _lane_of(user_id))
    assert job is not None and job["name"] == "signup_notice"
    assert not any(call.kwargs.get("send_messages") is False
                   for call in kept.set_permissions.call_args_list)
    return kept


def _lane_of(user_id: int = SIGNING_UP) -> str:
    """The lane of driver *user_id*'s signup channel."""
    return f"channel:{SIGNUP_CHANNEL_BASE + user_id}"


def _deletions_armed(league: Any) -> list[Any]:
    """Each 24-hour deletion of a signup channel armed through the scheduler service."""
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == "signup_channel_delete"]




async def test_a_signup_channel_kept_after_a_refused_notice_is_not_reached_by_the_season_s_end(
    tmp_path,
):
    """Driver 105 withdrew while the signup window stood open, and Discord refused their
    closing notice, so the turns in their signup channel stand stopped at `signup_notice`. The
    completion then closes the window: it plans no `signup_notice` and no `close_signup` for
    105, locks nothing of theirs and arms no deletion; their channel is told nothing more, and
    their stopped job is left stopped for Retry or Discard (decision 24)."""
    league = await pending_completion_league(tmp_path, signups_open=True)
    kept = await _withdrew_with_the_notice_refused(league)
    await _asked(league)

    await run_queue(league.bot)

    names = await _names(league)
    assert ("signup_notice", SIGNING_UP) not in names
    assert ("close_signup", SIGNING_UP) not in names
    assert SIGNING_UP not in league.locked
    assert league.notices == []
    assert _deletions_armed(league) == []
    assert kept.send.await_count == 1
    job = await stopped_job(league.db_path, _lane_of())
    assert job is not None and job["name"] == "signup_notice"
    assert await _status(league) == "COMPLETED"


async def test_the_end_save_marks_an_in_progress_signup_over(tmp_path):
    """Driver 105 is part-way through the wizard (Pending Signup Completion, their wizard
    collecting their notes) when the completion's `end` runs its driver pass. Their wizard is
    unengaged in that save, before any job of theirs runs, and a restart then arms no
    inactivity job for them and expires nothing."""
    league = await pending_completion_league(tmp_path)
    await _mid_wizard(league)
    await _asked(league)

    await _run_through(league, "end")

    assert await driver_state(league, SIGNING_UP) is None
    assert await wizard_state(league, SIGNING_UP) == "UNENGAGED"
    await run_queue(league.bot)
    assert await wizards_recovered(league) == {"armed": [], "expired": []}


def _channel_gone(league: Any, user_id: int = SIGNING_UP) -> None:
    """Driver *user_id*'s signup channel is gone from the server: deleted before their
    `signup_notice` ran, so there is nothing to tell or lock in it."""
    league.remove_channel(SIGNUP_CHANNEL_BASE + user_id)


def _locks_asked(league: Any, user_id: int = SIGNING_UP) -> list[Any]:
    """Each permission edit taking driver *user_id*'s typing away in their signup channel, the
    channel looked for on the server and among those removed from it."""
    cid = SIGNUP_CHANNEL_BASE + user_id
    held = league.channels.get(cid) or league._removed.get(cid)
    if held is None:
        return []
    return [call for call in held.set_permissions.call_args_list
            if call.kwargs.get("send_messages") is False]


def _deletion_armed_for(league: Any, user_id: int = SIGNING_UP) -> bool:
    """Whether a deletion of driver *user_id*'s signup channel was armed: through the scheduler
    service's `signup_channel_delete` timer, or the wizard service's own job on the scheduler."""
    scheduler = league.bot.scheduler_service
    return any(
        call.args[:2] == ("signup_channel_delete", str(user_id))
        for call in scheduler.arm_once.call_args_list
    ) or any(
        call.kwargs.get("id") == f"wizard_channel_delete_{user_id}"
        for call in scheduler._scheduler.add_job.call_args_list
    )


@pytest.mark.parametrize("whose", ["returned by the window's close", "in review"])
async def test_a_driver_with_no_signup_channel_left_is_named_and_nothing_is_locked(
    tmp_path, whose,
):
    """Driver 105 has no signup channel left when the completion comes to tell them: either the
    window's close returned them and their channel was deleted before `signup_notice` ran, or
    the driver pass returned them from review with no channel. Nothing is locked or set for
    deletion, and they are named: "<@105> had no signup channel left to tell", in the reply, under
    no warning heading as nothing was discarded, on a line of its own beneath "✅ Season marked
    as complete.", and, with the mention backticked, under `not done:` in the closing line,
    which reads "| Success" and its season line, with that `not done:` line straight after."""
    if whose == "in review":
        league = await pending_completion_league(tmp_path)
        await signing_up(league)
        await league.write(
            "UPDATE signup_wizard_records SET signup_channel_id = NULL WHERE discord_user_id = ?",
            str(SIGNING_UP),
        )
    else:
        league = await pending_completion_league(tmp_path, signups_open=True)
        await signing_up(league, state="PENDING_SIGNUP_COMPLETION")
    _channel_gone(league)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert ("signup_notice", SIGNING_UP) in await _names(league)
    assert NO_CHANNEL in reply(interaction)
    assert NOT_EVERYTHING not in reply(interaction)
    # The completion still succeeds: the reply says so, with the driver on a line of its own,
    # and the closing line reads "| Success", then its season line, then the driver.
    assert COMPLETED in reply(interaction)
    assert NO_CHANNEL in reply(interaction).splitlines()
    [line] = _closing_lines(league)
    assert f"  not done: {_logged(NO_CHANNEL)}" in line
    assert line.startswith(f"{SUCCESS}\n  not done: {_logged(NO_CHANNEL)}")
    assert _locks_asked(league) == []
    assert SIGNING_UP not in league.locked
    assert not _deletion_armed_for(league)
    assert league.notices == []
    assert await _status(league) == "COMPLETED"


async def _in_review_with_no_channel(league: Any) -> None:
    """Driver 105 in review, their signup channel gone, for the driver pass to return."""
    await signing_up(league)
    await league.write(
        "UPDATE signup_wizard_records SET signup_channel_id = NULL WHERE discord_user_id = ?",
        str(SIGNING_UP),
    )
    _channel_gone(league)


async def test_a_completion_with_a_discard_and_a_driver_with_no_channel_left_reads_incomplete(
    tmp_path,
):
    """Driver 105, in review, has no signup channel left, and Discord refuses to take Lewis's
    roles back, which a league admin discards. The closing line reads "| Incomplete", with both
    beneath it under `not done:`; the reply's "Not everything could be done" section holds
    both."""
    league = await pending_completion_league(tmp_path)
    await _in_review_with_no_channel(league)
    league.revoke_fails[LEWIS] = _forbidden()
    interaction = await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == ("revoke_roles", LEWIS)

    del league.revoke_fails[LEWIS]
    await discard_job(league.bot)

    [line] = _closing_lines(league)
    assert line.startswith(INCOMPLETE)
    assert f"  not done: {_logged(ROLES_KEPT)}" in line
    assert f"  not done: {_logged(NO_CHANNEL)}" in line
    assert {ROLES_KEPT, NO_CHANNEL} <= set(_not_done(reply(interaction)))
    assert await _status(league) == "COMPLETED"


#: Work in hand that is not an end of the season, as a request about it would find it: each kind,
#: and its payload.
_OTHER_WORK = {
    "a round's cancellation": ("season.round.cancel", {"round_id": round_id(PRO, 3)}),
    "a division's cancellation": ("season.division.cancel", {"division_id": AM}),
    "a /round amend": ("season.round.amend", {"round_id": round_id(PRO, 4)}),
    "a review open": ("results.review.open", {"round_id": round_id(PRO, 2)}),
}


@pytest.mark.parametrize("work", list(_OTHER_WORK))
@pytest.mark.parametrize("stopped", [False, True], ids=["waiting", "stopped"])
async def test_work_in_hand_that_is_not_the_season_s_end_does_not_refuse_it(
    tmp_path, work, stopped,
):
    """Only another end of the season refuses a season's end. With a round's cancellation, a
    division's cancellation, a `/round amend` or a review open waiting on the queue, or stopping
    it, `/season complete` on the season pending completion is acknowledged, no refusal line is
    written, and the completion is queued behind that work."""
    league = await pending_completion_league(tmp_path)
    kind, payload = _OTHER_WORK[work]
    await _seed_change(league, kind, payload, stopped=stopped)

    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    assert _refusal_lines(league) == []
    rows = await change_rows(league.db_path)
    assert [row["kind"] for row in rows][-2:] == [kind, SEASON_COMPLETE_KIND]
    assert rows[-1]["state"] == "QUEUED"


async def test_the_final_sheet_replaces_nothing_and_saves_no_id(tmp_path):
    """Attendance is on, and Pro's attendance channel holds its last round's sheet, 6200,
    recorded as its live one. Completing the season posts Pro's final sheet beside it: once the
    final sheet is posted, 6200 is still the live sheet recorded; once the season is complete,
    6200 still stands, nothing was deleted from the channel, and no job deleted a sheet."""
    old_sheet = 6200
    league = await pending_completion_league(tmp_path, attendance=True)
    await league.write(
        "UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?",
        str(old_sheet), PRO,
    )
    league.channel(PRO_CH.attendance).seed(old_sheet, "Pro's last round's sheet")
    await _asked(league)

    def _sheet_events() -> list[tuple[str, int]]:
        return [(kind, mid) for kind, cid, mid in league.events if cid == PRO_CH.attendance]

    for _ in range(100):
        if _sheet_events():
            break
        await run_queue(league.bot, steps=1)
    [(sent, _final)] = _sheet_events()
    assert sent == "send"
    [row] = await league.rows(
        "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?", PRO,
    )
    assert str(row["attendance_message_id"]) == str(old_sheet)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert await _status(league) == "COMPLETED"
    assert old_sheet in league.channel(PRO_CH.attendance).messages
    assert [kind for kind, _ in _sheet_events()] == ["send"]
    names = [job["name"] for job in await _jobs(league)]
    assert "delete_replaced" not in names


# ── The forecasts posted under test mode, each deleted by a job of its own ──────────

#: The forecasts standing as the season is completed: Pro's Phase 1 and 2 of round 4 and Am's
#: Phase 1 of round 4, each with the channel it stands in.
TEST_FORECASTS = {9401: (PRO, 1, PRO_CH.forecast), 9402: (PRO, 2, PRO_CH.forecast),
                  9501: (AM, 1, AM_CH.forecast)}


async def _test_forecasts_posted(tmp_path: Any) -> Any:
    """A season pending completion under test mode, with `TEST_FORECASTS` standing."""
    league = await pending_completion_league(tmp_path, test_mode=True)
    for message_id, (division, phase, _channel) in TEST_FORECASTS.items():
        await posted_forecast(league, round_id(division, 4), phase, message_id)
    return league


async def test_each_forecast_posted_under_test_mode_is_deleted_by_a_job_of_its_own(tmp_path):
    """Season 3 is pending completion under test mode, and three forecasts posted under it
    stand: Pro's Phase 1 and 2 of round 4 and Am's Phase 1 of round 4. Admin 77 completes the
    season and the queue runs. After `flush_forecasts`, each forecast is deleted from its forecast
    channel by a `delete_forecast` job of its own; no forecast record is left, and the season is
    completed."""
    league = await _test_forecasts_posted(tmp_path)
    await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert await _status(league) == "COMPLETED"
    names = [name for name, _key in await _names(league)]
    deletes = [i for i, name in enumerate(names) if name == "delete_forecast"]
    assert len(deletes) == 3
    assert names.index("flush_forecasts") < min(deletes)
    deleted = {(cid, mid) for kind, cid, mid in league.events if kind == "delete"}
    assert {(c, m) for m, (_d, _p, c) in TEST_FORECASTS.items()} <= deleted
    for message_id, (_division, _phase, channel) in TEST_FORECASTS.items():
        assert message_id not in league.channel(channel).messages
    assert await league.rows("SELECT * FROM forecast_messages") == []


async def test_a_forecast_that_will_not_delete_stops_the_completion_and_once_discarded_is_named_with_its_link(
    tmp_path,
):
    """As above, but Discord refuses to delete Pro's Phase 1 forecast of round 4 (9401): the
    queue stops at its `delete_forecast` job, the forecast and its record kept. A league admin
    discards it: the season is completed, the other two forecasts are deleted, and the reply and
    the closing line (Incomplete) name 'The Phase 1 forecast of round 4 could not be deleted:
    delete it by hand (…)' with the message's link."""
    league = await _test_forecasts_posted(tmp_path)
    league.undeletable.add(9401)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == ("delete_forecast", PRO)
    assert 9401 in league.channel(PRO_CH.forecast).messages
    kept = await league.rows("SELECT message_id FROM forecast_messages")
    assert 9401 in [int(row["message_id"]) for row in kept]

    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await _status(league) == "COMPLETED"
    assert 9402 not in league.channel(PRO_CH.forecast).messages
    assert 9501 not in league.channel(AM_CH.forecast).messages
    named = "The Phase 1 forecast of round 4 could not be deleted: delete it by hand ("
    [entry] = [line for line in _not_done(reply(interaction)) if named in line]
    assert "9401" in entry
    [line] = _closing_lines(league)
    assert INCOMPLETE in line and named in line and "9401" in line


async def _forecast_in_two_parts(tmp_path: Any) -> Any:
    """A season pending completion under test mode, with Pro's Phase 1 forecast of round 4 posted
    in two messages: 9400, then 9401, the last, which its record names."""
    from tests.support.season_league import _refusing_delete

    league = await pending_completion_league(tmp_path, test_mode=True)
    await posted_forecast(league, round_id(PRO, 4), 1, 9401)
    forecast = league.channel(PRO_CH.forecast)
    forecast.seed(9400, "Phase 1 forecast, first part")
    _refusing_delete(league, forecast.messages[9400])
    await league.write("UPDATE forecast_messages SET message_ids = ? WHERE message_id = ?",
                       "[9400, 9401]", 9401)
    return league


async def test_a_forecast_posted_in_two_parts_is_deleted_whole_when_the_season_is_completed(
    tmp_path,
):
    """Pro's Phase 1 forecast of round 4 stands as two messages. Admin 77 completes the season
    and the queue runs: both parts are deleted, the season is completed and the queue does not
    stop."""
    league = await _forecast_in_two_parts(tmp_path)
    await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert await _status(league) == "COMPLETED"
    assert not {9400, 9401} & set(league.channel(PRO_CH.forecast).messages)
    assert await league.rows("SELECT * FROM forecast_messages") == []


async def test_a_discarded_deletion_of_a_forecast_s_earlier_part_is_named_with_its_link(tmp_path):
    """As above, but Discord refuses to delete the first part (9400). The queue stops at the
    deletion of that part; a league admin discards it. The season is completed, and the reply and
    the closing line (Incomplete) name the earlier Phase 1 forecast as to be deleted by hand,
    with the message's link."""
    league = await _forecast_in_two_parts(tmp_path)
    league.undeletable.add(9400)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == ("delete_replaced", None)
    assert 9400 in league.channel(PRO_CH.forecast).messages

    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await _status(league) == "COMPLETED"
    named = "its earlier Phase 1 forecast could not be deleted ("
    [entry] = [line for line in _not_done(reply(interaction)) if named in line]
    assert "9400" in entry and "delete it by hand" in entry
    [line] = _closing_lines(league)
    assert INCOMPLETE in line and named in line and "9400" in line


# ── A refused standings picture: its text in the same try (owner, 2026-10-13) ──────────────


@pytest.mark.parametrize("refused", ["the picture", "everything"])
async def test_a_refused_final_standings_picture_is_posted_as_text_in_the_same_try(
    tmp_path, monkeypatch, refused,
):
    """Images on, both championships drawn. Pro's standings channel refuses (the picture) every
    send carrying a picture: the final standings go out as text in the same try, the job is
    done without stopping the queue and the season is completed, as a refused round's
    standings picture is ("No, same try everywhere"); nothing is recorded of them, as before.
    Where it refuses (everything) the text too, the queue stops at the job and the season is
    not completed."""
    from tests.support.season_league import draw_standings_pictures, refuses_pictures

    league = await pending_completion_league(tmp_path, images=True)
    render = draw_standings_pictures(monkeypatch, tmp_path)
    standings = league.channel(PRO_CH.standings)
    if refused == "the picture":
        standings.fail_when = refuses_pictures
    else:
        standings.send_fails = _forbidden()
    await _asked(league)

    await run_queue(league.bot)

    assert render.await_count >= 1, "the picture was never drawn"
    assert any(call.kwargs.get("file") for call in standings.send.await_args_list), \
        "no picture was sent to be refused"
    if refused == "everything":
        assert await _stopped_at(league) == ("final_standings", PRO)
        assert await _status(league) != "COMPLETED"
        return
    assert await _stopped_at(league) is None
    texts = league.texts(PRO_CH.standings)
    assert any("**Driver Standings**" in text for text in texts), texts
    assert any("**Team Standings**" in text for text in texts), texts
    assert all(not files for files in standings.files), "a picture was posted"
    assert await _status(league) == "COMPLETED"

