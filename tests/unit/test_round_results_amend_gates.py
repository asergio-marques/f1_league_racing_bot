"""What `/round results amend` refuses, and how it asks which session to amend.

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
nothing reads and post nothing, and the command is reached from `/round` where a league with the
module off can still see it.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

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
    season=SimpleNamespace(id=SEASON_ID, season_number=7),
    divisions=None,
    rounds=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(return_value=season)
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

    cog = SeasonCog.__new__(SeasonCog)
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


def _choice(session_type: SessionType | None):
    if session_type is None:
        return None
    return SimpleNamespace(name=session_type.value, value=session_type.value)


async def _amend(cog, interaction, *, division="Pro", round_number=3, session=None):
    return await undecorate(SeasonCog.round_results_amend)(
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

    assert "Results & Standings module is not enabled" in _replied(interaction)
    interaction.response.defer.assert_not_awaited()


async def test_a_server_with_no_season_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_noseason")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _amend(cog, interaction, session=SessionType.FEATURE_RACE)

    assert "No active season" in _replied(interaction)


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


async def _choose(cog, interaction, *, answer: SessionType | None, cancel: bool = False):
    """Run the command with no session given, answering the selection view with *answer*."""
    original = discord.ui.View.wait

    async def _answer(self):
        if getattr(self, "selected", "missing") == "missing":
            return await original(self)
        if cancel:
            self.cancelled = True
        else:
            self.selected = answer
        return None

    discord.ui.View.wait = _answer  # type: ignore[assignment]
    try:
        await _amend(cog, interaction, session=None)
    finally:
        discord.ui.View.wait = original  # type: ignore[assignment]


async def test_a_manager_who_does_not_name_a_session_is_asked(tmp_path):
    """A round has up to four sessions and only one is being amended; guessing would amend
    the wrong race."""
    db_path = await _make_db(tmp_path, name="amend_ask")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=True)

    assert "Select the session to re-submit" in _replied(interaction)


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

    labels = [item.label for item in _sent_view(interaction).children]
    assert labels == ["Feature Qualifying", "Feature Race", "❌ Cancel"]


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

    labels = [item.label for item in _sent_view(interaction).children]
    assert labels == ["Feature Race", "❌ Cancel"]


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

    labels = [item.label for item in _sent_view(interaction).children]
    assert labels[:4] == [
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
    """The view carries no timeout of its own, so this is the case of a manager closing the
    ephemeral message — `selected` stays None and must not be read as a session."""
    db_path = await _make_db(tmp_path, name="amend_timeout")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _choose(cog, interaction, answer=None, cancel=False)

    assert "Amendment cancelled" in _replied(interaction)
    interaction.guild.create_text_channel.assert_not_awaited()
