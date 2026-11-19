"""Unit tests for `/round amend`, carried out on the change queue (#439)."""
from __future__ import annotations

import contextlib
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

from tests.support.change_queue import run_queue, stopped_job
from tests.support.season_league import (
    AM,
    CALL_MESSAGES,
    CHARLES,
    DIVISIONS,
    MCLAREN,
    PRO,
    amend_round,
    ongoing_league,
    posted_forecast,
    profile_id,
    leads_armed,
    round_id,
)


# ---------------------------------------------------------------------------
# /round amend, carried out on the change queue (#439, slice 4b, amendment A)
# ---------------------------------------------------------------------------
#
# These tests once called `AmendmentService.amend_round` on a bot double. The amendment is now a
# change on the queue, so each runs `/round amend` through the cog on
# `tests/support/season_league.py`'s ongoing season (season 3: Pro tier 1, Am tier 2), presses
# the Confirm it offered as admin 77 and runs the queue, "now" pinned to the league's clock. Am's
# round 3 (id 223), with no check-in call, is the round amended unless a test says otherwise;
# Pro's round 3 (id 213) is used where its call must stand. Weather's phase runners are
# recorded rather than run, patched before the league is built; a check-in call is posted by
# attendance's own service and read from what its check-in channel was sent.

BAHRAIN = "Bahrain International Circuit"
SILVERSTONE = "Silverstone Circuit"
AM_3 = round_id(AM, 3)
PRO_3 = round_id(PRO, 3)


@pytest.fixture
def phases(monkeypatch):
    """Weather's draw of each phase, recording `(phase, round id)` in `ran`, then drawn as weather
    draws it; a phase in `failing` raises its fault instead, as a fault of the bot's own
    (`tests/support/weather_draws.py`)."""
    from tests.support.weather_draws import recorded_draws

    return recorded_draws(monkeypatch)


def _attendance_as_it_is(league, monkeypatch) -> None:
    """Attendance's own service in the league's place, its writers included, drawing no picture
    (the image module's check-in graphic gives none), so that a check-in call is posted and
    recorded as attendance posts it, whichever of the bot's code posts it."""
    from unittest.mock import AsyncMock

    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService

    monkeypatch.setattr(
        league.bot.image_drawing, "attach_check_in_call", AsyncMock(return_value=(None, ()))
    )
    attendance = AttendanceService(league.db_path)
    for name in dir(AttendanceService):
        if not name.startswith("_") and callable(getattr(AttendanceService, name)):
            setattr(league.bot.attendance_service, name, getattr(attendance, name))


class _Reposts:
    """What the `reposts` fixture saw of the league it wrapped: the check-in calls posted."""

    def __init__(self) -> None:
        self.league = None

    @property
    def posted(self) -> list[tuple[int, int]]:
        """`(round id, division id)` of each check-in call sent since the league was built: a
        send to a check-in channel whose message stands recorded as a round's call."""
        if self.league is None:
            return []
        checkins = {DIVISIONS[division][3].checkin for division in DIVISIONS}
        sent = [(cid, mid) for kind, cid, mid in self.league.events
                if kind == "send" and cid in checkins]
        with contextlib.closing(sqlite3.connect(self.league.db_path)) as db, db:
            calls = {(int(channel), int(message)): (rid, division)
                     for rid, division, message, channel in db.execute(
                         "SELECT round_id, division_id, message_id, channel_id "
                         "FROM rsvp_embed_messages")}
        return [calls[each] for each in sent if each in calls]


