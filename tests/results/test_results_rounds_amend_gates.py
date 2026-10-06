"""What `/results rounds amend` refuses, and how it asks which session to amend.

Issue #208. The command is four hundred lines and a live collection loop; this file covers
everything in front of that — the seven gates, and the session-selection step — because those
are what stand between a mistyped command and a destroyed classification.

**Amending is destructive, and that is why the gates matter.** Unlike a resubmission, amending
does not supersede: `amend_session_results` updates the header in place and deletes the round's
driver rows before re-inserting them, so the classification the league actually raced is gone
and no command puts it back. Every refusal here is a season's result not lost.

**Only a FINAL round may be amended.** A round still in review or appeals has a process running
that will change its results anyway, and amending underneath it would have the steward's work
land on rows that no longer exist. The refusal says *which* status is required rather than that
the round "cannot be amended", because a manager meeting it needs to know whether to wait or to
act.

**The session is asked for when it is not given.** A round has up to four sessions and only one
is being amended; guessing would amend the wrong race. The choice offers only the sessions the
round actually has, in racing order, and a manager who cancels gets nothing amended and no
channel created — the channel is the expensive, visible part, so nothing is created until the
choice is made.

**A superseded session is not offered.** It is not what the round is any more, and amending it
would write a correction onto results that were already replaced.

**The module has to be on.** Amending results with the results module off would write rows
nothing reads and post nothing, and a league with the module off can still see the command.
"""
from __future__ import annotations

import contextlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.models.season import SeasonStage

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.results.models.points_config import SessionType
from tests.support.undecorate import undecorate

SERVER_ID = 11408
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "amend_gates",
    sessions=(("FEATURE_RACE", "ACTIVE"),),
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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 7, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (ROUND_ID, DIVISION_ID),
        )
        for session_type, status in sessions:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name) VALUES (?, ?, ?, ?, 'Standard')",
                (ROUND_ID, DIVISION_ID, session_type, status),
            )
        await db.commit()
    return db_path


def _round(status: str = "FINAL", number: int = 3):
    return SimpleNamespace(
        id=ROUND_ID, division_id=DIVISION_ID, round_number=number, status=status
    )


def _make_cog(
    db_path: str,
    *,
    results_enabled: bool = True,
    season=SimpleNamespace(
        id=SEASON_ID, season_number=7, stage=SeasonStage.ONGOING
    ),
    divisions=None,
    rounds=None,
) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions
        if divisions is not None
        else [SimpleNamespace(id=DIVISION_ID, name="Pro", tier=1)]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else [_round()]
    )
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.config_service.get_server_config = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.guild = MagicMock()
    interaction.guild.create_text_channel = AsyncMock(
        side_effect=AssertionError("a channel was created before the session was chosen")
    )
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _sent_view(interaction):
    for call in interaction.followup.send.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


def _offered(interaction) -> list[str]:
    """The sessions the chooser offers, by label, in the order it lists them."""
    view = _sent_view(interaction)
    select = next(item for item in view.children if isinstance(item, discord.ui.Select))
    return [option.label for option in select.options]


def _choice(session_type: SessionType | None):
    if session_type is None:
        return None
    return SimpleNamespace(name=session_type.value, value=session_type.value)


async def _amend(cog, interaction, *, division="Pro", round_number=3, session=None):
    return await undecorate(ResultsCog.rounds_amend)(
        cog, interaction, division, round_number, _choice(session)
    )


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


async def test_the_results_module_must_be_on(tmp_path):
    """Amending with the module off would write rows nothing reads and post nothing, and
    the command is reached from `/round`, which a league with the module off still sees."""
    db_path = await _make_db(tmp_path, name="amend_module_off")
    cog = _make_cog(db_path, results_enabled=False)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    # Word for word: the results cog's own gate says "… not enabled on this server.", and
    # the command keeps its own words wherever it is declared (#462: only the names change).
    assert _replied(interaction) == "❌ The Results & Standings module is not enabled."
    interaction.response.defer.assert_not_awaited()
    # The module is checked before the season is read.
    cog.bot.season_service.get_setup_or_active_season.assert_not_awaited()


