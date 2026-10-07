"""Approving a season on the change queue (#439, slice 4a).

✅ Approve on `/season placements-review` runs its gates and, under test mode, the backup
question at the press, then asks the change queue for the season's approval. It is checked when
pressed and again when it runs; the sessions, the placements, the points and the season's state are
saved in one save; letting go of the setup held in memory, the arming of the timed work, each
driver's roles, the batch notice and each division's lineup, calendar, opening standings and
opening sheet follow as jobs, each of which stops the queue when it fails. While an approval is in
hand, a second is refused without naming its job. Where the outcome can no longer reach the member,
the review's channel is told, a refusal as the approval comes up to run included.

Every test drives the real cog and the real queue through `tests/support/season_league.py`, with
"now" pinned. The change type is unbuilt until the build, so each is marked to fail until then:
today the press approves on the spot, inside the button.
"""
from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.services.season_service import SeasonService
from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
)
from tests.support.season_league import (
    ADMIN_ID,
    AM,
    CHARLES,
    FERRARI_ROLE,
    LEWIS,
    MAX,
    MCLAREN_ROLE,
    PRO,
    PRO_ROLE,
    AM_ROLE,
    REVIEW_CHANNEL,
    SEASON_ID,
    SEASON_NUMBER,
    TEST_DRIVER,
    DIVISIONS,
    approval_changes,
    post_review,
    press_approve,
    reply,
    round_id,
    season_league,
)
from tests.support.undecorate import undecorate


PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
APPROVED = "✅ **Season approved and activated!**\nSeason #3 (ID: 7)"
NOT_EVERYTHING = "⚠️ **Not everything could be done**"
CHANNEL_APPROVED = (
    f"✅ <@{ADMIN_ID}> — Season #3 is approved and ongoing. Your confirmation could not be sent "
    "to you privately; the log channel has what it said."
)
CHANNEL_NOT_APPROVED = (
    f"⛔ <@{ADMIN_ID}> — Season #3 was not approved. Your reply could not be updated; the log "
    "channel has why. Run `/season placements-review` again."
)
LATE_REFUSAL_HEAD = (
    f"⛔ <@{ADMIN_ID}> — Season #3 was not approved when its turn came on the queue, and your "
    "reply could no longer be updated:"
)
ALREADY = (
    "⏳ This season is already being approved, so this press approves nothing. If that approval "
    "has stopped, a league manager or admin can press Retry, or a league admin Discard, on its "
    "notice in the log channel."
)
NOT_SAVED = (
    "Nothing has been approved: the season is still in placements. Run `/season "
    "placements-review` again."
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _pressed(league: Any, **kwargs: Any) -> Any:
    """Press ✅ Approve, the press raising nothing."""
    press = await press_approve(league, **kwargs)
    assert not league.errors, league.errors
    return press


async def _approval(league: Any) -> dict[str, Any]:
    rows = await approval_changes(league)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    return await step_rows(league.db_path, (await _approval(league))["id"])


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the approval's first job called *name* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the approval's job {name} never finished: {await _jobs(league)}")


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


def _refused(league: Any, cid: int) -> None:
    """Channel *cid* refuses every send, as Discord refusing it would."""
    league.channel(cid).send_fails = http_error(discord.Forbidden, status=403,
                                                text="Missing Access")


def _refuse_starting(league: Any, cid: int, start: str) -> None:
    """Channel *cid* refuses every send whose text starts *start*."""
    league.channel(cid).fail_when = lambda content, _kwargs: (content or "").startswith(start)


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch season_service's *name* with *new*, and the change type's own reference to it."""
    from leaguebot.core.services import season_approval_change, season_service

    stack = ExitStack()
    for module in (season_service, season_approval_change):
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _failing_save() -> ExitStack:
    """The save's commitment of the placements raises, as a fault of the database would."""
    return _everywhere("commit_placements_on", AsyncMock(side_effect=RuntimeError("disk I/O error")))


async def _sessions(league: Any) -> dict[int, int]:
    rows = await league.rows("SELECT round_id, COUNT(*) AS n FROM sessions GROUP BY round_id")
    return {row["round_id"]: row["n"] for row in rows}


async def _committed(league: Any) -> int:
    rows = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE committed = 1"
    )
    return rows[0]["n"]


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _confirmed_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league)
            if "| /season placements-review | Placements confirmed" in line]


def _all_rounds() -> list[int]:
    return [round_id(division, n) for division in (PRO, AM) for n in range(1, 5)]


def _armed(league: Any) -> dict[str, list[int]]:
    """The rounds armed, by the kind of timed work: weather, results or attendance."""
    armed: dict[str, list[int]] = {}
    for which, rounds in league.armed:
        armed.setdefault(which, []).extend(rounds)
    return {which: sorted(rounds) for which, rounds in armed.items()}


def _not_done(text: str) -> list[str]:
    """The bullets of the reply's "Not everything could be done" section, in order."""
    assert NOT_EVERYTHING in text, text
    section = text.split(NOT_EVERYTHING, 1)[1]
    return [line[2:] for line in section.splitlines() if line.startswith("• ")]


async def _edit_round(league: Any) -> None:
    await league.write("UPDATE rounds SET track_name = 'Monza' WHERE id = ?", round_id(PRO, 2))


