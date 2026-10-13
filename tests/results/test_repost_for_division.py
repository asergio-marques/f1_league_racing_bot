"""What a division's `/results ... sync` finds before it is asked of the change queue.

Issue #208. Both syncs are changes on the change queue (#439: the results sync in
`test_rounds_sync_change.py`, the standings sync in `test_standings_sync_change.py`), and each
repost, its order and its labels are pinned there. What each command finds before it asks, its
status, is read here: `division_repost_status` for the results, `standings_repost_status` for
the standings. They are what a manager reaches when the automatic posts have gone missing — a
channel deleted, a message purged, a restart at the wrong moment — and they exist so a league
does not have to re-race a round to get its board back.

**They answer with a status, not a boolean.** "Reposted", "there are no completed rounds" and
"no channel is configured" are three different situations with three different next steps, and
the command above translates each into words a manager can act on. Collapsing them would leave a
manager unable to tell an empty division from an unconfigured one.

**A configured channel that has since been deleted is `no_channel`, not a failure.** From the
manager's side the two are the same problem — there is nowhere to post — and the fix is the same
`/division ...-channel` command.

**Only rounds with active results count.** A round that has not been submitted has no results to
show, and a superseded one is not what the round is any more. Both are excluded by the query
rather than skipped in the loop, which is why each is worth its own test.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Why the standings status tests fail until C21 builds `standings_repost_status`.
NO_STANDINGS_STATUS = (
    "#439: the standings sync's status is read by repost_standings_for_division as it reposts, "
    "not by a standings_repost_status of its own"
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


async def _results(db_path, *, guild=None):
    """What the results sync finds for the division: its status, read by
    `division_repost_status`, the repost itself being a change on the queue (#439)."""
    from leaguebot.results.services.results_post_service import division_repost_status

    return await division_repost_status(db_path, DIVISION_ID, guild or _guild())


async def _standings(db_path, *, guild=None):
    """What the standings sync finds for the division: its status, read by
    `standings_repost_status`, the repost itself being a change on the queue (#439 6b)."""
    from leaguebot.results.services.results_post_service import standings_repost_status

    return await standings_repost_status(db_path, DIVISION_ID, guild or _guild())


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


@pytest.mark.xfail(strict=True, reason=NO_STANDINGS_STATUS)
async def test_a_division_with_no_standings_channel_says_so(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_nochannel", standings_channel=None)

    status = await _standings(db_path)

    assert status == "no_channel"


@pytest.mark.xfail(strict=True, reason=NO_STANDINGS_STATUS)
async def test_a_deleted_standings_channel_says_no_channel(tmp_path):
    db_path = await _make_db(tmp_path, name="standings_chgone")

    status = await _standings(db_path, guild=_guild(missing=True))

    assert status == "no_channel"


@pytest.mark.xfail(strict=True, reason=NO_STANDINGS_STATUS)
async def test_a_division_with_no_submitted_rounds_says_no_rounds(tmp_path):
    """Checked before the channel, unlike the results sync — a division with neither is
    told it has no rounds, which is the more actionable of the two."""
    db_path = await _make_db(tmp_path, name="standings_norounds", rounds=())

    status = await _standings(db_path)

    assert status == "no_rounds"


@pytest.mark.xfail(strict=True, reason=NO_STANDINGS_STATUS)
async def test_a_superseded_round_alone_counts_as_no_rounds(tmp_path):
    """Its results were replaced; there is no standings snapshot that describes it."""
    db_path = await _make_db(
        tmp_path,
        name="standings_superseded",
        sessions=(("FEATURE_RACE", "SUPERSEDED", None),),
    )

    status = await _standings(db_path)

    assert status == "no_rounds"
