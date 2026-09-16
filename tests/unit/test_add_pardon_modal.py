"""Staging an attendance pardon during penalty review.

Issue #208. `AddPardonModal.on_submit` is sixty statements and nine refusals, and was
uncovered. A pardon is how a steward forgives an attendance fault a driver had a reason for —
and the refusals are the whole substance of it, because a pardon granted for a fault that did
not happen is a pardon that hides a real one.

**A pardon must match the fault the driver actually has.** There are three faults and each has
one shape:

- `NO_RSVP` pardons a driver who never answered, so it is refused for anyone who did.
- `ABSENT` pardons a driver who did not turn up having not accepted, so it needs a status of
  NO_RSVP, TENTATIVE or DECLINED — an accepted driver who did not turn up is a *no-show*, which
  is the more serious fault and must not be pardoned as the milder one.
- `NO_SHOW` pardons a driver who accepted and then did not turn up, so it requires ACCEPTED.

Each refusal names the status it found, because the fix differs: a steward who pardoned the
wrong fault needs to know which one to grant instead.

**Attendance is read from the results, not from the attendance row.** The `attended` flag is not
written until the review is finalised, so reading it here would treat every driver as absent
during the very step that decides whether they were. Appearing in *any* active session of the
round counts — a driver who qualified and then retired was there.

**A driver who is in the results cannot be pardoned for missing.** Both ABSENT and NO_SHOW are
refused for someone who raced, because the fault is not one they have.

**A finalised round takes no more pardons.** Once post-race penalties are settled the attendance
points are computed and published; a pardon after that would change a published total with no
record of when.

**A pardon is staged, not applied.** It joins the review's list and is written only when the
review is confirmed, which is what lets a steward see the whole set before committing. Staging
the same pardon twice is refused rather than duplicated.

**The justification is logged where the calculation is, and nowhere else.** A pardon is a
steward's decision about one driver, and the reasoning belongs with the audit rather than in a
channel a division reads.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.points_config import SessionType  # noqa: E402
from services.penalty_wizard import (  # noqa: E402
    AddPardonModal,
    PenaltyReviewState,
    StagedPardon,
)

SERVER_ID = 11008
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
PROFILE_ID = 31
DRIVER_USER_ID = 900000001
ATTENDANCE_ID = 41
STEWARD_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "add_pardon",
    rsvp_status: str = "NO_RSVP",
    round_status: str = "AWAITING_REPORT_VERDICTS",
    attendance_row: bool = True,
    in_results: str | None = None,
) -> str:
    """*in_results* is None, "race" or "qualifying" — where the driver appears, if at all."""
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
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
            "VALUES (?, ?, ?, 'ASSIGNED')",
            (PROFILE_ID, SERVER_ID, str(DRIVER_USER_ID)),
        )
        if attendance_row:
            await db.execute(
                "INSERT INTO driver_round_attendance (id, round_id, division_id, "
                "driver_profile_id, rsvp_status) VALUES (?, ?, ?, ?, ?)",
                (ATTENDANCE_ID, ROUND_ID, DIVISION_ID, PROFILE_ID, rsvp_status),
            )
        if in_results is not None:
            cursor = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (ROUND_ID, DIVISION_ID),
            )
            table = (
                "race_session_results"
                if in_results == "race"
                else "qualifying_session_results"
            )
            await db.execute(
                f"INSERT INTO {table} (session_result_id, driver_user_id, team_role_id, "
                f"finishing_position, driver_profile_id) VALUES (?, ?, 3001, 1, ?)",
                (cursor.lastrowid, DRIVER_USER_ID, PROFILE_ID),
            )
        await db.commit()
    return db_path


def _state(db_path: str, *, staged_pardons=None) -> PenaltyReviewState:
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=700,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=bot,
        staged_pardons=list(staged_pardons or []),
        round_number=3,
        division_name="Pro",
    )


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = STEWARD_ID
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _submit(
    state,
    *,
    driver_id: str = str(DRIVER_USER_ID),
    pardon_type: str = "NO_RSVP",
    justification: str = "Power cut on the night",
):
    interaction = _interaction()
    modal = AddPardonModal(state)
    modal.driver_id_input._value = driver_id  # type: ignore[attr-defined]
    modal.pardon_type_input._value = pardon_type  # type: ignore[attr-defined]
    modal.justification_input._value = justification  # type: ignore[attr-defined]
    with patch("services.penalty_wizard._refresh_prompt", new=AsyncMock()) as refresh:
        await modal.on_submit(interaction)
    return interaction, refresh


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _existing(pardon_type: str = "NO_RSVP") -> StagedPardon:
    return StagedPardon(
        driver_user_id=DRIVER_USER_ID,
        driver_profile_id=PROFILE_ID,
        attendance_id=ATTENDANCE_ID,
        pardon_type=pardon_type,
        justification="already staged",
        grantor_id=STEWARD_ID,
    )


# ---------------------------------------------------------------------------
# A pardon that matches the fault
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pardon_type,rsvp_status",
    [
        ("NO_RSVP", "NO_RSVP"),
        ("ABSENT", "NO_RSVP"),
        ("ABSENT", "TENTATIVE"),
        ("ABSENT", "DECLINED"),
        ("NO_SHOW", "ACCEPTED"),
    ],
)
async def test_a_matching_pardon_is_staged(tmp_path, pardon_type, rsvp_status):
    """Each of the three faults, against every status it applies to."""
    db_path = await _make_db(
        tmp_path, name=f"ok_{pardon_type}_{rsvp_status}", rsvp_status=rsvp_status
    )
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type=pardon_type)

    assert [p.pardon_type for p in state.staged_pardons] == [pardon_type]
    assert "Pardon staged" in _replied(interaction)


async def test_the_staged_pardon_carries_who_granted_it_and_why(tmp_path):
    """A pardon is a steward's decision, and the record of who made it is what makes it
    reviewable afterwards."""
    db_path = await _make_db(tmp_path, name="pardon_fields")
    state = _state(db_path)

    await _submit(state, justification="Power cut on the night")

    pardon = state.staged_pardons[0]
    assert pardon.grantor_id == STEWARD_ID
    assert pardon.justification == "Power cut on the night"
    assert pardon.driver_profile_id == PROFILE_ID
    assert pardon.attendance_id == ATTENDANCE_ID


async def test_the_pardon_type_is_read_regardless_of_case(tmp_path):
    """Typed into a modal by a steward mid-review, not selected from a list."""
    db_path = await _make_db(tmp_path, name="pardon_case")
    state = _state(db_path)

    await _submit(state, pardon_type="no_rsvp")

    assert [p.pardon_type for p in state.staged_pardons] == ["NO_RSVP"]


async def test_surrounding_whitespace_is_ignored(tmp_path):
    db_path = await _make_db(tmp_path, name="pardon_spaces")
    state = _state(db_path)

    await _submit(state, driver_id=f"  {DRIVER_USER_ID}  ", pardon_type="  NO_RSVP ")

    assert len(state.staged_pardons) == 1


async def test_staging_does_not_write_to_the_attendance_row(tmp_path):
    """It joins the review's list and is written when the review is confirmed, which is
    what lets a steward see the whole set before committing."""
    db_path = await _make_db(tmp_path, name="pardon_notwritten")
    state = _state(db_path)

    await _submit(state)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_status FROM driver_round_attendance WHERE id = ?",
            (ATTENDANCE_ID,),
        )
        assert (await cursor.fetchone())["rsvp_status"] == "NO_RSVP"


async def test_the_review_prompt_is_refreshed(tmp_path):
    """The prompt is the steward's view of what they have staged; a pardon absent from it
    would be committed unseen."""
    db_path = await _make_db(tmp_path, name="pardon_refresh")

    _, refresh = await _submit(_state(db_path))

    refresh.assert_awaited_once()


async def test_the_justification_is_logged(tmp_path):
    """A pardon is a steward's decision about one driver, and the reasoning belongs with the
    audit rather than in a channel the division reads."""
    db_path = await _make_db(tmp_path, name="pardon_log")
    state = _state(db_path)

    await _submit(state, justification="Power cut on the night")

    logged = str(state.bot.output_router.post_log.await_args.args[1])
    assert "ATTENDANCE_PARDON_STAGED" in logged
    assert "Power cut on the night" in logged
    assert "Pro" in logged
    assert str(STEWARD_ID) in logged


# ---------------------------------------------------------------------------
# A pardon that does not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rsvp_status", ["ACCEPTED", "TENTATIVE", "DECLINED"])
async def test_no_rsvp_is_refused_for_a_driver_who_did_rsvp(tmp_path, rsvp_status):
    """There is no fault to pardon — they answered."""
    db_path = await _make_db(
        tmp_path, name=f"refuse_norsvp_{rsvp_status}", rsvp_status=rsvp_status
    )
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type="NO_RSVP")

    assert "NO_RSVP pardon rejected" in _replied(interaction)
    assert rsvp_status in _replied(interaction)
    assert state.staged_pardons == []


async def test_absent_is_refused_for_a_driver_who_accepted(tmp_path):
    """An accepted driver who did not turn up is a *no-show*, which is the more serious
    fault — pardoning it as the milder one would forgive the wrong thing."""
    db_path = await _make_db(tmp_path, name="refuse_absent", rsvp_status="ACCEPTED")
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type="ABSENT")

    assert "ABSENT pardon rejected" in _replied(interaction)
    assert "NO_RSVP, TENTATIVE, or DECLINED" in _replied(interaction)
    assert state.staged_pardons == []


@pytest.mark.parametrize("rsvp_status", ["NO_RSVP", "TENTATIVE", "DECLINED"])
async def test_no_show_is_refused_for_a_driver_who_did_not_accept(tmp_path, rsvp_status):
    """A no-show is a broken promise; someone who never promised cannot have broken one."""
    db_path = await _make_db(
        tmp_path, name=f"refuse_noshow_{rsvp_status}", rsvp_status=rsvp_status
    )
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type="NO_SHOW")

    assert "NO_SHOW pardon rejected" in _replied(interaction)
    assert "requires ACCEPTED" in _replied(interaction)
    assert state.staged_pardons == []


@pytest.mark.parametrize(
    "pardon_type,rsvp_status",
    [("ABSENT", "DECLINED"), ("NO_SHOW", "ACCEPTED")],
)
async def test_a_driver_in_the_results_cannot_be_pardoned_for_missing(
    tmp_path, pardon_type, rsvp_status
):
    """The fault is not one they have — they were there."""
    db_path = await _make_db(
        tmp_path,
        name=f"refuse_present_{pardon_type}",
        rsvp_status=rsvp_status,
        in_results="race",
    )
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type=pardon_type)

    assert "present in session results" in _replied(interaction)
    assert state.staged_pardons == []


async def test_qualifying_alone_counts_as_being_there(tmp_path):
    """A driver who qualified and then retired was present; reading only the race results
    would pardon them for an absence that did not happen."""
    db_path = await _make_db(
        tmp_path, name="refuse_quali_only", rsvp_status="ACCEPTED", in_results="qualifying"
    )
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type="NO_SHOW")

    assert "present in session results" in _replied(interaction)


async def test_a_superseded_result_does_not_count_as_being_there(tmp_path):
    """A resubmission supersedes rather than deletes, and a driver who appeared only in a
    submission that was corrected away was not in the round as it stands."""
    db_path = await _make_db(
        tmp_path, name="superseded_result", rsvp_status="ACCEPTED", in_results="race"
    )
    async with get_connection(db_path) as db:
        await db.execute("UPDATE session_results SET status = 'SUPERSEDED'")
        await db.commit()
    state = _state(db_path)

    await _submit(state, pardon_type="NO_SHOW")

    assert len(state.staged_pardons) == 1


# ---------------------------------------------------------------------------
# What cannot be pardoned at all
# ---------------------------------------------------------------------------


async def test_a_non_numeric_driver_id_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, name="refuse_badid")
    state = _state(db_path)

    interaction, _ = await _submit(state, driver_id="@racer")

    assert "must be a numeric ID" in _replied(interaction)
    assert state.staged_pardons == []


async def test_an_unknown_pardon_type_is_refused_and_the_options_given(tmp_path):
    """Three words typed by hand, and a steward who guessed needs the list rather than to
    be told their guess was wrong."""
    db_path = await _make_db(tmp_path, name="refuse_badtype")
    state = _state(db_path)

    interaction, _ = await _submit(state, pardon_type="FORGIVEN")

    replied = _replied(interaction)
    assert "Invalid pardon type `FORGIVEN`" in replied
    assert "NO_RSVP, ABSENT, NO_SHOW" in replied


async def test_a_driver_with_no_profile_here_is_refused(tmp_path):
    """Profiles are per server, so an id pasted from elsewhere resolves to nobody."""
    db_path = await _make_db(tmp_path, name="refuse_noprofile")
    state = _state(db_path)

    interaction, _ = await _submit(state, driver_id="900000009")

    assert "No driver profile found" in _replied(interaction)
    assert state.staged_pardons == []


async def test_a_driver_with_no_attendance_row_is_refused(tmp_path):
    """There is nothing to pardon against, and the reply says what to do first — the row is
    created when results are submitted."""
    db_path = await _make_db(tmp_path, name="refuse_norow", attendance_row=False)
    state = _state(db_path)

    interaction, _ = await _submit(state)

    replied = _replied(interaction)
    assert "No attendance row found" in replied
    assert "results have been submitted" in replied


async def test_a_finalised_round_takes_no_more_pardons(tmp_path):
    """Once post-race penalties are settled the attendance points are computed and
    published; a pardon after that would change a published total with no record of when."""
    db_path = await _make_db(
        tmp_path, name="refuse_finalised", round_status="AWAITING_APPEAL_VERDICTS"
    )
    state = _state(db_path)

    interaction, _ = await _submit(state)

    assert "already been finalized" in _replied(interaction)
    assert state.staged_pardons == []


# ---------------------------------------------------------------------------
# Staging the same thing twice
# ---------------------------------------------------------------------------


async def test_the_same_pardon_twice_is_refused(tmp_path):
    """Two identical pardons would be applied twice at confirmation, and a steward
    re-opening the modal after a misread prompt is how it happens."""
    db_path = await _make_db(tmp_path, name="refuse_duplicate")
    state = _state(db_path, staged_pardons=[_existing("NO_RSVP")])

    interaction, _ = await _submit(state, pardon_type="NO_RSVP")

    assert "already staged" in _replied(interaction)
    assert len(state.staged_pardons) == 1


async def test_a_different_pardon_for_the_same_driver_is_allowed(tmp_path):
    """One round can carry more than one fault — a driver may have neither answered nor
    turned up, and the two are pardoned separately."""
    db_path = await _make_db(tmp_path, name="two_pardons")
    state = _state(db_path, staged_pardons=[_existing("NO_RSVP")])

    await _submit(state, pardon_type="ABSENT")

    assert sorted(p.pardon_type for p in state.staged_pardons) == ["ABSENT", "NO_RSVP"]


async def test_the_same_pardon_for_another_driver_is_allowed(tmp_path):
    """The duplicate check is per attendance row, not per pardon type — a whole division
    failing to RSVP is one evening's work for a steward, not a mistake."""
    db_path = await _make_db(tmp_path, name="other_driver")
    other = _existing("NO_RSVP")
    other.attendance_id = 99
    other.driver_user_id = 900000002
    state = _state(db_path, staged_pardons=[other])

    await _submit(state, pardon_type="NO_RSVP")

    assert len(state.staged_pardons) == 2
