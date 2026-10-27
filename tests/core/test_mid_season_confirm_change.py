"""Confirming a season's placements mid-season on the change queue (#439, slice 8, Part B).

✅ Confirm placements on a mid-season `/season placements-review` is acknowledged at once and asks
the change queue for the confirmation, the review taken down ahead of it. It is checked when
pressed and again when it runs: another confirmation in hand, the season no longer placing
drivers, every division done, the season changed since the review, an unsettled signup, a missing
channel and an image fault each refuse it, and nothing is confirmed. The placements and the
season's return to Ongoing are saved in one save; each new driver's roles and each division's
lineup follow as jobs of their own, each stopping the queue when Discord refuses. Where the
outcome can no longer reach the manager, the review's channel is told. One log line and one audit
entry record it (core :128-:138, :293-:338, :350-354; constitution V).

The league is `placing_league`'s: season 3 ongoing, placements; Lewis committed at Ferrari in Pro;
Max beside him and Charles at McLaren in Am placed mid-season and not yet committed. Admin (77)
runs the review in channel 610 and presses. Every test drives the real cog and the real queue
through `tests/support/season_league.py`, with "now" pinned.
"""
from __future__ import annotations

import importlib
import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import discord
import pytest

from tests.support.change_queue import (
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    ADMIN_ID,
    AM,
    AM_ROLE,
    CHARLES,
    DIVISIONS,
    FERRARI_ROLE,
    LEWIS,
    MAX,
    MCLAREN_ROLE,
    PLACEMENTS_CONFIRM_KIND as KIND,
    PRO,
    PRO_ROLE,
    REVIEW_CHANNEL,
    SEASON_ID,
    TEST_DRIVER,
    placing_league,
    post_mid_season_review,
    press_approve,
    reply,
    round_id,
)
from tests.support.mentions import notified

PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
CONFIRMED = "✅ 2 placement(s) confirmed. The season is ongoing again."
NOT_EVERYTHING = "⚠️ **Not everything could be done**"
NOT_CONFIRMED = (
    "Nothing has been confirmed: the season is still placing drivers. Run `/season "
    "placements-review` again."
)
ALREADY = (
    "⏳ This season's placements are already being confirmed, so this press confirms nothing. "
    "If that confirmation has stopped, a league manager or admin can press Retry, or a league "
    "admin Discard, on its notice in the log channel."
)
EVERY_DIVISION_DONE = (
    "⛔ Every division of season 3 is done, so there is nothing left to place drivers into. "
    "Run `/season complete`, which winds the season down. **Nothing has been confirmed.**"
)
NO_LONGER_PLACING = "⛔ The season is no longer placing drivers. **Nothing has been confirmed.**"
CHANGED = "⛔ The season has changed since this review"
CHANNEL_CONFIRMED = (
    f"<@{ADMIN_ID}> — the placements are confirmed and the season is ongoing again"
)
CHANNEL_NOT_CONFIRMED = (
    f"⛔ <@{ADMIN_ID}> — Season #3's placements were not confirmed. Your reply could not be "
    "updated; the log channel has why. Run `/season placements-review` again."
)
LATE_REFUSAL_HEAD = (
    f"⛔ <@{ADMIN_ID}> — Season #3's placements were not confirmed when their turn came on the "
    "queue, and your reply could no longer be updated:"
)
CONFIRMED_LINE = (
    f"Admin (`<@{ADMIN_ID}>`) | /season placements-review | Confirmed\n"
    "  season: Season #3\n  placements: 2\n" + "―" * 36
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _league(tmp_path: Any, monkeypatch: Any, **kwargs: Any) -> Any:
    return await placing_league(tmp_path, monkeypatch=monkeypatch, **kwargs)


async def _pressed(league: Any, review: Any = None) -> Any:
    """Press ✅ Confirm placements on *review*, or on one posted for the purpose; the queue is
    not run."""
    press = await press_approve(league, review or await post_mid_season_review(league))
    assert not league.errors, league.errors
    return press


async def _confirmations(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _confirmation(league: Any) -> dict[str, Any]:
    rows = await _confirmations(league)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    return await step_rows(league.db_path, (await _confirmation(league))["id"])


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the confirmation's first job *name* is done."""
    for _ in range(100):
        if await _confirmations(league) and any(
            job["name"] == name and job["done_at"] for job in await _jobs(league)
        ):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the confirmation's job {name} never finished")


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


async def _uncommitted(league: Any) -> int:
    rows = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE committed = 0"
    )
    return rows[0]["n"]


async def _stage(league: Any) -> str:
    return (await league.season())["stage"]


def _refused(league: Any, cid: int) -> None:
    league.channel(cid).send_fails = http_error(discord.Forbidden, status=403,
                                                text="Missing Access")


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _confirmed_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league)
            if "| /season placements-review | Confirmed" in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if "⛔" in line and "Confirm placements" in line]