@pytest.fixture
def reposts(monkeypatch):
    """The check-in call posted as attendance posts it, by whichever of the bot's code posts it
    (#439, slice 6b, C28: today `repost_rsvp_call` whole, then the call's own jobs), and read
    from what Discord was sent, so that no test leans on either.

    It wraps this module's `ongoing_league`: the league each test builds gets attendance's own
    service (`_attendance_as_it_is`), and the fixture keeps it. `posted` is `(round id,
    division id)` of each call sent to a check-in channel and recorded as the round's call."""
    record = _Reposts()
    built = ongoing_league

    async def _wrapped(*args, **kwargs):
        league = await built(*args, **kwargs)
        _attendance_as_it_is(league, monkeypatch)
        record.league = league
        return league

    monkeypatch.setattr(sys.modules[__name__], "ongoing_league", _wrapped)
    return record


def _at(league, delta: timedelta) -> str:
    """The moment *delta* from "now", as `/round amend` takes it."""
    return (league.clock.now + delta).strftime("%Y-%m-%dT%H:%M:%S")


def _utc(moment) -> datetime:
    moment = datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def _place(league, rid: int = AM_3, *, at: timedelta, track: str = BAHRAIN) -> None:
    """Round *rid* falls *at* from "now", at *track*, in the NORMAL format."""
    await league.write(
        "UPDATE rounds SET scheduled_at = ?, track_name = ? WHERE id = ?",
        (league.clock.now + at).astimezone(timezone.utc).isoformat(), track, rid,
    )


async def _amend(league, division: str = "Am", number: int = 3, *, offered: bool = True,
                 **fields: str):
    """Run `/round amend` for *division*'s round *number*, press its Confirm and run the queue;
    gives the press. Where not *offered*, the offer is expected to refuse, and nothing is
    pressed."""
    press = await amend_round(league, division, number, **fields)
    assert (press is not None) is offered, "nothing was offered" if offered else "it was offered"
    assert not league.errors, league.errors
    await run_queue(league.bot)
    return press


async def _round(league, rid: int = AM_3) -> dict:
    return (await league.rows("SELECT * FROM rounds WHERE id = ?", rid))[0]


async def _round_audits(league) -> list[dict]:
    return await league.rows(
        "SELECT change_type, old_value, new_value FROM audit_entries "
        "WHERE change_type LIKE 'round.%' ORDER BY change_type"
    )


def _success_lines(league) -> list[str]:
    return [line for line in league.bot.log_channel.sent if "| /round amend | Success" in line]


async def _stopped_at(league) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


# ---------------------------------------------------------------------------
# amend_round actually amends — the query it opens with must name real columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mystery", "old_value"),
    [
        pytest.param(
            False, '"Bahrain International Circuit"', id="a named track",
        ),
        pytest.param(
            True, "null", id="a mystery round naming none",
        ),
    ],
)
async def test_amend_round_changes_the_field(tmp_path, mystery, old_value):
    """`/round amend` was dead on arrival: its opening SELECT asked `divisions` for a
    `division_id` column, which that table has never had, so every amendment of an active
    season's round raised `no such column: d.division_id` and the league was told the
    amendment failed. Nothing else in the suite executed this path.

    Am's round 3, at Bahrain International Circuit, or (a mystery round naming none) a mystery
    round with no track, is amended to Silverstone Circuit, the mystery round to the NORMAL
    format with it, and the queue run: the round holds the new track, and its record names the
    change from the old, each value as JSON: the old track in quotes, or null where there was
    none, and the new one in quotes.
    """
    league = await ongoing_league(tmp_path)
    await _place(league, at=timedelta(days=90))
    if mystery:
        await league.write(
            "UPDATE rounds SET format = 'MYSTERY', track_name = NULL WHERE id = ?", AM_3
        )
        await _amend(league, track=SILVERSTONE, format="NORMAL")
    else:
        await _amend(league, track=SILVERSTONE)

    assert (await _round(league))["track_name"] == SILVERSTONE
    [entry] = [a for a in await _round_audits(league) if a["change_type"] == "round.track_name"]
    assert entry["old_value"] == old_value
    assert entry["new_value"] == '"Silverstone Circuit"'


