"""Results' opening standings, posted when a season is approved (2026-09-08).

The opening classification brackets a season's start: the standings a league already reads
after every round, under the opening's heading and with no message text (see
``leaguebot.core.models.classification_occasion.ClassificationOccasion``). Results posts its
own, one division at a time, each a job of the season's approval on the change queue, signed up
to the hook core declares for placements confirmed (architecture.md, "How modules and core fit
together"). Each posting reads the division's standings channel and its first round as it runs,
and raises where it cannot post, for the queue to stop on. The standings channel is read from
`division_results_config`, where `/division results-channel` keeps it.

What matters here is which standings are posted for which division, against which round, and
that a posting raises where it cannot be made; what the standings *say* is covered by
``test_image_standings_service``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from tests.support.change_queue import in_a_job
from tests.support.season_classification import (
    bot_on,
    classification_posts,
    first_round_id,
    guild_double,
    seed_season,
)
from tests.support.season_league import AM, DIVISIONS, PRO, season_league


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
