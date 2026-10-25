"""Confirming placements mid-season, in Ongoing, placements (issue #220).

The drivers of a window closed mid-season are placed uncommitted. Confirming them is asked of the
change queue (#439, slice 8, Part B): the press is answered at once, refused there where the
season no longer places drivers, a signup is unsettled, a channel is missing or the images cannot
be drawn, and otherwise asks `season.placements.confirm`, which commits them, returns the season
to Ongoing, grants each their division's and team's roles and posts each affected lineup
(`test_mid_season_confirm_change.py`). The review is refused in the other ongoing stages.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.placement_service import PlacementService
from tests.support.change_queue import acknowledgement
from tests.support.season_league import PLACEMENTS_CONFIRM_KIND as KIND
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


# ── The service ────────────────────────────────────────────────────────────────────


async def test_the_uncommitted_placements_are_listed(db_path):
    placements = await _service(db_path).uncommitted_placements(1)

    assert [p["discord_user_id"] for p in placements] == ["1002"]
    assert placements[0]["team_name"] == "Alpha"


# ── The command ────────────────────────────────────────────────────────────────────


def _cog(db_path, stage: SeasonStage) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog._pending = {}
    season = SimpleNamespace(id=1, season_number=1, stage=stage)
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    cog.bot.output_router.post_log = AsyncMock()
    cog.bot.change_queue.ask = AsyncMock()
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


def _pressing():
    """A press of ✅ Confirm placements in channel 610, not yet answered: answering it, by its
    response or by deferring, marks it answered, as Discord's does."""
    interaction = _interaction()
    interaction.channel_id = 610
    interaction.message = None
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    return interaction


def _asked(cog) -> list[dict]:
    """The payload of every confirmation of placements the cog asked of the change queue."""
    return [call.args[1] for call in cog.bot.change_queue.ask.await_args_list
            if call.args and call.args[0] == KIND]


async def _uncommitted(db_path) -> list[str]:
    return [p["discord_user_id"] for p in await _service(db_path).uncommitted_placements(1)]


@pytest.mark.parametrize("stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS])
async def test_the_review_is_refused_in_the_other_ongoing_stages(db_path, stage):
    cog = _cog(db_path, stage)
    interaction = _interaction()
    interaction.response.is_done = MagicMock(return_value=False)

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert "only be reviewed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


async def test_confirming_returns_the_season_to_ongoing(db_path):
    """Season 1 in Ongoing, placements, every signup settled and its channels set, driver 1002
    placed in Pro and not yet committed. A manager presses ✅ Confirm placements on a review whose
    evidence reads Pro's rounds "as reviewed", in channel 610: the press is not deferred, and asks
    the change queue once for the confirmation of season 1, carrying the review's evidence and the
    channel, which returns the season to Ongoing when it runs. Nothing is committed on the spot."""
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _pressing()

    await cog._do_confirm_mid_season_placements(
        interaction, fingerprint={"rounds of Pro": "as reviewed"}
    )

    assert _asked(cog) == [{
        "season_id": 1, "season_number": 1,
        "fingerprint": {"rounds of Pro": "as reviewed"}, "channel_id": 610,
    }]
    assert interaction.response.defer.await_count == 0
    assert await _uncommitted(db_path) == ["1002"]


async def test_confirming_refuses_while_a_signup_is_unsettled(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 3")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _pressing()

    await cog._do_confirm_mid_season_placements(interaction)

    assert _asked(cog) == []
    assert await _uncommitted(db_path) == ["1002"]
    assert "Nothing has been confirmed" in acknowledgement(interaction)


async def test_confirming_refuses_a_deleted_channel_and_commits_nothing(db_path):
    """The review stands five minutes, and a channel deleted from the server meanwhile
    changes nothing the fingerprint reads (#374)."""
    import discord

    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _pressing()
    interaction.guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid == 700 else MagicMock()
    )
    interaction.guild.fetch_channel = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
    )

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = acknowledgement(interaction)
    assert "**Pro**'s lineup channel is no longer on the server" in refusal
    assert "Nothing has been confirmed" in refusal
    assert _asked(cog) == []
    assert await _uncommitted(db_path) == ["1002"]