# ---------------------------------------------------------------------------
# One line for a confirmed /round amend, saved with the amendment (#482; #439)
# ---------------------------------------------------------------------------
#
# `/round amend` wrote two success lines for one amendment: the service's "/round amend (field)"
# line, naming the fields by their columns, and the confirmation's own. The one line is in the
# cogs' success form, and is saved with the amendment, so it stands whatever fails after the
# save: the core specification's "The record of what changed".


async def test_a_round_amendment_writes_one_line_naming_the_member_the_round_and_each_change(
    tmp_path,
):
    """Admin amends Am's round 3 from Bahrain International Circuit in the NORMAL format to
    Silverstone Circuit in the SPRINT format. One line is written, in the success form, naming
    Admin and `/round amend`, the round, and each field from what to what, named as the
    command's parameter (`track`) rather than by its column."""
    league = await ongoing_league(tmp_path)
    await _place(league, at=timedelta(days=90))

    await _amend(league, track=SILVERSTONE, format="SPRINT")

    [line] = _success_lines(league)
    head, _, body = line.partition("\n")
    assert head == "Admin (`<@77>`) | /round amend | Success"
    assert "round 3" in body.lower()
    changes = body.splitlines()
    assert f"  track: {BAHRAIN} → {SILVERSTONE}" in changes
    assert "  format: NORMAL → SPRINT" in changes
    assert "track_name" not in body


async def test_a_round_amendment_line_leaves_out_a_field_given_at_the_value_it_held(tmp_path):
    """Admin amends Am's round 3, which stands at Bahrain International Circuit in the NORMAL
    format, giving the track it already has and the SPRINT format. The one line names the format
    from what to what, and says nothing of the track, which did not change: a line reading
    'track: Bahrain International Circuit → Bahrain International Circuit' would record a change
    that was never made."""
    league = await ongoing_league(tmp_path)
    await _place(league, at=timedelta(days=90))

    await _amend(league, track=BAHRAIN, format="SPRINT")

    [line] = _success_lines(league)
    head, _, body = line.partition("\n")
    assert head == "Admin (`<@77>`) | /round amend | Success"
    assert "  format: NORMAL → SPRINT" in body.splitlines()
    assert "track:" not in body


@pytest.mark.parametrize("fault_in", [
    pytest.param("arming the round's jobs"),
    "re-running a phase",
])
async def test_a_round_amendment_that_fails_after_the_save_still_leaves_its_line(
    tmp_path, request, fault_in
):
    """Admin moves Am's round 3, 90 days out with no forecast drawn, from Bahrain International
    Circuit to Silverstone Circuit and brings it forward to an hour from now, so that every phase
    falls due at once, with weather on. The amendment is saved, then fails: the scheduler cannot arm the round's jobs again, or the first overdue forecast cannot
    be drawn. The queue stops at that job; the round stands amended, and its one success line,
    saved with the amendment, has been written, so the record holds what changed."""
    # Only the draw's case records the draws, before the league is built: the arming case stops
    # before any draw is made.
    phases = request.getfixturevalue("phases") if fault_in == "re-running a phase" else None
    league = await ongoing_league(tmp_path, weather=True)
    await _place(league, at=timedelta(days=90))
    fault = RuntimeError("the fault after the save")
    if phases is None:
        league.arming_fails = fault
    else:
        phases.failing[1] = fault

    await _amend(league, track=SILVERSTONE, scheduled_at=_at(league, timedelta(hours=1)))

    assert await _stopped_at(league) == (
        "arm" if fault_in == "arming the round's jobs" else "draw_forecast"
    )
    assert (await _round(league))["track_name"] == SILVERSTONE
    [line] = _success_lines(league)
    assert line.startswith("Admin (`<@77>`) | /round amend | Success\n")
    assert f"  track: {BAHRAIN} → {SILVERSTONE}" in line.splitlines()


# ---------------------------------------------------------------------------
# A compound amendment is one amendment — issue #115
# ---------------------------------------------------------------------------


