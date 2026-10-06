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

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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
from tests.support.points_league import (
    AM_RESULTS,
    AM_STANDINGS,
    BETA_ATTENDANCE,
    BETA_RESULTS,
    BETA_STANDINGS,
    PRO,
    AM,
    PRO_RESULTS,
    PRO_STANDINGS,
    old_results,
    open_panel,
    points_league,
    points_state,
    press_approve,
    run_staging,
    write,
)
from tests.support.review_league import block_queue, stopped_at

KIND = "results.points_amendment.approve"

ON_THE_QUEUE = "#439: approving a points amendment is not yet a change on the change queue"
STAGING_HELD = "#439: the staging commands do not yet refuse while a points approval is in hand"

SUCCESS = "✅ Amendment approved. All standings recomputed and reposted."
MODE_OFF = (
    "❌ Amendment mode is not active. Nothing was changed: these changes were already "
    "approved, or amendment mode was turned off after this panel was drawn."
)
NOT_PUBLISHED = "⛔ Amendment not approved — the result could not be published:"
OUT_OF_ORDER = "❌ Amendment not approved — the points would be out of order:"
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
#: The season's own table, as it stood before any approval.
SEASON_TABLE = [("Standard", "FEATURE_RACE", 1, 25), ("Standard", "FEATURE_RACE", 2, 18)]
APPROVED_TABLE = [("Standard", "FEATURE_RACE", 1, 26), ("Standard", "FEATURE_RACE", 2, 18)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _replies(press: Any) -> str:
    """Everything the press was answered with: the acknowledgement, then its updates."""
    from tests.support.change_queue import updated_reply

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
    """The number of the job the first approval in hand waits on."""
    change = (await _approvals(league))[0]
    pending = [row for row in await step_rows(league.db_path, change["id"])
               if row["done_at"] is None]
    return pending[0]["id"]


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
    blocker, stopped at `names`, or stopped at a `post` after its save (Pro's round 1
    results refused once). Returns what clears the stop and lets the approval finish."""
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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
    assert lines[0].split("\n")[0].endswith(f"— {NOT_PUBLISHED}")
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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
    pytest.param(_out_of_order, {}, OUT_OF_ORDER, "STD", id="out-of-order"),
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
    is pressed. The press is answered with the refusal, one ⛔ line names it, nothing is queued
    and the season, its working copy and amendment mode stay as they stood."""
    league = await points_league(tmp_path, **options)
    panel = await open_panel(league)
    await setup(league)
    before = await points_state(league)
    asked = len(await _approvals(league))

    press = await press_approve(league, panel)

    reply = acknowledgement(press)
    assert reply.startswith(expected)
    if detail is not None and detail != "STD":
        assert detail in reply
    lines = _refusal_lines(league)
    assert len(lines) == 1
    assert lines[0].split("\n")[0].endswith(f"— {reply.split(chr(10))[0]}")
    assert len(await _approvals(league)) == asked
    assert await points_state(league) == before


async def _live_channel_lost(league: Any) -> None:
    league.remove_channel(PRO_RESULTS)


async def _cancelled_results_lost(league: Any) -> None:
    league.remove_channel(BETA_RESULTS)


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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
    assert len(_refusal_lines(league)) == 1
    assert await points_state(league) == before
    assert before["points"] == SEASON_TABLE


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
@pytest.mark.parametrize("state", ["waiting", "names", "post"])
async def test_a_second_press_while_the_approval_is_in_hand_is_refused_at_once_naming_its_job(
    tmp_path, state,
):
    """Owner, "Refuse at once": a second Approve while the first is waiting, stopped before its
    save or stopped after it is refused at once, naming the job the first waits on; the first
    finishes as it would have."""
    league = await points_league(tmp_path)
    second = await open_panel(league)
    with ExitStack() as stack:
        clear = await _approval_in_hand(league, state, stack)
        job = await _waits_on(league)

        refused = await press_approve(league, second)

        assert acknowledgement(refused) == IN_HAND.format(job=job)
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


@pytest.mark.xfail(strict=True, reason=STAGING_HELD)
@pytest.mark.parametrize("state", ["waiting", "names", "post"])
@pytest.mark.parametrize("command, args", _STAGING, ids=[c for c, _ in _STAGING])
async def test_the_staging_commands_are_refused_while_the_approval_is_in_hand(
    tmp_path, command, args, state,
):
    """Owner, "Refuse all six": while the approval is waiting, stopped before its save or
    stopped after it, each staging command (toggle turning the mode off before the save and on
    after it; bulk-session before its form, and its form submitted after the press) is refused
    naming the job, writing nothing; no form is shown where the command is refused."""
    league = await points_league(tmp_path)
    with ExitStack() as stack:
        await _approval_in_hand(league, state, stack)
        job = await _waits_on(league)
        before = await points_state(league)

        staged = await run_staging(league, command, **args)

        assert staged.reply == STAGING_REFUSAL.format(job=job)
        named = "bulk-session" if command == "bulk-session-form" else command
        assert len(_refusal_lines(league, f"results amend {named}")) == 1
        assert staged.modal is None
        assert await points_state(league) == before


@pytest.mark.xfail(strict=True, reason=ON_THE_QUEUE)
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