async def test_a_server_with_no_season_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_noseason")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    # The refusal names the archive rule (issue #224): a server whose only season is
    # completed or cancelled reaches this same branch, because the command now asks for
    # the *live* season and an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)


async def test_the_no_season_refusal_names_results_rounds_amend(tmp_path):
    """The season gate quotes back the name the command hands it, so a refusal naming a
    command that no longer exists sends a league admin looking for it. The repository's
    check of command names cannot see this one: the name is interpolated."""
    db_path = await _make_db(tmp_path, name="amend_noseason_name")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert _replied(interaction).startswith(
        "❌ `/results rounds amend` acts on the season this server is building or racing, "
        "and there is none."
    )


async def test_an_unknown_division_is_refused_by_name(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_nodiv")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, division="Rookie", session=SessionType.FEATURE_RACE)

    assert "Rookie" in _replied(interaction)
    assert "not found" in _replied(interaction)


async def test_the_division_is_matched_regardless_of_case(tmp_path):
    """Every other division lookup in the bot is case-insensitive, and this one destroys
    results — a manager should not meet a different rule here of all places."""
    db_path = await _make_db(tmp_path, name="amend_case")
    cog = _make_cog(db_path, rounds=[_round(status="AWAITING_RESULTS")])
    interaction = _interaction()

    await _amend(cog, interaction, division="pRo", session=SessionType.FEATURE_RACE)

    assert "not found" not in _replied(interaction)


async def test_an_unknown_round_is_refused_by_number(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_noround")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, round_number=9, session=SessionType.FEATURE_RACE)

    assert "Round 9 not found" in _replied(interaction)


@pytest.mark.parametrize(
    "status",
    ["NOT_RUN", "AWAITING_RESULTS", "AWAITING_REPORT_VERDICTS", "AWAITING_APPEAL_VERDICTS"],
)
async def test_a_round_that_has_not_reached_final_is_refused(tmp_path, status):
    """A round still in review or appeals has a process running that will change its
    results anyway, and amending underneath it would have the steward's work land on rows
    that no longer exist."""
    db_path = await _make_db(tmp_path, name=f"amend_{status}")
    cog = _make_cog(db_path, rounds=[_round(status=status)])
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "cannot be amended yet" in _replied(interaction)


async def test_the_refusal_names_the_status_that_is_required(tmp_path):
    """A manager meeting it needs to know whether to wait or to act."""
    db_path = await _make_db(tmp_path, name="amend_says_final")
    cog = _make_cog(db_path, rounds=[_round(status="AWAITING_RESULTS")])
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    replied = _replied(interaction)
    assert "FINAL" in replied
    assert "penalty review and appeals" in replied


async def test_a_round_with_no_results_is_refused(tmp_path):
    """Nothing to amend. A FINAL round with no active session results is one whose
    submission was superseded away, and the command would otherwise open a channel and
    collect a paste with nowhere to put it."""
    db_path = await _make_db(tmp_path, name="amend_noresults", sessions=())
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "No results found for this round" in _replied(interaction)


async def test_a_superseded_session_does_not_count_as_results(tmp_path):
    """It is not what the round is any more."""
    db_path = await _make_db(
        tmp_path, name="amend_superseded", sessions=(("FEATURE_RACE", "SUPERSEDED"),)
    )
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "No results found for this round" in _replied(interaction)


async def test_a_session_the_round_does_not_have_is_refused(tmp_path):
    """Chosen from the command's own list of four, which is the same list for every round —
    a normal round has no sprint to amend."""
    db_path = await _make_db(tmp_path, name="amend_wrongsession")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.SPRINT_RACE)

    assert "No SPRINT_RACE session found" in _replied(interaction)


# ---------------------------------------------------------------------------
# Choosing the session
# ---------------------------------------------------------------------------


async def _choose(
    cog, interaction, *, answer: list[SessionType] | None, cancel: bool = False
):
    """Run the command with no session given, answering the chooser with *answer*."""
    original = discord.ui.View.wait

    async def _answer(self):
        if getattr(self, "selected", "missing") == "missing":
            return await original(self)
        if cancel:
            self.cancelled = True
        else:
            self.selected = [st.value for st in (answer or [])]
        return None

    discord.ui.View.wait = _answer  # type: ignore[assignment]
    try:
        await _amend(cog, interaction, session=None)
    finally:
        discord.ui.View.wait = original  # type: ignore[assignment]


async def test_a_manager_who_does_not_name_a_session_is_asked(tmp_path):
    """A round has up to four sessions and any of them may be amended; guessing would amend
    the wrong race."""
    db_path = await _make_db(tmp_path, name="amend_ask")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert "Select the sessions to amend" in _replied(interaction)


async def test_only_the_sessions_the_round_has_are_offered(tmp_path):
    """Offering all four would let a manager pick one the round never ran and meet the
    refusal a step later, after the question rather than before it."""
    db_path = await _make_db(
        tmp_path,
        name="amend_offer",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "ACTIVE")),
    )
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert _offered(interaction) == ["Feature Qualifying", "Feature Race"]