def _not_done(text: str) -> list[str]:
    assert NOT_EVERYTHING in text, text
    section = text.split(NOT_EVERYTHING, 1)[1]
    return [line[2:] for line in section.splitlines() if line.startswith("• ")]


def _told(league: Any, text: str) -> int:
    return sum(text in sent for sent in league.texts(REVIEW_CHANNEL))


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch *name* in every module the confirmation may reach it through."""
    stack = ExitStack()
    for path in ("leaguebot.core.services.season_service",
                 "leaguebot.core.services.season_lifecycle_service",
                 "leaguebot.core.services.season_fingerprint_service",
                 "leaguebot.core.services.mid_season_confirm_change"):
        try:
            module = importlib.import_module(path)
        except ImportError:
            continue
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _failing_save(*, times: int | None = None) -> ExitStack:
    """The save commits the placements and then raises, as a fault of the database would,
    every time or the first *times* times."""
    from leaguebot.core.services import season_service

    real = season_service.commit_placements_on
    tries = {"n": 0}

    async def _then_fail(*args: Any, **kwargs: Any) -> Any:
        found = await real(*args, **kwargs)
        tries["n"] += 1
        if times is None or tries["n"] <= times:
            raise RuntimeError("disk I/O error")
        return found

    return _everywhere("commit_placements_on", _then_fail)


def _check_raises() -> ExitStack:
    """The confirmation's check raises as it runs: the fingerprint it takes raises."""
    return _everywhere("take_fingerprint", AsyncMock(side_effect=RuntimeError("disk I/O error")))


async def _edit_round(league: Any) -> None:
    await league.write("UPDATE rounds SET track_name = 'Monza' WHERE id = ?", round_id(PRO, 2))


async def _delete_lineup_channel(league: Any) -> None:
    league.remove_channel(PRO_CH.lineup)


async def _lose_rasteriser(league: Any) -> None:
    league.rasteriser_on = False


async def _finish_every_division(league: Any) -> None:
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE season_id = ?", SEASON_ID)


#: Each way the season can change while the confirmation waits: what changes it, the league it
#: needs, and the reply's words for it.
_REFUSALS = {
    "a round edited": (_edit_round, {}, CHANGED),
    "a lineup channel deleted": (
        _delete_lineup_channel, {},
        "**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`.",
    ),
    "the rasteriser gone": (_lose_rasteriser, {"images": True},
                            "• Inkscape is not installed on this host."),
}


# ── The defects ─────────────────────────────────────────────────────────────────────


async def test_a_stop_after_the_save_finishes_the_roles_and_lineups_on_restart(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)
    await _run_through(league, "apply")

    await league.restart()
    await run_queue(league.bot)

    assert league.granted == {MAX: [PRO_ROLE, FERRARI_ROLE], CHARLES: [AM_ROLE, MCLAREN_ROLE]}
    assert len(league.texts(PRO_CH.lineup)) == 1
    assert len(league.texts(AM_CH.lineup)) == 1
    assert await _stage(league) == "ONGOING"
    assert len(_confirmed_lines(league)) == 1