async def test_amending_two_fields_amends_once(tmp_path, phases):
    """Amending a round's track and its date together is one amendment, not two.

    `/round amend` called the service once per field the manager gave, so changing both ran the
    whole amendment twice: the league was told twice that its forecasts had been thrown away,
    and the round was cancelled, re-armed and had its overdue phases re-run twice over. Two
    audit entries are right — one per field — and everything else happens exactly once.

    Weather on; Am's round 3, 90 days out, has had its phase 1 forecast posted (message 8101),
    so the invalidation notice is reached at all. Its track and its date (a week later) are
    amended together.
    """
    league = await ongoing_league(tmp_path, weather=True)
    await _place(league, at=timedelta(days=90))
    await posted_forecast(league, AM_3, 1, 8101)

    await _amend(league, track=SILVERSTONE, scheduled_at=_at(league, timedelta(days=97)))

    rnd = await _round(league)
    # Both fields landed, in one amendment.
    assert rnd["track_name"] == SILVERSTONE
    assert _utc(rnd["scheduled_at"]) == _utc(_at(league, timedelta(days=97)))

    # One audit entry per field — the one thing that is right to do twice.
    assert [a["change_type"] for a in await _round_audits(league)] == [
        "round.scheduled_at", "round.track_name",
    ]

    # Everything else exactly once; both horizons stay in the future, so no overdue phase re-runs.
    assert len(league.texts(DIVISIONS[AM][3].forecast)) == 1
    assert len(_success_lines(league)) == 1
    assert league.unarmed == [AM_3]
    assert league.armed == [("weather", [AM_3])]
    assert phases.ran == []


# ---------------------------------------------------------------------------
# A forecast that would still have run is kept
# ---------------------------------------------------------------------------


async def test_a_phase_that_would_still_have_run_is_kept(tmp_path):
    """Each phase is judged on its own, by whether it would have run under the new moment.

    Every amendment used to invalidate all three phases, clear every slot and wipe all three
    done flags, whatever had actually been drawn. So a round nudged an hour threw away a
    forecast that was still perfectly good, and — because the flags went with it — the next
    amendment had no record that any forecast had ever been posted, which is what the track and
    format rules read.

    Am's round 3 one day out, delayed to four, weather off. Phase 1 falls five days before it
    and is behind us either way, so it stands. Phases 2 and 3 have not come round under the new
    moment, so they are withdrawn and armed again.
    """
    league = await ongoing_league(tmp_path)
    await _place(league, at=timedelta(days=1))
    # Phases 1 and 2 have run (their horizons, 5 and 2 days out, are behind us); 3 has not.
    await league.write("UPDATE rounds SET phase1_done = 1 WHERE id = ?", AM_3)
    await league.write("UPDATE rounds SET phase2_done = 1 WHERE id = ?", AM_3)
    for phase_number in (1, 2):
        await league.write(
            "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
            "VALUES (?, ?, '{}', 'ACTIVE', ?)",
            AM_3, phase_number, league.clock.now.isoformat(),
        )

    await _amend(league, scheduled_at=_at(league, timedelta(days=4)))

    flags = await _round(league)
    results = {
        r["phase_number"]: r["status"]
        for r in await league.rows(
            "SELECT phase_number, status FROM phase_results WHERE round_id = ? "
            "ORDER BY phase_number", AM_3,
        )
    }
    # Phase 1 would still have run, so its forecast and its flag both stand.
    assert flags["phase1_done"] == 1
    assert results[1] == "ACTIVE"

    # Phase 2 would not, so it is withdrawn and will be drawn again.
    assert flags["phase2_done"] == 0
    assert results[2] == "INVALIDATED"

    # Phase 3 never ran and is still ahead either way.
    assert flags["phase3_done"] == 0


# ---------------------------------------------------------------------------
# An amended round is re-armed at the league's own horizons — issue #110
# ---------------------------------------------------------------------------


