"""An amendment of a round's results run on the change queue (#439, slice 6b, C58–C64).

`/results rounds amend` asks `results.amend.open` with its interaction, acknowledged privately as
under way, its checks held again when it runs. On the league lane, whole (a command's jobs), it
deletes a stale or duplicate amendment channel first (`delete_stale`), creates the amendment
channel and records its row (`create_channel`), posts the opening with its Cancel button
(`post_opening`) and the first session's prompt, recording when it appeared (`post_prompt`,
`prompt_posted_at`), and arms the five minutes from then in a job of its own (`arm_lapse`,
`arm_once("amend_lapse", <amendment>, prompt_posted_at + 5 min)`; Q18).

Each paste typed in the amendment channel is then a `results.amend.turn` on the amendment's lane
(``channel:<amendment channel>``): marked ⏳ at once; when its turn comes the paste is deleted and
saved as a draft of the amendment (`amend_drafts`), or, refused, it ends the whole amendment, the
drafts dropped and nothing written (results spec :459), the channel deleted on the amendment's own
lane (decision 25); then the next prompt, or the configuration picker, and the five minutes armed
again. The configuration choice is a `results.amend.config` turn. The last session's turn hands
`results.amend.stage_one` to the league lane (the snapshot, the amended results and their points in
one save), which asks the amendment's lane back for the stage-two prompt (`results.amend.prompt`).
Cancel Amendment asks `results.amend.cancel` on the league lane: the round put back and the channel
deleted, the conversation ended. The lapse (`results.amend.lapse`) is asked by the timer's
callback, the half-hour sweep and the restart, as the bot, on the league lane: it is not due where a
paste typed in time waits on the amendment's lane (Q18), nor while the stage-two prompt is in hand
(Q19, results spec :491 extended).

The league is `review_league`'s: round 3 of Pro, FINAL, its Feature Race won by Lewis (101) ahead of
Max (102), its submission channel closed. The server creates the amendment channel as channel 705,
the league's channel double; the division's seats, as a paste's validation reads them, are Lewis and
Max at team 3001, shorthand FER. The manager is Alex (4242). Everything of the change types is
reached through the command, the cog, the queue or the timer the builder registers, or imported
inside a test, so this file collects while they are unbuilt.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.season_service import SeasonService
from leaguebot.results.services import result_submission_service
from leaguebot.results.services.result_submission_service import DivisionValidationData
from tests.support.change_queue import (
    MARK,
    MEMBER_ID,
    acknowledgement,
    change_rows,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
    typed_message,
    updated_reply,
)
from tests.support.review_league import (
    AMEND_CHANNEL,
    DIVISION_ID,
    LATER_ROUND_ID,
    LEWIS,
    MAX,
    NOW,
    ROUND_ID,
    ReviewLeague,
    block_queue,
    channel,
    race_rows,
    review_league,
)
from tests.support.undecorate import undecorate

OPEN = "results.amend.open"
TURN = "results.amend.turn"
CONFIG = "results.amend.config"
STAGE_ONE = "results.amend.stage_one"
PROMPT = "results.amend.prompt"
CANCEL = "results.amend.cancel"
LAPSE = "results.amend.lapse"
TIMER = "amend_lapse"
LANE = f"channel:{AMEND_CHANNEL}"
#: A finished amendment's channel that survived, or another amendment's, standing on the server.
STALE_CHANNEL = 706
HEAVY = "Heavy"

#: Max ahead of Lewis: the amended Feature Race.
RACE_PASTE = "1, <@102>, FER, 1:30:00.000, 1:20.000, N/A\n2, <@101>, FER, +5.000, 1:21.000, N/A"
QUALI_PASTE = "1, <@101>, FER, Soft, 1:19.000, N/A\n2, <@102>, FER, Soft, 1:19.500, +0.500"

_SEATED = DivisionValidationData(
    division_driver_ids={LEWIS, MAX}, team_of_role={}, reserve_team_role_id=None,
    driver_team_map={LEWIS: 3001, MAX: 3001}, reserve_driver_ids=set(),
    team_names={3001: "Team 3001"}, team_of_shorthand={"fer": 3001},
)

NO_AMEND_ON_THE_QUEUE = (
    "#439: `/results rounds amend` opens its channel, waits for each paste and choice, writes stage "
    "one and lapses inside the command's coroutine, off the change queue, rather than through "
    "`results.amend.open`, its turns, stage one, cancel and lapse"
)


def _alex() -> Any:
    return tier_member("admin", display_name="Alex", name="Alex#0001")


async def _league(
    tmp_path: Any, monkeypatch: Any, *, configs: tuple[str, ...] = ("Standard",),
    qualifying: bool = False,
) -> ReviewLeague:
    """The league of the module docstring. *configs* are the points configurations attached to
    the season; with more than one, the Feature Race carries none, so a choice is offered. With
    *qualifying*, round 3 has a Qualifying session too."""
    league = await review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1, in_penalty_review = 0"
        )
        for name in configs:
            await db.execute(
                "INSERT OR IGNORE INTO points_config_store (config_name) VALUES (?)", (name,)
            )
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (1, ?)", (name,)
            )
        if len(configs) > 1:
            await db.execute("UPDATE session_results SET config_name = NULL")
        if qualifying:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name) VALUES (?, ?, 'QUALIFYING', 'ACTIVE', ?)",
                (ROUND_ID, DIVISION_ID, configs[0]),
            )
        await db.commit()
    amend = league.channel(AMEND_CHANNEL)
    amend.posted = []
    league.created = []
    league.create_fails = None

    async def _create(name: str, **_kwargs: Any) -> Any:
        if league.create_fails is not None:
            raise league.create_fails
        amend.name = name
        league.created.append(AMEND_CHANNEL)
        return amend

    league.guild.create_text_channel = AsyncMock(side_effect=_create)
    league.guild.me = None
    league.guild.default_role = MagicMock(name="@everyone")
    league.bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    league.bot.season_service = SeasonService(league.db_path)
    monkeypatch.setattr(
        result_submission_service, "_build_division_validation_data",
        AsyncMock(return_value=_SEATED),
    )
    return league


async def _ask_open(
    league: ReviewLeague, *, sessions: tuple[str, ...] = ("FEATURE_RACE",), run: bool = True,
) -> Any:
    """Alex's `/results rounds amend` of round 3's *sessions*, asked of the queue as the command
    asks it; then, where *run*, the queue is run. Gives Alex's interaction."""
    interaction = member_interaction(league.bot, user=_alex())
    interaction.guild = league.guild
    await league.bot.change_queue.ask(
        OPEN,
        {"round_id": ROUND_ID, "division_id": DIVISION_ID, "session_types": list(sessions)},
        interaction=interaction,
        what="/results rounds amend Pro 3",
    )
    if run:
        await run_queue(league.bot)
    return interaction


