"""Approving a points amendment on the change queue (#439, slice 3).

✅ Approve on `/results amend review` asks the change queue for the season's approval. It is
checked when pressed and again when it runs; the season's points, the rescoring, the standings
and the attendance are saved in one save; the reposts, their deletions and the attendance follow
as jobs, each of which stops the queue when it fails. While the approval is in hand, the commands
that stage points changes are refused, naming its job.

Most tests drive the cog and the real queue through `tests/support/points_league.py`, with "now"
pinned. Those of the rescoring and reposting (#443), of a division with no channels and of the
attendance switch seed a season of their own on `db_path` and ask the queue for the approval
directly, since they need sessions, configurations, drivers or an attendance state that
`points_league` does not give.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.results.services.points.amendment_mode_service import get_amendment_state
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    restart_queue,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.points_league import (
    ADMIN_ID,
    AM_RESULTS,
    AM_STANDINGS,
    BETA,
    BETA_ATTENDANCE,
    BETA_RESULTS,
    BETA_STANDINGS,
    PRO,
    AM,
    PRO_ATTENDANCE,
    PRO_RESULTS,
    PRO_STANDINGS,
    PRO_VERDICTS,
    MAX,
    MAX_PROFILE,
    LEWIS,
    LEWIS_PROFILE,
    NOW,
    audit_rows,
    old_results,
    old_standings,
    open_panel,
    points_league,
    points_state,
    press_approve,
    race_points,
    round_id,
    run_staging,
    write,
)
from tests.support.review_league import block_queue, candidate, run_until_done, stopped_at
from tests.support.saves import (
    enable_amendment_mode,
    modify_fl_bonus,
    modify_fl_position_limit,
    modify_session_points,
)
from tests.support.settings_league import last_said
from tests.support.switches import answer_switch

KIND = "results.points_amendment.approve"

SUCCESS = "✅ Amendment approved. All standings recomputed and reposted."
MODE_OFF = (
    "❌ Amendment mode is not active. Nothing was changed: these changes were already "
    "approved, or amendment mode was turned off after this panel was drawn."
)
NOT_PUBLISHED = "⛔ Amendment not approved — the result could not be published:"
OUT_OF_ORDER = "❌ Amendment not approved — the points would be out of order:"
#: The ordering error for P2 staged at 30 over P1 staged at 26.
ORDERING_ERROR = "Config 'Standard', FEATURE_RACE: position 1 (26 pts) < position 2 (30 pts)"
NOTHING_CHANGED = (
    "Nothing was changed: the season keeps its points, the staged changes stay staged and "
    "amendment mode stays on. Run `/results amend review` again."
)
IN_HAND = "⏳ This points amendment is already being approved (job #{job}). If it has stopped, "\
    "press Retry or Discard on its notice in the log channel."
STAGING_REFUSAL = (
    "⏸️ Season 1's points amendment is being approved (job #{job}), so the staged changes and "
    "amendment mode cannot be changed until that is done. Let it finish, or press **Retry** or "
    "**Discard** on its notice if it has stopped, then try again."
)


def _naming(text: str, job: int) -> str:
    """*text* naming job *job*, or with its " (job #N)" left out where *job* is 0, only the
    approval's close being left."""
    return text.format(job=job) if job else text.replace(" (job #{job})", "")


def _unmarked(first_line: str) -> str:
    """A reply's first line without the mark it opens with (its emoji and the space after it), as
    every ⛔ refusal line gives the reply as its reason."""
    return first_line.split(" ", 1)[1]