async def test_confirming_refuses_an_image_fault_and_commits_nothing(db_path):
    await _settle_every_signup(db_path)
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog._mid_season_configuration_faults = AsyncMock(
        return_value=["Inkscape is not installed on this host."]
    )
    interaction = _pressing()

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = acknowledgement(interaction)
    assert "Inkscape is not installed on this host." in refusal
    assert "Nothing has been confirmed" in refusal
    assert _asked(cog) == []
    assert await _uncommitted(db_path) == ["1002"]


async def test_confirming_names_every_fault_at_once(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 3")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    cog._mid_season_configuration_faults = AsyncMock(
        return_value=["Inkscape is not installed on this host."]
    )
    interaction = _pressing()

    await cog._do_confirm_mid_season_placements(interaction)

    refusal = acknowledgement(interaction)
    assert "Unsettled signup: " in refusal
    assert "**Pro** has no lineup channel" in refusal
    assert "Inkscape is not installed on this host." in refusal
    assert _asked(cog) == []


async def test_confirming_a_season_no_longer_placing_confirms_nothing(db_path):
    cog = _cog(db_path, SeasonStage.ONGOING)
    interaction = _pressing()

    await cog._do_confirm_mid_season_placements(interaction)

    assert "The season is no longer placing drivers." in acknowledgement(interaction)
    assert _asked(cog) == []
    assert await _uncommitted(db_path) == ["1002"]


async def test_cancelling_discards_the_uncommitted_placements_and_frees_their_seats(db_path):
    from leaguebot.core.services.season_service import discard_uncommitted_placements_on

    async with get_connection(db_path) as db:
        assert await discard_uncommitted_placements_on(db, 1) == 1
        await db.commit()

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0
    assert await _service(db_path).uncommitted_placements(1) == []


# ── The mid-season report ──────────────────────────────────────────────────────────
#
# The review is asked of the change queue (#439, slice 6b, C95; decision 5, "Whole review on
# the queue"). The command computes the report as it always did and asks `season.review.post`
# once, with the manager's interaction, to post it in the command's channel: the report's
# sections, each lineup picture the league will see in place of its text, and the question
# where it is offered. It builds no view and posts nothing public itself, so what a test reads
# as the public review is that request; the private reasons are still the command's
# follow-ups. A lineup that will not draw is found when the review's change draws it, and
# withholds the button there (`test_season_review_changes.py`).

POST = "season.review.post"
#: Today's drawing helpers of the mid-season review, stood down so the request is all a test
#: reads; they are gone once the review is asked of the queue.
_TODAY_S_DRAWING = (
    "_prerender_mid_season_lineups",
    "_post_review_lineup_image",
    "_discard_prepared_review_images",
)


class _RecordedView:
    """Stands in for the mid-season review's view, which the command no longer builds."""

    made: list["_RecordedView"] = []

    def __init__(self, cog, reviewer_id):
        self.record_fingerprint = AsyncMock()
        self.bind = AsyncMock()
        self.carries = MagicMock()
        _RecordedView.made.append(self)


def _report_cog(*, placements, unsettled=(), channels=(), configuration=(), divisions=None,
                images=False):
    import leaguebot.core.cogs.season_cog as season_cog

    cog = _cog(":memory:", SeasonStage.ONGOING_PLACEMENTS)
    cog.bot.module_service.is_images_enabled = AsyncMock(return_value=images)
    cog.bot.change_queue.ask = AsyncMock(return_value=1)
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
    for name in _TODAY_S_DRAWING:
        if hasattr(SeasonCog, name):
            if name == "_discard_prepared_review_images":
                setattr(cog, name, MagicMock())
            elif name == "_post_review_lineup_image":
                setattr(cog, name, AsyncMock(return_value=getattr(season_cog, "REVIEW_IMAGE_TEXT", None)))
            else:
                setattr(cog, name, AsyncMock())
    return cog


def _placement(uid: str, *, division_id: int = DIVISION_ID, team="Alpha", test_name=None):
    return {
        "test_display_name": test_name, "discord_user_id": uid, "team_name": team,
        "team_full_name": f"{team} Racing" if team != "Reserve" else team,
        "division_id": division_id, "division_name": "Pro",
    }


