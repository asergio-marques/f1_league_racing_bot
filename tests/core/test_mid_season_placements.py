"""Confirming placements mid-season, in Ongoing, placements (issue #220).

The drivers of a window closed mid-season are placed uncommitted. Confirming commits them,
grants each their division's and team's roles, posts each affected lineup once, and returns the
season to Ongoing. The review is refused in the other ongoing stages.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.placement_service import PlacementService, PlacementsCommitted
from tests.support.undecorate import undecorate
from tests.attendance.test_uncommitted_drivers_outside_attendance import (
    DIVISION_ID,
    SERVER_ID,
    db_path,  # noqa: F401 — the fixture
)


def _service(db_path) -> PlacementService:
    service = PlacementService.__new__(PlacementService)
    service._db_path = db_path
    service._bot = None
    service._refresh_lineup_post = AsyncMock()
    service._grant_roles = AsyncMock()
    service.get_team_role_config = AsyncMock(return_value=SimpleNamespace(role_id=777))
    return service


def _guild():
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    return guild


# ── The service ────────────────────────────────────────────────────────────────────


async def test_the_uncommitted_placements_are_listed(db_path):
    placements = await _service(db_path).uncommitted_placements(1)

    assert [p["discord_user_id"] for p in placements] == ["1002"]
    assert placements[0]["team_name"] == "Alpha"


async def test_confirming_commits_grants_roles_and_posts_each_lineup_once(db_path):
    service = _service(db_path)

    committed = await service.commit_mid_season_placements(1, _guild())

    assert [p["discord_user_id"] for p in committed.placements] == ["1002"]
    assert await service.uncommitted_placements(1) == []
    role_ids = service._grant_roles.await_args.args[1:]
    assert role_ids == (1, 777)
    service._refresh_lineup_post.assert_awaited_once()
    assert service._refresh_lineup_post.await_args.args[1] == DIVISION_ID


async def test_confirming_with_nothing_uncommitted_does_nothing(db_path):
    service = _service(db_path)
    await service.commit_mid_season_placements(1, _guild())
    service._refresh_lineup_post.reset_mock()

    assert (await service.commit_mid_season_placements(1, _guild())).placements == []
    service._refresh_lineup_post.assert_not_awaited()


# Nothing after the commit raises (issue #387). The placements are committed by then, and a
# raise left every driver and lineup not yet reached undone for good — confirming again finds
# nothing left to commit.


async def test_a_driver_whose_team_role_cannot_be_read_is_left_ungranted_and_named(db_path):
    service = _service(db_path)
    service.get_team_role_config = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )

    committed = await service.commit_mid_season_placements(1, _guild())

    assert committed.ungranted == ["1002"]
    assert await service.uncommitted_placements(1) == []
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_lineup_that_raises_does_not_escape_the_commit(db_path):
    service = _service(db_path)
    service._refresh_lineup_post = AsyncMock(side_effect=RuntimeError("Discord is down"))

    committed = await service.commit_mid_season_placements(1, _guild())

    assert [p["discord_user_id"] for p in committed.placements] == ["1002"]
    assert committed.ungranted == []
    assert committed.unposted_lineups == [committed.placements[0]["division_name"]]


# ── The command ────────────────────────────────────────────────────────────────────


def _cog(db_path, stage: SeasonStage) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog._pending = {}
    season = SimpleNamespace(id=1, season_number=1, stage=stage)
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    cog.bot.season_service.set_stage = AsyncMock()
    cog.bot.placement_service.commit_mid_season_placements = AsyncMock(
        return_value=PlacementsCommitted(placements=[{}])
    )
    cog.bot.output_router.post_log = AsyncMock()
    # No module on: the season's lineup and calendar channels are the only ones it posts to.
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=False)
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=False)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize("stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS])
async def test_the_review_is_refused_in_the_other_ongoing_stages(db_path, stage):
    cog = _cog(db_path, stage)
    interaction = _interaction()

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert "only be reviewed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


async def test_confirming_returns_the_season_to_ongoing(db_path):
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_awaited_once()
    cog.bot.season_service.set_stage.assert_awaited_once_with(1, SeasonStage.ONGOING)


async def test_confirming_refuses_while_a_signup_is_unsettled(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 3")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()
    cog.bot.season_service.set_stage.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_refuses_a_deleted_channel_and_commits_nothing(db_path):
    """The review stands five minutes, and a channel deleted from the server meanwhile
    changes nothing the fingerprint reads (#374)."""
    import discord

    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()
    interaction.guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid == 700 else MagicMock()
    )
    interaction.guild.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
    )

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = interaction.followup.send.await_args.args[0]
    assert "**Pro**'s lineup channel is no longer on the server" in refusal
    assert "Nothing has been confirmed" in refusal
    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()
    cog.bot.season_service.set_stage.assert_not_awaited()


