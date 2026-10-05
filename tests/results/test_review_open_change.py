"""Opening a round's penalty review through the change queue: `results.review.open` (#439, slice 2).

Issue #208 first pinned this, against `enter_penalty_state`; the file was renamed from
`test_enter_penalty_state.py` when the review moved onto the queue. `review_open_change.py` carries
it as a change: `names` resolves the drivers' display names, `open` saves the round's standings
snapshots, the review flag and the status in one save, the posting jobs put out the interim results
and standings, `post_review_prompt` posts the prompt, and `close` writes the line.

**The flag goes up before anything is posted.** A stop during posting has to look like a
penalty-review orphan on the next restart, not a mid-submission one: the mid-submission recovery
path *deletes the session results and skips the round*. The `open` save is the first that writes.

**The round's status moves in the same save as the flag**, and only out of a cancellable state, so
a resubmission cannot drag a round that has reached appeals, or ended, backwards. A round in review
can no longer be cancelled; `ROUND_CANCELLABLE` is read from the model so the two cannot drift.

**A post Discord refuses stops the queue, and the prompt waits for it** ("Prompt waits"). It is
tried again, not swallowed (defect 11); `results_posted` is set only once the posts land. A
discarded post leaves the results unposted, and the prompt still comes.

**Recovery asks with `publish = False`** where the interim results went out before the restart:
that skips the posting and nothing else.

**The sessions offered are the active ones, in racing order**, and the prompt's message id is
saved with its post, so the next restart can replace it.

**A Discard of the `open` save or of the prompt asks for the review again**, as the bot ("Discard
reopens the review"). **Restart recovery** asks the queue rather than reopening the review itself,
and leaves alone a round whose review a change is still carrying out.

The league, its fake channels and its queue are `tests.support.review_league`'s, on a database of
this file's own. Everything of the change type is reached through the queue, so this file collects
while it is unbuilt.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.results.models.points_config import SessionType
from tests.support.change_queue import (
    MEMBER_ID,
    SERVER_ID,
    discard_job,
    http_error,
    restart_queue,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    tier_member,
)
from tests.support.review_league import (
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    PROMPT,
    ReviewLeague,
    changes_of,
    penalty,
    points_fail,
    run_until_done,
    stopped_at,
)
from tests.support.teams import seed_team_instances

KIND = "results.review.open"
NOT_BUILT = "#439: opening a round's review is not yet a change on the queue"
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701
SUBMISSION_CHANNEL = 702
OLD_RESULTS = 8800
OLD_APPEALS_PROMPT = 8950
CANCEL_MESSAGE = 8960


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "review_open",
    round_status: str = RoundStatus.AWAITING_RESULTS.value,
    sessions=(("FEATURE_RACE", "ACTIVE"),),
    results_channel: int | None = RESULTS_CHANNEL,
    standings_channel: int | None = STANDINGS_CHANNEL,
    config_row: bool = True,
    in_review: bool = False,
    results_posted: bool = False,
    prompt: int | None = None,
    resubmitting: bool = False,
    appeals_prompt: int | None = None,
) -> str:
    """Division 11 (Pro) of season 1, its round 3 at Silverstone with its submission channel open.
    Lewis (101) won each race session and Max (102) came second. Where *results_posted* is set, the
    interim results went out before, as message 8800 in the results channel."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', "
            "'Silverstone', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "in_penalty_review, results_posted, prompt_message_id, resubmitting) "
            "VALUES (?, ?, '2026-02-01T00:00:00+00:00', ?, ?, ?, ?)",
            (ROUND_ID, SUBMISSION_CHANNEL, int(in_review), int(results_posted), prompt,
             int(resubmitting)),
        )
        if appeals_prompt is not None:
            await db.execute(
                "UPDATE round_submission_channels SET appeals_prompt_message_id = ?",
                (appeals_prompt,),
            )
        if config_row:
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id, reserves_in_standings) VALUES (?, ?, ?, 1)",
                (DIVISION_ID, results_channel, standings_channel),
            )
        for profile, driver in ((LEWIS_PROFILE, LEWIS), (MAX_PROFILE, MAX)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(driver)),
            )
        for session_type, status in sessions:
            posted = results_posted and session_type == "FEATURE_RACE"
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "results_message_id, results_message_ids) VALUES (?, ?, ?, ?, ?, ?)",
                (ROUND_ID, DIVISION_ID, session_type, status,
                 OLD_RESULTS if posted else None, f"[{OLD_RESULTS}]" if posted else None),
            )
            if not session_type.endswith("RACE"):
                continue
            for position, (profile, driver, points) in enumerate(
                ((LEWIS_PROFILE, LEWIS, 25), (MAX_PROFILE, MAX, 18)), start=1,
            ):
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                    "team_instance_id, finishing_position, outcome, base_time_ms, "
                    "points_awarded, driver_profile_id) "
                    "VALUES (?, ?, 3001, ?, 'CLASSIFIED', ?, ?, ?)",
                    (session.lastrowid, driver, position, 3_600_000 + position * 1000, points,
                     profile),
                )
        await db.execute(
            "INSERT OR REPLACE INTO attendance_config (id, module_enabled) VALUES (1, 0)"
        )
        await db.execute("CREATE TABLE attendance_recorded (round_id INTEGER, pardons INTEGER)")
        await db.commit()
    return db_path