async def _type(
    league: ReviewLeague, content: str = RACE_PASTE, *, run: bool = True,
    after: timedelta = timedelta(0),
) -> Any:
    """Alex types *content* in the amendment channel, *after* now, and the results cog hears it;
    then, where *run*, the queue is run. Gives the message."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    message = typed_message(league.channel(AMEND_CHANNEL), _alex(), content,
                            created_at=NOW + after)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    await cog.on_message(message)
    if run:
        await run_queue(league.bot)
    return message


async def _rows(league: ReviewLeague, sql: str, *args: Any) -> list[dict[str, Any]]:
    async with get_connection(league.db_path) as db:
        return [dict(row) for row in await (await db.execute(sql, args)).fetchall()]


async def _amend_rows(league: ReviewLeague, round_id: int = ROUND_ID) -> list[dict[str, Any]]:
    return await _rows(league, "SELECT * FROM round_amend_channels WHERE round_id = ?", round_id)


async def _drafts(league: ReviewLeague) -> list[str]:
    rows = await _rows(league, "SELECT session_type FROM amend_drafts ORDER BY session_type")
    return [row["session_type"] for row in rows]


async def _changes(league: ReviewLeague, kind: str) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == kind]


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


def _texts(league: ReviewLeague) -> list[str]:
    amend = league.channel(AMEND_CHANNEL)
    return [str(amend.messages[mid].content) for mid in league.sent_to(AMEND_CHANNEL)
            if mid in amend.messages]


def _deleted(league: ReviewLeague, cid: int = AMEND_CHANNEL) -> bool:
    return ("delete_channel", cid, cid) in league.events


async def _winner(league: ReviewLeague) -> int:
    rows = await race_rows(league.db_path)
    return next(driver for driver, row in rows.items() if row["finishing_position"] == 1)


def _armed(league: ReviewLeague) -> list[tuple[Any, ...]]:
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == TIMER]


def _timer_callback(league: ReviewLeague) -> Any:
    """The callback the builder registered for the amendment's five minutes."""
    [callback] = [call.args[1]
                  for call in league.bot.scheduler_service.register_timer.call_args_list
                  if call.args and call.args[0] == TIMER]
    return callback


