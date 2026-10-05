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

The league is `tests.support.review_league`'s: round 3 of division 11 (Pro), Lewis (101) its
Feature Race winner, image generation off unless `drawn` switches the verdict graphic on. The
approvals are asked as the review's controls ask them, and the change types are imported by the
builder, so this file collects while they are unbuilt.
"""

from __future__ import annotations

import json
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
    discard_job,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    DIVISION_ID,
    HEADING,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    PROMPT,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    one,
    penalty,
    review_league,
    stopped_at,
    verdict_headings,
)

NOT_BUILT = "#439: a round's verdicts are not yet announced by jobs on the queue"
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_report_verdict_is_drawn_under_the_driver_s_server_name(tmp_path, drawn):
    league = await review_league(tmp_path)
    await _approve_reports(league, [penalty(LEWIS)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Lewis"]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_appeal_verdict_is_drawn_under_the_driver_s_server_name(tmp_path, drawn):
    league = await _appeals_league(tmp_path)
    await _approve_appeals(league, [penalty(LEWIS, seconds=-5)])
    await run_queue(league.bot)

    assert [item["driver_name"] for item in drawn] == ["Lewis"]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_test_driver_is_drawn_under_its_test_name(tmp_path, drawn):
    league = await review_league(tmp_path)
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
