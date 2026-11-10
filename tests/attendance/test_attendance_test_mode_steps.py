"""Attendance's steps for `/test-mode advance`: what of each round's check-in it still has to post
or take down, read from its own state alone and handed to core through the `pending_steps_read`
hook."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Noon on 12 October 2026: the moment each step is judged at; round 1 races a week later.
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
AHEAD = "2026-10-19T12:00:00+00:00"

#: Why each test here fails until attendance answers for its own steps.
ATTENDANCE_STEPS = "#283: attendance does not yet hand core its own test-mode steps"


async def _seed(
    db_path: str,
    *,
    attendance_on: bool = True,
    last_notice_hours: int = 24,
    call: bool = False,
    last_notice: bool = False,
    deadline_marked: bool = False,
    announced: bool = False,
    cleared: bool = False,
    in_hand: tuple[str, ...] = (),
) -> None:
    """Round 1 (Bahrain, Division A) of the active season, with attendance on or off and its last
    notice sent *last_notice_hours* before the deadline (0: never sent). Its check-in call stands
    where *call* is set, with its last notice sent, its deadline marked as run or its announcement
    posted as given; its check-in is taken down where *cleared* is set; and each change kind in
    *in_hand* is asked of the change queue for the round and not yet run."""
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 100, 200, 300)"
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, rsvp_last_notice_hours) "
            "VALUES (1, ?, ?)",
            (int(attendance_on), last_notice_hours),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2026-01-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (1, 1, 'Division A', 11, 21)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, "
            " checkin_cleared) VALUES (1, 1, 1, 'NORMAL', 'Bahrain', ?, ?)",
            (AHEAD, int(cleared)),
        )
        if call:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, last_notice_msg_id, "
                " distribution_msg_id, deadline_run_at) "
                "VALUES (1, 1, 'call1', 'ch1', '2026-10-14T12:00:00+00:00', ?, ?, ?)",
                (
                    "last1" if last_notice else None,
                    "dist1" if announced else None,
                    "2026-10-19T10:00:00+00:00" if deadline_marked else None,
                ),
            )
        for kind in in_hand:
            await db.execute(
                "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, what) "
                "VALUES (?, ?, ?, 'BOT', 'QUEUED', 'a check-in step')",
                (kind, f"{kind}:1", json.dumps({"round_id": 1})),
            )
        await db.commit()


def _round():
    """Round 1 as core reads it and hands it to each module."""
    from leaguebot.core.models.round import read_scheduled_at
    from leaguebot.core.services.test_mode_service import ActiveRound

    return ActiveRound(
        round_id=1, round_number=1, division_id=1, division_name="Division A", format="NORMAL",
        track_name="Bahrain", scheduled_at=read_scheduled_at(AHEAD), status="NOT_RUN",
    )


@pytest.mark.xfail(strict=True, reason=ATTENDANCE_STEPS)
@pytest.mark.parametrize("state, pending", [
    pytest.param({}, [(5, "rsvp_notice")], id="no call posted"),
    pytest.param({"in_hand": ("attendance.check_in.call",)}, [],
                 id="the call in hand on the change queue"),
    pytest.param({"call": True}, [(6, "rsvp_last_notice"), (7, "rsvp_deadline"),
                 (9, "rsvp_cleanup")], id="the call posted"),
    pytest.param({"call": True, "last_notice_hours": 0}, [(7, "rsvp_deadline"),
                 (9, "rsvp_cleanup")], id="the call posted, with no last notice set"),
    pytest.param({"call": True, "in_hand": ("attendance.check_in.last_notice",)},
                 [(7, "rsvp_deadline"), (9, "rsvp_cleanup")],
                 id="the last notice in hand on the change queue"),
    pytest.param({"call": True, "last_notice": True}, [(7, "rsvp_deadline"),
                 (9, "rsvp_cleanup")], id="the last notice sent"),
    pytest.param({"call": True, "last_notice": True, "deadline_marked": True},
                 [(9, "rsvp_cleanup")], id="the deadline marked as run"),
    pytest.param({"call": True, "last_notice": True, "announced": True},
                 [(9, "rsvp_cleanup")], id="the deadline's announcement posted"),
    pytest.param({"call": True, "last_notice": True,
                  "in_hand": ("attendance.check_in.deadline",)},
                 [(9, "rsvp_cleanup")], id="the deadline in hand on the change queue"),
    pytest.param({"call": True, "last_notice": True, "deadline_marked": True,
                  "in_hand": ("attendance.check_in.clean_up",)},
                 [], id="the clean-up in hand on the change queue"),
    pytest.param({"cleared": True}, [], id="the check-in taken down"),
    pytest.param({"attendance_on": False}, [], id="attendance off"),
    pytest.param({"attendance_on": False, "call": True}, [],
                 id="attendance off, with a call still up"),
])
async def test_attendance_hands_back_its_own_pending_steps(tmp_path, state, pending) -> None:
    """It is noon on 12 October 2026, and round 1 (Bahrain) races in a week. Attendance hands
    back the check-in call while none is posted and the check-in has not been taken down; once it
    is posted, the last notice until it is sent (never where the league set it to 0), the
    deadline until it has run (marked, or its announcement posted) and the clean-up while the
    call stands; never a step already asked of the change queue. It passes nothing over, and
    while it is off it hands back nothing."""
    from leaguebot.attendance.services.test_mode_steps import pending_steps

    db_path = str(tmp_path / "attendance_steps.db")
    await _seed(db_path, **state)

    steps = await pending_steps(db_path, [_round()], now=NOW)

    assert sorted((s.round_id, s.phase_number, s.kind) for s in steps.pending) == [
        (1, phase, kind) for phase, kind in pending
    ]
    assert steps.passed_over == []
