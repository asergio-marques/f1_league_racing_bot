"""Reposting a whole division's results or standings, from `/results ... sync`.

Issue #208. `repost_results_for_division` and `repost_standings_for_division` were uncovered.
They are what a manager reaches when the automatic posts have gone missing — a channel deleted,
a message purged, a restart at the wrong moment — and they exist so a league does not have to
re-race a round to get its board back.

**They answer with a status, not a boolean.** "Reposted", "there are no completed rounds" and
"no channel is configured" are three different situations with three different next steps, and
the command above translates each into words a manager can act on. Collapsing them would leave a
manager unable to tell an empty division from an unconfigured one.

**A configured channel that has since been deleted is `no_channel`, not a failure.** From the
manager's side the two are the same problem — there is nowhere to post — and the fix is the same
`/division ...-channel` command.

**Only rounds with active results are reposted.** A round that has not been submitted has no
results to show, and a superseded one is not what the round is any more. Both are excluded by
the query rather than skipped in the loop, which is why each is worth its own test.

**The stored message id is cleared between the delete and the repost**, for the same reason as
the final repost: `post_session_results` edits when it finds an id and inserts when it does not,
so leaving a stale one has it edit a message that has just been deleted — and the repost silently
never appears, which is precisely the fault the manager was trying to fix.

**Standings take the reserve setting once, and results take each round's own label.** A round
still awaiting verdicts is provisional and a finalised one is not, and a sync that labelled a
whole season "Final Results" would tell a league that rounds still under appeal are settled.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.results_post_service import (  # noqa: E402
    repost_results_for_division,
    repost_standings_for_division,
)

SERVER_ID = 12508
SEASON_ID = 1
DIVISION_ID = 11
RESULTS_CHANNEL = 700
STANDINGS_CHANNEL = 701


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "repost_division",
    rounds=((1, "FINAL", "NORMAL"),),
    sessions=(("FEATURE_RACE", "ACTIVE", None),),
    results_channel: int | None = RESULTS_CHANNEL,
    standings_channel: int | None = STANDINGS_CHANNEL,
    config_row: bool = True,
    division_row: bool = True,
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
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        if division_row:
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, 'Pro', 1, 555)",
                (DIVISION_ID, SEASON_ID),
            )
            if config_row:
                await db.execute(
                    "INSERT INTO division_results_config (division_id, "
                    "results_channel_id, standings_channel_id, reserves_in_standings) "
                    "VALUES (?, ?, ?, 1)",
                    (DIVISION_ID, results_channel, standings_channel),
                )
            for number, status, fmt in rounds:
                await db.execute(
                    "INSERT INTO rounds (id, division_id, round_number, scheduled_at, "
                    "format, track_name, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        number,
                        DIVISION_ID,
                        number,
                        f"2026-0{number}-01T18:00:00+00:00",
                        fmt,
                        f"Track {number}",
                        status,
                    ),
                )
                for session_type, sr_status, message_id in sessions:
                    await db.execute(
                        "INSERT INTO session_results (round_id, division_id, "
                        "session_type, status, config_name, results_message_id) "
                        "VALUES (?, ?, ?, ?, 'Standard', ?)",
                        (number, DIVISION_ID, session_type, sr_status, message_id),
                    )
        await db.commit()
    return db_path


def _guild(*, missing: bool = False):
    guild = MagicMock()
    channel = MagicMock()
    guild.get_channel = MagicMock(return_value=None if missing else channel)
    guild._channel = channel
    return guild


async def _results(db_path, *, guild=None, driver_rows=None):
    driver_rows = driver_rows if driver_rows is not None else []
    with patch(
        "services.results_post_service._delete_with_continuations", new=AsyncMock()
    ) as delete, patch(
        "services.results_post_service._load_driver_rows",
        new=AsyncMock(return_value=driver_rows),
    ), patch(
        "services.results_post_service.post_session_results", new=AsyncMock()
    ) as post:
        status = await repost_results_for_division(
            db_path, DIVISION_ID, guild or _guild(), bot=MagicMock()
        )
    return status, delete, post


async def _standings(db_path, *, guild=None):
    with patch(
        "services.results_post_service._clear_standings_messages", new=AsyncMock()
    ) as clear, patch(
        "services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.post_standings", new=AsyncMock()
    ) as post:
        status = await repost_standings_for_division(
            db_path, DIVISION_ID, guild or _guild(), bot=MagicMock()
        )
    return status, clear, post


async def _message_ids(db_path) -> list:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_message_id FROM session_results ORDER BY id"
        )
        return [r["results_message_id"] for r in await cursor.fetchall()]


def _driver_row(user_id: int, points: int, bonus: int = 0):
    return MagicMock(
        driver_user_id=user_id, points_awarded=points, fastest_lap_bonus=bonus
    )


# ---------------------------------------------------------------------------
# The results sync
# ---------------------------------------------------------------------------


async def test_a_divisions_results_are_reposted(tmp_path):
    db_path = await _make_db(tmp_path, name="results_ok")

    status, _, post = await _results(db_path)

    assert status == "ok"
    post.assert_awaited_once()


async def test_every_round_of_the_division_is_reposted(tmp_path):
    """A sync exists because a whole channel can be lost; reposting one round would leave
    the board half rebuilt."""
    db_path = await _make_db(
        tmp_path,
        name="results_all",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL"), (3, "FINAL", "NORMAL")),
    )

    _, _, post = await _results(db_path)

    assert [call.args[6] for call in post.await_args_list] == [1, 2, 3]


async def test_the_rounds_go_in_order(tmp_path):
    """Read by round number, so a channel rebuilt from scratch reads as the season ran."""
    db_path = await _make_db(
        tmp_path,
        name="results_order",
        rounds=((3, "FINAL", "NORMAL"), (1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )

    _, _, post = await _results(db_path)

    assert [call.args[6] for call in post.await_args_list] == [1, 2, 3]


async def test_every_session_of_a_round_is_reposted(tmp_path):
    """A sprint round has four, and one missing from a rebuilt channel is the fault the
    sync was run to fix."""
    db_path = await _make_db(
        tmp_path,
        name="results_sessions",
        rounds=((1, "FINAL", "SPRINT"),),
        sessions=(
            ("SPRINT_QUALIFYING", "ACTIVE", None),
            ("SPRINT_RACE", "ACTIVE", None),
            ("FEATURE_QUALIFYING", "ACTIVE", None),
            ("FEATURE_RACE", "ACTIVE", None),
        ),
    )

    _, _, post = await _results(db_path)

    assert post.await_count == 4


async def test_an_existing_message_is_deleted_first(tmp_path):
    """Not edited: a large division's table runs to several messages, so the chain goes and
    a fresh one replaces it."""
    db_path = await _make_db(
        tmp_path, name="results_delete", sessions=(("FEATURE_RACE", "ACTIVE", 8800),)
    )

    _, delete, _ = await _results(db_path)

    delete.assert_awaited_once()
    assert delete.await_args.args[1] == 8800


async def test_the_stale_message_id_is_cleared_before_reposting(tmp_path):
    """`post_session_results` edits when it finds an id, so leaving the old one has it edit
    a message that has just been deleted — and the repost never appears, which is exactly
    the fault the manager ran the sync to fix."""
    db_path = await _make_db(
        tmp_path, name="results_cleared", sessions=(("FEATURE_RACE", "ACTIVE", 8800),)
    )
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
    ):
        await repost_results_for_division(db_path, DIVISION_ID, _guild(), bot=MagicMock())

    assert seen["ids"] == [None]


async def test_a_session_never_posted_is_posted_now(tmp_path):
    """Which is the other reason to run a sync: the original post failed and the results
    have never been seen."""
    db_path = await _make_db(tmp_path, name="results_never")

    _, delete, post = await _results(db_path)

    delete.assert_not_awaited()
    post.assert_awaited_once()


async def test_a_superseded_session_is_not_reposted(tmp_path):
    """It is not what the round is any more, and reposting it would put a corrected-away
    classification back on the board."""
    db_path = await _make_db(
        tmp_path,
        name="results_superseded",
        sessions=(
            ("FEATURE_RACE", "ACTIVE", None),
            ("FEATURE_QUALIFYING", "SUPERSEDED", None),
        ),
    )

    _, _, post = await _results(db_path)

    assert post.await_count == 1


async def test_a_round_with_no_results_at_all_is_skipped(tmp_path):
    """A round that has not been submitted has nothing to show, and a heading with an empty
    table under it is worse than its absence."""
    db_path = await _make_db(
        tmp_path,
        name="results_unsubmitted",
        rounds=((1, "FINAL", "NORMAL"), (2, "AWAITING_RESULTS", "NORMAL")),
        sessions=(("FEATURE_RACE", "ACTIVE", None),),
    )
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM session_results WHERE round_id = 2")
        await db.commit()

    _, _, post = await _results(db_path)

    assert [call.args[6] for call in post.await_args_list] == [1]


async def test_each_round_is_labelled_by_its_own_status(tmp_path):
    """A round still awaiting verdicts is provisional and a finalised one is not; a sync
    labelling a whole season "Final Results" would tell a league that rounds under appeal
    are settled."""
    db_path = await _make_db(
        tmp_path,
        name="results_labels",
        rounds=((1, "FINAL", "NORMAL"), (2, "AWAITING_APPEAL_VERDICTS", "NORMAL")),
    )

    _, _, post = await _results(db_path)

    labels = {call.args[6]: call.args[8] for call in post.await_args_list}
    assert labels[1] == "Final Results"
    assert labels[2] == "Post-Race Penalty Results"


async def test_a_sprint_round_is_posted_as_one(tmp_path):
    db_path = await _make_db(
        tmp_path, name="results_sprintfmt", rounds=((1, "FINAL", "SPRINT"),)
    )

    _, _, post = await _results(db_path)

    assert post.await_args.args[9] is True


async def test_the_points_include_the_fastest_lap_bonus(tmp_path):
    """Stored apart from the awarded points; a table showing only one understates everyone
    who set a fastest lap."""
    db_path = await _make_db(tmp_path, name="results_fl")

    _, _, post = await _results(db_path, driver_rows=[_driver_row(101, 18, bonus=1)])

    assert post.await_args.args[3] == {101: 19}


async def test_a_division_with_no_results_channel_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="results_nochannel", results_channel=None)

    status, _, post = await _results(db_path)

    assert status == "no_channel"
    post.assert_not_awaited()


async def test_a_division_with_no_configuration_at_all_says_no_channel(tmp_path):
    """The LEFT JOIN finds nothing, which is the same problem from the manager's side and
    has the same fix."""
    db_path = await _make_db(tmp_path, name="results_noconfig", config_row=False)

    status, _, _ = await _results(db_path)

    assert status == "no_channel"


async def test_a_channel_that_has_been_deleted_says_no_channel(tmp_path):
    """Configured but gone. From the manager's side there is nowhere to post, and the fix
    is the same `/division results-channel` command."""
    db_path = await _make_db(tmp_path, name="results_chgone")

    status, _, post = await _results(db_path, guild=_guild(missing=True))

    assert status == "no_channel"
    post.assert_not_awaited()


async def test_a_division_with_no_rounds_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="results_norounds", rounds=())

    status, _, post = await _results(db_path)

    assert status == "no_rounds"
    post.assert_not_awaited()


async def test_a_division_that_does_not_exist_says_no_rounds(tmp_path):
    """Deleted between the command resolving it and the service reading it — there is
    nothing to post and nothing configured, and "no rounds" is the honest half."""
    db_path = await _make_db(tmp_path, name="results_nodiv", division_row=False)

    status, _, _ = await _results(db_path)

    assert status == "no_rounds"


# ---------------------------------------------------------------------------
# The standings sync
# ---------------------------------------------------------------------------


async def test_a_divisions_standings_are_reposted(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_ok")

    status, _, post = await _standings(db_path)

    assert status == "ok"
    post.assert_awaited_once()


async def test_every_round_gets_its_standings_back(tmp_path):
    """A championship is cumulative and the board shows a snapshot per round; rebuilding
    only the last would leave a season with one entry."""
    db_path = await _make_db(
        tmp_path,
        name="standings_all",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )

    _, _, post = await _standings(db_path)

    assert [call.args[3] for call in post.await_args_list] == [1, 2]


async def test_both_championships_old_messages_are_cleared(tmp_path):
    """Whichever flow posted them — the image path and the text path store their ids
    separately."""
    db_path = await _make_db(tmp_path, name="standings_clear")

    _, clear, _ = await _standings(db_path)

    clear.assert_awaited_once()


async def test_the_reserve_setting_is_read_once_for_the_division(tmp_path):
    """It is a division setting, not a round one, and reading it per round would be a
    database round trip for every round of the season."""
    db_path = await _make_db(
        tmp_path,
        name="standings_reserves",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET reserves_in_standings = 0 "
            "WHERE division_id = ?",
            (DIVISION_ID,),
        )
        await db.commit()

    _, _, post = await _standings(db_path)

    assert all(call.args[9] is False for call in post.await_args_list)


async def test_each_round_is_labelled_by_its_own_status(tmp_path):
    db_path = await _make_db(
        tmp_path,
        name="standings_labels",
        rounds=((1, "FINAL", "NORMAL"), (2, "AWAITING_REPORT_VERDICTS", "NORMAL")),
    )

    _, _, post = await _standings(db_path)

    labels = {call.args[3]: call.args[10] for call in post.await_args_list}
    assert labels[1] == "Final Results"
    assert labels[2] == "Provisional Results"


async def test_a_division_with_no_standings_channel_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_nochannel", standings_channel=None)

    status, _, post = await _standings(db_path)

    assert status == "no_channel"
    post.assert_not_awaited()


async def test_a_deleted_standings_channel_says_no_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_chgone")

    status, _, post = await _standings(db_path, guild=_guild(missing=True))

    assert status == "no_channel"
    post.assert_not_awaited()


async def test_a_division_with_no_submitted_rounds_says_no_rounds(tmp_path):
    """Checked before the channel, unlike the results sync — a division with neither is
    told it has no rounds, which is the more actionable of the two."""
    db_path = await _make_db(tmp_path, name="standings_norounds", rounds=())

    status, _, post = await _standings(db_path)

    assert status == "no_rounds"
    post.assert_not_awaited()


async def test_a_superseded_round_alone_counts_as_no_rounds(tmp_path):
    """Its results were replaced; there is no standings snapshot that describes it."""
    db_path = await _make_db(
        tmp_path,
        name="standings_superseded",
        sessions=(("FEATURE_RACE", "SUPERSEDED", None),),
    )

    status, _, _ = await _standings(db_path)

    assert status == "no_rounds"


async def test_a_round_is_reposted_under_its_own_number_and_track(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_titles")

    _, _, post = await _standings(db_path)

    args = post.await_args.args
    assert args[3] == 1
    assert args[4] == "Track 1"