#: The season's own table, as it stood before any approval.
SEASON_TABLE = [("Standard", "FEATURE_RACE", 1, 25), ("Standard", "FEATURE_RACE", 2, 18)]
APPROVED_TABLE = [("Standard", "FEATURE_RACE", 1, 26), ("Standard", "FEATURE_RACE", 2, 18)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replies(press: Any) -> str:
    """Everything the press was answered with: the acknowledgement, then its updates."""
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


def _refusal_lines(league: Any, command: str = "results amend review") -> list[str]:
    return [
        line for line in league.bot.log_channel.sent
        if line.startswith(f"⛔ `/{command}` refused for")
    ]


def _success_lines(league: Any) -> list[str]:
    return [line for line in league.bot.log_channel.sent
            if "/results amend review | Success" in line]


async def _approvals(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _waits_on(league: Any) -> int:
    """The number of the job the first approval in hand waits on, or 0 where every job is done
    and only its close is left."""
    change = (await _approvals(league))[0]
    pending = [row for row in await step_rows(league.db_path, change["id"])
               if row["done_at"] is None]
    return pending[0]["id"] if pending else 0


def _refuse_send(league: Any, cid: int, nth: int) -> None:
    """Channel *cid* refuses its *nth* send, once."""
    seen = {"sends": 0, "refused": False}

    def when(_content: str, _kwargs: dict[str, Any]) -> bool:
        seen["sends"] += 1
        if seen["sends"] == nth and not seen["refused"]:
            seen["refused"] = True
            return True
        return False

    league.channel(cid).fail_when = when


def _names_fail_once() -> Any:
    """The first `names` job of the approval fails, as Discord failing would."""
    from leaguebot.results.services.review import review_posting

    real = review_posting.standings_display_names
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise http_error(status=503, text="Service Unavailable")
        return await real(*args, **kwargs)

    return patch.object(review_posting, "standings_display_names", flaky)


def _standings_fail() -> Any:
    """Every team standings computed raises, as a fault in the bot would."""
    return patch(
        "leaguebot.results.services.standings.standings_service.compute_team_standings_on",
        new=AsyncMock(side_effect=RuntimeError("the standings could not be computed")),
    )


async def _approval_in_hand(league: Any, state: str, stack: ExitStack) -> Any:
    """Put the season's approval in hand, as *state* says: `waiting` behind a stopped
    blocker, stopped at `names`, stopped at a `post` after its save (Pro's round 1
    results refused once), or at `close`, its every job done and only its close left. Returns
    what clears the stop and lets the approval finish."""
    if state == "close":
        await press_approve(league)
        await run_until_done(league, "close")

        async def finish() -> None:
            await run_queue(league.bot)
        return finish
    if state == "waiting":
        holder = await block_queue(league)
        await press_approve(league)

        async def clear() -> None:
            holder["fail"] = False
            await retry_job(league.bot)
        return clear
    if state == "names":
        stack.enter_context(_names_fail_once())
    else:
        _refuse_send(league, PRO_RESULTS, 1)
    await press_approve(league)
    await run_queue(league.bot)
    stopped = await stopped_at(league)
    assert stopped == "names" if state == "names" else stopped not in (None, "names", "apply")

    async def clear_stop() -> None:
        await retry_job(league.bot)
    return clear_stop


# ---------------------------------------------------------------------------
# The defects
# ---------------------------------------------------------------------------


async def test_a_repost_discord_refuses_stops_the_queue_and_no_success_is_recorded_until_it_lands(
    tmp_path,
):
    """Defect 2: a repost that fails stops the queue, and the approval is not recorded as a
    success until it lands."""
    league = await points_league(tmp_path)
    _refuse_send(league, PRO_RESULTS, 2)  # round 2's Feature Race results

    press = await press_approve(league)
    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None
    assert "posting round 2's Feature Race results in <#700>" in league.log()
    assert _success_lines(league) == []
    assert (f"❌ `/results amend review` is stopped at job #{job['id']} and will be tried again."
            in _replies(press))

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(_success_lines(league)) == 1
    assert _replies(press).endswith(SUCCESS)


async def test_a_standings_recompute_that_fails_changes_nothing_and_can_be_approved_again(
    tmp_path,
):
    """Defect 2, after the commit: a standings recompute that fails stops the queue at the
    save, which leaves the season as it stood; discarded, the approval can be made again."""
    league = await points_league(tmp_path)
    before = await points_state(league)

    with _standings_fail():
        press = await press_approve(league)
        await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert await points_state(league) == before
    assert all(league.sent_to(cid) == []
               for cid in (PRO_RESULTS, PRO_STANDINGS, AM_RESULTS, AM_STANDINGS))

    await discard_job(league.bot)
    assert NOTHING_CHANGED in _replies(press)
    assert await points_state(league) == before

    again = await press_approve(league)
    await run_queue(league.bot)
    assert _replies(again).endswith(SUCCESS)
    assert (await points_state(league))["points"] == APPROVED_TABLE


async def test_a_second_approval_from_an_older_panel_is_refused_and_the_season_keeps_its_points(
    tmp_path,
):
    """#507: an older panel's Approve, pressed after the changes were approved, is refused at
    once and does not empty the season's points."""
    league = await points_league(tmp_path)
    first, second = await open_panel(league), await open_panel(league)

    await press_approve(league, first)
    await run_queue(league.bot)
    refused = await press_approve(league, second)

    assert acknowledgement(refused) == MODE_OFF
    assert len(_refusal_lines(league)) == 1
    assert len(await _approvals(league)) == 1
    assert (await points_state(league))["points"] == APPROVED_TABLE


async def test_an_approval_pressed_after_amendment_mode_was_turned_off_is_refused(tmp_path):
    """#507's second route: the staged changes reverted and amendment mode turned off while the
    panel stood open; its Approve is refused and the season's tables are untouched."""
    league = await points_league(tmp_path)
    panel = await open_panel(league)
    await run_staging(league, "revert")
    await run_staging(league, "toggle")
    before = await points_state(league)

    press = await press_approve(league, panel)

    assert acknowledgement(press) == MODE_OFF
    assert await _approvals(league) == []
    assert await points_state(league) == before
    assert before["points"] == SEASON_TABLE


async def test_the_old_results_tables_are_deleted_once_their_replacements_stand(tmp_path):
    """#445, the points half: with images off, each old results table is deleted, and only
    after its replacement is sent."""
    league = await points_league(tmp_path)

    await press_approve(league)
    await run_queue(league.bot)

    for division_id, cid in ((PRO, PRO_RESULTS), (AM, AM_RESULTS)):
        for number in (1, 2, 3):
            old = old_results(division_id, number)
            assert old in league.deleted_from(cid)
            deleted_at = league.events.index(("delete", cid, old))
            sends_before = [e for e in league.events[:deleted_at] if e[:2] == ("send", cid)]
            assert len(sends_before) >= number


@pytest.mark.parametrize("lost, setting", [(BETA_RESULTS, "results"),
                                           (BETA_STANDINGS, "standings")])
async def test_a_cancelled_division_whose_channel_is_gone_refuses_the_press(
    tmp_path, lost, setting,
):
    """Owner, "Refuse at the press": a cancelled division's lost channel is named on the panel,
    and the press is refused at once, changing nothing."""
    league = await points_league(tmp_path, cancelled_division=True)
    league.remove_channel(lost)
    fault = f"**Beta** — the {setting} channel (id {lost}) is not in the server."
    before = await points_state(league)

    panel = await open_panel(league)
    press = await press_approve(league, panel)

    assert fault in panel.text
    reply = acknowledgement(press)
    assert reply.startswith(NOT_PUBLISHED)
    assert fault in reply
    lines = _refusal_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0].endswith(f"— {_unmarked(NOT_PUBLISHED)}")
    assert fault in lines[0]
    assert await _approvals(league) == []
    assert await points_state(league) == before


#: The commands that set each channel the approval checks.
REPAIR_COMMANDS = (
    "`/results channel results`", "`/results channel standings`",
    "`/attendance channel attendance`", "`/results channel verdicts`",
)


async def test_the_panel_names_the_command_for_every_channel_the_approval_checks(tmp_path):
    """A cancelled division's attendance channel is lost: the panel's refusal names the command
    that sets each channel the approval checks, results, standings, attendance and verdicts, not
    the results and standings ones alone."""
    league = await points_league(tmp_path, cancelled_division=True, attendance=True)
    league.remove_channel(BETA_ATTENDANCE)

    panel = await open_panel(league)

    assert "**Beta** — the attendance channel (id 722)" in panel.text
    for command in REPAIR_COMMANDS:
        assert command in panel.text


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


async def _results_off(league: Any) -> None:
    await league.switch_results(False)


async def _no_live_season(league: Any) -> None:
    await write(league, "UPDATE seasons SET status = 'COMPLETED' WHERE id = 1")


async def _pending_completion(league: Any) -> None:
    await write(league, "UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = 1")


async def _another_season(league: Any) -> None:
    await write(league, "UPDATE seasons SET status = 'COMPLETED' WHERE id = 1")
    await write(
        league,
        "INSERT INTO seasons (id, season_number, start_date, status, stage) "
        "VALUES (2, 2, '2026-09-01', 'ACTIVE', 'ONGOING')",
    )


async def _mode_off(league: Any) -> None:
    await write(league, "UPDATE season_amendment_state SET amendment_active = 0")


async def _already_in_hand(league: Any) -> None:
    await block_queue(league)
    await press_approve(league)


async def _round_amended(league: Any) -> None:
    await write(
        league,
        "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
        "VALUES (113, 706, '[\"FEATURE_RACE\"]', '2026-10-06T11:55:00+00:00')",
    )


async def _out_of_order(league: Any) -> None:
    await write(
        league,
        "UPDATE season_modification_entries SET points = 30 WHERE position = 2",
    )


async def _undeliverable(league: Any) -> None:
    league.remove_channel(PRO_RESULTS)


async def _cancelled_attendance_lost(league: Any) -> None:
    league.remove_channel(BETA_ATTENDANCE)


async def _live_standings_lost(league: Any) -> None:
    league.remove_channel(PRO_STANDINGS)


async def _live_attendance_lost(league: Any) -> None:
    league.remove_channel(PRO_ATTENDANCE)


async def _cannot_post(league: Any) -> None:
    """Send Messages revoked on Pro's results channel, set by name: a bare `MagicMock` grants
    every permission, and a misspelt one would grant too."""
    denied = MagicMock()
    denied.send_messages = False
    league.channel(PRO_RESULTS).permissions_for.return_value = denied


async def _out_of_order_and_undeliverable(league: Any) -> None:
    """Both refusals apply. The ordering is the staged table's own fault, which the manager
    repairs from the panel, where an unreachable channel is the server's and may right itself,
    so the ordering is the one the press names."""
    await _out_of_order(league)
    await _undeliverable(league)


@pytest.mark.parametrize("setup, options, expected, detail", [
    pytest.param(_results_off, {},
                 "❌ The Results & Standings module is not enabled on this server.", None,
                 id="results-off"),
    pytest.param(_no_live_season, {},
                 "❌ `/results amend review` acts on the season this server is building or "
                 "racing, and there is none.", None, id="no-live-season"),
    pytest.param(_pending_completion, {},
                 "❌ Every division of this season is done, so `/results amend review` no "
                 "longer has anything to act on.", None, id="pending-completion"),
    pytest.param(_another_season, {},
                 "❌ The season these changes were staged for is no longer the current one. "
                 "Nothing was approved.", None, id="another-season-live"),
    pytest.param(_mode_off, {}, MODE_OFF, None, id="mode-off"),
    pytest.param(_already_in_hand, {},
                 "⏳ This points amendment is already being approved (job #", None,
                 id="already-in-hand"),
    pytest.param(_round_amended, {},
                 "⏸️ Not approved yet. Round 3 of **Pro** is being amended in <#706>.", None,
                 id="round-amendment-open"),
    pytest.param(_out_of_order, {}, OUT_OF_ORDER, ORDERING_ERROR, id="out-of-order"),
    pytest.param(_undeliverable, {}, NOT_PUBLISHED,
                 "**Pro** — the results channel (id 700) is not in the server.",
                 id="undeliverable"),
    pytest.param(_cancelled_attendance_lost,
                 {"cancelled_division": True, "attendance": True}, NOT_PUBLISHED,
                 "**Beta** — the attendance channel (id 722)", id="cancelled-attendance-lost"),
    pytest.param(_live_standings_lost, {}, NOT_PUBLISHED,
                 "**Pro** — the standings channel (id 701) is not in the server.",
                 id="live-standings-lost"),
    pytest.param(_live_attendance_lost, {"attendance": True}, NOT_PUBLISHED,
                 "**Pro** — the attendance channel (id 705)", id="live-attendance-lost"),
    pytest.param(_cannot_post, {}, NOT_PUBLISHED, "Send Messages", id="cannot-post"),
    pytest.param(_out_of_order_and_undeliverable, {}, OUT_OF_ORDER, ORDERING_ERROR,
                 id="out-of-order-and-undeliverable"),
])
async def test_the_press_is_refused_at_once_in_today_s_words(
    tmp_path, setup, options, expected, detail,
):
    """A panel drawn while the approval could be made; then the condition changes, and Approve
    is pressed. The press is answered with the refusal, one ⛔ line names it with its detail
    (the positions at fault, the channel lost, the permission missing) written beneath, nothing
    is queued, no success is logged and the season, its working copy and amendment mode stay as
    they stood. The ordering's reply and line say nothing of publishing, so the two refusals are
    told apart, and a table both out of order and undeliverable is refused on its ordering. A
    refusal for a channel fault still ends by saying to repair the channels."""
    league = await points_league(tmp_path, **options)
    panel = await open_panel(league)
    await setup(league)
    before = await points_state(league)
    asked = len(await _approvals(league))

    press = await press_approve(league, panel)

    reply = acknowledgement(press)
    assert reply.startswith(expected)
    lines = _refusal_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0].endswith(f"— {_unmarked(reply.split(chr(10))[0])}")
    if detail is not None:
        assert detail in reply
        assert detail in lines[0].partition("\n")[2]
    if expected == OUT_OF_ORDER:
        assert "published" not in lines[0]
        assert "published" not in reply
    if expected == NOT_PUBLISHED:
        assert reply.endswith("Repair the channels above and review again.")
    assert len(await _approvals(league)) == asked
    assert await points_state(league) == before
    assert _success_lines(league) == []


async def _live_channel_lost(league: Any) -> None:
    league.remove_channel(PRO_RESULTS)


async def _cancelled_results_lost(league: Any) -> None:
    league.remove_channel(BETA_RESULTS)


@pytest.mark.parametrize("setup, expected", [
    pytest.param(_live_channel_lost, NOT_PUBLISHED, id="live-channel-lost"),
    pytest.param(_cancelled_results_lost, NOT_PUBLISHED, id="cancelled-results-lost"),
    pytest.param(_mode_off, MODE_OFF, id="mode-off-written-directly"),
])
async def test_a_refusal_found_when_the_approval_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, setup, expected,
):
    """Queued behind a stopped blocker; before it runs, the condition changes. When it runs it
    is refused: the reply is updated with the refusal, one ⛔ line names it, the queue goes on
    and the season stands as it was."""
    league = await points_league(tmp_path, cancelled_division=True)
    holder = await block_queue(league)
    press = await press_approve(league)
    await setup(league)
    before = await points_state(league)

    holder["fail"] = False
    await retry_job(league.bot)

    assert expected.split("\n")[0] in _replies(press)
    assert len(_refusal_lines(league)) == 1
    assert await stopped_job(league.db_path) is None
    assert all(row["state"] not in ("QUEUED", "RUNNING") for row in await _approvals(league))
    assert await points_state(league) == before
    assert before["points"] == SEASON_TABLE


async def test_the_save_refuses_where_amendment_mode_ended_after_the_check(tmp_path):
    """A `names` job fails once; meanwhile amendment mode is turned off by a write to the
    database (no command can reach this window). Retried, the save writes nothing and the
    reply is the mode's refusal."""
    league = await points_league(tmp_path)
    with _names_fail_once():
        press = await press_approve(league)
        await run_queue(league.bot)
        assert await stopped_at(league) == "names"
        await _mode_off(league)
        before = await points_state(league)
        await retry_job(league.bot)

    assert MODE_OFF in _replies(press)
    lines = _refusal_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0].endswith(f"— {_unmarked(MODE_OFF)}")
    assert await points_state(league) == before
    assert before["points"] == SEASON_TABLE


async def test_a_table_repaired_after_the_panel_was_drawn_is_approved(tmp_path):
    """The panel was drawn while P2 was staged above P1; the table is repaired before the press,
    and the press approves it (the check reads at the press, not the panel's reading)."""
    league = await points_league(tmp_path, staged={1: 26, 2: 30})
    panel = await open_panel(league)
    assert "out of order" in panel.text
    await run_staging(league, "session", position=2, points=18)

    press = await press_approve(league, panel)
    await run_queue(league.bot)

    assert acknowledgement(press).startswith("⏳ Approving season 1's points amendment.")
    assert _replies(press).endswith(SUCCESS)
    assert (await points_state(league))["points"] == APPROVED_TABLE