async def _mid_season_report(cog, monkeypatch, interaction=None):
    """Run the command, returning what it shows as ``(text, ephemeral)``: the public review it
    asked the queue to post (its report's sections, then its question where it is offered),
    then its private follow-ups. It must ask `season.review.post` exactly once, with the
    manager's interaction, and post nothing public itself; the request is kept on the cog."""
    import leaguebot.core.cogs.season_cog as season_cog

    _RecordedView.made = []
    monkeypatch.setattr(season_cog, "_ConfirmMidSeasonPlacementsView", _RecordedView)
    # The review's database is `:memory:`, which holds no season to fingerprint: the evidence the
    # report was computed from is given, as `test_season_review_report.py` gives it.
    from leaguebot.core.services import season_fingerprint_service as sfs

    async def _evidence(*_args, **_kwargs):
        return sfs.SeasonFingerprint({"the season": "as reported"})

    monkeypatch.setattr(sfs, "take_fingerprint", _evidence)
    interaction = interaction or _interaction()
    interaction.followup.send = AsyncMock(return_value=MagicMock())
    sent = interaction.followup.send
    await undecorate(SeasonCog.season_review)(cog, interaction)
    asked = [call for call in cog.bot.change_queue.ask.await_args_list
             if (call.kwargs.get("kind") or (call.args[0] if call.args else None)) == POST]
    assert len(asked) == 1, cog.bot.change_queue.ask.await_args_list
    [call] = asked
    assert call.kwargs.get("interaction") is interaction
    review = call.kwargs.get("payload") or call.args[1]
    cog.review_asked = review
    followups = [(str(c.args[0]) if c.args else "", bool(c.kwargs.get("ephemeral")))
                 for c in sent.await_args_list]
    assert [text for text, ephemeral in followups if not ephemeral] == [], (
        "the command posts nothing public itself"
    )
    public = [(text, False) for text in review["report"]]
    if review.get("question"):
        public.append((review["question"], False))
    return public + followups


def _offered(cog) -> bool:
    """Whether the review asked of the queue carries the question the button is offered on."""
    return bool(cog.review_asked.get("question"))


def _lineup_wanted(monkeypatch, wanted: bool = True) -> None:
    """The league's lineup picture is switched on, or off."""
    import leaguebot.image.services.image_lineup_post as lineup_post

    monkeypatch.setattr(lineup_post, "lineup_enabled", AsyncMock(return_value=wanted))