async def _fire(league: ReviewLeague) -> None:
    """The amendment's five-minute timer fires, for the key it was last armed with."""
    *_, last = _armed(league)
    await _timer_callback(league)(last[1])


def _stage_two(content: str, _kwargs: dict[str, Any]) -> bool:
    return "Stage 2 of 3" in str(content)


# ---------------------------------------------------------------------------
# Opening the amendment (C58)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_an_amendment_is_acknowledged_and_opens_its_channel_with_the_first_prompt(
    tmp_path, monkeypatch,
):
    """Alex runs `/results rounds amend` for round 3's Feature Race. He is answered privately that
    it is under way, and nothing is created yet. Once the queue runs, on the league lane, the
    amendment channel is created and recorded against round 3 as Alex's, the opening is posted in
    it with its Cancel button, the first prompt follows and the time it appeared is recorded, and
    the acknowledgement is updated."""
    from leaguebot.results.cogs.results_cog import ResultsCog

    league = await _league(tmp_path, monkeypatch)
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot, user=_alex())
    interaction.guild = league.guild
    interaction.client.output_router = league.bot.output_router
    interaction.command = SimpleNamespace(qualified_name="results rounds amend")
    # A command that waited for a paste itself would find none typed, at once.
    league.bot.loop = asyncio.get_running_loop()
    league.bot.wait_for = AsyncMock(side_effect=asyncio.TimeoutError("nothing was typed"))

    await undecorate(ResultsCog.rounds_amend)(
        cog, interaction, "Pro", 3, SimpleNamespace(name="FEATURE_RACE", value="FEATURE_RACE"),
    )

    assert acknowledgement(interaction).startswith("⏳")
    assert league.created == []
    [change] = await _changes(league, OPEN)
    assert _lane(change) == "league"

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.created == [AMEND_CHANNEL]
    [row] = await _amend_rows(league)
    assert row["channel_id"] == AMEND_CHANNEL
    assert int(row["started_by"]) == MEMBER_ID
    assert row["closed_at"] is None
    assert row["prompt_posted_at"] is not None
    [opening] = [league.channel(AMEND_CHANNEL).messages[mid]
                 for mid in league.sent_to(AMEND_CHANNEL)
                 if "Amend Results — Round 3" in str(league.channel(AMEND_CHANNEL).messages[mid].content)]
    assert opening.view is not None
    assert updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_stale_or_duplicate_channel_is_deleted_as_a_job_first(tmp_path, monkeypatch):
    """An earlier amendment of round 3 has finished, but its channel (706) survived with its row.
    Alex amends round 3 again: once the queue runs, the stale channel is deleted by a job of the
    amendment, before the new channel is created, and its row gives way to the new one."""
    league = await _league(tmp_path, monkeypatch)
    league.channels[STALE_CHANNEL] = channel(STALE_CHANNEL, league.events)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by, closed_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, 77, ?)",
            (ROUND_ID, STALE_CHANNEL, NOW.isoformat(), NOW.isoformat()),
        )
        await db.commit()

    await _ask_open(league)

    assert await stopped_job(league.db_path) is None
    assert _deleted(league, STALE_CHANNEL)
    first_post = league.events.index(("send", AMEND_CHANNEL, league.sent_to(AMEND_CHANNEL)[0]))
    assert league.events.index(("delete_channel", STALE_CHANNEL, STALE_CHANNEL)) < first_post
    assert [row["channel_id"] for row in await _amend_rows(league)] == [AMEND_CHANNEL]
    [change] = await _changes(league, OPEN)
    [stale] = [step for step in await step_rows(league.db_path, change["id"])
               if step["name"] == "delete_stale"]
    assert stale["done_at"] is not None


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_an_amendment_refused_when_it_runs_says_why(tmp_path, monkeypatch):
    """Alex asks to amend round 3; before the queue reaches it, an amendment of round 4 of the
    same division is opened in channel 706. When the amendment runs it is refused: no channel is
    created, round 3 gains no amendment row, the queue is not stopped, and Alex's acknowledgement
    is updated with the refusal."""
    league = await _league(tmp_path, monkeypatch)
    interaction = await _ask_open(league, run=False)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, 77)",
            (LATER_ROUND_ID, STALE_CHANNEL, NOW.isoformat()),
        )
        await db.commit()

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert league.created == []
    assert await _amend_rows(league) == []
    assert updated_reply(interaction)