@pytest.mark.parametrize("state", ["waiting", "names", "post", "close"])
async def test_a_second_press_while_the_approval_is_in_hand_is_refused_at_once_naming_its_job(
    tmp_path, state,
):
    """Owner, "Refuse at once": a second Approve while the first is waiting, stopped before its
    save or stopped after it is refused at once, naming the job the first waits on, or naming
    none where only the first's close is left; the first finishes as it would have."""
    league = await points_league(tmp_path)
    second = await open_panel(league)
    with ExitStack() as stack:
        clear = await _approval_in_hand(league, state, stack)
        job = await _waits_on(league)

        refused = await press_approve(league, second)

        assert acknowledgement(refused) == _naming(IN_HAND, job)
        assert len(_refusal_lines(league)) == 1
        assert len(await _approvals(league)) == 1

        await clear()

    assert len(_success_lines(league)) == 1
    assert (await points_state(league))["points"] == APPROVED_TABLE


_STAGING = [
    ("toggle", {}),
    ("revert", {}),
    ("session", {"position": 1, "points": 27}),
    ("fl", {"points": 2}),
    ("fl-plimit", {"limit": 5}),
    ("bulk-session", {}),
    ("bulk-session-form", {"entries": "1, 27"}),
]


@pytest.mark.parametrize("state", ["waiting", "names", "post", "close"])
@pytest.mark.parametrize("command, args", _STAGING, ids=[c for c, _ in _STAGING])
async def test_the_staging_commands_are_refused_while_the_approval_is_in_hand(
    tmp_path, command, args, state,
):
    """Owner, "Refuse all six": while the approval is waiting, stopped before its save, stopped
    after it or left with only its close, each staging command (toggle turning the mode off before the save and on
    after it; bulk-session before its form, and its form submitted after the press) is refused
    naming the job (none where only the close is left), writing nothing; no form is shown where
    the command is refused."""
    league = await points_league(tmp_path)
    with ExitStack() as stack:
        await _approval_in_hand(league, state, stack)
        job = await _waits_on(league)
        before = await points_state(league)

        staged = await run_staging(league, command, **args)

        assert staged.reply == _naming(STAGING_REFUSAL, job)
        named = "bulk-session" if command == "bulk-session-form" else command
        assert len(_refusal_lines(league, f"results amend {named}")) == 1
        assert staged.modal is None
        assert await points_state(league) == before


async def test_the_staging_commands_run_again_once_the_approval_is_done_or_discarded(tmp_path):
    """Once the approval is done, `toggle` turns amendment mode on again; once a league admin
    discards its save, `session` stages again. Each is read from what the reply says once the
    change has run, after any acknowledgement."""
    for name in ("done", "discarded"):
        (tmp_path / name).mkdir()
    done = await points_league(tmp_path / "done")
    await press_approve(done)
    await run_queue(done.bot)
    toggled = await run_staging(done, "toggle")
    assert last_said(toggled.interaction).startswith("✅ Amendment mode enabled.")

    discarded = await points_league(tmp_path / "discarded")
    with _standings_fail():
        await press_approve(discarded)
        await run_queue(discarded.bot)
    assert await stopped_at(discarded) == "apply"
    await discard_job(discarded.bot)
    staged = await run_staging(discarded, "session", position=1, points=27)
    assert last_said(staged.interaction).startswith("✅ Updated in modification store")


# ---------------------------------------------------------------------------
# What it does
# ---------------------------------------------------------------------------

ACKNOWLEDGED = (
    "⏳ Approving season 1's points amendment. This message will be updated when it is done; "
    "if it takes longer, the log channel will say so. It begins with job #{job}."
)
APPROVED_BUT = (
    "⚠️ Amendment approved: the new points are in force and every round is rescored, but some "
    "of it could not be done:"
)
BOTH_SYNCS = "Repair the cause, then run `/results rounds sync` and `/results standings sync`."


def _incomplete_lines(league: Any) -> list[str]:
    return [line for line in league.bot.log_channel.sent
            if "/results amend review | Incomplete" in line]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    """The approval's jobs in order, each payload read back from JSON."""
    rows = await step_rows(league.db_path, (await _approvals(league))[0]["id"])
    for row in rows:
        row["payload"] = json.loads(row["payload"] or "{}")
    return rows


async def _saved(league: Any) -> dict[str, Any]:
    """What the approval's save writes besides the points: every session's points and every
    standings snapshot."""
    from leaguebot.framework.db.database import get_connection

    async with get_connection(league.db_path) as db:
        async def rows(sql: str) -> list[tuple[Any, ...]]:
            return [tuple(r) for r in await (await db.execute(sql)).fetchall()]

        return {
            "race": await rows(
                "SELECT session_result_id, driver_user_id, points_awarded "
                "FROM race_session_results ORDER BY session_result_id, driver_user_id"
            ),
            "standings": await rows(
                "SELECT round_id, driver_user_id, standing_position, total_points "
                "FROM driver_standings_snapshots ORDER BY round_id, driver_user_id"
            ),
        }


async def test_the_admin_is_told_at_once_and_the_reply_is_updated_when_every_round_is_reposted(
    tmp_path,
):
    """The press is acknowledged at once, naming the approval's first job; once every job is
    done, the acknowledgement is updated with today's success text."""
    league = await points_league(tmp_path)

    press = await press_approve(league)
    first = (await _jobs(league))[0]["id"]

    assert acknowledgement(press) == ACKNOWLEDGED.format(job=first)
    assert updated_reply(press) == ""

    await run_queue(league.bot)

    assert updated_reply(press) == SUCCESS


async def test_each_post_and_each_deletion_is_a_job_of_its_own(tmp_path):
    """The names are looked up a division at a time, then the save; then each results table,
    each standings and each deletion of an old message is a job of its own, Pro's before Am's,
    each old message deleted after the post that replaces it; the close comes last."""
    league = await points_league(tmp_path)

    await press_approve(league)
    await run_queue(league.bot)

    jobs = await _jobs(league)
    names = [job["name"] for job in jobs]
    assert names[:4] == ["plan_names", "names", "names", "apply"]
    assert names[-1] == "close"
    assert names.count("post_session_results") == 6
    assert names.count("post_standings") == 6
    assert names.count("delete_message") == 12
    posts = [(i, job) for i, job in enumerate(jobs)
             if job["name"] in ("post_session_results", "post_standings")]
    divisions = [job["payload"]["division"] for _, job in posts]
    assert divisions == ["Pro"] * 6 + ["Am"] * 6
    for division_id, name, cid in ((PRO, "Pro", PRO_RESULTS), (AM, "Am", AM_RESULTS)):
        for number in (1, 2, 3):
            posted_at = next(
                i for i, job in posts
                if job["name"] == "post_session_results"
                and job["payload"]["division"] == name
                and job["payload"]["round_number"] == number
            )
            deleted_at = next(
                i for i, job in enumerate(jobs)
                if job["name"] == "delete_message"
                and job["payload"]["message_id"] == old_results(division_id, number)
            )
            assert jobs[deleted_at]["payload"]["channel_id"] == cid
            assert deleted_at > posted_at


async def test_the_points_rescoring_standings_and_attendance_are_saved_in_one_save(tmp_path):
    """Attendance on; its recalculation raises inside the save. Nothing of the four is saved:
    the season's points, working copy and mode, every session's points and every standings
    snapshot stand as they were, and the queue is stopped at the save."""
    league = await points_league(tmp_path, attendance=True)
    league.attendance.recalculate_fails = RuntimeError("the attendance could not be recalculated")
    before, saved = await points_state(league), await _saved(league)

    await press_approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert any(call[0] == "recalculate_on" for call in league.attendance.calls)
    assert await points_state(league) == before
    assert await _saved(league) == saved
    assert await race_points(league, PRO, 1) == {LEWIS: 25, MAX: 18}


async def test_the_approval_is_audited_with_every_value_changed(tmp_path):
    """One `POINTS_AMENDMENT_APPROVED` record, by the admin who pressed, holding the season and
    the one value changed (Feature Race P1), before (25) and after (26)."""
    league = await points_league(tmp_path)

    await press_approve(league)
    await run_queue(league.bot)

    rows = await audit_rows(league, "POINTS_AMENDMENT_APPROVED")
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_ID
    old, new = json.loads(rows[0]["old_value"]), json.loads(rows[0]["new_value"])
    assert old["season_id"] == new["season_id"] == 1
    assert len(old["changed"]) == len(new["changed"]) == 1
    assert "25" in json.dumps(old["changed"]) and "26" not in json.dumps(old["changed"])
    assert "26" in json.dumps(new["changed"]) and "25" not in json.dumps(new["changed"])


async def test_one_success_line_names_the_admin_and_the_values_set_after_the_last_job(tmp_path):
    """One success line, written once the last post is sent, names the admin, the season, each
    value set and the rounds reposted; the old `AMENDMENT_APPROVED` line and "standings
    recomputed and reposted" are gone."""
    league = await points_league(tmp_path)
    seen_at_send: list[int] = []

    def _count(_content: str, _kwargs: dict[str, Any]) -> bool:
        seen_at_send.append(len(_success_lines(league)))
        return False

    league.channel(AM_STANDINGS).fail_when = _count

    await press_approve(league)
    await run_queue(league.bot)

    lines = _success_lines(league)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith(f"Admin (`<@{ADMIN_ID}>`) | /results amend review | Success")
    assert "  season: 1" in line
    assert "  points changed: Standard, Feature Race, P1: 25 → 26" in line
    assert "  rounds rescored and reposted: 6 across 2 division(s)" in line
    assert seen_at_send and set(seen_at_send) == {0}
    assert "AMENDMENT_APPROVED" not in league.log()
    assert "standings recomputed and reposted" not in league.log()


async def test_only_raced_rounds_are_reposted_each_under_its_own_label(tmp_path):
    """Pro's rounds 1 and 2 (final) and 3 (awaiting its report verdicts) are reposted, each under
    its own label; round 4, not run, and round 5, recorded as cancelled, post nothing."""
    league = await points_league(tmp_path)
    await write(
        league,
        "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, track_name, "
        "status) VALUES (?, ?, 5, '2026-07-01T18:00:00+00:00', 'NORMAL', 'Silverstone', "
        "'CANCELLED')",
        round_id(PRO, 5), PRO,
    )

    await press_approve(league)
    await run_queue(league.bot)

    channel = league.channel(PRO_RESULTS)
    posted = [channel.messages[mid].content for mid in league.sent_to(PRO_RESULTS)]
    assert len(posted) == 3
    assert "Final Results" in posted[0] and "Final Results" in posted[1]
    assert "Provisional Results" in posted[2]
    assert len(league.sent_to(PRO_STANDINGS)) == 3
    assert all(job["payload"].get("round_number") in (None, 1, 2, 3)
               for job in await _jobs(league))


