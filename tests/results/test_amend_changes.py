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
Max at team 3001, shorthand FER. The manager is Alex (4242).
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
    discard_job,
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
                "config_name) VALUES (?, ?, 'FEATURE_QUALIFYING', 'ACTIVE', ?)",
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
    """What the amend channel shows of what the bot posted there: the messages standing, or,
    once the channel is deleted, those it held as it went."""
    amend = league.channel(AMEND_CHANNEL)
    shown = {**amend.held_at_deletion, **amend.messages}
    return [str(shown[mid].content) for mid in league.sent_to(AMEND_CHANNEL) if mid in shown]


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


async def test_each_amended_paste_is_deleted_and_saved_as_a_draft_by_its_turn(
    tmp_path, monkeypatch,
):
    """Alex amends round 3's Qualifying and Feature Race, and pastes the Qualifying. His message
    is marked ⏳ at once and a turn is asked on the amendment's lane; nothing is saved yet. When the
    turn runs, his paste is deleted, the Qualifying is saved as a draft of the amendment, the
    round's own results are untouched, and the Feature Race's prompt follows."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _ask_open(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))

    message = await _type(league, QUALI_PASTE, run=False)

    assert MARK in message.marks
    [turn] = await _changes(league, TURN)
    assert _lane(turn) == LANE
    assert await _drafts(league) == []

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert message.gone
    assert await _drafts(league) == ["FEATURE_QUALIFYING"]
    assert await _winner(league) == LEWIS
    assert "Feature Race" in _texts(league)[-1]


async def test_an_amended_paste_while_its_turn_waits_is_turned_away(tmp_path, monkeypatch):
    """Alex pastes the Feature Race, and before its turn has run pastes it again. The second
    message is turned away at once: deleted, with a note in the channel, and no second turn is
    asked; the first stays marked ⏳. The refusal is logged: one ⛔ line naming Alex, round 3 and
    the Feature Race still waited on (results :257)."""
    from tests.support.change_queue import queued_log_lines

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    first = await _type(league, run=False)
    sent_before = len(league.sent_to(AMEND_CHANNEL))

    second = await _type(league, run=False)

    assert second.gone
    assert MARK in first.marks
    assert len(await _changes(league, TURN)) == 1
    assert len(league.sent_to(AMEND_CHANNEL)) == sent_before + 1
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    assert logged.count("⛔") == 1, logged
    line = logged[logged.index("⛔"):]
    assert f"<@{MEMBER_ID}>" in line and "Round 3" in line and "Feature Race" in line, line


async def test_a_refused_amended_paste_ends_the_whole_amendment_writing_nothing(
    tmp_path, monkeypatch,
):
    """Alex amends round 3's Qualifying and Feature Race. His Qualifying paste is accepted as a
    draft; his Feature Race paste names a driver outside the division and is refused. The whole
    amendment ends (results spec :459): the drafts are dropped, the refusal is said, the channel is
    deleted and its row closed, and the round's results stand as they were."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _ask_open(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))
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


