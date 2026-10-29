"""Restarting a round's result submission after the results turned out to be wrong.

Issue #208. `enter_resubmit_flow` is what a league manager reaches by pressing **Resubmit** in
the penalty review, and it was unexecuted. It throws away every penalty staged against the
round's results and asks for the results again.

**Everything discarded is logged before it is discarded.** The staged penalties are written
into the calculation log with the driver, session, type and seconds of each — because once they
are gone there is no other record that they were ever staged, and a manager who pressed the
button by mistake would otherwise have no way to reconstruct what they had been reviewing.
`test_the_discarded_penalties_are_logged_in_full` is what holds that, and the ordering matters:
the log is written while `state.staged` is still populated.

**A failure to log must not block the resubmission.** Both audit writes are wrapped, because
the log channel may be missing or unreachable and a manager who has decided the results are
wrong must still be able to replace them. The alternative — refusing to resubmit because the
audit failed — would leave the round stuck with results the league knows to be wrong.

**The old results are superseded, not deleted** (issue #210). Pressing the button deleted the
round's results on the spot, and the collection that should have replaced them never ran. The
results now stand, published and counted, until every session has been entered again;
`test_resubmission_changes.py::test_resubmit_is_acknowledged_and_takes_the_prompt_down_before_announcing`
holds that. The round stays in penalty review
and the channel is flagged `resubmitting`, which lets the pastes past the review channel's
message guard. The review prompt is taken down, and any approval message with it (#402), so
nobody can approve the results being replaced or start a second resubmission over the first.

**The press is a change on the queue** (#439, slice 6b, C51). Pressing Resubmit asks
`results.resubmission.start` with its interaction, acknowledged privately as under way; its jobs
flag the channel, take the prompt and the approval message down, and post the announcement with
its Cancel button. These cases press the button on round 3 of Pro, its review prompt posted
through the queue on `test_review_open_change.py`'s league (its Feature Race won by Lewis ahead
of Max, its review open in submission channel 702), run the queue, and read what it did; the
resubmission's pastes are `test_resubmission_changes.py`'s and `test_resubmission_collection.py`'s.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
# The results pipeline's session types, not the weather module's — `penalty_service`
# and `result_submission_service` both import this one, and these are the values
# `session_results.session_type` actually holds.
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.result_submission_service import (
    ResubmissionCancelView,
)
from tests.results.test_resubmission_changes import (
    _announcement,
    _reviewing,
    _sessions,
)
from tests.results.test_review_open_change import (
    SUBMISSION_CHANNEL,
    _channel_row,
    _prompt_view,
    _prompts,
)
from tests.support.change_queue import (
    MEMBER_ID,
    http_error,
    stopped_job,
)
from tests.support.review_league import APPROVAL

SERVER_ID = 9908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
SUBMISSION_CHANNEL_ID = 770501
ACTOR_ID = 77
DRIVER_A = 4001
ANNOUNCEMENT_ID = 880210


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, results: int = 2) -> str:
    db_path = os.path.join(str(tmp_path), "resubmit.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        # One row per session type: `session_results` is unique on (round, session type),
        # which is the shape a real round has — a qualifying and a race, not two races.
        for index, session_type in enumerate(
            ("FEATURE_QUALIFYING", "FEATURE_RACE")[:results], start=1
        ):
            await db.execute(
                "INSERT INTO session_results "
                "(id, round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, ?, 'ACTIVE')",
                (index, ROUND_ID, DIVISION_ID, session_type),
            )
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, in_penalty_review, results_posted, created_at) "
            "VALUES (?, ?, 1, 1, ?)",
            (
                ROUND_ID,
                SUBMISSION_CHANNEL_ID,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return db_path


def _state(
    db_path: str, *, staged=(), pardons=(), channel=None, prompt_message_id=None,
    approval_message_id=None,
):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.get_channel = MagicMock(return_value=channel)
    return SimpleNamespace(
        bot=bot,
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        round_number=3,
        division_name="Division 1",
        submission_channel_id=SUBMISSION_CHANNEL_ID,
        staged=list(staged),
        staged_pardons=list(pardons),
        prompt_message_id=prompt_message_id,
        approval_message_id=approval_message_id,
    )


async def _flags(db_path: str) -> tuple[int, int, int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT in_penalty_review, results_posted, resubmitting "
            "FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return row["in_penalty_review"], row["results_posted"], row["resubmitting"]


def _penalty(seconds: int = 5, penalty_type: str = "TIME") -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=DRIVER_A,
        session_type=SessionType.FEATURE_RACE,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


def _pardon(pardon_type: str = "ABSENT", justification: str = "Ill"):
    from leaguebot.results.services.penalty_wizard import StagedPardon

    return StagedPardon(
        driver_user_id=DRIVER_A, driver_profile_id=31, attendance_id=41,
        pardon_type=pardon_type, justification=justification, grantor_id=ACTOR_ID,
    )


async def _press(tmp_path, monkeypatch, *, staged=(), pardons=(), approval: bool = False,
                 prompt_gone: bool = False):
    """Round 3's review prompt standing, with *staged* penalties and *pardons* in its review and,
    where *approval*, its approval message (8901) standing too; where *prompt_gone*, the prompt
    was deleted by hand. Alex, a league manager, presses Resubmit and the queue is run. Gives the
    league, the review's state and the press's interaction."""
    league = await _reviewing(tmp_path, monkeypatch)
    state = _prompt_view(league).state
    state.staged[:] = list(staged)
    state.staged_pardons[:] = list(pardons)
    if approval:
        state.approval_message_id = APPROVAL
    view = _prompt_view(league)
    if prompt_gone:
        [prompt] = _prompts(league)
        league.channel(SUBMISSION_CHANNEL).messages.pop(prompt)
    interaction = await _click(league, view)
    return league, state, interaction


async def _click(league, view):
    """Alex, a league manager, presses Resubmit on *view* in channel 702; then the queue is run.
    Gives the press's interaction."""
    from tests.support.change_queue import member_interaction, run_queue, tier_member

    interaction = member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001")
    )
    interaction.channel_id = SUBMISSION_CHANNEL
    button = next(item for item in view.children
                  if getattr(item, "custom_id", None) == "pw_resubmit")
    await button.callback(interaction)
    await run_queue(league.bot)
    return interaction