async def test_the_mid_season_review_names_each_driver_to_confirm_and_offers_the_button(monkeypatch):
    """Season 1 is placing two drivers mid-season, a member (1002) to Alpha Racing and the test
    driver Test Bravo to Reserve, both in Pro; Old is cancelled. The review asked of the queue,
    in the command's channel for season 1, names each driver and Pro's lineup as it will stand,
    says nothing of Old, and offers the question with the evidence its report was computed
    from; the command builds no view."""
    cog = _report_cog(
        placements=[
            _placement("1002"),
            _placement("9000", team="Reserve", test_name="Test Bravo"),
        ]
    )
    interaction = _interaction()

    messages = await _mid_season_report(cog, monkeypatch, interaction)
    text = "\n".join(m for m, _ in messages)

    assert "Placements Review (Season #1) — mid-season" in text
    assert "<@1002> → **Alpha Racing** in **Pro**" in text
    assert "Test Bravo → **Reserve** in **Pro**" in text
    assert "**Alpha**: <@1001>, <@1002>" in text
    assert "**Reserve**: *(empty)*" in text
    assert "Old" not in text, "a cancelled division has no lineup to confirm"
    assert "Do you confirm these placements?" in messages[-1][0]
    review = cog.review_asked
    assert (review["channel_id"], review["season_id"]) == (interaction.channel_id, 1)
    assert review["reviewer_id"] == 42
    assert review["evidence"]
    assert _RecordedView.made == []


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
    assert not _offered(cog)


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
    assert not _offered(cog)


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
    assert not _offered(cog)


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
    picture is asked — with the new drivers in it, as it will be posted. With the image module
    on and the lineup picture switched on, a driver placed in Pro and none in Am: the review
    asks one picture, Pro's lineup drawn with its uncommitted drivers, carrying Pro's lineup
    text it stands in for; the report carries Am's lineup as text and not Pro's."""
    divisions = [
        SimpleNamespace(id=DIVISION_ID, name="Pro", status="ACTIVE"),
        SimpleNamespace(id=77, name="Am", status="ACTIVE"),
    ]
    _lineup_wanted(monkeypatch)
    cog = _report_cog(placements=[_placement("1002")], divisions=divisions, images=True)

    messages = await _mid_season_report(cog, monkeypatch)
    text = "\n".join(m for m, private in messages if not private)

    (picture,) = cog.review_asked["pictures"]
    assert (picture["kind"], picture["division_id"]) == ("lineup", DIVISION_ID)
    assert picture["include_uncommitted"] is True
    assert "**Pro** — lineup once confirmed" in picture["text"]
    assert "**Pro** — lineup once confirmed" not in text, "the picture stands in its place"
    assert "**Am** — lineup once confirmed" in text, "a division with no new driver stays text"
    assert _offered(cog)


async def test_nothing_is_drawn_or_announced_where_the_lineup_graphic_is_off(monkeypatch):
    """With the image module on but the lineup picture switched off, the review asks the queue
    for no picture, and Pro's lineup, once confirmed, is in its report as text."""
    _lineup_wanted(monkeypatch, wanted=False)
    cog = _report_cog(placements=[_placement("1002")], images=True)

    messages = await _mid_season_report(cog, monkeypatch)

    assert list(cog.review_asked.get("pictures") or []) == []
    assert "**Pro** — lineup once confirmed" in "\n".join(m for m, _ in messages)


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


# ── What the confirmation leaves ───────────────────────────────────────────────────


async def _settle_every_signup(db_path) -> None:
    """Every signup settled, and the division given the channels it posts to (#374), so the
    confirmation has nothing to refuse on."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'ASSIGNED'")
        await db.execute(
            "UPDATE divisions SET lineup_channel_id = 700, calendar_channel_id = 701"
        )
        await db.commit()


async def _placing(tmp_path, monkeypatch):
    """`placing_league`'s season 3 in Ongoing, placements: Max in Pro and Charles in Am placed
    mid-season and not yet committed. Admin's ✅ Confirm placements is pressed on a mid-season
    review posted in channel 610; the queue is not run."""
    from tests.support.season_league import placing_league, post_mid_season_review, press_approve

    league = await placing_league(tmp_path, monkeypatch=monkeypatch)
    press = await press_approve(league, await post_mid_season_review(league))
    assert not league.errors, league.errors
    return league, press


async def test_a_lineup_discord_refuses_after_a_confirmation_stops_the_queue_and_the_placements_stand(
    tmp_path, monkeypatch,
):
    """On `placing_league`, Am's lineup channel refuses every post. Admin confirms the placements
    mid-season and the queue runs: it stops at the confirmation's own job posting Am's lineup, no
    separate lineup change asked, with Charles's placement committed and the season ongoing
    again. With the channel posting again, Retry posts Am's lineup once and the queue runs on."""
    import discord

    from leaguebot.core.services.lineup_posting import LINEUP_POST
    from tests.support.change_queue import change_rows, http_error, retry_job, run_queue, \
        stopped_job
    from tests.support.season_league import AM, CHARLES, DIVISIONS, profile_id

    am_lineup = DIVISIONS[AM][3].lineup
    league, _press = await _placing(tmp_path, monkeypatch)
    league.channel(am_lineup).send_fails = http_error(discord.Forbidden, status=403)
    before = len(league.texts(am_lineup))

    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_lineup"
    changes = {row["id"]: row["kind"] for row in await change_rows(league.db_path)}
    assert changes[stopped["change_id"]] == KIND
    assert LINEUP_POST not in changes.values()
    [row] = await league.rows("SELECT committed FROM driver_season_assignments "
                              "WHERE driver_profile_id = ?", profile_id(CHARLES))
    assert row["committed"] == 1
    assert (await league.season())["stage"] == "ONGOING"

    league.channel(am_lineup).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert len(league.texts(am_lineup)) == before + 1


