"""A lineup asked of the change queue (#439, slice 6): a command that changes the drivers makes
its own change, then asks the queue, as the bot's and repeatably, to post each lineup it touched
(`lineup.post`); its reply is unchanged and the post follows on the queue. `/driver move`,
`release` and `sack` ask no lineup: each is a change on the queue that posts its lineups as steps
of its own (#283), pinned in `test_driver_move.py`, `test_driver_release.py` and
`test_sack_driver.py`. `/driver assign` and `unassign` ask none either: each is a change on the
queue whose placement is never confirmed, so no lineup is owed (`test_driver_assign.py`,
`test_driver_unassign.py`).

The league is `ongoing_league`'s: season 3 being raced, Pro (21) with its lineup channel 600 and Am
(22) with 700, images off.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.models.change import ChangeOrigin
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    queued_log_lines,
    register,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.season_league import (
    AM,
    DIVISIONS,
    PRO,
    ongoing_league,
    run_division_channel_command,
)


KIND = "lineup.post"
LINEUP = {PRO: DIVISIONS[PRO][3].lineup, AM: DIVISIONS[AM][3].lineup}


async def _said(league: Any) -> str:
    """Every log line, sent or still waiting on the old retry queue."""
    return "\n".join([league.log(), *await queued_log_lines(league.db_path)])


async def _ask(league: Any, *divisions: int) -> None:
    from leaguebot.core.services.lineup_posting import ask_lineup_posts

    await ask_lineup_posts(league.bot.change_queue, league.db_path, list(divisions))


def _refusing(league: Any, channel_id: int) -> Any:
    """Make *channel_id* refuse every send; gives its own send, to put back."""
    channel = league.channel(channel_id)
    own = channel.send
    channel.send = AsyncMock(side_effect=http_error(discord.Forbidden, status=403,
                                                    text="Missing Access"))
    return own


async def _stop_the_queue(league: Any) -> None:
    """A change of the bot's, asked before anything else, whose job Discord refuses: the queue
    stops at it, and whatever is asked after it waits."""
    from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord, StepKind, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

    async def _refused(_ctx: Any) -> Any:
        raise StepFailedOnDiscord("Missing Access")

    async def _go(_ctx: Any) -> Any:
        return Verdict.go()

    register(league.bot, ChangeType(
        kind="blocker", opening=(PlannedStep("block", {}),),
        steps={"block": Step("block", StepKind.ACT, _refused)}, check=_go,
        key=lambda _payload: "blocker", doing=lambda _payload: "Blocking",
        outcome=lambda _ctx: "",
    ))
    await league.bot.change_queue.ask("blocker", {}, what="the blocker", origin=ChangeOrigin.BOT)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "block"


# ── What the queue does with the ask ───────────────────────────────────────────────


async def test_a_lineup_a_command_asked_for_that_discord_refuses_stops_the_queue(tmp_path):
    league = await ongoing_league(tmp_path)
    own = _refusing(league, LINEUP[PRO])
    before = len(league.texts(LINEUP[PRO]))

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert "The queue is stopped at job #" in await _said(league)

    league.channel(LINEUP[PRO]).send = own
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(LINEUP[PRO])) == before + 1


async def test_two_commands_in_a_row_each_ask_for_the_lineup(tmp_path):
    league = await ongoing_league(tmp_path)
    await _stop_the_queue(league)

    await _ask(league, PRO)
    await _ask(league, PRO)

    rows = [row for row in await change_rows(league.db_path) if row["kind"] == KIND]
    assert [row["state"] for row in rows] == ["QUEUED", "QUEUED"]


async def test_a_stopped_lineup_lands_once_the_lineup_channel_is_set_and_retried(tmp_path):
    """Set the channel, then Retry: the remedy the stop notice leads a manager to, end to end.

    Pro's lineup channel is unset, and Pro's lineup stops the queue at its post, its notice
    naming `/division lineup-channel`. Alex runs `/division lineup-channel` for Pro with channel
    600 and is acknowledged; the queue runs. The setting does not wait behind the stopped
    lineup: it runs past it, saves 600 as Pro's lineup channel and tells Alex the channel was
    set, while the lineup still stops the league's lane. Alex presses Retry: the lineup lands
    once in 600, and nothing stops the queue."""
    from leaguebot.core.cogs.division_cog import DivisionCog

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET lineup_channel_id = NULL WHERE id = ?", PRO)
    before = len(league.texts(LINEUP[PRO]))
    await _ask(league, PRO)
    await run_queue(league.bot)
    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert "`/division lineup-channel`" in await _said(league)

    manager = await run_division_channel_command(
        league, DivisionCog, DivisionCog.division_lineup_channel, "division lineup-channel",
        LINEUP[PRO],
    )
    assert acknowledgement(manager).startswith("⏳ ")
    await run_queue(league.bot)

    assert updated_reply(manager) == f"✅ Lineup channel for **Pro** set to <#{LINEUP[PRO]}>."
    [row] = await league.rows("SELECT lineup_channel_id FROM divisions WHERE id = ?", PRO)
    assert int(row["lineup_channel_id"]) == LINEUP[PRO]
    assert (await stopped_job(league.db_path))["name"] == "post_lineup", (
        "setting the channel ran the job"
    )
    await retry_job(league.bot)

    assert len(league.texts(LINEUP[PRO])) == before + 1
    assert await stopped_job(league.db_path) is None


async def test_a_lineup_for_a_division_with_no_lineup_channel_stops_the_queue_naming_the_command(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET lineup_channel_id = NULL WHERE id = ?", PRO)
    before = len(league.texts(LINEUP[PRO]))

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert ("**Pro** has no lineup channel set: set one with `/division lineup-channel`, "
            "then press Retry.") in await _said(league)

    await league.write("UPDATE divisions SET lineup_channel_id = ? WHERE id = ?",
                       LINEUP[PRO], PRO)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(LINEUP[PRO])) == before + 1

    # A division no longer of the season by the time its lineup is asked for: dropped.
    await league.bot.change_queue.ask(KIND, {"division_id": 999, "division_name": "Gone"},
                                      what="posting the lineup of **Gone**",
                                      origin=ChangeOrigin.BOT)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    gone = [row["state"] for row in await change_rows(league.db_path)
            if row["kind"] == KIND and json.loads(row["payload"])["division_id"] == 999]
    assert gone in ([], ["DROPPED"])


# ── The post and the deletion of the lineup it replaces, as two jobs ─────────────────

#: Pro's lineup standing in its lineup channel before the ask.
OLD_LINEUP = 6100
#: The link Discord gives Pro's old lineup, which a discarded deletion names.
OLD_LINEUP_LINK = f"https://discord.test/{LINEUP[PRO]}/{OLD_LINEUP}"


async def _old_lineup_standing(league: Any) -> None:
    """Pro's lineup stands as message 6100, recorded against the division."""
    await league.write("UPDATE divisions SET lineup_message_id = ? WHERE id = ?", OLD_LINEUP, PRO)
    league.channel(LINEUP[PRO]).seed(OLD_LINEUP, "Pro's lineup")


