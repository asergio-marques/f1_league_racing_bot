"""`AddPenaltyModal` — the form a steward types a penalty into.

Issue #208. This is the one place a penalty enters the bot, and it serves both review passes:
the same modal stages a penalty in the penalty pass and a correction in the appeals pass,
switched by `use_appeals_staging`. That sharing is why it is worth testing as one thing — a
change made for one pass reaches the other.

**A driver must actually be in the session's results.** A steward typing the wrong mention
would otherwise stage a penalty against somebody who did not race it, and it would be applied
on approval against a row that does not exist. The check runs against the *active* results for
that exact session, and race and qualifying are separate queries against separate tables.

**Qualifying takes only a disqualification.** There is no race time to add seconds to, so
`validate_penalty_input` refuses a time penalty outright for a qualifying session — the modal is
shared between the two and the difference lives in the validator.

**The staged adjustment is the rule worth the file.** A negative penalty removes time already
applied, and cannot remove more than was applied. Penalties are held in memory until the pass is
approved, so the driver's stored figure does not yet reflect them — a second reduction has to be
measured against what *remains* after the first, or two reductions that each look legal alone
would together remove more penalty than the driver ever had.
`test_a_second_reduction_is_measured_against_the_remaining_headroom` is what holds it, and the
summing is filtered by driver, session **and** penalty type, so a DSQ staged against the same
driver does not distort the arithmetic.

**The two passes stage into different lists**, and the reply names which. A correction staged
onto the penalty list would be applied a pass too early, against results the appeal was lodged
about.

The modal is constructed inside `async def` tests, as `CLAUDE.md` requires of anything building
a `Modal`: apt's discord.py calls `asyncio.get_running_loop()` in the constructor.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
# The results pipeline's session types, not the weather module's — `penalty_service`
# and `result_submission_service` both import this one, and these are the values
# `session_results.session_type` actually holds.
from models.points_config import SessionType  # noqa: E402
from services.penalty_service import StagedPenalty  # noqa: E402
from services.penalty_wizard import AddPenaltyModal, PenaltyReviewState  # noqa: E402

SERVER_ID = 11208
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
DRIVER = 4001
STRANGER = 4099


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    base_time_ms: int | None = 3_600_000,
    ingame_ms: int = 0,
    qualifying: bool = False,
) -> str:
    db_path = os.path.join(str(tmp_path), "add_penalty.db")
    await run_migrations(db_path)
    session_type = "FEATURE_QUALIFYING" if qualifying else "FEATURE_RACE"
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
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
            "VALUES (1, ?, ?, ?, 'ACTIVE')",
            (ROUND_ID, DIVISION_ID, session_type),
        )
        if qualifying:
            await db.execute(
                "INSERT INTO qualifying_session_results "
                "(id, session_result_id, driver_user_id, team_role_id, finishing_position, "
                " outcome) VALUES (1, 1, ?, 0, 1, 'CLASSIFIED')",
                (DRIVER,),
            )
        else:
            await db.execute(
                "INSERT INTO race_session_results "
                "(id, session_result_id, driver_user_id, team_role_id, finishing_position, "
                " outcome, base_time_ms, ingame_time_penalties_ms) "
                "VALUES (1, 1, ?, 0, 1, 'CLASSIFIED', ?, ?)",
                (DRIVER, base_time_ms, ingame_ms),
            )
        await db.commit()
    return db_path


def _state(db_path: str, *, staged=(), appeals=()):
    state = PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=1,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=MagicMock(),
    )
    state.round_number = 3
    state.division_name = "Division 1"
    state.staged = list(staged)
    state.staged_appeals = list(appeals)
    return state


def _interaction():
    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        if call.args
    )


async def _submit(
    state,
    *,
    driver: str = f"<@{DRIVER}>",
    penalty: str = "+5s",
    session: SessionType = SessionType.FEATURE_RACE,
    appeals: bool = False,
):
    """Build the modal, fill it in, and submit — with the prompt refresh stubbed."""
    modal = AddPenaltyModal(state, session, use_appeals_staging=appeals)
    modal.driver_input._value = driver
    modal.penalty_input._value = penalty
    modal.description_input._value = "Contact at turn one"
    modal.justification_input._value = "Reviewed the footage"
    interaction = _interaction()

    with patch(
        "services.penalty_wizard._refresh_prompt", new=AsyncMock(return_value=None)
    ), patch(
        "services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock(return_value=None)
    ):
        await modal.on_submit(interaction)
    return interaction


def _penalty(seconds: int, *, driver: int = DRIVER, session=SessionType.FEATURE_RACE,
             penalty_type: str = "TIME") -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=session,
        penalty_type=penalty_type,  # type: ignore[arg-type]
        penalty_seconds=seconds,
    )


# ---------------------------------------------------------------------------
# Naming the driver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typed", [f"<@{DRIVER}>", f"<@!{DRIVER}>", str(DRIVER)])
async def test_a_driver_may_be_named_by_mention_or_by_id(tmp_path, typed):
    """A steward pastes a mention from the results, or types an id from the log."""
    state = _state(await _make_db(tmp_path))

    await _submit(state, driver=typed)

    assert len(state.staged) == 1


@pytest.mark.parametrize("typed", ["Lewis", "", "<@abc>", "not-a-driver"])
async def test_an_unparseable_driver_is_refused(tmp_path, typed):
    state = _state(await _make_db(tmp_path))

    interaction = await _submit(state, driver=typed)

    assert "Could not parse driver" in _replied(interaction)
    assert state.staged == []


async def test_a_driver_not_in_the_session_s_results_is_refused(tmp_path):
    """Otherwise the penalty is applied on approval against a row that does not exist."""
    state = _state(await _make_db(tmp_path))

    interaction = await _submit(state, driver=f"<@{STRANGER}>")

    assert "was not found" in _replied(interaction)
    assert state.staged == []


async def test_the_refusal_names_the_session_looked_in(tmp_path):
    """A steward penalising the wrong session needs to know which one was searched."""
    state = _state(await _make_db(tmp_path))

    interaction = await _submit(state, driver=f"<@{STRANGER}>")

    assert "Feature Race" in _replied(interaction)


async def test_a_qualifying_driver_is_found_in_the_qualifying_results(tmp_path):
    """Race and qualifying are separate queries against separate tables, so each needs its
    own exercise — a driver present in one is not present in the other.

    Staged as a DSQ because that is the only penalty qualifying takes."""
    state = _state(await _make_db(tmp_path, qualifying=True))

    await _submit(state, session=SessionType.FEATURE_QUALIFYING, penalty="DSQ")

    assert len(state.staged) == 1


async def test_a_time_penalty_is_refused_for_qualifying(tmp_path):
    """There is no race time to add seconds to, so the validator refuses it outright —
    the modal is shared between the two passes and the difference lives there."""
    state = _state(await _make_db(tmp_path, qualifying=True))

    interaction = await _submit(state, session=SessionType.FEATURE_QUALIFYING, penalty="+5s")

    assert "Only DSQ" in _replied(interaction)
    assert state.staged == []


async def test_a_driver_in_the_race_is_not_found_in_qualifying(tmp_path):
    state = _state(await _make_db(tmp_path, qualifying=False))

    interaction = await _submit(state, session=SessionType.FEATURE_QUALIFYING)

    assert "was not found" in _replied(interaction)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


async def test_a_penalty_is_staged_with_its_description_and_justification(tmp_path):
    """Both reach the verdict the league reads, so losing either at this point loses it
    for good — nothing later asks for them again."""
    state = _state(await _make_db(tmp_path))

    await _submit(state)

    staged = state.staged[0]
    assert staged.driver_user_id == DRIVER
    assert staged.description == "Contact at turn one"
    assert staged.justification == "Reviewed the footage"


async def test_a_disqualification_is_staged(tmp_path):
    state = _state(await _make_db(tmp_path))

    await _submit(state, penalty="DSQ")

    assert state.staged[0].penalty_type == "DSQ"


async def test_the_reply_names_what_was_staged(tmp_path):
    """The prompt is refreshed too, but the steward needs an immediate answer to the form
    they just submitted."""
    state = _state(await _make_db(tmp_path))

    interaction = await _submit(state)

    replied = _replied(interaction)
    assert "Staged Penalty" in replied
    assert "+5s" in replied


async def test_an_appeal_correction_is_staged_on_the_appeals_list(tmp_path):
    """Staged onto the penalty list it would be applied a pass too early, against the very
    results the appeal was lodged about."""
    state = _state(await _make_db(tmp_path))

    interaction = await _submit(state, penalty="+3s", appeals=True)

    assert state.staged == []
    assert len(state.staged_appeals) == 1
    assert "Staged Correction" in _replied(interaction)


async def test_the_prompt_is_refreshed_for_the_pass_being_staged_into(tmp_path):
    """Refreshing the wrong one leaves the steward reading a list that does not include
    what they just added."""
    state = _state(await _make_db(tmp_path))
    modal = AddPenaltyModal(state, SessionType.FEATURE_RACE, use_appeals_staging=True)
    modal.driver_input._value = f"<@{DRIVER}>"
    modal.penalty_input._value = "+3s"
    modal.description_input._value = "d"
    modal.justification_input._value = "j"

    with patch(
        "services.penalty_wizard._refresh_prompt", new=AsyncMock(return_value=None)
    ) as penalty_refresh, patch(
        "services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock(return_value=None)
    ) as appeals_refresh:
        await modal.on_submit(_interaction())

    appeals_refresh.assert_awaited_once()
    penalty_refresh.assert_not_awaited()


# ---------------------------------------------------------------------------
# The staged adjustment
# ---------------------------------------------------------------------------


async def test_a_second_reduction_is_measured_against_the_remaining_headroom(tmp_path):
    """Penalties are held in memory until the pass is approved, so the driver's stored
    time does not yet reflect the first one. Two reductions that each look legal alone
    would together take the time negative."""
    state = _state(
        await _make_db(tmp_path, base_time_ms=5_000, ingame_ms=0),
        staged=[_penalty(-4)],
    )

    interaction = await _submit(state, penalty="-4s")

    assert len(state.staged) == 1  # the first one only
    assert _replied(interaction).startswith("❌")


async def test_a_reduction_within_the_remaining_headroom_is_accepted(tmp_path):
    """The other side of the same bound: ten applied, four staged, six remain."""
    state = _state(
        await _make_db(tmp_path, ingame_ms=10_000),
        staged=[_penalty(-4)],
    )

    await _submit(state, penalty="-4s")

    assert len(state.staged) == 2


async def test_another_driver_s_staged_penalty_does_not_count(tmp_path):
    """The sum is filtered by driver; counting somebody else's would refuse a legal
    penalty for reasons the steward could not see."""
    state = _state(
        await _make_db(tmp_path, ingame_ms=6_000),
        staged=[_penalty(-4, driver=STRANGER)],
    )

    await _submit(state, penalty="-4s")

    assert len(state.staged) == 2


async def test_another_session_s_staged_penalty_does_not_count(tmp_path):
    """Filtered by session too — a qualifying correction has nothing to do with the race
    time being reduced."""
    state = _state(
        await _make_db(tmp_path, ingame_ms=6_000),
        staged=[_penalty(-4, session=SessionType.FEATURE_QUALIFYING)],
    )

    await _submit(state, penalty="-4s")

    assert len(state.staged) == 2


async def test_a_staged_disqualification_does_not_distort_the_arithmetic(tmp_path):
    """Filtered by penalty type. A DSQ carries no seconds, and including it would either
    raise or silently skew the headroom."""
    state = _state(
        await _make_db(tmp_path, ingame_ms=6_000),
        staged=[_penalty(None, penalty_type="DSQ")],
    )

    await _submit(state, penalty="-4s")

    assert len(state.staged) == 2


async def test_the_appeals_pass_counts_its_own_staged_corrections(tmp_path):
    """Each pass sums the list it stages into, so a penalty staged in the earlier pass
    does not constrain a correction in the later one."""
    state = _state(
        await _make_db(tmp_path, ingame_ms=6_000),
        appeals=[_penalty(-4)],
    )

    interaction = await _submit(state, penalty="-4s", appeals=True)

    assert len(state.staged_appeals) == 1
    assert _replied(interaction).startswith("❌")