async def test_a_restart_leaves_a_refused_deletion_to_the_amendment_s_own_lane(
    tmp_path, monkeypatch,
):
    """Alex's paste is refused and Discord refuses to delete the amendment channel, so only the
    amendment's lane is stopped. The bot restarts: the recovery asks the lapse of the closed
    amendment, but its channel's deletion is in hand on the amendment's lane, so the lapse is not
    due and the league lane is not stopped by a second try at the same refusal."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    await _type(league, "1, <@103>, FER, 1:30:00.000, 1:20.000, N/A")
    assert await stopped_job(league.db_path, LANE) is not None

    await bot_module._recover_orphaned_amend_channels(league.bot)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path, "league") is None
    assert await _changes(league, LAPSE) == []
    assert await stopped_job(league.db_path, LANE) is not None


# ---------------------------------------------------------------------------
# The configuration choice (C60)
# ---------------------------------------------------------------------------


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


def _amendment_picker(league: ReviewLeague) -> Any:
    """The message standing in the amendment channel that offers the configurations."""
    [picker] = [message for message in league.channel(AMEND_CHANNEL).messages.values()
                if message.view is not None
                and "Select the points configuration" in str(message.content)]
    return picker


async def _press_choice(
    league: ReviewLeague, picker: Any, label: str, *, after: timedelta = timedelta(0),
) -> Any:
    """Alex presses *label* on *picker*, *after* now; gives the press, its edit of the message and
    its private answers recorded."""
    button = next(item for item in picker.view.children if item.label == label)
    press = member_interaction(league.bot, user=_alex(), created_at=NOW + after)
    press.guild = league.guild
    press.channel_id = AMEND_CHANNEL
    press.message = picker
    press.response.edit_message = AsyncMock()
    await button.callback(press)
    return press


def _privately(press: Any) -> list[str]:
    """Every message *press* was answered with, seen by its presser alone, in order."""
    calls = [*press.response.send_message.await_args_list, *press.followup.send.await_args_list]
    return [str(call.args[0] if call.args else call.kwargs.get("content", ""))
            for call in calls if call.kwargs.get("ephemeral")]


@pytest.mark.xfail(
    strict=True,
    reason="a second press edits the amendment's picker to name its own choice before the queue "
           "refuses it",
)
async def test_a_second_press_of_the_amendment_s_picker_is_refused_and_the_first_choice_holds(
    tmp_path, monkeypatch,
):
    """The season has two points configurations attached and the Feature Race carries neither.
    Alex pastes the Feature Race and its turn offers the configurations. He presses "Heavy", then
    "Standard" on the same picker. The second press does not touch the message and is refused,
    seen by him alone, naming Heavy; one configuration turn is asked, and when the queue runs the
    amended Feature Race is written scored by Heavy."""
    league = await _league(tmp_path, monkeypatch, configs=("Standard", HEAVY))
    await _ask_open(league)
    await _type(league)
    picker = _amendment_picker(league)

    await _press_choice(league, picker, HEAVY)
    second = await _press_choice(league, picker, "Standard")
    await run_queue(league.bot)

    second.response.edit_message.assert_not_awaited()
    [told] = _privately(second)
    assert told.startswith("⚠️") and "already been chosen" in told and HEAVY in told, told
    assert len(await _changes(league, CONFIG)) == 1
    assert await stopped_job(league.db_path) is None
    [session] = await _rows(league,
        "SELECT config_name FROM session_results WHERE round_id = ? "
        "AND session_type = 'FEATURE_RACE'", ROUND_ID)
    assert session["config_name"] == HEAVY


# ---------------------------------------------------------------------------
# Stage one and its stage-two prompt (C61)
# ---------------------------------------------------------------------------


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


async def test_a_message_typed_while_stage_one_is_in_hand_is_turned_away(tmp_path, monkeypatch):
    """The league lane is stopped, and Alex pastes the amendment's last session: its turn runs on
    the amendment's lane and hands stage one to the league, which waits behind the stopped job. A
    message Alex types meanwhile is turned away at once, as one typed while a turn waits is: it is
    deleted, a note is left in the channel, no turn is asked for it, and the refusal is logged."""
    from tests.support.change_queue import queued_log_lines

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    await block_queue(league)
    await _type(league)
    [stage_one] = await _changes(league, STAGE_ONE)
    assert stage_one["state"] == "QUEUED"
    sent_before = len(league.sent_to(AMEND_CHANNEL))

    second = await _type(league, run=False)

    assert second.gone
    assert len(await _changes(league, TURN)) == 1
    assert len(league.sent_to(AMEND_CHANNEL)) == sent_before + 1
    logged = "\n".join(await queued_log_lines(league.db_path)) + league.log()
    assert logged.count("⛔") == 1, logged


async def test_stage_one_s_line_says_what_was_recorded_and_that_nothing_is_published_yet(
    tmp_path, monkeypatch,
):
    """Alex pastes the amended Feature Race, scored under Standard. When the queue runs, stage
    one's change writes one line to the log channel: "AMEND_STAGE_1 | Recorded", naming Alex by
    mention, round 3, the session with the configuration it was scored under
    ("FEATURE_RACE=Standard"), and that nothing is published until the report and appeal stages
    are approved. No "RESULT_AMENDED" is written: that belongs to the final stage (#345)."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)

    await _type(league)

    [line] = [sent for sent in league.bot.log_channel.sent
              if "AMEND_STAGE_1 | Recorded" in str(sent)]
    assert f"<@{MEMBER_ID}>" in line
    assert "round: 3" in line
    assert "FEATURE_RACE=Standard" in line
    assert "Nothing is published until" in line
    assert "RESULT_AMENDED" not in league.log()


async def test_a_stage_one_leaving_out_a_driver_with_a_verdict_is_refused_and_ends_the_amendment(
    tmp_path, monkeypatch,
):
    """Max carries a penalty in round 3's Feature Race, and Alex pastes a corrected classification
    with Lewis alone. Stage one is refused when it runs: its save is undone whole (no snapshot is
    kept, Lewis still wins, Max's row and his penalty stand), the refusal naming Max is said in the
    amendment channel and written to the log channel as a refusal, and the amendment ends as a
    refused paste ends it: the drafts dropped, the row closed and the channel deleted. Neither
    lane is left stopped."""
    league = await _league(tmp_path, monkeypatch)
    async with get_connection(league.db_path) as db:
        [max_row] = await (await db.execute(
            "SELECT id FROM race_session_results WHERE driver_user_id = ?", (MAX,)
        )).fetchall()
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 5, 'Contact at turn 1', 'Wholly at fault', '9001', "
            "'2026-02-02T00:00:00+00:00')",
            (max_row["id"],),
        )
        await db.commit()
    await _ask_open(league)

    await _type(league, "1, <@101>, FER, 1:30:00.000, 1:20.000, N/A")

    assert await stopped_job(league.db_path, "league") is None
    assert await stopped_job(league.db_path, LANE) is None
    [stage_one] = await _changes(league, STAGE_ONE)
    assert stage_one["state"] == "DONE"
    [row] = await _amend_rows(league)
    assert row["pre_amendment_state"] is None
    assert row["closed_at"] is not None
    assert await _drafts(league) == []
    assert await _winner(league) == LEWIS
    assert sorted(await race_rows(league.db_path)) == [LEWIS, MAX]
    assert any("<@102>" in text and "leaves out" in text for text in _texts(league))
    [refusal] = [line for line in league.bot.log_channel.sent if str(line).startswith("⛔ ")]
    assert "<@102>" in str(refusal)
    assert "AMEND_STAGE_1" not in league.log()
    assert _deleted(league)
    assert await _rows(league, "SELECT id FROM penalty_records") != []