async def test_the_placements_and_the_season_s_move_are_saved_in_one_save(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)

    with _failing_save():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    assert await _uncommitted(league) == 2
    assert await _stage(league) == "ONGOING_PLACEMENTS"

    await retry_job(league.bot)

    assert await _uncommitted(league) == 0
    assert await _stage(league) == "ONGOING"


@pytest.mark.parametrize("when", ["at the press", "when it runs"])
async def test_a_confirmation_with_every_division_done_is_refused_naming_season_complete(
    tmp_path, monkeypatch, when,
):
    """Pro and Am are finished before the review is posted, or after the press while the
    confirmation waits: the refusal names `/season complete`, and nothing is committed."""
    league = await _league(tmp_path, monkeypatch)
    if when == "at the press":
        await _finish_every_division(league)
    press = await _pressed(league)
    if when == "when it runs":
        await _finish_every_division(league)
        await run_queue(league.bot)
        assert (await _confirmation(league))["state"] == "REFUSED"
    else:
        assert await _confirmations(league) == []

    assert EVERY_DIVISION_DONE in reply(press)
    assert await _uncommitted(league) == 2
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert league.granted == {}


async def test_a_role_discord_refuses_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    league.grant_fails[MAX] = http_error(discord.Forbidden, status=403,
                                         text="Missing Permissions")
    press = await _pressed(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "grant_roles"
    assert f"granting `<@{MAX}>` the roles of **Pro** and Ferrari" in league.log()
    await discard_job(league.bot)

    line = (f"<@{MAX}> — their roles could not be granted. Give them their division's and "
            "team's roles by hand.")
    assert line in _not_done(reply(press))
    assert f"not done: {line.replace(f'<@{MAX}>', f'`<@{MAX}>`')}" in league.log()
    assert CHARLES in league.granted
    assert await _stage(league) == "ONGOING"


async def test_a_driver_discord_cannot_fetch_for_another_reason_stops_the_queue(
    tmp_path, monkeypatch,
):
    """Discord answers 500 for Max: today the confirmation passes him over in silence."""
    league = await _league(tmp_path, monkeypatch)
    league.fetch_fails[MAX] = http_error(status=500, text="Internal Server Error")
    await _pressed(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "grant_roles"
    assert MAX not in league.granted
    assert await _uncommitted(league) == 0


async def test_a_lineup_discord_refuses_stops_the_queue_and_once_discarded_is_named(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    _refused(league, PRO_CH.lineup)
    press = await _pressed(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_lineup"
    await discard_job(league.bot)

    line = ("**Pro** — its lineup could not be posted. No command posts it again; it is posted "
            "with the next change to its drivers.")
    assert line in _not_done(reply(press))
    assert f"not done: {line}" in league.log()
    assert await _uncommitted(league) == 0


# ── A second press ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("first", ["waiting", "stopped at apply", "stopped at a role"])
async def test_a_second_press_while_the_confirmation_is_in_hand_is_refused_at_once_without_a_job_number(
    tmp_path, monkeypatch, first,
):
    """Two reviews stand; the second is pressed while the first's confirmation waits on the
    queue, is stopped at its save, or is stopped at a driver's roles after it."""
    league = await _league(tmp_path, monkeypatch)
    review, other = await post_mid_season_review(league), await post_mid_season_review(league)
    await _pressed(league, review)
    stack = ExitStack()
    if first == "stopped at apply":
        stack = _failing_save()
        with stack:
            await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    elif first == "stopped at a role":
        league.grant_fails[MAX] = http_error(discord.Forbidden, status=403)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "grant_roles"

    second = await _pressed(league, other)

    assert ALREADY in reply(second)
    assert "job #" not in reply(second)
    assert len(await _confirmations(league)) == 1

    league.grant_fails.clear()
    if first == "waiting":
        await run_queue(league.bot)
    else:
        await retry_job(league.bot)
    assert await _stage(league) == "ONGOING"
    assert len(_confirmed_lines(league)) == 1


async def test_a_press_while_the_confirmation_is_stopped_after_its_save_is_refused_as_in_hand_not_as_changed(
    tmp_path, monkeypatch,
):
    """The first confirmation committed the placements, so the season no longer fingerprints as
    the second review saw it; the second press is told what is true: one is in hand."""
    league = await _league(tmp_path, monkeypatch)
    review, other = await post_mid_season_review(league), await post_mid_season_review(league)
    league.grant_fails[MAX] = http_error(discord.Forbidden, status=403)
    await _pressed(league, review)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "grant_roles"

    second = await _pressed(league, other)

    assert ALREADY in reply(second)
    assert "has changed since this review" not in reply(second)


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_manager_is_told_at_once_and_the_reply_is_updated_when_the_placements_are_confirmed(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    first_job = (await _jobs(league))[0]["id"]

    assert reply(press) == (
        "⏳ Confirming season 3's placements. This message will be updated when it is done; if "
        f"it takes longer, the log channel will say so. It begins with job #{first_job}."
    )

    await run_queue(league.bot)

    assert CONFIRMED in reply(press)
    assert NOT_EVERYTHING not in reply(press)


async def test_the_press_is_not_deferred_the_acknowledgement_is_its_response(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)

    press = await _pressed(league)

    assert press.response.defer.await_count == 0
    [ack] = press.response.send_message.await_args_list
    assert str(ack.args[0] if ack.args else ack.kwargs["content"]).startswith(
        "⏳ Confirming season 3's placements."
    )


async def test_one_line_records_the_confirmation_after_the_last_job(tmp_path, monkeypatch):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)

    await _run_through(league, "tell_review_channel")
    assert _confirmed_lines(league) == []
    await run_queue(league.bot)

    assert _confirmed_lines(league) == [CONFIRMED_LINE]


async def test_each_driver_s_roles_and_each_division_s_lineup_are_jobs_of_their_own(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)

    await run_queue(league.bot)

    names = [job["name"] for job in await _jobs(league)]
    assert names[0] == "apply"
    assert names[-2:] == ["tell_review_channel", "close"]
    assert names.count("grant_roles") == 2
    assert names.count("post_lineup") == 2
    assert list(league.granted) == [MAX, CHARLES]
    assert LEWIS not in league.granted
    sends = [cid for kind, cid, _mid in league.events if kind == "send"]
    assert sends.index(PRO_CH.lineup) < sends.index(AM_CH.lineup)


async def test_a_driver_discord_reports_absent_is_passed_over_and_the_queue_runs_on(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    league.absent.add(MAX)
    await _pressed(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    assert MAX not in league.granted
    assert CHARLES in league.granted
    assert (await _confirmation(league))["state"] == "DONE"


async def test_a_test_driver_is_granted_nothing(tmp_path, monkeypatch):
    """The test driver (104), placed at McLaren in Am beside Charles, is committed with the
    others and granted nothing."""
    league = await _league(tmp_path, monkeypatch, extra_drivers=True)
    await _pressed(league)

    await run_queue(league.bot)

    assert TEST_DRIVER not in league.granted
    assert [job["name"] for job in await _jobs(league)].count("grant_roles") == 2
    assert await _uncommitted(league) == 0


async def test_the_confirmation_is_audited(tmp_path, monkeypatch):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)

    await run_queue(league.bot)

    rows = await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'PLACEMENTS_CONFIRMED'"
    )
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_ID
    assert json.loads(rows[0]["old_value"])["stage"] == "ONGOING_PLACEMENTS"
    new = json.loads(rows[0]["new_value"])
    assert (new["season_id"], new["stage"]) == (SEASON_ID, "ONGOING")
    assert 2 in new.values()


# ── The checks when it runs ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("change", sorted(_REFUSALS))
async def test_a_refusal_found_when_the_confirmation_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, monkeypatch, change,
):
    alter, kwargs, words = _REFUSALS[change]
    league = await _league(tmp_path, monkeypatch, **kwargs)
    press = await _pressed(league)
    await alter(league)

    await run_queue(league.bot)

    assert words in reply(press)
    assert (await _confirmation(league))["state"] == "REFUSED"
    assert len(_refusal_lines(league)) == 1
    assert await _uncommitted(league) == 2
    assert league.granted == {}
    assert await _stopped_at(league) is None


async def test_a_season_no_longer_placing_when_the_confirmation_runs_is_refused_and_nothing_committed(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    await league.write("UPDATE seasons SET stage = 'ONGOING' WHERE id = ?", SEASON_ID)

    await run_queue(league.bot)

    assert NO_LONGER_PLACING in reply(press)
    assert (await _confirmation(league))["state"] == "REFUSED"
    assert await _uncommitted(league) == 2
    assert league.granted == {}


async def test_a_retried_save_judges_the_confirmation_again_and_refuses_what_changed_while_it_was_stopped(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    with _failing_save(times=1):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await _edit_round(league)

        await retry_job(league.bot)

    assert CHANGED in reply(press)
    assert CONFIRMED not in reply(press)
    assert "judge" in [job["name"] for job in await _jobs(league)]
    assert len(_refusal_lines(league)) == 1
    assert _confirmed_lines(league) == []
    assert await _uncommitted(league) == 2
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert await _stopped_at(league) is None


async def test_a_retried_save_with_nothing_changed_confirms(tmp_path, monkeypatch):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    with _failing_save(times=1):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"

        await retry_job(league.bot)

    assert CONFIRMED in reply(press)
    assert await _stage(league) == "ONGOING"
    assert len(_confirmed_lines(league)) == 1
    assert await _stopped_at(league) is None


async def test_a_discarded_save_confirms_nothing_and_says_to_review_again(tmp_path, monkeypatch):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)

    with _failing_save():
        await run_queue(league.bot)
        await discard_job(league.bot)

    assert NOT_CONFIRMED in reply(press)
    assert CONFIRMED not in reply(press)
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert await _uncommitted(league) == 2
    assert _confirmed_lines(league) == []
    assert league.granted == {}


async def test_a_discarded_rejudgement_confirms_nothing(tmp_path, monkeypatch):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    with _failing_save(times=1):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        with _check_raises():
            await retry_job(league.bot)
        assert await _stopped_at(league) == "judge"

        await discard_job(league.bot)

    assert NOT_CONFIRMED in reply(press)
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert await _uncommitted(league) == 2
    assert league.granted == {}
    assert await _stopped_at(league) is None


# ── The review's channel ────────────────────────────────────────────────────────────


async def test_the_confirmation_s_review_channel_line_notifies_only_the_member_it_names(
    tmp_path, monkeypatch,
):
    """The league admin confirms season 3's mid-season placements, and the bot restarts before
    their reply is updated. The line telling the review's channel the placements are confirmed
    mentions the admin, and may notify them alone: no role, no other member, and not
    @everyone."""
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)
    await league.restart()

    await run_queue(league.bot)

    [call] = [call for call in league.channels[REVIEW_CHANNEL].send.await_args_list
              if call.args and CHANNEL_CONFIRMED in str(call.args[0])]
    assert notified(call.kwargs.get("allowed_mentions")) == {
        "everyone": False, "users": {int(ADMIN_ID)}, "roles": set()
    }


@pytest.mark.parametrize("cut_off", ["fourteen minutes passed", "a restart"])
async def test_a_confirmation_whose_reply_can_no_longer_be_updated_tells_the_review_s_channel(
    tmp_path, monkeypatch, cut_off,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)
    if cut_off == "a restart":
        await league.restart()
        await run_queue(league.bot)
    else:
        _refused(league, PRO_CH.lineup)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "post_lineup"
        league.clock.advance(minutes=15)
        league.channel(PRO_CH.lineup).send_fails = None
        await retry_job(league.bot)

    assert _told(league, CHANNEL_CONFIRMED) == 1
    assert "tell_review_channel" in [job["name"] for job in await _jobs(league)]


async def test_a_confirmation_whose_reply_is_updated_tells_the_channel_nothing(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)

    await run_queue(league.bot)

    assert CONFIRMED in reply(press)
    assert _told(league, f"<@{ADMIN_ID}> —") == 0
    assert "tell_review_channel" in [job["name"] for job in await _jobs(league)]


async def test_a_refusal_at_run_after_the_reply_expired_is_told_in_the_review_s_channel(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)
    league.clock.advance(minutes=15)
    await _edit_round(league)

    await run_queue(league.bot)

    assert (await _confirmation(league))["state"] == "REFUSED"
    notices = [text for text in league.texts(REVIEW_CHANNEL)
               if text.startswith(LATE_REFUSAL_HEAD)]
    assert len(notices) == 1
    assert CHANGED in notices[0]
    told = [row for row in await change_rows(league.db_path) if row["kind"] == "channel.tell"]
    assert [(row["origin"], row["actor_id"]) for row in told] == [("BOT", ADMIN_ID)]


async def test_a_discard_of_the_confirmation_stopped_at_its_check_after_the_reply_expired_is_told_in_the_review_s_channel(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    press = await _pressed(league)
    with _check_raises():
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    assert (await _confirmation(league))["state"] == "QUEUED"
    league.clock.advance(minutes=15)

    await discard_job(league.bot)

    assert (await _confirmation(league))["state"] == "DISCARDED"
    assert _told(league, CHANNEL_NOT_CONFIRMED) == 1
    assert await _stage(league) == "ONGOING_PLACEMENTS"
    assert "was discarded by a league admin" not in reply(press)


async def test_a_channel_notice_discord_refuses_stops_the_queue_and_once_discarded_is_named_in_the_line(
    tmp_path, monkeypatch,
):
    league = await _league(tmp_path, monkeypatch)
    await _pressed(league)
    await league.restart()
    league.channel(REVIEW_CHANNEL).fail_when = (
        lambda content, _kwargs: CHANNEL_CONFIRMED in (content or "")
    )

    await run_queue(league.bot)
    assert await _stopped_at(league) == "tell_review_channel"
    assert (f"telling <#{REVIEW_CHANNEL}> how the confirmation of season 3's placements ended"
            in league.log())
    await discard_job(league.bot)

    assert _told(league, CHANNEL_CONFIRMED) == 0
    assert (f"not done: <#{REVIEW_CHANNEL}> was not told how the confirmation ended"
            in league.log())


# ── The review ──────────────────────────────────────────────────────────────────────


async def test_the_review_is_taken_down_ahead_of_the_confirmation_and_stays_up_for_a_refused_request(
    tmp_path, monkeypatch,
):
    """Admin's review is pressed: the take-down is asked before the confirmation and, run,
    deletes the report and the prompt and forgets the row. A second review pressed while the
    confirmation waits is refused at once and stays standing."""
    from leaguebot.core.services.season_review_changes import TAKE_DOWN

    league = await _league(tmp_path, monkeypatch)
    review, other = await post_mid_season_review(league), await post_mid_season_review(league)

    await _pressed(league, review)
    await _pressed(league, other)

    assert [row["kind"] for row in await change_rows(league.db_path)] == [TAKE_DOWN, KIND]
    await run_queue(league.bot)
    for message in [*review.report, review.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) in league.events
    for message in [*other.report, other.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) not in league.events
    [standing] = await league.rows("SELECT * FROM season_review_prompts")
    assert int(standing["message_id"]) == other.prompt.id