async def _lineup_jobs(league: Any) -> list[dict[str, Any]]:
    """The jobs of the one `lineup.post` asked, in order, each with its payload read back."""
    [change] = [row for row in await change_rows(league.db_path) if row["kind"] == KIND]
    jobs = await step_rows(league.db_path, change["id"])
    for job in jobs:
        job["payload"] = json.loads(job["payload"] or "{}")
    return jobs


async def _run_lineup_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the lineup's job called *name* is done."""
    for _ in range(50):
        if any(job["name"] == name and job["done_at"] for job in await _lineup_jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the job {name} never finished: {await _lineup_jobs(league)}")


def _lineup_events(league: Any) -> list[tuple[str, int]]:
    """What Pro's lineup channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == LINEUP[PRO]]


async def _lineup_id(league: Any) -> Any:
    [row] = await league.rows("SELECT lineup_message_id FROM divisions WHERE id = ?", PRO)
    return row["lineup_message_id"]


async def _lineup_audits(league: Any) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT actor_name, new_value, timestamp FROM audit_entries "
        "WHERE change_type = 'SIGNUP_LINEUP_POSTED' AND division_id = ?", PRO,
    )


def _pictures_on(league: Any, monkeypatch: Any, tmp_path: Any) -> Any:
    """The lineup is drawn as a picture: the drawing the builder handed placement answers that the
    league draws its lineup, and draws it as a PNG. Gives the stand-in drawing."""
    from tests.support.lineup_drawing import LineupDrawingStub, draw_lineups_with

    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    return draw_lineups_with(league.bot, monkeypatch, LineupDrawingStub(picture=png))


async def test_the_text_lineup_is_posted_before_the_old_one_is_deleted(tmp_path):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(sent, new)] = _lineup_events(league)
    assert sent == "send"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert _lineup_events(league) == [("send", new), ("delete", OLD_LINEUP)]
    names = [job["name"] for job in await _lineup_jobs(league)]
    assert names == ["post_lineup", "delete_replaced"]
    assert await _lineup_id(league) == new
    assert "Lineup posted as text" not in await _said(league), "the text is no fallback here"


async def test_a_refused_text_lineup_leaves_the_old_lineup_standing(tmp_path):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    league.channel(LINEUP[PRO]).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert (await stopped_job(league.db_path))["name"] == "post_lineup"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages
    assert ("delete", OLD_LINEUP) not in _lineup_events(league)
    assert await _lineup_id(league) == OLD_LINEUP


async def test_a_lineup_picture_is_posted_before_the_old_lineup_is_deleted_by_a_job(
    tmp_path, monkeypatch,
):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    _pictures_on(league, monkeypatch, tmp_path)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(sent, new)] = _lineup_events(league)
    assert sent == "send"
    assert league.channel(LINEUP[PRO]).files[-1] is not None, "the lineup was not a picture"
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await run_queue(league.bot)

    assert _lineup_events(league) == [("send", new), ("delete", OLD_LINEUP)]
    assert [job["name"] for job in await _lineup_jobs(league)] == [
        "post_lineup", "delete_replaced",
    ]


async def test_a_refused_lineup_picture_is_posted_as_text_and_stops_nothing(
    tmp_path, monkeypatch,
):
    """Pro's lineup stands as 6100, and the drawing the builder handed draws Pro's lineup as a
    PNG; Pro's lineup channel refuses every send carrying a picture and takes the text. The
    lineup asked for is posted as text in the same try: the post job is done at its first try,
    the queue does not stop, no picture stands in the channel, the old lineup is deleted by the
    job after it, nothing records the refusal in the log channel, and the PNG is released."""
    from tests.support.lineup_drawing import LineupDrawingStub, draw_lineups_with
    from tests.support.season_league import refuses_pictures

    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    png = tmp_path / "lineup.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    drawing = draw_lineups_with(league.bot, monkeypatch, LineupDrawingStub(picture=png))
    channel = league.channel(LINEUP[PRO])
    channel.fail_when = refuses_pictures

    await _ask(league, PRO)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert any(call.kwargs.get("file") for call in channel.send.await_args_list), \
        "no picture was sent to be refused"
    assert all(not files for files in channel.files), "a picture was posted"
    [(sent, new)] = [event for event in _lineup_events(league) if event[0] == "send"]
    assert ("delete", OLD_LINEUP) in _lineup_events(league)
    assert await _lineup_id(league) == new
    post = [job for job in await _lineup_jobs(league) if job["name"] == "post_lineup"]
    assert [(bool(job["done_at"]), job["tries"]) for job in post] == [(True, 0)]
    assert "Lineup posted as text" not in await _said(league)
    assert drawing.discarded == [png]


#: Why Pro's lineup could not be drawn, where its drawing fails.
PROBLEM = "the template has no rows"
#: The posting job's own line where the lineup it posted fell back to text, after the member and
#: the change it ran for: the division, the problem, and what puts it right (the owner's decision
#: on #283).
POSTED_AS_TEXT = (
    "{change} | Lineup posted as text\n"
    "  division: {division}\n"
    "  problem: {problem}\n"
    "  to put it right: fix the lineup template; the picture is posted with the next change to "
    "the division's drivers"
)


async def _lineup_asked(tmp_path: Any, monkeypatch: Any, drawing: Any) -> tuple[Any, None]:
    """Pro's lineup asked of the queue, as a driver command asks it, with *drawing* the one the
    builder handed placement. Returns the league, and no reply, the bot's change having none."""
    from tests.support.lineup_drawing import draw_lineups_with

    league = await ongoing_league(tmp_path)
    draw_lineups_with(league.bot, monkeypatch, drawing)
    await _ask(league, PRO)
    return league, None


async def _review_approved(tmp_path: Any, monkeypatch: Any, drawing: Any) -> tuple[Any, Any]:
    """Round 3's penalty review in Pro, attendance on with its real hook and the real placement
    service handed *drawing* (roles granted and revoked by doubles), Pro's lineup channel and
    attendance channel set. Pro sets an autoreserve threshold of one point and has a Reserve team
    with a free seat; Lewis, raced round 4 without an RSVP, is on the threshold. Alex approves
    round 3's reports with a penalty for Lewis: he is moved to the Reserve team, and the review's
    own job posts Pro's lineup. Returns the league and Alex's interaction."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services.placement_service import PlacementService
    from tests.results.test_report_approval_change import _approve, _lewis_owed_an_autoreserve
    from tests.support.change_queue import set_user
    from tests.support.review_league import DIVISION_ID, LEWIS, channel, penalty, review_league
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    # The drawing placement is handed: the capacity guards finding room, and the lineup *drawing*'s.
    handed = SimpleNamespace(
        reserve_capacity_problem=AsyncMock(return_value=None),
        sheet_capacity_problem=AsyncMock(return_value=None),
        standings_capacity_problem=AsyncMock(return_value=None),
        declared_capacity_problem=AsyncMock(return_value=None),
        lineup_wanted=drawing.lineup_wanted,
        draw_lineup=drawing.draw_lineup,
        discard_picture=drawing.discard_picture,
    )

    def _hook(bot: Any, db_path: str) -> Any:
        placement = PlacementService(db_path, drawing=handed)
        placement._grant_roles = AsyncMock()
        placement._revoke_roles = AsyncMock()
        placement.grant_roles = AsyncMock()
        placement.revoke_roles = AsyncMock()
        return AttendanceAfterReview(bot, placement)

    league = await review_league(tmp_path, attendance=True, attendance_hook=_hook)
    await _lewis_owed_an_autoreserve(league)
    for channel_id in (REVIEW_ATTENDANCE, REVIEW_LINEUP):
        league.channels[channel_id] = channel(channel_id, league.events)
        league.channels[channel_id].guild = league.guild
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(REVIEW_ATTENDANCE)),
        )
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = ? WHERE id = ?", (REVIEW_LINEUP, DIVISION_ID)
        )
        await db.commit()
    set_user(league.bot, MagicMock(id=9000))
    league.bot.user.__str__.return_value = "LeagueBot"
    return league, await _approve(league, staged=[penalty(LEWIS)])


#: Pro's attendance and lineup channels in the review's league.
REVIEW_ATTENDANCE, REVIEW_LINEUP = 707, 708


async def _placements_approved(tmp_path: Any, monkeypatch: Any, drawing: Any) -> tuple[Any, Any]:
    """Season 3's placements approved with ✅ Approve on `/season placements-review` by the admin
    (77), with *drawing* the one the builder handed placement: the approval's own job posts Pro's
    and Am's lineups. Returns the league and the admin's press."""
    from tests.support.lineup_drawing import draw_lineups_with
    from tests.support.season_league import press_approve, season_league

    league = await season_league(tmp_path, monkeypatch=monkeypatch)
    draw_lineups_with(league.bot, monkeypatch, drawing)
    press = await press_approve(league)
    assert not league.errors, league.errors
    return league, press


async def _placements_confirmed(tmp_path: Any, monkeypatch: Any, drawing: Any) -> tuple[Any, Any]:
    """Season 3 ongoing and placing drivers mid-season, Max (Pro) and Charles (Am) placed and not
    yet committed, with *drawing* the one the builder handed placement; the admin (77) presses ✅
    Confirm placements on a mid-season `/season placements-review`: the confirmation's own job
    posts Pro's and Am's lineups. Returns the league and the admin's press."""
    from tests.support.lineup_drawing import draw_lineups_with
    from tests.support.season_league import placing_league, post_mid_season_review, press_approve

    league = await placing_league(tmp_path, monkeypatch=monkeypatch)
    draw_lineups_with(league.bot, monkeypatch, drawing)
    press = await press_approve(league, await post_mid_season_review(league))
    assert not league.errors, league.errors
    return league, press


@pytest.mark.parametrize(("start", "member", "change", "divisions"), [
    pytest.param(_lineup_asked, "the bot", "posting the lineup of **Pro**", ("Pro",),
                 id="a lineup asked"),
    pytest.param(_review_approved, "Alex (`<@{member_id}>`)",
                 "✅ Approve on round 3's penalty review", ("Pro",), id="a review's sanction"),
    pytest.param(_placements_approved, "Admin (`<@{admin_id}>`)",
                 "the ✅ Approve button of `/season placements-review`", ("Pro", "Am"),
                 id="the placements approved"),
    pytest.param(_placements_confirmed, "Admin (`<@{admin_id}>`)",
                 "the ✅ Confirm placements button of `/season placements-review`",
                 ("Pro", "Am"), id="the placements confirmed mid-season"),
])
async def test_a_lineup_fallen_back_to_text_is_recorded_by_every_change_that_posts_it(
    tmp_path, monkeypatch, start, member, change, divisions,
):
    """Each change that posts a lineup, where the drawing the builder handed hands back the
    problem "the template has no rows" and the text stands in: each lineup it posts is posted as
    text, the queue does not stop, and the log channel names the problem once for each division,
    in that lineup's posting job's own "Lineup posted as text" line, opening with the member and
    the change it ran for, as every line does, naming the division and saying what puts it right. No reply names the problem or a
    lineup posted as text."""
    from tests.support.change_queue import MEMBER_ID
    from tests.support.lineup_drawing import LineupDrawingStub
    from tests.support.season_league import ADMIN_ID

    drawing = LineupDrawingStub(problem=PROBLEM)
    league, asked = await start(tmp_path, monkeypatch, drawing)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(drawing.drawn) == len(divisions), drawing.drawn
    logged = list(league.bot.log_channel.sent)
    named = [line for line in logged if PROBLEM in line]
    assert len(named) == len(divisions), named
    for division in divisions:
        line = member.format(member_id=MEMBER_ID, admin_id=ADMIN_ID) + " | " + (
            POSTED_AS_TEXT.format(change=change, division=division, problem=PROBLEM)
        )
        assert sum(each.startswith(line) for each in named) == 1, named
    if asked is not None:
        from tests.support.season_league import reply

        told = reply(asked)
        for words in (PROBLEM, "posted as text"):
            assert words not in told, told


@pytest.mark.parametrize("form", ["text", "image"])
@pytest.mark.xfail(strict=True, reason="#283: the lineup's audit is stamped with the change queue's time")
async def test_the_lineup_s_id_and_audit_are_saved_with_its_post(tmp_path, monkeypatch, form):
    """Once the post job is done, and before the deletion after it runs, the division records
    the new lineup and one `SIGNUP_LINEUP_POSTED` audit stands, written by the system, saying
    "image" where the lineup was a picture, and stamped at the queue's clock, moved an hour on
    from the host's."""
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    if form == "image":
        _pictures_on(league, monkeypatch, tmp_path)
    league.clock.advance(hours=1)

    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")

    [(_sent, new)] = _lineup_events(league)
    assert await _lineup_id(league) == new
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages, "the old lineup went too soon"
    [audit] = await _lineup_audits(league)
    assert audit["actor_name"] == "system"
    assert audit["timestamp"] == league.clock.now.isoformat()
    value = json.loads(audit["new_value"])
    assert value["division"] == "Pro"
    assert value.get("form") == ("image" if form == "image" else None)


async def test_an_old_lineup_discord_will_not_delete_stops_the_queue_and_once_discarded_is_named(
    tmp_path,
):
    """The new lineup is posted; Discord refuses to delete the old one: the queue stops at the
    deletion, keeping the old lineup's link. Once a league admin discards it, the log channel's
    Discard line names the deletion not done, and both lineups stand."""
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    league.channel(LINEUP[PRO]).messages[OLD_LINEUP].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _ask(league, PRO)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_replaced"
    assert OLD_LINEUP_LINK in str(stopped["result"])

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "deleting the earlier lineup of **Pro**" in await _said(league)
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages
    assert len([kind for kind, _ in _lineup_events(league) if kind == "send"]) == 1


async def test_a_stop_between_the_lineup_post_and_its_delete_deletes_the_old_one_on_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await _old_lineup_standing(league)
    await _ask(league, PRO)
    await _run_lineup_through(league, "post_lineup")
    assert OLD_LINEUP in league.channel(LINEUP[PRO]).messages

    await league.restart()
    await run_queue(league.bot)

    assert OLD_LINEUP not in league.channel(LINEUP[PRO]).messages
    assert len([kind for kind, _ in _lineup_events(league) if kind == "send"]) == 1
    assert [job["name"] for job in await _lineup_jobs(league) if job["done_at"]].count(
        "delete_replaced") == 1


async def test_a_lineup_step_with_no_channel_raises(tmp_path):
    """Pro's lineup channel was never set. The lineup job a review or an approval runs for Pro
    (`lineup_step`, through the real placement service) raises, naming the command that sets the
    channel, for the queue to stop on; nothing is posted."""
    from types import SimpleNamespace

    from leaguebot.core.models.change import StepFailedOnDiscord
    from leaguebot.core.services.lineup_posting import lineup_step
    from tests.support.change_queue import in_a_job

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET lineup_channel_id = NULL WHERE id = ?", PRO)
    before = len(league.texts(LINEUP[PRO]))
    step = lineup_step(league.bot.placement_service)
    ctx = SimpleNamespace(
        bot=league.bot, kept=[], step_payload={"division_id": PRO, "division_name": "Pro"},
        payload={}, tries=0,
    )

    with in_a_job(), pytest.raises(StepFailedOnDiscord, match="/division lineup-channel"):
        await step.run(ctx)

    assert len(league.texts(LINEUP[PRO])) == before