# ---------------------------------------------------------------------------
# Each paste a turn (C59)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_each_amended_paste_is_deleted_and_saved_as_a_draft_by_its_turn(
    tmp_path, monkeypatch,
):
    """Alex amends round 3's Qualifying and Feature Race, and pastes the Qualifying. His message
    is marked ⏳ at once and a turn is asked on the amendment's lane; nothing is saved yet. When the
    turn runs, his paste is deleted, the Qualifying is saved as a draft of the amendment, the
    round's own results are untouched, and the Feature Race's prompt follows."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _ask_open(league, sessions=("QUALIFYING", "FEATURE_RACE"))

    message = await _type(league, QUALI_PASTE, run=False)

    assert MARK in message.marks
    [turn] = await _changes(league, TURN)
    assert _lane(turn) == LANE
    assert await _drafts(league) == []

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert message.gone
    assert await _drafts(league) == ["QUALIFYING"]
    assert await _winner(league) == LEWIS
    assert "Feature Race" in _texts(league)[-1]


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_an_amended_paste_while_its_turn_waits_is_turned_away(tmp_path, monkeypatch):
    """Alex pastes the Feature Race, and before its turn has run pastes it again. The second
    message is turned away at once: deleted, with a note in the channel, and no second turn is
    asked; the first stays marked ⏳."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    first = await _type(league, run=False)
    sent_before = len(league.sent_to(AMEND_CHANNEL))

    second = await _type(league, run=False)

    assert second.gone
    assert MARK in first.marks
    assert len(await _changes(league, TURN)) == 1
    assert len(league.sent_to(AMEND_CHANNEL)) == sent_before + 1


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_refused_amended_paste_ends_the_whole_amendment_writing_nothing(
    tmp_path, monkeypatch,
):
    """Alex amends round 3's Qualifying and Feature Race. His Qualifying paste is accepted as a
    draft; his Feature Race paste names a driver outside the division and is refused. The whole
    amendment ends (results spec :459): the drafts are dropped, the refusal is said, the channel is
    deleted and its row closed, and the round's results stand as they were."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _ask_open(league, sessions=("QUALIFYING", "FEATURE_RACE"))
    await _type(league, QUALI_PASTE)

    await _type(league, "1, <@103>, FER, 1:30:00.000, 1:20.000, N/A")

    assert await stopped_job(league.db_path) is None
    assert await _drafts(league) == []
    assert any("rejected" in text.lower() for text in _texts(league))
    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None
    assert row["pre_amendment_state"] is None
    assert await _winner(league) == LEWIS


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_refused_deletion_after_a_refused_paste_stops_only_the_amendment_s_lane(
    tmp_path, monkeypatch,
):
    """Alex's Feature Race paste is refused, and Discord refuses to delete the amendment channel.
    The amendment ending itself, its own lane stops at the deletion; the league lane does not."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _type(league, "1, <@103>, FER, 1:30:00.000, 1:20.000, N/A")

    assert await stopped_job(league.db_path, "league") is None
    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None
    assert not _deleted(league)