async def test_every_division_is_reposted_a_cancelled_one_included(tmp_path):
    """Beta, cancelled, has its raced round 1 reposted in its own channels (results 721,
    standings 720), after Am's, and its old messages deleted."""
    league = await points_league(tmp_path, cancelled_division=True)

    press = await press_approve(league)
    await run_queue(league.bot)

    assert len(league.sent_to(BETA_RESULTS)) == 1
    assert len(league.sent_to(BETA_STANDINGS)) == 1
    assert old_results(BETA, 1) in league.deleted_from(BETA_RESULTS)
    assert old_standings(BETA, 1) in league.deleted_from(BETA_STANDINGS)
    sends = [(i, cid) for i, (kind, cid, _mid) in enumerate(league.events) if kind == "send"]
    last_am = max(i for i, cid in sends if cid in (AM_RESULTS, AM_STANDINGS))
    first_beta = min(i for i, cid in sends if cid in (BETA_RESULTS, BETA_STANDINGS))
    assert first_beta > last_am
    assert updated_reply(press) == SUCCESS


async def test_a_discarded_repost_is_named_incomplete_with_its_division_and_both_sync_commands(
    tmp_path,
):
    """Pro's round 2 Feature Race results are refused; a league admin discards the job. The
    queue runs on, the old table stands, and the reply and the `| Incomplete` line name the
    table and its division, ending with both sync commands; no success line."""
    league = await points_league(tmp_path)
    _refuse_send(league, PRO_RESULTS, 2)

    press = await press_approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"
    await discard_job(league.bot)

    named = "⚠️ Round 2's Feature Race results in Pro were not posted."
    reply = updated_reply(press)
    assert APPROVED_BUT in reply
    assert named in reply
    assert reply.endswith(BOTH_SYNCS)
    lines = _incomplete_lines(league)
    assert len(lines) == 1
    assert named in lines[0] and BOTH_SYNCS in lines[0]
    assert _success_lines(league) == []
    assert old_results(PRO, 2) not in league.deleted_from(PRO_RESULTS)
    assert old_results(PRO, 2) in league.channel(PRO_RESULTS).messages
    assert await stopped_job(league.db_path) is None


async def test_a_discarded_deletion_links_the_old_table_for_deletion_by_hand(tmp_path):
    """Pro's old round 1 results table cannot be deleted; a league admin discards the job. Both
    tables stand, and the reply and the `| Incomplete` line name the old one for deletion by
    hand."""
    league = await points_league(tmp_path)
    old = old_results(PRO, 1)
    league.channel(PRO_RESULTS).messages[old].delete = AsyncMock(
        side_effect=http_error(status=403, text="Missing Permissions")
    )

    press = await press_approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "delete_message"
    await discard_job(league.bot)

    reply = updated_reply(press)
    assert APPROVED_BUT in reply
    named = [line for line in reply.split("\n")
             if line.startswith("⚠️ An earlier message could not be deleted (")]
    assert len(named) == 1
    assert named[0].endswith("delete it by hand.")
    assert str(old) in named[0] or f"<#{PRO_RESULTS}>" in named[0]
    assert named[0] in _incomplete_lines(league)[0]
    assert old in league.channel(PRO_RESULTS).messages
    assert len(league.sent_to(PRO_RESULTS)) == 3


async def test_a_discarded_save_says_nothing_was_changed_and_leaves_amendment_mode_on(tmp_path):
    """The save fails (the standings cannot be computed) and a league admin discards it: the
    reply says nothing was changed, the season keeps its points, the staged changes and
    amendment mode, nothing is posted, and no success or `Incomplete` line is written."""
    league = await points_league(tmp_path)
    before = await points_state(league)

    with _standings_fail():
        press = await press_approve(league)
        await run_queue(league.bot)
        assert await stopped_at(league) == "apply"
        await discard_job(league.bot)

    assert updated_reply(press).endswith(NOTHING_CHANGED)
    assert await points_state(league) == before
    assert before["mode"][0][0] == 1
    assert league.sent_to(PRO_RESULTS) == league.sent_to(PRO_STANDINGS) == []
    assert _success_lines(league) == [] and _incomplete_lines(league) == []


async def test_a_discarded_names_job_changes_nothing_and_the_save_is_never_made(tmp_path):
    """A `names` job fails and a league admin discards it: the save is not due, so the reply
    says nothing was changed, the season keeps its points, the staged changes and amendment
    mode, no session is rescored, nothing is posted or audited, and no success or
    `Incomplete` line is written; the queue runs on."""
    league = await points_league(tmp_path)
    before, saved = await points_state(league), await _saved(league)

    with _names_fail_once():
        press = await press_approve(league)
        await run_queue(league.bot)
        assert await stopped_at(league) == "names"
        await discard_job(league.bot)

    assert updated_reply(press).endswith(NOTHING_CHANGED)
    assert await stopped_job(league.db_path) is None
    assert await points_state(league) == before
    assert before["mode"][0][0] == 1
    assert await _saved(league) == saved
    assert await audit_rows(league, "POINTS_AMENDMENT_APPROVED") == []
    assert all(league.sent_to(cid) == []
               for cid in (PRO_RESULTS, PRO_STANDINGS, AM_RESULTS, AM_STANDINGS))
    assert _success_lines(league) == [] and _incomplete_lines(league) == []


async def test_a_stop_after_the_save_finishes_the_reposts_on_restart_and_records_one_success(
    tmp_path,
):
    """The bot stops just after the save and a first post; restarted, the queue finishes the
    reposts: each round posted once, every old table deleted once, one success line, one audit
    record, and the points applied once."""
    league = await points_league(tmp_path)
    press = await press_approve(league)
    await run_until_done(league, "apply")
    await run_queue(league.bot, steps=1)

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    for division_id, results in ((PRO, PRO_RESULTS), (AM, AM_RESULTS)):
        assert len(league.sent_to(results)) == 3
        assert sorted(league.deleted_from(results)) == [
            old_results(division_id, n) for n in (1, 2, 3)
        ]
    assert len(_success_lines(league)) == 1
    assert len(await audit_rows(league, "POINTS_AMENDMENT_APPROVED")) == 1
    assert (await points_state(league))["points"] == APPROVED_TABLE
    assert await race_points(league, PRO, 1) == {LEWIS: 26, MAX: 18}
    assert acknowledgement(press).startswith("⏳ Approving season 1's points amendment.")


async def test_a_stop_before_the_save_applies_the_points_once_on_restart(tmp_path):
    """The bot stops after the first `names` job, before the save; restarted, the queue applies
    the points once: the season's table, every session rescored, amendment mode off, one audit
    record and one success line."""
    league = await points_league(tmp_path)
    await press_approve(league)
    await run_until_done(league, "names")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    state = await points_state(league)
    assert state["points"] == APPROVED_TABLE
    assert state["mode"][0][0] == 0
    assert await race_points(league, PRO, 1) == {LEWIS: 26, MAX: 18}
    assert await race_points(league, AM, 3) == {LEWIS: 26, MAX: 18}
    assert len(await audit_rows(league, "POINTS_AMENDMENT_APPROVED")) == 1
    assert len(_success_lines(league)) == 1


# ---------------------------------------------------------------------------
# Attendance (owner, "As a round amendment")
# ---------------------------------------------------------------------------

#: The round each division's attendance is recalculated and its sanctions fall due at: its
#: latest round awaiting appeals or final, round 2 (round 3 awaits its report verdicts).
LATEST = 2
SANCTIONS_UNWORKED = (
    "⚠️ The attendance sanctions of Pro were not worked out, so none was applied. Repair the "
    f"cause, then run `/attendance sync division:Pro round:{LATEST}`."
)


async def _attendance_league(tmp_path: Any, *owed: dict[str, Any]) -> Any:
    """Pro alone, attendance on, with *owed* the drivers over a threshold."""
    league = await points_league(tmp_path, attendance=True, other_division=False)
    league.attendance.candidates = list(owed)
    return league


def _headed_by(league: Any) -> list[int]:
    """Make the double's card announcement record the round whose banner heads it."""
    headed: list[int] = []
    recorded = league.attendance.announce_sanction

    async def _announce(rid: int, division_id: int, owed: dict[str, Any], *,
                        as_text: bool) -> None:
        headed.append(rid)
        await recorded(rid, division_id, owed, as_text=as_text)

    league.attendance.announce_sanction = _announce
    return headed


async def test_each_division_s_sheet_and_sanctions_follow_its_reposts_at_its_latest_approved_round(
    tmp_path,
):
    """Attendance on, Pro and Am. The save recalculates each division's attendance at its round
    2; each division's sheet, and the job working out its sanctions, come after that division's
    last repost, at round 2."""
    league = await points_league(tmp_path, attendance=True)

    press = await press_approve(league)
    await run_queue(league.bot)

    recalculated = [call for call in league.attendance.calls if call[0] == "recalculate_on"]
    assert recalculated == [("recalculate_on", round_id(PRO, LATEST), PRO),
                            ("recalculate_on", round_id(AM, LATEST), AM)]
    assert [call for call in league.attendance.calls if call[0] == "post_sheet"] == [
        ("post_sheet", PRO, False), ("post_sheet", AM, False),
    ]
    jobs = await _jobs(league)
    for division_id, name in ((PRO, "Pro"), (AM, "Am")):
        last_post = max(
            i for i, job in enumerate(jobs)
            if job["name"] in ("post_session_results", "post_standings")
            and job["payload"]["division"] == name
        )
        for job_name in ("attendance_sheet", "plan_sanctions"):
            at = next(i for i, job in enumerate(jobs)
                      if job["name"] == job_name and job["payload"]["division_id"] == division_id)
            assert at > last_post
            assert jobs[at]["payload"]["round_id"] == round_id(division_id, LATEST)
    assert updated_reply(press) == SUCCESS