async def test_amending_a_round_rearms_it_at_the_configured_horizons(tmp_path):
    """A league that configured its own forecast horizons keeps them across an amendment.

    The arming once fell back to the packaged 5 / 2 / 2 when it was not told otherwise, and the
    amendment never told it — so a league running 7 / 3 / 4 had the defaults quietly restored
    every time it moved a round, and the phases fired at moments nobody had chosen. It also put
    the amendment at odds with itself once the rules began reading the configured horizons: a
    phase would be judged at seven days and armed at five. Weather now answers its own leads,
    and core keeps no horizons of its own.

    Weather on, the league's horizons 7 / 3 / 4; Am's round 3 has its track amended. The round is
    armed once, its three phases 7 days, 3 days and 4 hours before it.
    """
    league = await ongoing_league(tmp_path, weather=True, horizons=(7, 3, 4))
    await _place(league, at=timedelta(days=90))

    await _amend(league, track=SILVERSTONE)

    [armed] = leads_armed(league.bot.round_jobs)
    phases = {lead.kind: lead.offset for lead in armed["leads"] if lead.kind.startswith("weather_p")}
    assert phases == {
        "weather_p1": -timedelta(days=7),
        "weather_p2": -timedelta(days=3),
        "weather_p3": -timedelta(hours=4),
    }


# ---------------------------------------------------------------------------
# An amended round keeps its check-in — issue #120
# ---------------------------------------------------------------------------


async def test_amending_a_round_rearms_its_check_in(tmp_path):
    """The reported defect: amending a round destroyed its check-in for good.

    Cancelling the round takes all eight of its jobs, the check-in call, its reminder and its
    deadline included, and only the weather ones were ever put back. Nothing else armed them —
    the check-in was armed from the confirmation of placements and nowhere else, and that cannot
    be run again on an active season. So the round asked nobody whether they were racing,
    opened no attendance records, charged nobody, and read afterwards as perfect attendance for
    the entire division, with nothing anywhere reporting it.

    Attendance on, at the league's timings (call 5 days out, last notice 24 hours, deadline 2
    hours); Am's round 3 (season 3, tier 2) is moved from 90 to 100 days out. The round is armed
    once, as amended, under season 3 and tier 2, its leads holding the check-in's call 5 days,
    last notice 24 hours and deadline 2 hours before it and its clean-up, against the queue's
    clock, never the wall clock.
    """
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, at=timedelta(days=90))

    await _amend(league, scheduled_at=_at(league, timedelta(days=100)))

    [armed] = leads_armed(league.bot.round_jobs)
    # Armed against the league's own timings, and named for the right season and tier.
    check_in = {lead.kind: lead.offset for lead in armed["leads"] if lead.kind.startswith("rsvp_")}
    assert {kind: offset for kind, offset in check_in.items() if kind != "rsvp_cleanup"} == {
        "rsvp_notice": -timedelta(days=5),
        "rsvp_last_notice": -timedelta(hours=24),
        "rsvp_deadline": -timedelta(hours=2),
    }
    assert "rsvp_cleanup" in check_in
    assert (armed["season_number"], armed["division_tier"]) == (3, 2)
    # And against the round as amended, not as it stood.
    assert _utc(armed["rnd"].scheduled_at) == _utc(_at(league, timedelta(days=100)))
    # Judged against the queue's clock.
    assert armed["now"] == league.clock.now


async def test_amending_a_round_arms_no_check_in_while_attendance_is_disabled(tmp_path):
    """A disabled module produces nothing, a scheduled job included: attendance off and weather
    on, Am's round 3 moved from 90 to 100 days out is armed once, its leads holding weather's
    forecasts and no check-in."""
    league = await ongoing_league(tmp_path, attendance=False, weather=True)
    await _place(league, at=timedelta(days=90))

    await _amend(league, scheduled_at=_at(league, timedelta(days=100)))

    [armed] = leads_armed(league.bot.round_jobs)
    assert any(lead.kind.startswith("weather_p") for lead in armed["leads"])
    assert not [lead.kind for lead in armed["leads"] if lead.kind.startswith("rsvp_")]


