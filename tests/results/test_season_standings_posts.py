"""Results' opening and final standings, posted when a season is approved and when it
completes (2026-09-08).

The opening and final classifications bracket a season: the standings a league already reads
after every round, under the occasion's heading and with no message text (see
``leaguebot.core.models.classification_occasion.ClassificationOccasion``). Results posts its
own, one division at a time, each a job of the season's approval or of its completion on the
change queue, signed up to the hooks core declares for placements confirmed and for a season
ending (architecture.md, "How modules and core fit together"). Each posting reads the division's
standings channel and its round as it runs, the first round for the opening and the last round
with results for the final, and raises where it cannot post, for the queue to stop on. The
standings channel is read from `division_results_config`, where `/division results-channel`
keeps it.

What matters here is which standings are posted for which division, against which round, and
that a posting raises where it cannot be made; what the standings *say* is covered by
``test_image_standings_service``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from tests.support.change_queue import in_a_job
from tests.support.season_classification import (
    bot_on,
    classification_posts,
    first_round_id,
    guild_double,
    second_round_with_results,
    seed_season,
)
from tests.support.season_league import AM, DIVISIONS, PRO, season_league


_NOT_RESULTS_FINAL_YET = "#283: the final standings are not yet posted by results' own module"


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "classification.db")
    await run_migrations(path)
    return path


async def test_the_opening_standings_are_posted_for_every_division(db_path):
    """Div A and Div B, one round and one seated driver each: each division's opening standings
    are posted once, under the opening's heading."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path, divisions=("Div A", "Div B"))
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    with first, second, third:
        for division_id in division_ids:
            await service.post_opening_standings(
                bot_on(db_path), guild_double(), db_path, division_id, as_text=False
            )

    assert standings.await_count == 2
    assert all(
        call.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
        for call in standings.await_args_list
    )


async def test_the_opening_standings_are_drawn_against_the_division_s_first_round(db_path):
    """Not because they stand after it — the grid of rounds is read from the calendar."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00')",
            (division_ids[0],),
        )
        await db.commit()
    opener = await first_round_id(db_path, division_ids[0])
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    with first, second, third:
        await service.post_opening_standings(
            bot_on(db_path), guild_double(), db_path, division_ids[0], as_text=False
        )

    assert standings.await_args.args[2] == opener


async def test_a_division_with_no_rounds_posts_no_opening_standings(db_path):
    """Div A's only round deleted: its opening standings post nothing."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rounds WHERE division_id = ?", (division_ids[0],))
        await db.commit()
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    with first, second, third:
        await service.post_opening_standings(
            bot_on(db_path), guild_double(), db_path, division_ids[0], as_text=False
        )

    assert standings.await_count == 0


async def test_the_opening_standings_post_reads_the_division_s_standings_channel(db_path):
    """Div A's standings channel (900) is set with `/division results-channel`, in the results
    module's own settings: the opening classification is posted there, both championships."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    guild = guild_double()
    standings_channel = MagicMock()
    guild.get_channel.side_effect = lambda channel_id: (
        standings_channel if channel_id == 900 else None
    )
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    with first, second, third:
        await service.post_opening_standings(
            bot_on(db_path), guild, db_path, division_ids[0], as_text=False
        )

    standings.assert_awaited_once()
    args = standings.await_args.args
    assert args[5] is standings_channel
    assert args[6], "the drivers' championship"
    assert args[7] is not None, "the constructors' championship"
    assert standings.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
    assert args[10] == "", "the opening carries no lifecycle label"


async def test_an_opening_standings_channel_set_and_gone_raises(db_path):
    """Div A's standings channel (900) has been deleted from the server: the job raises for
    the queue to stop on, rather than passing over the division in silence."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    guild = guild_double()
    guild.get_channel.return_value = None
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    with first, second, third, pytest.raises(StepFailedOnDiscord):
        await service.post_opening_standings(
            bot_on(db_path), guild, db_path, division_ids[0], as_text=False
        )

    standings.assert_not_awaited()