async def test_a_clean_confirmation_reports_nothing_undone(tmp_path, monkeypatch):
    """On `placing_league`, Admin confirms the placements mid-season and the queue runs with
    nothing refused: the acknowledgement is updated to say both placements are confirmed and the
    season is ongoing again, with no "not everything could be done" section, and the log
    channel's confirmation line names nothing not done."""
    from tests.support.change_queue import run_queue

    league, press = await _placing(tmp_path, monkeypatch)

    await run_queue(league.bot)

    updated = "\n".join(str(call.args[0] if call.args else call.kwargs.get("content", ""))
                        for call in press.edit_original_response.await_args_list)
    assert "✅ 2 placement(s) confirmed. The season is ongoing again." in updated
    assert "Not everything could be done" not in updated
    [line] = [line for line in league.bot.log_channel.sent
              if "| /season placements-review | Confirmed" in line]
    assert "not done" not in line


# ── The mid-season button (#439, slice 6b) ─────────────────────────────────────────
#
# The button reads its review from the review's row, not from its view's memory, and deletes
# and posts nothing itself: a review it ends is taken down by the queue (C92, C97, C97a). The
# confirmation asks the take-down once its own gates pass, ahead of its own work. The league
# and Alex's review standing in channel 700 (report R1 and R2, question Q) are
# `test_review_outcomes_recorded.py`'s.

CONFIRM = "_do_confirm_mid_season_placements"


async def _standing_mid_season(tmp_path):
    """Alex's mid-season placements review of season 1, standing in channel 700."""
    from tests.core.test_review_outcomes_recorded import _standing

    return await _standing(tmp_path, "/season placements-review")


def _confirm_view(env):
    """Alex's review's ✅ Confirm placements button, on a cog whose confirmation is recorded and
    goes through."""
    from leaguebot.core.cogs.season_cog import _ConfirmMidSeasonPlacementsView
    from tests.core.test_review_outcomes_recorded import _view_on

    return _view_on(env, _ConfirmMidSeasonPlacementsView, CONFIRM)


def _real_confirmation(env, cog, *, stage=SeasonStage.ONGOING_PLACEMENTS) -> None:
    """The real confirmation on *cog*, season 1 at *stage*, nothing unsettled and no fault."""
    from types import MethodType

    env.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, season_number=1, stage=stage)
    )
    cog._placement_confirmation_faults = AsyncMock(return_value=([], []))
    cog._mid_season_configuration_faults = AsyncMock(return_value=[])
    setattr(cog, CONFIRM, MethodType(getattr(SeasonCog, CONFIRM), cog))


def _press(env, user_id: int | None = None, name: str = "Alex"):
    from tests.core.test_review_outcomes_recorded import REVIEWER, _press_on

    return _press_on(env, REVIEWER if user_id is None else user_id, name)


async def _take_downs(env) -> list[dict]:
    from tests.core.test_review_outcomes_recorded import _take_downs as take_downs

    return await take_downs(env)


async def _prompt_rows(env) -> int:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM season_review_prompts")
        return (await cursor.fetchone())[0]


async def test_a_stumbled_confirmation_still_clears_its_review(tmp_path, monkeypatch):
    """On `placing_league`, Admin's mid-season review stands in channel 610, the season unchanged
    since its report. Admin presses ✅ Confirm placements and the queue runs, but the
    confirmation's save fails (a fault of the database). The review's take-down, asked ahead of
    the confirmation in the same request, stands and runs: the report and the question are
    deleted and the review's row forgotten, while the queue stops at the confirmation's save with
    nothing committed and the season still placing drivers."""
    from tests.core.test_mid_season_confirm_change import _failing_save
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import (
        REVIEW_CHANNEL, placing_league, post_mid_season_review, press_approve,
    )

    league = await placing_league(tmp_path, monkeypatch=monkeypatch)
    review = await post_mid_season_review(league)
    await press_approve(league, review)

    with _failing_save():
        await run_queue(league.bot)
        stopped = await stopped_job(league.db_path)
        assert stopped is not None and stopped["name"] == "apply"

    for message in [*review.report, review.prompt]:
        assert ("delete", REVIEW_CHANNEL, message.id) in league.events
    assert await league.rows("SELECT * FROM season_review_prompts") == []
    [row] = await league.rows(
        "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE committed = 0"
    )
    assert row["n"] == 2
    assert (await league.season())["stage"] == "ONGOING_PLACEMENTS"