async def _league(tmp_path, **kwargs: Any) -> ReviewLeague:
    """The league of `_make_db`, its bot with a real queue and the real change types."""
    league = ReviewLeague(await _make_db(tmp_path, **kwargs), attendance=False)
    league.bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    league.bot.add_view = MagicMock()
    return league


async def _open(league: ReviewLeague, *, by_bot: bool = False, run: bool = True,
                **changes: Any) -> int | None:
    """Ask for round 3's review to open, as the paste of the league manager Alex or as the bot,
    then run the queue. *changes* replace the payload's defaults; one given as None is left out.
    Gives what the queue's `ask` gives."""
    from leaguebot.core.models.change import ChangeOrigin

    payload: dict[str, Any] = {"round_id": ROUND_ID, "label": "Provisional Results",
                               "publish": True}
    payload.update(changes)
    payload = {key: value for key, value in payload.items() if value is not None}
    change_id = await league.bot.change_queue.ask(
        KIND, payload,
        actor=None if by_bot else tier_member("manager", display_name="Alex", name="Alex#0001"),
        what="the penalty review of round 3",
        origin=ChangeOrigin.BOT if by_bot else ChangeOrigin.MEMBER,
    )
    if run:
        await run_queue(league.bot)
    return change_id


def _prompts(league: ReviewLeague, view: str = "PenaltyReviewView") -> list[int]:
    return [
        mid for mid, message in league.channel(SUBMISSION_CHANNEL).messages.items()
        if type(message.view).__name__ == view
    ]


def _prompt_view(league: ReviewLeague) -> Any:
    prompts = _prompts(league)
    assert len(prompts) == 1
    return league.channel(SUBMISSION_CHANNEL).messages[prompts[0]].view


def _at(league: ReviewLeague, kind: str, channel_id: int, message_id: int) -> int:
    return league.events.index((kind, channel_id, message_id))


def _contents(league: ReviewLeague, channel_id: int) -> list[str]:
    channel = league.channel(channel_id)
    return [channel.messages[mid].content for mid in league.sent_to(channel_id)
            if mid in channel.messages]


async def _fail_the_open_save(db_path: str, failing: bool) -> None:
    """While *failing*, a fault of the database refuses any write of the review flag, so the
    `open` save fails and is rolled back."""
    async with get_connection(db_path) as db:
        if failing:
            await db.execute(
                "CREATE TRIGGER fail_open BEFORE UPDATE OF in_penalty_review "
                "ON round_submission_channels BEGIN SELECT RAISE(ABORT, 'disk fault'); END"
            )
        else:
            await db.execute("DROP TRIGGER fail_open")
        await db.commit()


async def _channel_row(db_path) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM round_submission_channels WHERE round_id = ?", (ROUND_ID,),
        )
        return dict(await cursor.fetchone())


async def _round_status(db_path) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (ROUND_ID,))
        return (await cursor.fetchone())["status"]