async def test_a_division_with_no_standings_channel_set_posts_no_opening_standings(
    tmp_path, monkeypatch,
):
    """Am's standings channel never set: its opening standings job posts nothing and raises
    nothing, while Pro's posts."""
    from leaguebot.results.services import season_standings_posts

    league = await season_league(tmp_path, monkeypatch=monkeypatch, results=True)
    await league.write(
        "UPDATE division_results_config SET standings_channel_id = NULL WHERE division_id = ?", AM,
    )

    for division in (PRO, AM):
        with in_a_job():
            await season_standings_posts.post_opening_standings(
                league.bot, league.guild, league.db_path, division, as_text=False,
            )

    assert league.texts(DIVISIONS[PRO][3].standings)
    assert league.texts(DIVISIONS[AM][3].standings) == []


# ── The final standings ────────────────────────────────────────────────────
#
# Each division's final standings are a job of the season's completion on the change queue,
# raising where they cannot post, as the opening's are. They are posted beside the last round's
# (results spec :561, image spec :1025: "Neither posting replaces a standings message nor has its
# ID recorded").


async def _recorded_standings_ids(path, round_id: int) -> list:
    async with get_connection(path) as db:
        rows = await (
            await db.execute(
                "SELECT standings_message_id FROM driver_standings_snapshots "
                "WHERE round_id = ? ORDER BY id",
                (round_id,),
            )
        ).fetchall()
    return [row["standings_message_id"] for row in rows]


def _lewis(round_id: int, division_id: int):
    from leaguebot.results.models.standings_snapshot import DriverStandingsSnapshot

    return DriverStandingsSnapshot(
        id=0, round_id=round_id, division_id=division_id, driver_user_id=500,
        standing_position=1, total_points=25, finish_counts={}, first_finish_rounds={},
    )


