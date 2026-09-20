"""Re-announcing a division's verdicts from a round forward, in order (#345).

Amending round 1 of five rescores every later round's standings, and the replay reposts every
round's results and standings so the channels still read 1, 2, 3, 4, 5. The verdicts channel is
the fourth, and it has the same problem: a round's verdicts are a contiguous run of messages, so
re-announcing round 1's would put them below round 5's unless the later rounds move too.

**All of a round's verdicts, not only the ones that changed.** Announcing a subset would
interleave new decisions with old ones, leaving a run that matches neither the classification
nor the order the decisions were taken in.

**The superseded ids are read before anything is re-announced.** Announcing overwrites
`announcement_message_id` on the very rows the old messages are identified by, so a later read
returns the new announcements — and deleting by them would remove what had just been put up.

**A verdict announced before ids were recorded cannot be taken down.** Nothing identifies its
message. It is named as a fault and left standing, which is honest; guessing by adjacency would
delete whatever happened to sit there.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.verdict_announcement_service import (  # noqa: E402
    republish_verdicts_from_round,
)

SEASON_ID = 71
DIVISION_ID = 81
VERDICTS_CHANNEL = 6100


async def _seed(tmp_path, name: str, *, rounds=(1, 2, 3)) -> tuple[str, dict]:
    """A division of *rounds* rounds, each with one race and one driver."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    ids: dict = {}
    async with get_connection(db_path) as db:
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
        for number in rounds:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "status) VALUES (?, ?, ?, ?, 'NORMAL', 'FINAL')",
                (number, DIVISION_ID, number, f"2026-0{number}-01T18:00:00+00:00"),
            )
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (number, DIVISION_ID),
            )
            cursor = await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_role_id, finishing_position) VALUES (?, ?, 3001, 1)",
                (session.lastrowid, 100 + number),
            )
            ids[number] = cursor.lastrowid
        await db.commit()
    return db_path, ids


async def _verdict(db_path, result_id, *, anchor=None, chunks=None, channel=VERDICTS_CHANNEL,
                   table="penalty_records"):
    async with get_connection(db_path) as db:
        if table == "penalty_records":
            cursor = await db.execute(
                "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
                "description, justification, applied_by, applied_at, "
                "announcement_message_id, announcement_message_ids, announcement_channel_id) "
                "VALUES (?, 'TIME', 5, 'Contact', 'At fault', '77', "
                "'2026-02-02T00:00:00+00:00', ?, ?, ?)",
                (result_id, anchor, chunks, str(channel) if channel else None),
            )
        else:
            cursor = await db.execute(
                "INSERT INTO appeal_records (race_result_id, status, penalty_type, "
                "time_seconds, description, justification, submitted_by, submitted_at, "
                "announcement_message_id, announcement_message_ids, announcement_channel_id) "
                "VALUES (?, 'UPHELD', 'TIME', 3, 'Appeal', 'Upheld', '78', "
                "'2026-02-03T00:00:00+00:00', ?, ?, ?)",
                (result_id, anchor, chunks, str(channel) if channel else None),
            )
        await db.commit()
        return cursor.lastrowid


def _bot(channel=None):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel if channel is not None else MagicMock())
    return bot


async def _republish(db_path, bot, from_round_id, *, penalties=None, appeals=None):
    """Run the republish, recording announcements and deletions in order."""
    events: list[tuple[str, object]] = []

    async def _post_pen(_bot, state, records, **_kw):
        events.append(("penalties", (state.round_id, len(records))))
        return list(penalties or [])

    async def _post_app(_bot, state, records, **_kw):
        events.append(("appeals", (state.round_id, len(records))))
        return list(appeals or [])

    async def _delete(_channel, anchor, _ids, **_kw):
        events.append(("delete", anchor))

    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(side_effect=_post_app),
    ), patch(
        "services.verdict_announcement_service.banner_for_round", MagicMock(return_value=None)
    ), patch(
        "services.results_post_service._delete_posting", new=AsyncMock(side_effect=_delete)
    ):
        faults = await republish_verdicts_from_round(
            bot, db_path, DIVISION_ID, from_round_id,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
        )
    return faults, events


async def test_every_round_from_the_named_one_is_re_announced(tmp_path):
    """Inclusive of the round amended, and forward from it."""
    db_path, ids = await _seed(tmp_path, "rep_forward")
    for number in (1, 2, 3):
        await _verdict(db_path, ids[number], anchor=str(8000 + number),
                       chunks=f"[{8000 + number}]")

    _, events = await _republish(db_path, _bot(), from_round_id=2)

    announced = [payload[0] for kind, payload in events if kind == "penalties"]
    assert announced == [2, 3]