async def test_a_superseded_session_is_not_offered(tmp_path):
    """Amending it would write a correction onto results that were already replaced."""
    db_path = await _make_db(
        tmp_path,
        name="amend_offer_superseded",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "SUPERSEDED")),
    )
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert _offered(interaction) == ["Feature Race"]


async def test_the_sessions_are_offered_in_racing_order(tmp_path):
    """Sprint before feature, qualifying before its race — a manager picking from a list in
    another order is working against their memory of the evening."""
    db_path = await _make_db(
        tmp_path,
        name="amend_offer_order",
        sessions=(
            ("FEATURE_RACE", "ACTIVE"),
            ("SPRINT_QUALIFYING", "ACTIVE"),
            ("FEATURE_QUALIFYING", "ACTIVE"),
            ("SPRINT_RACE", "ACTIVE"),
        ),
    )
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert _offered(interaction) == [
        "Sprint Qualifying",
        "Sprint Race",
        "Feature Qualifying",
        "Feature Race",
    ]


async def test_cancelling_the_choice_amends_nothing(tmp_path):
    """And creates no channel: the channel is the expensive, visible part, so nothing is
    made until the choice is made."""
    db_path = await _make_db(tmp_path, name="amend_cancel")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert "Amendment cancelled" in _replied(interaction)
    interaction.guild.create_text_channel.assert_not_awaited()


async def test_a_choice_that_times_out_amends_nothing(tmp_path):
    """A manager who closes the ephemeral message or lets it lapse chooses nothing, and an
    empty choice must not be read as a session."""
    db_path = await _make_db(tmp_path, name="amend_timeout")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=False)

    assert "Amendment cancelled" in _replied(interaction)
    interaction.guild.create_text_channel.assert_not_awaited()


async def test_several_sessions_can_be_chosen_at_once(tmp_path):
    """**One amendment for as many of the round's sessions as need correcting** (#345, decided
    2026-09-21). A round's reports and appeals are reviewed together, so the chooser takes any
    number, up to every session the round ran."""
    db_path = await _make_db(
        tmp_path,
        name="amend_offer_many",
        sessions=(("FEATURE_RACE", "ACTIVE"), ("FEATURE_QUALIFYING", "ACTIVE")),
    )
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    view = _sent_view(interaction)
    select = next(item for item in view.children if isinstance(item, discord.ui.Select))
    assert (select.min_values, select.max_values) == (1, 2)


