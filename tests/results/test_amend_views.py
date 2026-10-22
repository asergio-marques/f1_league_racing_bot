"""The Cancel Amendment button of an amendment's channel, pressed in each state the amendment can
be in (#439, slice 6b, C62).

The view holds no state of the amendment's: a press checks who is pressing, then asks the change
queue for `results.amend.cancel` as the member who pressed, acknowledged privately as under way,
and the change's check refuses what cannot be cancelled. The league is `review_league`'s: round 3
of Pro, FINAL; its amendment is recorded in channel 705 by Alex (4242).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.services.amend_views import AmendCancelView
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    member_interaction,
    tier_member,
    updated_reply,
)
from tests.support.review_league import AMEND_CHANNEL, DIVISION_ID, ROUND_ID, review_league

ALEX = 4242
CANCEL = "results.amend.cancel"


async def _view(tmp_path: Any, *, row: str | None = "open") -> tuple[Any, AmendCancelView]:
    """The league and the view of its amendment: *row* "open" (stage one not yet written),
    "closed" (it has ended) or None (no row at all)."""
    league = await review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        if row is not None:
            await db.execute(
                "INSERT INTO round_amend_channels (id, round_id, channel_id, session_types, "
                "created_at, started_by, closed_at) VALUES (1, ?, ?, ?, '2026-10-05T12:00:00', ?, ?)",
                (ROUND_ID, AMEND_CHANNEL, json.dumps(["FEATURE_RACE"]), ALEX,
                 "2026-10-05T12:30:00+00:00" if row == "closed" else None),
            )
        await db.commit()
    view = AmendCancelView(
        amend_id=1,
        opener_id=ALEX,
        queue=league.bot.change_queue,
        db_path=league.db_path,
        server_cfg=await league.bot.config_service.get_server_config(),
        what="`/results rounds amend` of round 3 (Pro)",
    )
    return league, view


async def _press(league: Any, view: AmendCancelView, user: Any) -> Any:
    press = member_interaction(league.bot, user=user)
    await view.cancel_btn.callback(press)
    return press


async def _asked(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == CANCEL]


@pytest.mark.parametrize(
    "presser",
    [
        pytest.param(tier_member(None, member_id=ALEX), id="the opener"),
        pytest.param(tier_member("manager", member_id=99), id="a league manager"),
        pytest.param(tier_member("admin", member_id=98), id="a league admin"),
    ],
)
async def test_the_cancel_press_by_state_asks_the_queue_as_the_presser(tmp_path, presser):
    """Alex's amendment stands open. The member who opened it, a league manager and a league admin
    each press Cancel Amendment: the press is answered as under way, and `results.amend.cancel` is
    asked, as the presser, for the amendment, its round, division and channel."""
    league, view = await _view(tmp_path)

    press = await _press(league, view, presser)

    assert acknowledgement(press).startswith("⏳")
    [asked] = await _asked(league)
    assert asked["actor_id"] == presser.id
    assert json.loads(asked["payload"]) == {
        "amend_id": 1, "round_id": ROUND_ID, "division_id": DIVISION_ID,
        "channel_id": AMEND_CHANNEL,
    }


async def test_the_cancel_press_by_state_refuses_a_member_who_may_not(tmp_path):
    """A member who neither opened the amendment nor holds a manager tier presses Cancel
    Amendment: they are told only league managers can cancel, and nothing is asked."""
    league, view = await _view(tmp_path)

    press = await _press(league, view, tier_member(None, member_id=77))

    assert "Only league managers can cancel" in acknowledgement(press)
    assert await _asked(league) == []


@pytest.mark.parametrize("row", [pytest.param("closed", id="ended"), pytest.param(None, id="gone")])
async def test_the_cancel_press_by_state_is_too_late_for_an_amendment_that_ended(tmp_path, row):
    """The amendment has ended (its row closed) or is gone. Alex presses Cancel Amendment: he is
    told it is too late and nothing is asked."""
    league, view = await _view(tmp_path, row=row)

    press = await _press(league, view, tier_member(None, member_id=ALEX))

    assert "Too late" in "\n".join([acknowledgement(press), updated_reply(press)])
    assert await _asked(league) == []


async def test_the_cancel_press_by_state_is_refused_while_stage_one_is_being_recorded(tmp_path):
    """Stage one of the amendment is on the change queue, waiting its turn. Alex presses Cancel
    Amendment: he is told a stage is being approved and nothing is asked."""
    league, view = await _view(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, actor_id, what) "
            "VALUES ('results.amend.stage_one', 'results.amend.stage_one:1', ?, 'MEMBER', "
            "'QUEUED', ?, 'Recording round 3''s amended results')",
            (json.dumps({"amend_id": 1, "round_id": ROUND_ID, "division_id": DIVISION_ID,
                         "channel_id": AMEND_CHANNEL}), ALEX),
        )
        await db.commit()

    press = await _press(league, view, tier_member(None, member_id=ALEX))

    assert "being approved" in "\n".join([acknowledgement(press), updated_reply(press)])
    assert await _asked(league) == []
