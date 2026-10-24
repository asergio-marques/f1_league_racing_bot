"""A round's results resubmitted on the change queue (#439, slice 6b, C51–C53).

A league manager presses **🔄 Resubmit Initial Results** on the penalty review's prompt. The press
asks `results.resubmission.start` with its interaction, acknowledged privately as under way: its
jobs flag the channel `resubmitting` (`mark`), take the review prompt and any approval message down
(`delete_prompt`, `delete_approval`), post the announcement with its Cancel button, recording its
id (`post_announcement`), and post the first session's prompt (`post_prompt`). The round's results
stand meanwhile.

Each paste typed in the submission channel is then a `results.resubmission.turn` on the round's
lane (``channel:<submission channel>``): marked ⏳ at once, saved as a draft of the resubmission
(`resubmission_drafts`) when its turn comes, followed by the next session's prompt. The last paste
hands `results.resubmission.swap` to the league lane, which replaces the round's results with the
drafts, deletes them, and asks the review again; the Cancel button comes down and the review's
prompt comes back through the round's lane. A restart during a resubmission abandons it in the
review opening's own jobs (`abandon_resubmission`, `take_down_cancel`, `post_restart_notice`), the
drafts surviving until then; where the channel is gone the notice completes, posting nothing, and
the resubmission still ends (results spec :417).

The league, its channels and its queue are `test_review_open_change.py`'s: round 3 of Pro, a Normal
round, its Feature Race won by Lewis (101) ahead of Max (102), its review open in submission channel
702. The division's seats, as a paste's validation reads them, are Lewis and Max at team 3001,
shorthand FER.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services import penalty_wizard, result_submission_service
from leaguebot.results.services.result_submission_service import (
    DivisionValidationData,
    get_sessions_for_format,
)
from leaguebot.results.utils import results_formatter
from tests.results.test_review_open_change import (
    SUBMISSION_CHANNEL,
    _league,
    _open,
    _prompt_view,
    _prompts,
    _recover,
    _refuse_the_job,
)
from tests.support.change_queue import (
    MARK,
    MEMBER_ID,
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    member_interaction,
    restart_queue,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
    typed_message,
    updated_reply,
)
from tests.support.review_league import NOW, ReviewLeague, block_queue

START_KIND = "results.resubmission.start"
TURN_KIND = "results.resubmission.turn"
SWAP_KIND = "results.resubmission.swap"
REVIEW_OPEN = "results.review.open"
ROUND_ID = 21
LANE = f"channel:{SUBMISSION_CHANNEL}"
ANNOUNCEMENT = "⚠️ **Results resubmission started.**"

QUALIFYING_TYPE, RACE_TYPE = get_sessions_for_format(RoundFormat.NORMAL)[:2]
QUALIFYING, RACE = (
    results_formatter.format_session_label(session, is_sprint=False)
    for session in (QUALIFYING_TYPE, RACE_TYPE)
)
RACE_PASTE = "1, <@102>, FER, 1:30:00.000, 1:20.000, N/A\n2, <@101>, FER, +5.000, 1:21.000, N/A"

_SEATED = DivisionValidationData(
    division_driver_ids={101, 102}, team_of_role={}, reserve_team_role_id=None,
    driver_team_map={101: 3001, 102: 3001}, reserve_driver_ids=set(),
    team_names={3001: "Team 3001"}, team_of_shorthand={"fer": 3001},
)


def _manager() -> Any:
    return tier_member("manager", display_name="Alex", name="Alex#0001")


async def _reviewing(tmp_path: Any, monkeypatch: Any) -> ReviewLeague:
    """The league of the module docstring, round 3's review prompt posted through the queue."""
    league = await _league(tmp_path)
    await _open(league)
    sub = league.channel(SUBMISSION_CHANNEL)
    sub.posted = []
    monkeypatch.setattr(penalty_wizard, "_is_league_manager", AsyncMock(return_value=True))
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=_SEATED),
    )
    return league


async def _press_resubmit(league: ReviewLeague, *, run: bool = True) -> Any:
    """The manager presses Resubmit on round 3's review prompt; then, where *run*, the queue is
    run. Gives the press's interaction."""
    view = _prompt_view(league)
    interaction = member_interaction(league.bot, user=_manager())
    interaction.channel_id = SUBMISSION_CHANNEL
    button = next(item for item in view.children
                  if getattr(item, "custom_id", None) == "pw_resubmit")
    await button.callback(interaction)
    if run:
        await run_queue(league.bot)
    return interaction


