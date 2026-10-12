"""Reposting a whole division's results or standings, from `/results ... sync`.

Issue #208. `repost_results_for_division` and `repost_standings_for_division` were uncovered.
The results sync is now a change on the change queue (#439, `test_rounds_sync_change.py`); what
it finds before it is asked, its status, is read here through `division_repost_status`. They are
what a manager reaches when the automatic posts have gone missing — a channel deleted,
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

**The stored message id is cleared before the repost**, for the same reason as the final repost:
the poster edits when it finds an id and inserts when it does not, so leaving a stale
one has it edit the very message it is meant to be replacing — and the replacement silently never
appears, which is precisely the fault the manager was trying to fix.

**The replacement is produced before the original is destroyed** (Constitution XIV.8, #345).
Every round is reposted first, in round order, and only then are the messages they replace taken
down. The old ordering — delete this session, repost it, move on — left a failure part-way through
with the rounds it had reached rebuilt and the rest deleted with nothing put back. Now a failure
before the deletions leaves the league exactly the board it had, and the caller takes the new
messages down again.

**Standings take the reserve setting once, and results take each round's own label.** A round
still awaiting verdicts is provisional and a finalised one is not, and a sync that labelled a
whole season "Final Results" would tell a league that rounds still under appeal are settled.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.results_post_service import repost_standings_for_division

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


async def _results(db_path, *, guild=None):
    """What the results sync finds for the division: its status, read by
    `division_repost_status`, the repost itself being a change on the queue (#439)."""
    from leaguebot.results.services.results_post_service import division_repost_status

    return await division_repost_status(db_path, DIVISION_ID, guild or _guild())


async def _standings(db_path, *, guild=None):
    with patch(
        "leaguebot.results.services.results_post_service._forget_standings_messages",
        new=AsyncMock(return_value=[]),
    ) as clear, patch(
        "leaguebot.results.services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", new=AsyncMock()
    ) as post:
        status = await repost_standings_for_division(
            db_path, DIVISION_ID, guild or _guild(), bot=MagicMock()
        )
    return status, clear, post


# ---------------------------------------------------------------------------
# The results sync
# ---------------------------------------------------------------------------


async def test_a_division_with_no_results_channel_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="results_nochannel", results_channel=None)

    status = await _results(db_path)

    assert status == "no_channel"


async def test_a_division_with_no_configuration_at_all_says_no_channel(tmp_path):
    """The LEFT JOIN finds nothing, which is the same problem from the manager's side and
    has the same fix."""
    db_path = await _make_db(tmp_path, name="results_noconfig", config_row=False)

    status = await _results(db_path)

    assert status == "no_channel"


async def test_a_channel_that_has_been_deleted_says_no_channel(tmp_path):
    """Configured but gone. From the manager's side there is nowhere to post, and the fix
    is the same `/results channel results` command."""
    db_path = await _make_db(tmp_path, name="results_chgone")

    status = await _results(db_path, guild=_guild(missing=True))

    assert status == "no_channel"


async def test_a_division_with_no_rounds_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="results_norounds", rounds=())

    status = await _results(db_path)

    assert status == "no_rounds"


async def test_a_division_that_does_not_exist_says_no_rounds(tmp_path):
    """Deleted between the command resolving it and the service reading it — there is
    nothing to post and nothing configured, and "no rounds" is the honest half."""
    db_path = await _make_db(tmp_path, name="results_nodiv", division_row=False)

    status = await _results(db_path)

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


async def test_both_championships_old_messages_are_forgotten(tmp_path):
    """Whichever flow posted them — the image path and the text path store their ids
    separately.

    Forgotten rather than deleted: the ids leave the database before the replacement is posted,
    so `post_standings` inserts instead of editing the message it replaces, while the messages
    themselves stand until every round has been reposted (#345).
    """
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


# ---------------------------------------------------------------------------
# The replacement is produced before the original is destroyed (#345)
# ---------------------------------------------------------------------------


async def _standings_recording_order(db_path, *, fail_on_post: int | None = None):
    """Repost a division's standings, recording posts and deletions in order."""
    events: list[tuple[str, object]] = []
    posts = 0

    async def _post(_db, _division_id, round_id, *_a, **_kw):
        nonlocal posts
        posts += 1
        if fail_on_post is not None and posts == fail_on_post:
            raise RuntimeError("Discord said no")
        events.append(("post", round_id))

    async def _delete(_channel, anchor, _ids, **_kw):
        events.append(("delete", anchor))

    with patch(
        "leaguebot.results.services.results_post_service._delete_posting", new=AsyncMock(side_effect=_delete)
    ), patch(
        "leaguebot.results.services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", new=AsyncMock(side_effect=_post)
    ):
        try:
            status = await repost_standings_for_division(
                db_path, DIVISION_ID, _guild(), bot=MagicMock()
            )
        except RuntimeError:
            status = "raised"
    return status, events