async def test_a_refused_post_of_stage_one_s_refusal_stops_only_the_amendment_s_lane(
    tmp_path, monkeypatch,
):
    """Max carries a penalty in round 3's Feature Race and Alex pastes a classification with Lewis
    alone, so stage one is refused; Discord refuses the refusal's post into the amendment channel.
    The refusal is the amendment's lane's to post (decision 23): that lane stops with the post and
    the channel standing, and the league lane, which recorded the ending, does not."""
    league = await _league(tmp_path, monkeypatch)
    async with get_connection(league.db_path) as db:
        [max_row] = await (await db.execute(
            "SELECT id FROM race_session_results WHERE driver_user_id = ?", (MAX,)
        )).fetchall()
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 5, 'Contact at turn 1', 'Wholly at fault', '9001', "
            "'2026-02-02T00:00:00+00:00')",
            (max_row["id"],),
        )
        await db.commit()
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).fail_when = lambda content, _kwargs: "leaves out" in str(content)

    await _type(league, "1, <@101>, FER, 1:30:00.000, 1:20.000, N/A")

    assert await stopped_job(league.db_path, "league") is None
    assert await stopped_job(league.db_path, LANE) is not None
    [stage_one] = await _changes(league, STAGE_ONE)
    assert stage_one["state"] == "DONE"
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None
    assert not _deleted(league)
    assert not any("leaves out" in text for text in _texts(league))


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