# ---------------------------------------------------------------------------
# A division with a job on the queue (owner, 2026-10-05, #439 slice 2)
# ---------------------------------------------------------------------------

LATER_ROUND_ID = 22
OTHER_DIVISION_ID = 12
OTHER_ROUND_ID = 31


class _AmendmentWentOn(Exception):
    """Raised where the command creates the amendment's channel: it was not held back."""


async def _seed_queued_change(
    db_path: str,
    *,
    kind: str = "results.review.open",
    payload: dict | None = None,
    state: str = "QUEUED",
    stopped: bool = False,
) -> int:
    """A change of *kind* with *payload* (round 4's review opening by default), in *state*, with
    its one job; the job has failed and stops the queue where *stopped*. An earlier change of
    three jobs, long done, is seeded first, so that the job's number and its change's id differ,
    as they do in a league that has used the queue. Gives the job's number."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 4, '2026-02-08T18:00:00+00:00', 'NORMAL', 'AWAITING_RESULTS')",
            (LATER_ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Am', 2, 556)",
            (OTHER_DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', 'AWAITING_RESULTS')",
            (OTHER_ROUND_ID, OTHER_DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES ('hub.refresh', 'hub.refresh', '{}', 'BOT', 'DONE', 'refreshing the hub')"
        )
        for position in range(3):
            await db.execute(
                "INSERT INTO queued_change_steps (change_id, position, name, done_at) "
                "VALUES (?, ?, 'refresh', '2026-10-05T11:00:00+00:00')",
                (cursor.lastrowid, position),
            )
        payload = {"round_id": LATER_ROUND_ID} if payload is None else payload
        cursor = await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
            "VALUES (?, ?, ?, 'MEMBER', ?, 'a change of the test')",
            (kind, f"{kind}:{json.dumps(payload, sort_keys=True)}", json.dumps(payload), state),
        )
        change_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name, payload, done_at, "
            "tries, failing_since, last_failure) VALUES (?, 0, 'open', '{}', ?, ?, ?, ?)",
            (
                change_id,
                "2026-10-05T12:00:00+00:00" if state == "DONE" else None,
                1 if stopped else 0,
                "2026-10-05T12:00:00+00:00" if stopped else None,
                "OperationalError" if stopped else None,
            ),
        )
        job = cursor.lastrowid
        await db.commit()
    assert job is not None and job != change_id
    return job


def _logged(interaction) -> list[str]:
    """The lines the refusal recorded in the log channel, through the bot's output router."""
    return [str(call.args[0]) for call in interaction.client.output_router.post_log.await_args_list]


def _gate_interaction():
    """An admin's interaction whose log lines are caught, and whose command, if it goes on to
    create the amendment's channel, stops there with `_AmendmentWentOn`."""
    interaction = _interaction()
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.guild.create_text_channel = AsyncMock(side_effect=_AmendmentWentOn())
    return interaction


