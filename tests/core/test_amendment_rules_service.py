"""What an amendment to a round may do — `amendment_rules_service`.

Core judges its own rules here; the check-in's part is attendance's
(`tests/attendance/test_check_in_amendment_rules.py`) and the forecasts' weather's
(`tests/weather/test_forecast_amendment_rules.py`). A rule that spans core and a module is judged
through `tests/support/amendment_judging.judged`, which gathers both modules' parts as core does.

Pure tests: no database, no Discord. Every one pins ``now`` alongside the dates it seeds, because
the module takes ``now`` for exactly that reason and a test that seeded a future date and let the
code read the wall clock would pass today and fail silently months later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leaguebot.core.models.round import Round, RoundFormat, RoundStatus
from leaguebot.core.services.rounds.amendment_rules_service import judge_amendment
from leaguebot.core.models.round import AttendanceWindows, WeatherWindows
from tests.support.amendment_judging import judged

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

#: The packaged defaults: call 5 days out, reminder 24 hours out, deadline 2 hours out.
DEFAULT_ATTENDANCE = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=2)
#: The packaged forecast horizons.
DEFAULT_WEATHER = WeatherWindows(phase_1_days=5, phase_2_days=2, phase_3_hours=2)


def _round(
    *,
    scheduled_at: datetime,
    status: str = RoundStatus.NOT_RUN.value,
    fmt: RoundFormat = RoundFormat.NORMAL,
    track: str | None = "Bahrain International Circuit",
) -> Round:
    return Round(
        id=1,
        division_id=1,
        round_number=1,
        format=fmt,
        track_name=track,
        scheduled_at=scheduled_at,
        status=status,
    )


# ---------------------------------------------------------------------------
# Whether the round may be amended at all
# ---------------------------------------------------------------------------


def test_a_round_well_ahead_may_be_amended():
    """A round 30 days out given another track, attendance on: nothing is refused."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judged(
        rnd, {"track_name": "Silverstone Circuit"}, now=NOW, attendance=DEFAULT_ATTENDANCE
    )
    assert verdict.allowed
    assert verdict.refusals == []


def test_a_round_whose_results_are_in_may_not_be_amended():
    """The same rule that governs cancellation: drivers have reports and appeals lodged."""
    rnd = _round(
        scheduled_at=NOW + timedelta(days=30),
        status=RoundStatus.AWAITING_REPORT_VERDICTS.value,
    )
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("results have been entered" in r for r in verdict.refusals)


def test_a_cancelled_round_may_not_be_amended():
    rnd = _round(scheduled_at=NOW + timedelta(days=30), status=RoundStatus.CANCELLED.value)
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("cancelled" in r for r in verdict.refusals)


def test_an_empty_change_set_is_refused():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    assert not judge_amendment(rnd, {}, now=NOW).allowed


def test_a_field_that_is_not_amendable_is_refused():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"round_number": 4}, now=NOW)
    assert not verdict.allowed
    assert any("round_number" in r for r in verdict.refusals)


# ---------------------------------------------------------------------------
# A change of track, brought back into question by a change of moment
# ---------------------------------------------------------------------------