async def _type(league: ReviewLeague, content: str, *, run: bool = True) -> Any:
    """The manager types *content* in channel 702 and the results cog hears it; then, where *run*,
    the queue is run. Gives the message."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    message = typed_message(league.channel(SUBMISSION_CHANNEL), _manager(), content,
                            created_at=NOW)
    await ResultsCog(league.bot).on_message(message)
    if run:
        await run_queue(league.bot)
    return message


async def _rows(league: ReviewLeague, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with get_connection(league.db_path) as db:
        return [dict(row) for row in await (await db.execute(sql, args)).fetchall()]


async def _row(league: ReviewLeague) -> dict[str, Any]:
    [row] = await _rows(league, 
        "SELECT resubmitting, resubmit_prompt_message_id, resubmit_started_by "
        "FROM round_submission_channels WHERE round_id = ?", ROUND_ID,
    )
    return row


async def _drafts(league: ReviewLeague) -> list[tuple[str, str]]:
    rows = await _rows(league,
        "SELECT session_type, status FROM resubmission_drafts WHERE round_id = ? "
        "ORDER BY session_type", ROUND_ID,
    )
    return [(row["session_type"], row["status"]) for row in rows]


async def _sessions(league: ReviewLeague) -> list[tuple[str, str]]:
    rows = await _rows(league,
        "SELECT session_type, status FROM session_results WHERE round_id = ? ORDER BY id", ROUND_ID
    )
    return [(row["session_type"], row["status"]) for row in rows]


async def _changes(league: ReviewLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


def _announcement(league: ReviewLeague) -> Any:
    [message] = [message for message in league.channel(SUBMISSION_CHANNEL).messages.values()
                 if str(message.content).startswith(ANNOUNCEMENT)]
    return message


def _texts(league: ReviewLeague) -> list[str]:
    sub = league.channel(SUBMISSION_CHANNEL)
    return [str(sub.messages[mid].content) for mid in league.sent_to(SUBMISSION_CHANNEL)
            if mid in sub.messages]


async def test_resubmit_is_acknowledged_and_takes_the_prompt_down_before_announcing(
    tmp_path, monkeypatch,
):
    """The manager presses Resubmit. They are answered privately that it is under way, and
    nothing has changed yet: the prompt stands and the channel is not resubmitting. Once the
    queue runs, the channel is flagged resubmitting, the review prompt is deleted, and only then
    is the announcement posted with its Cancel button, followed by the prompt for the first
    session, Qualifying; the round's results still stand, and the acknowledgement is updated."""
    league = await _reviewing(tmp_path, monkeypatch)
    [prompt] = _prompts(league)

    interaction = await _press_resubmit(league, run=False)

    assert acknowledgement(interaction).startswith("⏳")
    assert _prompts(league) == [prompt]
    assert (await _row(league))["resubmitting"] == 0

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [change["state"] for change in await _changes(league, START_KIND)] == ["DONE"]
    assert (await _row(league))["resubmitting"] == 1
    assert _prompts(league) == []
    announcement = _announcement(league)
    assert announcement.view is not None
    assert (league.events.index(("delete", SUBMISSION_CHANNEL, prompt))
            < league.events.index(("send", SUBMISSION_CHANNEL, announcement.id)))
    assert any(f"📋 Submit **{QUALIFYING}** results" in text for text in _texts(league))
    assert await _sessions(league) == [("FEATURE_RACE", "ACTIVE")]
    assert updated_reply(interaction)


async def test_the_announcement_s_id_is_saved_with_its_mark(tmp_path, monkeypatch):
    """Once the queue has run the press, the submission row records the announcement's id and
    the manager who started the resubmission, saved with the mark of the job that posted it."""
    league = await _reviewing(tmp_path, monkeypatch)

    await _press_resubmit(league)

    row = await _row(league)
    assert row["resubmit_prompt_message_id"] == _announcement(league).id
    assert int(row["resubmit_started_by"]) == MEMBER_ID
    [change] = await _changes(league, START_KIND)
    [posted] = [step for step in await step_rows(league.db_path, change["id"])
                if step["name"] == "post_announcement"]
    assert posted["done_at"] is not None


MARK_UNDISCARDABLE = "#439: marking the resubmission may still be discarded"