async def test_a_sanction_that_does_not_apply_stops_the_queue_and_once_discarded_is_named_with_attendance_sync(
    tmp_path,
):
    """Max and Lewis are owed an autoreserve in Pro; Max's cannot be applied. The queue stops at
    it, Lewis's waiting; a league admin discards it, Lewis's applies, and the reply and the
    `| Incomplete` line name Max's sanction with the `/attendance sync` command."""
    league = await _attendance_league(
        tmp_path, candidate(MAX_PROFILE, MAX), candidate(LEWIS_PROFILE, LEWIS),
    )
    league.attendance.apply_fails[MAX_PROFILE] = http_error(status=403, text="Missing Access")

    press = await press_approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "apply_sanction"
    assert ("apply_sanction", LEWIS_PROFILE) not in league.attendance.calls
    await discard_job(league.bot)

    assert ("apply_sanction", LEWIS_PROFILE) in league.attendance.calls
    assert league.attendance.applied == {LEWIS_PROFILE}
    named = (
        f"⚠️ The autoreserve of <@{MAX}> was not applied. Repair the cause, then run "
        f"`/attendance sync division:Pro round:{LATEST}`."
    )
    reply = updated_reply(press)
    assert APPROVED_BUT in reply
    assert named in reply
    lines = _incomplete_lines(league)
    assert len(lines) == 1 and named.replace(f"<@{MAX}>", f"`<@{MAX}>`") in lines[0]
    assert _success_lines(league) == []


async def test_a_clean_sanctions_run_adds_nothing_to_the_reply(tmp_path):
    """Max is owed an autoreserve in Pro and it applies and is announced: the reply is today's
    success text alone, and one success line is written, no `Incomplete`."""
    league = await _attendance_league(tmp_path, candidate(MAX_PROFILE, MAX))

    press = await press_approve(league)
    await run_queue(league.bot)

    assert league.attendance.applied == {MAX_PROFILE}
    assert ("announce_sanction", MAX_PROFILE) in league.attendance.calls
    assert updated_reply(press) == SUCCESS
    assert len(_success_lines(league)) == 1
    assert _incomplete_lines(league) == []


async def test_attendance_off_posts_no_sheet_and_applies_no_sanction(tmp_path):
    """Attendance off, Max over a threshold as the double sees it: no sheet is posted, no
    sanction applied or announced, and the approval succeeds."""
    league = await points_league(tmp_path, other_division=False)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]

    press = await press_approve(league)
    await run_queue(league.bot)

    called = {call[0] for call in league.attendance.calls}
    assert not called & {"post_sheet", "apply_sanction", "announce_sanction", "refresh_lineup"}
    assert league.attendance.applied == set()
    assert updated_reply(press) == SUCCESS


async def test_the_sanction_cards_go_beneath_the_round_s_recorded_banner(tmp_path):
    """A banner is recorded for Pro's round 2, where Max's autoreserve falls due: his card goes
    beneath it, no heading is planned and the banner stands."""
    league = await _attendance_league(tmp_path, candidate(MAX_PROFILE, MAX))
    banner = 8990
    await write(
        league,
        "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at, "
        "heads_sanctions) VALUES (?, ?, ?, ?, 0)",
        round_id(PRO, LATEST), str(PRO_VERDICTS), str(banner), NOW.isoformat(),
    )
    league.channel(PRO_VERDICTS).seed(banner, "a banner")
    headed = _headed_by(league)

    press = await press_approve(league)
    await run_queue(league.bot)

    assert headed == [round_id(PRO, LATEST)]
    assert "announce_heading" not in [job["name"] for job in await _jobs(league)]
    assert banner in league.channel(PRO_VERDICTS).messages
    assert league.sent_to(PRO_VERDICTS) == []
    assert updated_reply(press) == SUCCESS


async def test_the_sanction_cards_raise_a_heading_only_where_the_round_has_none_and_it_stops_the_queue(
    tmp_path,
):
    """No banner is recorded for Pro's round 2. One heading is planned, ahead of Max's card; the
    verdicts channel refuses it once, which stops the queue with the card waiting. A league
    admin discards it: the card goes out beneath none, and the reply names the heading."""
    league = await _attendance_league(tmp_path, candidate(MAX_PROFILE, MAX))
    _refuse_send(league, PRO_VERDICTS, 1)

    press = await press_approve(league)
    await run_queue(league.bot)

    jobs = await _jobs(league)
    names = [job["name"] for job in jobs]
    assert names.count("announce_heading") == 1
    assert names.index("announce_heading") < names.index("announce_sanction")
    assert await stopped_at(league) == "announce_heading"
    assert ("announce_sanction", MAX_PROFILE) not in league.attendance.calls

    await discard_job(league.bot)

    assert ("announce_sanction", MAX_PROFILE) in league.attendance.calls
    named = (
        f"⚠️ The heading over round {LATEST}'s attendance sanctions was not posted: their cards "
        "stand beneath none."
    )
    assert named in updated_reply(press)
    assert named in _incomplete_lines(league)[0]


async def test_a_discarded_sanctions_plan_makes_the_approval_incomplete_and_names_attendance_sync(
    tmp_path,
):
    """Citation c1: the drivers owed a sanction cannot be read, so the queue stops at the job
    working them out; a league admin discards it. The reply and the `| Incomplete` line name it
    with `/attendance sync`; no success line, and no sanction applied."""
    league = await _attendance_league(tmp_path, candidate(MAX_PROFILE, MAX))
    league.attendance.candidates_fail = RuntimeError("the drivers owed could not be read")

    press = await press_approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "plan_sanctions"
    await discard_job(league.bot)

    reply = updated_reply(press)
    assert APPROVED_BUT in reply
    assert SANCTIONS_UNWORKED in reply
    lines = _incomplete_lines(league)
    assert len(lines) == 1 and SANCTIONS_UNWORKED in lines[0]
    assert _success_lines(league) == []
    assert league.attendance.applied == set()


# ---------------------------------------------------------------------------
# What an approval saves (#130, #185)
# ---------------------------------------------------------------------------

#: Who staged each change, and when: the modification store records each change it stages (#442).
ACTOR = {
    "actor_id": 99,
    "actor_name": "Admin#0001",
    "now": datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc),
}


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "amend_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        await db.commit()
    return path, season_id


async def _seed_season_points(db_path: str, season_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, position, points) "
            "VALUES (?, 'STD', 'FEATURE_RACE', 1, 25)",
            (season_id,),
        )
        await db.commit()


async def _seed_division_with_rounds(path: str, season_id: int):
    """One division with two raced rounds and a third not yet run.

    Returns ``(division_id, raced_round_ids, unraced_round_id)``.
    """
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, 501, 502)",
            (division_id,),
        )
        raced: list[int] = []
        for round_number in (1, 2):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
                "VALUES (?, ?, 'NORMAL', 'FINAL', '2026-06-01T18:00:00+00:00')",
                (division_id, round_number),
            )
            round_id = cursor.lastrowid
            raced.append(round_id)
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (round_id, division_id),
            )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 3, 'NORMAL', 'NOT_RUN', '2026-09-01T18:00:00+00:00')",
            (division_id,),
        )
        unraced_round_id = cursor.lastrowid
        await db.commit()

    return division_id, raced, unraced_round_id


#: The stub bot's own Discord user id. The pre-flight looks its member up by it (#187).
_BOT_USER_ID = 4242

#: "Now", for the queue that carries the approval.
_APPROVE_NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

_message_ids = itertools.count(4300)


async def _bot_recording_reposts(path: str, reposted: list[tuple], *,
                                 missing: tuple[int, ...] = (), attendance_hook_of=None):
    """A bot double whose guild is real enough for the approval's posting jobs to run.

    Built on the change queue's `league_bot` (#439, slice 3; #283): a real output router, so every
    line the approval writes lands in `bot.log_channel`, whose `sent` records it. Its guild's
    channels record each send in *reposted* as ``(channel_id, content)``, and each message they
    send can be fetched and deleted again, as the jobs replacing an old message do.

    *missing* names channels the guild no longer holds, for the refusal tests: a deleted
    channel is what ``guild.get_channel`` answers None to.

    *attendance_hook_of*, where given, makes attendance's hook, as `league_bot` takes it; else the
    hook is `league_bot`'s double, as a league with attendance off sees it.

    The channels are specced as ``discord.TextChannel`` because the approval checks that what a
    division points at is something that can be posted in (#187). A bare ``AsyncMock`` is not,
    and would be refused before any reposting was attempted.

    **A bare ``MagicMock`` grants every permission.** The validation reads permissions with
    ``getattr(permissions, name, False)``, and a ``MagicMock`` answers any attribute with a
    truthy child mock — so the object handed back by ``permissions_for`` below means
    "everything allowed", which is what these tests want: they are about the cascade, not
    about permissions.

    The trap is in the other direction. A test that means to *deny* a permission must set
    that attribute to ``False`` by name, and a **misspelt** attribute silently grants
    instead of denying — leaving a test that reads as though it proves a refusal while
    actually exercising the success path. Set the attribute, then assert on the fault text,
    so the test fails if the denial never took.
    """
    import discord
    from unittest.mock import AsyncMock, MagicMock

    from tests.support.change_queue import SERVER_ID, league_bot

    bot = await league_bot(path, attendance_hook_of=attendance_hook_of)
    guild = MagicMock()
    guild.id = SERVER_ID
    # The bot's own member resolves — the pre-flight reads its permissions through it — and
    # nobody else's does, which is what makes the postings below fall back to plain ids for
    # the drivers. Two different questions asked of one cache (#187).
    guild.get_member = lambda user_id: MagicMock() if user_id == _BOT_USER_ID else None
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )
    channels: dict[int, object] = {}

    def _channel(channel_id):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.mention = f"<#{channel_id}>"
        channel.guild = guild
        messages: dict[int, object] = {}

        def _message(message_id):
            message = MagicMock()
            message.id = message_id
            message.channel = channel
            message.jump_url = f"https://discord.test/{channel_id}/{message_id}"

            async def _delete(*_args, **_kwargs):
                messages.pop(message_id, None)

            message.delete = AsyncMock(side_effect=_delete)
            message.edit = AsyncMock(return_value=message)
            return message

        async def fake_send(content=None, **kwargs):
            reposted.append((channel_id, content or ""))
            message = _message(next(_message_ids))
            message.content = content or ""
            messages[message.id] = message
            return message

        async def fetch_message(message_id):
            if message_id not in messages:
                raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Message")
            return messages[message_id]

        channel.send = AsyncMock(side_effect=fake_send)
        channel.fetch_message = AsyncMock(side_effect=fetch_message)
        channel.get_partial_message = MagicMock(
            side_effect=lambda message_id: messages.get(message_id) or _message(message_id)
        )
        # Everything granted: these tests are about the cascade, not about permissions.
        channel.permissions_for.return_value = MagicMock()
        return channel

    def get_channel(channel_id):
        if channel_id in missing:
            return None
        if channel_id not in channels:
            channels[channel_id] = _channel(channel_id)
        return channels[channel_id]

    guild.get_channel = get_channel

    bot.user.id = _BOT_USER_ID
    bot.get_guild = MagicMock(return_value=guild)
    answer_switch(bot.module_service, "attendance", False)
    answer_switch(bot.module_service, "images", False)
    bot.image_config_service.get_toggles = AsyncMock(return_value={})
    return bot


