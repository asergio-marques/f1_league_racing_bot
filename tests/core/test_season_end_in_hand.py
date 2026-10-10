"""Which end of a season is in hand on the change queue (#439, slice 5, answer B).

While a season's completion, cancellation or abort is waiting, being carried out or stopped,
every request about that season is refused at once, naming the job. Each request reads it through
one helper, `season_end_changes.season_end_in_hand`, which gives the kind of the end in hand and
the number of the job it waits on, the one nearest its turn first; 0 where only its close is left;
and nothing where it has finished, been refused, dropped or discarded, or is another season's.

Each season's end is put on the queue straight through its tables (`seed_season_end`), so the
helper is read alone, on a database built by the migrations. The helper is unbuilt until the
build, so the test imports it inside itself and is marked to fail until then.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.season_league import (
    SEASON_ABORT_KIND,
    SEASON_CANCEL_KIND,
    SEASON_COMPLETE_KIND,
    SEASON_END_KINDS,
    SEASON_ID,
    seed_season_end,
)


def _other(kind: str) -> str:
    """Another kind of a season's end than *kind*."""
    return next(each for each in SEASON_END_KINDS if each != kind)


async def _waiting(path: str, kind: str) -> Any:
    return kind, await seed_season_end(path, kind)


async def _stopped(path: str, kind: str) -> Any:
    return kind, await seed_season_end(path, kind, stopped=True)


async def _running(path: str, kind: str) -> Any:
    return kind, await seed_season_end(path, kind, state="RUNNING")


async def _only_its_close_left(path: str, kind: str) -> Any:
    await seed_season_end(path, kind, first_done=True)
    return kind, 0


async def _ahead_of_another_end(path: str, kind: str) -> Any:
    job = await seed_season_end(path, kind, stopped=True)
    await seed_season_end(path, _other(kind))
    return kind, job


async def _behind_one_ended(path: str, kind: str) -> Any:
    await seed_season_end(path, _other(kind), state="DISCARDED")
    return kind, await seed_season_end(path, kind)


def _ended(state: str) -> Callable[[str, str], Awaitable[Any]]:
    async def seed(path: str, kind: str) -> Any:
        await seed_season_end(path, kind, state=state, first_done=state == "DONE")
        return None
    return seed


async def _another_season_s(path: str, kind: str) -> Any:
    await seed_season_end(path, kind, season_id=SEASON_ID + 1)
    return None


#: Each way the season's end stands on the queue: what it seeds, giving what the helper should
#: give for season 7 (its kind and job, its kind and 0, or None).
_CASES = {
    "waiting": _waiting,
    "stopped": _stopped,
    "running": _running,
    "only its close left": _only_its_close_left,
    "ahead of another end of the season": _ahead_of_another_end,
    "behind another end discarded": _behind_one_ended,
    "done": _ended("DONE"),
    "refused": _ended("REFUSED"),
    "dropped": _ended("DROPPED"),
    "discarded": _ended("DISCARDED"),
    "another season's": _another_season_s,
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("kind", [SEASON_COMPLETE_KIND, SEASON_CANCEL_KIND, SEASON_ABORT_KIND])
async def test_season_end_in_hand_names_the_job_nearest_its_turn_and_leaves_out_a_finished_or_discarded_one(
    tmp_path, kind, case,
):
    """Season 7's completion, cancellation or abort stands on the queue as *case* says: waiting
    for its turn, stopped at its first job, running, with only its close left, ahead of another
    end of the season or behind one discarded; or finished, refused, dropped or discarded; or the
    end is another season's. Asked which end of season 7 is in hand, the helper gives its kind and
    first job not done, 0 for the job where only the close is left, the earlier where two are in
    hand, and nothing where none is."""
    from leaguebot.core.services.season_end_changes import season_end_in_hand

    path = str(tmp_path / "league.db")
    await run_migrations(path)
    expected = await _CASES[case](path, kind)

    assert await season_end_in_hand(path, SEASON_ID) == expected
