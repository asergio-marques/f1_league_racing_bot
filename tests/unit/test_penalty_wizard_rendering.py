"""The penalty review wizard — its permission gate, its arithmetic, and the prompt it draws.

Issue #208. `penalty_wizard.py` was the worst-covered file in the results module at 28.7%. It
is entirely button-driven, which is why so little of it was reachable from a test and why the
one defect it did have went unnoticed for so long.

**The permission gate is the part with history.** `_is_league_manager` used to ask for the
interaction role and nothing else, so a league *admin* who did not also hold that role was
refused all thirteen buttons of the penalty and appeals reviews — the same defect the commands
had (issue #116), on the half of the bot where nobody would notice, because nothing here is a
command anybody could run and see refused. `test_a_league_admin_may_drive_the_review` is the
regression test for exactly that, and it is the reason this file leads with the gate rather
than the rendering.

**`_parse_penalty_seconds` never raises and never guesses.** It reads whatever the submission
validator stored — four different shapes, plus `"N/A"`, plus `None` — and returns `0` for
anything it cannot read. Returning zero rather than raising is deliberate: a penalty review
that died on one malformed cell would block the whole round's approval, and zero is the value
that changes nothing.

**The prompt is the only thing a league manager sees.** They approve a round from it, so every
staged penalty and pardon must appear on it — a staged penalty missing from the prompt would be
applied on approval without anybody having read it. The empty cases matter as much: "none" is
printed explicitly, because a blank section reads as a rendering failure rather than as nothing
staged.

Test drivers share one Discord account, so the display name is carried into every mention;
without it a test-mode review shows the same name against every penalty.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.session import SessionType  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.penalty_wizard import (  # noqa: E402
    PenaltyReviewState,
    StagedPardon,
    _is_league_manager,
    _parse_penalty_seconds,
    _pen_label,
    _render_prompt_content,
    _require_lm,
)

SERVER_ID = 9508
ROUND_ID = 5
DIVISION_ID = 11
DRIVER_A = 4001
DRIVER_B = 4002


# ---------------------------------------------------------------------------
# _parse_penalty_seconds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5.000", 5),
        ("5", 5),
        ("1:05.000", 65),
        ("0:30.500", 30),
        ("1:00:00.000", 3600),
        ("1:01:01.000", 3661),
        ("+5.000", 5),
        ("  5.000  ", 5),
    ],
)
def test_every_stored_penalty_shape_is_read(raw, expected):
    """Four formats the submission validator can store, plus a leading `+` and padding."""
    assert _parse_penalty_seconds(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "N/A", "n/a", "nonsense", "a:b:c"])
def test_an_unreadable_penalty_counts_as_none(raw):
    """Zero rather than an exception: a review that died on one malformed cell would block
    the whole round's approval, and zero is the value that changes nothing."""
    assert _parse_penalty_seconds(raw) == 0


# ---------------------------------------------------------------------------
# _pen_label
# ---------------------------------------------------------------------------


def _penalty(seconds: int | None, penalty_type: str = "TIME") -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=DRIVER_A,
        session_type=SessionType.FULL_RACE,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


def test_a_time_penalty_is_labelled_with_its_sign():
    assert _pen_label(_penalty(5)) == "+5s"


def test_a_disqualification_is_labelled_as_such():
    """A DSQ has no seconds, so it cannot be rendered as a time."""
    assert _pen_label(_penalty(None, "DSQ")) == "DSQ"


def test_a_negative_penalty_keeps_its_own_sign():
    """A correction giving time back is legitimate on appeal, and "+-5s" would read as a
    typo rather than as a credit."""
    assert _pen_label(_penalty(-5)) == "-5s"


# ---------------------------------------------------------------------------
# The permission gate
# ---------------------------------------------------------------------------


def _interaction(*, member: bool = True):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock(spec=discord.Member) if member else MagicMock()
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _state(bot, db_path: str = ":memory:") -> PenaltyReviewState:
    return PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=1,
        session_types_present=[SessionType.FULL_RACE],
        db_path=db_path,
        bot=bot,
    )