async def _recover(league: ReviewLeague) -> None:
    """Restart: a fresh queue on the same database, then restart recovery, then the queue run."""
    import leaguebot.__main__ as bot_module

    await restart_queue(league.bot)
    await bot_module._recover_orphaned_submission_channels(league.bot)
    await run_queue(league.bot)


# ---------------------------------------------------------------------------
# The flag, the status, and their order
# ---------------------------------------------------------------------------


async def test_the_channel_is_marked_as_in_review(tmp_path):
    league = await _league(tmp_path)

    await _open(league)

    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1


async def test_the_review_flag_is_set_before_any_posting(tmp_path):
    """A stop during posting has to look like a penalty-review orphan on the next restart, not a
    mid-submission one — the mid-submission recovery path deletes the session results and skips
    the round, so this order is a division's race."""
    league = await _league(tmp_path, name="open_flag_order")
    await _open(league, run=False)

    await run_until_done(league, "open")

    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert league.sent_to(STANDINGS_CHANNEL) == []


async def test_the_round_now_awaits_report_verdicts(tmp_path):
    league = await _league(tmp_path, name="open_status")

    await _open(league)

    assert await _round_status(league.db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


@pytest.mark.parametrize("status", sorted(ROUND_CANCELLABLE))
async def test_a_cancellable_round_moves_into_review(tmp_path, status):
    """Read from the model's own frozenset: a state added to what may be cancelled and not
    to what may enter review would strand a round with its results in."""
    league = await _league(tmp_path, name=f"open_from_{status}", round_status=status)

    await _open(league)

    assert await _round_status(league.db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


@pytest.mark.parametrize(
    "status",
    [RoundStatus.AWAITING_APPEAL_VERDICTS.value, RoundStatus.FINAL.value],
)
async def test_a_round_past_review_is_not_dragged_backwards(tmp_path, status):
    """A resubmission asks for the review again, and a round that has reached appeals or ended
    must not be reopened for report verdicts that were already given."""
    league = await _league(tmp_path, name=f"open_keep_{status}", round_status=status,
                           in_review=True, results_posted=True, prompt=PROMPT)

    await _open(league, label="Provisional Results (amended)")

    assert await _round_status(league.db_path) == status
    assert _prompts(league) == []


async def test_a_round_in_review_can_no_longer_be_cancelled(tmp_path):
    """The drivers have reports and appeals to lodge, and calling the round off would take
    that from them. The status is what enforces it."""
    league = await _league(tmp_path, name="open_uncancellable")

    await _open(league)

    assert await _round_status(league.db_path) not in ROUND_CANCELLABLE


# ---------------------------------------------------------------------------
# The interim results
# ---------------------------------------------------------------------------


async def test_the_interim_results_and_standings_are_posted(tmp_path):
    league = await _league(tmp_path, name="open_posts")

    await _open(league)

    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert len(league.sent_to(STANDINGS_CHANNEL)) == 1
    assert (await _channel_row(league.db_path))["results_posted"] == 1


async def test_the_standings_are_ordered_by_the_names_they_are_posted_under(tmp_path):
    """The stored classification and the one the league is shown cannot be allowed to
    disagree, so the display names the `names` job resolves order the snapshot as well as label
    it: the job runs before the save that writes the snapshot."""
    league = await _league(tmp_path, name="open_names")

    await _open(league)

    names = [row["name"] for row in await step_rows(league.db_path)]
    assert names.index("names") < names.index("open")
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT driver_user_id FROM driver_standings_snapshots WHERE round_id = ? "
            "ORDER BY standing_position",
            (ROUND_ID,),
        )
        assert [row[0] for row in await cursor.fetchall()] == [LEWIS, MAX]


async def test_a_resubmission_says_so_on_both_posts(tmp_path):
    """A division seeing a second provisional table needs to know it replaces the first
    rather than adds to it."""
    league = await _league(tmp_path, name="open_amended")

    await _open(league, label="Provisional Results (amended)")

    for channel_id in (RESULTS_CHANNEL, STANDINGS_CHANNEL):
        posted = _contents(league, channel_id)
        assert posted
        assert all("Provisional Results (amended)" in content for content in posted)


async def test_a_first_submission_is_labelled_plainly(tmp_path):
    league = await _league(tmp_path, name="open_plain")

    await _open(league)

    posted = _contents(league, RESULTS_CHANNEL)
    assert posted
    assert all("Provisional Results" in content for content in posted)
    assert not any("(amended)" in content for content in posted)


async def test_a_division_with_no_results_channel_posts_no_results(tmp_path):
    """Configuring one is optional, and the review has to open either way: a channel the
    division was never given plans no job."""
    league = await _league(tmp_path, name="open_nores", results_channel=None)

    await _open(league)

    assert "post_session_results" not in [row["name"] for row in await step_rows(league.db_path)]
    assert len(league.sent_to(STANDINGS_CHANNEL)) == 1
    assert len(_prompts(league)) == 1


async def test_a_division_with_no_standings_channel_posts_no_standings(tmp_path):
    league = await _league(tmp_path, name="open_nostand", standings_channel=None)

    await _open(league)

    assert "post_standings" not in [row["name"] for row in await step_rows(league.db_path)]
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert len(_prompts(league)) == 1


async def test_a_deleted_results_channel_stops_the_queue_at_its_post(tmp_path):
    """The id is configured but the channel has gone. The post fails and stops the queue, the
    prompt waiting behind it, until the manager sets the channel and presses Retry."""
    league = await _league(tmp_path, name="open_gone")
    del league.channels[RESULTS_CHANNEL]

    await _open(league)

    assert await stopped_at(league) == "post_session_results"
    assert _prompts(league) == []


async def test_a_division_with_no_configuration_still_enters_review(tmp_path):
    """Nothing is configured, so no post is planned — the review is the part that matters and
    it must not depend on the channels being set up."""
    league = await _league(tmp_path, name="open_noconfig", config_row=False)

    await _open(league)

    assert len(_prompts(league)) == 1
    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1


async def test_a_failure_to_post_stops_the_queue_before_the_review_opens(tmp_path):
    """The interim results are not swallowed any more: Discord's refusal stops the queue at the
    post, and the prompt, which waits for them, is not yet posted."""
    league = await _league(tmp_path, name="open_post_fails")
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=503, text="Discord is down")

    await _open(league)

    assert await stopped_at(league) == "post_session_results"
    assert _prompts(league) == []