async def test_a_refused_confirmation_leaves_its_review_standing(tmp_path, monkeypatch):
    """The core specification's "Confirming placements", as for every review's button: a press
    refused at the press leaves the review and its button standing, to be pressed again within
    its five minutes. Alex's mid-season review stands, the season unchanged since its report, but
    the season is no longer placing drivers. Alex presses ✅ Confirm placements over the real
    confirmation: he is answered at once that the season is no longer placing drivers, no
    confirmation is asked of the queue, nothing is deleted, no take-down is asked, and the
    review's row stands."""
    from tests.core.test_review_outcomes_recorded import _season_since_the_review
    from tests.support.change_queue import change_rows

    env = await _standing_mid_season(tmp_path)
    await _season_since_the_review(env, monkeypatch)
    view, cog = _confirm_view(env)
    _real_confirmation(env, cog, stage=SeasonStage.ONGOING)
    interaction = _press(env)

    await type(view).approve(view, interaction, MagicMock())

    assert "no longer placing drivers" in acknowledgement(interaction)
    assert [row for row in await change_rows(env.db_path) if row["kind"] == KIND] == []
    assert env.channel.deleted == []
    assert await _take_downs(env) == []
    assert await _prompt_rows(env) == 1


async def test_the_reviewer_confirms_the_mid_season_placements(tmp_path, monkeypatch):
    """Alex's mid-season review stands, posted a moment ago, the season unchanged since its
    report, its row's evidence reading Pro's rounds "as reviewed". Alex, who ran it, presses
    ✅ Confirm placements: the confirmation is worked once, handed the press, the button's own
    name (which it records any refusal under) and the review's evidence, which the confirmation
    is judged against again when it runs. The button deletes nothing and asks no take-down
    itself: that is the confirmation's, asked of the queue with it."""
    from leaguebot.core.cogs.season_cog import _review_button
    from tests.core.test_review_outcomes_recorded import AREA, _season_since_the_review

    env = await _standing_mid_season(tmp_path)
    await _season_since_the_review(env, monkeypatch)
    view, cog = _confirm_view(env)
    interaction = _press(env)

    await type(view).approve(view, interaction, MagicMock())

    getattr(cog, CONFIRM).assert_awaited_once_with(
        interaction, what=_review_button("Confirm placements", "/season placements-review"),
        fingerprint={AREA: "as reviewed"},
    )
    assert env.channel.deleted == []
    assert await _take_downs(env) == []


async def test_another_league_manager_may_not_confirm_the_mid_season_placements(tmp_path):
    """Sam, a league manager who neither ran Alex's mid-season review nor holds the league's
    admin role, presses ✅ Confirm placements: he is refused privately, told nothing has been
    confirmed, and the review is left alone: nothing deleted, no take-down asked."""
    from tests.support.change_queue import acknowledgement

    env = await _standing_mid_season(tmp_path)
    view, cog = _confirm_view(env)
    interaction = _press(env, 99, "Sam")

    await type(view).approve(view, interaction, MagicMock())

    getattr(cog, CONFIRM).assert_not_awaited()
    assert "Nothing has been confirmed" in acknowledgement(interaction)
    assert env.channel.deleted == []
    assert await _take_downs(env) == []


