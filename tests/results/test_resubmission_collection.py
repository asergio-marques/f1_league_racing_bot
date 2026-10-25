"""Collecting a round's results again after a league manager presses Resubmit (#439, slice 6b).

Issue #208 found the resubmission's collection 99% untested, and issue #210 found why nothing had
noticed it never worked: its tests patched in a round context the real query did not select.
**Every test here reads the round from the database**: nothing of the round is patched.

The collection mirrors the first submission: sessions in racing order, a refused paste answered
and the same session asked again, a fastest-lap override that must name a finisher. It differs
from the first submission in two ways, and both are deliberate:

- **It reuses the existing channel.** The league manager pressed Resubmit inside it, and it
  already holds the review's history.
- **Nothing is written until the last session is in.** Each paste is saved as a draft of the
  resubmission, and the last hands the swap of the round's results to the league. Team agreement
  across sessions is checked against those drafts, not the stored results being replaced.

Since #439's slice 6b the press asks `results.resubmission.start`, and each paste is a
`results.resubmission.turn` on the round's lane; `test_resubmission_changes.py` pins those
changes' jobs, lanes and restarts. This file pins the collection's own rules through them: the
press, the cog hearing each paste, and the queue run after each. Cancel ends the resubmission,
asking the review back on the queue as cancelled, publishing nothing, with the earlier results
standing. The league, its channels and its queue are `test_resubmission_changes.py`'s.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundFormat
from leaguebot.results.services import penalty_wizard, result_submission_service
from leaguebot.results.services.result_submission_service import (
    DivisionValidationData,
    get_sessions_for_format,
)
from tests.results.test_resubmission_changes import (
    QUALIFYING_TYPE,
    RACE_PASTE,
    RACE_TYPE,
    REVIEW_OPEN,
    ROUND_ID,
    SWAP_KIND,
    _announcement,
    _changes,
    _drafts,
    _manager,
    _press_resubmit,
    _reviewing,
    _row,
    _sessions,
    _texts,
    _type,
)
from tests.results.test_review_open_change import SUBMISSION_CHANNEL, _prompts
from tests.support.change_queue import (
    MEMBER_ID,
    acknowledgement,
    member_interaction,
    queued_log_lines,
    retry_job,
    run_queue,
    tier_member,
    updated_reply,
)
from tests.support.review_league import ReviewLeague, block_queue
from tests.support.teams import seed_team_instances

AMENDED = "Provisional Results (amended)"
AMENDED_ROUND = 20
AMEND_CHANNEL = 8200
QUALI_PASTE = "1, <@102>, FER, Soft, 1:19.000, N/A\n2, <@101>, FER, Soft, 1:19.500, +0.500"


def _paste_for(session: Any) -> str:
    return QUALI_PASTE if "QUALIFYING" in session.value else RACE_PASTE


async def _write(league: ReviewLeague, sql: str, *args: Any) -> None:
    async with get_connection(league.db_path) as db:
        await db.execute(sql, args)
        await db.commit()


async def _open_amendment(league: ReviewLeague) -> None:
    """Round 2 of Pro, final, is being amended in channel 8200."""
    async with get_connection(league.db_path) as db:
        [division_id] = await (await db.execute(
            "SELECT division_id FROM rounds WHERE id = ?", (ROUND_ID,)
        )).fetchone()
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (AMENDED_ROUND, division_id),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-01T00:00:00+00:00')",
            (AMENDED_ROUND, AMEND_CHANNEL),
        )
        await db.commit()


async def _press_cancel(league: ReviewLeague, *, user: Any = None) -> Any:
    """*user*, the manager by default, presses Cancel on the resubmission's announcement; then
    the queue is run. Gives the press's interaction."""
    view = _announcement(league).view
    interaction = member_interaction(league.bot, user=user or _manager())
    interaction.channel_id = SUBMISSION_CHANNEL
    button = next(item for item in view.children if getattr(item, "label", None) == "Cancel")
    await button.callback(interaction)
    await run_queue(league.bot)
    return interaction


async def _review_asked(league: ReviewLeague) -> dict[str, Any]:
    """The last request to open round 3's review: its payload, with the change's `origin` and
    `actor_id` beside it."""
    import json

    last = (await _changes(league, REVIEW_OPEN))[-1]
    return {**json.loads(last["payload"]), "origin": last["origin"],
            "actor_id": last["actor_id"]}


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