async def test_confirming_refuses_an_image_fault_and_commits_nothing(db_path):
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog._mid_season_configuration_faults = AsyncMock(
        return_value=["Inkscape is not installed on this host."]
    )
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = interaction.followup.send.await_args.args[0]
    assert "Inkscape is not installed on this host." in refusal
    assert "Nothing has been confirmed" in refusal
    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()


async def test_confirming_names_every_fault_at_once(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 3")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog._mid_season_configuration_faults = AsyncMock(
        return_value=["Inkscape is not installed on this host."]
    )
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = interaction.followup.send.await_args.args[0]
    assert "Unsettled signup: " in refusal
    assert "**Pro** has no lineup channel" in refusal
    assert "Inkscape is not installed on this host." in refusal


async def test_confirming_a_season_no_longer_placing_confirms_nothing(db_path):
    cog = _cog(db_path, SeasonStage.ONGOING)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()


async def test_cancelling_discards_the_uncommitted_placements_and_frees_their_seats(db_path):
    from leaguebot.core.services.season_service import SeasonService

    assert await SeasonService(db_path).discard_uncommitted_placements(1) == 1

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0
    assert await _service(db_path).uncommitted_placements(1) == []


# ── The mid-season report ──────────────────────────────────────────────────────────


class _RecordedView:
    made: list["_RecordedView"] = []

    def __init__(self, cog, reviewer_id):
        self.record_fingerprint = AsyncMock()
        self.bind = AsyncMock()
        self.carries = MagicMock()
        _RecordedView.made.append(self)