async def _seed_standings_messages(db_path, rounds, *, anchor_from: int = 7701):
    """Give each round a driver-standings message, as a posted season would have."""
    async with get_connection(db_path) as db:
        for offset, round_number in enumerate(rounds):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, "
                "driver_user_id, standing_position, total_points, standings_message_id) "
                "VALUES (?, ?, 101, 1, 25, ?)",
                (round_number, DIVISION_ID, anchor_from + offset),
            )
        await db.commit()


async def test_no_standings_are_deleted_until_all_have_been_posted(tmp_path):
    """The same rule as results, on the channel a championship is read from."""
    db_path = await _make_db(
        tmp_path,
        name="std_ptd_order",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )
    await _seed_standings_messages(db_path, (1, 2))

    status, events = await _standings_recording_order(db_path)

    assert status == "ok"
    assert [kind for kind, _ in events] == ["post", "post", "delete", "delete"]


async def test_a_standings_failure_part_way_leaves_the_originals_standing(tmp_path):
    """A championship half-deleted is worse than one not yet updated."""
    db_path = await _make_db(
        tmp_path,
        name="std_ptd_failure",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )
    await _seed_standings_messages(db_path, (1, 2))

    status, events = await _standings_recording_order(db_path, fail_on_post=2)

    assert status == "raised"
    assert [kind for kind, _ in events] == ["post"]


async def test_the_old_standings_go_in_round_order(tmp_path):
    """So the channel reads in round order throughout the rebuild."""
    db_path = await _make_db(
        tmp_path,
        name="std_ptd_delete_order",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )
    await _seed_standings_messages(db_path, (1, 2))

    _, events = await _standings_recording_order(db_path)

    assert [anchor for kind, anchor in events if kind == "delete"] == [7701, 7702]


async def test_the_standings_id_is_cleared_before_its_replacement_is_posted(tmp_path):
    """Otherwise `post_standings` edits the very message it is replacing."""
    db_path = await _make_db(tmp_path, name="std_ptd_cleared")
    await _seed_standings_messages(db_path, (1,))
    seen: list = []

    async def _post(_db, division_id, round_id, *_a, **_kw):
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT standings_message_id FROM driver_standings_snapshots "
                "WHERE round_id = ? AND division_id = ?",
                (round_id, division_id),
            )
            seen.append((await cursor.fetchone())["standings_message_id"])

    with patch(
        "leaguebot.results.services.results_post_service._delete_posting", new=AsyncMock()
    ), patch(
        "leaguebot.results.services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", new=AsyncMock(side_effect=_post)
    ):
        await repost_standings_for_division(
            db_path, DIVISION_ID, _guild(), bot=MagicMock()
        )

    assert seen == [None]


# ---------------------------------------------------------------------------
# A failure part-way is undone, not merely survived (#345)
# ---------------------------------------------------------------------------


async def test_a_failed_standings_rebuild_takes_its_replacements_down_and_restores_the_ids(
    tmp_path,
):
    db_path = await _make_db(
        tmp_path,
        name="std_ptd_undo",
        rounds=((1, "FINAL", "NORMAL"), (2, "FINAL", "NORMAL")),
    )
    await _seed_standings_messages(db_path, (1, 2))
    deleted: list = []
    posts = 0

    async def _post(_db, division_id, round_id, *_a, **_kw):
        nonlocal posts
        posts += 1
        if posts == 2:
            raise RuntimeError("Discord said no")
        from leaguebot.results.services.results_post_service import _set_standings_message_id

        await _set_standings_message_id(
            db_path, division_id, round_id, 9000 + posts, message_ids=f"[{9000 + posts}]"
        )

    async def _delete(_channel, anchor, ids, **_kw):
        deleted.append((anchor, ids))

    with patch(
        "leaguebot.results.services.results_post_service._delete_posting", new=AsyncMock(side_effect=_delete)
    ), patch(
        "leaguebot.results.services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", new=AsyncMock(side_effect=_post)
    ), pytest.raises(RuntimeError):
        await repost_standings_for_division(db_path, DIVISION_ID, _guild(), bot=MagicMock())

    assert deleted == [(9001, [9001])], "only the replacement is taken down"
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, standings_message_id FROM driver_standings_snapshots "
            "ORDER BY round_id"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [(1, 7701), (2, 7702)]


# ---------------------------------------------------------------------------
# The stored standings id survives a change of leader (#345)
# ---------------------------------------------------------------------------