def _bot(config):
    bot = MagicMock()
    bot.config_service = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=config)
    return bot


async def test_a_league_admin_may_drive_the_review(monkeypatch):
    """Issue #116, on the button-driven half. The gate asks `is_league_manager`, and the
    higher tier carries the lower — so an admin passes without also holding the
    interaction role. Asking for that role alone refused all thirteen buttons."""
    import services.penalty_wizard as pw

    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: True)
    interaction = _interaction()

    assert await _is_league_manager(interaction, ":memory:", _bot(MagicMock())) is True


async def test_an_ordinary_member_may_not(monkeypatch):
    import services.penalty_wizard as pw

    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)
    interaction = _interaction()

    assert await _is_league_manager(interaction, ":memory:", _bot(MagicMock())) is False


async def test_an_interaction_from_outside_a_server_is_refused():
    """Not a `Member`, so there are no roles to check at all."""
    interaction = _interaction(member=False)

    assert await _is_league_manager(interaction, ":memory:", _bot(MagicMock())) is False


async def test_an_unconfigured_server_refuses_rather_than_assuming():
    """No configuration means no roles are known, and assuming permission would let
    anybody approve a round on a half-set-up server."""
    interaction = _interaction()

    assert await _is_league_manager(interaction, ":memory:", _bot(None)) is False


async def test_a_refused_actor_is_told_why(monkeypatch):
    import services.penalty_wizard as pw

    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: False)
    interaction = _interaction()

    assert await _require_lm(interaction, _state(_bot(MagicMock()))) is False
    assert "Only league managers" in interaction.response.send_message.await_args.args[0]


async def test_a_permitted_actor_is_not_interrupted(monkeypatch):
    import services.penalty_wizard as pw

    monkeypatch.setattr(pw, "is_league_manager", lambda config, member: True)
    interaction = _interaction()

    assert await _require_lm(interaction, _state(_bot(MagicMock()))) is True
    interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# The prompt
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, attendees=(), test_names=None) -> str:
    """A round with `ACTIVE` race results for each of *attendees*."""
    db_path = os.path.join(str(tmp_path), "penalty_prompt.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (1, ?, 1, '2026-01-01', 'ACTIVE')",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, 1, 'Division 1', 1, 555)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        if attendees:
            await db.execute(
                "INSERT INTO session_results "
                "(id, round_id, division_id, session_type, status) "
                "VALUES (1, ?, ?, 'FULL_RACE', 'ACTIVE')",
                (ROUND_ID, DIVISION_ID),
            )
            for index, uid in enumerate(attendees, start=1):
                await db.execute(
                    "INSERT INTO race_session_results "
                    "(id, session_result_id, driver_user_id, team_role_id, "
                    " finishing_position, outcome) VALUES (?, 1, ?, 0, ?, 'FINISHED')",
                    (index, uid, index),
                )
        for uid, name in (test_names or {}).items():
            await db.execute(
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
                "is_test_driver, test_display_name) VALUES (?, ?, 'ACTIVE', 1, ?)",
                (SERVER_ID, str(uid), name),
            )
        await db.commit()
    return db_path


def _state_for(db_path: str, *, staged=(), pardons=()) -> PenaltyReviewState:
    state = _state(MagicMock(), db_path)
    state.round_number = 3
    state.division_name = "Division 1"
    state.staged = list(staged)
    state.staged_pardons = list(pardons)
    return state


async def test_the_prompt_names_the_round_and_division(tmp_path):
    db_path = await _make_db(tmp_path)

    content = await _render_prompt_content(_state_for(db_path))

    assert "Round 3" in content
    assert "Division 1" in content


