"""A check-in call whose graphic falls back is told as a line of its job (architecture.md, "Errors
and failures", the sixth place: the drawing of a graphic riding on a post).

The league is `ongoing_league`'s with attendance and images on: Pro's round 4 is three days off,
past the moment its call falls due and before its deadline, with no call posted for it yet. The
call is asked of the queue as its timer asks it, and the queue is run. Where the graphic cannot be
drawn, or is drawn without the deadline, the call still posts and is recorded, and the log channel
is told what was left out, naming the division and the round; the full error stays in the host's
log.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from tests.support.change_queue import run_queue, stopped_job
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    SeasonLeague,
    attendance_from_the_database,
    ongoing_league,
    round_id,
)

CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_4 = round_id(PRO, 4)
#: The clause the log channel is given for a graphic that could not be drawn at all.
NOT_DRAWN = "the graphic could not be drawn, so the call is posted without it: template unreadable"
#: The clause the log channel is given for a graphic drawn without its deadline.
NO_DEADLINE = "the deadline could not be read, so the check-in graphic is drawn without it"


async def _league(tmp_path: Any) -> SeasonLeague:
    league = await ongoing_league(tmp_path, attendance=True, images=True)
    attendance_from_the_database(league)
    when = league.clock.now + timedelta(days=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.astimezone(timezone.utc).isoformat(), ROUND_4)
    return league


async def _ask_and_run(league: SeasonLeague) -> None:
    from leaguebot.attendance.services.check_in.check_in_call import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(league.bot.change_queue, league.db_path, ROUND_4,
                            origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


def _sent(league: SeasonLeague) -> list[int]:
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]


@pytest.mark.parametrize("fault", ["not drawn", "no deadline", "none"])
async def test_a_call_whose_graphic_cannot_be_drawn_is_told_as_a_line_of_its_job(
    tmp_path, monkeypatch, fault
):
    """(not drawn) the drawing raises: the call posts without a picture and the log channel is
    told so. (no deadline) the configuration read for the deadline raises: the graphic is drawn
    without it, the call posts with it, and the log channel is told so. (none) the graphic is drawn
    whole: no such line is written."""
    from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
    from leaguebot.image.services import image_rsvp_post

    league = await _league(tmp_path)
    picture = tmp_path / "checkin.png"

    async def _attach(*_args: Any, **_kwargs: Any) -> discord.File:
        if fault == "not drawn":
            raise RuntimeError("template unreadable")
        picture.write_bytes(b"png")
        return discord.File(str(picture), filename="checkin.png")

    monkeypatch.setattr(image_rsvp_post, "try_attach", AsyncMock(side_effect=_attach))
    if fault == "no deadline":
        monkeypatch.setattr(AttendanceService, "get_config",
                            AsyncMock(side_effect=RuntimeError("config unreadable")))

    await _ask_and_run(league)

    assert await stopped_job(league.db_path) is None
    assert len(_sent(league)) == 1
    assert (league.channel(CHECK_IN).files[-1] is None) == (fault == "not drawn")
    [record] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_4)
    assert record["message_id"] == str(_sent(league)[0])
    lines = [line for line in league.log().splitlines() if "check-in call of round 4:" in line]
    expected = {"not drawn": [NOT_DRAWN], "no deadline": [NO_DEADLINE], "none": []}[fault]
    # Each line names the division and the round.
    heading = f"**{DIVISIONS[PRO][0]}** — check-in call of round 4: "
    assert lines == [heading + clause for clause in expected]