def test_moving_a_round_forward_lets_its_track_be_amended_in_the_same_change():
    """The league's remedy, and the reason a change set is judged as one change.

    The round has started and its first forecast is out, so a bare track change is refused twice
    over. Moving it puts both rules back in question, and they read the round as it will stand.
    """
    rnd = _round(scheduled_at=NOW - timedelta(hours=1))
    verdict = judged(
        rnd,
        {"track_name": "Silverstone Circuit", "scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
        drawn={1: True},
    )
    assert verdict.allowed, verdict.refusals


# ---------------------------------------------------------------------------
# Where a round may be moved to
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attendance,weather",
    [
        pytest.param(None, None, id="no_module"),
        (DEFAULT_ATTENDANCE, None),
        (None, DEFAULT_WEATHER),
        (DEFAULT_ATTENDANCE, DEFAULT_WEATHER),
    ],
)
def test_moving_a_round_into_the_past_is_refused_whatever_the_modules(attendance, weather):
    """Decided 2026-09-14: the answer shall not turn on which modules a league runs.

    The round's result submission is armed against its moment whatever the modules, so a
    backwards move costs the same round the same thing in every one of these combinations.
    The case with no module enabled is the rule on its own: the check-in deadline rule refuses
    a backwards move too, and a test that let it answer would pass whether or not this rule
    existed.
    """
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judged(
        rnd,
        {"scheduled_at": NOW - timedelta(hours=1)},
        now=NOW,
        attendance=attendance,
        weather=weather,
    )
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_moment_falling_exactly_now_counts_as_the_past():
    """``<=``, the threshold every other rule here holds to, so the two cannot disagree."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW}, now=NOW)
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_round_already_in_the_past_may_still_be_moved_forward():
    """The escape, and the guard against over-tightening: forward is always still open."""
    rnd = _round(scheduled_at=NOW - timedelta(days=2))
    verdict = judged(
        rnd,
        {"scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )
    assert verdict.allowed, verdict.refusals


def test_the_past_refusal_is_silent_when_the_moment_is_not_amended():
    """A track change on a round already past is refused by the started gate, not by this one.

    The round is not being *moved* anywhere, so a refusal telling the manager not to move it
    into the past would name a thing they did not ask for. It also pins the started gate's own
    refusal, so the "already started" check is not merely a precondition here.
    """
    rnd = _round(scheduled_at=NOW - timedelta(hours=1))
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("already started" in r for r in verdict.refusals)
    assert not any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_round_may_not_be_moved_from_one_past_moment_to_another():
    """Already past is not a licence to stay there — the round still loses its submission."""
    rnd = _round(scheduled_at=NOW - timedelta(days=2))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW - timedelta(hours=1)}, now=NOW)
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_mystery_round_may_not_be_given_a_track_without_a_format():
    """A mystery round names no circuit, so naming one alone would leave it in a forbidden state."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30), fmt=RoundFormat.MYSTERY, track=None)
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("mystery" in r.lower() for r in verdict.refusals)