async def test_every_driver_with_a_result_is_listed_as_an_attendee(tmp_path):
    """The attendee list is what the manager checks the round against before approving."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A, DRIVER_B))

    content = await _render_prompt_content(_state_for(db_path))

    assert f"<@{DRIVER_A}>" in content
    assert f"<@{DRIVER_B}>" in content
    assert "Preliminary Attendees (2)" in content


async def test_a_round_with_no_results_says_so_rather_than_showing_a_blank(tmp_path):
    """A blank section reads as a rendering failure; a manager needs to know the results
    are genuinely absent, because approving then would record a round nobody raced."""
    db_path = await _make_db(tmp_path)

    content = await _render_prompt_content(_state_for(db_path))

    assert "no session results found" in content


async def test_a_test_driver_s_display_name_is_carried_into_the_mention(tmp_path):
    """Test drivers share one Discord account, so without the name every penalty in a
    test-mode review reads as being against the same person."""
    db_path = await _make_db(
        tmp_path, attendees=(DRIVER_A,), test_names={DRIVER_A: "Test Lewis"}
    )

    content = await _render_prompt_content(_state_for(db_path))

    assert "Test Lewis" in content


async def test_no_staged_penalties_says_so(tmp_path):
    db_path = await _make_db(tmp_path)

    content = await _render_prompt_content(_state_for(db_path))

    assert "Staged Penalties:" in content
    assert "click Add Penalty" in content


async def test_every_staged_penalty_appears_on_the_prompt(tmp_path):
    """The manager approves the round from this message, so a staged penalty missing from
    it would be applied on approval without anybody having read it."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A,))
    staged = [
        StagedPenalty(DRIVER_A, SessionType.FULL_RACE, "TIME", 5),
        StagedPenalty(DRIVER_B, SessionType.FULL_RACE, "DSQ", None),
    ]

    content = await _render_prompt_content(_state_for(db_path, staged=staged))

    assert "Staged Penalties (2)" in content
    assert "+5s" in content
    assert "DSQ" in content


async def test_each_staged_penalty_is_numbered_for_removal(tmp_path):
    """The removal buttons are numbered, so the prompt has to agree with them or a manager
    removes the wrong penalty."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A,))
    staged = [
        StagedPenalty(DRIVER_A, SessionType.FULL_RACE, "TIME", 5),
        StagedPenalty(DRIVER_B, SessionType.FULL_RACE, "TIME", 10),
    ]

    content = await _render_prompt_content(_state_for(db_path, staged=staged))

    assert "Remove #1" in content
    assert "Remove #2" in content


async def test_the_session_a_penalty_belongs_to_is_named(tmp_path):
    """A penalty applies to one session, and a qualifying penalty read as a race penalty
    would move the wrong grid."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A,))
    staged = [StagedPenalty(DRIVER_A, SessionType.FULL_QUALIFYING, "TIME", 5)]

    content = await _render_prompt_content(_state_for(db_path, staged=staged))

    assert "Full Qualifying" in content


async def test_staged_pardons_are_shown_with_their_kind(tmp_path):
    """A pardon waives an attendance penalty, so it changes the standings as surely as a
    time penalty does and must be visible before approval."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A,))
    pardons = [
        StagedPardon(
            driver_user_id=DRIVER_A,
            driver_profile_id=1,
            attendance_id=1,
            pardon_type="NO_SHOW",
            justification="Power cut",
            grantor_id=77,
        )
    ]

    content = await _render_prompt_content(_state_for(db_path, pardons=pardons))

    assert "Staged Attendance Pardons (1)" in content
    assert "NO_SHOW" in content


async def test_a_pardon_s_justification_is_not_shown_on_the_prompt(tmp_path):
    """It is logged to the calculation channel rather than posted where the division can
    read it — a driver's reason for missing a race is theirs."""
    db_path = await _make_db(tmp_path, attendees=(DRIVER_A,))
    pardons = [
        StagedPardon(
            driver_user_id=DRIVER_A,
            driver_profile_id=1,
            attendance_id=1,
            pardon_type="NO_SHOW",
            justification="Hospital appointment",
            grantor_id=77,
        )
    ]

    content = await _render_prompt_content(_state_for(db_path, pardons=pardons))

    assert "Hospital appointment" not in content
    assert "justification logged" in content


async def test_no_pardons_adds_no_pardon_section(tmp_path):
    """Unlike penalties, which always show a "none" line — pardons are the exception, and
    an empty section every round would be noise."""
    db_path = await _make_db(tmp_path)

    content = await _render_prompt_content(_state_for(db_path))

    assert "Attendance Pardons" not in content
