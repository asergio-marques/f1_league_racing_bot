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

from leaguebot.core.db.database import get_connection
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.verdict_announcement_service import (
    NO_FURTHER_ACTION,
    describe_penalty,
)
from tests.support.change_queue import (
    attach_queue,
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    AMEND_CHANNEL,
    DIVISION_ID,
    HEADING,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    NOW,
    PROMPT,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    channel,
    one,
    penalty,
    review_league,
    stopped_at,
    verdict_headings,
)

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
    from leaguebot.image.services import image_verdict_post
    from leaguebot.image.services.image_verdict_post import VerdictRender
    from leaguebot.image.services.image_verdict_service import VerdictDrawing

    png = tmp_path / "verdict.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    built: list[dict[str, Any]] = []

    async def _enabled(_bot: Any) -> bool:
        return True

    async def _build(_bot: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return VerdictDrawing(
            kind=kwargs["kind"], season_number=kwargs["season_number"],
            division_name=kwargs["division_name"], round_number=kwargs["round_number"],
            session_name=kwargs["session_label"], driver_name=kwargs["driver_name"],
            team_name=kwargs.get("team_name"), penalty=kwargs["penalty_description"],
            description=kwargs["description_text"], justification=kwargs["justification_text"],
        )

    async def _render(_bot: Any, _drawing: Any, **_kwargs: Any) -> Any:
        return VerdictRender(png=png, notices=[], problem=None)

    async def _quiet(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _team(_bot: Any, _guild: Any, **_kwargs: Any) -> str:
        return "Red Bull"

    monkeypatch.setattr(image_verdict_post, "verdicts_enabled", _enabled)
    monkeypatch.setattr(image_verdict_post, "build_drawing", _build)
    monkeypatch.setattr(image_verdict_post, "render_verdict", _render)
    monkeypatch.setattr(image_verdict_post, "report", _quiet)
    monkeypatch.setattr(image_verdict_post, "report_notices", _quiet)
    monkeypatch.setattr(image_verdict_post, "team_name_for_entry", _team)
    return built


# ---------------------------------------------------------------------------
# What a verdict says
# ---------------------------------------------------------------------------


async def test_a_report_verdict_names_the_round_the_driver_the_penalty_and_the_incident(
    tmp_path,
):
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
            "INSERT INTO signup_records (discord_user_id, discord_username, server_display_name) "
            "VALUES (?, 'lewis44', 'Signed Up Lewis')",
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


#: The change `/attendance sync` asks for the cards of the sanctions it applied (C37).
SANCTIONS_ANNOUNCE = "attendance.sanctions.announce"


async def _announce_lewis_s_autoreserve(league: ReviewLeague) -> None:
    """Hand the change types attendance's real hook, then ask the queue, as the bot and as the
    sync asks it, to announce Lewis's move to reserve in round 3 of Pro, and run it."""
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.review_league import candidate

    _real_attendance_hook(league)
    await league.bot.change_queue.ask(
        SANCTIONS_ANNOUNCE,
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID, "division": "Pro",
            "sanctions": [candidate(LEWIS_PROFILE, LEWIS)],
        },
        what="announcing round 3's attendance sanctions",
        origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)


async def test_a_sync_s_sanction_card_is_drawn_under_the_driver_s_server_name(tmp_path, drawn):
    """Attendance is on with its real hook, and the verdict graphic is on. A sync has moved
    Lewis (101), known to the server as "Lewis", to reserve, and asks the queue to announce it:
    the queue runs whole, and the sanction's card is drawn under "Lewis", never his user id
    (#141; image spec: a graphic names a person by their display name on the server)."""
    league = await review_league(tmp_path, attendance=True)

    await _announce_lewis_s_autoreserve(league)

    assert await stopped_at(league) is None
    assert [item["driver_name"] for item in drawn] == ["Lewis"]
    assert str(LEWIS) not in drawn[0]["driver_name"]


async def test_a_sync_s_sanction_card_as_text_mentions_the_driver(tmp_path):
    """As above, but with image generation off: the card is posted as text in Pro's verdicts
    channel (704), mentioning Lewis as <@101> and saying he was moved to reserve."""
    league = await review_league(tmp_path, attendance=True)

    await _announce_lewis_s_autoreserve(league)

    assert await stopped_at(league) is None
    [card] = _verdicts(league)
    assert f"<@{LEWIS}>" in card
    assert "Moved to Reserve" in card


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
    "Heading stops the queue"). Once a league admin discards it, both verdicts are announced
    beneath no heading, and the approval is recorded as incomplete."""
    league = await review_league(tmp_path)
    league.channel(VERDICTS_CHANNEL).fail_when = lambda content, _kwargs: content == HEADING
    interaction = await _approve_reports(league, [penalty(LEWIS), penalty(MAX)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    assert league.sent_to(VERDICTS_CHANNEL) == []
    assert await _announced(league) == 0

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
    from leaguebot.image.services import image_verdict_banner_post

    real = image_verdict_banner_post.try_post
    broken = {"on": True}

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        if broken["on"]:
            raise RuntimeError("the banner could not be posted")
        return await real(*args, **kwargs)

    monkeypatch.setattr(image_verdict_banner_post, "try_post", _boom)
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
    from leaguebot.image.services import image_verdict_banner_post as banner

    async def _enabled(_bot: Any) -> bool:
        return True

    async def _render(_bot: Any, _drawing: Any, **_kwargs: Any) -> Any:
        return banner.BannerRender(png=None, problem="RASTERISER")

    async def _quiet(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(banner, "banner_enabled", _enabled)
    monkeypatch.setattr(banner, "render_banner", _render)
    monkeypatch.setattr(banner, "report", _quiet)
    monkeypatch.setattr(banner, "report_notices", _quiet)
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
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    league = await review_league(tmp_path, attendance=True)
    await seed(league)
    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "refresh_lineup", "guard_move", "sack_on",
                 "move_on"):
        setattr(placement, name, AsyncMock(return_value=None))
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)

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


def _real_attendance_hook(league: ReviewLeague) -> None:
    """Hand the change types attendance's real hook, `AttendanceAfterReview`, with a placement
    service that does nothing, so that the sheet is posted by `post_attendance_sheet` itself."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "refresh_lineup", "guard_move", "sack_on",
                 "move_on"):
        setattr(placement, name, AsyncMock(return_value=None))
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)


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
    league = await review_league(tmp_path, attendance=True)
    _real_attendance_hook(league)

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    assert "/attendance channel attendance" in _sheet_stop(league)