# ---------------------------------------------------------------------------
# The configuration choice (C60)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_amendment_s_configuration_choice_is_a_turn(tmp_path, monkeypatch):
    """The season has two points configurations attached and the Feature Race carries neither.
    Alex pastes the Feature Race: its turn offers the configurations in the amendment channel and
    writes nothing. He presses "Heavy": a configuration turn is asked on the amendment's lane and
    he is answered privately; when the queue runs, the amended Feature Race is written scored by
    "Heavy"."""
    league = await _league(tmp_path, monkeypatch, configs=("Standard", HEAVY))
    await _ask_open(league)
    await _type(league)

    [picker] = [message for message in league.channel(AMEND_CHANNEL).messages.values()
                if message.view is not None
                and "Select the points configuration" in str(message.content)]
    assert await _winner(league) == LEWIS
    [button] = [item for item in picker.view.children if getattr(item, "label", None) == HEAVY]
    press = member_interaction(league.bot, user=_alex())
    press.guild = league.guild
    press.channel_id = AMEND_CHANNEL
    press.message = picker
    press.response.edit_message = AsyncMock()

    await button.callback(press)

    [choice] = await _changes(league, CONFIG)
    assert _lane(choice) == LANE

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == MAX
    [session] = await _rows(league,
        "SELECT config_name FROM session_results WHERE round_id = ? "
        "AND session_type = 'FEATURE_RACE'", ROUND_ID)
    assert session["config_name"] == HEAVY


# ---------------------------------------------------------------------------
# Stage one and its stage-two prompt (C61)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_last_amended_paste_records_stage_one_and_posts_stage_two_as_jobs(
    tmp_path, monkeypatch,
):
    """Alex pastes the amended Feature Race, Max ahead of Lewis. When the queue runs, stage one is
    recorded: the round's results before it are kept as the amendment's snapshot, the amended
    Feature Race is written, and the stage-two prompt is posted in the amendment channel."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league)

    assert await stopped_job(league.db_path) is None
    assert [change["state"] for change in await _changes(league, STAGE_ONE)] == ["DONE"]
    [row] = await _amend_rows(league)
    assert row["pre_amendment_state"] is not None
    assert await _winner(league) == MAX
    assert any("Stage 2 of 3" in text for text in _texts(league))


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_last_amended_paste_hands_stage_one_to_the_league_and_its_prompt_comes_back_on_the_lane(
    tmp_path, monkeypatch,
):
    """Alex's last paste runs on the amendment's lane and hands stage one to the league lane; the
    stage-two prompt comes back as a change on the amendment's lane, asked after stage one. Where
    Discord refuses that prompt, only the amendment's lane stops, stage one already recorded."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).fail_when = _stage_two

    await _type(league)

    [turn] = await _changes(league, TURN)
    [stage_one] = await _changes(league, STAGE_ONE)
    [prompt] = await _changes(league, PROMPT)
    assert (_lane(turn), _lane(stage_one), _lane(prompt)) == (LANE, "league", LANE)
    assert turn["id"] < stage_one["id"] < prompt["id"]
    assert stage_one["state"] == "DONE"
    assert await stopped_job(league.db_path, "league") is None
    assert await stopped_job(league.db_path, LANE) is not None
    assert await _winner(league) == MAX


# ---------------------------------------------------------------------------
# Cancel Amendment (C62)
# ---------------------------------------------------------------------------