async def test_mid_season_placements_changed_since_the_review_are_not_confirmed(
        tmp_path, monkeypatch):
    """Alex's mid-season review stands, its row recording Pro's rounds as its report described
    them; they have changed since. Alex presses ✅ Confirm placements a moment after the question
    appeared. He is refused privately, the reply naming Pro's rounds as changed and
    `/season placements-review` as the command to run again, and nothing is confirmed. The
    button deletes nothing itself: it asks the queue to take the review down, ending refused, as
    an expired review ends (core :282, :296)."""
    from tests.core.test_review_outcomes_recorded import AREA, _season_since_the_review
    from tests.support.change_queue import acknowledgement

    env = await _standing_mid_season(tmp_path)
    await _season_since_the_review(env, monkeypatch, now="changed")
    view, cog = _confirm_view(env)
    interaction = _press(env)

    await type(view).approve(view, interaction, MagicMock())

    getattr(cog, CONFIRM).assert_not_awaited()
    reply = acknowledgement(interaction)
    assert f"• {AREA}" in reply
    assert "`/season placements-review`" in reply
    assert env.channel.deleted == []
    [payload] = await _take_downs(env)
    assert (payload["ending"], payload["question_id"]) == ("refused", env.channel.q.id)


# ── What the log channel holds for the mid-season review (#482) ────────────────────
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why, and a review that posts its question writes one line
# naming who ran it. A refusal by ✅ Confirm placements names the button and the review it
# belongs to, and one whose reply is a list carries every item of it.


def _manager_confirming(cog):
    """Manager (id 42) pressing ✅ Confirm placements, connected to the cog's log channel, the
    press not yet answered."""
    interaction = _pressing()
    interaction.client = cog.bot
    interaction.user.display_name = "Manager"
    return interaction


def _gone_by_now(cog):
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)


def _moved_on(cog):
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, season_number=1, stage=SeasonStage.ONGOING)
    )


def _faults_on_every_count(cog):
    cog._placement_confirmation_faults = AsyncMock(
        return_value=(["**Racer** — awaiting approval"], ["**Pro** has no lineup channel set"])
    )
    cog._mid_season_configuration_faults = AsyncMock(
        return_value=["Inkscape is not installed on this host."]
    )


@pytest.mark.parametrize(
    "arrange, reply_says, carried",
    [
        pytest.param(
            _gone_by_now, "The season is no longer placing drivers.",
            ["The season is no longer placing drivers. **Nothing has been confirmed.**"],
            id="no_season_being_raced",
        ),
        pytest.param(
            _moved_on, "The season is no longer placing drivers.",
            ["The season is no longer placing drivers. **Nothing has been confirmed.**"],
            id="the_season_no_longer_placing",
        ),
        pytest.param(
            _faults_on_every_count, "Placements cannot be confirmed:",
            [
                "Unsettled signup: **Racer** — awaiting approval",
                "**Pro** has no lineup channel set",
                "Inkscape is not installed on this host.",
            ],
            id="unsettled_signups_channel_and_image_faults",
        ),
    ],
)
async def test_every_mid_season_confirmation_refusal_is_recorded(
    db_path, arrange, reply_says, carried
):
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    arrange(cog)
    interaction = _manager_confirming(cog)

    await cog._do_confirm_mid_season_placements(interaction)

    replies = "\n".join(
        call.args[0] for call in interaction.response.send_message.await_args_list
    )
    assert reply_says in replies
    assert "Nothing has been confirmed" in replies
    assert _asked(cog) == []
    assert await _uncommitted(db_path) == ["1002"]
    lines = [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]
    assert len(lines) == 1, lines
    line = lines[0]
    head = line.splitlines()[0]
    assert head.startswith("⛔ ")
    assert "Confirm placements" in head and "/season placements-review" in head
    assert " refused for Manager (<@42>) — " in head
    for item in carried:
        assert item in line, (item, line)


async def test_a_posted_mid_season_review_writes_no_line_of_its_own_and_asks_the_review_as_the_manager_s(
        monkeypatch):
    """Manager (id 42) runs /season placements-review on a season being raced whose closed
    window left one driver to place, with nothing standing in the way. The command builds no
    view and writes no line naming `/season placements-review` itself: the review's change
    writes the line once its question is posted (#482, criterion 6;
    `test_season_review_changes.py`). It asks the review of the queue with Manager's
    interaction, the question offered."""
    cog = _report_cog(placements=[_placement("1002")])
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "season placements-review"
    interaction.user.display_name = "Manager"

    await _mid_season_report(cog, monkeypatch, interaction)

    assert _RecordedView.made == []
    logged = [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]
    assert [line for line in logged if "/season placements-review" in line] == []
    assert _offered(cog)