async def test_a_failure_to_post_leaves_the_results_unposted(tmp_path):
    """`results_posted` is set only once the posts land, which is what makes the next restart
    post them rather than assume it was done."""
    league = await _league(tmp_path, name="open_post_fails_flag")
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=503, text="Discord is down")

    await _open(league)

    assert (await _channel_row(league.db_path))["results_posted"] == 0


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


async def test_recovery_skips_the_posting(tmp_path):
    """Asked with `publish = False` only where the results went out already — posting again
    would put a second provisional table in the division's channel after every restart."""
    league = await _league(tmp_path, name="open_skip", in_review=True, results_posted=True,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _open(league, by_bot=True, publish=False)

    names = [row["name"] for row in await step_rows(league.db_path)]
    assert "post_session_results" not in names
    assert "post_standings" not in names
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert league.sent_to(STANDINGS_CHANNEL) == []


async def test_recovery_still_opens_the_review(tmp_path):
    """Skipping the posting must not skip the flag, the status or the prompt — the round is
    being recovered *into* review."""
    league = await _league(tmp_path, name="open_skip_rest", results_posted=True)

    await _open(league, by_bot=True, publish=False)

    assert len(_prompts(league)) == 1
    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1
    assert await _round_status(league.db_path) == RoundStatus.AWAITING_REPORT_VERDICTS.value


async def test_a_round_that_does_not_exist_does_nothing(tmp_path):
    """A request outliving its round is no longer due: the bot's is dropped at its check rather
    than stopping the queue, and nothing is posted."""
    league = await _league(tmp_path, name="open_noround")

    change_id = await _open(league, by_bot=True, round_id=9999)

    assert change_id is None
    assert await stopped_at(league) is None
    assert league.sent_to(SUBMISSION_CHANNEL) == []


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


async def test_the_prompt_is_posted_to_the_submission_channel(tmp_path):
    """Where the stewards already are, and where the results were submitted."""
    league = await _league(tmp_path, name="open_prompt")

    await _open(league)

    prompts = _prompts(league)
    assert len(prompts) == 1
    assert prompts[0] in league.sent_to(SUBMISSION_CHANNEL)


async def test_the_prompt_message_id_is_persisted(tmp_path):
    """Recovery deletes the old prompt once a new one stands; without the id it cannot, and the
    round ends up with two live prompts and two sets of staged penalties."""
    league = await _league(tmp_path, name="open_promptid")

    await _open(league)

    assert (await _channel_row(league.db_path))["prompt_message_id"] == _prompts(league)[0]


async def test_the_view_is_registered_for_persistent_routing(tmp_path):
    """Its buttons have to keep working across a restart, which is what `add_view` with the
    message id is for."""
    league = await _league(tmp_path, name="open_addview")

    await _open(league)

    league.bot.add_view.assert_called_once()
    assert league.bot.add_view.call_args.kwargs["message_id"] == _prompts(league)[0]


# ---------------------------------------------------------------------------
# The sessions offered to the stewards
# ---------------------------------------------------------------------------


async def test_the_rounds_active_sessions_are_offered(tmp_path):
    """A steward can only penalise a session that is in the review, so one missing here is
    a session nobody can apply a penalty to."""
    league = await _league(
        tmp_path, name="open_sessions", config_row=False,
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "ACTIVE")),
    )

    await _open(league)

    assert set(_prompt_view(league).state.session_types_present) == {
        SessionType.FEATURE_RACE,
        SessionType.FEATURE_QUALIFYING,
    }


