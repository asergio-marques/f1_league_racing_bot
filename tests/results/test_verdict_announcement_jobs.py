"""A round's verdicts announced by the review's `announce_verdict` jobs (#439, slice 2).

`post_penalty_announcements` and `post_appeal_announcements` lose their callers to the queue, one
`announce_verdict` job for each penalty or correction an approval saves, and go. What their tests
held that still holds is pinned here, through the queue: what a verdict says, the driver named
rather than by their user id, the name the league recorded where the server no longer knows them,
no further action announced as no penalty, the message and channel a verdict was announced in
saved with it, a verdicts channel missing or gone stopping the queue rather than being
stepped over, and the heading over the verdicts: drawn in words where the banner cannot be drawn,
and, where it cannot be posted at all, a job like any other that stops the queue until it is
retried or discarded, a discarded heading letting the verdicts go out beneath none (decided with
the owner at Gate 2, 2026-10-05, overruling a best-effort heading).

The league is `tests.support.review_league`'s: round 3 of division 11 (Pro), Lewis (101) its Feature
Race winner, image generation off unless `drawn` switches the verdict graphic on. The approvals are
asked as the review's controls ask them.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.attendance.services.hooks.attendance_after_review import AttendanceAfterReview
from leaguebot.framework.db.database import get_connection
from leaguebot.results.services.review.penalty_service import StagedPenalty
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.review.verdict_announcement_service import (
    NO_FURTHER_ACTION,
    describe_penalty,
)
from tests.support.change_queue import (
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    set_user,
    tier_member,
    updated_reply,
)
from tests.support.mentions import notified
from tests.support.review_league import (
    AMEND_CHANNEL,
    DIVISION_ID,
    HEADING,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    NOW,
    PROMPT,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    candidate,
    channel,
    one,
    penalty,
    review_league,
    stopped_at,
    verdict_headings,
)
from tests.support.settings_league import unruled
from tests.support.switches import answer_switch

APPEALS_PROMPT = 8902



def _nfa(driver: int) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver, session_type=SessionType.FEATURE_RACE, penalty_type="NFA",
        penalty_seconds=None, description="Contact at turn four",
        justification="Racing incident",
    )


async def _approve_reports(league: ReviewLeague, staged: list[StagedPenalty]) -> Any:
    """Alex, a league manager, presses Approve on round 3's review with *staged*. Gives his
    interaction, whose acknowledgement the queue updates with the outcome."""
    interaction = member_interaction(league.bot, user=tier_member("manager"))
    await league.bot.change_queue.ask(
        "results.reports.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged], "pardons": [],
            "prompt_message_id": PROMPT, "approval_message_id": None,
        },
        interaction=interaction,
        what="✅ Approve on round 3's penalty review",
    )
    return interaction


async def _appeals_league(tmp_path: Any, **options: Any) -> ReviewLeague:
    """Round 3 awaiting its appeal verdicts, its appeals prompt standing."""
    league = await review_league(
        tmp_path, round_status="AWAITING_APPEAL_VERDICTS", other_division=False,
        appeals_prompt=APPEALS_PROMPT, **options,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    return league


async def _approve_appeals(league: ReviewLeague, staged: list[StagedPenalty]) -> None:
    await league.bot.change_queue.ask(
        "results.appeals.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged],
            "appeals_prompt_message_id": APPEALS_PROMPT,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's appeals review",
    )


def _verdicts(league: ReviewLeague) -> list[str]:
    """What the verdicts channel was sent that names Lewis, in order."""
    channel = league.channel(VERDICTS_CHANNEL)
    return [
        channel.messages[mid].content for mid in league.sent_to(VERDICTS_CHANNEL)
        if f"<@{LEWIS}>" in (channel.messages[mid].content or "")
    ]


@pytest.fixture()
def drawn(monkeypatch: Any, tmp_path: Any) -> list[dict[str, Any]]:
    """Switch the verdict graphic on, and capture what each verdict asks it to draw without a
    rasteriser. Gives the list of the drawings' arguments, in order."""
    from leaguebot.image.services.aspects import image_verdict_post
    from leaguebot.image.services.aspects.image_verdict_post import VerdictRender
    from leaguebot.image.services.aspects.image_verdict_service import VerdictDrawing
    from leaguebot.image.services.render.image_drawing import ImageDrawing
    from leaguebot.results.services.review import verdict_announcement_service as vas

    png = tmp_path / "verdict.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    built: list[dict[str, Any]] = []

    real_wanted = ImageDrawing.aspect_wanted

    async def _wanted(self: Any, aspect: str, template_key: str | None = None) -> bool:
        return True if aspect == "verdicts" else await real_wanted(self, aspect, template_key)

    async def _build(**kwargs: Any) -> Any:
        built.append(kwargs)
        return VerdictDrawing(
            kind=kwargs["kind"], season_number=kwargs["season_number"],
            division_name=kwargs["division_name"], round_number=kwargs["round_number"],
            session_name=kwargs["session_label"], driver_name=kwargs["driver_name"],
            team_name=kwargs.get("team_name"), penalty=kwargs["penalty_description"],
            description=kwargs["description_text"], justification=kwargs["justification_text"],
        )

    async def _render(_config: Any, _renderer: Any, _drawing: Any, **_kwargs: Any) -> Any:
        return VerdictRender(png=png, notices=[], problem=None)

    async def _team(*_args: Any, **_kwargs: Any) -> str:
        return "Red Bull"

    monkeypatch.setattr(ImageDrawing, "aspect_wanted", _wanted)
    monkeypatch.setattr(image_verdict_post, "build_drawing", _build)
    monkeypatch.setattr(image_verdict_post, "render_verdict", _render)
    monkeypatch.setattr(vas, "team_name_for_entry", _team)
    return built


# ---------------------------------------------------------------------------
# What a verdict says
# ---------------------------------------------------------------------------


async def test_a_report_verdict_names_the_round_the_driver_the_penalty_and_the_incident(
    tmp_path,
):
    """Alex approves a five-second penalty for Lewis in round 3. The verdict names the round,
    Lewis, the penalty and the incident, and may notify the members it mentions, never
    @everyone nor a role."""
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    [verdict] = _verdicts(league)
    assert verdict.startswith("**Season 1 Pro Round 3")
    assert f"**Driver**: <@{LEWIS}>" in verdict
    assert f"**Penalty**: {describe_penalty('TIME', 5)}" in verdict
    assert "**Description**: Corner cutting" in verdict
    assert "**Justification**: Turn 4, lap 12" in verdict
    [sent] = [call for call in league.channel(VERDICTS_CHANNEL).send.await_args_list
              if f"<@{LEWIS}>" in (call.args[0] if call.args else "")]
    assert notified(sent.kwargs.get("allowed_mentions")) == {
        "everyone": False, "users": "any", "roles": set()
    }


