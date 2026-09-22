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
from tests.support.teams import seed_team_instances  # noqa: E402

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
        await seed_team_instances(db, DIVISION_ID, 3001)
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


async def test_a_verdict_with_no_recorded_message_is_not_reported_here(tmp_path):
    """A record with no id is usually one the report stage has just rewritten (#345).

    Approving the amendment's report stage deletes the round's verdict records and writes the
    approved set back, so the new rows carry no announcement id. Reporting each of those as
    "announced before the bot began recording its message" was false — the bot had recorded it,
    and had just discarded the record — and it named a fault the amendment itself caused.

    The predecessor's id is noted before that clearing happens and taken down separately, so
    this path stays silent rather than reporting the same thing twice.
    """
    db_path, ids = await _seed(tmp_path, "rep_legacy", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=None, chunks=None)

    faults, events = await _republish(db_path, _bot(), from_round_id=1)

    assert faults == []
    assert [kind for kind, _ in events if kind == "delete"] == []
    # The replacement still goes up; only the taking-down is somebody else's job.
    assert [kind for kind, _ in events if kind == "penalties"]
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


async def test_an_upheld_appeal_is_announced_once_as_an_appeal(tmp_path):
    """**Upholding an appeal writes a `penalty_records` row beside its appeal record** (#345).

    Announcing every penalty row gave the driver the same decision twice — once as a penalty
    verdict the first pass never announced, and once as the appeal it was. The row the appeal
    wrote carries the appeal's own text, which is how it is told apart from a report.
    """
    db_path, ids = await _seed(tmp_path, "appeal_once", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=None, table="appeal_records")
    async with get_connection(db_path) as db:
        # The row `apply_penalties` writes for that appeal: same driver, session and sanction,
        # and the appeal's description and justification.
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 3, 'Appeal', 'Upheld', '78', '2026-02-03T00:00:00+00:00')",
            (ids[1],),
        )
        await db.commit()

    _, events = await _republish(db_path, _bot(), 1)

    assert ("appeals", (1, 1)) in events
    assert not [e for e in events if e[0] == "penalties"]


async def test_a_report_the_same_size_as_an_appeal_is_still_announced(tmp_path):
    """A separate report of the same sanction, for another incident, is a report."""
    db_path, ids = await _seed(tmp_path, "report_beside_appeal", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=None, table="appeal_records")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 3, 'Appeal', 'Upheld', '78', '2026-02-03T00:00:00+00:00')",
            (ids[1],),
        )
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) "
            "VALUES (?, 'TIME', 3, 'Turn 1', 'Divebomb', '77', '2026-02-02T00:00:00+00:00')",
            (ids[1],),
        )
        await db.commit()

    _, events = await _republish(db_path, _bot(), 1)

    assert ("penalties", (1, 1)) in events
    assert ("appeals", (1, 1)) in events


# ---------------------------------------------------------------------------
# The banner heading each round's run (#345)
# ---------------------------------------------------------------------------


async def test_each_round_is_announced_under_its_own_number(tmp_path):
    """The one fault line that cannot read a round from the database names it by the number its
    state carries; the replay's states were built without one, and read "Round 0"."""
    db_path, ids = await _seed(tmp_path, "round_numbers", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor=5001)
    await _verdict(db_path, ids[2], anchor=5002)
    seen: list[int] = []

    async def _post_pen(_bot, state, _records, **_kw):
        seen.append(state.round_number)
        return []

    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.verdict_announcement_service.banner_for_round", MagicMock(return_value=None)
    ), patch("services.results_post_service._delete_posting", new=AsyncMock()):
        await republish_verdicts_from_round(
            _bot(), db_path, DIVISION_ID, 1,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
        )

    assert seen == [1, 2]


def test_the_replays_states_name_the_division():
    from services.result_submission_service import _amend_verdict_state

    state = _amend_verdict_state("x.db", DIVISION_ID, MagicMock(), division_name="Pro")(21)

    assert state.division_name == "Pro"
    assert state.is_amendment is True


async def _banner(db_path, round_id: int, message_id: int, channel=VERDICTS_CHANNEL):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at) "
            "VALUES (?, ?, ?, '2026-02-02T00:00:00+00:00')",
            (round_id, str(channel), str(message_id)),
        )
        await db.commit()