async def test_a_superseded_session_is_not_offered(tmp_path):
    """It is not what the round is any more, and penalising it would apply a penalty to
    results that were already replaced."""
    league = await _league(
        tmp_path, name="open_superseded", config_row=False,
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "SUPERSEDED")),
    )

    await _open(league)

    assert _prompt_view(league).state.session_types_present == [SessionType.FEATURE_RACE]


async def test_the_sessions_are_offered_in_racing_order(tmp_path):
    """Sprint before feature, qualifying before its race. A sprint round reviewed
    feature-first reads as a different race, and the steward is working from memory of the
    evening."""
    league = await _league(
        tmp_path, name="open_order", config_row=False,
        sessions=(
            ("FEATURE_RACE", "ACTIVE"),
            ("SPRINT_QUALIFYING", "ACTIVE"),
            ("FEATURE_QUALIFYING", "ACTIVE"),
            ("SPRINT_RACE", "ACTIVE"),
        ),
    )

    await _open(league)

    assert _prompt_view(league).state.session_types_present == list(SessionType)


async def test_the_state_carries_the_round_and_division_it_describes(tmp_path):
    """Every prompt the steward sees is titled from these, and the round number is what
    tells two reviews open at once apart."""
    league = await _league(tmp_path, name="open_state", config_row=False)

    await _open(league)

    state = _prompt_view(league).state
    assert state.round_id == ROUND_ID
    assert state.division_id == DIVISION_ID
    assert state.round_number == 3
    assert state.division_name == "Pro"
    assert state.submission_channel_id == SUBMISSION_CHANNEL


# ---------------------------------------------------------------------------
# Defect 11's first case: the interim results on the queue
# ---------------------------------------------------------------------------