async def test_no_further_action_is_announced_as_no_penalty(tmp_path):
    """Never "Disqualified", and never a number of seconds (#138)."""
    league = await review_league(tmp_path)
    await _approve_reports(league, [_nfa(LEWIS)])
    await run_queue(league.bot)

    [verdict] = _verdicts(league)
    assert f"**Penalty**: {NO_FURTHER_ACTION}" in verdict
    assert "Disqualified" not in verdict
    assert "seconds" not in verdict


async def test_an_appeal_of_no_further_action_is_announced_as_no_penalty(tmp_path):
    league = await _appeals_league(tmp_path)
    await _approve_appeals(league, [_nfa(LEWIS)])
    await run_queue(league.bot)

    [verdict] = _verdicts(league)
    assert f"**Penalty**: {NO_FURTHER_ACTION}" in verdict
    assert "Disqualified" not in verdict


CURRENT_ACCOUNT = 31337


async def test_a_verdict_on_a_result_under_a_past_account_names_the_current_one(tmp_path):
    """Core specification, driver accounts: everything posted from then on names the driver by
    their current account. Lewis's result stands under 101; his profile has since moved to
    31337."""
    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
            (str(CURRENT_ACCOUNT), LEWIS_PROFILE),
        )
        await db.commit()
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    channel = league.channel(VERDICTS_CHANNEL)
    sent = [channel.messages[mid].content or "" for mid in league.sent_to(VERDICTS_CHANNEL)]
    assert [content for content in sent if f"<@{CURRENT_ACCOUNT}>" in content]
    assert not [content for content in sent if f"<@{LEWIS}>" in content]


# ---------------------------------------------------------------------------
# The verdict graphic names the driver, never their user id (#141)
# ---------------------------------------------------------------------------


async def test_a_report_verdict_is_drawn_under_the_driver_s_server_name(tmp_path, drawn):
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Lewis"]


async def test_an_appeal_verdict_is_drawn_under_the_driver_s_server_name(tmp_path, drawn):
    league = await _appeals_league(tmp_path)
    await _approve_appeals(league, [penalty(LEWIS, seconds=-5)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Lewis"]


async def test_a_driver_the_server_no_longer_knows_is_drawn_under_their_signup_name(
    tmp_path, drawn,
):
    league = await review_league(tmp_path)
    league.guild.get_member = MagicMock(return_value=None)
    league.guild.fetch_member = AsyncMock(
        side_effect=http_error(discord.NotFound, status=404, text="Unknown Member"),
    )
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (created_at, discord_user_id, discord_username, server_display_name) "
            "VALUES ('2026-01-01T00:00:00+00:00', ?, 'lewis44', 'Signed Up Lewis')",
            (str(LEWIS),),
        )
        await db.commit()
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Signed Up Lewis"]


async def test_a_test_driver_is_drawn_under_its_test_name(tmp_path, drawn):
    """A test driver is no member of the server, so the server names nobody: the driver is
    drawn under the name test mode gave it."""
    league = await review_league(tmp_path)
    league.guild.get_member = MagicMock(return_value=None)
    league.guild.fetch_member = AsyncMock(
        side_effect=http_error(discord.NotFound, status=404, text="Unknown Member"),
    )
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET is_test_driver = 1, test_display_name = 'Test Lewis' "
            "WHERE id = ?",
            (LEWIS_PROFILE,),
        )
        await db.commit()
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Test Lewis"]


async def test_no_further_action_is_drawn_as_no_penalty(tmp_path, drawn):
    league = await review_league(tmp_path)
    await _approve_reports(league, [_nfa(LEWIS)])
    await run_queue(league.bot)

    assert [item["penalty_description"] for item in drawn] == [NO_FURTHER_ACTION]


# ---------------------------------------------------------------------------
# Where a verdict was announced is saved with it (#189)
# ---------------------------------------------------------------------------


async def test_a_report_verdict_saves_the_message_and_channel_it_was_announced_in(tmp_path):
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    channel = league.channel(VERDICTS_CHANNEL)
    [verdict_id] = [mid for mid in league.sent_to(VERDICTS_CHANNEL)
                    if f"<@{LEWIS}>" in (channel.messages[mid].content or "")]
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT announcement_message_id, announcement_message_ids, announcement_channel_id "
            "FROM penalty_records"
        )
        row = dict(await cursor.fetchone())
    assert row["announcement_message_id"] == str(verdict_id)
    assert json.loads(row["announcement_message_ids"]) == [verdict_id]
    assert str(row["announcement_channel_id"]) == str(VERDICTS_CHANNEL)


async def test_an_appeal_verdict_saves_the_message_it_was_announced_in(tmp_path):
    league = await _appeals_league(tmp_path)
    await _approve_appeals(league, [penalty(LEWIS, seconds=-5)])
    await run_queue(league.bot)

    channel = league.channel(VERDICTS_CHANNEL)
    [verdict_id] = [mid for mid in league.sent_to(VERDICTS_CHANNEL)
                    if f"<@{LEWIS}>" in (channel.messages[mid].content or "")]
    assert await one(
        league.db_path, "SELECT announcement_message_id FROM appeal_records",
    ) == str(verdict_id)


# ---------------------------------------------------------------------------
# A verdicts channel missing or gone is reported, not stepped over (#237)
# ---------------------------------------------------------------------------


async def test_a_division_with_no_verdicts_channel_stops_the_queue_at_its_report_verdict(
    tmp_path,
):
    """The heading over the verdicts has nowhere to go either, and stops the queue first; once a
    league admin discards it, the verdict stops it in turn rather than being stepped over."""
    league = await review_league(tmp_path, verdicts_channel=False)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    await discard_job(league.bot)
    assert await stopped_at(league) == "announce_verdict"
    assert await one(league.db_path, "SELECT COUNT(*) FROM penalty_records") == 1


async def test_a_deleted_verdicts_channel_stops_the_queue_at_its_verdict(tmp_path):
    """As with no verdicts channel: the heading stops the queue first, and once it is discarded
    the verdict stops it in turn."""
    league = await review_league(tmp_path)
    del league.channels[VERDICTS_CHANNEL]
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    await discard_job(league.bot)
    assert await stopped_at(league) == "announce_verdict"
    assert await one(league.db_path, "SELECT COUNT(*) FROM penalty_records") == 1


# ---------------------------------------------------------------------------
# The heading over the verdicts is a job of its own (owner, Gate 2, 2026-10-05)
# ---------------------------------------------------------------------------


async def _announced(league: ReviewLeague) -> int:
    return await one(
        league.db_path,
        "SELECT COUNT(*) FROM penalty_records WHERE announcement_message_id IS NOT NULL",
    )