async def test_a_cancel_asked_before_the_last_paste_hands_stage_one_over_ends_the_amendment(
    tmp_path, monkeypatch,
):
    """The league lane is stopped at another job. Alex presses Cancel Amendment, which waits on the
    league lane, and then pastes the Feature Race: his turn runs on the amendment's lane and asks
    stage one of the league behind the cancel. Once the stopped job goes through, the cancel runs
    first: stage one asked after it is no stage in hand ahead of it, so the cancel is not refused,
    the amendment ends with the round as it stood, and stage one is found no longer due."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    holder = await block_queue(league)
    await _press_cancel(league)
    await _type(league)
    [cancel] = await _changes(league, CANCEL)
    [stage_one] = await _changes(league, STAGE_ONE)
    assert cancel["id"] < stage_one["id"]

    holder["fail"] = False
    await retry_job(league.bot)

    [cancel] = await _changes(league, CANCEL)
    [stage_one] = await _changes(league, STAGE_ONE)
    assert cancel["state"] == "DONE"
    assert stage_one["state"] == "DROPPED"
    assert await stopped_job(league.db_path) is None
    assert await _winner(league) == LEWIS
    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None


# ---------------------------------------------------------------------------
# The lapse (C63, C64)
# ---------------------------------------------------------------------------


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


async def test_an_unanswered_configuration_choice_lapses_and_tells_the_manager_it_expired(
    tmp_path, monkeypatch,
):
    """The season has Standard and Heavy attached and the Feature Race carries neither. Alex opens
    the amendment and, two minutes on, pastes the Feature Race: its turn posts the configuration
    picker, records when it appeared, and arms the five minutes again from then. Nobody presses
    it. The five minutes armed for the picker run out and the lapse runs: the channel is deleted
    and the row closed, nothing is written (Lewis still leads), the lapse is recorded in the log
    channel naming Alex, and Alex is told privately that the amendment expired and that
    `/results rounds amend` may be run again (results :460, #135)."""
    league = await _league(tmp_path, monkeypatch, configs=("Standard", HEAVY))
    interaction = await _ask_open(league)
    getattr(league.bot, "_change_queue_test_setup").clock.advance(minutes=2)

    await _type(league, after=timedelta(minutes=2))

    assert [message for message in league.channel(AMEND_CHANNEL).messages.values()
            if message.view is not None
            and "Select the points configuration" in str(message.content)]
    [row] = await _amend_rows(league)
    assert row["prompt_posted_at"] == (NOW + timedelta(minutes=2)).isoformat()
    *_, last = _armed(league)
    assert last[2] == NOW + timedelta(minutes=7)

    await _fire(league)
    [lapse] = await _changes(league, LAPSE)
    assert (_lane(lapse), lapse["origin"]) == ("league", "BOT")
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None
    assert await _winner(league) == LEWIS
    assert await _drafts(league) == []
    assert f"<@{MEMBER_ID}>" in league.log() or "Alex" in league.log()
    told = updated_reply(interaction)
    assert "expired" in told.lower()
    assert "/results rounds amend" in told


async def test_a_lapse_after_the_managers_reply_can_no_longer_be_updated_tells_the_log_alone(
    tmp_path, monkeypatch,
):
    """The queue keeps the manager's reply for the lapse only while it could still be updated, as
    long as Discord lets it be. Alex opens the amendment; the bot is late and the timer's lapse
    runs fifteen minutes on. The channel is deleted and the lapse recorded in the log channel
    naming him, but his reply is not updated: it still says what the opening told him."""
    league = await _league(tmp_path, monkeypatch)
    interaction = await _ask_open(league)
    told = updated_reply(interaction)
    getattr(league.bot, "_change_queue_test_setup").clock.advance(minutes=15)

    await _fire(league)
    await run_queue(league.bot)

    assert _deleted(league)
    assert f"<@{MEMBER_ID}>" in league.log()
    assert updated_reply(interaction) == told
    assert "expired" not in updated_reply(interaction).lower()


@pytest.mark.parametrize("cause", ["timer", "sweep", "restart"])
async def test_a_lapse_asked_for_a_closed_amendment_is_dropped_unless_a_restart_finishes_it(
    tmp_path, monkeypatch, cause,
):
    """An amendment that ended but whose channel could not be deleted is kept, closed, for a
    restart to finish. Asked for that closed amendment's lapse, the timer and the sweep find it not
    due and ask nothing; a restart asks it, and when it runs the channel is deleted and the row
    forgotten, with nothing said in the log channel."""
    from leaguebot.results.services.amend_end_changes import ask_lapse

    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_amend_channels SET closed_at = ?", (NOW.isoformat(),))
        await db.commit()
    [row] = await _amend_rows(league)

    asked = await ask_lapse(
        league.bot.change_queue, league.db_path, int(row["id"]), cause=cause,
        armed_for=row["prompt_posted_at"],
    )
    await run_queue(league.bot)

    if cause != "restart":
        assert asked is None
        assert await _changes(league, LAPSE) == []
        assert not _deleted(league)
    else:
        assert asked is not None
        assert _deleted(league)
        assert await _amend_rows(league) == []
        assert "lapsed" not in league.log() and "restart" not in league.log()


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


async def test_a_paste_typed_after_the_five_minutes_is_not_taken(tmp_path, monkeypatch):
    """Alex pastes the Feature Race six minutes after the prompt appeared, before the timer's lapse
    has run (results :460, Q18: an answer counts only if typed within the five minutes). His turn
    finds it no longer due: nothing is saved, the round is untouched and no next prompt follows.
    The lapse then ends the amendment."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    sent_before = len(league.sent_to(AMEND_CHANNEL))

    await _type(league, after=timedelta(minutes=6))

    assert await _drafts(league) == []
    assert await _winner(league) == LEWIS
    assert len(league.sent_to(AMEND_CHANNEL)) == sent_before
    assert not _deleted(league)

    await _fire(league)
    await run_queue(league.bot)

    assert _deleted(league)
    [row] = await _amend_rows(league)
    assert row["closed_at"] is not None
    assert await _winner(league) == LEWIS


async def test_a_late_choice_of_configuration_is_not_taken(tmp_path, monkeypatch):
    """The season has Standard and Heavy attached, and Alex pastes the Feature Race at once: the
    picker appears. He presses Heavy six minutes later, before the lapse has run. The press is
    judged by when it was pressed: its turn finds it no longer due, nothing is held, and the lapse
    ends the amendment."""
    league = await _league(tmp_path, monkeypatch, configs=("Standard", HEAVY))
    await _ask_open(league)
    await _type(league)
    [picker] = [message for message in league.channel(AMEND_CHANNEL).messages.values()
                if message.view is not None
                and "Select the points configuration" in str(message.content)]
    button = next(item for item in picker.view.children if item.label == HEAVY)
    interaction = member_interaction(
        league.bot, user=_alex(), created_at=NOW + timedelta(minutes=6)
    )
    interaction.guild = league.guild
    interaction.channel_id = AMEND_CHANNEL
    interaction.message = picker
    interaction.response.edit_message = AsyncMock()

    await button.callback(interaction)
    await run_queue(league.bot)

    assert await _drafts(league) == []
    assert not _deleted(league)

    await _fire(league)
    await run_queue(league.bot)

    assert _deleted(league)
    assert await _winner(league) == LEWIS


async def test_a_lapse_queued_for_an_earlier_prompt_does_not_swallow_the_next_prompt_s(
    tmp_path, monkeypatch,
):
    """Alex amends the Qualifying and the Feature Race. The league lane is stopped, and the first
    prompt's five minutes run out: its lapse is asked and waits. Just before they ran out Alex
    pasted the Qualifying, whose turn ran on the amendment's lane at once; the Feature Race's prompt
    appeared and armed five minutes of its own. When that timer fires its lapse is asked as well,
    not taken for a repeat of the one still waiting: two lapses are on the queue."""
    league = await _league(tmp_path, monkeypatch, qualifying=True)
    await _ask_open(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))
    await block_queue(league)
    await _fire(league)
    getattr(league.bot, "_change_queue_test_setup").clock.advance(minutes=4)
    await _type(league, QUALI_PASTE, after=timedelta(minutes=4))
    assert await _drafts(league) == ["FEATURE_QUALIFYING"]

    await _fire(league)

    assert len(await _changes(league, LAPSE)) == 2


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

    await sweep_expired_amendments(league.bot, queue=league.bot.change_queue, now=NOW + timedelta(minutes=31))
    await run_queue(league.bot)

    assert not _deleted(league)
    assert await _winner(league) == MAX
    [row] = await _amend_rows(league)
    assert row["closed_at"] is None


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


async def test_a_lapse_is_named_in_the_log_channel_by_its_round_and_division(
    tmp_path, monkeypatch,
):
    """Alex's amendment of round 3 lapses and Discord refuses to delete its channel. The stop
    notice in the log channel names the amendment by its round number and division and says why it
    ended in words, not by the round's database id nor by the cause's code word."""
    league = await _league(tmp_path, monkeypatch)
    await _ask_open(league)
    league.channel(AMEND_CHANNEL).delete_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )

    await _fire(league)
    await run_queue(league.bot)

    log = league.log()
    assert "ending the amendment of round 3 (Pro): its five minutes ran out" in log, log
    assert f"round {ROUND_ID}'s" not in log and "(timer)" not in log


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


async def _discarded_opening(league: ReviewLeague) -> str:
    """Alex's amendment is asked and run; an admin discards the job the league lane stopped at,
    and the queue runs on. Gives Alex's reply as it was last updated."""
    interaction = await _ask_open(league)
    assert await stopped_job(league.db_path, "league") is not None
    await discard_job(league.bot, lane="league")
    assert await stopped_job(league.db_path) is None
    return updated_reply(interaction)


async def test_a_discarded_channel_creation_is_worded_in_the_managers_reply(tmp_path, monkeypatch):
    """Discord refuses to create the amendment channel and an admin discards the job. Nothing is
    opened, and Alex's reply, which would otherwise stay at "under way" for good, says so."""
    league = await _league(tmp_path, monkeypatch)
    league.create_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")

    told = await _discarded_opening(league)

    assert "was not created" in told
    assert "✅" not in told
    assert await _amend_rows(league) == []


async def test_a_discarded_stale_deletion_names_the_channel_left_to_delete_by_hand(
    tmp_path, monkeypatch,
):
    """The finished amendment's channel (706) will not delete and an admin discards that job. The
    new amendment opens and its row replaces the stale one, the only record of 706, so Alex's reply
    names 706 as left standing."""
    league = await _league(tmp_path, monkeypatch)
    stale = channel(STALE_CHANNEL, league.events)
    stale.delete_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    league.channels[STALE_CHANNEL] = stale
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "started_by, closed_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, 77, ?)",
            (ROUND_ID, STALE_CHANNEL, NOW.isoformat(), NOW.isoformat()),
        )
        await db.commit()

    told = await _discarded_opening(league)

    assert f"✅ Amendment channel created: <#{AMEND_CHANNEL}>" in told
    assert f"<#{STALE_CHANNEL}> was not deleted" in told
    assert [row["channel_id"] for row in await _amend_rows(league)] == [AMEND_CHANNEL]