# ---------------------------------------------------------------------------
# An amended round keeps its result submission — issue #133
# ---------------------------------------------------------------------------


async def test_amending_a_round_rearms_its_results_job_with_weather_off(tmp_path):
    """The result submission once came only with the weather jobs.

    So with weather switched off it was never armed, and the amendment's cancel took the
    round's results job with nothing putting it back. That job is the round's one clock-driven
    status transition — without it the round never leaves NOT_RUN, its division never finishes,
    and its season can never be completed. Results answers with its submission whatever the
    modules.

    Weather and attendance off; Am's round 3 (season 3, tier 2) moved from 90 to 100 days out:
    the round is armed once, with results' submission alone, under season 3 and tier 2.
    """
    league = await ongoing_league(tmp_path)
    await _place(league, at=timedelta(days=90))

    await _amend(league, scheduled_at=_at(league, timedelta(days=100)))

    [armed] = leads_armed(league.bot.round_jobs)
    assert armed["rnd"].id == AM_3
    assert [lead.kind for lead in armed["leads"]] == ["results"]
    assert (armed["season_number"], armed["division_tier"]) == (3, 2)


async def test_the_results_job_is_not_armed_twice_when_weather_is_on(tmp_path):
    """With weather on, the round's forecasts and its result submission are armed together, and
    the submission once only: arming it again would duplicate. Am's round 3 moved from 90 to 100
    days out is armed once, its leads holding one result submission."""
    league = await ongoing_league(tmp_path, weather=True)
    await _place(league, at=timedelta(days=90))

    await _amend(league, scheduled_at=_at(league, timedelta(days=100)))

    [armed] = leads_armed(league.bot.round_jobs)
    assert [lead.kind for lead in armed["leads"]].count("results") == 1
    assert league.armed == [("weather", [AM_3])]


# ---------------------------------------------------------------------------
# What becomes of a check-in call that has already gone out
# ---------------------------------------------------------------------------


async def _call_rows(league) -> list[dict]:
    return await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", PRO_3)


async def test_a_standing_call_is_taken_down_when_the_round_moves_out_of_its_window(
    tmp_path, reposts
):
    """Moved far enough that the call would not have gone out, so the one standing is withdrawn.

    The armed job posts a fresh one at the new time. Leaving the old one up would have the
    division reading a call for a circuit, a date or a set of sessions the round no longer has.

    Attendance on; Pro's round 3, two days out, has its call standing (messages 7001 to 7003),
    and is moved 40 days out: the call's messages and its record go, and no call is posted again.
    """
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, PRO_3, at=timedelta(days=2))

    await _amend(league, "Pro", scheduled_at=_at(league, timedelta(days=40)))

    assert await _call_rows(league) == []
    checkin = league.channel(DIVISIONS[PRO][3].checkin)
    assert not any(mid in checkin.messages for mid in CALL_MESSAGES)
    assert reposts.posted == []