def test_a_mystery_round_changed_to_another_type_amends_both_at_once():
    """A mystery round given another format and a circuit in one change is allowed."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30), fmt=RoundFormat.MYSTERY, track=None)
    verdict = judge_amendment(
        rnd,
        {"track_name": "Silverstone Circuit", "format": RoundFormat.NORMAL},
        now=NOW,
    )
    assert verdict.allowed, verdict.refusals


# ---------------------------------------------------------------------------
# A change set is one change
# ---------------------------------------------------------------------------


def test_one_refused_field_refuses_the_whole_change_set():
    """Track and format together, with phase 2 out: the format alone is what refuses it, and the
    track goes with it. None of the amendment happens."""
    rnd = _round(scheduled_at=NOW + timedelta(days=1))
    verdict = judged(
        rnd,
        {"track_name": "Silverstone Circuit", "format": RoundFormat.SPRINT},
        now=NOW,
        drawn={2: True},
    )
    assert not verdict.allowed


# ---------------------------------------------------------------------------
# An amendment that changes nothing (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a command that changes nothing because
# nothing was asked of it records that nothing was changed. `/round amend` given the values that
# already stand writes no audit entry, replies that nothing changed and records it. Whether it does
# is judged here, before the command opens a connection, from the values it has already read. The
# division commands' own judgement is `season_service`'s, beside every other division rule, and
# is tested in `test_division_no_op_judgement.py`.

#: A round as `/round amend` holds it before it asks for anything: its track, its format as a
#: `RoundFormat` and its moment as an aware UTC `datetime`, which is what `_row_to_round` makes of
#: the stored row on the active path and what the pending path's round already carries.
_STANDING = {
    "track_name": "Bahrain International Circuit",
    "format": RoundFormat.SPRINT,
    "scheduled_at": datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc),
}


def _asked_moment(text: str) -> datetime:
    """A moment as `/round amend` holds it once typed: through `parse_datetime`, aware UTC."""
    from leaguebot.core.utils.input_validator import parse_datetime

    moment = parse_datetime(text)
    assert moment is not None
    return moment


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"track_name": "Bahrain International Circuit"}, id="the_same_track"),
        pytest.param({"format": RoundFormat.SPRINT}, id="the_same_format"),
        pytest.param(
            {"scheduled_at": _asked_moment("2026-07-01T18:00:00")},
            id="the_same_moment_given_in_utc",
        ),
        pytest.param(
            {"scheduled_at": _asked_moment("2026-07-01T20:00:00+02:00")},
            id="the_same_moment_given_in_another_zone",
        ),
        pytest.param(
            {
                "track_name": "Bahrain International Circuit",
                "format": RoundFormat.SPRINT,
                "scheduled_at": _asked_moment("2026-07-01T18:00:00"),
            },
            id="every_field_as_it_stands",
        ),
    ],
)
def test_a_round_amendment_to_the_values_that_stand_changes_nothing(changes):
    from leaguebot.core.services.rounds.amendment_rules_service import amendment_changes_nothing

    assert amendment_changes_nothing(_STANDING, changes)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"track_name": "Silverstone Circuit"}, id="another_track"),
        pytest.param({"format": RoundFormat.NORMAL}, id="another_format"),
        pytest.param(
            {"scheduled_at": _asked_moment("2026-07-01T19:00:00")},
            id="another_moment",
        ),
        pytest.param(
            {"track_name": "Bahrain International Circuit", "format": RoundFormat.NORMAL},
            id="one_field_as_it_stands_and_one_changed",
        ),
    ],
)
def test_a_round_amendment_with_any_value_changed_changes_something(changes):
    from leaguebot.core.services.rounds.amendment_rules_service import amendment_changes_nothing

    assert not amendment_changes_nothing(_STANDING, changes)


# ---------------------------------------------------------------------------
# The modules' parts of the judgement (#287)
# ---------------------------------------------------------------------------
#
# Core judges its own rules, and each module judges its own windows behind `amendment_judged`,
# answering an `AmendmentPart`: what it refuses, warns and notes, and what it keeps for the save.
# `judged_with_the_modules` asks the hook and `judge_amendment` gathers the parts.

def _judged_hook(*answers):
    """An `amendment_judged` hook whose subscribers answer *answers* in sign-up order, and the
    list each records what it was handed into: the connection, the round, the changes, the
    amended moment, the clock and the windows."""
    from leaguebot.core.utils.hooks import AwaitedHook

    hook = AwaitedHook()
    handed: list[tuple] = []
    for answer in answers:
        async def part(db, rnd, changes, *, moment, now, attendance, weather, answer=answer):
            handed.append((db, rnd, changes, moment, now, attendance, weather))
            return answer
        hook.subscribe(part)
    return hook, handed


async def test_a_module_s_refusal_refuses_the_amendment():
    from leaguebot.core.models.amendment import AmendmentPart
    from leaguebot.core.services.rounds.amendment_rules_service import judged_with_the_modules

    hook, _handed = _judged_hook(AmendmentPart(refusals=("The module refuses it.",)))
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = await judged_with_the_modules(
        hook, rnd, {"track_name": "Silverstone Circuit"}, now=NOW,
        attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER, db=None,
    )
    assert not verdict.allowed
    assert verdict.refusals == ["The module refuses it."]


async def test_with_no_module_signed_up_nothing_module_specific_is_refused():
    """A round brought to an hour away with its track changed: with the modules signed up the
    check-in deadline would refuse it, and with none core finds nothing of its own to refuse,
    warn or keep, whatever windows it is handed."""
    from leaguebot.core.services.rounds.amendment_rules_service import judged_with_the_modules

    hook, _handed = _judged_hook()
    rnd = _round(scheduled_at=NOW + timedelta(days=3))
    verdict = await judged_with_the_modules(
        hook, rnd,
        {"scheduled_at": NOW + timedelta(hours=1), "track_name": "Silverstone Circuit"},
        now=NOW, attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER, db=None,
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.warnings == []
    assert verdict.kept == {}


@pytest.mark.xfail(
    strict=True,
    reason="#287: the mystery round's refusal still says to amend its format to reveal it",
)
async def test_refusals_run_core_s_head_then_each_module_in_sign_up_order_then_core_s_tail():
    """Every one of core's rules refusing at once, beside two modules': the field that cannot be
    amended, the round's status and the move into the past, then attendance's refusal and
    weather's, in the order they signed up, then the round already started and the mystery
    round given a track alone. Today's order, kept."""
    from leaguebot.core.models.amendment import AmendmentPart
    from leaguebot.core.services.rounds.amendment_rules_service import (
        judged_with_the_modules,
        status_refusal,
    )

    hook, _handed = _judged_hook(
        AmendmentPart(refusals=("Attendance refuses it.",)),
        AmendmentPart(refusals=("Weather refuses it.",)),
    )
    rnd = _round(
        scheduled_at=NOW - timedelta(hours=1),
        status=RoundStatus.AWAITING_REPORT_VERDICTS.value,
        fmt=RoundFormat.MYSTERY,
        track=None,
    )
    verdict = await judged_with_the_modules(
        hook, rnd,
        {
            "round_number": 4,
            "scheduled_at": NOW - timedelta(hours=2),
            "track_name": "Silverstone Circuit",
        },
        now=NOW, attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER, db=None,
    )
    assert verdict.refusals == [
        "A round's round_number cannot be amended.",
        status_refusal(RoundStatus.AWAITING_REPORT_VERDICTS.value),
        "A round cannot be moved into the past. Its result submission would open late, at "
        "once, and not at the round's moment. Give it a moment still to come.",
        "Attendance refuses it.",
        "Weather refuses it.",
        "This round has already started, so its track and format can no longer be amended.",
        "A mystery round names no circuit. Change its format as well to give it one.",
    ]