def _report_cog(*, placements, unsettled=(), channels=(), configuration=(), divisions=None):
    cog = _cog(":memory:", SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.placement_service.uncommitted_placements = AsyncMock(return_value=placements)
    cog.bot.season_service.get_divisions = AsyncMock(
        return_value=divisions
        or [
            SimpleNamespace(id=DIVISION_ID, name="Pro", status="ACTIVE"),
            SimpleNamespace(id=99, name="Old", status="CANCELLED"),
        ]
    )
    cog.bot.team_service.get_division_teams = AsyncMock(
        return_value=[
            {"name": "Alpha", "seats": [{"discord_user_id": "1001"}, {"discord_user_id": "1002"}]},
            {"name": "Reserve", "seats": [{"discord_user_id": None}]},
        ]
    )
    cog._placement_confirmation_faults = AsyncMock(
        return_value=(list(unsettled), list(channels))
    )
    cog._mid_season_configuration_faults = AsyncMock(return_value=list(configuration))
    return cog


def _placement(uid: str, *, division_id: int = DIVISION_ID, team="Alpha", test_name=None):
    return {
        "test_display_name": test_name, "discord_user_id": uid, "team_name": team,
        "team_full_name": f"{team} Racing" if team != "Reserve" else team,
        "division_id": division_id, "division_name": "Pro",
    }


async def _mid_season_report(cog, monkeypatch):
    import leaguebot.core.cogs.season_cog as season_cog

    _RecordedView.made = []
    monkeypatch.setattr(season_cog, "_ConfirmMidSeasonPlacementsView", _RecordedView)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    sent = interaction.followup.send
    await undecorate(SeasonCog.season_review)(cog, interaction)
    return [(call.args[0], bool(call.kwargs.get("ephemeral"))) for call in sent.await_args_list]


async def test_the_mid_season_review_names_each_driver_to_confirm_and_offers_the_button(monkeypatch):
    cog = _report_cog(
        placements=[
            _placement("1002"),
            _placement("9000", team="Reserve", test_name="Test Bravo"),
        ]
    )

    messages = await _mid_season_report(cog, monkeypatch)
    text = "\n".join(m for m, _ in messages)

    assert "Placements Review (Season #1) — mid-season" in text
    assert "<@1002> → **Alpha Racing** in **Pro**" in text
    assert "Test Bravo → **Reserve** in **Pro**" in text
    assert "**Alpha**: <@1001>, <@1002>" in text
    assert "**Reserve**: *(empty)*" in text
    assert "Old" not in text, "a cancelled division has no lineup to confirm"
    assert "Do you confirm these placements?" in messages[-1][0]
    (view,) = _RecordedView.made
    view.record_fingerprint.assert_awaited_once_with(1)
    view.bind.assert_awaited_once()


async def test_a_mid_season_review_with_nothing_new_says_so(monkeypatch):
    cog = _report_cog(placements=[])

    messages = await _mid_season_report(cog, monkeypatch)

    assert "*No new placement to confirm.*" in messages[0][0]


async def test_an_unsettled_signup_withholds_the_mid_season_button(monkeypatch):
    cog = _report_cog(placements=[], unsettled=["**Racer** — awaiting approval"])

    messages = await _mid_season_report(cog, monkeypatch)

    refusal, ephemeral = messages[-1]
    assert ephemeral
    assert "Every signup must be settled" in refusal
    listing = [text for text, private in messages if not private and "Unsettled signups" in text]
    assert listing and "**Racer** — awaiting approval" in listing[0]
    assert _RecordedView.made == []


# ── What else withholds the mid-season button (#374) ───────────────────────────────


def _private(messages) -> str:
    return "\n".join(text for text, private in messages if private)


async def test_a_deleted_channel_withholds_the_mid_season_button(monkeypatch):
    """Nothing clears a channel's id when Discord deletes it, and the confirmation posts each
    affected lineup: a lineup channel gone would take the post with it, the log alone told."""
    gone = "**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`."
    cog = _report_cog(placements=[_placement("1002")], channels=[gone])

    messages = await _mid_season_report(cog, monkeypatch)

    assert "Every division needs every channel it posts to" in _private(messages)
    assert gone in _private(messages)
    assert _RecordedView.made == []


async def test_the_channels_are_judged_upon_the_review_s_own_server(monkeypatch):
    cog = _report_cog(placements=[_placement("1002")])
    import leaguebot.core.cogs.season_cog as season_cog

    monkeypatch.setattr(season_cog, "_ConfirmMidSeasonPlacementsView", _RecordedView)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(return_value=MagicMock())

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert cog._placement_confirmation_faults.await_args.args == (1, interaction.guild)


async def test_an_image_fault_withholds_the_mid_season_button(monkeypatch):
    """Templates, artwork and image settings may all change while a season is raced."""
    cog = _report_cog(
        placements=[_placement("1002")],
        configuration=["Template **Lineup**: the file is missing."],
    )

    messages = await _mid_season_report(cog, monkeypatch)

    assert "image module is not correctly configured" in _private(messages)
    assert "Template **Lineup**: the file is missing." in _private(messages)
    assert _RecordedView.made == []


async def test_every_fault_is_named_at_once(monkeypatch):
    cog = _report_cog(
        placements=[_placement("1002")],
        unsettled=["**Racer** — awaiting approval"],
        channels=["**Pro** has no lineup channel — `/division lineup-channel`."],
        configuration=["Inkscape is not installed on this host."],
    )

    private = _private(await _mid_season_report(cog, monkeypatch))

    assert "Every signup must be settled" in private
    assert "Every division needs every channel" in private
    assert "Inkscape is not installed" in private


async def test_the_new_drivers_lineup_is_drawn_with_them_in_it_and_in_place_of_its_text(
    monkeypatch,
):
    """Only a division holding a new driver has its lineup posted by confirming, so only its
    graphic is drawn here — with the new drivers in it, as it will be posted."""
    import leaguebot.core.cogs.season_cog as season_cog

    divisions = [
        SimpleNamespace(id=DIVISION_ID, name="Pro", status="ACTIVE"),
        SimpleNamespace(id=77, name="Am", status="ACTIVE"),
    ]
    cog = _report_cog(placements=[_placement("1002")], divisions=divisions)
    cog._prerender_mid_season_lineups = AsyncMock()
    cog._post_review_lineup_image = AsyncMock(return_value=season_cog.REVIEW_IMAGE_DREW)

    messages = await _mid_season_report(cog, monkeypatch)
    text = "\n".join(m for m, _ in messages)

    (call,) = cog._post_review_lineup_image.await_args_list
    assert call.args[1].id == DIVISION_ID
    assert call.kwargs["include_uncommitted"] is True
    assert "**Pro** — lineup once confirmed" not in text, "the graphic stands in its place"
    assert "**Am** — lineup once confirmed" in text, "a division with no new driver stays text"
    assert len(_RecordedView.made) == 1


async def test_a_lineup_that_will_not_draw_withholds_the_mid_season_button(monkeypatch):
    """Confirming would post it, and a lineup that falls back to text is a fault the manager
    reading this is the one person able to fix."""
    import leaguebot.core.cogs.season_cog as season_cog

    cog = _report_cog(placements=[_placement("1002")])
    cog._prerender_mid_season_lineups = AsyncMock()
    cog._post_review_lineup_image = AsyncMock(return_value=season_cog.REVIEW_IMAGE_FAULT)

    messages = await _mid_season_report(cog, monkeypatch)
    text = "\n".join(m for m, _ in messages)

    assert "could not be drawn" in _private(messages)
    assert "**Pro** — lineup once confirmed" in text, "the text stands in for the graphic"
    assert _RecordedView.made == []


async def test_the_lineups_are_drawn_first_with_their_new_drivers(monkeypatch):
    import leaguebot.image.services.image_lineup_post as post

    cog = _report_cog(placements=[_placement("1002")])
    outcome = SimpleNamespace(png_path=None)
    render = AsyncMock(return_value=outcome)
    monkeypatch.setattr(post, "lineup_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(post, "render_for_command", render)
    interaction = _interaction()
    interaction.channel.send = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
    prepared: dict = {}

    await cog._prerender_mid_season_lineups(
        interaction, [SimpleNamespace(id=DIVISION_ID)], prepared
    )

    render.assert_awaited_once_with(
        cog.bot,
        interaction.guild,
        DIVISION_ID,
        include_uncommitted=True,
        obtain_missing_portraits=True,
    )


async def test_nothing_is_drawn_or_announced_where_the_lineup_graphic_is_off(monkeypatch):
    import leaguebot.image.services.image_lineup_post as post

    cog = _report_cog(placements=[_placement("1002")])
    render = AsyncMock()
    monkeypatch.setattr(post, "lineup_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(post, "render_for_command", render)
    interaction = _interaction()
    interaction.channel.send = AsyncMock()

    await cog._prerender_mid_season_lineups(interaction, [SimpleNamespace(id=DIVISION_ID)], {})

    render.assert_not_awaited()
    interaction.channel.send.assert_not_awaited()


async def test_only_the_image_checks_of_the_configuration_review_are_repeated():
    """The season settled the rest (#374): its roles, its signup configuration and its team
    list are fixed, and its points were recorded upon it. A server points configuration
    removed or reordered mid-season must not stop drivers being placed."""
    cog = _cog(":memory:", SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=True)
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    cog.bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    cog._image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])
    cog._missing_points_config_problems = AsyncMock(return_value=["Standrad"])
    cog._points_ordering_problems = AsyncMock(return_value=["P2 beats P1"])
    cog._team_name_problems = AsyncMock(return_value=["a bad name"])

    assert await cog._mid_season_configuration_faults() == ["Inkscape is not installed."]
    cog._missing_points_config_problems.assert_not_awaited()
    cog._points_ordering_problems.assert_not_awaited()


async def test_no_image_check_is_made_while_the_module_is_off():
    cog = _cog(":memory:", SeasonStage.ONGOING_PLACEMENTS)
    cog._image_configuration_faults = AsyncMock(return_value=["Inkscape is not installed."])

    assert await cog._mid_season_configuration_faults() == []


async def test_confirming_after_the_season_moved_on_still_reports_the_placements(db_path):
    """The placements are committed either way; only the stage move is skipped."""
    from leaguebot.core.models.season import InvalidStageTransition

    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.season_service.set_stage = AsyncMock(side_effect=InvalidStageTransition("moved"))
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_awaited_once()
    assert "1 placement(s) confirmed" in interaction.followup.send.await_args.args[0]
    assert "placements: 1" in cog.bot.output_router.post_log.await_args.args[0]


# ── Nothing after the commit escapes the confirmation (issue #387) ─────────────────
#
# The placements are committed before the season moves, the reply goes out or the log is
# written, so a raise from any of those reached the view's error handler: the manager was
# told the confirmation did not finish, and the review stood until it expired.


async def _settle_every_signup(db_path) -> None:
    """Every signup settled, and the division given the channels it posts to (#374), so the
    confirmation has nothing to refuse on."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'ASSIGNED'")
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = 700, calendar_channel_id = 701"
        )
        await db.commit()


def _token_lapsed():
    """What Discord answers a follow-up sent after the interaction's fifteen minutes."""
    import discord

    return discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Webhook")


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


def _logged(cog) -> str:
    return "\n".join(
        str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list
    )


def _leave_a_driver_ungranted(cog, interaction, monkeypatch) -> None:
    cog.bot.placement_service.commit_mid_season_placements = AsyncMock(
        return_value=PlacementsCommitted(placements=[{}], ungranted=["1002"])
    )


def _fail_the_return_to_ongoing(cog, interaction, monkeypatch) -> None:
    cog.bot.season_service.set_stage = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )


def _fail_the_move_to_pending_completion(cog, interaction, monkeypatch) -> None:
    import leaguebot.core.services.season_lifecycle_service as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "advance_to_pending_completion",
        AsyncMock(side_effect=sqlite3.OperationalError("database is locked")),
    )


def _refuse_the_reply(cog, interaction, monkeypatch) -> None:
    interaction.followup.send = AsyncMock(side_effect=_token_lapsed())


def _fail_the_log_line(cog, interaction, monkeypatch) -> None:
    cog.bot.output_router.post_log = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )


#: Each step after the commit, made to fail.
_FAILURES = {
    "drivers left ungranted": _leave_a_driver_ungranted,
    "the return to Ongoing": _fail_the_return_to_ongoing,
    "the move to Pending completion": _fail_the_move_to_pending_completion,
    "the reply": _refuse_the_reply,
    "the log line": _fail_the_log_line,
}


@pytest.mark.parametrize("failure", sorted(_FAILURES))
async def test_nothing_after_the_commit_escapes_the_confirmation(
    db_path, monkeypatch, failure
):
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()
    interaction.channel.send = AsyncMock()
    _FAILURES[failure](cog, interaction, monkeypatch)

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_awaited_once()
    assert "placement(s) confirmed" in _replied(interaction)
    assert "| Confirmed" in _logged(cog)