@pytest.mark.xfail(strict=True, reason=MARK_UNDISCARDABLE)
async def test_marking_the_resubmission_cannot_be_discarded(tmp_path, monkeypatch):
    """The manager Alex presses Resubmit on round 3's review prompt in Pro. The save of the
    change's first job, which flags the submission channel as resubmitting and records who
    started it, fails and stops round 3's lane at `mark`. A league admin's Discard on it is
    refused, saying round 3 in Pro's resubmission would run unrecorded: its pastes would be
    refused and its review left with no prompt until the bot restarts. The lane stays stopped
    there, the review prompt still stands and the channel is not resubmitting. Once the fault is
    lifted, a Retry flags the channel, takes the prompt down and posts the announcement."""
    league = await _reviewing(tmp_path, monkeypatch)
    [prompt] = _prompts(league)
    await _refuse_the_job(league.db_path, START_KIND, True, job="mark")
    await _press_resubmit(league)
    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None and stopped["name"] == "mark"

    refused = await discard_job(league.bot, lane=LANE)

    assert acknowledgement(refused) == (
        "⛔ This job can't be discarded: without it round 3 in **Pro**'s resubmission would run "
        "unrecorded: its pastes would be refused and its review left with no prompt until the bot "
        "restarts. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path, LANE))["id"] == stopped["id"]
    assert _prompts(league) == [prompt]
    assert (await _row(league))["resubmitting"] == 0

    await _refuse_the_job(league.db_path, START_KIND, False, job="mark")
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert (await _row(league))["resubmitting"] == 1
    assert _prompts(league) == []
    assert _announcement(league).view is not None


async def test_each_resubmitted_paste_is_a_draft_saved_by_its_turn(tmp_path, monkeypatch):
    """With the resubmission started, the manager types CANCELLED for Qualifying. The message is
    marked ⏳ at once and a turn asked on the round's lane; nothing is saved until it runs. Once it
    runs, Qualifying is saved as a cancelled draft of the resubmission, the round's own results
    are untouched, the prompt for the Feature Race follows, and the mark is cleared."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    message = await _type(league, "CANCELLED", run=False)

    assert MARK in message.marks
    [turn] = await _changes(league, TURN_KIND)
    assert turn["lane"] == LANE
    assert await _drafts(league) == []

    await run_queue(league.bot)

    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "CANCELLED")]
    assert await _sessions(league) == [("FEATURE_RACE", "ACTIVE")]
    assert any(f"📋 Submit **{RACE}** results" in text for text in _texts(league))
    assert MARK not in message.marks


async def test_a_paste_whose_resubmission_ended_before_its_turn_loses_its_mark(
    tmp_path, monkeypatch,
):
    """Alex types Qualifying while the resubmission stands, and the resubmission ends (Cancel
    ran) before the paste's turn does. The turn finds nothing to take and clears the paste's ⏳
    mark, rather than being refused with the mark left on the message for good; nothing is held."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    message = await _type(league, "CANCELLED", run=False)
    assert MARK in message.marks
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET resubmitting = 0, "
            "resubmit_prompt_message_id = NULL WHERE round_id = ?", (ROUND_ID,),
        )
        await db.commit()

    await run_queue(league.bot)

    assert MARK not in message.marks
    assert await _drafts(league) == []
    assert await stopped_job(league.db_path) is None


async def test_a_resubmitted_paste_while_its_turn_waits_is_turned_away_and_logged(
    tmp_path, monkeypatch,
):
    """With the resubmission started, the manager types CANCELLED for Qualifying and, before its
    turn has run, types it again. The second message is turned away at once: deleted, with a note
    in the channel that the earlier message is still waiting its turn, and no second turn is
    asked; the first stays marked ⏳. The refusal is logged: one ⛔ line naming the manager,
    round 3 and Qualifying still waited on (results :257). Once the queue runs, only the first is
    saved as a draft."""
    from tests.support.change_queue import queued_log_lines

    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    first = await _type(league, "CANCELLED", run=False)

    second = await _type(league, "CANCELLED", run=False)

    assert second.gone
    assert MARK in first.marks
    assert len(await _changes(league, TURN_KIND)) == 1
    assert any("still waiting its turn" in text for text in _texts(league))
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    turned_away = [line for line in logged.splitlines() if "⛔" in line]
    assert len(turned_away) == 1, logged
    line = logged[logged.index(turned_away[0]):]
    assert f"<@{MEMBER_ID}>" in line and "Round 3" in line and QUALIFYING in line, line

    await run_queue(league.bot)

    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "CANCELLED")]