async def test_a_discarded_opening_message_is_worded_in_the_managers_reply(tmp_path, monkeypatch):
    """Discord refuses the amendment's opening message and an admin discards it. The channel and
    the first prompt stand, and Alex's reply says the opening was not posted."""
    league = await _league(tmp_path, monkeypatch)
    league.channel(AMEND_CHANNEL).fail_when = lambda content, _kwargs: "Amend Results" in str(content)

    told = await _discarded_opening(league)

    assert f"✅ Amendment channel created: <#{AMEND_CHANNEL}>" in told
    assert f"The opening message was not posted in <#{AMEND_CHANNEL}>" in told
    [row] = await _amend_rows(league)
    assert row["prompt_posted_at"] is not None


async def test_a_discarded_prompt_says_no_time_limit_runs(tmp_path, monkeypatch):
    """Discord refuses the first session's prompt and an admin discards it. With no time recorded
    for it nothing times the amendment out, and Alex's reply says the prompt was not posted."""
    league = await _league(tmp_path, monkeypatch)
    league.channel(AMEND_CHANNEL).fail_when = (
        lambda content, _kwargs: "Amend Results" not in str(content)
    )

    told = await _discarded_opening(league)

    assert f"✅ Amendment channel created: <#{AMEND_CHANNEL}>" in told
    assert f"prompt was not posted in <#{AMEND_CHANNEL}>" in told
    [row] = await _amend_rows(league)
    assert row["prompt_posted_at"] is None