def _plain(line: str) -> str:
    """A log channel's line as written: the mentions the router wraps in backticks unwrapped,
    and the rule it closes each entry with dropped."""
    kept = [part for part in line.splitlines() if part.strip("―")]
    return "\n".join(kept).replace("`<@", "<@").replace(">`", ">")


async def _flags_of(league) -> tuple[int, int, int]:
    row = await _channel_row(league.db_path)
    return row["in_penalty_review"], row["results_posted"], row["resubmitting"]


def _lines(league) -> list[str]:
    return [_plain(line) for line in league.bot.log_channel.sent
            if "RESULTS_RESUBMISSION" in line]


# ---------------------------------------------------------------------------
# What is kept, and what is thrown away
# ---------------------------------------------------------------------------


async def test_the_approval_message_is_taken_down_when_resubmission_starts(tmp_path, monkeypatch):
    """**#402, as reported.** The prompt went and the approval message stayed, and its Approve
    finalised the round on the results the manager had just said were wrong. With the approval
    message (8901) standing, the queue deletes it with the prompt and the review forgets it."""
    league, state, _interaction = await _press(tmp_path, monkeypatch, approval=True)

    assert ("delete", SUBMISSION_CHANNEL, APPROVAL) in league.events
    assert APPROVAL not in league.channel(SUBMISSION_CHANNEL).messages
    assert state.approval_message_id is None


async def test_a_prompt_already_gone_does_not_stop_the_resubmission(tmp_path, monkeypatch):
    """The review prompt was deleted by hand before the press. Its deletion completes, the
    queue does not stop, and the channel is flagged resubmitting."""
    league, _state, _interaction = await _press(tmp_path, monkeypatch, prompt_gone=True)

    assert await stopped_job(league.db_path) is None
    assert await _flags_of(league) == (1, 1, 1)


async def test_the_staged_penalties_are_discarded(tmp_path, monkeypatch):
    """They were staged against results that no longer exist. Two penalties staged, Resubmit
    pressed and the queue run: the review holds none."""
    league, state, _interaction = await _press(
        tmp_path, monkeypatch, staged=[_penalty(), _penalty(10)]
    )

    assert state.staged == []


async def test_the_staged_pardons_are_discarded(tmp_path, monkeypatch):
    """The log says they are, and the state outlives the prompt: an approval message still
    standing holds it, and must not grant what the resubmission threw away (#356)."""
    league, state, _interaction = await _press(tmp_path, monkeypatch, pardons=[_pardon()])

    assert state.staged_pardons == []


# ---------------------------------------------------------------------------
# What is written down first
# ---------------------------------------------------------------------------


