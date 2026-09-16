"""Moving a round into penalty review once its results are in.

Issue #208. `enter_penalty_state` is eighty-five statements and was uncovered. It is the hinge
between "results submitted" and "stewards reviewing", and almost everything it does is ordered
the way it is for a reason a later reader would otherwise tidy away.

**The flag goes up before anything is posted.** A crash during posting has to look like a
penalty-review orphan on the next restart, not a mid-submission one — the mid-submission
recovery path *deletes the session results and skips the round*, so getting this order wrong
loses a division's race. `test_the_review_flag_is_set_before_any_posting` is written to fail if
the write moves after the posting block.

**The round's status moves in the same transaction as the flag**, for the same reason: a crash
between the two would leave the round and its channel contradicting each other. And it moves
only out of a cancellable state, so a resubmission cannot drag a round that has reached appeals,
or ended, backwards.

**A round in review can no longer be cancelled.** The drivers have reports and appeals to lodge,
and calling the round off would take that from them. The status change is what enforces it, and
`ROUND_CANCELLABLE` is read from the model here rather than restated so the two cannot drift.

**Posting is best-effort; the review is not.** If the interim results fail to post, the review
still opens — the stewards' work does not depend on the division having seen a provisional
table, and refusing to open the review would strand the round with its results in and nobody
able to act on them. `results_posted` stays 0 in that case, which is what makes the next restart
try again.

**Recovery skips the posting and nothing else.** `skip_results_post` exists for a restart that
crashed *after* posting; passing it must not skip the flag, the status, or the prompt.

**The sessions offered to the stewards are the active ones, in racing order.** A superseded
submission's sessions must not appear — they are not what the round is any more — and the order
is the enum's, because a sprint reviewed feature-first reads as a different race.

**The prompt's message id is persisted.** Recovery deletes the old prompt before posting a new
one, and without the id it cannot; the result is two live prompts and two sets of staged
penalties for one round.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from models.round import ROUND_CANCELLABLE, RoundStatus  # noqa: E402
from services.result_submission_service import enter_penalty_state  # noqa: E402

SERVER_ID = 11108
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701
SUBMISSION_CHANNEL = 702
PROMPT_MESSAGE_ID = 8800


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "enter_penalty",
    round_status: str = RoundStatus.AWAITING_RESULTS.value,
    sessions=(("FEATURE_RACE", "ACTIVE"),),
    results_channel: int | None = RESULTS_CHANNEL,
    standings_channel: int | None = STANDINGS_CHANNEL,
    config_row: bool = True,
) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', "
            "'Silverstone', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00')",
            (ROUND_ID, SUBMISSION_CHANNEL),
        )
        if config_row:
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id, reserves_in_standings) VALUES (?, ?, ?, 1)",
                (DIVISION_ID, results_channel, standings_channel),
            )
        for session_type, status in sessions:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, ?)",
                (ROUND_ID, DIVISION_ID, session_type, status),
            )
        await db.commit()
    return db_path


def _bot(db_path: str):
    bot = MagicMock()
    bot.db_path = db_path
    bot.add_view = MagicMock()
    return bot


def _guild(*, channels_missing: bool = False):
    guild = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    guild.get_channel = MagicMock(return_value=None if channels_missing else channel)
    return guild


def _sub_channel():
    channel = MagicMock()
    channel.id = SUBMISSION_CHANNEL
    message = MagicMock()
    message.id = PROMPT_MESSAGE_ID
    channel.send = AsyncMock(return_value=message)
    return channel


async def _enter(db_path, *, guild=None, sub_channel=None, posting_error=None, **kwargs):
    """Run it with the posting and prompt machinery stubbed, returning the stubs."""
    bot = _bot(db_path)
    sub_channel = sub_channel or _sub_channel()

    with patch(
        "services.results_post_service.standings_display_names",
        new=AsyncMock(return_value={}),
    ), patch(
        "services.standings_service.compute_and_persist_round",
        new=AsyncMock(side_effect=posting_error),
    ) as persist, patch(
        "services.results_post_service.post_round_results", new=AsyncMock()
    ) as post_results, patch(
        "services.results_post_service.post_standings", new=AsyncMock()
    ) as post_standings, patch(
        "services.standings_service.compute_driver_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.standings_service.compute_team_standings", new=AsyncMock(return_value=[])
    ), patch(
        "services.penalty_wizard.PenaltyReviewView", new=MagicMock()
    ) as view, patch(
        "services.penalty_wizard._render_prompt_content",
        new=AsyncMock(return_value="review this"),
    ):
        await enter_penalty_state(
            bot, guild or _guild(), ROUND_ID, DIVISION_ID, sub_channel, **kwargs
        )
    return {
        "bot": bot,
        "sub_channel": sub_channel,
        "persist": persist,
        "post_results": post_results,
        "post_standings": post_standings,
        "state": view.call_args.kwargs["state"] if view.call_args else None,
    }


async def _channel_row(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT in_penalty_review, results_posted, prompt_message_id "
            "FROM round_submission_channels WHERE round_id = ?",
            (ROUND_ID,),
        )
        return dict(await cursor.fetchone())


async def _round_status(db_path) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (ROUND_ID,))
        return (await cursor.fetchone())["status"]


# ---------------------------------------------------------------------------
# The flag, the status, and their order
# ---------------------------------------------------------------------------


async def test_the_channel_is_marked_as_in_review(tmp_path):
    db_path = await _make_db(tmp_path)

    await _enter(db_path)

    assert (await _channel_row(db_path))["in_penalty_review"] == 1


async def test_the_review_flag_is_set_before_any_posting(tmp_path):
    """A crash during posting has to look like a penalty-review orphan on the next restart,
    not a mid-submission one — the mid-submission recovery path deletes the session results
    and skips the round, so this order is a division's race."""
    db_path = await _make_db(tmp_path, name="enter_flag_order")
    seen: dict[str, int] = {}

    async def _record(*_args, **_kwargs):
        seen["flag"] = (await _channel_row(db_path))["in_penalty_review"]

    with patch(
        "services.results_post_service.standings_display_names", new=AsyncMock(side_effect=_record)
    ), patch(
        "services.standings_service.compute_and_persist_round", new=AsyncMock()
    ), patch(
        "services.results_post_service.post_round_results", new=AsyncMock()
    ), patch(
        "services.results_post_service.post_standings", new=AsyncMock()
    ), patch(
        "services.standings_service.compute_driver_standings", new=AsyncMock(return_value=[])
    ), patch(
        "services.standings_service.compute_team_standings", new=AsyncMock(return_value=[])
    ), patch(
        "services.penalty_wizard.PenaltyReviewView", new=MagicMock()
    ), patch(
        "services.penalty_wizard._render_prompt_content", new=AsyncMock(return_value="x")
    ):
        await enter_penalty_state(
            _bot(db_path), _guild(), ROUND_ID, DIVISION_ID, _sub_channel()
        )

    assert seen["flag"] == 1