async def test_a_heading_discord_refuses_stops_the_queue_before_the_verdicts(tmp_path):
    """The verdicts channel refuses the written heading alone. The heading is a job of its own,
    so the queue stops at it and no verdict goes out ahead of it (owner, Gate 2, 2026-10-05:
    "Heading stops the queue"), and the stop notice names the fault in the core specification's
    plain words, that Discord refused or failed a request. Once a league admin discards it, both
    verdicts are announced beneath no heading, and the approval is recorded as incomplete."""
    league = await review_league(tmp_path)
    league.channel(VERDICTS_CHANNEL).fail_when = lambda content, _kwargs: content == HEADING
    interaction = await _approve_reports(league, [penalty(LEWIS), penalty(MAX)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    assert league.sent_to(VERDICTS_CHANNEL) == []
    assert await _announced(league) == 0
    assert "Discord refused or failed a request" in league.log(), league.log()

    # The channel would take the heading now, so a build that tried it again behind the
    # discarded job would show here.
    league.channel(VERDICTS_CHANNEL).fail_when = None
    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert verdict_headings(league) == []
    assert len(league.sent_to(VERDICTS_CHANNEL)) == 2
    assert await _announced(league) == 2
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    assert "heading" in updated_reply(interaction).lower(), (
        "the reply does not name the heading that was not posted"
    )


async def test_a_heading_that_raises_stops_the_queue_before_the_verdicts(tmp_path, monkeypatch):
    """The banner's poster raising, a fault in the bot, stops the queue at the heading like any
    failure; no verdict goes out until it is cleared. A Retry once it works posts the heading
    first, then both verdicts beneath it."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    real = ImageDrawing.draw_banner
    broken = {"on": True}

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        if broken["on"]:
            raise RuntimeError("the banner could not be posted")
        return await real(*args, **kwargs)

    monkeypatch.setattr(ImageDrawing, "draw_banner", _boom)
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS), penalty(MAX)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    assert league.sent_to(VERDICTS_CHANNEL) == []

    broken["on"] = False
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 3
    assert await _announced(league) == 2


async def test_a_banner_that_cannot_be_drawn_heads_the_verdicts_in_words(tmp_path, monkeypatch):
    """The banner switched on, its render failing for want of a rasteriser: the verdicts are
    headed by the written heading instead, posted first, and the verdict follows it."""
    from leaguebot.image.services.aspects import image_verdict_banner_post as banner
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    real_wanted = ImageDrawing.aspect_wanted

    async def _wanted(self: Any, aspect: str, template_key: str | None = None) -> bool:
        return True if aspect == "verdict_banner" else await real_wanted(self, aspect, template_key)

    async def _render(_config: Any, _renderer: Any, _drawing: Any, **_kwargs: Any) -> Any:
        return banner.BannerRender(png=None, problem="RASTERISER")

    monkeypatch.setattr(ImageDrawing, "aspect_wanted", _wanted)
    monkeypatch.setattr(banner, "render_banner", _render)
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 2
    assert await _announced(league) == 1


# ---------------------------------------------------------------------------
# A sheet or a sanction that stops the queue names what to repair
# ---------------------------------------------------------------------------

#: A channel the division was given and the server no longer holds.
GONE_ATTENDANCE_CHANNEL = 706


async def _no_attendance_channel(league: ReviewLeague) -> None:
    """Division 11 was given an attendance channel the server no longer holds."""
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(GONE_ATTENDANCE_CHANNEL)),
        )
        await db.commit()


async def _no_reserve_team(league: ReviewLeague) -> None:
    """Division 11 sets an autoreserve threshold of one point and has no Reserve team. Lewis, in
    the seat of team 3001, raced round 4, the last final round and so where the division's totals
    stand, without an RSVP, which puts him on the threshold. The division has an attendance
    channel the server holds, for the sheet before the sanction to post to."""
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE attendance_config SET autoreserve_threshold = 1")
        seat = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (3001, 1, ?)",
            (LEWIS_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id) VALUES (?, 1, ?, ?)",
            (LEWIS_PROFILE, DIVISION_ID, seat.lastrowid),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status, attended) VALUES (?, ?, ?, 'NO_RSVP', 1)",
            (LATER_ROUND_ID, DIVISION_ID, LEWIS_PROFILE),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL)),
        )
        await db.commit()
    league.channels[ATTENDANCE_CHANNEL] = channel(ATTENDANCE_CHANNEL, league.events)


@pytest.mark.parametrize(
    "seed, job, reason, fault",
    [
        pytest.param(_no_attendance_channel, "attendance_sheet", "is not in the server",
                     "StepFailedOnDiscord", id="sheet"),
        pytest.param(_no_reserve_team, "check_sanction", "the division has no Reserve team",
                     "SanctionNotApplicable", id="sanction"),
    ],
)
async def test_a_sheet_or_sanction_stop_names_what_to_repair(tmp_path, seed, job, reason, fault):
    """Attendance is on and its real hook, `AttendanceAfterReview`, is the one the change types
    are handed. Division 11's attendance channel is gone from the server, or it has an
    autoreserve threshold Lewis reaches and no Reserve team to move him to. Alex approves round
    3's reports: the queue stops at the sheet, or at the check of Lewis's sanction ahead of its
    save, and the stop line's fault
    is the reason the hook gave, which says what to repair, never the name of an exception."""
    league = await review_league(tmp_path, attendance=True, attendance_hook=_real_attendance_hook)
    await seed(league)

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == job
    [stop] = [line for line in league.bot.log_channel.sent
              if "The queue is stopped at job #" in line]
    assert reason in stop
    assert fault not in stop and "StepFailedOnDiscord" not in stop


# ---------------------------------------------------------------------------
# A sheet with no attendance channel set stops the queue (#439, slice 6b, fault 5)
# ---------------------------------------------------------------------------

#: The amendment's appeals prompt, in the amendment channel.
AMEND_APPEALS_PROMPT = 8952


def _real_attendance_hook(bot: Any, _db_path: str) -> Any:
    """Attendance's real hook, `AttendanceAfterReview`, for *bot*, with a placement service and a
    lineup poster that do nothing, so that the sheet is posted by `post_attendance_sheet` itself:
    handed to the league as it is built (`review_league`'s *attendance_hook*), so the change types
    hold it."""
    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "guard_move", "sack_on", "move_on"):
        setattr(placement, name, AsyncMock(return_value=None))
    lineups = MagicMock()
    lineups.refresh_lineup = AsyncMock(return_value=None)
    return AttendanceAfterReview(
        bot, placement, lineups=lineups, drawing=bot.image_drawing
    )


def _sheet_stop(league: ReviewLeague) -> str:
    [stop] = [line for line in league.bot.log_channel.sent
              if "The queue is stopped at job #" in line]
    return stop


async def test_a_review_s_attendance_sheet_with_no_channel_stops_the_queue_naming_the_command(
    tmp_path,
):
    """Attendance is on, and division 11 (Pro) was never given an attendance channel. Alex
    approves round 3's reports: the queue stops at the sheet, its stop line naming the command
    that sets the channel, rather than the sheet completing having posted nothing."""
    league = await review_league(tmp_path, attendance=True, attendance_hook=_real_attendance_hook)

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    assert "/attendance channel attendance" in _sheet_stop(league)


async def test_a_review_s_attendance_sheet_drawn_without_its_graphic_tells_the_log_channel(
    tmp_path,
):
    """Attendance is on. Alex approves round 3's reports, and Pro's attendance sheet is posted
    as text, its graphic having failed: attendance's posting hands back the fault as a line. The
    queue does not stop, and the log channel is told the fault on a line opening with Alex and the
    button he pressed, as it is for the sheet the sync and a season's approval and completion
    post."""
    league = await review_league(tmp_path, attendance=True)
    fault = (
        "Season 1 **Pro** — attendance after round 3: the graphic could not be drawn, so it is "
        "posted as text"
    )
    league.attendance.sheet_posted = {
        "new": 9001, "channel_id": 4242, "takes_slot": False, "faults": [fault],
    }

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert ("post_sheet", DIVISION_ID, False) in league.attendance.calls
    assert any(
        f"{_JOB_OPENING}{fault}" in sent.splitlines() for sent in league.bot.log_channel.sent
    ), league.bot.log_channel.sent


async def test_an_amendment_s_attendance_sheet_with_no_channel_stops_the_queue(tmp_path):
    """Round 3 of Pro, final, is under an amendment of its Feature Race whose report stage is
    approved, its appeals prompt standing in the amendment channel. Attendance is on and Pro was
    never given an attendance channel. Alex approves the amendment's appeals stage with nothing
    staged: the rebuild stops the queue at the sheet, naming the command that sets the channel."""
    league = await review_league(tmp_path, attendance=True, round_status="FINAL",
                                 attendance_hook=_real_attendance_hook)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1, in_penalty_review = 0"
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state, expires_at, started_by, reports_approved_at) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?, ?, 77, ?)",
            (ROUND_ID, AMEND_CHANNEL, NOW.isoformat(),
             json.dumps({"sessions": [], "profiles_before": []}),
             (NOW + timedelta(minutes=30)).isoformat(), NOW.isoformat()),
        )
        await db.commit()
    league.channel(AMEND_CHANNEL).seed(AMEND_APPEALS_PROMPT, "the amendment's appeals")

    await league.bot.change_queue.ask(
        "results.amendment.appeals.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "session_types": ["FEATURE_RACE"], "staged": [], "pardons": [],
            "appeals_prompt_message_id": AMEND_APPEALS_PROMPT,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's amendment appeals",
    )
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    assert "/attendance channel attendance" in _sheet_stop(league)


#: Pro's attendance channel, where the lineup test has one, so that its sheet is posted.
ATTENDANCE_CHANNEL = 707


#: The notice the review's attendance sheet is drawn with, a flag falling back.
REVIEW_SHEET_NOTICE = "no `flag` image for “Lewis”"


async def test_a_review_s_refused_attendance_sheet_s_notice_reaches_the_log_once_when_retried(
    tmp_path, monkeypatch,
):
    """Attendance is on, with its real hook, and Pro has an attendance channel and Lewis seated in
    it. The image module is asked to draw the sheet, and draws it with one notice, a flag falling
    back. Pro's attendance channel refuses every send (Missing Access). Alex approves round 3's
    reports and the queue stops at the attendance sheet; the channel is let accept and a Retry
    posts the sheet as text. The queue runs to the end, and the log channel carries the notice
    once, in a line naming the sheet drawn (season 1, Pro, attendance after the round), never lost
    with the refused try."""
    from leaguebot.image.services.render.image_drawing import ImageDrawing

    picture = tmp_path / "sheet.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    asked: list[str] = []

    async def _wanted(_self: Any) -> bool:
        return True

    async def _draw(_self: Any, _sheet: Any, *, what: str) -> Any:
        asked.append(what)
        return (discord.File(str(picture), filename=picture.name),
                (f"⚠️ {what} — {REVIEW_SHEET_NOTICE}",))

    monkeypatch.setattr(ImageDrawing, "attendance_wanted", _wanted)
    monkeypatch.setattr(ImageDrawing, "draw_attendance_sheet", _draw)
    league = await review_league(tmp_path, attendance=True, attendance_hook=_real_attendance_hook)
    async with get_connection(league.db_path) as db:
        seat = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (3001, 1, ?)",
            (LEWIS_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id) VALUES (?, 1, ?, ?)",
            (LEWIS_PROFILE, DIVISION_ID, seat.lastrowid),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL)),
        )
        await db.commit()
    league.channels[ATTENDANCE_CHANNEL] = channel(ATTENDANCE_CHANNEL, league.events)
    league.channel(ATTENDANCE_CHANNEL).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)
    assert await stopped_at(league) == "attendance_sheet"
    [what] = asked
    assert what.startswith("Season 1 Pro — attendance after round "), what
    league.channel(ATTENDANCE_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert league.sent_to(ATTENDANCE_CHANNEL)
    said = [line for line in league.bot.log_channel.sent if REVIEW_SHEET_NOTICE in line]
    assert len(said) == 1 and said[0].count(REVIEW_SHEET_NOTICE) == 1, league.log()
    assert what in said[0], said[0]


async def test_a_review_s_lineup_with_no_channel_stops_the_queue_naming_the_command(tmp_path):
    """Attendance is on, with its real hook, the real placement service and the real lineup
    poster (roles granted and revoked by doubles on the real team-role service), and division 11
    (Pro) has an attendance channel but was never given a lineup channel. Pro sets an autoreserve
    threshold of one point and has a Reserve team with a free seat; Lewis, raced round 4 without
    an RSVP, is on the threshold. Alex approves round 3's reports: Lewis is moved
    to the Reserve team, and the queue stops at the lineup the sanction owes, its stop line
    naming the command that sets the channel, rather than the lineup completing having posted
    nothing."""
    from leaguebot.core.services.drivers.lineup_posting import LineupPoster
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService

    def _hook(bot: Any, db_path: str) -> Any:
        roles = TeamRoleService(db_path)
        roles._grant_roles = AsyncMock()
        roles._revoke_roles = AsyncMock()
        roles.grant_roles = AsyncMock()
        roles.revoke_roles = AsyncMock()
        placement = PlacementService(db_path, roles=roles)
        return AttendanceAfterReview(bot, placement, roles=roles,
                                     lineups=LineupPoster(db_path),
                                     drawing=bot.image_drawing)

    league = await review_league(tmp_path, attendance=True, attendance_hook=_hook)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE attendance_config SET autoreserve_threshold = 1")
        seat = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (3001, 1, ?)",
            (LEWIS_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id) VALUES (?, 1, ?, ?)",
            (LEWIS_PROFILE, DIVISION_ID, seat.lastrowid),
        )
        reserve = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 'Reserve', 2, 1)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
            (reserve.lastrowid,),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status, attended) VALUES (?, ?, ?, 'NO_RSVP', 1)",
            (LATER_ROUND_ID, DIVISION_ID, LEWIS_PROFILE),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL)),
        )
        await db.commit()
    league.channels[ATTENDANCE_CHANNEL] = channel(ATTENDANCE_CHANNEL, league.events)
    set_user(league.bot, MagicMock(id=9000))
    league.bot.user.__str__.return_value = "LeagueBot"

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "refresh_lineup"
    assert "/division lineup-channel" in _sheet_stop(league)


#: Pro's team 3001's role, and its Reserve team's.
TEAM_ROLE, RESERVE_ROLE = 5502, 5503


async def test_a_review_s_sanction_whose_role_change_discord_refuses_stops_the_queue(tmp_path):
    """Attendance is on, with its real hook, the real placement service, the real team-role
    service and the real lineup poster. Pro sets an
    autoreserve threshold of one point and has a Reserve team with a free seat; Lewis, placed in
    Team 3001 and raced round 4 without an RSVP, is on the threshold. Team 3001 and the Reserve
    team each have a role, and Discord refuses every role change for Lewis (Missing
    Permissions). Alex approves round 3's reports: Lewis is moved to the Reserve team, and the
    queue stops at the job changing his roles, its notice naming it, rather than the approval
    going on as though his roles had been changed."""
    from leaguebot.core.services.drivers.lineup_posting import LineupPoster
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService

    def _hook(bot: Any, db_path: str) -> Any:
        roles = TeamRoleService(db_path)
        return AttendanceAfterReview(bot, PlacementService(db_path, roles=roles), roles=roles,
                                     lineups=LineupPoster(db_path),
                                     drawing=bot.image_drawing)

    league = await review_league(tmp_path, attendance=True, attendance_hook=_hook)
    await _no_reserve_team(league)
    async with get_connection(league.db_path) as db:
        reserve = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 'Reserve', 2, 1)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
            (reserve.lastrowid,),
        )
        await db.executemany(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES (?, ?, '2031-03-04T05:06:07+00:00')",
            [("Team 3001", TEAM_ROLE), ("Reserve", RESERVE_ROLE)],
        )
        await db.execute(
            "UPDATE driver_season_assignments SET committed = 1 WHERE driver_profile_id = ?",
            (LEWIS_PROFILE,),
        )
        await db.commit()
    roles: dict[int, Any] = {}
    for role_id in (555, TEAM_ROLE, RESERVE_ROLE):
        role = MagicMock(spec=discord.Role)
        role.id = role_id
        roles[role_id] = role
    league.guild.get_role = MagicMock(side_effect=roles.get)
    refused = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    lewis = MagicMock(spec=discord.Member)
    lewis.id = LEWIS
    lewis.display_name = "Lewis"
    lewis.mention = f"<@{LEWIS}>"
    lewis.guild = league.guild
    lewis.roles = [roles[555], roles[TEAM_ROLE]]
    lewis.add_roles = AsyncMock(side_effect=refused)
    lewis.remove_roles = AsyncMock(side_effect=refused)
    member_of = league.guild.get_member.side_effect
    league.guild.get_member = MagicMock(
        side_effect=lambda uid: lewis if uid == LEWIS else member_of(uid)
    )
    league.guild.fetch_member = AsyncMock(
        side_effect=lambda uid: lewis if uid == LEWIS else member_of(uid)
    )
    set_user(league.bot, MagicMock(id=9000))
    league.bot.user.__str__.return_value = "LeagueBot"

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "sanction_roles"
    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT ti.is_reserve FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.driver_profile_id = ?", (LEWIS_PROFILE,),
        )).fetchone()
    assert row["is_reserve"] == 1, "Lewis's autoreserve was not saved"
    # The stop notice is a log line, whose mentions are wrapped in code to name without notifying.
    assert f"changing `<@{LEWIS}>`'s roles for their autoreserve in **Pro**" in _sheet_stop(league)


# ---------------------------------------------------------------------------
# A verdict whose picture fails says why in the log channel (image spec :1401)
# ---------------------------------------------------------------------------


#: The words the render gives where Inkscape is not installed, which the log channel keeps.
CONVERTER_ABSENT_WORDS = "Inkscape is not installed on this host."
#: What the verdict's line says puts its picture right where Inkscape is not installed.
CONVERTER_ABSENT_REMEDY = (
    "Inkscape is not installed on the machine running the bot, and the host must install it; "
    "once it has and the round is final, amend the round with `/results rounds amend` to "
    "announce it again"
)


@pytest.mark.parametrize("fault", [
    pytest.param("bot"),
    pytest.param("converter absent"),
])
async def test_a_verdict_whose_picture_fails_is_announced_as_text_and_says_why_in_the_log(
    tmp_path, request, monkeypatch, fault,
):
    """Alex approves a five-second penalty for Lewis in round 3, the verdict graphic switched on,
    and the verdict's picture is not drawn: ("bot") the image module's step that builds it raises
    a fault it does not catch itself (a file it reads cannot be opened), a fault in the bot;
    ("converter absent") the render comes back not drawn, as Inkscape is not installed on the
    host. The verdict is announced as text naming Lewis and the queue runs to the end. For the
    fault in the bot, the log channel gains one line naming round 3 of Pro, the verdict picture,
    the fault by its kind alone and never in the words it was raised with, that the verdict went
    out as text, and that the fault is the bot's, its detail in the host's log, and that once it
    is cleared and the round is final, amending the round with `/results rounds amend` announces
    it again. Without Inkscape, the log channel keeps the render's words, and says the host must
    install it and that once it has and the round is final, amending the round announces it
    again. The fault line, formed already opened with Alex and the button he pressed, opens with
    them once, never twice."""
    from leaguebot.image.services.aspects import image_verdict_post

    raised = "the verdict picture's file could not be opened"
    league = await review_league(tmp_path)
    if fault == "bot":
        request.getfixturevalue("drawn")

        async def _fails(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError(raised)

        monkeypatch.setattr(image_verdict_post, "build_drawing", _fails)
    else:
        from leaguebot.image.models.image_module import Problem, RenderOutcome

        answer_switch(league.bot.module_service, "images", True)
        league.bot.image_config_service.get_toggles = AsyncMock(return_value={"verdicts": True})
        league.bot.image_validity_service.template_reports = AsyncMock(
            return_value={"verdicts_template": MagicMock(valid=True)}
        )
        league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
            problem=Problem(kind="CONVERTER_ABSENT", detail=CONVERTER_ABSENT_WORDS,
                            template_key="verdicts_template"),
        ))
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    [verdict] = _verdicts(league)
    assert f"<@{LEWIS}>" in verdict
    if fault == "converter absent":
        lines = [line for line in league.bot.log_channel.sent if CONVERTER_ABSENT_WORDS in line]
        assert any(CONVERTER_ABSENT_REMEDY in line for line in lines), league.bot.log_channel.sent
        return
    assert f"**Driver**: <@{LEWIS}>" in verdict
    assert not [line for line in league.bot.log_channel.sent if raised in line], (
        league.bot.log_channel.sent
    )
    faults = [line.lower() for line in league.bot.log_channel.sent
              if "Pro" in line and "round 3" in line.lower()]
    assert len(faults) == 1, league.bot.log_channel.sent
    [opened] = [sent for sent in league.bot.log_channel.sent if "Verdict posted as text" in sent]
    assert opened.startswith(f"{_JOB_OPENING}Verdict posted as text\n"), opened
    assert opened.count(_JOB_OPENING) == 1, opened
    [line] = faults
    assert "verdict" in line and ("picture" in line or "image" in line), line
    assert "text" in line and "the bot hit an internal fault" in line, line
    assert ("to put it right: the fault is in the bot, and its detail is in the host's log; "
            "once it is cleared and the round is final, amend the round with "
            "`/results rounds amend` to announce it again") in line, line


# ---------------------------------------------------------------------------
# A verdict's or a banner's render fault is named by its own picture in the log channel
# ---------------------------------------------------------------------------

#: The detail of the template fault the render below fails on.
RENDER_FAULT = "the template declares no `driver_name` field"

#: Each picture: the toggle `/images config toggle` offers it under, its template, and the name
#: its fault line is headed with.
_FAULTED_PICTURES = [
    ("verdicts", "verdicts_template", "Verdicts"),
    ("verdict_banner", "verdict_banner_template", "Verdict banner"),
]


@pytest.mark.parametrize(("aspect", "template", "name", "kind", "detail"), [
    *[pytest.param(aspect, template, name, "TEMPLATE_INVALID", RENDER_FAULT, id=aspect)
      for aspect, template, name in _FAULTED_PICTURES],
    pytest.param(
        "verdict_banner", "verdict_banner_template", "Verdict banner",
        "CONVERTER_ABSENT", CONVERTER_ABSENT_WORDS,
        id="verdict_banner-converter absent",
    ),
])
async def test_a_verdict_picture_s_render_fault_is_named_by_its_own_picture(
    tmp_path, aspect, template, name, kind, detail,
):
    """Image generation is on with the *aspect* picture alone switched on and its template
    valid. Alex approves a five-second penalty for Lewis in round 3 of Pro, and the image
    module's render of that picture fails on a template fault, or, for the banner, for want of
    Inkscape. The verdict is announced naming Lewis and the queue runs to the end; the log channel
    gives the fault on a line opening with Alex and the button he pressed, then "⚠️ <name>
    image", the name `/images config toggle` offers, and nothing in the log calls it a results
    image. Where Inkscape is not installed, the banner's
    line keeps the render's words and says that the host must install it, and that once it has
    and the round is final, amending the round with `/results rounds amend` announces it
    again."""
    from leaguebot.image.models.image_module import Problem, RenderOutcome

    league = await review_league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    league.bot.image_config_service.get_toggles = AsyncMock(return_value={aspect: True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={template: MagicMock(valid=True)}
    )
    league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
        problem=Problem(kind=kind, detail=detail, template_key=template),
    ))
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    [verdict] = _verdicts(league)
    assert f"<@{LEWIS}>" in verdict
    log = league.log()
    [line] = [line for line in log.splitlines() if detail in line]
    assert line.startswith(f"{_JOB_OPENING}⚠️ {name} image — "), line
    assert "Results image" not in log, log
    if kind == "CONVERTER_ABSENT":
        assert CONVERTER_ABSENT_REMEDY in line, line


#: The detail of the one render notice the drawing below comes back with.
NOTICE_DETAIL = "no `flag` image for “Lewis”"

#: The job that posts each verdict picture.
_POSTED_BY = {"verdicts": "announce_verdict", "verdict_banner": "announce_heading"}

#: What every line of the review's jobs opens with: Alex, then the button he pressed.
_JOB_OPENING = "Admin (`<@4242>`) | ✅ Approve on round 3's penalty review | "

#: What a verdict picture's notice block opens with: the job's opening, then the block's heading.
_NOTICE_OPENING = f"{_JOB_OPENING}Image render notices — "

async def _notice_is_framed_and_kept(
    league: ReviewLeague, aspect: str, interaction: Any
) -> None:
    """The log channel carries the one notice once, in a line opening with Alex and the button he
    pressed, then "Image render notices — <picture>:" naming Pro and round 3, and beneath it the
    notice under "Artwork not supplied by the league", naming its field `flag`, with no kind code
    and no template name; the job that posted the *aspect* picture keeps the picture's name and
    its grouped notices on its result; and Alex's reply, on *interaction*, ends in one "Image
    notices" section listing the picture under that name, linked to the log channel's message
    holding it, with its grouped notice beneath, told once."""
    from tests.support.change_queue import step_rows
    from tests.support.drawn_notices import told_section

    log = league.log()
    assert log.count(NOTICE_DETAIL) == 1, log
    [(held, block)] = [(n, message) for n, message in enumerate(league.bot.log_channel.sent, 1)
                       if NOTICE_DETAIL in message]
    assert block.startswith(_NOTICE_OPENING), block
    heading, *grouped = unruled(block).splitlines()
    assert "Pro" in heading and "round 3" in heading.lower(), block
    assert grouped == [
        "  Artwork not supplied by the league", f"    • `flag` — {NOTICE_DETAIL}",
    ], block
    assert "ASSET_FALLBACK_USED" not in block and "_template" not in block, block
    from leaguebot.framework.models.change import RESULT_DRAWN, RESULT_NOTICES, RESULT_PICTURES
    [kept] = [row["result"] for row in await step_rows(league.db_path)
              if row["name"] == _POSTED_BY[aspect]]
    label = heading.removeprefix(_NOTICE_OPENING).removesuffix(":")
    assert kept[RESULT_PICTURES] == [{RESULT_DRAWN: label, RESULT_NOTICES: grouped}], kept
    told = updated_reply(interaction)
    assert told.count("⚠️ **Image notices**") == 1, told
    listed = told_section(label, f"https://discord.test/{held}", tuple(grouped))
    assert listed.split("\n", 1)[1] in told, told
    assert told.count(NOTICE_DETAIL) == 1, told


@pytest.mark.parametrize(
    ("aspect", "template", "name"), _FAULTED_PICTURES,
    ids=[aspect for aspect, _t, _n in _FAULTED_PICTURES],
)
async def test_a_verdict_picture_s_render_notices_reach_the_log_once_naming_the_round(
    tmp_path, aspect, template, name,
):
    """Image generation is on with the *aspect* picture alone switched on and its template
    valid. Alex approves a five-second penalty for Lewis in round 3 of Pro, and the image
    module's render of that picture is drawn with one notice, a flag falling back. The verdict is
    announced naming Lewis and the queue runs to the end; the log channel carries the notice
    once, in a line opening with Alex and the button he pressed, then a heading naming Pro and
    round 3, and beneath it the notice grouped under its kind in plain words, naming its field;
    the job that posted the picture keeps its name and grouped notices, and Alex's reply ends in
    one "Image notices" section listing the picture under that name, linked to the log message,
    its notice told once (issue #635; Constitution V)."""
    from leaguebot.image.models.image_module import RenderNotice, RenderOutcome

    league = await review_league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    league.bot.image_config_service.get_toggles = AsyncMock(return_value={aspect: True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={template: MagicMock(valid=True)}
    )
    picture = tmp_path / f"{aspect}.png"
    picture.write_bytes(b"\x89PNG")
    league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
        png_paths=[picture], notices=[RenderNotice(
            image_type=template, notice_kind="ASSET_FALLBACK_USED", detail=NOTICE_DETAIL,
            field_id="flag",
        )],
    ))
    interaction = await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert any(f"<@{LEWIS}>" in verdict for verdict in _verdicts(league))
    await _notice_is_framed_and_kept(league, aspect, interaction)


@pytest.mark.parametrize(
    ("aspect", "template", "name"), _FAULTED_PICTURES,
    ids=[aspect for aspect, _t, _n in _FAULTED_PICTURES],
)
async def test_a_refused_verdict_picture_s_notice_reaches_the_log_once_when_it_is_retried(
    tmp_path, aspect, template, name,
):
    """Image generation is on with the *aspect* picture alone switched on and its template valid,
    and the verdicts channel refuses every post carrying a picture. Alex approves a five-second
    penalty for Lewis in round 3 of Pro; the picture is drawn with one notice, a flag falling back,
    and Discord refuses its post, stopping the queue. A Retry posts it in words: the queue runs to
    the end, Lewis's verdict is announced, and the log channel carries the notice once, in a line
    opening with Alex and the button he pressed, then a heading naming Pro and round 3, and the
    notice grouped beneath; the job keeps the picture's name and grouped notices across the stop,
    never lost with the refused try, and Alex's reply, after the Retry, lists the picture under
    "Image notices" with its log link and its notice told once (issue #635; image spec, Errors and
    the rejection of input, #550)."""
    from leaguebot.image.models.image_module import RenderNotice, RenderOutcome

    league = await review_league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    league.bot.image_config_service.get_toggles = AsyncMock(return_value={aspect: True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={template: MagicMock(valid=True)}
    )
    picture = tmp_path / f"{aspect}.png"

    async def _render(*_args: Any, **_kwargs: Any) -> Any:
        picture.write_bytes(b"\x89PNG")
        return RenderOutcome(png_paths=[picture], notices=[RenderNotice(
            image_type=template, notice_kind="ASSET_FALLBACK_USED", detail=NOTICE_DETAIL,
            field_id="flag",
        )])

    league.bot.image_render_service.render = AsyncMock(side_effect=_render)
    league.channel(VERDICTS_CHANNEL).fail_when = (
        lambda _content, kwargs: kwargs.get("file") is not None
    )
    interaction = await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is not None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert any(f"<@{LEWIS}>" in verdict for verdict in _verdicts(league))
    await _notice_is_framed_and_kept(league, aspect, interaction)


async def test_a_verdict_picture_that_could_not_be_drawn_keeps_its_fault_line_when_its_text_post_is_refused(
    tmp_path,
):
    """Image generation is on with the verdict picture alone switched on and its template valid,
    and its render fails on a template fault, so the verdict is to be announced as text. The
    verdicts channel refuses every post on the first try, the text included, stopping the queue;
    the channel is let accept and a Retry announces the verdict. The log channel carries the
    picture's fault line, opening with Alex and the button he pressed, then "⚠️ Verdicts image",
    once: it is not lost with the refused try."""
    from leaguebot.image.models.image_module import Problem, RenderOutcome

    league = await review_league(tmp_path)
    answer_switch(league.bot.module_service, "images", True)
    league.bot.image_config_service.get_toggles = AsyncMock(return_value={"verdicts": True})
    league.bot.image_validity_service.template_reports = AsyncMock(
        return_value={"verdicts_template": MagicMock(valid=True)}
    )
    league.bot.image_render_service.render = AsyncMock(return_value=RenderOutcome(
        problem=Problem(kind="TEMPLATE_INVALID", detail=RENDER_FAULT,
                        template_key="verdicts_template"),
    ))
    league.channel(VERDICTS_CHANNEL).fail_when = lambda _content, _kwargs: True
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is not None
    league.channel(VERDICTS_CHANNEL).fail_when = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert any(f"<@{LEWIS}>" in verdict for verdict in _verdicts(league))
    log = league.log()
    lines = [line for line in log.splitlines() if RENDER_FAULT in line]
    assert len(lines) == 1, log
    assert lines[0].startswith(f"{_JOB_OPENING}⚠️ Verdicts image — "), lines[0]


#: The notice the review's attendance sheet and its refreshed lineup are each drawn with, grouped
#: as the drawing hands it back: its kind in plain words, then the notice naming its field.
_GROUPED_NOTICE = (
    "  Artwork not supplied by the league", f"    • `flag` — {NOTICE_DETAIL}",
)

#: Pro's sheet after round 3, as the sheet's notice block names it.
_SHEET_DRAWN = "Season 1 Pro — attendance after round 3"


def _held(league: ReviewLeague) -> tuple[int, str]:
    """The one log channel message carrying the notice, and its place among those sent, which
    the reply's link names."""
    [(held, message)] = [(n, message) for n, message in enumerate(league.bot.log_channel.sent, 1)
                         if NOTICE_DETAIL in message]
    return held, message


def _told_once(interaction: Any, label: str, held: int) -> None:
    """Alex's reply ends in one "Image notices" section listing the picture *label* linked to the
    log message *held*, its grouped notice beneath, told once."""
    from tests.support.drawn_notices import told_section

    told = updated_reply(interaction)
    assert told.count("⚠️ **Image notices**") == 1, told
    listed = told_section(label, f"https://discord.test/{held}", _GROUPED_NOTICE)
    assert listed.split("\n", 1)[1] in told, told
    assert told.count(NOTICE_DETAIL) == 1, told


@pytest.mark.parametrize("refused", [False, True], ids=["posted", "refused_then_retried"])
async def test_a_review_s_attendance_sheet_drawn_with_notices_is_framed_kept_and_listed_in_the_reply(
    tmp_path, refused,
):
    """Attendance is on, and Max is owed an autoreserve. Alex approves round 3's reports with a
    five-second penalty for Lewis, so the sanction applies and Pro's attendance sheet is posted
    twice by the review's own sheet jobs: plain before the sanction, and after it drawn with one
    notice, a flag falling back, which attendance's posting hands back marked as the drawing marks
    it: "Image render notices — Season 1 Pro — attendance after round 3:" over the notice grouped
    under its kind. Run twice: posted on the first try; and refused by Discord on the first try
    (Missing Access), the stop keeping the notice, then posted as text by a Retry. Either way the
    queue runs to the end, and the log channel carries the notice once, in one message opening
    with Alex and the button he pressed, then the heading and the grouped notice beneath, never a
    bare block; the sheet job after the sanction keeps the sheet's name and its grouped notice on
    its result and no raw fault list, the one before it no pictures; and Alex's reply ends in one
    "Image notices" section listing the sheet under that name, linked to that log message, its
    notice told once (issue #635; owner decision 2026-11-25)."""
    from leaguebot.framework.models.change import (
        RESULT_DRAWN,
        RESULT_NOTICES,
        RESULT_PICTURES,
        NoticeBlock,
        StepFailedOnDiscord,
    )
    from leaguebot.framework.utils.log_lines import notices_block
    from tests.support.change_queue import step_rows

    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    block = NoticeBlock(notices_block(_SHEET_DRAWN, _GROUPED_NOTICE))
    posted = {"new": 9001, "channel_id": 4242, "takes_slot": False, "faults": []}

    async def _post_sheet(_round_id: int, _division_id: int, *, sanctioned: Any, as_text: bool,
                          earlier_lines: tuple[str, ...] = ()) -> dict[str, Any]:
        # The sheet before the sanctions is posted plain; the one after them is drawn.
        if not sanctioned:
            return posted
        if refused and not as_text:
            raise StepFailedOnDiscord(
                "the attendance sheet could not be posted: 403 Forbidden: Missing Access"
            ).carrying((block,))
        return posted if refused else {**posted, "faults": [block]}

    league.attendance.post_sheet = _post_sheet
    interaction = await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)
    if refused:
        assert await stopped_at(league) == "attendance_sheet"
        await retry_job(league.bot)

    assert await stopped_at(league) is None
    held, message = _held(league)
    assert message.count(NOTICE_DETAIL) == 1, league.log()
    assert unruled(message) == _NOTICE_OPENING + "\n".join(
        [f"{_SHEET_DRAWN}:", *_GROUPED_NOTICE]
    ), message
    before, kept = [row["result"] for row in await step_rows(league.db_path)
                    if row["name"] == "attendance_sheet"]
    assert RESULT_PICTURES not in before, before
    assert kept[RESULT_PICTURES] == [
        {RESULT_DRAWN: _SHEET_DRAWN, RESULT_NOTICES: list(_GROUPED_NOTICE)},
    ], kept
    assert "faults" not in kept, kept
    _told_once(interaction, _SHEET_DRAWN, held)


@pytest.mark.parametrize("refused", [False, True], ids=["posted", "refused_then_retried"])
async def test_a_review_s_refreshed_lineup_drawn_with_notices_keeps_them_across_a_retry_and_links_them(
    tmp_path, refused,
):
    """Attendance is on, and Max is owed an autoreserve. Alex approves round 3's reports with a
    five-second penalty for Lewis, so the sanction applies and the review's refresh-lineup job
    posts Pro's lineup afresh. The lineup is drawn with one notice, a flag falling back. Run
    twice: posted as a picture on the first try; and on the first try Discord refusing the
    picture and then the text (Missing Access), stopping the queue, and a Retry posting the
    lineup as text, which draws no picture. Either way the queue runs to the end; the log
    channel carries the notice once, in one message opening with Alex and the button he pressed,
    then "Image render notices — lineup — Pro:" and the grouped notice beneath; the job keeps the
    lineup's notice, the refused try's included; and Alex's reply ends in one "Image notices"
    section listing "lineup — Pro" linked to that log message, its notice told once (issue #635;
    owner decision 2026-11-25)."""
    from leaguebot.core.services.drivers.lineup_posting import LINEUP_DRAWN_ATTRIBUTE
    from leaguebot.framework.models.change import (
        RESULT_DRAWN,
        RESULT_NOTICES,
        StepFailedOnDiscord,
    )
    from tests.support.change_queue import step_rows

    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    asked: list[bool] = []

    async def _refresh(division_id: int, *, as_text: bool = False) -> dict[str, Any]:
        asked.append(as_text)
        if refused and not as_text:
            stop = StepFailedOnDiscord(
                "the lineup could not be posted: 403 Forbidden: Missing Access"
            )
            setattr(stop, LINEUP_DRAWN_ATTRIBUTE, {
                "division_name": "Pro", "notices": list(_GROUPED_NOTICE),
                "problem": "Discord refused the picture (403 Forbidden: Missing Access)",
                "refused": True, "fault_source": "league",
            })
            raise stop
        return {
            "new": 9101, "old": None, "old_channel_id": None, "channel_id": 4343,
            "division_name": "Pro", "form": "text" if as_text else "image", "problem": None,
            RESULT_NOTICES: [] if as_text else list(_GROUPED_NOTICE),
            RESULT_DRAWN: "lineup — Pro", "refused": False, "fault_source": "league",
        }

    league.attendance.refresh_lineup = _refresh
    interaction = await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)
    if refused:
        assert await stopped_at(league) == "refresh_lineup"
        await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert asked == ([False, True] if refused else [False])
    held, message = _held(league)
    assert message.count(NOTICE_DETAIL) == 1, league.log()
    assert unruled(message) == _NOTICE_OPENING + "\n".join(
        ["lineup — Pro:", *_GROUPED_NOTICE]
    ), message
    [kept] = [row["result"] for row in await step_rows(league.db_path)
              if row["name"] == "refresh_lineup"]
    assert kept[RESULT_NOTICES] == list(_GROUPED_NOTICE), kept
    _told_once(interaction, "lineup — Pro", held)