@pytest.mark.parametrize(
    ("state", "stopped"),
    [("QUEUED", False), ("RUNNING", False), ("RUNNING", True), ("QUEUED", True)],
    ids=["queued", "running", "running-stopped", "queued-stopped"],
)
async def test_a_round_is_not_amended_while_its_division_has_a_job_on_the_queue(
    tmp_path, state, stopped,
):
    """Owner, 2026-10-05: "It shouldn't be possible to amend a round of a division which has
    jobs in the queue", any job naming a round of the division, queued, running or stopped on a
    failure. Round 4's review opening is on the queue; amending round 3 of the same division is
    refused, naming the job it waits on (its own number, not its change's) and how to clear it,
    and recorded in the log as a refusal. Nothing is amended: no channel is created."""
    db_path = await _make_db(tmp_path, name=f"amend_queued_{state}_{stopped}")
    job = await _seed_queued_change(db_path, state=state, stopped=stopped)
    cog = _make_cog(db_path)
    interaction = _gate_interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    replied = _replied(interaction)
    assert f"job #{job}" in replied, "the refusal does not name the job it waits on"
    assert "Retry" in replied and "Discard" in replied
    [line] = _logged(interaction)
    assert line.startswith("⛔") and f"job #{job}" in line
    interaction.guild.create_text_channel.assert_not_called()
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM round_amend_channels")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.xfail(strict=True, reason="#439: the refusal names job #0 where only the close is left")
async def test_a_change_with_only_its_close_left_holds_the_amendment_without_a_job_number(tmp_path):
    """Round 4's review opening has done its every job and only its close is left: it still holds
    the division, and the refusal leaves out the job number rather than naming job #0, as the
    points approval's own refusals do."""
    db_path = await _make_db(tmp_path, name="amend_close_only")
    job = await _seed_queued_change(db_path, state="RUNNING")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE queued_change_steps SET done_at = '2026-10-05T12:00:00+00:00' WHERE id = ?",
            (job,),
        )
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _gate_interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    replied = _replied(interaction)
    assert replied.startswith("⏸️ A round of Pro has a job on the change queue, so it cannot be")
    assert "job #" not in replied
    [line] = _logged(interaction)
    assert line.startswith("⛔") and "job #" not in line
    interaction.guild.create_text_channel.assert_not_called()


async def test_a_job_naming_the_division_alone_still_blocks_the_amendment(tmp_path):
    """A change whose payload names the division and no round of it is the division's job too."""
    db_path = await _make_db(tmp_path, name="amend_queued_division")
    job = await _seed_queued_change(
        db_path, kind="results.reports.approve", payload={"division_id": DIVISION_ID},
    )
    cog = _make_cog(db_path)
    interaction = _gate_interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert f"job #{job}" in _replied(interaction)
    interaction.guild.create_text_channel.assert_not_called()


@pytest.mark.parametrize(
    "seed",
    [
        {"payload": {"round_id": OTHER_ROUND_ID}},
        {"payload": {"round_id": OTHER_ROUND_ID, "division_id": OTHER_DIVISION_ID},
         "kind": "results.reports.approve"},
        {"state": "DONE"},
        {"kind": "module.off:results", "payload": {"cascade_attendance": False}},
    ],
    ids=["another-division", "another-division-by-id", "finished", "bot-wide"],
)
async def test_a_job_that_is_not_the_division_s_in_hand_does_not_hold_the_amendment(
    tmp_path, seed,
):
    """Only a job naming this division, and still in hand, holds the amendment back: one for
    another division, one done, or a bot-wide one (turning results off) does not. The command
    goes on to create the amendment's channel, and nothing is refused."""
    db_path = await _make_db(tmp_path, name=f"amend_not_held_{len(str(seed))}")
    await _seed_queued_change(db_path, **seed)
    cog = _make_cog(db_path)
    interaction = _gate_interaction()

    with pytest.raises(_AmendmentWentOn):
        await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "job #" not in _replied(interaction)
    assert _logged(interaction) == []


# ---------------------------------------------------------------------------
# A points approval of the season on the queue (owner, 2026-10-06, "Refuse it", #439 slice 3)
#
# Approving a change to the season's points reposts every round of every division, so while one
# is in hand no round of the season is amended: the approval's reposts would publish the
# amendment's corrections before they were approved. The refusal is r1-1's, word for word.
# ---------------------------------------------------------------------------

POINTS_KIND = "results.points_amendment.approve"
POINTS_PAYLOAD = {"season_id": SEASON_ID, "season_number": 7}

_PRO = SimpleNamespace(id=DIVISION_ID, name="Pro", tier=1)
_AM = SimpleNamespace(id=OTHER_DIVISION_ID, name="Am", tier=2)


def _division_cog(db_path: str, division: str):
    """The cog, its season holding Pro and Am, with round 3 of *division* FINAL."""
    division_id = DIVISION_ID if division == "Pro" else OTHER_DIVISION_ID
    round_id = ROUND_ID if division == "Pro" else OTHER_ROUND_ID
    return _make_cog(
        db_path,
        divisions=[_PRO, _AM],
        rounds=[SimpleNamespace(id=round_id, division_id=division_id, round_number=3,
                                status="FINAL")],
    )