async def test_the_discarded_penalties_are_logged_in_full(tmp_path, monkeypatch):
    """Once they are gone there is no other record they were ever staged, so a manager who
    pressed the button by mistake could not reconstruct what they had been reviewing. With a
    5-second penalty and a disqualification staged, the log channel's line counts and names
    both."""
    league, _state, _interaction = await _press(
        tmp_path, monkeypatch, staged=[_penalty(5), _penalty(None, "DSQ")]
    )

    logged = "\n".join(_lines(league))
    assert "RESULTS_RESUBMISSION | Started" in logged
    assert "discarded_count: 2" in logged
    assert str(DRIVER_A) in logged
    assert "DSQ" in logged


async def test_the_discard_log_is_machine_readable(tmp_path, monkeypatch):
    """It is JSON so the detail survives being read back, rather than being a sentence
    somebody has to parse by eye."""
    league, _state, _interaction = await _press(tmp_path, monkeypatch, staged=[_penalty(5)])

    line = next(
        l for l in "\n".join(_lines(league)).splitlines() if l.strip().startswith("discarded:")
    )
    payload = json.loads(line.split("discarded:", 1)[1].strip())
    assert payload[0]["driver_user_id"] == DRIVER_A
    assert payload[0]["penalty_seconds"] == 5


async def test_the_discarded_pardons_are_logged_in_full(tmp_path, monkeypatch):
    """The entry listed the penalties it threw away and not the pardons, which went with them
    unrecorded (#356). They are written as the penalties are, count and detail."""
    league, _state, _interaction = await _press(
        tmp_path, monkeypatch, staged=[_penalty(5)],
        pardons=[_pardon("ABSENT", "Ill"), _pardon("NO_RSVP", "Power cut")],
    )

    logged = "\n".join(_lines(league))
    assert "discarded_count: 1" in logged
    assert "discarded_pardons_count: 2" in logged
    line = next(
        l for l in logged.splitlines() if l.strip().startswith("discarded_pardons:")
    )
    payload = json.loads(line.split("discarded_pardons:", 1)[1].strip())
    assert payload == [
        {"driver_user_id": DRIVER_A, "pardon_type": "ABSENT", "justification": "Ill"},
        {"driver_user_id": DRIVER_A, "pardon_type": "NO_RSVP", "justification": "Power cut"},
    ]


async def test_the_resubmission_counts_the_pardons_it_discarded(tmp_path, monkeypatch):
    """Resubmit pressed with one pardon staged and no penalty: the resubmission's one line
    counts no penalty and one pardon discarded."""
    league, _state, _interaction = await _press(tmp_path, monkeypatch, pardons=[_pardon()])

    started = next(line for line in _lines(league) if "RESULTS_RESUBMISSION | Started" in line)
    assert "discarded_count: 0" in started
    assert "discarded_pardons_count: 1" in started


async def test_resubmit_writes_one_line_naming_what_it_discarded(tmp_path, monkeypatch):
    """Alex presses Resubmit on round 3 (Pro) with a 5-second penalty and an ABSENT pardon
    justified "Ill" staged: once the queue has run, the log channel holds one resubmission line,
    "Alex (<@…>) | RESULTS_RESUBMISSION | Started", which names the discarded penalty and the
    discarded pardon with its justification."""
    league, _state, _interaction = await _press(
        tmp_path, monkeypatch, staged=[_penalty(5)], pardons=[_pardon()]
    )

    lines = _lines(league)
    assert len(lines) == 1, lines
    [line] = lines
    assert line.startswith(f"Alex (<@{MEMBER_ID}>) | RESULTS_RESUBMISSION | Started")
    discarded = json.loads(
        next(l for l in line.splitlines() if l.strip().startswith("discarded:"))
        .split("discarded:", 1)[1]
    )
    assert [(d["driver_user_id"], d["penalty_seconds"]) for d in discarded] == [(DRIVER_A, 5)]
    pardons = json.loads(
        next(l for l in line.splitlines() if l.strip().startswith("discarded_pardons:"))
        .split("discarded_pardons:", 1)[1]
    )
    assert pardons == [{"driver_user_id": DRIVER_A, "pardon_type": "ABSENT", "justification": "Ill"}]


async def test_a_resubmission_with_nothing_staged_still_logs(tmp_path, monkeypatch):
    """A manager may resubmit because the results were wrong rather than because a penalty
    was — the round still changed and the log still has to say so."""
    league, _state, _interaction = await _press(tmp_path, monkeypatch)

    assert "discarded_count: 0" in "\n".join(_lines(league))
    assert "RESULTS_RESUBMISSION | Started" in "\n".join(_lines(league))