async def test_the_banner_over_a_replaced_run_is_taken_down_with_it(tmp_path):
    """**A banner belongs to no verdict record**, so nothing else knows where it is. Left alone,
    each amendment stranded a header over empty space and posted a fresh one below it."""
    db_path, ids = await _seed(tmp_path, "banner_replaced", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor=5001)
    await _verdict(db_path, ids[2], anchor=5002)
    await _banner(db_path, 1, 6001)
    await _banner(db_path, 2, 6002)

    _, events = await _republish(db_path, _bot(), 1)

    deleted = [anchor for kind, anchor in events if kind == "delete"]
    assert 6001 in deleted and 6002 in deleted
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM verdict_banner_messages")
        assert (await cursor.fetchone())[0] == 0


async def _heads_sanctions(db_path, message_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE verdict_banner_messages SET heads_sanctions = 1 WHERE message_id = ?",
            (str(message_id),),
        )
        await db.commit()


async def _banners_left(db_path) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT message_id FROM verdict_banner_messages ORDER BY id")
        return [row[0] for row in await cursor.fetchall()]


async def test_a_round_left_with_no_verdicts_loses_its_banner(tmp_path):
    """An amendment that removed a round's last verdict took its cards down and left their
    banner heading empty space (decided 2026-09-21)."""
    db_path, ids = await _seed(tmp_path, "banner_emptied", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor=5001)
    await _banner(db_path, 1, 6001)
    await _banner(db_path, 2, 6002)  # round 2's verdicts were all removed

    _, events = await _republish(db_path, _bot(), 1)

    deleted = [anchor for kind, anchor in events if kind == "delete"]
    assert 6001 in deleted and 6002 in deleted
    assert await _banners_left(db_path) == []


async def test_a_banner_over_sanction_cards_is_kept(tmp_path):
    """**Attendance sanction cards share the banner and nothing takes them down**, so a banner
    over one stays, whether or not its round has a verdict left (decided 2026-09-21)."""
    db_path, ids = await _seed(tmp_path, "banner_sanctions", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor=5001)
    await _banner(db_path, 1, 6001)
    await _banner(db_path, 2, 6002)
    await _heads_sanctions(db_path, 6001)  # round 1 re-announces; its old banner heads a sacking
    await _heads_sanctions(db_path, 6002)  # round 2 has no verdict left, only its sanctions

    _, events = await _republish(db_path, _bot(), 1)

    deleted = [anchor for kind, anchor in events if kind == "delete"]
    assert 6001 not in deleted and 6002 not in deleted
    assert 5001 in deleted, "the old verdict card still goes"
    assert await _banners_left(db_path) == ["6001", "6002"]


async def test_a_sanction_card_marks_exactly_its_banner(tmp_path):
    from services.verdict_announcement_service import _mark_banner_over_sanction

    db_path, _ = await _seed(tmp_path, "banner_marked", rounds=(1, 2))
    for round_id, message_id in ((1, 6001), (1, 6003), (2, 6004)):
        await _banner(db_path, round_id, message_id)

    await _mark_banner_over_sanction(db_path, SimpleNamespace(id=6003))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT message_id FROM verdict_banner_messages WHERE heads_sanctions = 1"
        )
        assert [row[0] for row in await cursor.fetchall()] == ["6003"]


async def test_a_card_under_no_banner_marks_nothing(tmp_path):
    from services.verdict_announcement_service import _mark_banner_over_sanction

    db_path, _ = await _seed(tmp_path, "banner_unmarked", rounds=(1,))
    await _banner(db_path, 1, 6001)

    await _mark_banner_over_sanction(db_path, None)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM verdict_banner_messages WHERE heads_sanctions = 1"
        )
        assert (await cursor.fetchone())[0] == 0


async def test_the_banners_go_after_the_replacements_are_up(tmp_path):
    """Produce before destroying, the banner included: it heads what a league is reading until
    the new run is in place."""
    db_path, ids = await _seed(tmp_path, "banner_order", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=5001)
    await _banner(db_path, 1, 6001)

    _, events = await _republish(db_path, _bot(), 1)

    kinds = [kind for kind, _ in events]
    assert kinds.index("penalties") < kinds.index("delete")