async def test_earlier_rounds_are_left_alone(tmp_path):
    """They are already above the amended round and in the right order."""
    db_path, ids = await _seed(tmp_path, "rep_earlier")
    await _verdict(db_path, ids[1], anchor="8001", chunks="[8001]")
    await _verdict(db_path, ids[3], anchor="8003", chunks="[8003]")

    _, events = await _republish(db_path, _bot(), from_round_id=3)

    assert [payload[0] for kind, payload in events if kind == "penalties"] == [3]
    assert [anchor for kind, anchor in events if kind == "delete"] == [8003]


async def test_nothing_is_taken_down_until_everything_is_announced(tmp_path):
    """Produce before destroy, on the verdicts channel as on the others."""
    db_path, ids = await _seed(tmp_path, "rep_ptd")
    for number in (1, 2):
        await _verdict(db_path, ids[number], anchor=str(8000 + number),
                       chunks=f"[{8000 + number}]")

    _, events = await _republish(db_path, _bot(), from_round_id=1)

    kinds = [kind for kind, _ in events]
    assert kinds.index("delete") > max(
        index for index, kind in enumerate(kinds) if kind == "penalties"
    )


async def test_reports_are_announced_before_appeals(tmp_path):
    """The order a round was decided in, and the order the replay states (#345)."""
    db_path, ids = await _seed(tmp_path, "rep_order", rounds=(1,))
    await _verdict(db_path, ids[1], anchor="8001", chunks="[8001]")
    await _verdict(db_path, ids[1], anchor="8002", chunks="[8002]",
                   table="appeal_records")

    _, events = await _republish(db_path, _bot(), from_round_id=1)

    kinds = [kind for kind, _ in events if kind in ("penalties", "appeals")]
    assert kinds == ["penalties", "appeals"]


async def test_the_superseded_message_is_the_one_deleted(tmp_path):
    """Read before re-announcing, or the delete removes the replacement just posted.

    Announcing overwrites `announcement_message_id`, so a read taken afterwards returns the new
    message. This is the test that catches that mistake.
    """
    db_path, ids = await _seed(tmp_path, "rep_superseded", rounds=(1,))
    await _verdict(db_path, ids[1], anchor="8001", chunks="[8001]")

    async def _post_pen(_bot, state, records, **_kw):
        # Stand in for the real poster, which records the new announcement as it goes.
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE penalty_records SET announcement_message_id = '9999', "
                "announcement_message_ids = '[9999]'"
            )
            await db.commit()
        return []

    deleted: list[int] = []

    async def _delete(_channel, anchor, _ids, **_kw):
        deleted.append(anchor)

    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.verdict_announcement_service.banner_for_round", MagicMock(return_value=None)
    ), patch(
        "services.results_post_service._delete_posting", new=AsyncMock(side_effect=_delete)
    ):
        await republish_verdicts_from_round(
            _bot(), db_path, DIVISION_ID, 1,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
        )

    assert deleted == [8001]


async def test_a_verdict_with_no_recorded_message_is_named(tmp_path):
    """It cannot be taken down, so the replay says so rather than doubling it silently."""
    db_path, ids = await _seed(tmp_path, "rep_legacy", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=None, chunks=None)

    faults, events = await _republish(db_path, _bot(), from_round_id=1)

    assert len(faults) == 1
    assert "by hand" in faults[0]
    assert [kind for kind, _ in events if kind == "delete"] == []


async def test_a_round_with_no_verdicts_is_skipped(tmp_path):
    """Nothing to announce and nothing to remove; the round is simply passed over."""
    db_path, ids = await _seed(tmp_path, "rep_empty", rounds=(1, 2))
    await _verdict(db_path, ids[2], anchor="8002", chunks="[8002]")

    _, events = await _republish(db_path, _bot(), from_round_id=1)

    assert [payload[0] for kind, payload in events if kind == "penalties"] == [2]


async def test_a_cancelled_round_is_not_republished(tmp_path):
    """It has no classification for a verdict to describe."""
    db_path, ids = await _seed(tmp_path, "rep_cancelled", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor="8001", chunks="[8001]")
    await _verdict(db_path, ids[2], anchor="8002", chunks="[8002]")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'CANCELLED' WHERE id = 2")
        await db.commit()

    _, events = await _republish(db_path, _bot(), from_round_id=1)

    assert [payload[0] for kind, payload in events if kind == "penalties"] == [1]


async def test_an_unreachable_channel_is_reported_not_raised(tmp_path):
    """The announcements went out; only the removal of the old ones did not."""
    db_path, ids = await _seed(tmp_path, "rep_no_channel", rounds=(1,))
    await _verdict(db_path, ids[1], anchor="8001", chunks="[8001]")
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)

    faults, events = await _republish(db_path, bot, from_round_id=1)

    assert len(faults) == 1
    assert "no longer reachable" in faults[0]
    assert [kind for kind, _ in events if kind == "penalties"]
