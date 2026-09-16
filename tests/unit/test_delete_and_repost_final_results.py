"""Replacing a round's provisional results with the final ones, once the penalties are in.

Issue #208. `delete_and_repost_final_results` was uncovered. It runs when a penalty review is
confirmed, and its job is that a division is left with *one* results table per session and one
set of standings — the corrected ones.

**The old message is deleted, not edited.** A results table can run to several messages when a
division is large, so the interim post is a chain, and editing the first of a chain would leave
the rest of the provisional classification standing beneath the corrected one. Deleting the
whole chain and posting fresh is what keeps a channel from accumulating a copy of the results
for every penalty a round attracts.

**The stored message id is cleared between the delete and the repost.** `post_session_results`
inserts a fresh row when it finds no id and edits when it finds one, so leaving the old id in
place would have it try to edit a message that had just been deleted — and the final results
would never appear. `test_the_stale_message_id_is_cleared_before_reposting` is what holds that
order.

**Both championships' standings messages go, whichever flow posted them.** The image flow and
the text flow store their ids separately, so clearing only the one this run would have posted
leaves the other championship showing provisional positions next to final ones.

**Points are read again, not carried in.** The whole point of the repost is that the penalties
changed them; the driver rows are reloaded and the fastest-lap bonus added back to the awarded
points, because the two are stored apart and a table showing only one of them understates
everyone who set a fastest lap.

**A missing channel is not an error.** By the time this runs the penalties are already applied
and the database is correct; a deleted results channel must not stop the standings being
reposted, and neither must stop the review being confirmed.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.results_post_service import delete_and_repost_final_results  # noqa: E402

SERVER_ID = 11508
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701
OLD_MESSAGE_ID = 8800
LABEL = "Final Results"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "final_repost",
    sessions=(("FEATURE_RACE", "ACTIVE", OLD_MESSAGE_ID),),
    results_channel: int | None = RESULTS_CHANNEL,
    standings_channel: int | None = STANDINGS_CHANNEL,
    fmt: str = "NORMAL",
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
            "track_name, status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', ?, "
            "'Silverstone', 'FINAL')",
            (ROUND_ID, DIVISION_ID, fmt),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, reserves_in_standings) VALUES (?, ?, ?, 1)",
            (DIVISION_ID, results_channel, standings_channel),
        )
        for session_type, status, message_id in sessions:
            await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name, results_message_id) VALUES (?, ?, ?, ?, 'Standard', ?)",
                (ROUND_ID, DIVISION_ID, session_type, status, message_id),
            )
        await db.commit()
    return db_path


def _guild(*, missing: bool = False):
    guild = MagicMock()
    channel = MagicMock()
    channel.id = RESULTS_CHANNEL
    guild.get_channel = MagicMock(return_value=None if missing else channel)
    guild._channel = channel
    return guild


async def _repost(db_path, *, guild=None, driver_rows=None):
    driver_rows = driver_rows if driver_rows is not None else []
    with patch(
        "services.results_post_service._delete_with_continuations", new=AsyncMock()
    ) as delete, patch(
        "services.results_post_service._load_driver_rows",
        new=AsyncMock(return_value=driver_rows),
    ), patch(
        "services.results_post_service.post_session_results", new=AsyncMock()
    ) as post_results, patch(
        "services.results_post_service._clear_standings_messages", new=AsyncMock()
    ) as clear, patch(
        "services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.post_standings", new=AsyncMock()
    ) as post_standings:
        await delete_and_repost_final_results(
            db_path, ROUND_ID, DIVISION_ID, guild or _guild(), LABEL
        )
    return {
        "delete": delete,
        "post_results": post_results,
        "clear": clear,
        "post_standings": post_standings,
    }


async def _message_ids(db_path) -> list:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_message_id FROM session_results WHERE round_id = ? ORDER BY id",
            (ROUND_ID,),
        )
        return [r["results_message_id"] for r in await cursor.fetchall()]


def _driver_row(user_id: int, points: int, bonus: int = 0):
    return MagicMock(
        driver_user_id=user_id, points_awarded=points, fastest_lap_bonus=bonus
    )


# ---------------------------------------------------------------------------
# The results table
# ---------------------------------------------------------------------------


async def test_the_interim_message_is_deleted(tmp_path):
    """Not edited: a large division's table runs to several messages, and editing the first
    of a chain leaves the rest of the provisional classification standing beneath the
    corrected one."""
    db_path = await _make_db(tmp_path)

    stubs = await _repost(db_path)

    stubs["delete"].assert_awaited_once()
    assert stubs["delete"].await_args.args[1] == OLD_MESSAGE_ID


async def test_the_final_results_are_posted(tmp_path):
    db_path = await _make_db(tmp_path, name="final_posted")

    stubs = await _repost(db_path)

    stubs["post_results"].assert_awaited_once()
    assert LABEL in stubs["post_results"].await_args.args


async def test_the_stale_message_id_is_cleared_before_reposting(tmp_path):
    """`post_session_results` edits when it finds an id and inserts when it does not, so
    leaving the old one would have it edit a message that had just been deleted — and the
    final results would never appear."""
    db_path = await _make_db(tmp_path, name="final_cleared")
    seen: dict[str, list] = {}

    async def _record(*_args, **_kwargs):
        seen["ids"] = await _message_ids(db_path)

    with patch(
        "services.results_post_service._delete_with_continuations", new=AsyncMock()
    ), patch(
        "services.results_post_service._load_driver_rows", new=AsyncMock(return_value=[])
    ), patch(
        "services.results_post_service.post_session_results",
        new=AsyncMock(side_effect=_record),
    ), patch(
        "services.results_post_service._clear_standings_messages", new=AsyncMock()
    ), patch(
        "services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.post_standings", new=AsyncMock()
    ):
        await delete_and_repost_final_results(
            db_path, ROUND_ID, DIVISION_ID, _guild(), LABEL
        )

    assert seen["ids"] == [None]


async def test_a_session_never_posted_is_still_posted_now(tmp_path):
    """A round whose interim post failed still has final results to publish, and skipping
    it because there is nothing to delete would leave that session unpublished for good."""
    db_path = await _make_db(
        tmp_path, name="final_neverposted", sessions=(("FEATURE_RACE", "ACTIVE", None),)
    )

    stubs = await _repost(db_path)

    stubs["delete"].assert_not_awaited()
    stubs["post_results"].assert_awaited_once()


async def test_every_active_session_is_replaced(tmp_path):
    """A sprint round has four, and one left showing provisional results next to three
    final ones is the confusing half-state this exists to prevent."""
    db_path = await _make_db(
        tmp_path,
        name="final_all",
        sessions=(
            ("SPRINT_QUALIFYING", "ACTIVE", 8801),
            ("SPRINT_RACE", "ACTIVE", 8802),
            ("FEATURE_QUALIFYING", "ACTIVE", 8803),
            ("FEATURE_RACE", "ACTIVE", 8804),
        ),
        fmt="SPRINT",
    )

    stubs = await _repost(db_path)

    assert stubs["post_results"].await_count == 4
    assert [c.args[1] for c in stubs["delete"].await_args_list] == [8801, 8802, 8803, 8804]


async def test_a_superseded_session_is_not_reposted(tmp_path):
    """Its results were replaced, not penalised, and reposting them would publish a
    classification the round no longer has."""
    db_path = await _make_db(
        tmp_path,
        name="final_superseded",
        sessions=(
            ("FEATURE_RACE", "ACTIVE", 8801),
            ("FEATURE_QUALIFYING", "SUPERSEDED", 8802),
        ),
    )

    stubs = await _repost(db_path)

    assert stubs["post_results"].await_count == 1
    assert [c.args[1] for c in stubs["delete"].await_args_list] == [8801]


async def test_the_points_are_read_again(tmp_path):
    """The whole point of the repost is that the penalties changed them."""
    db_path = await _make_db(tmp_path, name="final_points")

    stubs = await _repost(db_path, driver_rows=[_driver_row(101, 18), _driver_row(102, 15)])

    points_map = stubs["post_results"].await_args.args[3]
    assert points_map == {101: 18, 102: 15}


async def test_the_fastest_lap_bonus_is_added_back_in(tmp_path):
    """The bonus is stored apart from the awarded points, and a table showing only one of
    them understates everyone who set a fastest lap."""
    db_path = await _make_db(tmp_path, name="final_fl")

    stubs = await _repost(db_path, driver_rows=[_driver_row(101, 18, bonus=1)])

    assert stubs["post_results"].await_args.args[3] == {101: 19}


async def test_the_round_is_reposted_under_its_own_number_and_track(tmp_path):
    db_path = await _make_db(tmp_path, name="final_title")

    stubs = await _repost(db_path)

    args = stubs["post_results"].await_args.args
    assert args[6] == 3
    assert args[7] == "Silverstone"


async def test_a_sprint_round_is_posted_as_one(tmp_path):
    """The format changes how the table is laid out, and it is read from the round rather
    than inferred from the sessions present."""
    db_path = await _make_db(tmp_path, name="final_sprint", fmt="SPRINT")

    stubs = await _repost(db_path)

    assert stubs["post_results"].await_args.args[9] is True


async def test_a_normal_round_is_not(tmp_path):
    db_path = await _make_db(tmp_path, name="final_normal", fmt="NORMAL")

    stubs = await _repost(db_path)

    assert stubs["post_results"].await_args.args[9] is False


# ---------------------------------------------------------------------------
# The standings
# ---------------------------------------------------------------------------


async def test_both_championships_interim_messages_are_cleared(tmp_path):
    """The image flow and the text flow store their ids separately; clearing only the one
    this run would have posted leaves the other showing provisional positions next to
    final ones."""
    db_path = await _make_db(tmp_path, name="final_clear")

    stubs = await _repost(db_path)

    stubs["clear"].assert_awaited_once()


async def test_the_final_standings_are_posted(tmp_path):
    db_path = await _make_db(tmp_path, name="final_standings")

    stubs = await _repost(db_path)

    stubs["post_standings"].assert_awaited_once()
    assert LABEL in stubs["post_standings"].await_args.args


async def test_the_divisions_reserve_setting_is_carried_through(tmp_path):
    """A league that leaves reserves out of its championship must not have them reappear
    the moment a penalty triggers a repost."""
    db_path = await _make_db(tmp_path, name="final_reserves")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET reserves_in_standings = 0 WHERE division_id = ?",
            (DIVISION_ID,),
        )
        await db.commit()

    stubs = await _repost(db_path)

    assert stubs["post_standings"].await_args.args[9] is False


# ---------------------------------------------------------------------------
# What is missing
# ---------------------------------------------------------------------------


async def test_a_round_that_does_not_exist_does_nothing(tmp_path):
    """Called from the confirmation of a review; raising here would fail a review whose
    penalties are already applied."""
    db_path = await _make_db(tmp_path, name="final_noround")

    with patch(
        "services.results_post_service.post_session_results", new=AsyncMock()
    ) as post:
        await delete_and_repost_final_results(db_path, 9999, DIVISION_ID, _guild(), LABEL)

    post.assert_not_awaited()


async def test_a_division_with_no_results_channel_still_reposts_the_standings(tmp_path):
    """The two are configured separately, and one being absent is not a reason to withhold
    the other."""
    db_path = await _make_db(tmp_path, name="final_nores", results_channel=None)

    stubs = await _repost(db_path)

    stubs["post_results"].assert_not_awaited()
    stubs["post_standings"].assert_awaited_once()


async def test_a_division_with_no_standings_channel_still_reposts_the_results(tmp_path):
    db_path = await _make_db(tmp_path, name="final_nostand", standings_channel=None)

    stubs = await _repost(db_path)

    stubs["post_standings"].assert_not_awaited()
    stubs["post_results"].assert_awaited_once()


async def test_a_deleted_channel_is_stepped_over(tmp_path):
    """By the time this runs the penalties are applied and the database is correct; a
    channel that has gone must not fail the review that is confirming them."""
    db_path = await _make_db(tmp_path, name="final_gone")

    stubs = await _repost(db_path, guild=_guild(missing=True))

    stubs["post_results"].assert_not_awaited()
    stubs["post_standings"].assert_not_awaited()