def _final_inputs(round_id: int, division_id: int):
    """The drivers and teams of the round, as the final standings read them."""
    return (
        patch(
            "leaguebot.results.services.results_post_service.driver_standings_for_display",
            AsyncMock(return_value=[_lewis(round_id, division_id)]),
        ),
        patch(
            "leaguebot.results.services.standings_service.compute_team_standings",
            AsyncMock(return_value=[]),
        ),
    )


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_the_final_standings_are_drawn_against_the_last_round_with_results(db_path):
    """The final standings *are* that round's classification, restated under their own heading.
    Div A's round 2 is its last round with results, as the completion's first save finds it: its
    final standings are drawn against round 2, as the final classification."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    second = await second_round_with_results(db_path, division_ids[0])

    standings = AsyncMock(return_value=[])
    first, middle, last = classification_posts(standings)
    inputs = _final_inputs(second, division_ids[0])
    with first, middle, last, inputs[0], inputs[1]:
        await service.post_final_standings(
            bot_on(db_path), guild_double(), db_path, division_ids[0], second, as_text=False
        )

    assert standings.await_args.args[2] == second
    assert standings.await_args.args[3] == 2
    assert standings.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_the_final_standings_stand_beside_the_last_round_s_and_replace_nothing(db_path):
    """Div A's round 2 has its standings posted as message 9100, recorded against it. The final
    standings are posted as text: a new message is sent beside it, and 9100 is neither edited
    nor deleted, its recorded id unchanged."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    division_id = division_ids[0]
    round_id = await second_round_with_results(db_path, division_id, message_id=9100)
    old_message = MagicMock()
    old_message.edit = AsyncMock()
    old_message.delete = AsyncMock()
    channel = MagicMock()
    channel.send = AsyncMock(return_value=MagicMock(id=9200))
    channel.fetch_message = AsyncMock(return_value=old_message)
    channel.delete_messages = AsyncMock()
    guild = guild_double()
    guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 900 else None

    first, second = _final_inputs(round_id, division_id)
    with first, second:
        with in_a_job():
            await service.post_final_standings(
                bot_on(db_path), guild, db_path, division_id, round_id, as_text=True
            )

    channel.send.assert_awaited()
    old_message.edit.assert_not_awaited()
    old_message.delete.assert_not_awaited()
    channel.delete_messages.assert_not_awaited()
    assert await _recorded_standings_ids(db_path, round_id) == [9100]


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_final_standings_whose_channel_was_deleted_raise(db_path):
    """Div A's standings channel (900) has been deleted from the server: the job raises for the
    queue to stop on."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    round_id = await second_round_with_results(db_path, division_ids[0])
    guild = guild_double()
    guild.get_channel.return_value = None
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    inputs = _final_inputs(round_id, division_ids[0])
    with first, second, third, inputs[0], inputs[1], pytest.raises(StepFailedOnDiscord):
        await service.post_final_standings(
            bot_on(db_path), guild, db_path, division_ids[0], round_id, as_text=False
        )

    standings.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_the_final_standings_record_no_message_id(db_path):
    """The final standings of Div A, drawn against its round 2, go out as message 9200 through
    results' posting that records nothing, asked to post afresh as the final classification:
    no id is recorded against round 2."""
    from leaguebot.results.services import season_standings_posts as service
    from leaguebot.results.services.results_post_service import PostedTable

    _season_id, division_ids = await seed_season(db_path)
    division_id = division_ids[0]
    round_id = await second_round_with_results(db_path, division_id)
    produce = AsyncMock(return_value=[PostedTable([9200], [])])
    recording = AsyncMock()

    inputs = _final_inputs(round_id, division_id)
    with inputs[0], inputs[1], patch(
        "leaguebot.results.services.results_post_service.produce_standings", produce
    ), patch(
        "leaguebot.results.services.results_post_service.post_standings", recording, create=True,
    ):
        await service.post_final_standings(
            bot_on(db_path), guild_double(), db_path, division_id, round_id, as_text=False
        )

    produce.assert_awaited_once()
    assert produce.await_args.args[2] == round_id
    assert produce.await_args.kwargs["fresh"] is True
    assert produce.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_FINAL
    recording.assert_not_awaited()
    assert await _recorded_standings_ids(db_path, round_id) == [None]


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_a_part_posted_final_standings_is_kept_and_removed_by_the_next_try(db_path):
    """Div A's final standings are posted in part: message 9201 went out and the rest was
    refused. The job raises with 9201 to keep; the next try, handed it, removes 9201 from the
    standings channel before it posts both again."""
    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    division_id = division_ids[0]
    round_id = await second_round_with_results(db_path, division_id)
    channel = MagicMock()
    guild = guild_double()
    guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 900 else None
    refusal = RuntimeError("the constructors' table was refused")
    setattr(refusal, "left_standing", [9201])
    produce = AsyncMock(side_effect=[refusal, []])
    remove = AsyncMock(return_value=([], []))

    inputs = _final_inputs(round_id, division_id)
    with inputs[0], inputs[1], patch(
        "leaguebot.results.services.results_post_service.produce_standings", produce
    ), patch(
        "leaguebot.results.services.results_post_service.remove_part_posted_messages", remove
    ):
        with pytest.raises(StepFailedOnDiscord) as failed:
            await service.post_final_standings(
                bot_on(db_path), guild, db_path, division_id, round_id, as_text=False
            )
        assert failed.value.result == {"new": [9201], "channel_id": 900}
        remove.assert_not_awaited()

        await service.post_final_standings(
            bot_on(db_path), guild, db_path, division_id, round_id,
            as_text=True, kept=failed.value.result,
        )

    remove.assert_awaited_once_with(channel, [9201])
    assert produce.await_count == 2


@pytest.mark.xfail(strict=True, reason=_NOT_RESULTS_FINAL_YET)
async def test_a_final_standings_channel_never_set_posts_nothing(db_path):
    """Div A was never given a standings channel: its final standings post nothing and raise
    nothing."""
    from leaguebot.results.services import season_standings_posts as service

    _season_id, division_ids = await seed_season(db_path)
    division_id = division_ids[0]
    round_id = await second_round_with_results(db_path, division_id)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET standings_channel_id = NULL "
            "WHERE division_id = ?",
            (division_id,),
        )
        await db.commit()
    standings = AsyncMock(return_value=[])

    first, second, third = classification_posts(standings)
    inputs = _final_inputs(round_id, division_id)
    with first, second, third, inputs[0], inputs[1]:
        await service.post_final_standings(
            bot_on(db_path), guild_double(), db_path, division_id, round_id, as_text=False
        )

    standings.assert_not_awaited()