async def _approve(path: str, season_id: int, bot):
    """Press Approve on the season's points amendment, as user 99, and run the queue.

    A queue is attached on the test's database, results turned on and the season ongoing, as
    a league using amendments has them, and the approval asked for through a league admin's press, as
    `/results amend review`'s Approve button asks it (#439, slice 3). Returns the press, whose
    reply is the queue's acknowledgement, or the refusal the change's check gave at once.
    """
    from leaguebot.results.services.points.points_amendment_change import KIND
    from tests.support.change_queue import league_queue, member_interaction, run_queue, tier_member

    async with get_connection(path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        # The season is being raced: the approval's check refuses a season with no stage.
        await db.execute(
            "UPDATE seasons SET stage = 'ONGOING' WHERE id = ? AND stage IS NULL", (season_id,)
        )
        cursor = await db.execute(
            "SELECT season_number FROM seasons WHERE id = ?", (season_id,)
        )
        season_number = (await cursor.fetchone())["season_number"]
        await db.commit()
    league_queue(bot, path, now=_APPROVE_NOW)
    press = member_interaction(bot, user=tier_member("admin", member_id=99))
    await bot.change_queue.ask(
        KIND, {"season_id": season_id, "season_number": season_number},
        interaction=press, what="`/results amend review`",
    )
    await run_queue(bot)
    return press


async def test_approve_amendment_still_overwrites_the_points(tmp_path):
    """P1 staged at 26 over the season's 25 and approved: the season's table pays 26, and
    amendment mode is off with nothing marked as modified."""
    league = await points_league(tmp_path)

    await press_approve(league)
    await run_queue(league.bot)

    state = await points_state(league)
    assert state["points"] == APPROVED_TABLE
    assert state["mode"] == [(0, 0)]


async def test_approve_amendment_overwrites_the_fastest_lap_points(tmp_path):
    """The fastest lap bonus, staged at 3 over the season's 1, is overwritten by the same save as
    the points.

    Issue #185. The test that claimed to cover this transaction simulated it by hand and
    knew only about `season_points_entries` — so the whole `season_points_fl` half could
    have been dropped from `approve_amendment` and nothing would have failed. A league
    amending the bonus would have seen the panel accept the change and the old bonus keep
    being awarded.
    """
    league = await points_league(tmp_path)
    await modify_fl_bonus(league.db_path, 1, "Standard", "FEATURE_RACE", 3)

    await press_approve(league)
    await run_queue(league.bot)

    assert (await points_state(league))["fl"] == [("Standard", "FEATURE_RACE", 3, 10)]


async def test_an_approved_amendment_empties_the_modification_store(tmp_path):
    """P1 and the fastest lap bonus staged and approved: both staging tables are emptied, so the
    next amendment starts from the season.

    A store left behind is what a second amendment would build on: reopening amendment
    mode would show the previous amendment's figures as though they were pending changes,
    and approving it would rewrite the season with them.
    """
    league = await points_league(tmp_path)
    await modify_fl_bonus(league.db_path, 1, "Standard", "FEATURE_RACE", 3)

    await press_approve(league)
    await run_queue(league.bot)

    state = await points_state(league)
    assert state["staged"] == [], "season_modification_entries was left behind"
    assert state["staged_fl"] == [], "season_modification_fl was left behind"


# ---------------------------------------------------------------------------
# An amendment that could not be published is refused entire (#187)
#
# Either everything succeeds or everything fails. The approval used to overwrite the
# season's points first and discover afterwards that it could not repost them, swallow
# that, and tell the league the championship had been republished.
# ---------------------------------------------------------------------------


async def _staged_amendment(path: str, season_id: int) -> None:
    """Amendment mode on, with one sound change staged and ready to approve."""
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )


async def _season_state(path: str, season_id: int):
    """The three things a refusal must leave exactly as it found them."""
    async with get_connection(path) as db:
        points = await (
            await db.execute(
                "SELECT position, points FROM season_points_entries WHERE season_id = ? "
                "ORDER BY position",
                (season_id,),
            )
        ).fetchall()
        staged = await (
            await db.execute(
                "SELECT position, points FROM season_modification_entries WHERE season_id = ? "
                "ORDER BY position",
                (season_id,),
            )
        ).fetchall()
    state = await get_amendment_state(path, season_id)
    return (
        [(r["position"], r["points"]) for r in points],
        [(r["position"], r["points"]) for r in staged],
        state,
    )


@pytest.mark.xfail(strict=True, reason="#513: a refused Approve tells the admin to repair the "
                   "channels whatever the fault")
async def test_an_amendment_is_refused_when_the_guild_is_not_in_cache(tmp_path):
    """The bot is no longer in the league's server when Approve is pressed: the press is refused
    saying so, nothing is queued and the season stands as it was (#187). It used to overwrite
    the points and then silently repost nothing at all. No channel is at fault, so the reply
    does not tell the admin to repair the channels (#513)."""
    league = await points_league(tmp_path)
    panel = await open_panel(league)
    before = await points_state(league)
    league.bot.get_guild.return_value = None

    press = await press_approve(league, panel)

    reply = acknowledgement(press)
    assert reply.startswith(NOT_PUBLISHED)
    assert "not in this server" in reply
    assert "Repair the channels" not in reply
    assert await _approvals(league) == []
    assert await points_state(league) == before


@pytest.mark.xfail(strict=True, reason="#513: a season that cannot be read is refused as a "
                   "publishing fault, saying twice that nothing changed and to repair the channels")
async def test_an_amendment_is_refused_when_the_season_cannot_be_read(tmp_path):
    """The panel is drawn while the season stands; then the approval's check finds the season
    cannot be read (stubbed where `approval_faults` asks for it: the season itself stays live, as
    a real removal is refused earlier, at the live-season read), and Approve is pressed. The press
    is refused under a heading naming the season, not publishing, saying once that nothing has been
    changed and naming no channel repair; one ⛔ line is logged with no detail beneath it, nothing
    is queued, and the season's points, the staged changes and amendment mode stand as they
    were."""
    import re

    league = await points_league(tmp_path)
    panel = await open_panel(league)
    before = await points_state(league)

    with patch("leaguebot.results.services.points.points_amendment_faults.season_exists",
               new=AsyncMock(return_value=False)):
        press = await press_approve(league, panel)

    reply = acknowledgement(press)
    assert reply.startswith("⛔ Amendment not approved — the season could not be read.")
    assert "could not be published" not in reply
    assert len(re.findall(r"nothing (?:was|has been) changed", reply, re.IGNORECASE)) == 1
    assert "**Nothing has been changed**" in reply
    assert "Repair the channels" not in reply
    [line] = _refusal_lines(league)
    assert "\n" not in line
    assert await _approvals(league) == []
    assert await points_state(league) == before


@pytest.mark.asyncio
async def test_a_division_with_no_channels_does_not_refuse_the_amendment(db_path):
    """The guard against over-refusing: an unconfigured channel is ordinary (#187)."""
    path, season_id = db_path
    await _seed_season_points(path, season_id)
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, 'Alpha', 777)",
            (season_id,),
        )
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, NULL, NULL)",
            (cursor.lastrowid,),
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    await _approve(path, season_id, await _bot_recording_reposts(path, []))

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30, "a sound amendment was refused"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unreadable",
    [pytest.param(False, id="module off"), pytest.param(True, id="module's state unreadable")],
)
async def test_the_attendance_channels_are_not_checked_while_the_module_is_off(db_path, unreadable):
    """A league without the attendance module must not be refused for a channel it has
    never configured — the same gate the cascade's own recalculation holds to. Nor is one whose
    attendance switch cannot be read (the database is locked): the reader stands aside, so it is
    never the thing that refuses an amendment."""
    from unittest.mock import AsyncMock

    path, season_id = db_path
    await _seed_season_points(path, season_id)
    division_id, _raced, _unraced = await _seed_division_with_rounds(path, season_id)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, autosack_threshold) VALUES (1, 3)"
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, "
            "attendance_channel_id) VALUES (?, 601)",
            (division_id,),
        )
        await db.commit()
    await _staged_amendment(path, season_id)

    # 601 is absent, but the module is off, or cannot be read, so nothing asks after it.
    bot = await _bot_recording_reposts(path, [], missing=(601,))
    if unreadable:
        answer_switch(bot.module_service, "attendance", sqlite3.OperationalError("database is locked"))
    await _approve(path, season_id, bot)

    async with get_connection(path) as db:
        row = await (
            await db.execute(
                "SELECT points FROM season_points_entries WHERE season_id = ? AND position = 1",
                (season_id,),
            )
        ).fetchone()
    assert row is not None and row["points"] == 30, "a sound amendment was refused"


# ---------------------------------------------------------------------------
# The ordering rule holds at the mid-season end too
#
# The confirmation of placements refuses a points table that is out of order. Approving an amendment
# installed one without a word — deleting the season's points and refilling them from
# the modification store, then rescoring and reposting every round of every division
# against the new numbers. A rule that bound only the approval was a rule a league could
# step around by approving a good table and amending it afterwards.
# ---------------------------------------------------------------------------


async def test_a_well_ordered_amendment_still_applies(tmp_path):
    """The other half: a guard that refuses everything is no better than none at all. P3 and
    P4 staged at 0 below the season's 25 and 18, paying nothing below the points, are the
    ordinary shape of a table, mid-season as at the start: approved, the season pays them."""
    league = await points_league(tmp_path, staged={3: 0, 4: 0})

    await press_approve(league)
    await run_queue(league.bot)

    assert (await points_state(league))["points"] == SEASON_TABLE + [
        ("Standard", "FEATURE_RACE", 3, 0), ("Standard", "FEATURE_RACE", 4, 0),
    ]