async def test_each_session_is_saved_again(tmp_path, monkeypatch):
    """Alex presses Resubmit on round 3 (Pro, Normal) and types Qualifying's results, then the
    Feature Race's. Once the queue has run, the round holds both sessions anew."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    await _type(league, QUALI_PASTE)
    await _type(league, RACE_PASTE)

    assert await _sessions(league) == [
        (QUALIFYING_TYPE.value, "ACTIVE"), (RACE_TYPE.value, "ACTIVE"),
    ]


async def test_a_sprint_round_asks_for_all_four_sessions(tmp_path, monkeypatch):
    """Round 3 is a Sprint round. Alex resubmits and types each of its four sessions in racing
    order: the round then holds all four, in that order."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _write(league, "UPDATE rounds SET format = 'SPRINT' WHERE id = ?", ROUND_ID)
    sessions = get_sessions_for_format(RoundFormat.SPRINT)
    await _press_resubmit(league)

    for session in sessions:
        await _type(league, _paste_for(session))

    assert [session for session, _status in await _sessions(league)] == [
        session.value for session in sessions
    ]


async def test_the_round_goes_back_to_penalty_review_as_a_resubmission(tmp_path, monkeypatch):
    """Once the last session is in, the round's review is asked again, publishing the new
    results as "Provisional Results (amended)", so the tables it posts say they replace the
    earlier ones."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    await _type(league, QUALI_PASTE)
    await _type(league, RACE_PASTE)

    back = await _review_asked(league)
    assert back["round_id"] == ROUND_ID
    assert back["label"] == AMENDED
    assert back["publish"] is True
    assert not back.get("returning")


async def test_a_refused_paste_is_answered_and_the_session_asked_again(tmp_path, monkeypatch):
    """Alex types "garbage" for Qualifying. Its turn answers in channel 702 that validation
    failed, and nothing is saved; Qualifying's and the race's results typed after it are taken."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    await _type(league, "garbage")

    assert any("Validation failed" in text for text in _texts(league))
    assert await _drafts(league) == []

    await _type(league, QUALI_PASTE)
    await _type(league, RACE_PASTE)

    assert [session for session, _status in await _sessions(league)] == [
        QUALIFYING_TYPE.value, RACE_TYPE.value,
    ]