async def test_a_failing_log_does_not_block_the_resubmission(tmp_path, monkeypatch):
    """The log channel may be missing or unreachable, and a manager who has decided the
    results are wrong must still be able to replace them — refusing would leave the round
    stuck with results the league knows to be wrong. The log channel refuses every post: once
    the queue has run the press, the results stand and the channel is flagged resubmitting."""
    from unittest.mock import AsyncMock as _AsyncMock

    league = await _reviewing(tmp_path, monkeypatch)
    _prompt_view(league).state.staged[:] = [_penalty()]
    league.bot.log_channel.send = _AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Access")
    )

    await _click(league, _prompt_view(league))

    assert await _sessions(league) == [("FEATURE_RACE", "ACTIVE")]
    assert await _flags_of(league) == (1, 1, 1)


# ---------------------------------------------------------------------------
# What the league is told
# ---------------------------------------------------------------------------


async def test_the_division_is_told_in_the_submission_channel(tmp_path, monkeypatch):
    """The division is about to be asked for results again; without this the request
    arrives with no explanation. Once the queue has run the press, the announcement stands in
    channel 702."""
    league, _state, _interaction = await _press(tmp_path, monkeypatch)

    announcement = str(_announcement(league).content)
    assert "resubmission started" in announcement.lower()
    assert "stand until every session has been entered again" in announcement


# ---------------------------------------------------------------------------
# Every refusal is recorded (#482)
#
# The core specification's "The record of what changed": a refusal replies as today and writes
# one line naming the member, what was refused and why. The Resubmit button's own checks are
# driven with the rest of the penalty review's in `test_penalty_review_buttons.py`; these are the
# two refusals the resubmission itself makes.
# ---------------------------------------------------------------------------


def _pressing_interaction(*, deferred: bool):
    """A press by Alex (id 77), answering as Discord's does — done once it replies or defers —
    and connected to a log channel of its own. Alex holds no league manager tier: the user is
    not a `discord.Member`, which the penalty review's permission check refuses outright."""
    answered = {"done": deferred}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Alex"
    interaction.client = MagicMock()
    interaction.client.output_router = MagicMock()
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize(
    "press, reply",
    [
        pytest.param(
            "resubmit",
            "❌ The submission channel could not be found, so the results cannot be resubmitted.",
            id="resubmit-with-the-submission-channel-gone",
        ),
        pytest.param(
            "cancel",
            "⛔ Only league managers can interact with the penalty review.",
            id="cancel-by-somebody-without-the-tier",
        ),
    ],
)
async def test_every_refused_press_of_the_resubmission_is_recorded(
    tmp_path, monkeypatch, press, reply,
):
    """Round 3 of Division 1 is in penalty review, one penalty staged. Alex presses 🔄 Resubmit
    Initial Results after the submission channel was deleted by hand, or presses the Cancel
    button of a resubmission under way without holding the league manager tier. The reply is
    today's, nothing changes — the penalty stays staged, the channel is not flagged and the
    resubmission is not cancelled — and exactly one line records the refusal, naming Alex and
    the reason. With nowhere to collect in, the review is all the round has, so a Resubmit with
    its channel gone keeps it whole. The Resubmit press is made on the review of `test_review_open_change.py`'s
    league, its queue run after it (#439, slice 6b)."""
    if press == "resubmit":
        league = await _reviewing(tmp_path, monkeypatch)
        state = _prompt_view(league).state
        state.staged[:] = [_penalty()]
        view = _prompt_view(league)
        league.channels.pop(SUBMISSION_CHANNEL)
        interaction = await _click(league, view)
        flags = await _flags_of(league)
        lines = [_plain(line) for line in league.bot.log_channel.sent if line.startswith("⛔ ")]
        actor = MEMBER_ID
    else:
        db_path = await _make_db(tmp_path)
        state = _state(db_path, staged=[_penalty()], channel=None)
        state.db_path = db_path
        view = ResubmissionCancelView(state)
        interaction = _pressing_interaction(deferred=False)
        await view.cancel_btn.callback(interaction)
        assert not view.cancelled
        assert not view.pressed.is_set()
        flags = await _flags(db_path)
        lines = [
            str(call.args[0])
            for router in (state.bot.output_router, interaction.client.output_router)
            for call in router.post_log.await_args_list
        ]
        actor = ACTOR_ID

    assert len(state.staged) == 1
    assert flags == (1, 1, 0)
    (replied,) = [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]
    assert replied == reply
    reason = reply.split(" ", 1)[1]
    (line,) = lines
    assert line.startswith("⛔ "), line
    if press == "cancel":
        assert line.startswith("⛔ the “Cancel” button"), line
    assert line.endswith(f" refused for Alex (<@{actor}>) — {reason}"), line
