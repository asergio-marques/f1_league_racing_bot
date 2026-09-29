"""What the log channel holds for a season review's button: its refusals and its lapse (#482).

A season review ends in a public question with one button — ✅ Approve on a placements review
before the season starts, ✅ Confirm placements on one of a season being raced, ✅ Confirm
configuration on a configuration review. The core specification's "The record of what changed"
asks every outcome of it to be recorded: a press refused is one line naming the presser, the
button and its review, and a review left for its five minutes one lapse line naming the member
who ran it, with what became of it and what to do next.

A review refused because the season changed "shall end as an expired one does" ("The evidence
placements are confirmed upon"): the channel is cleared as a lapse clears it, but the log records
the press once, as the refusal. A press that hits a fault leaves the review standing, pressable
for the rest of its five minutes (owner, 2026-09-29), and the lapse it then records says an
earlier press failed rather than that nothing was done.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.season_cog import (
    _ApproveView,
    _ConfirmConfigurationView,
    _ConfirmMidSeasonPlacementsView,
)
from leaguebot.core.models.server_config import ServerConfig

#: Alex, the league manager who ran the review.
REVIEWER = 4242
#: Sam, another league manager, who may not answer Alex's review.
BYSTANDER = 99
ADMIN_ROLE = 444

#: Each review button: its view, its label without the mark, the review it belongs to, the
#: helper a press hands on to, and the verb its replies use.
_BUTTONS = [
    pytest.param(
        _ApproveView, "Approve", "/season placements-review", "_do_approve", "approved",
        id="approve",
    ),
    pytest.param(
        _ConfirmMidSeasonPlacementsView, "Confirm placements", "/season placements-review",
        "_do_confirm_mid_season_placements", "confirmed",
        id="confirm_placements",
    ),
    pytest.param(
        _ConfirmConfigurationView, "Confirm configuration", "/season config-review",
        "_do_confirm_configuration", "confirmed",
        id="confirm_configuration",
    ),
]


def _review(view_class, helper: str):
    """A review Alex ran, standing in the channel with its question bound, on the league's server.

    The bot finds Alex on the server by id, as a lapse (which has no interaction) names him, and
    every line it writes to the log channel is kept on `bot.output_router.post_log`.
    """
    cog = MagicMock()
    setattr(cog, helper, AsyncMock())
    bot = cog.bot
    bot.db_path = "/nonexistent/nowhere.db"
    bot.config_service.get_server_config = AsyncMock(
        return_value=ServerConfig(
            server_id=7,
            interaction_role_id=222,
            league_admin_role_id=ADMIN_ROLE,
            interaction_channel_id=111,
            log_channel_id=333,
        )
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=7)
    alex = MagicMock()
    alex.id = REVIEWER
    alex.display_name = "Alex"
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER else None)
    bot.get_guild = MagicMock(return_value=guild)
    bot.output_router.post_log = AsyncMock()

    view = view_class(cog, REVIEWER)
    view._server_id = 7
    view._season_id = 1
    message = MagicMock()
    message.delete = AsyncMock()
    message.channel.send = AsyncMock()
    view._message = message
    return view, cog, message


def _member(user_id: int, name: str):
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.display_name = name
    member.roles = []
    return member


def _press(cog, user_id: int = REVIEWER, name: str = "Alex"):
    """A press of the review's button, reading as Discord's does until it is answered."""
    interaction = MagicMock()
    interaction.guild_id = 7
    interaction.client = cog.bot
    interaction.user = _member(user_id, name)
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _logged(cog) -> list[str]:
    return [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]


def _changed_since_the_review(monkeypatch, area: str = "Division Pro's rounds") -> None:
    """The season as it stands differs from what the review described, in *area*."""
    from leaguebot.core.services import season_fingerprint_service as sfs

    async def _now(*_args, **_kwargs):
        return sfs.SeasonFingerprint({area: "after"})

    monkeypatch.setattr(sfs, "take_fingerprint", _now)


# ── A press refused ────────────────────────────────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_by_someone_who_may_not_answer_is_recorded(
    view_class, label, review, helper, verb
):
    """Sam, a league manager who is neither the reviewer nor a league admin, presses Alex's button.

    Sam is answered as today, privately, and the review is left alone; the log channel gets one
    refusal line naming Sam, the button and the review it belongs to.
    """
    view, cog, message = _review(view_class, helper)
    interaction = _press(cog, BYSTANDER, "Sam")

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    message.delete.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert reply.startswith("⛔ Only the person who ran this review, or a league admin, can ")
    assert f"**Nothing has been {verb}.**" in reply
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    lines = _logged(cog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ "), line
    refused, _, reason = line.partition(" refused for Sam (<@99>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert reason.startswith("Only the person who ran this review, or a league admin"), line


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_press_refused_because_the_season_changed_is_recorded_once(
    monkeypatch, view_class, label, review, helper, verb
):
    """Alex presses his own button, but the season has changed since the review was posted.

    He is refused as today, and the review ends as an expired one does: question deleted and the
    public notice posted. The log holds exactly one line for it, the refusal, naming the button,
    the review and what changed; no lapse is recorded beside it.
    """
    view, cog, message = _review(view_class, helper)
    from leaguebot.core.services.season_fingerprint_service import SeasonFingerprint

    view._fingerprint = SeasonFingerprint({"Division Pro's rounds": "before"})
    _changed_since_the_review(monkeypatch)
    interaction = _press(cog)

    await view_class.approve(view, interaction, MagicMock())

    getattr(cog, helper).assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "has changed since this review" in reply
    assert f"**Nothing has been {verb}.**" in reply
    message.delete.assert_awaited_once()
    message.channel.send.assert_awaited_once()
    lines = _logged(cog)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ "), line
    refused, _, reason = line.partition(" refused for Alex (<@4242>) — ")
    assert reason, line
    assert label in refused and review in refused, line
    assert "Division Pro's rounds" in reason, line
    assert "lapsed" not in line


# ── A review left to lapse ─────────────────────────────────────────────────


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_left_for_its_five_minutes_records_its_lapse(
    view_class, label, review, helper, verb
):
    """Alex runs a review and nobody presses its button; the five minutes run out.

    The question is deleted and today's public notice posted, and the log gets one lapse line
    naming the review and Alex as the member who started it, with beneath it what became of it
    (nothing done) and what to do next (run the review again).
    """
    view, cog, message = _review(view_class, helper)

    await view.on_timeout()

    message.delete.assert_awaited_once()
    notice = message.channel.send.await_args.args[0]
    assert "<@4242> your review has expired" in notice
    lines = _logged(cog)
    assert len(lines) == 1, lines
    head, *beneath = lines[0].splitlines()
    assert head.startswith("⌛ "), head
    assert review in head, head
    assert head.endswith("lapsed unconfirmed (started by Alex (<@4242>))"), head
    detail = "\n".join(beneath)
    assert "nothing has been" in detail.lower(), detail
    assert review in detail and "again" in detail, detail


@pytest.mark.parametrize("view_class,label,review,helper,verb", _BUTTONS)
async def test_a_review_whose_press_failed_says_so_when_it_lapses(
    view_class, label, review, helper, verb
):
    """Alex presses his button and the bot hits a fault part-way (the press raises).

    The review stays up and may be pressed again for the rest of its five minutes, as today. Left
    to lapse after that, its lapse line says an earlier press failed and may have been partly
    done, to check the failure line before running the review again, and does not claim that
    nothing was done.
    """
    view, cog, message = _review(view_class, helper)
    getattr(cog, helper).side_effect = RuntimeError("the database is locked")

    with pytest.raises(RuntimeError):
        await view_class.approve(view, _press(cog), MagicMock())

    assert not view.is_finished()
    assert view._message is message
    message.delete.assert_not_awaited()

    await view.on_timeout()

    lines = _logged(cog)
    assert len(lines) == 1, lines
    head, *beneath = lines[0].splitlines()
    assert head.startswith("⌛ "), head
    assert head.endswith("lapsed unconfirmed (started by Alex (<@4242>))"), head
    detail = "\n".join(beneath).lower()
    assert "failed" in detail and "partly" in detail, detail
    assert "nothing has been" not in detail, detail