async def _leave_placements(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'ACTIVE' WHERE id = ?", SEASON_ID)


async def _pass_a_date(league: Any) -> None:
    league.clock.advance(days=31)


async def _delete_lineup_channel(league: Any) -> None:
    league.remove_channel(PRO_CH.lineup)


async def _lose_rasteriser(league: Any) -> None:
    league.rasteriser_on = False


#: Each way a season can change while its approval waits: what changes it, the league it needs,
#: and the reply's words for it.
_REFUSALS = {
    "the stage written to ongoing": (
        _leave_placements, {},
        "⛔ The season is no longer in placements. **Nothing has been approved.**",
    ),
    "a round edited": (
        _edit_round, {},
        "⛔ Your season has changed since this review, so the report above no longer describes it:",
    ),
    "a date passed": (
        _pass_a_date, {},
        "❌ Season cannot be approved — its calendar holds dates that have already gone by:",
    ),
    "a lineup channel deleted": (
        _delete_lineup_channel, {},
        "**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`.",
    ),
    "the rasteriser gone": (
        _lose_rasteriser, {"images": True},
        "• Inkscape is not installed on this host.",
    ),
}


async def _league_for(tmp_path: Any, monkeypatch: Any, **kwargs: Any) -> Any:
    return await season_league(tmp_path, monkeypatch=monkeypatch, **kwargs)


# ── The defects ─────────────────────────────────────────────────────────────────────


async def test_a_stop_after_the_season_is_saved_finishes_its_roles_lineups_calendars_and_sheets_on_restart(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, attendance=True)
    await _pressed(league)
    await run_queue(league.bot, steps=1)
    assert [job["name"] for job in await _jobs(league) if job["done_at"]] == ["apply"]

    await league.restart()
    await run_queue(league.bot)

    assert sorted(league.granted[LEWIS]) == sorted([PRO_ROLE, FERRARI_ROLE])
    assert sorted(league.granted[MAX]) == sorted([PRO_ROLE, FERRARI_ROLE])
    assert sorted(league.granted[CHARLES]) == sorted([AM_ROLE, MCLAREN_ROLE])
    for chans in (PRO_CH, AM_CH):
        assert len(league.texts(chans.lineup)) == 1
        assert len(league.texts(chans.calendar)) == 1
        assert len(league.texts(chans.attendance)) == 1
    assert _armed(league) == {"results": sorted(_all_rounds()),
                              "attendance": sorted(_all_rounds())}
    assert len(_confirmed_lines(league)) == 1


async def test_a_restart_while_the_approval_is_in_hand_does_not_say_nothing_was_confirmed(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    assert (await _approval(league))["state"] == "QUEUED"

    await league.restart()
    await run_queue(league.bot)

    assert not any("expired while the bot was restarting" in text
                   for text in league.texts(REVIEW_CHANNEL))
    assert "nothing has been confirmed" not in league.log().lower()
    assert (await league.season())["stage"] == "ONGOING"


async def test_a_stop_before_the_save_leaves_no_timed_work_armed_for_a_season_in_placements(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, weather=True, attendance=True)
    await _pressed(league)

    with _failing_save():
        await run_queue(league.bot)

    assert await _stopped_at(league) == "apply"
    assert league.armed == []
    assert (await league.season())["stage"] == "PLACEMENTS"


@pytest.mark.parametrize("first", ["waiting", "stopped at apply", "stopped at a post"])
async def test_a_second_approval_from_another_review_is_refused_at_once_without_a_job_number(
    tmp_path, monkeypatch, first,
):
    """Both reviews stand before either is pressed; the second is pressed while the first's
    approval waits on the queue, is stopped at its save, or is stopped at a post after it."""
    league = await _league_for(tmp_path, monkeypatch, test_mode=True)
    review, other = await post_review(league), await post_review(league)
    await _pressed(league, review=review, backup="skip")
    if first == "stopped at apply":
        with _failing_save():
            await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    elif first == "stopped at a post":
        _refused(league, PRO_CH.lineup)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "refresh_lineup"
    refusals_before = sum(line.startswith("⛔") for line in _log_lines(league))

    second = await _pressed(league, review=other, backup="save")

    assert ALREADY in reply(second)
    assert "job #" not in reply(second)
    assert sum(line.startswith("⛔") for line in _log_lines(league)) == refusals_before + 1
    assert len(await approval_changes(league)) == 1
    assert [answer for answer, _asked in league.backup_answers] == ["skip"]

    league.channel(PRO_CH.lineup).send_fails = None
    if first == "waiting":
        await run_queue(league.bot)
    else:
        await retry_job(league.bot)
    assert (await league.season())["stage"] == "ONGOING"
    assert len(_confirmed_lines(league)) == 1


async def test_the_opening_standings_are_posted_in_each_division_s_standings_channel(
    tmp_path, monkeypatch,
):
    """Results on, Pro's standings channel 602 and Am's 702 set in `division_results_config`:
    each is sent the opening classification, the drivers' and the teams' championships."""
    league = await _league_for(tmp_path, monkeypatch, results=True)
    await _pressed(league)

    await run_queue(league.bot)

    assert league.texts(PRO_CH.standings)
    assert league.texts(AM_CH.standings)
    assert await _stopped_at(league) is None


async def test_a_role_discord_refuses_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    league.grant_fails[LEWIS] = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    press = await _pressed(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "grant_roles"
    assert f"granting `<@{LEWIS}>` the roles of **Pro** and Ferrari" in league.log()
    await discard_job(league.bot)

    line = f"<@{LEWIS}> — their roles could not be granted. Give them their division's and team's roles by hand."
    assert line in _not_done(reply(press))
    logged = line.replace(f"<@{LEWIS}>", f"`<@{LEWIS}>`")
    assert f"not done: {logged}" in league.log()
    assert MAX in league.granted


async def test_a_lineup_discord_refuses_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    _refused(league, PRO_CH.lineup)
    press = await _pressed(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "refresh_lineup"
    await discard_job(league.bot)

    line = ("**Pro** — its lineup could not be posted. No command posts it again; it is posted "
            "with the next change to its drivers.")
    assert line in _not_done(reply(press))
    assert f"not done: {line}" in league.log()


# ── The checks ──────────────────────────────────────────────────────────────────────


async def test_the_press_runs_every_gate_before_asking_and_asks_nothing_when_one_refuses(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    league.cog._team_name_problems = AsyncMock(return_value=["**Pro** — Ferrari has no driver"])

    refused = await _pressed(league)

    assert "Ferrari has no driver" in reply(refused)
    assert await change_rows(league.db_path) == []

    league.cog._team_name_problems = AsyncMock(return_value=[])
    await _pressed(league)

    assert (await _approval(league))["state"] == "QUEUED"
    assert (await league.season())["stage"] == "PLACEMENTS"


@pytest.mark.parametrize("change", sorted(_REFUSALS))
async def test_a_refusal_found_when_the_approval_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, monkeypatch, change,
):
    alter, kwargs, words = _REFUSALS[change]
    league = await _league_for(tmp_path, monkeypatch, attendance=True, **kwargs)
    press = await _pressed(league)
    await alter(league)

    await run_queue(league.bot)

    refusals = [line for line in _log_lines(league) if "⛔" in line and "Approve" in line]
    if change == "a date passed":
        # The clock has moved 31 days, far past the 14 minutes the reply can be updated in, so
        # the refusal is read from its line in the log channel, which gives it without its mark.
        assert words.removeprefix("❌ ") in refusals[0]
    else:
        assert words in reply(press)
    assert (await _approval(league))["state"] == "REFUSED"
    assert len(refusals) == 1
    assert await _sessions(league) == {}
    assert await _committed(league) == 0
    assert league.armed == []
    assert await _stopped_at(league) is None
    from leaguebot.core.services.season_approval_change import TELL_KIND

    # Past the 14 minutes, the approval asks the change that tells the review's channel (2.1b).
    told = [TELL_KIND] if change == "a date passed" else []
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        (await _approval(league))["kind"], *told
    ]


async def test_the_save_refuses_writing_nothing_where_the_season_left_placements_after_the_check(
    tmp_path, monkeypatch,
):
    """The season leaves Placements between the check and the save: the last thing the check
    reads, the channels, moves it as it is read."""
    from leaguebot.core.services import approval_checks, season_approval_change

    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    real = approval_checks.division_channel_faults

    async def _and_leave(*args: Any, **kwargs: Any) -> Any:
        found = await real(*args, **kwargs)
        await _leave_placements(league)
        return found

    for module in (approval_checks, season_approval_change):
        if hasattr(module, "division_channel_faults"):
            monkeypatch.setattr(module, "division_channel_faults", _and_leave)

    await run_queue(league.bot)

    assert "⛔ The season is no longer in placements. **Nothing has been approved.**" in reply(press)
    assert await _sessions(league) == {}
    assert await _committed(league) == 0
    assert league.armed == []
    assert _confirmed_lines(league) == []


async def test_the_backup_question_is_put_after_every_gate_and_before_the_approval_is_asked(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, test_mode=True)
    league.cog._lineup_problems = AsyncMock(return_value=["**Pro** — Ferrari is short"])
    await _pressed(league, backup="save")
    assert league.backup_answers == []

    league.cog._lineup_problems = AsyncMock(return_value=[])
    await _pressed(league, backup="save")

    assert league.backup_answers == [("save", 0)]
    assert (await _approval(league))["state"] == "QUEUED"


async def test_cancelling_at_the_backup_question_asks_nothing(tmp_path, monkeypatch):
    league = await _league_for(tmp_path, monkeypatch, test_mode=True)

    press = await _pressed(league, backup="cancel")

    assert "Nothing has been approved, and nothing has been saved." in reply(press)
    assert await change_rows(league.db_path) == []
    assert (await league.season())["stage"] == "PLACEMENTS"


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_manager_is_told_at_once_and_the_reply_is_updated_when_the_season_is_approved(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    first_job = (await _jobs(league))[0]["id"]

    assert reply(press) == (
        "⏳ Approving season 3. This message will be updated when it is done; if it takes "
        f"longer, the log channel will say so. It begins with job #{first_job}."
    )

    await run_queue(league.bot)

    assert APPROVED in reply(press)
    assert NOT_EVERYTHING not in reply(press)


async def test_the_sessions_placements_points_and_season_are_saved_in_one_save(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, results=True)
    await _pressed(league)

    with _failing_save():
        await run_queue(league.bot)

    assert await _stopped_at(league) == "apply"
    assert await _sessions(league) == {}
    assert await league.rows("SELECT * FROM season_points_entries WHERE season_id = ?",
                             SEASON_ID) == []
    assert await _committed(league) == 0
    season = await league.season()
    assert (season["status"], season["stage"]) == ("SETUP", "PLACEMENTS")


async def test_the_save_reads_nothing_but_its_own_connection(tmp_path, monkeypatch):
    """From its first write on, every module flag and every season reader that opens a
    connection of its own raises: the save goes through all the same."""
    league = await _league_for(tmp_path, monkeypatch, results=True, attendance=True)
    await _pressed(league)
    reading = {"forbidden": False}

    def _guarded(real: Any) -> Any:
        async def _read(*args: Any, **kwargs: Any) -> Any:
            if reading["forbidden"]:
                raise AssertionError(f"the save read {real.__name__} outside its connection")
            return await real(*args, **kwargs)
        return _read

    for cls, names in (
        (ModuleService, ("is_weather_enabled", "is_results_enabled", "is_attendance_enabled",
                         "is_images_enabled", "is_signup_enabled")),
        (SeasonService, ("get_divisions", "get_division_rounds", "get_stage",
                         "get_divisions_with_results_config")),
    ):
        for name in names:
            monkeypatch.setattr(cls, name, _guarded(getattr(cls, name)))
    from leaguebot.core.services import season_service

    real_sessions = season_service.create_sessions_for_round_on

    async def _first_write(*args: Any, **kwargs: Any) -> Any:
        reading["forbidden"] = True
        return await real_sessions(*args, **kwargs)

    with _everywhere("create_sessions_for_round_on", _first_write):
        await run_queue(league.bot, steps=1)
    reading["forbidden"] = False

    apply = (await _jobs(league))[0]
    assert apply["name"] == "apply" and apply["done_at"] is not None
    assert (await league.season())["stage"] == "ONGOING"


async def test_each_round_gets_its_sessions_once_however_often_the_save_is_tried(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    from leaguebot.core.services import season_service

    real = season_service.commit_placements_on
    tries = {"n": 0}

    async def _fails_twice(*args: Any, **kwargs: Any) -> Any:
        tries["n"] += 1
        if tries["n"] <= 2:
            raise RuntimeError("disk I/O error")
        return await real(*args, **kwargs)

    with _everywhere("commit_placements_on", _fails_twice):
        await run_queue(league.bot)
        await retry_job(league.bot)
        await retry_job(league.bot)

    assert tries["n"] == 3
    assert await _sessions(league) == {rid: 2 for rid in _all_rounds()}


@pytest.mark.parametrize("results", [True, False])
async def test_the_points_configurations_are_copied_onto_the_season_only_with_results_on(
    tmp_path, monkeypatch, results,
):
    league = await _league_for(tmp_path, monkeypatch, results=results)
    await _pressed(league)

    await run_queue(league.bot)

    copied = await league.rows(
        "SELECT config_name, position, points FROM season_points_entries WHERE season_id = ? "
        "ORDER BY position", SEASON_ID,
    )
    expected = [{"config_name": "Standard", "position": 1, "points": 25},
                {"config_name": "Standard", "position": 2, "points": 18}]
    assert copied == (expected if results else [])
    assert (await _approval(league))["state"] == "DONE"


async def test_the_approval_is_audited_with_the_season_and_the_placements_committed(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)

    await run_queue(league.bot)

    rows = await league.rows("SELECT * FROM audit_entries WHERE change_type = 'SEASON_APPROVED'")
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_ID
    assert json.loads(rows[0]["old_value"]) == {"season_id": SEASON_ID, "stage": "PLACEMENTS"}
    assert json.loads(rows[0]["new_value"]) == {
        "season_id": SEASON_ID, "season_number": 3, "stage": "ONGOING",
        "placements_committed": 3,
    }


async def test_each_driver_s_roles_division_s_lineup_calendar_and_opening_posts_are_jobs_of_their_own(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, results=True, attendance=True)
    await _pressed(league)

    await run_queue(league.bot)

    names = [job["name"] for job in await _jobs(league)]
    assert names[:3] == ["apply", "forget_setup", "arm"]
    assert names[-2:] == ["tell_review_channel", "close"]
    assert sorted(names[3:-2]) == sorted(
        ["grant_roles"] * 3 + ["post_batch_notice", "delete_batch_notice"]
        + ["refresh_lineup", "post_calendar", "opening_standings", "opening_sheet"] * 2
    )
    assert names.index("post_batch_notice") > max(
        i for i, name in enumerate(names) if name == "grant_roles"
    )
    assert names.index("delete_batch_notice") == len(names) - 3
    assert list(league.granted) == [LEWIS, MAX, CHARLES]
    sends = [cid for kind, cid, _mid in league.events if kind == "send"]
    for pro, am in ((PRO_CH.lineup, AM_CH.lineup), (PRO_CH.calendar, AM_CH.calendar),
                    (PRO_CH.standings, AM_CH.standings),
                    (PRO_CH.attendance, AM_CH.attendance)):
        assert sends.index(pro) < sends.index(am)


async def test_a_driver_discord_reports_absent_is_passed_over_and_the_queue_runs_on(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    league.absent.add(LEWIS)
    await _pressed(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert LEWIS not in league.granted
    assert sorted(league.granted) == [MAX, CHARLES]
    assert (await _approval(league))["state"] == "DONE"


async def test_a_driver_discord_cannot_fetch_for_another_reason_stops_the_queue(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    league.fetch_fails[LEWIS] = http_error(status=503, text="Service Unavailable")
    await _pressed(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "grant_roles"
    assert LEWIS not in league.granted


async def test_a_test_driver_is_granted_nothing(tmp_path, monkeypatch):
    """A test driver (104) placed at McLaren in Am beside Charles."""
    league = await _league_for(tmp_path, monkeypatch, extra_drivers=True)
    await _pressed(league)

    await run_queue(league.bot)

    assert CHARLES in league.granted
    assert TEST_DRIVER not in league.granted
    assert [job["name"] for job in await _jobs(league)].count("grant_roles") == 3


@pytest.mark.parametrize(
    ("modules", "armed"),
    [
        pytest.param({"weather": True}, ["weather"], id="weather on"),
        pytest.param({"weather": False, "results": True}, ["results"],
                     id="weather off and results on"),
        pytest.param({"weather": False, "results": True, "test_mode": True}, [],
                     id="weather off and results on under test mode"),
        pytest.param({"weather": False, "results": True, "attendance": True},
                     ["results", "attendance"], id="weather off, results and attendance on"),
        pytest.param({"weather": False, "results": False}, ["results"],
                     id="weather and results off"),
        pytest.param({"weather": False, "results": False, "test_mode": True}, ["results"],
                     id="weather and results off under test mode"),
    ],
)
async def test_the_timed_work_is_armed_by_the_module_rules(tmp_path, monkeypatch, modules, armed):
    """Each round's result submission is armed whatever the modules, save under test mode with
    results on, where `/test-mode advance` opens it by hand (owner, "Fold them in")."""
    league = await _league_for(tmp_path, monkeypatch, **modules)
    await _pressed(league)

    await run_queue(league.bot)

    assert _armed(league) == {which: sorted(_all_rounds()) for which in armed}
    assert (await _approval(league))["state"] == "DONE"


@pytest.mark.parametrize("test_mode", [False, True], ids=["test mode off", "test mode on"])
async def test_a_season_with_weather_and_results_off_arms_a_result_submission_for_each_round(
    tmp_path, monkeypatch, test_mode,
):
    league = await _league_for(tmp_path, monkeypatch, weather=False, results=False,
                               test_mode=test_mode)
    await _pressed(league)

    await run_queue(league.bot)

    arm = league.bot.scheduler_service.schedule_result_submission_jobs
    assert arm.call_count == 1, league.armed
    assert league.armed == [("results", _all_rounds())]
    assert arm.call_args.kwargs["division_meta"] == {
        division: (SEASON_NUMBER, DIVISIONS[division][1]) for division in (PRO, AM)
    }


async def test_the_armed_submission_with_results_off_moves_each_round_on_so_the_division_finishes(
    tmp_path, monkeypatch,
):
    """Each armed job fired as the scheduler would fire it at its round's moment."""
    from leaguebot.results.services import result_submission_service

    league = await _league_for(tmp_path, monkeypatch, weather=False, results=False)
    await _pressed(league)
    await run_queue(league.bot)
    fired = _armed(league).get("results", [])
    assert fired == sorted(_all_rounds())
    before = {cid: league.texts(cid) for cid in league.channels}

    with patch.object(result_submission_service, "create_submission_channel",
                      AsyncMock()) as create:
        for each in fired:
            await result_submission_service.run_result_submission_job(each, league.bot)

    rounds = await league.rows("SELECT status FROM rounds")
    assert {row["status"] for row in rounds} == {"FINAL"}
    divisions = await league.rows("SELECT status FROM divisions")
    assert {row["status"] for row in divisions} == {"FINISHED"}
    create.assert_not_awaited()
    assert {cid: league.texts(cid) for cid in league.channels} == before


async def test_a_stuck_arming_leaves_the_season_ongoing_and_arms_once_retried(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, results=True)
    league.arming_fails = RuntimeError("the job store is locked")
    await _pressed(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "arm"
    assert "arming the timed work of season 3" in league.log()
    assert (await league.season())["stage"] == "ONGOING"
    assert league.armed == []

    league.arming_fails = None
    await retry_job(league.bot)

    assert league.armed == [("results", _all_rounds())]


async def test_the_setup_held_in_memory_is_let_go_of_once_the_season_is_saved(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    assert league.cog._get_pending() is not None

    await _run_through(league, "apply")
    assert league.cog._get_pending() is not None
    await _run_through(league, "forget_setup")

    assert league.cog._get_pending() is None


async def test_a_round_amended_while_the_posts_wait_is_amended_on_the_ongoing_season(
    tmp_path, monkeypatch,
):
    """Stopped at Pro's lineup, `/round amend` moving Pro's round 1 a week later is offered as
    an amendment of the running season (decided 2026-09-22)."""
    from leaguebot.core.cogs.season_cog import SeasonCog, _ConfirmView

    league = await _league_for(tmp_path, monkeypatch)
    _refused(league, PRO_CH.lineup)
    await _pressed(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "refresh_lineup"

    amend = member_interaction(league.bot, user=tier_member("admin", member_id=ADMIN_ID))
    amend.guild = league.guild
    later = league.clock.now + timedelta(days=37)
    await undecorate(SeasonCog.round_amend)(
        league.cog, amend, division_name="Pro", round_number=1,
        scheduled_at=later.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    assert any(
        "**Amend Round 1** in division **Pro**" in str(call.args[0])
        and isinstance(call.kwargs.get("view"), _ConfirmView)
        for call in amend.followup.send.await_args_list if call.args
    )


async def test_the_calendar_is_posted_with_the_season_number_and_its_rounds_as_they_stand_when_it_runs(
    tmp_path, monkeypatch,
):
    """Images on with the calendar aspect: the drawing is handed season 3, and the rounds read
    when the calendar job runs, Pro's round 2 moved to Monza after the save."""
    from leaguebot.core.services import calendar_post_service

    league = await _league_for(tmp_path, monkeypatch, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    drawn: list[dict[str, Any]] = []

    async def _draw(_bot: Any, division: Any, rounds: Any, _tracks: Any, **kwargs: Any) -> Any:
        drawn.append({"division": division.name, "tracks": [r.track_name for r in rounds],
                      **kwargs})
        return SimpleNamespace(problem=None, notices=[], png_paths=[])

    monkeypatch.setattr(calendar_post_service, "render_calendar_image", _draw)
    await _pressed(league)
    await _run_through(league, "apply")
    await _edit_round(league)

    await run_queue(league.bot)

    pro = [each for each in drawn if each["division"] == "Pro"]
    assert pro and pro[-1]["season_number"] == 3
    assert pro[-1]["tracks"][1] == "Monza"
    assert "Monza" in league.texts(PRO_CH.calendar)[-1]


async def test_a_season_numbered_zero_is_drawn_as_zero_and_logged(tmp_path, monkeypatch, caplog):
    """A season number of 0 is a malformed row, `seasons.season_number` counting from one. The
    calendar is drawn with 0, never hidden, and the bot logs that it will draw "SEASON 0" (#213).
    The number is 0 both in the setup the press reads and in the database, so the test holds
    wherever the approval takes it from; it passes today and guards the rule through the build.
    It replaces the deleted
    `test_do_approve_posting.py::test_a_season_with_no_number_still_draws_and_is_logged`."""
    from leaguebot.core.services import calendar_post_service

    league = await _league_for(tmp_path, monkeypatch, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    drawn: list[dict[str, Any]] = []

    async def _draw(_bot: Any, division: Any, rounds: Any, _tracks: Any, **kwargs: Any) -> Any:
        drawn.append({"division": division.name, **kwargs})
        return SimpleNamespace(problem=None, notices=[], png_paths=[])

    monkeypatch.setattr(calendar_post_service, "render_calendar_image", _draw)
    await league.write("UPDATE seasons SET season_number = 0 WHERE id = ?", SEASON_ID)
    league.cog._pending[ADMIN_ID].season_number = 0

    with caplog.at_level(logging.WARNING):
        await _pressed(league)
        await run_queue(league.bot)

    pro = [each for each in drawn if each["division"] == "Pro"]
    assert pro and pro[-1]["season_number"] is not None and pro[-1]["season_number"] == 0
    assert "SEASON 0" in caplog.text


async def test_a_calendar_that_falls_back_to_text_is_reported_in_the_reply_and_the_log(
    tmp_path, monkeypatch,
):
    from leaguebot.core.services import calendar_post_service

    league = await _league_for(tmp_path, monkeypatch, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    monkeypatch.setattr(
        calendar_post_service, "render_calendar_image",
        AsyncMock(side_effect=RuntimeError("the template has no rows")),
    )
    press = await _pressed(league)

    await run_queue(league.bot)

    assert "⚠️ **Calendar images**" in reply(press)
    assert "the template has no rows" in reply(press)
    calendar_lines = [line for line in _log_lines(league)
                      if "| /season placements-review | Calendar image generation" in line]
    assert len(calendar_lines) == 1
    assert "Fell back to the textual calendar" in calendar_lines[0]
    assert "Pro: the template has no rows" in calendar_lines[0]


#: Each post retried: its job, its channel, the modules it needs, and where it reaches for its
#: picture, stubbed to stand aside so that the text follows on every host, whether or not it
#: carries the rasteriser: the module, the function, and what the stub returns.
_RETRIED_POSTS = {
    "the lineup": (
        "refresh_lineup", lambda chans: chans.lineup, {},
        ("leaguebot.image.services.image_lineup_post", "try_post",
         MagicMock(applicable=False)),
    ),
    "the calendar": (
        "post_calendar", lambda chans: chans.calendar, {},
        ("leaguebot.core.services.calendar_post_service", "render_calendar_image",
         SimpleNamespace(problem=None, notices=[], png_paths=[])),
    ),
    "the opening standings": (
        "opening_standings", lambda chans: chans.standings, {"results": True},
        ("leaguebot.image.services.image_standings_post", "try_post",
         MagicMock(rejects=False, applicable=False)),
    ),
    "the opening sheet": (
        "opening_sheet", lambda chans: chans.attendance, {"attendance": True},
        ("leaguebot.image.services.image_attendance_post", "attendance_enabled", False),
    ),
}


@pytest.mark.parametrize("post", sorted(_RETRIED_POSTS))
async def test_a_retried_post_is_posted_as_text(tmp_path, monkeypatch, post):
    """Images on, every aspect on: Pro's channel refuses the first send. The first try reaches
    for the picture, Pro's retry does not and posts the text, and Am's post, a first try of its
    own, reaches for it again."""
    import importlib

    job, which, modules, (module, name, returned) = _RETRIED_POSTS[post]
    league = await _league_for(tmp_path, monkeypatch, images=True, **modules)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=True)
    cid = which(PRO_CH)
    league.channel(cid).send_fails = http_error(status=503, text="Service Unavailable")
    await _pressed(league)
    draw = AsyncMock(return_value=returned)
    monkeypatch.setattr(importlib.import_module(module), name, draw)

    await run_queue(league.bot)
    assert await _stopped_at(league) == job
    assert draw.await_count == 1, "the first try did not reach for the picture"

    league.channel(cid).send_fails = None
    await retry_job(league.bot)

    assert league.texts(cid), "nothing was posted on the retry"
    assert league.channel(cid).files[-1] is None
    assert league.texts(which(AM_CH)), "Am's post did not follow the retry"
    assert draw.await_count == 2, "the retry reached for the picture"


async def test_a_division_with_no_standings_channel_set_posts_no_opening_standings(
    tmp_path, monkeypatch,
):
    """Am's standings channel never set: its opening standings job posts nothing and raises
    nothing, while Pro's posts."""
    from leaguebot.core.services import season_classification_service

    league = await _league_for(tmp_path, monkeypatch, results=True)
    await league.write(
        "UPDATE division_results_config SET standings_channel_id = NULL WHERE division_id = ?", AM,
    )

    for division in (PRO, AM):
        await season_classification_service.post_opening_standings(
            league.bot, league.guild, league.db_path, division, as_text=False,
        )

    assert league.texts(PRO_CH.standings)
    assert league.texts(AM_CH.standings) == []


async def test_the_batch_notice_brackets_the_posts_in_the_review_s_channel(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, results=True, attendance=True)
    await _pressed(league)

    await run_queue(league.bot)

    review_sends = [mid for kind, cid, mid in league.events
                    if kind == "send" and cid == REVIEW_CHANNEL]
    texts = league.texts(REVIEW_CHANNEL)
    notice = review_sends[texts.index(
        "🎨 Posting lineups, calendars and opening classifications — one moment."
    )]
    division_channels = set(vars(PRO_CH).values()) | set(vars(AM_CH).values())
    posts = [i for i, (kind, cid, _mid) in enumerate(league.events)
             if kind == "send" and cid in division_channels]
    posted = league.events.index(("send", REVIEW_CHANNEL, notice))
    deleted = league.events.index(("delete", REVIEW_CHANNEL, notice))
    assert posts and posted < min(posts) and deleted > max(posts)


async def _fail_the_notice_post(league: Any) -> None:
    _refuse_starting(league, REVIEW_CHANNEL, "🎨")


async def _fail_the_notice_delete(league: Any) -> None:
    await _run_through(league, "post_batch_notice")
    for message in league.review_channel.messages.values():
        if str(message.content).startswith("🎨"):
            message.delete = AsyncMock(
                side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
            )


@pytest.mark.parametrize(
    ("half", "job", "line"),
    [
        ("post", "post_batch_notice",
         "The notice that the season's posts were being made could not be posted."),
        ("delete", "delete_batch_notice",
         "The notice that the season's posts were being made could not be deleted"),
    ],
)
async def test_a_batch_notice_discord_refuses_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch, half, job, line,
):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    await (_fail_the_notice_post if half == "post" else _fail_the_notice_delete)(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == job
    await discard_job(league.bot)

    assert any(bullet.startswith(line) for bullet in _not_done(reply(press)))
    assert f"not done: {line}" in league.log()
    assert (await _approval(league))["state"] == "DONE"


async def test_a_discarded_save_approves_nothing_and_says_to_review_again(tmp_path, monkeypatch):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)

    with _failing_save():
        await run_queue(league.bot)
        await discard_job(league.bot)

    assert NOT_SAVED in reply(press)
    assert APPROVED not in reply(press)
    assert (await league.season())["stage"] == "PLACEMENTS"
    assert _confirmed_lines(league) == []
    assert league.armed == [] and league.granted == {}


async def _fail_forget(league: Any) -> None:
    league.cog.clear_pending = MagicMock(side_effect=RuntimeError("the cog is reloading"))


async def _fail_arm(league: Any) -> None:
    league.arming_fails = RuntimeError("the job store is locked")


async def _fail_grant(league: Any) -> None:
    league.grant_fails[LEWIS] = http_error(discord.Forbidden, status=403,
                                           text="Missing Permissions")


async def _fail_lineup(league: Any) -> None:
    _refused(league, PRO_CH.lineup)


async def _fail_calendar(league: Any) -> None:
    _refused(league, PRO_CH.calendar)


async def _fail_standings(league: Any) -> None:
    _refused(league, PRO_CH.standings)


async def _fail_sheet(league: Any) -> None:
    _refused(league, PRO_CH.attendance)


#: Each job after the save, made to fail; the line naming it once discarded.
_DISCARDED = {
    "forget_setup": (_fail_forget, "The setup the bot held in memory could not be let go of: "
                     "`/round amend` may refuse this season until the bot restarts."),
    "arm": (_fail_arm, "⛔ The season's timed work was not armed: no round will open its "
            "results submission, and no forecast or check-in call will be posted. No command "
            "arms it again."),
    "grant_roles": (_fail_grant, f"<@{LEWIS}> — their roles could not be granted. Give them "
                    "their division's and team's roles by hand."),
    "post_batch_notice": (_fail_the_notice_post, "The notice that the season's posts were being "
                          "made could not be posted."),
    "refresh_lineup": (_fail_lineup, "**Pro** — its lineup could not be posted. No command posts "
                       "it again; it is posted with the next change to its drivers."),
    "post_calendar": (_fail_calendar, "**Pro** — its calendar could not be posted. Run "
                      "`/division calendar-sync`."),
    "opening_standings": (_fail_standings, "**Pro** — its opening standings could not be "
                          "posted. No command posts them again; the first round's results post "
                          "the standings as usual."),
    "opening_sheet": (_fail_sheet, "**Pro** — its opening attendance sheet could not be posted. "
                      "No command posts it again; the first round's attendance posts the sheet "
                      "as usual."),
    "delete_batch_notice": (_fail_the_notice_delete, "The notice that the season's posts were "
                            "being made could not be deleted"),
}


@pytest.mark.parametrize("job", list(_DISCARDED))
async def test_each_discarded_post_is_named_under_not_everything_could_be_done(
    tmp_path, monkeypatch, job,
):
    fail, line = _DISCARDED[job]
    league = await _league_for(tmp_path, monkeypatch, results=True, attendance=True)
    press = await _pressed(league)
    await fail(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == job
    await discard_job(league.bot)

    assert APPROVED in reply(press)
    assert any(bullet.startswith(line) for bullet in _not_done(reply(press)))
    confirmed = _confirmed_lines(league)
    logged = line.replace(f"<@{LEWIS}>", f"`<@{LEWIS}>`")  # the log's mentions are in backticks
    assert len(confirmed) == 1 and f"\n  not done: {logged}" in confirmed[0]


async def test_no_separate_opening_classification_line_is_written(tmp_path, monkeypatch):
    """Each opening post is a job of the approval, and one discarded is named on the approval's
    own line (the test above). The separate "| Opening classification" report line goes with
    `post_opening_classifications` (plan 2.3): it is not written when Pro's opening standings and
    sheet fail and are discarded. It replaces what the deleted
    `test_do_approve_posting.py::test_the_opening_classification_report_names_the_member_who_approved`
    pinned as present."""
    league = await _league_for(tmp_path, monkeypatch, results=True, attendance=True)
    await _pressed(league)
    await _fail_standings(league)
    await _fail_sheet(league)

    await run_queue(league.bot)
    discarded: list[str] = []
    for _ in range(10):
        job = await _stopped_at(league)
        if job is None:
            break
        discarded.append(job)
        await discard_job(league.bot)

    assert sorted(discarded) == ["opening_sheet", "opening_standings"]
    assert len(_confirmed_lines(league)) == 1
    assert not any("| Opening classification" in line for line in _log_lines(league))


async def test_a_discarded_arming_is_named_first_and_says_the_season_has_no_timed_work(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch, results=True)
    _refused(league, PRO_CH.lineup)
    press = await _pressed(league)
    await _fail_arm(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "arm"
    await discard_job(league.bot)
    assert await _stopped_at(league) == "refresh_lineup"
    await discard_job(league.bot)

    bullets = _not_done(reply(press))
    assert bullets[0].startswith("⛔ The season's timed work was not armed:")
    assert "No command arms it again." in bullets[0]
    assert any(bullet.startswith("**Pro** — its lineup could not be posted.") for bullet in bullets)


async def test_one_line_records_the_approval_after_the_last_job(tmp_path, monkeypatch):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)

    await _run_through(league, "tell_review_channel")
    assert _confirmed_lines(league) == []
    await run_queue(league.bot)

    # As the log channel carries every line: its mentions in backticks, the separator beneath.
    assert _confirmed_lines(league) == [
        f"Admin (`<@{ADMIN_ID}>`) | /season placements-review | Placements confirmed\n"
        "  season: 3\n  season_id: 7\n" + "―" * 36
    ]


async def test_the_review_is_cleared_and_its_prompt_forgotten_once_the_approval_is_asked(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    review = await post_review(league)

    await _pressed(league, review=review)

    assert (await _approval(league))["state"] == "QUEUED"
    assert (await league.season())["stage"] == "PLACEMENTS"
    assert await league.rows("SELECT * FROM season_review_prompts") == []
    for message in [*review.report, review.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) in league.events


async def test_a_stop_before_the_save_approves_once_on_restart(tmp_path, monkeypatch):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    with _failing_save():
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"

    await league.restart()
    await retry_job(league.bot)

    assert (await league.season())["stage"] == "ONGOING"
    assert len(_confirmed_lines(league)) == 1
    assert sorted(league.granted[LEWIS]) == sorted([PRO_ROLE, FERRARI_ROLE])


# ── A save stopped and tried again is judged again ──────────────────────────────────
#
# The queue judges a change only before it starts, and the save reads nothing but its own
# connection, so a save stopped and retried would otherwise commit the season as it stands at
# the retry. Core specification: what can change while the confirmation waits is judged again
# when it is carried out (#439, code-1-2).


def _save_fails_once() -> ExitStack:
    """The save's commitment of the placements raises the first time only, as a passing fault
    of the database would; every later save goes through."""
    from leaguebot.core.services import season_service

    real = season_service.commit_placements_on
    tries = {"n": 0}

    async def _once(*args: Any, **kwargs: Any) -> Any:
        tries["n"] += 1
        if tries["n"] == 1:
            raise RuntimeError("disk I/O error")
        return await real(*args, **kwargs)

    return _everywhere("commit_placements_on", _once)


async def _window_opening_soon(league: Any) -> None:
    """Pro's round 1 is moved so that its check-in notice (five days before it, attendance on)
    opens five minutes after "now": the press and the first try find nothing gone by."""
    soon = league.clock.now + timedelta(days=5, minutes=5)
    await league.write(
        "UPDATE rounds SET scheduled_at = ? WHERE id = ?", soon.isoformat(), round_id(PRO, 1)
    )


async def _pass_the_window(league: Any) -> None:
    """Ten minutes on: the check-in notice has opened, and the reply can still be updated."""
    league.clock.advance(minutes=10)


#: What changes while the save is stopped: what sets the league up before the press, what
#: changes it, and the reply's words for the refusal.
_WHILE_STOPPED = {
    "a round edited": (None, _edit_round, _REFUSALS["a round edited"][2]),
    "a window passed": (_window_opening_soon, _pass_the_window, _REFUSALS["a date passed"][2]),
}


async def _points_copied(league: Any) -> list[dict[str, Any]]:
    return await league.rows("SELECT * FROM season_points_entries WHERE season_id = ?", SEASON_ID)


@pytest.mark.parametrize("change", sorted(_WHILE_STOPPED))
async def test_a_retried_save_judges_the_approval_again_and_refuses_what_changed_while_it_was_stopped(
    tmp_path, monkeypatch, change,
):
    """The save fails once and the queue stops at it; while it is stopped the season changes;
    then Retry. The retry judges the approval again before it saves, and refuses it with the
    check's own words, writing nothing."""
    before, alter, words = _WHILE_STOPPED[change]
    league = await _league_for(tmp_path, monkeypatch, attendance=True)
    if before is not None:
        await before(league)
    press = await _pressed(league)
    with _save_fails_once():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await alter(league)

        await retry_job(league.bot)

    assert words in reply(press)
    assert APPROVED not in reply(press)
    assert "judge" in [job["name"] for job in await _jobs(league)]
    assert len([line for line in _log_lines(league) if "⛔" in line and "Approve" in line]) == 1
    assert _confirmed_lines(league) == []
    assert (await league.season())["stage"] == "PLACEMENTS"
    assert await _sessions(league) == {}
    assert await _committed(league) == 0
    assert await _points_copied(league) == []
    assert league.armed == [] and league.granted == {}
    assert await _stopped_at(league) is None
    assert (await _approval(league))["state"] not in ("QUEUED", "RUNNING")


async def test_a_retried_save_with_nothing_changed_while_it_was_stopped_approves(
    tmp_path, monkeypatch,
):
    """The control: the save fails once and is retried with nothing changed; judged again, the
    approval still stands, and the season is approved once."""
    league = await _league_for(tmp_path, monkeypatch, attendance=True)
    press = await _pressed(league)
    with _save_fails_once():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"

        await retry_job(league.bot)

    assert APPROVED in reply(press)
    assert (await league.season())["stage"] == "ONGOING"
    assert len(_confirmed_lines(league)) == 1
    assert await _sessions(league) == {rid: 2 for rid in _all_rounds()}
    assert await _stopped_at(league) is None


# ── The public notice ───────────────────────────────────────────────────────────────


def _told(league: Any, text: str) -> int:
    return sum(text in sent for sent in league.texts(REVIEW_CHANNEL))


@pytest.mark.parametrize("cut_off", ["fourteen minutes passed", "a restart"])
async def test_an_approval_whose_reply_can_no_longer_be_updated_tells_the_review_s_channel(
    tmp_path, monkeypatch, cut_off,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    if cut_off == "a restart":
        await league.restart()
        await run_queue(league.bot)
    else:
        _refused(league, PRO_CH.lineup)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "refresh_lineup"
        league.clock.advance(minutes=15)
        league.channel(PRO_CH.lineup).send_fails = None
        await retry_job(league.bot)

    assert _told(league, CHANNEL_APPROVED) == 1
    assert not any("expired while the bot was restarting" in text
                   for text in league.texts(REVIEW_CHANNEL))


async def test_an_approval_whose_reply_is_updated_tells_the_channel_nothing(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)

    await run_queue(league.bot)

    assert APPROVED in reply(press)
    assert _told(league, f"<@{ADMIN_ID}> — Season #3") == 0
    assert "tell_review_channel" in [job["name"] for job in await _jobs(league)]


async def test_a_not_approved_outcome_that_cannot_reach_the_member_is_told_in_the_channel(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    with _failing_save():
        await run_queue(league.bot)
        league.clock.advance(minutes=15)
        await discard_job(league.bot)

    assert _told(league, CHANNEL_NOT_APPROVED) == 1
    assert NOT_SAVED not in reply(press)


async def test_a_channel_notice_discord_refuses_stops_the_queue_and_once_discarded_is_named_in_the_line(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    await league.restart()
    _refuse_starting(league, REVIEW_CHANNEL, f"✅ <@{ADMIN_ID}> — Season #3")

    await run_queue(league.bot)
    assert await _stopped_at(league) == "tell_review_channel"
    assert f"telling <#{REVIEW_CHANNEL}> how the approval of season 3 ended" in league.log()
    await discard_job(league.bot)

    assert _told(league, CHANNEL_APPROVED) == 0
    assert f"not done: <#{REVIEW_CHANNEL}> was not told how the approval ended" in league.log()


# ── A refusal at run, told in the channel ───────────────────────────────────────────


async def _tell_changes(league: Any) -> list[dict[str, Any]]:
    from leaguebot.core.services.season_approval_change import TELL_KIND

    return [row for row in await change_rows(league.db_path) if row["kind"] == TELL_KIND]


def _late_notices(league: Any) -> list[str]:
    return [text for text in league.texts(REVIEW_CHANNEL) if text.startswith(LATE_REFUSAL_HEAD)]


@pytest.mark.parametrize("change", sorted(_REFUSALS))
async def test_a_refusal_at_run_after_the_reply_expired_is_told_in_the_review_s_channel(
    tmp_path, monkeypatch, change,
):
    """The approval waits fourteen minutes and more on the queue while the season changes, then
    is refused as it comes up to run."""
    alter, kwargs, words = _REFUSALS[change]
    league = await _league_for(tmp_path, monkeypatch, attendance=True, **kwargs)
    await _pressed(league)
    league.clock.advance(minutes=15)
    await alter(league)

    await run_queue(league.bot)

    assert (await _approval(league))["state"] == "REFUSED"
    assert sum("⛔" in line and "Approve" in line for line in _log_lines(league)) == 1
    told = await _tell_changes(league)
    assert len(told) == 1
    assert (told[0]["origin"], told[0]["actor_id"]) == ("BOT", ADMIN_ID)
    notices = _late_notices(league)
    assert len(notices) == 1
    assert words in notices[0]


async def test_a_refusal_at_run_while_the_reply_can_be_updated_tells_the_channel_nothing(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    league.clock.advance(minutes=5)
    await _edit_round(league)

    await run_queue(league.bot)

    assert "⛔ Your season has changed since this review" in reply(press)
    assert await _tell_changes(league) == []
    assert _late_notices(league) == []


async def test_a_refusal_at_run_after_a_restart_is_told_in_the_channel(tmp_path, monkeypatch):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    await league.restart()
    await _edit_round(league)

    await run_queue(league.bot)

    assert (await _approval(league))["state"] == "REFUSED"
    notices = _late_notices(league)
    assert len(notices) == 1
    assert "⛔ Your season has changed since this review" in notices[0]


async def test_a_stop_between_the_refusal_and_its_notice_posts_the_notice_on_restart(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    league.clock.advance(minutes=15)
    await _edit_round(league)
    _refused(league, REVIEW_CHANNEL)

    await run_queue(league.bot)
    assert (await _approval(league))["state"] == "REFUSED"
    assert await _stopped_at(league) == "tell_review_channel"

    league.channel(REVIEW_CHANNEL).send_fails = None
    await league.restart()
    await retry_job(league.bot)

    assert len(_late_notices(league)) == 1
    assert await _stopped_at(league) is None


async def test_a_late_refusal_notice_discord_refuses_stops_the_queue_and_a_discard_drops_it(
    tmp_path, monkeypatch,
):
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    league.clock.advance(minutes=15)
    await _edit_round(league)
    _refuse_starting(league, REVIEW_CHANNEL, LATE_REFUSAL_HEAD)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "tell_review_channel"
    named = f"telling <#{REVIEW_CHANNEL}> that season 3 was not approved"
    assert named in league.log()

    await discard_job(league.bot)

    discards = [line for line in _log_lines(league) if "| Discard job #" in line]
    assert len(discards) == 1 and named in discards[0]
    assert _late_notices(league) == []
    assert await _stopped_at(league) is None
    # The follow-on started as its check passed, so its one job is discarded and the change is
    # done, as any started change whose job is discarded is (call p5).
    told = (await _tell_changes(league))[0]
    assert told["state"] == "DONE"
    jobs = await step_rows(league.db_path, told["id"])
    assert len(jobs) == 1 and "discarded" in jobs[0]["result"]


# ── A discard of the approval before it started, told in the channel ────────────────
#
# The owner's answer r1-2 ("Tell the review's channel"): every outcome of a season approval that
# cannot reach the member is told in the review's channel, a Discard of the approval stopped at
# its own check included. The hook that asks it is unbuilt and named here by analogy with the
# refusal hook of plan 2.1b (`ChangeType.on_refused`): `ChangeType.on_discarded`. The channel is
# told `CHANNEL_NOT_APPROVED`, plan 2.1's "Not approved (`apply` discarded…)" notice: a Discard at
# the check leaves what a discarded `apply` leaves, nothing approved and no refusal to quote, and
# the log channel has the Discard's line for why. 2.1b's `LATE_REFUSAL_HEAD` frames a refusal's own
# reply, which a Discard does not have.

def _check_raises() -> ExitStack:
    """The approval's check raises as it runs, as a fault reading the season would: the
    fingerprint it takes raises. The press, already made, is not affected."""
    return _everywhere("take_fingerprint", AsyncMock(side_effect=RuntimeError("disk I/O error")))


async def _stopped_at_its_check(league: Any) -> None:
    with _check_raises():
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    assert (await _approval(league))["state"] == "QUEUED"


async def test_a_discard_of_the_approval_stopped_at_its_check_after_the_reply_expired_is_told_in_the_review_s_channel(
    tmp_path, monkeypatch,
):
    """The approval's check raises as it comes up to run, so the queue stops at it before it
    starts; fifteen minutes on, the reply past updating, a league admin discards it. The approval's
    discard hook (`on_discarded`, by analogy with `on_refused`) asks the change that tells the
    review's channel the season was not approved, as the bot's for the approver."""
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    await _stopped_at_its_check(league)
    league.clock.advance(minutes=15)

    await discard_job(league.bot)

    assert (await _approval(league))["state"] == "DISCARDED"
    assert (await league.season())["stage"] == "PLACEMENTS"
    told = await _tell_changes(league)
    assert [(row["origin"], row["actor_id"], row["state"]) for row in told] == [
        ("BOT", ADMIN_ID, "DONE")
    ]
    assert _told(league, CHANNEL_NOT_APPROVED) == 1
    assert "was discarded by a league admin" not in reply(press)
    assert await _stopped_at(league) is None


async def test_a_discard_of_the_approval_stopped_at_its_check_while_the_reply_can_be_updated_tells_the_channel_nothing(
    tmp_path, monkeypatch,
):
    """The same, discarded five minutes on: the member's reply says the approval was discarded,
    and nothing is asked or posted in the review's channel. Passes already, and is left unmarked:
    the queue updates the reply of a change discarded before it started today, and the discard
    hook (`on_discarded`, by analogy with `on_refused`) must ask nothing where it still can."""
    league = await _league_for(tmp_path, monkeypatch)
    press = await _pressed(league)
    await _stopped_at_its_check(league)
    league.clock.advance(minutes=5)

    await discard_job(league.bot)

    assert (await _approval(league))["state"] == "DISCARDED"
    assert "was discarded by a league admin: nothing of it was done." in reply(press)
    assert await _tell_changes(league) == []
    assert _told(league, f"<@{ADMIN_ID}> — Season #3") == 0


async def test_a_discard_of_the_approval_stopped_at_its_check_after_a_restart_is_told_in_the_channel(
    tmp_path, monkeypatch,
):
    """The press, a restart, then the approval's check raising as it runs; a league admin
    discards it at once. Nothing being held after a restart, the reply cannot be updated, so the
    discard hook (`on_discarded`, by analogy with `on_refused`) asks the channel's notice."""
    league = await _league_for(tmp_path, monkeypatch)
    await _pressed(league)
    with _check_raises():
        await league.restart()
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"

    await discard_job(league.bot)

    assert (await _approval(league))["state"] == "DISCARDED"
    assert _told(league, CHANNEL_NOT_APPROVED) == 1