async def test_a_season_not_returned_to_ongoing_names_the_repair(db_path):
    """The review still offers its button with nothing left to commit, and confirming then
    makes the move alone — so that is what the manager is told to do."""
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.season_service.set_stage = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    replied = _replied(interaction)
    assert "Not everything could be done" in replied
    assert "could not be returned to Ongoing" in replied
    assert "`/season placements-review` and confirm again" in replied
    assert "ongoing again" not in replied
    assert "not done: The season could not be returned to Ongoing" in _logged(cog)


async def test_ungranted_drivers_are_named_in_the_reply_and_the_log(db_path):
    """Confirming again finds nothing left to commit, so nothing would grant them later."""
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.placement_service.commit_mid_season_placements = AsyncMock(
        return_value=PlacementsCommitted(placements=[{}], ungranted=["1002", "1003"])
    )
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    assert "<@1002>, <@1003> — their roles could not be granted" in _replied(interaction)
    assert "not done: <@1002>, <@1003>" in _logged(cog)


async def test_a_lineup_the_confirmation_could_not_post_is_named(db_path):
    """No command posts a lineup again, so the line says when it will be."""
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.placement_service.commit_mid_season_placements = AsyncMock(
        return_value=PlacementsCommitted(placements=[{}], unposted_lineups=["Pro"])
    )
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    assert "**Pro** — its lineup could not be posted" in _replied(interaction)
    assert "the next change to its drivers" in _replied(interaction)
    assert "not done: **Pro** — its lineup could not be posted" in _logged(cog)


async def test_a_clean_confirmation_reports_nothing_undone(db_path):
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    assert "The season is ongoing again" in _replied(interaction)
    assert "Not everything could be done" not in _replied(interaction)
    assert "not done" not in _logged(cog)


async def test_a_refused_confirmation_is_told_in_the_channel_instead(db_path):
    """As at approval (decided 2026-09-22): one line in the review's channel, through the
    bot's own token, pointing at the log channel."""
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()
    interaction.followup.send = AsyncMock(side_effect=_token_lapsed())
    interaction.channel.send = AsyncMock()

    await cog._do_confirm_mid_season_placements(interaction)

    told = str(interaction.channel.send.await_args.args[0])
    assert "<@42> — the placements are confirmed and the season is ongoing again" in told
    assert "the log channel has what it said" in told