def test_what_the_modules_keep_is_merged_for_the_save():
    from leaguebot.core.models.amendment import AmendmentPart

    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd, {"scheduled_at": NOW + timedelta(days=20)}, now=NOW,
        parts=(AmendmentPart(kept={"reopened": True}), AmendmentPart(kept={"withdrawn": [2, 3]})),
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.kept == {"reopened": True, "withdrawn": [2, 3]}


def test_a_key_kept_by_two_modules_is_a_fault():
    """Neither module's value may silently replace the other's in what the save is handed."""
    from leaguebot.core.models.amendment import AmendmentPart

    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    with pytest.raises(ValueError, match="withdrawn"):
        judge_amendment(
            rnd, {"scheduled_at": NOW + timedelta(days=20)}, now=NOW,
            parts=(AmendmentPart(kept={"withdrawn": [1]}), AmendmentPart(kept={"withdrawn": [2]})),
        )


@pytest.mark.parametrize("db", [pytest.param(None, id="asked"), pytest.param(object(), id="saved")])
async def test_the_modules_are_handed_the_amended_moment_one_utc_now_and_the_save_s_connection(db):
    """The clock and the new moment given in another zone: each module is handed both in UTC, the
    one clock core judges by, and the connection of the save, or none where it is asked outside
    one."""
    from leaguebot.core.models.amendment import AmendmentPart
    from leaguebot.core.services.rounds.amendment_rules_service import judged_with_the_modules

    hook, handed = _judged_hook(AmendmentPart(), AmendmentPart())
    zone = timezone(timedelta(hours=2))
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    changes = {"scheduled_at": datetime(2026, 7, 1, 20, 0, tzinfo=zone)}
    await judged_with_the_modules(
        hook, rnd, changes, now=NOW.astimezone(zone),
        attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER, db=db,
    )
    assert len(handed) == 2
    for each_db, each_rnd, each_changes, moment, now, attendance, weather in handed:
        assert each_db is db
        assert each_rnd is rnd
        assert each_changes == changes
        assert moment == datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
        assert moment.utcoffset() == timedelta(0)
        assert now == NOW
        assert now.utcoffset() == timedelta(0)
        assert (attendance, weather) == (DEFAULT_ATTENDANCE, DEFAULT_WEATHER)
    assert handed[0][4] is handed[1][4]