async def _press_cancel(league: ReviewLeague) -> Any:
    """Alex presses ❌ Cancel Amendment on the opening; gives the press."""
    [opening] = [message for message in league.channel(AMEND_CHANNEL).messages.values()
                 if "Amend Results — Round 3" in str(message.content)]
    [button] = [item for item in opening.view.children
                if "Cancel" in str(getattr(item, "label", ""))]
    press = member_interaction(league.bot, user=_alex())
    press.guild = league.guild
    press.channel_id = AMEND_CHANNEL
    press.message = opening
    await button.callback(press)
    return press


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_cancel_amendment_reverts_and_deletes_the_channel_as_jobs(tmp_path, monkeypatch):
    """Stage one of Alex's amendment is recorded, Max now ahead. Alex presses Cancel Amendment: he
    is answered privately that it is under way and nothing is undone yet. When the queue runs, the
    round is put back as it stood, Lewis ahead again, the amendment channel is deleted and its row
    closed."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league)

    press = await _press_cancel(league)

    assert acknowledgement(press).startswith("⏳")
    assert await _winner(league) == MAX
    assert not _deleted(league)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [change["state"] for change in await _changes(league, CANCEL)] == ["DONE"]
    assert await _winner(league) == LEWIS
    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_cancel_amendment_runs_on_the_league_and_ends_the_conversation(
    tmp_path, monkeypatch,
):
    """Alex presses Cancel Amendment after stage one, and before it runs types in the amendment
    channel again. The cancellation runs on the league lane and ends the amendment in its save:
    the turn waiting on the amendment's lane is then found no longer due and writes no draft."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await _type(league)
    await _press_cancel(league)
    await _type(league, run=False)

    await run_queue(league.bot)

    [cancel] = await _changes(league, CANCEL)
    assert _lane(cancel) == "league"
    assert await stopped_job(league.db_path) is None
    assert await _drafts(league) == []
    assert await _winner(league) == LEWIS