async def test_a_stumbled_confirmation_still_clears_its_review(db_path):
    """Driven through the button itself, over the real confirmation."""
    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView

    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.season_service.set_stage = AsyncMock(
        side_effect=sqlite3.OperationalError("database is locked")
    )
    view = _ConfirmMidSeasonPlacementsView(cog, 42)
    view._season_id = 1
    report = [MagicMock(delete=AsyncMock()), MagicMock(delete=AsyncMock())]
    view.carries(report)
    view._message = prompt = MagicMock(delete=AsyncMock())
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_review_prompts "
            "(id, season_id, channel_id, message_id, reviewer_id, posted_at) "
            "VALUES (1, 1, 700, 800, 42, '2026-03-01T00:00:00+00:00')"
        )
        await db.commit()
    interaction = _interaction()

    await _ConfirmMidSeasonPlacementsView.approve(view, interaction, MagicMock())

    assert "placement(s) confirmed" in _replied(interaction)
    for message in [*report, prompt]:
        message.delete.assert_awaited_once()
    assert view.is_finished()
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        assert (await cursor.fetchone())[0] == 0


# ── The mid-season button ──────────────────────────────────────────────────────────


REVIEWER = 42
ADMIN_ROLE = 444


def _view():
    import discord  # noqa: F401 — the view is built on a running loop

    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView

    cog = MagicMock()
    cog._do_confirm_mid_season_placements = AsyncMock()
    cog.bot.db_path = "/nonexistent/nowhere.db"
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(league_admin_role_id=ADMIN_ROLE)
    )
    view = _ConfirmMidSeasonPlacementsView(cog, REVIEWER)
    view._server_id = SERVER_ID
    view._season_id = 1
    view._forget = AsyncMock()
    view._clear_report = AsyncMock()
    view._expire_now = AsyncMock()
    return view, cog


def _pressed_by(user_id: int):
    import discord

    interaction = _interaction()
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.roles = []
    interaction.user = member
    return interaction


async def test_the_reviewer_confirms_the_mid_season_placements():
    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView

    view, cog = _view()
    interaction = _pressed_by(REVIEWER)

    await _ConfirmMidSeasonPlacementsView.approve(view, interaction, MagicMock())

    cog._do_confirm_mid_season_placements.assert_awaited_once_with(interaction)
    view._clear_report.assert_awaited_once()


async def test_another_league_manager_may_not_confirm_the_mid_season_placements():
    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView

    view, cog = _view()
    interaction = _pressed_by(99)

    await _ConfirmMidSeasonPlacementsView.approve(view, interaction, MagicMock())

    cog._do_confirm_mid_season_placements.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.response.send_message.await_args.args[0]


async def test_mid_season_placements_changed_since_the_review_are_not_confirmed(monkeypatch):
    import leaguebot.core.services.season_fingerprint_service as fingerprints
    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView

    view, cog = _view()
    view._fingerprint = MagicMock()
    view._fingerprint.differs_from = MagicMock(return_value=["the seated drivers"])
    monkeypatch.setattr(fingerprints, "take_fingerprint", AsyncMock(return_value=MagicMock()))
    interaction = _pressed_by(REVIEWER)

    await _ConfirmMidSeasonPlacementsView.approve(view, interaction, MagicMock())

    cog._do_confirm_mid_season_placements.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "• the seated drivers" in reply
    assert "`/season placements-review`" in reply
    view._expire_now.assert_awaited_once()


# ── Committing mid-season: who gets roles ─────────────────────────────────────────


async def test_a_test_driver_is_committed_without_roles(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET is_test_driver = 1 WHERE id = 2")
        await db.commit()
    service = _service(db_path)

    committed = await service.commit_mid_season_placements(1, _guild())

    assert len(committed.placements) == 1
    service._grant_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_awaited_once()


async def test_a_member_not_cached_is_fetched_for_their_roles(db_path):
    service = _service(db_path)
    member = MagicMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=member)

    await service.commit_mid_season_placements(1, guild)

    assert service._grant_roles.await_args.args[0] is member


async def test_a_member_who_left_is_committed_and_the_lineup_still_posted(db_path):
    import discord

    service = _service(db_path)
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )

    committed = await service.commit_mid_season_placements(1, guild)

    assert len(committed.placements) == 1
    assert await service.uncommitted_placements(1) == []
    service._grant_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_awaited_once()