@pytest.mark.parametrize("division", ["Pro", "Am"])
@pytest.mark.parametrize(
    ("state", "stopped"),
    [("QUEUED", False), ("RUNNING", False), ("RUNNING", True)],
    ids=["waiting", "running", "stopped"],
)
async def test_a_round_is_not_amended_while_a_points_approval_of_its_season_is_in_hand(
    tmp_path, state, stopped, division,
):
    """Season 7's points approval is on the queue, waiting, being carried out, or stopped at a
    failed job; its payload names the season alone, no division and no round. Amending round 3
    of Pro, or of Am, is refused in r1-1's words, naming the approval's job and how to clear it,
    with one refusal line in the log; no amendment is opened and no channel created."""
    db_path = await _make_db(tmp_path, name=f"amend_points_{state}_{stopped}_{division}")
    job = await _seed_queued_change(
        db_path, kind=POINTS_KIND, payload=POINTS_PAYLOAD, state=state, stopped=stopped,
    )
    cog = _division_cog(db_path, division)
    interaction = _gate_interaction()

    with contextlib.suppress(_AmendmentWentOn):
        await _amend(cog, interaction, division=division, session=SessionType.FEATURE_RACE)

    assert _replied(interaction) == (
        f"\u23f8\ufe0f A round of {division} has a job on the change queue (job #{job}), so it "
        "cannot be amended until that is done. Let it finish, or press **Retry** or **Discard** "
        "on its notice if it has stopped, then amend again."
    )
    [line] = _logged(interaction)
    assert line.startswith("\u26d4") and f"job #{job}" in line
    interaction.guild.create_text_channel.assert_not_called()
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM round_amend_channels")
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.parametrize("state", ["DONE", "DISCARDED"], ids=["finished", "discarded"])
async def test_a_finished_or_discarded_points_approval_does_not_hold_the_amendment(
    tmp_path, state,
):
    """Season 7's points approval has finished, or a league admin discarded it: it is no longer
    in hand, so amending round 3 of Pro goes on to create the amendment's channel, nothing
    refused and nothing logged."""
    db_path = await _make_db(tmp_path, name=f"amend_points_over_{state}")
    await _seed_queued_change(db_path, kind=POINTS_KIND, payload=POINTS_PAYLOAD, state=state)
    cog = _division_cog(db_path, "Pro")
    interaction = _gate_interaction()

    with pytest.raises(_AmendmentWentOn):
        await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "job #" not in _replied(interaction)
    assert _logged(interaction) == []


@pytest.mark.parametrize(
    "points_first",
    [
        pytest.param(False, id="review-job-first"),
        pytest.param(True, id="points-approval-first"),
    ],
)
async def test_a_review_job_and_a_points_approval_name_the_one_nearer_its_turn(
    tmp_path, points_first,
):
    """Both round 4 of Pro's review opening and season 7's points approval are waiting on the
    queue, asked in either order. Amending round 3 of Pro is refused naming the job of whichever
    was asked first, the one nearer its turn, and not the other."""
    db_path = await _make_db(tmp_path, name=f"amend_points_and_review_{points_first}")
    seeds = [
        {"kind": POINTS_KIND, "payload": POINTS_PAYLOAD},
        {},
    ]
    if not points_first:
        seeds.reverse()
    first = await _seed_queued_change(db_path, **seeds[0])
    second = await _seed_queued_change(db_path, **seeds[1])
    cog = _division_cog(db_path, "Pro")
    interaction = _gate_interaction()

    with contextlib.suppress(_AmendmentWentOn):
        await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    replied = _replied(interaction)
    assert f"job #{first})" in replied
    assert f"job #{second})" not in replied