async def test_interim_results_discord_refuses_stop_the_queue_and_are_retried_not_swallowed(
    tmp_path,
):
    league = await _league(tmp_path, name="open_retried")
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    await _open(league)
    assert await stopped_at(league) == "post_session_results"

    league.channel(RESULTS_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert (await _channel_row(league.db_path))["results_posted"] == 1
    assert len(_prompts(league)) == 1


async def test_the_review_prompt_waits_for_the_interim_results(tmp_path):
    league = await _league(tmp_path, name="open_prompt_waits")

    await _open(league)

    prompt = _at(league, "send", SUBMISSION_CHANNEL, _prompts(league)[0])
    assert _at(league, "send", RESULTS_CHANNEL, league.sent_to(RESULTS_CHANNEL)[0]) < prompt
    assert _at(league, "send", STANDINGS_CHANNEL, league.sent_to(STANDINGS_CHANNEL)[0]) < prompt


async def test_a_discarded_interim_post_leaves_results_unposted_and_the_prompt_still_comes(
    tmp_path,
):
    league = await _league(tmp_path, name="open_post_discarded")
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    await _open(league)
    assert await stopped_at(league) == "post_session_results"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert len(_prompts(league)) == 1
    assert (await _channel_row(league.db_path))["results_posted"] == 0


@pytest.mark.parametrize("job", ["open", "post_review_prompt"])
async def test_a_discarded_open_or_review_prompt_asks_for_the_review_again(tmp_path, job):
    """"Discard reopens the review": once the `open` save or the prompt's post is discarded,
    `close` asks for the review again, as the bot, and the fresh request opens it."""
    league = await _league(tmp_path, name=f"open_reopened_{job}")
    if job == "open":
        await _fail_the_open_save(league.db_path, True)
    else:
        league.channel(SUBMISSION_CHANNEL).fail_when = (
            lambda _content, kwargs: type(kwargs.get("view")).__name__ == "PenaltyReviewView"
        )
    await _open(league)
    assert await stopped_at(league) == job

    await discard_job(league.bot, run=False)
    if job == "open":
        await _fail_the_open_save(league.db_path, False)
    else:
        league.channel(SUBMISSION_CHANNEL).fail_when = None
    await run_queue(league.bot)

    asked = await changes_of(league.db_path, KIND)
    assert len(asked) == 2
    assert asked[1]["origin"] == "BOT"
    assert await stopped_at(league) is None
    assert len(_prompts(league)) == 1
    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1


# ---------------------------------------------------------------------------
# A resubmission, and its return to review
# ---------------------------------------------------------------------------


async def test_a_resubmission_is_published_as_amended(tmp_path):
    """Once a resubmission has replaced the results, the review opens again with the new
    results posted as "Provisional Results (amended)", the new message standing before the
    earlier provisional one is deleted."""
    league = await _league(tmp_path, name="open_resubmitted", in_review=True,
                           results_posted=True,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _open(league, label="Provisional Results (amended)")

    new = league.sent_to(RESULTS_CHANNEL)
    assert len(new) == 1
    assert "Provisional Results (amended)" in league.channel(RESULTS_CHANNEL).messages[new[0]].content
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    assert _at(league, "send", RESULTS_CHANNEL, new[0]) < _at(
        league, "delete", RESULTS_CHANNEL, OLD_RESULTS
    )
    assert len(_prompts(league)) == 1


async def test_a_cancelled_resubmission_is_recorded_once_the_review_is_back(tmp_path):
    """A cancel puts the review back first: its line in the channel and in the log come after
    the prompt stands, never while the prompt is still owed. The cancel button is taken off."""
    league = await _league(tmp_path, name="open_cancelled", in_review=True,
                           results_posted=True, resubmitting=True,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)
    submission = league.channel(SUBMISSION_CHANNEL)
    submission.seed(CANCEL_MESSAGE, "🔄 Resubmission under way")
    submission.messages[CANCEL_MESSAGE].view = MagicMock()
    submission.fail_when = (
        lambda _content, kwargs: type(kwargs.get("view")).__name__ == "PenaltyReviewView"
    )

    await _open(league, publish=False, returning="cancelled", cancel_message_id=CANCEL_MESSAGE)

    assert await stopped_at(league) == "post_review_prompt"
    assert "cancelled" not in league.log()
    assert not any("Resubmission cancelled" in text for text in _contents(league, SUBMISSION_CHANNEL))

    submission.fail_when = None
    await retry_job(league.bot)

    prompt = _prompts(league)[0]
    cancel_lines = [mid for mid in league.sent_to(SUBMISSION_CHANNEL)
                    if "Resubmission cancelled" in submission.messages[mid].content]
    assert len(cancel_lines) == 1
    assert submission.messages[cancel_lines[0]].content == (
        "↩️ **Resubmission cancelled.** The earlier results stand."
    )
    assert _at(league, "send", SUBMISSION_CHANNEL, prompt) < _at(
        league, "send", SUBMISSION_CHANNEL, cancel_lines[0]
    )
    assert league.log().count("resubmission of round 3") == 1
    lines = league.bot.log_channel.sent
    [cancel] = [line for line in lines if "cancelled by" in line]
    assert cancel.startswith("↩️ ")
    assert f"cancelled by Alex (`<@{MEMBER_ID}>`)" in cancel.split("\n", 1)[0]
    assert "The earlier results stand." in cancel
    assert "Press 🔄 Resubmit Initial Results to start again." in cancel
    assert not any("RESULTS_RESUBMISSION | Cancelled" in line for line in lines)
    assert submission.messages[CANCEL_MESSAGE].view is None
    row = await _channel_row(league.db_path)
    assert row["resubmitting"] == 0
    assert row["resubmit_prompt_message_id"] is None


async def test_a_resubmission_that_fails_before_the_swap_returns_to_review_and_says_so(tmp_path):
    """A resubmission that failed before its results were swapped in leaves the earlier results
    standing: the bot asks for the review back, and the log says the resubmission ended."""
    league = await _league(tmp_path, name="open_failed", in_review=True, results_posted=True,
                           resubmitting=True,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _open(league, by_bot=True, publish=False, returning="failed")

    assert await stopped_at(league) is None
    assert len(_prompts(league)) == 1
    assert (await _channel_row(league.db_path))["resubmitting"] == 0
    assert "resubmission of round 3" in league.log()


async def test_a_member_s_open_with_its_channel_gone_is_refused(tmp_path):
    league = await _league(tmp_path, name="open_member_gone")
    del league.channels[SUBMISSION_CHANNEL]

    change_id = await _open(league)

    assert change_id is None
    assert await changes_of(league.db_path, KIND) == []
    assert (await _channel_row(league.db_path))["in_penalty_review"] == 0


async def test_a_bot_s_open_with_its_channel_gone_stops_the_queue_with_the_reason(tmp_path):
    """A bot's request is not refused: its check stops the queue, giving its reason in the stop
    line, until the channel is back and someone presses Retry."""
    league = await _league(tmp_path, name="open_bot_gone", in_review=True, results_posted=True,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)
    del league.channels[SUBMISSION_CHANNEL]

    await _open(league, by_bot=True, publish=False)

    assert await stopped_at(league) is not None
    stops = [line for line in league.bot.log_channel.sent
             if "The queue is stopped at job #" in line]
    assert len(stops) == 1
    assert "channel" in stops[0]


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


async def _ask_report_approval(league: ReviewLeague) -> None:
    await league.bot.change_queue.ask(
        "results.reports.approve",
        {"round_id": ROUND_ID, "division_id": DIVISION_ID,
         "staged": [penalty(LEWIS).to_payload()], "pardons": [],
         "prompt_message_id": PROMPT, "approval_message_id": None},
        actor=tier_member("manager", display_name="Alex", name="Alex#0001"),
        what="✅ Approve on round 3's penalty review",
    )


async def test_recovery_leaves_alone_a_round_whose_review_is_in_hand(tmp_path):
    league = await _league(tmp_path, name="recover_in_hand", in_review=True,
                           results_posted=True, prompt=PROMPT,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)
    await _ask_report_approval(league)

    import leaguebot.__main__ as bot_module

    await restart_queue(league.bot)
    await bot_module._recover_orphaned_submission_channels(league.bot)

    assert await changes_of(league.db_path, KIND) == []
    assert PROMPT in league.channel(SUBMISSION_CHANNEL).messages


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_recovery_leaves_alone_a_round_whose_approval_is_stopped(tmp_path):
    league = await _league(tmp_path, name="recover_stopped", in_review=True,
                           results_posted=True, prompt=PROMPT,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)
    with points_fail():
        await _ask_report_approval(league)
        await run_queue(league.bot)
    assert await stopped_at(league) == "apply"

    await _recover(league)

    assert await changes_of(league.db_path, KIND) == []
    assert await stopped_at(league) == "apply"
    assert _prompts(league) == []


async def test_recovery_reopens_a_report_stage_through_the_queue_replacing_the_old_prompt(
    tmp_path,
):
    league = await _league(tmp_path, name="recover_report", in_review=True,
                           results_posted=True, prompt=PROMPT,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _recover(league)

    import json

    asked = await changes_of(league.db_path, KIND)
    assert len(asked) == 1
    assert asked[0]["origin"] == "BOT"
    payload = json.loads(asked[0]["payload"])
    assert payload["publish"] is False
    assert payload["old_prompt_id"] == PROMPT
    prompts = _prompts(league)
    assert len(prompts) == 1
    assert PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    assert _at(league, "send", SUBMISSION_CHANNEL, prompts[0]) < _at(
        league, "delete", SUBMISSION_CHANNEL, PROMPT
    )
    assert league.sent_to(RESULTS_CHANNEL) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_recovery_reopens_a_review_whose_open_was_discarded(tmp_path):
    """The paste asked for the review, its `open` save failed and was discarded, and the review
    `close` asked for again was discarded at its check (the channel was gone then): nothing is in
    hand, and the round awaits its review. A restart asks for it, as the bot, and it opens with
    its results posted; the results are not taken for a first paste cut short and deleted."""
    league = await _league(tmp_path, name="recover_discarded_open")
    await _fail_the_open_save(league.db_path, True)
    await _open(league)
    assert await stopped_at(league) == "open"
    await discard_job(league.bot, run=False)
    await _fail_the_open_save(league.db_path, False)
    gone = league.channels.pop(SUBMISSION_CHANNEL)
    await run_queue(league.bot)
    assert await stopped_at(league) is not None
    await discard_job(league.bot)
    assert await stopped_at(league) is None
    league.channels[SUBMISSION_CHANNEL] = gone

    await _recover(league)

    asked = await changes_of(league.db_path, KIND)
    assert len(asked) == 3
    assert asked[2]["origin"] == "BOT"
    assert len(_prompts(league)) == 1
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert (await _channel_row(league.db_path))["in_penalty_review"] == 1
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM session_results WHERE round_id = ?",
                                  (ROUND_ID,))
        assert (await cursor.fetchone())[0] == 1


async def test_recovery_reposts_the_appeals_prompt_replacing_the_old_one(tmp_path):
    """The appeals prompt is posted again as a change, its id saved for the next restart and its
    view registered so its buttons keep working across the next restart too."""
    league = await _league(tmp_path, name="recover_appeals", in_review=True,
                           results_posted=True, appeals_prompt=OLD_APPEALS_PROMPT,
                           round_status=RoundStatus.AWAITING_APPEAL_VERDICTS.value)
    league.channel(SUBMISSION_CHANNEL).seed(OLD_APPEALS_PROMPT, "appeals review")

    await _recover(league)

    asked = await changes_of(league.db_path, "results.appeals.open")
    assert len(asked) == 1
    assert asked[0]["origin"] == "BOT"
    prompts = _prompts(league, "AppealsReviewView")
    assert len(prompts) == 1
    assert OLD_APPEALS_PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    assert (await _channel_row(league.db_path))["appeals_prompt_message_id"] == prompts[0]
    assert any(call.kwargs.get("message_id") == prompts[0]
               for call in league.bot.add_view.call_args_list)
    assert await changes_of(league.db_path, KIND) == []


async def test_recovery_closes_a_final_round_left_with_an_open_channel(tmp_path):
    """Only a run from before #439 can leave a final round with its channel open. It is closed,
    with a line, rather than its dead review posted again at every restart."""
    league = await _league(tmp_path, name="recover_final", in_review=True,
                           results_posted=True, prompt=PROMPT,
                           round_status=RoundStatus.FINAL.value)

    await _recover(league)

    assert len(await changes_of(league.db_path, "results.review.close_stale")) == 1
    assert ("delete_channel", SUBMISSION_CHANNEL, SUBMISSION_CHANNEL) in league.events
    assert (await _channel_row(league.db_path))["closed"] == 1
    assert _prompts(league) == []
    assert _prompts(league, "AppealsReviewView") == []
    assert "round 3" in league.log().lower()


async def test_recovery_no_longer_warns_of_penalties_already_applied(tmp_path):
    """The queue carries what was staged, so "already applied" can no longer happen: the
    warning is gone, and the column it read with it."""
    league = await _league(tmp_path, name="recover_no_warning", in_review=True,
                           results_posted=True, prompt=PROMPT,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _recover(league)

    assert "staged_penalties" not in await _channel_row(league.db_path)
    assert not any("already applied" in text.lower()
                   for text in _contents(league, SUBMISSION_CHANNEL))
    assert len(_prompts(league)) == 1