async def test_a_fastest_lap_override_naming_a_non_finisher_is_refused(tmp_path, monkeypatch):
    """Qualifying is taken; the race is then typed with a fastest-lap override naming <@999>,
    who is not in it. The paste is refused naming the override, and only Qualifying is held."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, QUALI_PASTE)

    await _type(league, "FL: <@999>\n" + RACE_PASTE)

    assert any("FL override <@999> is not in the submitted results" in text
               for text in _texts(league))
    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "ACTIVE")]


async def test_every_session_cancelled_closes_the_channel(tmp_path, monkeypatch):
    """Alex types CANCELLED for both sessions. The resubmission closes channel 702 rather than
    asking the review again, and round 3 ends CANCELLED from awaiting its report verdicts, marked
    as not held (#439, slice 8, Part E; core :232-234)."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    asked_before = len(await _changes(league, REVIEW_OPEN))

    await _type(league, "CANCELLED")
    await _type(league, "CANCELLED")

    assert ("delete_channel", SUBMISSION_CHANNEL, SUBMISSION_CHANNEL) in league.events
    assert len(await _changes(league, REVIEW_OPEN)) == asked_before
    async with get_connection(league.db_path) as db:
        [row] = await (await db.execute(
            "SELECT status, not_held FROM rounds WHERE id = ?", (ROUND_ID,)
        )).fetchall()
    assert (row["status"], row["not_held"]) == ("CANCELLED", 1)


# ---------------------------------------------------------------------------
# The earlier results stand until the new ones replace them
# ---------------------------------------------------------------------------


async def test_the_earlier_results_stand_until_the_last_session_is_in(tmp_path, monkeypatch):
    """**Issue #210.** A resubmission supersedes the round's results rather than deleting them
    first. With Qualifying typed, the round still holds exactly its earlier Feature Race, and
    Qualifying is a draft; once the race is in, the round holds both."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    await _type(league, QUALI_PASTE)

    assert await _sessions(league) == [(RACE_TYPE.value, "ACTIVE")]
    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "ACTIVE")]

    await _type(league, RACE_PASTE)

    assert [session for session, _status in await _sessions(league)] == [
        QUALIFYING_TYPE.value, RACE_TYPE.value,
    ]


async def test_team_agreement_is_checked_against_the_resubmission_not_the_old_results(
    tmp_path, monkeypatch,
):
    """The stored race has Lewis under team 9999, the wrong team that made the manager
    resubmit. Alex resubmits both sessions with Lewis under FER: neither paste is refused, and
    the swap goes through, since agreement is checked against the resubmission alone."""
    league = await _reviewing(tmp_path, monkeypatch)
    async with get_connection(league.db_path) as db:
        await seed_team_instances(db, 11, 9999)
        await db.execute(
            "UPDATE race_session_results SET team_instance_id = 9999 WHERE driver_user_id = 101"
        )
        await db.commit()
    await _press_resubmit(league)

    await _type(league, QUALI_PASTE)
    await _type(league, RACE_PASTE)

    assert not any("Validation failed" in text for text in _texts(league))
    assert [swap["state"] for swap in await _changes(league, SWAP_KIND)] == ["DONE"]


async def test_a_team_disagreement_within_the_resubmission_is_refused(tmp_path, monkeypatch):
    """Lewis is a reserve, so no seat pins his team: only the Qualifying just typed does, under
    FER. The race typed with him under MCL is refused, saying where he was recorded, and only
    Qualifying is held."""
    league = await _reviewing(tmp_path, monkeypatch)
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=DivisionValidationData(
            division_driver_ids={101, 102}, team_of_role={}, reserve_team_role_id=None,
            driver_team_map={102: 3001}, reserve_driver_ids={101},
            team_names={3001: "Team 3001", 3002: "Team 3002"},
            team_of_shorthand={"fer": 3001, "mcl": 3002},
        )),
    )
    await _press_resubmit(league)
    await _type(league, QUALI_PASTE)

    await _type(league, RACE_PASTE.replace("<@101>, FER", "<@101>, MCL"))

    assert any("was recorded under" in text for text in _texts(league))
    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "ACTIVE")]


# ---------------------------------------------------------------------------
# Nothing is entered while another round of the division is being amended (#345)
# ---------------------------------------------------------------------------
#
# Decided 2026-09-21, as for the first submission: a resubmission posts its provisional standings
# from a database holding the amendment's unapproved corrections. Its pastes are refused while
# one is open, the last of them being what swaps the results in.


async def test_a_resubmitted_paste_is_refused_while_another_round_is_amended(
    tmp_path, monkeypatch,
):
    """Round 2 of Pro is being amended when Alex types Qualifying: the paste is refused, saying
    so, and nothing is held. Once the amendment has ended, Qualifying typed again is taken."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _open_amendment(league)

    await _type(league, QUALI_PASTE)

    assert any("is being amended" in text for text in _texts(league))
    assert await _drafts(league) == []

    await _write(league, "DELETE FROM round_amend_channels")
    await _type(league, QUALI_PASTE)

    assert await _drafts(league) == [(QUALIFYING_TYPE.value, "ACTIVE")]


async def test_a_resubmitted_paste_held_by_an_amendment_is_logged(tmp_path, monkeypatch):
    """Round 2 of Pro is being amended when Alex types Qualifying for round 3's resubmission:
    the paste is refused, and one `RESULT_SUBMISSION_REJECTED` line is logged naming season 1,
    Pro, round 3 and the qualifying, the amendment as the reason, and the paste as typed
    (results :261)."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _open_amendment(league)

    await _type(league, QUALI_PASTE)

    logged = [*await queued_log_lines(league.db_path), *league.bot.log_channel.sent]
    [line] = [line.replace("`<@", "<@").replace(">`", ">") for line in logged
              if "RESULT_SUBMISSION_REJECTED" in line]
    assert "season: 1, division: 'Pro'" in line, line
    assert f"round: 3, session: {QUALIFYING_TYPE.value}" in line, line
    assert "reason:" in line and "is being amended" in line, line
    assert QUALI_PASTE in line, line


async def test_the_last_paste_refused_leaves_the_earlier_results_standing(tmp_path, monkeypatch):
    """Qualifying is taken; round 2's amendment then opens, and the race typed is refused. The
    round still holds its earlier results, and no swap is asked."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, QUALI_PASTE)
    await _open_amendment(league)

    await _type(league, RACE_PASTE)

    assert any("is being amended" in text for text in _texts(league))
    assert await _sessions(league) == [(RACE_TYPE.value, "ACTIVE")]
    assert await _changes(league, SWAP_KIND) == []


async def test_cancelling_a_resubmitted_session_is_refused_while_amended(tmp_path, monkeypatch):
    """Round 2 is being amended when Alex types CANCELLED for Qualifying: refused, nothing
    held."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _open_amendment(league)

    await _type(league, "CANCELLED")

    assert any("is being amended" in text for text in _texts(league))
    assert await _drafts(league) == []


# ---------------------------------------------------------------------------
# Cancelling the resubmission
# ---------------------------------------------------------------------------


async def test_the_button_is_labelled_cancel(tmp_path, monkeypatch):
    """The resubmission's announcement carries one button, Cancel."""
    league = await _reviewing(tmp_path, monkeypatch)

    await _press_resubmit(league)

    assert [child.label for child in _announcement(league).view.children] == ["Cancel"]


async def test_cancelling_the_resubmission_keeps_the_earlier_results(tmp_path, monkeypatch):
    """Alex types Qualifying, then presses Cancel. The round keeps its earlier results and the
    channel is no longer resubmitting."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, QUALI_PASTE)

    await _press_cancel(league)

    assert await _sessions(league) == [(RACE_TYPE.value, "ACTIVE")]
    assert (await _row(league))["resubmitting"] == 0


async def test_a_resubmission_started_again_after_a_cancel_asks_for_the_first_session(
    tmp_path, monkeypatch,
):
    """Alex presses Resubmit, pastes Qualifying, then presses Cancel. The attempt's draft is gone
    with it. Pressed again, Resubmit asks for Qualifying afresh and holds no draft."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, QUALI_PASTE)
    assert await _drafts(league) != []
    await _press_cancel(league)

    assert await _drafts(league) == []
    asked = sum("Submit **" in text for text in _texts(league))

    await _press_resubmit(league)

    assert await _drafts(league) == []
    assert sum("Submit **Qualifying" in text for text in _texts(league)) >= 2
    assert sum("Submit **" in text for text in _texts(league)) > asked


async def test_cancelling_the_resubmission_returns_the_round_to_penalty_review(
    tmp_path, monkeypatch,
):
    """The results were never touched and are already posted, so Cancel asks the review back
    as cancelled without publishing them again, in Alex's name, naming the announcement for
    the review's own jobs to take its button down; a review prompt then stands again."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    announcement = _announcement(league)

    await _press_cancel(league)

    back = await _review_asked(league)
    assert back["returning"] == "cancelled"
    assert back["publish"] is False
    assert back["cancel_message_id"] == announcement.id
    assert back["origin"] == "MEMBER"
    assert int(back["actor_id"]) == MEMBER_ID
    assert back.get("label") != AMENDED
    assert len(_prompts(league)) == 1


async def test_cancelling_the_resubmission_tells_the_manager_the_earlier_results_stand(
    tmp_path, monkeypatch,
):
    """Alex presses Cancel on round 3's resubmission. His private acknowledgement is updated to
    say the resubmission of round 3 was cancelled and the earlier results stand, naming the
    round by its number and not by the database's id (results :410)."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)

    interaction = await _press_cancel(league)

    reply = updated_reply(interaction)
    assert "Resubmission of round 3" in reply
    assert "cancelled. The earlier results stand." in reply
    assert "penalty review is open" not in reply


async def test_a_cancel_pressed_behind_the_last_paste_s_swap_finds_the_resubmission_ended(
    tmp_path, monkeypatch,
):
    """The league lane is stopped at another job when Alex types the last resubmitted paste, so
    the swap waits. He presses Cancel meanwhile; the cancel's review opening queues behind the
    swap. Once the lane is retried the swap replaces the results, and the cancel finds the
    resubmission ended: Alex is told so, the log carries no 'earlier results stand' line, and the
    round holds the resubmitted sessions."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    await _type(league, "CANCELLED")
    holder = await block_queue(league, lane="league")
    await _type(league, RACE_PASTE)
    interaction = await _press_cancel(league)

    holder["fail"] = False
    await retry_job(league.bot, lane="league")
    await run_queue(league.bot)

    assert (QUALIFYING_TYPE.value, "CANCELLED") in await _sessions(league)
    assert (await _row(league))["resubmitting"] == 0
    assert "already ended" in updated_reply(interaction)
    assert not any("earlier results stand" in text.lower() for text in _texts(league))


async def test_cancel_refuses_somebody_without_the_tier(tmp_path, monkeypatch):
    """A member who is not a league manager presses Cancel: they are told only league managers
    may, the review is not asked back, and the resubmission goes on."""
    league = await _reviewing(tmp_path, monkeypatch)
    await _press_resubmit(league)
    asked_before = len(await _changes(league, REVIEW_OPEN))
    monkeypatch.setattr(penalty_wizard, "_is_league_manager", AsyncMock(return_value=False))
    monkeypatch.setattr(penalty_wizard, "is_league_manager", lambda config, member: False)

    interaction = await _press_cancel(league, user=tier_member(None, member_id=5))

    assert "Only league managers" in acknowledgement(interaction)
    assert len(await _changes(league, REVIEW_OPEN)) == asked_before
    assert (await _row(league))["resubmitting"] == 1