async def test_an_earlier_rounds_banner_is_left_alone(tmp_path):
    """Only the rounds being replayed are rebuilt."""
    db_path, ids = await _seed(tmp_path, "banner_earlier", rounds=(1, 2))
    await _verdict(db_path, ids[2], anchor=5002)
    await _banner(db_path, 1, 6001)
    await _banner(db_path, 2, 6002)

    _, events = await _republish(db_path, _bot(), 2)

    deleted = [anchor for kind, anchor in events if kind == "delete"]
    assert 6001 not in deleted
    assert 6002 in deleted


async def test_one_banner_heads_a_rounds_reports_and_its_appeals(tmp_path):
    """A second banner posted for the appeals would head the same run twice — and the poster's
    own fallback records it, so the next amendment would find two to take down for one run."""
    db_path, ids = await _seed(tmp_path, "banner_shared", rounds=(1,))
    await _verdict(db_path, ids[1], anchor=5001)
    await _verdict(db_path, ids[1], anchor=5002, table="appeal_records")
    heads: list = []

    async def _post_pen(_bot, _state, _records, **kwargs):
        heads.append(kwargs.get("head"))
        return []

    async def _post_app(_bot, _state, _records, **kwargs):
        heads.append(kwargs.get("head"))
        return []

    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(side_effect=_post_app),
    ), patch(
        "services.verdict_announcement_service.banner_for_round",
        MagicMock(return_value="the-banner"),
    ), patch(
        "services.results_post_service._delete_posting", new=AsyncMock()
    ):
        await republish_verdicts_from_round(
            _bot(), db_path, DIVISION_ID, 1,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
        )

    assert heads == ["the-banner", "the-banner"]


async def test_a_round_whose_announcements_failed_keeps_its_old_ones(tmp_path):
    """**A whole batch can fail without raising** — an unreadable context, a verdicts channel
    taken away. Taking the originals down then would leave those decisions in no channel at
    all; doubled announcements a league can read and reconcile, missing ones it cannot."""
    db_path, ids = await _seed(tmp_path, "round_failed", rounds=(1, 2))
    await _verdict(db_path, ids[1], anchor=5001)
    await _verdict(db_path, ids[2], anchor=5002)

    async def _post_pen(_bot, state, records, **_kw):
        return ["round one's verdicts channel is gone"] if state.round_id == 1 else []

    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.verdict_announcement_service.banner_for_round", MagicMock(return_value=None)
    ), patch(
        "services.results_post_service._delete_posting", new=AsyncMock()
    ) as delete:
        faults = await republish_verdicts_from_round(
            _bot(), db_path, DIVISION_ID, 1,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
        )

    deleted = [call.args[1] for call in delete.await_args_list]
    assert 5001 not in deleted, "round one's originals were taken down with no replacement"
    assert 5002 in deleted
    assert any("left standing" in fault for fault in faults)


async def test_the_rounds_rebuilt_are_reported_to_the_caller(tmp_path):
    """**Including a round left with nothing to announce.** An amendment that removed a round's
    last verdict has its old announcement taken down on exactly that footing — every decision
    the round carries is in the channel, there being none — and a round whose batch failed is
    not reported, so its originals stay (#345)."""
    db_path, ids = await _seed(tmp_path, "rebuilt_reported", rounds=(1, 2, 3))
    await _verdict(db_path, ids[1], anchor=5001)
    await _verdict(db_path, ids[3], anchor=5003)

    async def _post_pen(_bot, state, records, **_kw):
        return ["round three's channel is gone"] if state.round_id == 3 else []

    rebuilt: list[int] = []
    with patch(
        "services.verdict_announcement_service.post_penalty_announcements",
        new=AsyncMock(side_effect=_post_pen),
    ), patch(
        "services.verdict_announcement_service.post_appeal_announcements",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.verdict_announcement_service.banner_for_round", MagicMock(return_value=None)
    ), patch(
        "services.results_post_service._delete_posting", new=AsyncMock()
    ):
        await republish_verdicts_from_round(
            _bot(), db_path, DIVISION_ID, 1,
            lambda round_id: SimpleNamespace(round_id=round_id, db_path=db_path),
            rebuilt=rebuilt,
        )

    assert sorted(rebuilt) == [1, 2]