async def test_an_amendment_s_attendance_sheet_with_no_channel_stops_the_queue(tmp_path):
    """Round 3 of Pro, final, is under an amendment of its Feature Race whose report stage is
    approved, its appeals prompt standing in the amendment channel. Attendance is on and Pro was
    never given an attendance channel. Alex approves the amendment's appeals stage with nothing
    staged: the rebuild stops the queue at the sheet, naming the command that sets the channel."""
    league = await review_league(tmp_path, attendance=True, round_status="FINAL")
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
    _real_attendance_hook(league)

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


async def test_a_review_s_lineup_with_no_channel_stops_the_queue_naming_the_command(tmp_path):
    """Attendance is on, with its real hook and the real placement service (roles granted and
    revoked by doubles), and division 11 (Pro) has an attendance channel but was never given a
    lineup channel. Pro sets an
    autoreserve threshold of one point and has a Reserve team with a free seat; Lewis, raced
    round 4 without an RSVP, is on the threshold. Alex approves round 3's reports: Lewis is moved
    to the Reserve team, and the queue stops at the lineup the sanction owes, its stop line
    naming the command that sets the channel, rather than the lineup completing having posted
    nothing."""
    from leaguebot.core.services.placement_service import PlacementService
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    league = await review_league(tmp_path, attendance=True)
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
    league.bot.user = MagicMock(id=9000)
    league.bot.user.__str__.return_value = "LeagueBot"
    placement = PlacementService(league.db_path, league.bot)
    placement._grant_roles = AsyncMock()
    placement._revoke_roles = AsyncMock()
    placement.grant_roles = AsyncMock()
    placement.revoke_roles = AsyncMock()
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)

    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) == "refresh_lineup"
    assert "/division lineup-channel" in _sheet_stop(league)


#: Pro's team 3001's role, and its Reserve team's.
TEAM_ROLE, RESERVE_ROLE = 5502, 5503


async def test_a_review_s_sanction_whose_role_change_discord_refuses_stops_the_queue(tmp_path):
    """Attendance is on, with its real hook and the real placement service. Pro sets an
    autoreserve threshold of one point and has a Reserve team with a free seat; Lewis, placed in
    Team 3001 and raced round 4 without an RSVP, is on the threshold. Team 3001 and the Reserve
    team each have a role, and Discord refuses every role change for Lewis (Missing
    Permissions). Alex approves round 3's reports: Lewis is moved to the Reserve team, and the
    queue stops at the job changing his roles, its notice naming it, rather than the approval
    going on as though his roles had been changed."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
    from leaguebot.core.services.placement_service import PlacementService

    league = await review_league(tmp_path, attendance=True)
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
            "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
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
    league.bot.user = MagicMock(id=9000)
    league.bot.user.__str__.return_value = "LeagueBot"
    placement = PlacementService(league.db_path, league.bot)
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)

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