# ---------------------------------------------------------------------------
# An approved amendment scores every raced session again (#443)
#
# The approval replaced the season's points tables and reposted every round, but scored no
# session again: every repost and every standings carried the points the rounds were first
# scored under. Each session seeded below on `db_path` was scored under the old
# table, 25-18-15 with one point for the fastest lap inside the top ten, before the
# amendment is approved.
# ---------------------------------------------------------------------------

_WINNER, _RUNNER_UP, _THIRD = 1001, 1002, 1003

#: The teams every seeded division fields, two drivers to a team as a league runs them:
#: the winner and the runner-up drive for the first, third and the fourth driver for the
#: second, and a fifth driver for the third. ``(shorthand, full name)``.
_TEAMS = (("RBR", "Red Bull Racing"), ("FER", "Ferrari"), ("MCL", "McLaren"))


def _team_of(driver: int) -> int:
    """The index in ``_TEAMS`` of the team *driver* (before any offset) drives for."""
    return (driver - _WINNER) // 2

#: The classification every raced round below records unless a test gives its own: the
#: winner, the runner-up, and third with the quickest lap, each holding what the old table
#: gave them. ``(driver, position, outcome, fastest lap, points, fastest-lap bonus,
#: post-race time penalty in ms)``.
_OLD_CLASSIFICATION = (
    (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
    (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 0, 0),
    (_THIRD, 3, "CLASSIFIED", "1:30.000", 15, 1, 0),
)


async def _seed_points_config(
    path: str,
    season_id: int,
    config: str = "STD",
    *,
    race: dict[int, int] | None = None,
    fastest_lap: tuple[int, int] | None = (1, 10),
    qualifying: dict[int, int] | None = None,
) -> None:
    """Give the season *config*: 25-18-15 for the feature race unless *race* says otherwise,
    ``(points, position limit)`` for its fastest lap, and *qualifying* for feature qualifying."""
    race = {1: 25, 2: 18, 3: 15} if race is None else race
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config),
        )
        for session_type, table in (("FEATURE_RACE", race), ("FEATURE_QUALIFYING", qualifying or {})):
            for position, points in table.items():
                await db.execute(
                    "INSERT INTO season_points_entries "
                    "(season_id, config_name, session_type, position, points) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (season_id, config, session_type, position, points),
                )
        if fastest_lap is not None:
            await db.execute(
                "INSERT INTO season_points_fl "
                "(season_id, config_name, session_type, fl_points, fl_position_limit) "
                "VALUES (?, ?, 'FEATURE_RACE', ?, ?)",
                (season_id, config, *fastest_lap),
            )
        await db.commit()


async def _seed_raced_division(
    path: str,
    season_id: int,
    name: str,
    *,
    channels: tuple[int, int],
    round_statuses: tuple[str, ...] = ("FINAL", "FINAL"),
    division_status: str = "ACTIVE",
    unraced_status: str = "NOT_RUN",
    config: str = "STD",
    round_formats: tuple[str, ...] | None = None,
    round_configs: tuple[str, ...] | None = None,
    classification: tuple = _OLD_CLASSIFICATION,
    qualifying: tuple = (),
    fastest_lap_override: int | None = None,
    driver_offset: int = 0,
):
    """A division whose raced rounds were scored under the old table, and one round to come.

    It fields the teams of ``_TEAMS``, each driver in the team ``_team_of`` names, so that
    no team has more than two drivers in a session.

    *channels* are its ``(results, standings)`` channels. Each status in *round_statuses* is
    a raced round, in order, carrying one feature race under *config* with *classification*,
    and a feature qualifying with *qualifying* ``(driver, position, points)`` where given. The
    round after them has not been raced. *driver_offset* is added to every driver id, so that
    two divisions do not share a driver.

    Every raced round is a Normal one scored under *config*, unless *round_formats* and
    *round_configs* give each raced round, in order, a format and a configuration of its own.

    Returns ``(division_id, raced round ids, race session ids, qualifying session ids,
    unraced round id)``.
    """
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status) "
            "VALUES (?, ?, 777, ?)",
            (season_id, name, division_status),
        )
        division_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?)",
            (division_id, *channels),
        )
        team_ids: list[int] = []
        for shorthand, full_name in _TEAMS:
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name) VALUES (?, ?, ?)",
                (division_id, shorthand, full_name),
            )
            team_ids.append(cursor.lastrowid)

        raced: list[int] = []
        race_sessions: list[int] = []
        qualifying_sessions: list[int] = []
        formats = round_formats or ("NORMAL",) * len(round_statuses)
        configs = round_configs or (config,) * len(round_statuses)
        for round_number, (status, round_format, round_config) in enumerate(
            zip(round_statuses, formats, configs, strict=True), start=1
        ):
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, status, "
                "scheduled_at) VALUES (?, ?, ?, 'Monza', ?, '2026-06-01T18:00:00+00:00')",
                (division_id, round_number, round_format, status),
            )
            round_id = cursor.lastrowid
            raced.append(round_id)
            if qualifying:
                cursor = await db.execute(
                    "INSERT INTO session_results "
                    "(round_id, division_id, session_type, status, config_name) "
                    "VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', ?)",
                    (round_id, division_id, round_config),
                )
                qualifying_sessions.append(cursor.lastrowid)
                for driver, position, points in qualifying:
                    await db.execute(
                        "INSERT INTO qualifying_session_results (session_result_id, "
                        "driver_user_id, team_instance_id, finishing_position, outcome, "
                        "best_lap, points_awarded) VALUES (?, ?, ?, ?, 'CLASSIFIED', "
                        "'1:29.000', ?)",
                        (
                            cursor.lastrowid, driver + driver_offset,
                            team_ids[_team_of(driver)], position, points,
                        ),
                    )
            cursor = await db.execute(
                "INSERT INTO session_results "
                "(round_id, division_id, session_type, status, config_name, fl_driver_override) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', ?, ?)",
                (
                    round_id,
                    division_id,
                    round_config,
                    None if fastest_lap_override is None else fastest_lap_override + driver_offset,
                ),
            )
            session_id = cursor.lastrowid
            race_sessions.append(session_id)
            for driver, position, outcome, lap, points, bonus, penalty_ms in classification:
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                    "team_instance_id, finishing_position, outcome, fastest_lap, "
                    "points_awarded, fastest_lap_bonus, postrace_time_penalties_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id, driver + driver_offset, team_ids[_team_of(driver)],
                        position, outcome, lap, points, bonus, penalty_ms,
                    ),
                )
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, status, "
            "scheduled_at) VALUES (?, ?, 'NORMAL', 'Spa', ?, '2026-09-01T18:00:00+00:00')",
            (division_id, len(round_statuses) + 1, unraced_status),
        )
        unraced_round_id = cursor.lastrowid
        await db.commit()
    return division_id, raced, race_sessions, qualifying_sessions, unraced_round_id


async def _race_points(path: str, session_id: int) -> dict[int, tuple[int, int]]:
    """Each driver's stored ``(points, fastest-lap bonus)`` in a race session."""
    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id, points_awarded, fastest_lap_bonus "
            "FROM race_session_results WHERE session_result_id = ?",
            (session_id,),
        )
        return {
            r["driver_user_id"]: (r["points_awarded"], r["fastest_lap_bonus"])
            for r in await cursor.fetchall()
        }


def _posts(reposted: list[tuple], channel_id: int, round_number: int) -> list[str]:
    """What was posted to *channel_id* under the heading of *round_number*."""
    return [
        content
        for posted_to, content in reposted
        if posted_to == channel_id and f"Round {round_number} " in content
    ]


async def _scoring_bot(path: str, reposted: list[tuple]):
    """The repost double above, for sessions that carry drivers.

    These sessions carry drivers, which the #130 double never had to name. Naming one reads
    the database through the bot, and asks the server for them, which answers as Discord
    does for somebody who is not a member.
    """
    return await _bot_recording_reposts(path, reposted)


async def _approve_raised_win(path: str, season_id: int, reposted: list[tuple] | None = None):
    """Stage a win worth 30 rather than 25, and approve it."""
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )
    bot = await _scoring_bot(path, [] if reposted is None else reposted)
    await _approve(path, season_id, bot)


async def test_an_approved_amendment_rescores_every_raced_session(tmp_path):
    """P1 raised from 25 to 26 and approved: Lewis's win is worth 26 in every raced round of
    Pro and of Am, and Max keeps his 18."""
    league = await points_league(tmp_path)

    await press_approve(league)
    await run_queue(league.bot)

    for division_id in (PRO, AM):
        for number in (1, 2, 3):
            assert await race_points(league, division_id, number) == {LEWIS: 26, MAX: 18}


async def test_an_approved_amendment_moves_the_standings(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    division_id, raced, _s, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )

    await _approve_raised_win(path, season_id)

    async with get_connection(path) as db:
        cursor = await db.execute(
            "SELECT total_points FROM driver_standings_snapshots "
            "WHERE round_id = ? AND division_id = ? AND driver_user_id = ?",
            (raced[-1], division_id, _WINNER),
        )
        row = await cursor.fetchone()
    assert row is not None and row["total_points"] == 60, "the standings kept the old points"