async def test_a_rounds_standings_message_is_found_after_its_leader_changes(tmp_path):
    """**The id is written to the leading driver's row, and a recomputation reorders the rows.**

    Reading the top row therefore returned nothing the moment a penalty or an amendment changed
    who led that round: the posting became unreachable, so the next repost neither replaced nor
    deleted it and the league was left reading two standings for one round. Which row carries
    the id is an implementation detail — the round has one posting either way.
    """
    from leaguebot.results.services.results_post_service import (
        _get_standings_message_id,
        _get_standings_message_ids,
        _set_standings_message_id,
    )

    db_path = await _make_db(tmp_path, name="std_leader_change")
    async with get_connection(db_path) as db:
        for driver, position in ((101, 1), (102, 2)):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, "
                "driver_user_id, standing_position, total_points) VALUES (1, ?, ?, ?, 25)",
                (DIVISION_ID, driver, position),
            )
        await db.commit()
    await _set_standings_message_id(db_path, DIVISION_ID, 1, 5555, message_ids="[5555]")

    # The amendment rescores the round and driver 102 now leads it.
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_standings_snapshots SET standing_position = "
            "CASE driver_user_id WHEN 101 THEN 2 ELSE 1 END WHERE round_id = 1"
        )
        await db.commit()

    assert await _get_standings_message_id(db_path, DIVISION_ID, 1) == 5555
    assert await _get_standings_message_ids(db_path, DIVISION_ID, 1) == [5555]


async def test_only_one_row_of_a_round_names_its_standings_posting(tmp_path):
    """Or a superseded id left on a row that is no longer top would be read as the current one."""
    from leaguebot.results.services.results_post_service import _set_standings_message_id

    db_path = await _make_db(tmp_path, name="std_one_row")
    async with get_connection(db_path) as db:
        for driver, position in ((101, 1), (102, 2)):
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, "
                "driver_user_id, standing_position, total_points) VALUES (1, ?, ?, ?, 25)",
                (DIVISION_ID, driver, position),
            )
        await db.commit()
    await _set_standings_message_id(db_path, DIVISION_ID, 1, 5555, message_ids="[5555]")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_standings_snapshots SET standing_position = "
            "CASE driver_user_id WHEN 101 THEN 2 ELSE 1 END WHERE round_id = 1"
        )
        await db.commit()

    await _set_standings_message_id(db_path, DIVISION_ID, 1, 6666, message_ids="[6666]")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT standings_message_id FROM driver_standings_snapshots "
            "WHERE round_id = 1 AND standings_message_id IS NOT NULL"
        )
        assert [r[0] for r in await cursor.fetchall()] == [6666]


async def test_a_standings_table_that_shrinks_drops_the_chunks_it_no_longer_fills(tmp_path):
    """**Editing the anchor in place leaves the rest of a longer posting behind** (#345).

    A standings table past Discord's limit occupies several messages. When the next one fits in
    one, the anchor is edited and the others have to go — and once the stored list is rewritten
    as a single message, nothing could reach them again now that the adjacency walk is retired.
    """
    from leaguebot.results.models.standings_snapshot import DriverStandingsSnapshot
    from leaguebot.results.services.results_post_service import post_standings, _set_standings_message_id

    db_path = await _make_db(tmp_path, name="std_shrink")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
            "standing_position, total_points) VALUES (1, ?, 101, 1, 25)",
            (DIVISION_ID,),
        )
        await db.commit()
    await _set_standings_message_id(
        db_path, DIVISION_ID, 1, 7001, message_ids="[7001, 7002, 7003]"
    )
    deleted: list = []

    async def _delete(_channel, anchor, ids, **_kw):
        deleted.append(anchor)

    existing = AsyncMock()
    existing.id = 7001
    channel = MagicMock()
    channel.fetch_message = AsyncMock(return_value=existing)
    channel.send = AsyncMock(return_value=MagicMock(id=7009))

    with patch(
        "leaguebot.results.services.results_post_service._delete_posting", new=AsyncMock(side_effect=_delete)
    ):
        await post_standings(
            db_path=db_path,
            division_id=DIVISION_ID,
            round_id=1,
            round_number=1,
            track_name="Track 1",
            standings_channel=channel,
            driver_snapshots=[
                DriverStandingsSnapshot(
                    id=0, round_id=1, division_id=DIVISION_ID, driver_user_id=101,
                    standing_position=1, total_points=25, finish_counts={},
                    first_finish_rounds={},
                )
            ],
            team_snapshots=[],
            guild=_guild(),
            show_reserves=False,
            label="Final Results",
        )

    existing.edit.assert_awaited_once()
    assert deleted == [7002, 7003]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT standings_message_ids FROM driver_standings_snapshots "
            "WHERE standings_message_id IS NOT NULL"
        )
        assert (await cursor.fetchone())[0] == "[7001]"
