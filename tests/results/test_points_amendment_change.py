"""Approving a points amendment on the change queue (#439, slice 3).

✅ Approve on `/results amend review` asks the change queue for the season's approval. It is
checked when pressed and again when it runs; the season's points, the rescoring, the standings
and the attendance are saved in one save; the reposts, their deletions and the attendance follow
as jobs, each of which stops the queue when it fails. While the approval is in hand, the commands
that stage points changes are refused, naming its job.

Every test drives the cog and the real queue through `tests/support/points_league.py`, with
"now" pinned. The change type is unbuilt until the build, so each is marked to fail until then:
today the press approves on the spot, inside the button.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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
    from leaguebot.results.services import review_posting

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
        "leaguebot.results.services.standings_service.compute_team_standings_on",
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
])
async def test_the_press_is_refused_at_once_in_today_s_words(
    tmp_path, setup, options, expected, detail,
):
    """A panel drawn while the approval could be made; then the condition changes, and Approve
    is pressed. The press is answered with the refusal, one ⛔ line names it with its detail
    (the positions at fault, the channel lost) written beneath, nothing is queued and the
    season, its working copy and amendment mode stay as they stood. The ordering's line says
    nothing of publishing, so the two refusals are told apart."""
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
    assert len(await _approvals(league)) == asked
    assert await points_state(league) == before


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
    discards its save, `session` stages again."""
    for name in ("done", "discarded"):
        (tmp_path / name).mkdir()
    done = await points_league(tmp_path / "done")
    await press_approve(done)
    await run_queue(done.bot)
    assert (await run_staging(done, "toggle")).reply.startswith("✅ Amendment mode enabled.")

    discarded = await points_league(tmp_path / "discarded")
    with _standings_fail():
        await press_approve(discarded)
        await run_queue(discarded.bot)
    assert await stopped_at(discarded) == "apply"
    await discard_job(discarded.bot)
    staged = await run_staging(discarded, "session", position=1, points=27)
    assert staged.reply.startswith("✅ Updated in modification store")


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
    from leaguebot.core.db.database import get_connection

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