async def test_the_last_resubmitted_paste_swaps_the_results_takes_cancel_down_and_opens_the_review(
    tmp_path, monkeypatch,
):
    """The manager types CANCELLED for Qualifying, then the Feature Race's results. Once the queue
    has run, the round's results are replaced by the resubmission's (Qualifying cancelled), the
    drafts are gone, the channel is no longer resubmitting, the announcement's Cancel button is
    taken down, the swap ran on the league lane, the review was asked again after it, and a
    fresh review prompt stands in channel 702."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    await _type(league, "CANCELLED")
    await _type(league, RACE_PASTE)

    assert await stopped_job(league.db_path) is None
    assert (QUALIFYING_TYPE.value, "CANCELLED") in await _sessions(league)
    assert await _drafts(league) == []
    assert (await _row(league))["resubmitting"] == 0
    assert _announcement(league).view is None
    [swap] = await _changes(league, SWAP_KIND)
    assert (swap["lane"] or "league") == "league" and swap["state"] == "DONE"
    assert [change["id"] for change in await _changes(league, REVIEW_OPEN)][-1] > swap["id"]
    assert len(_prompts(league)) == 1


async def test_a_resubmission_s_drafts_survive_a_restart_until_it_is_abandoned(
    tmp_path, monkeypatch,
):
    """Qualifying is saved as a cancelled draft when the bot restarts: the draft is still there
    on the fresh queue. The start-up then abandons the resubmission, and the draft is deleted with
    it; the round's own results stand."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, "CANCELLED")

    await restart_queue(league.bot)

    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "CANCELLED")]

    await _recover(league)

    assert await _drafts(league) == []
    assert (await _row(league))["resubmitting"] == 0
    assert await _sessions(league) == [("FEATURE_RACE", "ACTIVE")]


async def test_a_restart_during_a_resubmission_abandons_it_in_the_review_s_own_jobs(
    tmp_path, monkeypatch,
):
    """The bot restarts mid-resubmission. The start-up asks one change of the round, its review's
    opening, whose own jobs abandon the resubmission, take the Cancel button down and post the
    restart notice; once it has run the channel is no longer resubmitting, the announcement has
    no Cancel button, and a review prompt stands again."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    asked_before = {change["id"] for change in await change_rows(league.db_path)}

    await _recover(league)

    asked = [change for change in await change_rows(league.db_path)
             if change["id"] not in asked_before]
    assert [change["kind"] for change in asked] == [REVIEW_OPEN]
    names = [step["name"] for step in await step_rows(league.db_path, asked[0]["id"])]
    for name in ("abandon_resubmission", "take_down_cancel", "post_restart_notice"):
        assert name in names
    assert await stopped_job(league.db_path) is None
    assert (await _row(league))["resubmitting"] == 0
    assert _announcement(league).view is None
    assert len(_prompts(league)) == 1


async def test_a_refused_cancel_take_down_after_the_swap_stops_the_queue(tmp_path, monkeypatch):
    """Discord refuses to edit the announcement (Missing Permissions) when its Cancel button is to
    come down after the last paste. The results are swapped all the same and the drafts gone,
    and the round's lane stops at the take-down, not the league's."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    _announcement(league).edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _type(league, "CANCELLED")
    await _type(league, RACE_PASTE)

    assert await stopped_job(league.db_path, LANE) is not None
    assert await stopped_job(league.db_path, "league") is None
    assert await _drafts(league) == []
    assert (await _row(league))["resubmitting"] == 0


async def test_a_restart_notice_whose_channel_is_gone_completes_and_the_resubmission_still_ends(
    tmp_path, monkeypatch,
):
    """Channel 702 was deleted by hand during the resubmission when the bot restarts. The
    review opening's restart notice completes without posting, the queue does not stop, and the
    resubmission still ends: the channel is no longer resubmitting and its drafts are gone
    (results spec :417)."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, "CANCELLED")
    league.channels.pop(SUBMISSION_CHANNEL)

    await _recover(league)

    assert await stopped_job(league.db_path) is None
    assert (await _row(league))["resubmitting"] == 0
    assert await _drafts(league) == []


async def test_the_last_resubmitted_paste_hands_the_swap_to_the_league(tmp_path, monkeypatch):
    """The league lane is stopped at another job when the manager types the last paste. The
    paste's turn still runs on the round's lane, but the swap waits on the league: the round is
    still resubmitting and the message keeps its ⏳ mark. Once the stopped job is retried and goes
    through, the swap runs and the channel is no longer resubmitting."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, "CANCELLED")
    holder = await block_queue(league, lane="league")

    message = await _type(league, RACE_PASTE)

    [turn] = [change for change in await _changes(league, TURN_KIND)
              if change["state"] == "DONE"][-1:]
    assert turn["lane"] == LANE
    [swap] = await _changes(league, SWAP_KIND)
    assert swap["state"] == "QUEUED"
    assert (await _row(league))["resubmitting"] == 1
    assert MARK in message.marks

    holder["fail"] = False
    await retry_job(league.bot, lane="league")

    assert (await _row(league))["resubmitting"] == 0
    assert MARK not in message.marks