async def test_an_approved_amendment_reposts_every_standings_with_the_new_points(db_path):
    """What the owner asked for at Gate 1: every standings, of every division, reposted with
    the new totals, and none for a round not yet raced.

    Each post carries both championships. The winner and the runner-up drive for Red Bull
    Racing, which the old table gave 43 a round and the new one 48.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    for standings_channel, winner in ((502, _WINNER), (512, _WINNER + 1000)):
        for round_number, total, team_total in ((1, 30, 48), (2, 60, 96)):
            posts = _posts(reposted, standings_channel, round_number)
            assert posts, f"round {round_number} standings were not reposted to {standings_channel}"
            assert f"<@{winner}> — **{total} pts**" in posts[-1], posts[-1]
            assert f"1. Red Bull Racing — **{team_total} pts**" in posts[-1], posts[-1]
        assert not _posts(reposted, standings_channel, 3), (
            "standings were posted for a round that has not been raced"
        )


async def test_an_approved_amendment_reposts_every_results_table_with_the_new_points(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    for results_channel, winner in ((501, _WINNER), (511, _WINNER + 1000)):
        for round_number in (1, 2):
            posts = _posts(reposted, results_channel, round_number)
            assert posts, f"round {round_number} results were not reposted to {results_channel}"
            winner_line = next(
                line for line in posts[-1].splitlines() if line.startswith(f"**1.** <@{winner}>")
            )
            assert winner_line.endswith("**30 pts**"), winner_line


async def test_an_approved_fastest_lap_amendment_rescores_the_bonus(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await _approve(path, season_id, await _scoring_bot(path, []))

    for session_id in sessions:
        assert (await _race_points(path, session_id))[_THIRD] == (15, 3)


async def test_rescoring_keeps_the_fastest_lap_override(db_path):
    """The runner-up was given the fastest lap by hand; the rescoring must not hand it back
    to the quickest time."""

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 1, 0),
            (_THIRD, 3, "CLASSIFIED", "1:30.000", 15, 0, 0),
        ),
        fastest_lap_override=_RUNNER_UP,
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_bonus(path, season_id, "STD", "FEATURE_RACE", 3)

    await _approve(path, season_id, await _scoring_bot(path, []))

    for session_id in sessions:
        points = await _race_points(path, session_id)
        assert points[_RUNNER_UP] == (18, 3), points
        assert points[_THIRD] == (15, 0), points


async def test_an_approved_position_limit_moves_the_fastest_lap_bonus(db_path):
    """Third set the quickest lap; with the bonus now limited to the top two, nobody holds it."""

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await enable_amendment_mode(path, season_id)
    await modify_fl_position_limit(path, season_id, "STD", "FEATURE_RACE", 2)

    await _approve(path, season_id, await _scoring_bot(path, []))

    for session_id in sessions:
        points = await _race_points(path, session_id)
        assert points[_THIRD] == (15, 0), points
        assert all(bonus == 0 for _points, bonus in points.values()), points


async def test_an_approved_amendment_rescores_qualifying(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id, qualifying={1: 3})
    _division, _raced, _s, qualifying_sessions, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        qualifying=((_WINNER, 1, 3), (_RUNNER_UP, 2, 0)),
    )
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_QUALIFYING", [(1, 5)], **ACTOR
    )

    await _approve(path, season_id, await _scoring_bot(path, []))

    async with get_connection(path) as db:
        for session_id in qualifying_sessions:
            cursor = await db.execute(
                "SELECT points_awarded FROM qualifying_session_results "
                "WHERE session_result_id = ? AND driver_user_id = ?",
                (session_id, _WINNER),
            )
            assert (await cursor.fetchone())["points_awarded"] == 5


async def test_a_session_under_another_configuration_keeps_its_points(db_path):
    """Beta races under ALT, 12-8-6 with no fastest-lap bonus; amending STD leaves it alone."""
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_points_config(path, season_id, "ALT", race={1: 12, 2: 8, 3: 6}, fastest_lap=None)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    _division, _raced, alt_sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), config="ALT", driver_offset=1000,
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 12, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 8, 0, 0),
            (_THIRD, 3, "CLASSIFIED", "1:30.000", 6, 0, 0),
        ),
    )

    await _approve_raised_win(path, season_id)

    for session_id in alt_sessions:
        assert await _race_points(path, session_id) == {
            _WINNER + 1000: (12, 0),
            _RUNNER_UP + 1000: (8, 0),
            _THIRD + 1000: (6, 0),
        }


async def test_a_round_under_an_unchanged_configuration_keeps_its_points_and_is_reposted(db_path):
    """What the owner asked for at Gate 2: two rounds of different formats, each scored under
    a configuration of its own, both paying 25 for a win.

    Round 1 is a Normal round under STD, round 2 an Endurance round under ENDURO. Only STD's
    win is raised to 30: round 1 is scored again, round 2 keeps its points, and round 2 is
    reposted all the same, its results table and its standings both.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_points_config(path, season_id, "ENDURO")
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        round_formats=("NORMAL", "ENDURANCE"), round_configs=("STD", "ENDURO"),
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    assert (await _race_points(path, sessions[0]))[_WINNER] == (30, 0), (
        "the round under the amended configuration kept its old points"
    )
    assert await _race_points(path, sessions[1]) == {
        _WINNER: (25, 0),
        _RUNNER_UP: (18, 0),
        _THIRD: (15, 1),
    }, "the round under the unchanged configuration was scored under the amended one"

    results = _posts(reposted, 501, 2)
    assert results, "round 2's results were not reposted"
    winner_line = next(
        line for line in results[-1].splitlines() if line.startswith(f"**1.** <@{_WINNER}>")
    )
    assert winner_line.endswith("**25 pts**"), winner_line
    standings = _posts(reposted, 502, 2)
    assert standings, "round 2's standings were not reposted"
    assert f"<@{_WINNER}> — **55 pts**" in standings[-1], standings[-1]


async def test_rescoring_keeps_the_sanctions(db_path):
    """The winner and the runner-up are paid the new table; the penalties stand.

    The runner-up was given a time penalty and is stored at the second place it left them
    in; third did not finish, fourth was disqualified having set the quickest lap, and
    fifth did not start.
    """
    path, season_id = db_path
    await _seed_points_config(path, season_id, race={1: 25, 2: 18, 3: 15, 4: 12, 5: 10})
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502), round_statuses=("FINAL",),
        classification=(
            (_WINNER, 1, "CLASSIFIED", "1:31.000", 25, 0, 0),
            (_RUNNER_UP, 2, "CLASSIFIED", "1:31.500", 18, 0, 5000),
            (_THIRD, 3, "DNF", "1:32.000", 0, 0, 0),
            (1004, 4, "DSQ", "1:29.000", 0, 0, 0),
            (1005, 5, "DNS", None, 0, 0, 0),
        ),
    )
    await enable_amendment_mode(path, season_id)
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(1, 30)], **ACTOR
    )
    await modify_session_points(
        path, season_id, "STD", "FEATURE_RACE", [(2, 20)], **ACTOR
    )

    await _approve(path, season_id, await _scoring_bot(path, []))

    points = await _race_points(path, sessions[0])
    assert points[_WINNER][0] == 30, points
    assert points[_RUNNER_UP][0] == 20, "the time-penalised driver was not scored at their place"
    assert points[_THIRD][0] == 0, "a driver who did not finish was paid for their place"
    assert points[1004] == (0, 0), "a disqualified driver was paid"
    assert points[1005] == (0, 0), "a driver who did not start was paid"


async def test_a_round_in_review_is_rescored_and_reposted_under_its_own_label(db_path):
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502),
        round_statuses=("FINAL", "AWAITING_REPORT_VERDICTS"),
    )

    reposted: list[tuple] = []
    await _approve_raised_win(path, season_id, reposted)

    assert (await _race_points(path, sessions[1]))[_WINNER] == (30, 0)
    posts = _posts(reposted, 501, 2)
    assert posts, "the round in review was not reposted"
    assert "Provisional Results" in posts[-1], posts[-1]
    assert "**30 pts**" in posts[-1], posts[-1]


@pytest.mark.parametrize(
    "division_status",
    [
        pytest.param("CANCELLED", id="cancelled"),
        pytest.param("FINISHED", id="finished-while-another-races"),
    ],
)
async def test_a_cancelled_or_finished_division_is_rescored(db_path, division_status):
    """Beta was cancelled after two rounds, or finished its season with its last round
    cancelled while Alpha still has a round to race; what it raced is scored under the new
    table too (decided 2026-09-26)."""
    path, season_id = db_path
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))
    _division, _raced, beta_sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Beta", channels=(511, 512), driver_offset=1000,
        division_status=division_status, unraced_status="CANCELLED",
    )

    await _approve_raised_win(path, season_id)

    for session_id in beta_sessions:
        assert (await _race_points(path, session_id))[_WINNER + 1000] == (30, 0)


async def test_rescoring_leaves_another_season_alone(db_path):
    """A completed season keeps the points it was scored with.

    Its own STD table now pays 20 for a win while its winners hold 25, so a rescoring that
    reaches that season at all moves them, whichever season's table it scores them under.
    """
    path, season_id = db_path
    async with get_connection(path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2025-01-01', 'COMPLETED', 0)"
        )
        other_season_id = cursor.lastrowid
        await db.commit()
    await _seed_points_config(path, other_season_id, race={1: 20, 2: 18, 3: 15})
    _division, _raced, other_sessions, _q, _unraced = await _seed_raced_division(
        path, other_season_id, "Alpha", channels=(531, 532), driver_offset=2000
    )
    await _seed_points_config(path, season_id)
    await _seed_raced_division(path, season_id, "Alpha", channels=(501, 502))

    await _approve_raised_win(path, season_id)

    for session_id in other_sessions:
        assert (await _race_points(path, session_id))[_WINNER + 2000] == (25, 0)


async def test_a_rescore_that_fails_changes_nothing(db_path, monkeypatch):
    """Scoring the second session raises: the approval's save fails whole and the queue stops
    at it, and the season, the staged changes and the first session's points are exactly as
    they stood. Nothing is reposted, and the log records no success."""
    import sqlite3

    from leaguebot.results.services.points import scoring

    path, season_id = db_path
    await _seed_points_config(path, season_id)
    _division, _raced, sessions, _q, _unraced = await _seed_raced_division(
        path, season_id, "Alpha", channels=(501, 502)
    )
    await _staged_amendment(path, season_id)

    real_scoring = scoring._apply_points_in_tx
    calls: list[int] = []

    async def failing_on_the_second_session(db, session_result_id, *args, **kwargs):
        calls.append(session_result_id)
        if len(calls) == 2:
            raise sqlite3.OperationalError("database is locked")
        return await real_scoring(db, session_result_id, *args, **kwargs)

    monkeypatch.setattr(
        scoring, "_apply_points_in_tx", failing_on_the_second_session
    )

    reposted: list[tuple] = []
    bot = await _scoring_bot(path, reposted)
    await _approve(path, season_id, bot)

    from tests.support.change_queue import stopped_job

    stopped = await stopped_job(path)
    assert stopped is not None and stopped["name"] == "apply", (
        f"the queue did not stop at the approval's save: {stopped}"
    )
    assert reposted == [], "a failed approval reposted"
    logged = "\n".join(bot.log_channel.sent)
    assert "| Success" not in logged, "a failed approval was logged as a success"
    points, staged, state = await _season_state(path, season_id)
    assert points == [(1, 25), (2, 18), (3, 15)], "the season's table moved"
    assert staged == [(1, 30), (2, 18), (3, 15)], "the staged changes were lost"
    assert state is not None and state.amendment_active and state.modified_flag
    for session_id in sessions:
        assert (await _race_points(path, session_id))[_WINNER] == (25, 0), (
            "a failed approval kept part of its rescoring"
        )