# ---------------------------------------------------------------------------
# The lapse (C63, C64)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_five_minutes_start_when_the_prompt_appears(tmp_path, monkeypatch):
    """Alex asks for the amendment, but the queue reaches it four minutes later. The prompt's
    time is recorded as when it appeared, and the five minutes are armed from then, nine minutes
    after the ask, not five."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league, run=False)
    getattr(league.bot, "_change_queue_test_setup").clock.advance(minutes=4)

    await run_queue(league.bot)

    [row] = await _amend_rows(league)
    assert row["prompt_posted_at"] == (NOW + timedelta(minutes=4)).isoformat()
    *_, last = _armed(league)
    assert last[2] == NOW + timedelta(minutes=9)


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_an_amendment_lapses_five_minutes_after_its_prompt_with_nothing_typed(
    tmp_path, monkeypatch,
):
    """Alex opens the amendment and types nothing. Its five minutes run out: the timer asks the
    lapse, as the bot, on the league lane, and when it runs the channel is deleted, the row
    closed, nothing written, and the lapse recorded in the log channel naming Alex."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _fire(league)
    [lapse] = await _changes(league, LAPSE)
    assert (_lane(lapse), lapse["origin"]) == ("league", "BOT")
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None
    assert await _winner(league) == LEWIS
    assert f"<@{MEMBER_ID}>" in league.log() or "Alex" in league.log()


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_lapsed_amendment_tells_the_manager_who_ran_it_that_it_expired(
    tmp_path, monkeypatch,
):
    """Alex opens the amendment of round 3's Feature Race and types nothing. Its five minutes run
    out and the lapse runs: Alex, who ran `/results rounds amend`, is told privately that the
    amendment expired and that the command may be run again (results :460, #135), not only the
    log channel."""
    league = await _league(tmp_path, monkeypatch)
    interaction = await _ask_open(league)

    await _fire(league)
    await run_queue(league.bot)

    told = updated_reply(interaction)
    assert "expired" in told.lower()
    assert "/results rounds amend" in told


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_paste_typed_in_time_holds_the_lapse_though_its_turn_waits(tmp_path, monkeypatch):
    """The amendment's lane is stopped at another job when Alex pastes the Feature Race four
    minutes after the prompt; his turn waits. The five minutes run out and the lapse is asked:
    it finds a paste typed in time waiting, and neither deletes the channel nor closes the row."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await block_queue(league, lane=LANE)
    await _type(league, after=timedelta(minutes=4))

    await _fire(league)
    await run_queue(league.bot)

    assert not _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is None


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_lapse_reads_a_paste_typed_in_time_on_the_amendment_s_lane(tmp_path, monkeypatch):
    """As before, Alex's paste typed in time waits behind a stopped job on the amendment's lane
    when the lapse runs on the league lane and leaves the amendment open. Once the stopped job is
    retried and goes through, his paste's turn runs and stage one is recorded."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    holder = await block_queue(league, lane=LANE)
    await _type(league, after=timedelta(minutes=4))
    await _fire(league)
    await run_queue(league.bot)

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert not _deleted(league)
    assert await _winner(league) == MAX


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_half_hour_lapse_is_held_while_the_stage_two_prompt_is_in_hand(
    tmp_path, monkeypatch,
):
    """Stage one of Alex's amendment is recorded, but Discord refuses the stage-two prompt and
    the amendment's lane stops at it. Thirty-one minutes on, the half-hour sweep runs: the
    amendment is held (results spec :491), neither put back nor its channel deleted."""
    from leaguebot.results.services.result_submission_service import sweep_expired_amendments

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).fail_when = _stage_two
    await _type(league)
    assert await stopped_job(league.db_path, LANE) is not None

    await sweep_expired_amendments(league.bot, now=NOW + timedelta(minutes=31))
    await run_queue(league.bot)

    assert not _deleted(league)
    assert await _winner(league) == MAX
    [row] = await _amend_rows(league)
    assert row["closed_at"] is None


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_restart_asks_the_lapse_of_each_abandoned_amendment_before_the_queue_starts(
    tmp_path, monkeypatch,
):
    """The bot stops with Alex's amendment of round 3 open and nothing pasted. At the restart the
    recovery asks the queue for its lapse, as the bot, and deletes nothing itself; when the queue
    runs, the channel is deleted and the row closed."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path, monkeypatch)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by, prompt_posted_at) VALUES (?, ?, ?, ?, ?, ?)",
            (ROUND_ID, AMEND_CHANNEL, json.dumps(["FEATURE_RACE"]), NOW.isoformat(),
             MEMBER_ID, NOW.isoformat()),
        )
        await db.commit()

    await bot_module._recover_orphaned_amend_channels(league.bot)

    [lapse] = await _changes(league, LAPSE)
    assert lapse["origin"] == "BOT"
    assert not _deleted(league)

    await run_queue(league.bot)

    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_a_refused_channel_deletion_on_a_lapse_stops_the_queue(tmp_path, monkeypatch):
    """Alex's amendment lapses, and Discord refuses to delete its channel. The lapse is the
    league's, so the league lane stops at the deletion, the channel standing."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _fire(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "delete_channel"
    assert not _deleted(league)


@pytest.mark.xfail(strict=True, reason=NO_AMEND_ON_THE_QUEUE)
async def test_the_lapse_timer_is_armed_by_a_job_of_its_own_and_reads_its_row_again(
    tmp_path, monkeypatch,
):
    """Once the amendment opens, its five minutes are armed by an `arm_lapse` job after the job
    that posts the prompt, keyed to the amendment. Where a later prompt has since been recorded,
    the timer armed for the earlier one fires and asks nothing."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    [change] = await _changes(league, OPEN)
    names = [step["name"] for step in await step_rows(league.db_path, change["id"])]
    assert names.index("post_prompt") < names.index("arm_lapse")
    assert len(_armed(league)) == 1
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET prompt_posted_at = ?",
            ((NOW + timedelta(minutes=3)).isoformat(),),
        )
        await db.commit()

    await _fire(league)

    assert await _changes(league, LAPSE) == []