async def test_the_round_now_awaits_report_verdicts(tmp_path):
    db_path = await _make_db(tmp_path, name="enter_status")

    await _enter(db_path)

    assert await _round_status(db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


@pytest.mark.parametrize("status", sorted(ROUND_CANCELLABLE))
async def test_a_cancellable_round_moves_into_review(tmp_path, status):
    """Read from the model's own frozenset: a state added to what may be cancelled and not
    to what may enter review would strand a round with its results in."""
    db_path = await _make_db(tmp_path, name=f"enter_from_{status}", round_status=status)

    await _enter(db_path)

    assert await _round_status(db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


@pytest.mark.parametrize(
    "status",
    [RoundStatus.AWAITING_APPEAL_VERDICTS.value, RoundStatus.FINAL.value],
)
async def test_a_round_past_review_is_not_dragged_backwards(tmp_path, status):
    """A resubmission calls this again, and a round that has reached appeals or ended must
    not be reopened for report verdicts that were already given."""
    db_path = await _make_db(tmp_path, name=f"enter_keep_{status}", round_status=status)

    await _enter(db_path, is_resubmission=True)

    assert await _round_status(db_path) == status


async def test_a_round_in_review_can_no_longer_be_cancelled(tmp_path):
    """The drivers have reports and appeals to lodge, and calling the round off would take
    that from them. The status is what enforces it."""
    db_path = await _make_db(tmp_path, name="enter_uncancellable")

    await _enter(db_path)

    assert await _round_status(db_path) not in ROUND_CANCELLABLE


# ---------------------------------------------------------------------------
# The interim results
# ---------------------------------------------------------------------------


async def test_the_interim_results_and_standings_are_posted(tmp_path):
    db_path = await _make_db(tmp_path, name="enter_posts")

    stubs = await _enter(db_path)

    stubs["post_results"].assert_awaited_once()
    stubs["post_standings"].assert_awaited_once()
    assert (await _channel_row(db_path))["results_posted"] == 1


async def test_the_standings_are_ordered_by_the_names_they_are_posted_under(tmp_path):
    """The stored classification and the one the league is shown cannot be allowed to
    disagree, so the display names order the snapshot as well as label it."""
    db_path = await _make_db(tmp_path, name="enter_names")

    stubs = await _enter(db_path)

    stubs["persist"].assert_awaited_once()


async def test_a_resubmission_says_so_on_both_posts(tmp_path):
    """A division seeing a second provisional table needs to know it replaces the first
    rather than adds to it."""
    db_path = await _make_db(tmp_path, name="enter_amended")

    stubs = await _enter(db_path, is_resubmission=True)

    assert stubs["post_results"].await_args.kwargs["label"] == "Provisional Results (amended)"
    assert (
        stubs["post_standings"].await_args.kwargs["label"] == "Provisional Results (amended)"
    )


async def test_a_first_submission_is_labelled_plainly(tmp_path):
    db_path = await _make_db(tmp_path, name="enter_plain")

    stubs = await _enter(db_path)

    assert stubs["post_results"].await_args.kwargs["label"] == "Provisional Results"


async def test_a_division_with_no_results_channel_posts_no_results(tmp_path):
    """Configuring one is optional, and the review has to open either way."""
    db_path = await _make_db(tmp_path, name="enter_nores", results_channel=None)

    stubs = await _enter(db_path)

    stubs["post_results"].assert_not_awaited()
    stubs["post_standings"].assert_awaited_once()


async def test_a_division_with_no_standings_channel_posts_no_standings(tmp_path):
    db_path = await _make_db(tmp_path, name="enter_nostand", standings_channel=None)

    stubs = await _enter(db_path)

    stubs["post_standings"].assert_not_awaited()
    stubs["post_results"].assert_awaited_once()


async def test_a_deleted_channel_is_stepped_over(tmp_path):
    """The id is configured but the channel has gone — ordinary on a server that has been
    reorganised, and not a reason to withhold the review."""
    db_path = await _make_db(tmp_path, name="enter_gone")

    stubs = await _enter(db_path, guild=_guild(channels_missing=True))

    stubs["post_results"].assert_not_awaited()
    stubs["sub_channel"].send.assert_awaited_once()


async def test_a_division_with_no_configuration_still_enters_review(tmp_path):
    """The LEFT JOIN finds nothing, and every posting target is absent — the review is the
    part that matters and it must not depend on the channels being set up."""
    db_path = await _make_db(tmp_path, name="enter_noconfig", config_row=False)

    stubs = await _enter(db_path)

    stubs["sub_channel"].send.assert_awaited_once()
    assert (await _channel_row(db_path))["in_penalty_review"] == 1


async def test_a_failure_to_post_does_not_stop_the_review_opening(tmp_path):
    """The stewards' work does not depend on the division having seen a provisional table,
    and refusing would strand the round with its results in and nobody able to act."""
    db_path = await _make_db(tmp_path, name="enter_post_fails")

    stubs = await _enter(db_path, posting_error=RuntimeError("Discord is down"))

    stubs["sub_channel"].send.assert_awaited_once()
    assert (await _channel_row(db_path))["in_penalty_review"] == 1


async def test_a_failure_to_post_leaves_the_results_unposted(tmp_path):
    """Which is what makes the next restart try again rather than assume it was done."""
    db_path = await _make_db(tmp_path, name="enter_post_fails_flag")

    await _enter(db_path, posting_error=RuntimeError("Discord is down"))

    assert (await _channel_row(db_path))["results_posted"] == 0


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


async def test_recovery_skips_the_posting(tmp_path):
    """Passed only where `results_posted = 1` already — posting again would put a second
    provisional table in the division's channel after every restart."""
    db_path = await _make_db(tmp_path, name="enter_skip")

    stubs = await _enter(db_path, skip_results_post=True)

    stubs["post_results"].assert_not_awaited()
    stubs["post_standings"].assert_not_awaited()


async def test_recovery_still_opens_the_review(tmp_path):
    """Skipping the posting must not skip the flag, the status or the prompt — the round is
    being recovered *into* review."""
    db_path = await _make_db(tmp_path, name="enter_skip_rest")

    stubs = await _enter(db_path, skip_results_post=True)

    stubs["sub_channel"].send.assert_awaited_once()
    assert (await _channel_row(db_path))["in_penalty_review"] == 1
    assert await _round_status(db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


async def test_a_round_that_does_not_exist_does_nothing(tmp_path):
    """A job outliving its round, and a failure here would be raised inside a restart's
    recovery loop and stop every round after it."""
    db_path = await _make_db(tmp_path, name="enter_noround")
    sub_channel = _sub_channel()

    with patch("services.penalty_wizard.PenaltyReviewView", new=MagicMock()):
        await enter_penalty_state(
            _bot(db_path), _guild(), 9999, DIVISION_ID, sub_channel
        )

    sub_channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


async def test_the_prompt_is_posted_to_the_submission_channel(tmp_path):
    """Where the stewards already are, and where the results were submitted."""
    db_path = await _make_db(tmp_path, name="enter_prompt")

    stubs = await _enter(db_path)

    stubs["sub_channel"].send.assert_awaited_once()
    assert stubs["sub_channel"].send.await_args.args[0] == "review this"


async def test_the_prompt_message_id_is_persisted(tmp_path):
    """Recovery deletes the old prompt before posting a new one; without the id it cannot,
    and the round ends up with two live prompts and two sets of staged penalties."""
    db_path = await _make_db(tmp_path, name="enter_promptid")

    await _enter(db_path)

    assert (await _channel_row(db_path))["prompt_message_id"] == PROMPT_MESSAGE_ID


async def test_the_view_is_registered_for_persistent_routing(tmp_path):
    """Its buttons have to keep working across a restart, which is what `add_view` with the
    message id is for."""
    db_path = await _make_db(tmp_path, name="enter_addview")

    stubs = await _enter(db_path)

    stubs["bot"].add_view.assert_called_once()
    assert stubs["bot"].add_view.call_args.kwargs["message_id"] == PROMPT_MESSAGE_ID


# ---------------------------------------------------------------------------
# The sessions offered to the stewards
# ---------------------------------------------------------------------------


async def test_the_rounds_active_sessions_are_offered(tmp_path):
    """A steward can only penalise a session that is in the review, so one missing here is
    a session nobody can apply a penalty to."""
    db_path = await _make_db(
        tmp_path,
        name="enter_sessions",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "ACTIVE")),
    )

    stubs = await _enter(db_path)

    assert set(stubs["state"].session_types_present) == {
        SessionType.FEATURE_RACE,
        SessionType.FEATURE_QUALIFYING,
    }


async def test_a_superseded_session_is_not_offered(tmp_path):
    """It is not what the round is any more, and penalising it would apply a penalty to
    results that were already replaced."""
    db_path = await _make_db(
        tmp_path,
        name="enter_superseded",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "SUPERSEDED")),
    )

    stubs = await _enter(db_path)

    assert stubs["state"].session_types_present == [SessionType.FEATURE_RACE]


async def test_the_sessions_are_offered_in_racing_order(tmp_path):
    """Sprint before feature, qualifying before its race. A sprint round reviewed
    feature-first reads as a different race, and the steward is working from memory of the
    evening."""
    db_path = await _make_db(
        tmp_path,
        name="enter_order",
        sessions=(
            ("FEATURE_RACE", "ACTIVE"),
            ("SPRINT_QUALIFYING", "ACTIVE"),
            ("FEATURE_QUALIFYING", "ACTIVE"),
            ("SPRINT_RACE", "ACTIVE"),
        ),
    )

    stubs = await _enter(db_path)

    assert stubs["state"].session_types_present == list(SessionType)


async def test_the_state_carries_the_round_and_division_it_describes(tmp_path):
    """Every prompt the steward sees is titled from these, and the round number is what
    tells two reviews open at once apart."""
    db_path = await _make_db(tmp_path, name="enter_state")

    stubs = await _enter(db_path)

    state = stubs["state"]
    assert state.round_id == ROUND_ID
    assert state.division_id == DIVISION_ID
    assert state.round_number == 3
    assert state.division_name == "Pro"
    assert state.submission_channel_id == SUBMISSION_CHANNEL
