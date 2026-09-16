"""Recomputing the rest of a season after a penalty changes one round's points.

Issue #208. `repost_subsequent_standings` runs after a round's final results are corrected, and
was uncovered. A championship is cumulative, so a penalty applied to round 3 changes the
standings posted for rounds 4, 5 and 6 as well — and those are messages already sitting in a
channel, which nobody edits by hand.

**The recompute comes first, and it covers every later round whether or not one was posted.**
The database snapshots are the league's record of the championship; the Discord messages are
only a view of them. A round nobody posted standings for still has to be recomputed, or the
round after it would be built on a total that was never corrected.

**A round is reposted only if it was posted.** Reposting a round whose standings were never
published would announce a championship position out of nowhere, in the middle of a correction
nobody asked to see.

**"Posted" means either championship, not the drivers' alone.** The image flow can leave the
two in different states, so a round whose constructors graphic stands and whose drivers message
does not is still a posted round — and checking only the drivers column is how it would never be
recomputed. That is `test_a_round_with_only_a_constructors_message_is_reposted`, and it is the
reason the check is written as `any` over both.

**Cancelled rounds are skipped.** A cancelled round scores nothing, so its standings are the
round before it and reposting them would tell a division its championship changed when it did
not.

**A round is skipped rather than raising when its channel has gone.** The recompute has already
happened by then, so the database is correct either way; refusing to continue would leave the
later rounds of the season unreposted because of one deleted channel.

**Only rounds *after* the corrected one move.** The correction itself is posted by
`delete_and_repost_final_results`; doing it again here would post two copies of the round a
league is actually looking at.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.results_post_service import (  # noqa: E402
    STANDINGS_CONSTRUCTORS,
    STANDINGS_DRIVERS,
    repost_subsequent_standings,
)

SERVER_ID = 10608
SEASON_ID = 1
DIVISION_ID = 11
STANDINGS_CHANNEL = 4242


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "repost_standings",
    rounds=((1, "FINAL"), (2, "FINAL"), (3, "FINAL")),
    standings_channel: int | None = STANDINGS_CHANNEL,
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
        for number, status in rounds:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, "
                "format, track_name, status) "
                "VALUES (?, ?, ?, ?, 'NORMAL', ?, ?)",
                (
                    number,
                    DIVISION_ID,
                    number,
                    f"2026-0{number}-01T18:00:00+00:00",
                    f"Track {number}",
                    status,
                ),
            )
        if standings_channel is not None:
            await db.execute(
                "INSERT INTO division_results_config (division_id, standings_channel_id, "
                "reserves_in_standings) VALUES (?, ?, 1)",
                (DIVISION_ID, standings_channel),
            )
        await db.commit()
    return db_path


def _guild(*, channel_missing: bool = False):
    guild = MagicMock()
    channel = MagicMock()
    channel.id = STANDINGS_CHANNEL
    guild.get_channel = MagicMock(return_value=None if channel_missing else channel)
    return guild


async def _run(db_path, *, from_round: int = 1, guild=None, posted=None):
    """Run the cascade with everything below it stubbed.

    *posted* maps round_id -> {championship: message_id}; anything absent is unposted.
    """
    posted = posted if posted is not None else {}

    async def _message_id(_db, _div, round_id, championship):
        return posted.get(round_id, {}).get(championship)

    with patch(
        "services.results_post_service.recompute_standings_from_round", new=AsyncMock()
    ) as recompute, patch(
        "services.results_post_service._get_standings_message_id", new=_message_id
    ), patch(
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
        await repost_subsequent_standings(
            db_path, DIVISION_ID, from_round, guild or _guild(), bot=MagicMock()
        )
    return recompute, clear, post


def _both(message_id: int = 900) -> dict:
    return {STANDINGS_DRIVERS: message_id, STANDINGS_CONSTRUCTORS: message_id + 1}


def _reposted_rounds(post) -> list[int]:
    return [call.args[2] for call in post.await_args_list]


# ---------------------------------------------------------------------------
# The recompute
# ---------------------------------------------------------------------------


async def test_the_snapshots_are_recomputed_first(tmp_path):
    """The database snapshots are the league's record of the championship; the Discord
    messages are only a view of them."""
    db_path = await _make_db(tmp_path)

    recompute, _, _ = await _run(db_path, from_round=1)

    recompute.assert_awaited_once()
    assert recompute.await_args.args[1:3] == (DIVISION_ID, 1)


async def test_the_recompute_happens_even_where_nothing_was_posted(tmp_path):
    """A round nobody posted standings for still has to be recomputed, or the round after
    it would be built on a total that was never corrected."""
    db_path = await _make_db(tmp_path, name="repost_unposted")

    recompute, _, post = await _run(db_path, from_round=1, posted={})

    recompute.assert_awaited_once()
    assert _reposted_rounds(post) == []


# ---------------------------------------------------------------------------
# Which rounds are reposted
# ---------------------------------------------------------------------------


async def test_a_posted_later_round_is_reposted(tmp_path):
    db_path = await _make_db(tmp_path, name="repost_one")

    _, _, post = await _run(db_path, from_round=1, posted={2: _both()})

    assert _reposted_rounds(post) == [2]


async def test_every_posted_later_round_is_reposted(tmp_path):
    """A championship is cumulative, so a penalty on round 1 changes rounds 2 and 3 alike."""
    db_path = await _make_db(tmp_path, name="repost_all")

    _, _, post = await _run(db_path, from_round=1, posted={2: _both(), 3: _both(910)})

    assert _reposted_rounds(post) == [2, 3]


async def test_the_reposts_go_in_round_order(tmp_path):
    """They are read in order, and a championship posted out of order reads as though the
    season ran that way."""
    db_path = await _make_db(tmp_path, name="repost_order")

    _, _, post = await _run(db_path, from_round=1, posted={3: _both(), 2: _both(910)})

    assert _reposted_rounds(post) == [2, 3]


async def test_the_corrected_round_itself_is_not_reposted(tmp_path):
    """`delete_and_repost_final_results` has already posted it; doing it again here would
    put two copies of the round a league is actually looking at in the channel."""
    db_path = await _make_db(tmp_path, name="repost_not_self")

    _, _, post = await _run(db_path, from_round=2, posted={1: _both(), 2: _both(910), 3: _both(920)})

    assert _reposted_rounds(post) == [3]


async def test_an_earlier_round_is_not_reposted(tmp_path):
    """A penalty cannot change a championship that was already settled before the round it
    applies to."""
    db_path = await _make_db(tmp_path, name="repost_not_earlier")

    _, _, post = await _run(db_path, from_round=3, posted={1: _both(), 2: _both(910)})

    assert _reposted_rounds(post) == []


async def test_a_cancelled_round_is_skipped(tmp_path):
    """It scores nothing, so its standings are the round before it — reposting them would
    tell a division its championship changed when it did not."""
    db_path = await _make_db(
        tmp_path,
        name="repost_cancelled",
        rounds=((1, "FINAL"), (2, "CANCELLED"), (3, "FINAL")),
    )

    _, _, post = await _run(db_path, from_round=1, posted={2: _both(), 3: _both(910)})

    assert _reposted_rounds(post) == [3]


async def test_an_unposted_round_is_skipped(tmp_path):
    """Reposting one whose standings were never published would announce a championship
    position out of nowhere, in the middle of a correction nobody asked to see."""
    db_path = await _make_db(tmp_path, name="repost_skip_unposted")

    _, _, post = await _run(db_path, from_round=1, posted={3: _both()})

    assert _reposted_rounds(post) == [3]


@pytest.mark.parametrize(
    "championship", [STANDINGS_DRIVERS, STANDINGS_CONSTRUCTORS]
)
async def test_a_round_with_only_one_championship_posted_is_reposted(tmp_path, championship):
    """"Posted" is either championship, not the drivers' alone. The image flow can leave the
    two in different states, and checking only one column is how a round whose other
    graphic stands would never be recomputed."""
    db_path = await _make_db(tmp_path, name=f"repost_{championship}")

    _, _, post = await _run(db_path, from_round=1, posted={2: {championship: 900}})

    assert _reposted_rounds(post) == [2]


async def test_a_division_with_no_standings_channel_reposts_nothing(tmp_path):
    """There is nowhere to post, and the recompute has already put the database right."""
    db_path = await _make_db(tmp_path, name="repost_nochannel", standings_channel=None)

    recompute, _, post = await _run(db_path, from_round=1, posted={2: _both()})

    recompute.assert_awaited_once()
    assert _reposted_rounds(post) == []


async def test_a_deleted_channel_is_skipped_rather_than_raising(tmp_path):
    """The recompute has already happened, so the database is correct either way. Refusing
    to continue would leave the rest of the season unreposted for one deleted channel."""
    db_path = await _make_db(tmp_path, name="repost_deleted_channel")

    _, _, post = await _run(
        db_path, from_round=1, guild=_guild(channel_missing=True), posted={2: _both()}
    )

    assert _reposted_rounds(post) == []


# ---------------------------------------------------------------------------
# What a repost does
# ---------------------------------------------------------------------------


async def test_the_old_messages_are_cleared_before_the_new_ones_are_posted(tmp_path):
    """Both championships' messages go, and their ids are forgotten — otherwise the channel
    accumulates a copy of the standings for every penalty the season sees."""
    db_path = await _make_db(tmp_path, name="repost_clear")
    order: list[str] = []

    async def _message_id(_db, _div, round_id, championship):
        return 900 if round_id == 2 else None

    with patch(
        "services.results_post_service.recompute_standings_from_round", new=AsyncMock()
    ), patch(
        "services.results_post_service._get_standings_message_id", new=_message_id
    ), patch(
        "services.results_post_service._clear_standings_messages",
        new=AsyncMock(side_effect=lambda *a, **k: order.append("clear")),
    ), patch(
        "services.results_post_service.driver_standings_for_display",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.standings_service.compute_team_standings",
        new=AsyncMock(return_value=[]),
    ), patch(
        "services.results_post_service.post_standings",
        new=AsyncMock(side_effect=lambda *a, **k: order.append("post")),
    ):
        await repost_subsequent_standings(
            db_path, DIVISION_ID, 1, _guild(), bot=MagicMock()
        )

    assert order == ["clear", "post"]


async def test_the_repost_carries_the_rounds_own_number_and_track(tmp_path):
    """Every reposted round is titled with its own, and a cascade that carried the
    corrected round's would title the whole season after one race."""
    db_path = await _make_db(tmp_path, name="repost_titles")

    _, _, post = await _run(db_path, from_round=1, posted={3: _both()})

    args = post.await_args.args
    assert args[2] == 3  # round id
    assert args[3] == 3  # round number
    assert args[4] == "Track 3"


async def test_the_repost_carries_the_divisions_reserve_setting(tmp_path):
    """A league that leaves reserves out of its championship must not have them reappear
    the moment a penalty triggers a repost."""
    db_path = await _make_db(tmp_path, name="repost_reserves")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET reserves_in_standings = 0 WHERE division_id = ?",
            (DIVISION_ID,),
        )
        await db.commit()

    _, _, post = await _run(db_path, from_round=1, posted={2: _both()})

    assert post.await_args.args[9] is False


async def test_reserves_are_shown_where_the_division_has_not_said_otherwise(tmp_path):
    """A division that never touched the setting shows reserves. The default lives in the
    schema — the column is NOT NULL DEFAULT 1 — and the `is not None` fallback in the
    repost is for the LEFT JOIN finding no configuration row at all, which is the same
    answer reached a different way."""
    db_path = await _make_db(tmp_path, name="repost_reserves_default")
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM division_results_config WHERE division_id = ?", (DIVISION_ID,)
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, standings_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, STANDINGS_CHANNEL),
        )
        await db.commit()

    _, _, post = await _run(db_path, from_round=1, posted={2: _both()})

    assert post.await_args.args[9] is True