async def test_a_standing_call_is_posted_again_when_its_window_has_passed(tmp_path, reposts):
    """The call was due and is still open, so it goes out again carrying what changed.

    Attendance on; Pro's round 3, two days out (the call, 5 days before, is behind us; the
    deadline, 2 hours before, is not), has its call standing and its track amended: the call is
    posted again, once."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, PRO_3, at=timedelta(days=2))

    await _amend(league, "Pro", track=SILVERSTONE)

    assert reposts.posted == [(PRO_3, PRO)]


async def test_a_closed_check_in_is_left_alone(tmp_path, reposts):
    """Past its deadline the check-in is settled and the reserves are distributed against it.

    Reopening it would unsettle a grid already told who is racing, so nothing is posted and
    nothing is taken down — the amendment touches the forecasts and the schedule alone.

    Attendance on; Pro's round 3, one hour out (its deadline two hours before it gone by), has
    its call standing and its track amended. The rules refuse an amendment whose check-in
    deadline has passed, at the offer and again when it runs, so nothing is asked of the queue:
    the call stands as it was, and none is posted.
    """
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, PRO_3, at=timedelta(hours=1))

    await _amend(league, "Pro", offered=False, track=SILVERSTONE)

    assert reposts.posted == []
    assert len(await _call_rows(league)) == 1
    checkin = league.channel(DIVISIONS[PRO][3].checkin)
    assert all(mid in checkin.messages for mid in CALL_MESSAGES)


_CHECK_INS = [
    (timedelta(days=2), "moved-out"),
    (timedelta(days=2), "track"),
    (timedelta(hours=1), "track"),
]


def _change(league, changes: str, days_out: timedelta) -> dict:
    """The amendment *changes* names, and whether it is offered: not where the round's check-in
    deadline has passed (*days_out* inside two hours), which the rules refuse."""
    offered = days_out > timedelta(hours=2)
    if changes == "moved-out":
        return {"offered": offered, "scheduled_at": _at(league, timedelta(days=40))}
    return {"offered": offered, "track": SILVERSTONE}


@pytest.mark.parametrize(
    "days_out,changes,cleared",
    [(*_CHECK_INS[0], False), (*_CHECK_INS[1], False), (*_CHECK_INS[2], True)],
    ids=["call-withdrawn", "call-reposted", "check-in-closed"],
)
async def test_reopening_a_check_in_clears_the_mark_of_one_taken_down(
    tmp_path, reposts, days_out, changes, cleared
):
    """A round's check-in marked taken down, then reopened by an amendment, is no longer taken
    down (#425): test mode reads the mark, and would otherwise never offer the new call. A
    check-in the amendment leaves closed keeps it.

    Attendance on; Am's round 3, two days out or one hour out, its check-in marked taken down,
    is moved 40 days out or has its track amended. One hour out its check-in deadline has
    passed, and the rules refuse the amendment at the offer, so the mark stands."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, at=days_out)
    await league.write("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", AM_3)

    await _amend(league, **_change(league, changes, days_out))

    assert bool((await _round(league))["checkin_cleared"]) is cleared


@pytest.mark.parametrize(
    "days_out,changes,forgotten",
    [(*_CHECK_INS[0], True), (*_CHECK_INS[1], True), (*_CHECK_INS[2], False)],
    ids=["call-withdrawn", "call-reposted", "check-in-closed"],
)
async def test_reopening_a_check_in_forgets_the_distribution_of_the_call_it_replaces(
    tmp_path, reposts, days_out, changes, forgotten
):
    """A check-in reopened by an amendment carries its answers over, not its distribution
    (#429). The reserves were placed against the call being withdrawn, and the call that
    replaces it has its own deadline to place them; a reserve keeping the old team was charged
    as a no-show after the new deadline put them on standby. A check-in the amendment leaves
    closed keeps its distribution: it is the one the division was told about.

    Attendance on; Charles accepted Am's round 3 and was placed at McLaren. The round, two days
    out or one hour out, is moved 40 days out or has its track amended. One hour out its check-in
    deadline has passed, and the rules refuse the amendment at the offer, so the placement
    stands."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _place(league, at=days_out)
    await league.write(
        "INSERT INTO driver_round_attendance "
        "(round_id, division_id, driver_profile_id, rsvp_status, assigned_team_id) "
        "VALUES (?, ?, ?, 'ACCEPTED', ?)",
        AM_3, AM, profile_id(CHARLES), MCLAREN,
    )

    await _amend(league, **_change(league, changes, days_out))

    [row] = await league.rows(
        "SELECT assigned_team_id, is_standby FROM driver_round_attendance WHERE round_id = ?",
        AM_3,
    )
    assert (row["assigned_team_id"], row["is_standby"]) == (
        (None, 0) if forgotten else (MCLAREN, 0)
    )
