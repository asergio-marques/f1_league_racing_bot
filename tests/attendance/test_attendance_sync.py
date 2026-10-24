"""`sync_attendance` — what `/attendance sync` does (decided 2026-09-18, issue #239).

A sanction that did not apply used to be recoverable only by hand, through `/driver sack` or
`/driver move`, which post no verdict and mark no sheet. The sync is the recovery: every
finalised round from the one named on is recomputed from its results in one transaction, the
division's sheet is posted for the latest of them, and the sanctions are enforced against that
latest round, where the running totals stand.

**Safe to run twice** is the property the whole recovery rests on, and is pinned through the
real placement service rather than a double: a driver already sacked holds no seat and so is
no longer a candidate, which a double that recorded calls would never show.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.attendance.services import attendance_service
from leaguebot.attendance.services.attendance_service import SanctionOutcome, sync_attendance
from tests.attendance.test_attendance_sanctions import (
    DIVISION_ID,
    FULL_TIME_PROFILE,
    ROUND_ID,
    SEASON_ID,
    _make_bot,
    _make_db,
    _asking_for_sheets,
    _seed_totals,
)
from tests.attendance.test_attendance_tracking import _awarded, _make_two_round_db


#: The change the sync asks for the verdicts and banner its sanctions owe.
ANNOUNCE_KIND = "attendance.sanctions.announce"


async def _add_rounds(db_path: str, statuses: dict[int, str]) -> None:
    """Rounds 2 onwards of the division, by number, beside the fixture's round 1."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'FINAL' WHERE id = ?", (ROUND_ID,)
        )
        for number, status in statuses.items():
            await db.execute(
                "INSERT INTO rounds "
                "(id, division_id, round_number, format, track_name, scheduled_at, status) "
                "VALUES (?, ?, ?, 'NORMAL', 'Monza', '2026-06-01T18:00:00', ?)",
                (number, DIVISION_ID, number, status),
            )
        await db.commit()


@pytest.fixture
def pipeline():
    """Stub the recalculation, the sheet and the sanctions, so what reaches each is tested."""
    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ) as recompute, patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ) as distribute, patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ) as sheet, patch.object(
        attendance_service,
        "enforce_attendance_sanctions",
        new=AsyncMock(return_value=SanctionOutcome()),
    ) as sanctions:
        yield recompute, distribute, sheet, sanctions


async def _sync(bot, db_path: str, from_round_id: int = ROUND_ID) -> SanctionOutcome:
    return await sync_attendance(
        bot, MagicMock(), db_path, DIVISION_ID, from_round_id, SEASON_ID,
        queue=bot.change_queue,
    )


async def test_sync_recalculates_from_the_input_round_forward(tmp_path, pipeline):
    """Every round from the one named on is recomputed from its results, not merely
    redistributed — a later round whose recording failed is repaired too."""
    recompute, distribute, _, _ = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "AWAITING_APPEAL_VERDICTS"})

    await _sync(_make_bot(db_path), db_path)

    assert [c.args[1] for c in recompute.await_args_list] == [1, 2, 3]
    assert [c.args[1] for c in distribute.await_args_list] == [1, 2, 3]


async def test_sync_leaves_earlier_rounds_and_unfinished_ones_alone(tmp_path, pipeline):
    recompute, distribute, _, _ = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "FINAL", 4: "NOT_RUN"})

    await _sync(_make_bot(db_path), db_path, from_round_id=2)

    assert [c.args[1] for c in recompute.await_args_list] == [2, 3]
    assert [c.args[1] for c in distribute.await_args_list] == [2, 3]


async def test_sync_posts_the_sheet_and_enforces_against_the_latest_round(
    tmp_path, pipeline
):
    """The running totals are carried forward, so the latest round is where they stand —
    and the channel holds one sheet, which is that round's."""
    _, _, _, sanctions = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "FINAL"})
    bot = _make_bot(db_path)

    with _asking_for_sheets() as asked:
        await _sync(bot, db_path)

    assert asked == [(DIVISION_ID, 3, set())]
    assert sanctions.await_args.args[3] == 3
    assert sanctions.await_args.kwargs["queue"] is bot.change_queue


async def test_sync_returns_what_the_sanctions_did(tmp_path, pipeline):
    _, _, _, sanctions = pipeline
    outcome = SanctionOutcome(failed=[("<@5>", "autosack", "no")])
    sanctions.return_value = outcome
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})

    assert await _sync(_make_bot(db_path), db_path) is outcome


async def test_a_second_sync_applies_nothing_twice(tmp_path):
    """The recovery is to run it again, so running it again must be harmless: the driver
    sacked by the first run is not attempted by the second. The first asks the change queue,
    once, for the sack's announcement, and the second asks for none."""
    from leaguebot.core.services.placement_service import PlacementService

    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _add_rounds(db_path, {})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    placement = PlacementService(db_path)
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement

    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ), patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ), patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ), _asking_for_sheets(), patch(
        "leaguebot.results.services.verdict_announcement_service.post_autosanction_announcement",
        # Returns the sanctions it could not announce (#237). `None` is not merely
        # inert here: the run does `posting_faults += <return>`, which raises inside
        # the per-driver try and is recorded as a bogus failed sanction. `create=True`
        # because the run asks the queue to announce instead, and the announcer is deleted.
        new=AsyncMock(return_value=[]), create=True,
    ) as announce, patch(
        "leaguebot.results.services.verdict_announcement_service.banner_for_round",
        new=MagicMock(return_value=None), create=True,
    ):
        first = await _sync(bot, db_path)
        second = await _sync(bot, db_path)

    assert [sanction for _, sanction in first.applied] == ["autosack"]
    assert second.applied == [] and second.complete
    announce.assert_not_awaited()
    asked = [call for call in bot.change_queue.ask.await_args_list
             if call.args[0] == ANNOUNCE_KIND]
    assert len(asked) == 1, "the second sync asks for no announcement"


async def test_sync_still_applies_a_sanction_whose_role_change_discord_refuses(tmp_path, caplog):
    """`/attendance sync` keeps today's role behaviour (#439 slice 8, Part C): a driver over the
    autosack threshold, placed in Alpha, is sacked, Discord refuses to take any of their roles (Missing
    Permissions), the refusal is logged on the host, and the sack is applied all the same."""
    import logging

    import discord

    from leaguebot.core.services.placement_service import PlacementService

    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _add_rounds(db_path, {})
    async with get_connection(db_path) as db:
        # A real driver, not a test one, placed in Alpha: a sack takes their roles back.
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED', is_test_driver = 0 "
            "WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, ?, ?, 20, 1)",
            (FULL_TIME_PROFILE, SEASON_ID, DIVISION_ID),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    placement = PlacementService(db_path)
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement
    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=lambda role_id: MagicMock(id=role_id))
    member = MagicMock()
    member.guild = guild
    member.roles = MagicMock()
    member.roles.__contains__ = MagicMock(return_value=True)  # holds every role asked of it
    member.remove_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)

    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ), patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ), patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ), _asking_for_sheets(), caplog.at_level(logging.WARNING):
        outcome = await sync_attendance(
            bot, guild, db_path, DIVISION_ID, ROUND_ID, SEASON_ID, queue=bot.change_queue,
        )

    assert [sanction for _, sanction in outcome.applied] == ["autosack"]
    member.remove_roles.assert_awaited()
    assert "failed to remove role" in caplog.text
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) AS n FROM team_seats WHERE driver_profile_id = ?",
            (FULL_TIME_PROFILE,),
        )).fetchone()
    assert row["n"] == 0, "the sack was not saved"


async def test_sync_is_one_transaction(tmp_path, monkeypatch):
    """#187's rule holds for the sync as for an amendment: a failure part-way through the
    rounds writes nothing, rather than leaving the division scored to two rules."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    real = attendance_service.record_attendance_from_results_full_recompute
    calls: list[int] = []

    async def failing_on_the_second(db_path, round_id, division_id, *, db=None):
        calls.append(round_id)
        if len(calls) > 1:
            raise RuntimeError("the recompute failed part-way through")
        return await real(db_path, round_id, division_id, db=db)

    monkeypatch.setattr(
        attendance_service, "record_attendance_from_results_full_recompute",
        failing_on_the_second,
    )
    monkeypatch.setattr(attendance_service, "post_attendance_sheet", AsyncMock())
    monkeypatch.setattr(attendance_service, "enforce_attendance_sanctions", AsyncMock())

    with pytest.raises(RuntimeError):
        await sync_attendance(None, None, db_file, division_id, round_ids[0], 1,
                              queue=MagicMock())

    assert calls == round_ids
    assert await _awarded(db_file, round_ids[0]) is None


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog  # noqa: E402
from leaguebot.core.models.season import SeasonStage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from leaguebot.core.utils.channel_guard import LEAGUE_MANAGER, TIER_ATTRIBUTE  # noqa: E402


def _cog(db_path: str, *, stage=SeasonStage.ONGOING) -> AttendanceCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    season = None if stage is None else SimpleNamespace(id=SEASON_ID, stage=stage)
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Division 1")]
    )
    bot.output_router.post_log = AsyncMock()
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(
            interaction.response.defer.await_count
            or interaction.response.send_message.await_count
        )
    )
    interaction.followup.send = AsyncMock()
    return interaction


async def _invoke(cog, interaction, *, division="division 1", round=1,
                  faults=(), outcome=None):
    interaction.command = AttendanceCog.sync
    interaction.client = cog.bot
    with patch(
        "leaguebot.attendance.cogs.attendance_cog.recalculation_faults", new=AsyncMock(return_value=list(faults))
    ), patch(
        "leaguebot.attendance.cogs.attendance_cog.sync_attendance",
        new=AsyncMock(return_value=outcome or SanctionOutcome()),
    ) as synced:
        await undecorate(AttendanceCog.sync)(cog, interaction, division, round)
    return synced


def _replied(interaction) -> str:
    return "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)


async def test_the_command_is_a_league_manager_s():
    """Decided 2026-09-18: it applies only thresholds the league has already configured."""
    assert getattr(AttendanceCog.sync.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


@pytest.mark.parametrize("stage", [None, SeasonStage.PLACEMENTS, SeasonStage.PENDING_COMPLETION])
async def test_it_is_refused_outside_the_ongoing_stages(tmp_path, stage):
    db_path = await _make_db(tmp_path, autosack=20)
    interaction = _interaction()

    synced = await _invoke(_cog(db_path, stage=stage), interaction)

    synced.assert_not_awaited()
    assert "only while the season is ongoing" in _replied(interaction)


async def test_an_unknown_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    interaction = _interaction()

    synced = await _invoke(_cog(db_path), interaction, division="Nowhere")

    synced.assert_not_awaited()
    assert "not found" in _replied(interaction)


async def test_an_unknown_round_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    interaction = _interaction()

    synced = await _invoke(_cog(db_path), interaction, round=9)

    synced.assert_not_awaited()
    assert "has no round 9" in _replied(interaction)


async def test_a_round_not_yet_finalised_is_refused(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "AWAITING_REPORT_VERDICTS"})
    interaction = _interaction()

    synced = await _invoke(_cog(db_path), interaction, round=2)

    synced.assert_not_awaited()
    assert "has not had its penalties approved" in _replied(interaction)


async def _amendment_open(db_path) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at) "
            "VALUES (?, 8200, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00+00:00')",
            (ROUND_ID,),
        )
        await db.commit()


async def test_it_waits_while_a_round_of_the_division_is_amended(tmp_path):
    """#345, decided 2026-09-21. The recalculation reads the amendment's corrections before
    they are approved; the sheet it posts and the sanctions it applies would stand even if the
    amendment were then cancelled."""
    db_path = await _make_db(tmp_path, autosack=20)
    await _amendment_open(db_path)
    interaction = _interaction()

    synced = await _invoke(_cog(db_path), interaction)

    synced.assert_not_awaited()
    replied = _replied(interaction)
    assert "of **Division 1** is being amended in <#8200>" in replied
    assert "Run this again then." in replied


async def test_a_channel_fault_refuses_it_with_nothing_changed(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    interaction = _interaction()

    synced = await _invoke(
        _cog(db_path), interaction, faults=["Division 1: the attendance channel is gone"]
    )

    synced.assert_not_awaited()
    replied = _replied(interaction)
    assert "Nothing was changed" in replied
    assert "the attendance channel is gone" in replied


async def test_it_syncs_from_the_named_round_and_lists_what_applied_and_failed(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL"})
    interaction = _interaction()
    cog = _cog(db_path)
    outcome = SanctionOutcome(
        applied=[("<@4> (Four)", "autosack")],
        failed=[("<@5> (Five)", "autoreserve", "the division has no Reserve team")],
    )

    synced = await _invoke(cog, interaction, round=2, outcome=outcome)

    assert synced.await_args.args[3:] == (DIVISION_ID, 2, SEASON_ID)
    assert synced.await_args.kwargs["queue"] is cog.bot.change_queue
    replied = _replied(interaction)
    assert "• <@4> (Four) — autosack" in replied
    assert "Still not applied" in replied
    assert "• <@5> (Five) — autoreserve: the division has no Reserve team" in replied
    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "/attendance sync | Incomplete" in logged


async def test_a_clean_sync_with_nothing_owed_says_so(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    interaction = _interaction()
    cog = _cog(db_path)

    await _invoke(cog, interaction)

    assert "No sanction was owed." in _replied(interaction)
    assert "/attendance sync | Success" in cog.bot.output_router.post_log.await_args.args[0]


# ---------------------------------------------------------------------------
# Every refusal is recorded in the log channel (#482)
# ---------------------------------------------------------------------------


async def _refused_while_disabled(tmp_path):
    cog = _cog(await _make_db(tmp_path, autosack=20))
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    return cog, {}


async def _refused_outside_ongoing(tmp_path):
    return _cog(await _make_db(tmp_path, autosack=20), stage=None), {}


async def _refused_unknown_division(tmp_path):
    return _cog(await _make_db(tmp_path, autosack=20)), {"division": "Nowhere"}


async def _refused_while_amended(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _amendment_open(db_path)
    return _cog(db_path), {}


async def _refused_unknown_round(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    return _cog(db_path), {"round": 9}


async def _refused_not_finalised(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "AWAITING_REPORT_VERDICTS"})
    return _cog(db_path), {"round": 2}


async def _refused_for_a_channel_fault(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    return _cog(db_path), {"faults": ["Division 1: the attendance channel is gone"]}


@pytest.mark.parametrize(
    "arrange,reason",
    [
        (_refused_while_disabled, "The Attendance module is not enabled."),
        (_refused_outside_ongoing, "only while the season is ongoing"),
        (_refused_unknown_division, "Division 'Nowhere' not found."),
        (_refused_while_amended, "is being amended"),
        (_refused_unknown_round, "has no round 9"),
        (_refused_not_finalised, "has not had its penalties approved"),
        (_refused_for_a_channel_fault, "the attendance channel is gone"),
    ],
    ids=[
        "module-disabled", "not-ongoing", "unknown-division", "amended", "unknown-round",
        "not-finalised", "channel-fault",
    ],
)
async def test_every_refusal_of_the_sync_is_recorded(tmp_path, arrange, reason):
    """A5: each refusal is answered and writes one "⛔ `/attendance sync` refused" line,
    giving the reason; a channel fault's line names the channel at fault."""
    cog, kwargs = await arrange(tmp_path)
    interaction = _interaction()

    synced = await _invoke(cog, interaction, **kwargs)

    synced.assert_not_awaited()
    replies = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(replies) == 1
    lines = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert len(lines) == 1
    assert lines[0].startswith("\u26d4 `/attendance sync` refused for Manager (<@77>) \u2014 ")
    assert reason in lines[0]


# ---------------------------------------------------------------------------
# The sync's announcements follow on the change queue (#439, slice 6b, C37)
# ---------------------------------------------------------------------------

async def test_a_sync_asks_the_queue_to_announce_its_sanctions(tmp_path):
    """Division 1's full-time driver has 25 attendance points against an autosack threshold of
    20, and round 1 is final. A manager syncs from round 1: the driver is sacked as today, but
    no verdict is posted by the sync itself; it asks the change queue, once, for the
    announcement of the round's sanctions."""
    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _add_rounds(db_path, {})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)

    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ), patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ), _asking_for_sheets(), patch(
        # The announcer the sync posted through until now; `create=True` because the change
        # deletes it, which leaves nothing to call.
        "leaguebot.results.services.verdict_announcement_service.post_autosanction_announcement",
        new=AsyncMock(return_value=[]), create=True,
    ) as announce, patch(
        "leaguebot.results.services.verdict_announcement_service.banner_for_round",
        new=MagicMock(return_value=None), create=True,
    ):
        outcome = await _sync(bot, db_path)

    assert [sanction for _, sanction in outcome.applied] == ["autosack"]
    announce.assert_not_awaited()
    asked = [call for call in bot.change_queue.ask.await_args_list
             if call.args[0] == ANNOUNCE_KIND]
    assert len(asked) == 1
    assert asked[0].args[1]["round_id"] == ROUND_ID


async def test_the_sync_s_reply_lists_the_sanctions_applied_and_says_the_announcements_follow(
    tmp_path,
):
    """A manager syncs Division 1 from round 1, and the sync sacks <@4> (Four). The reply lists
    the sanction applied and says its announcement follows on the change queue, with no line
    saying a sanction was applied but not announced."""
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    interaction = _interaction()
    cog = _cog(db_path)
    outcome = SanctionOutcome(applied=[("<@4> (Four)", "autosack")])

    await _invoke(cog, interaction, outcome=outcome)

    replied = _replied(interaction)
    assert "• <@4> (Four) — autosack" in replied
    assert "queue" in replied
    assert "not announced" not in replied


# The announcement's own jobs, run by a real queue. The league is `review_league`'s: Pro's
# round 3 at Silverstone, its verdicts channel 704 with no heading standing, attendance on, and
# the card of each sanction posted through the attendance hook the builder hands the change types
# (`AttendanceDouble`, which records each card and refuses one where told). The change is asked
# as the sync asks it: as the bot's, its payload naming the round, the division and each
# sanction applied, as the candidate the sanctions were owed as (`review_league.candidate`).

from leaguebot.core.models.change import StepFailedOnDiscord  # noqa: E402
from leaguebot.results.services import verdict_announcement_service as vas  # noqa: E402
from tests.support import review_league as rl  # noqa: E402
from tests.support.change_queue import (  # noqa: E402
    change_rows,
    discard_job,
    run_queue,
    step_rows,
)


async def _ask_announcement(league: rl.ReviewLeague, *drivers: tuple[int, int]) -> None:
    """Ask the queue, as the bot, to announce the autoreserve of each (profile, driver) of
    *drivers* in round 3 of Pro; the queue is not run."""
    from leaguebot.core.models.change import ChangeOrigin

    payload = {
        "round_id": rl.ROUND_ID, "division_id": rl.DIVISION_ID, "division": "Pro",
        "sanctions": [rl.candidate(profile, driver) for profile, driver in drivers],
    }
    await league.bot.change_queue.ask(
        ANNOUNCE_KIND, payload, what="announcing round 3's attendance sanctions",
        origin=ChangeOrigin.BOT,
    )


def _cards(league: rl.ReviewLeague) -> list[int]:
    """The profile of each sanction card the hook was asked to post, in order."""
    return [call[1] for call in league.attendance.calls if call[0] == "announce_sanction"]


async def test_a_sync_s_banner_is_posted_before_its_first_card_and_recorded_with_its_mark(
    tmp_path,
):
    """A sync has moved Lewis (101) and Max (102) of Pro to reserve, and round 3 has no heading
    standing in its verdicts channel. The queue is asked to announce both and run: its first job
    posts a heading in the verdicts channel (704), recorded as round 3's banner; Lewis's card and
    then Max's follow beneath it, each by a job of its own; nothing stops."""
    league = await rl.review_league(tmp_path, attendance=True)

    await _ask_announcement(
        league, (rl.LEWIS_PROFILE, rl.LEWIS), (rl.MAX_PROFILE, rl.MAX),
    )
    await run_queue(league.bot)

    assert await rl.stopped_at(league) is None
    [change] = [row for row in await change_rows(league.db_path) if row["kind"] == ANNOUNCE_KIND]
    steps = await step_rows(league.db_path, change["id"])
    assert [step["name"] for step in steps] == [
        "announce_heading", "announce_sanction", "announce_sanction",
    ]
    assert all(step["done_at"] is not None for step in steps)
    [heading] = league.sent_to(rl.VERDICTS_CHANNEL)
    assert [message_id for _, message_id in await vas._banners_of(league.db_path, rl.ROUND_ID)] == [
        heading
    ]
    assert _cards(league) == [rl.LEWIS_PROFILE, rl.MAX_PROFILE]


async def test_a_refused_card_stops_the_queue_and_a_discard_names_it(tmp_path):
    """As above, but Discord refuses Max's card (Missing Access). The queue stops at Max's card,
    Lewis's posted beneath the heading. A league admin discards it: the queue goes on, and the
    log channel names Max's autoreserve as applied but not announced, for posting by hand."""
    league = await rl.review_league(tmp_path, attendance=True)
    league.attendance.announce_fails = {rl.MAX_PROFILE: StepFailedOnDiscord("Missing Access")}

    await _ask_announcement(
        league, (rl.LEWIS_PROFILE, rl.LEWIS), (rl.MAX_PROFILE, rl.MAX),
    )
    await run_queue(league.bot)

    assert await rl.stopped_at(league) == "announce_sanction"
    assert _cards(league) == [rl.LEWIS_PROFILE, rl.MAX_PROFILE]
    assert len(league.sent_to(rl.VERDICTS_CHANNEL)) == 1

    await discard_job(league.bot)

    assert await rl.stopped_at(league) is None
    named = [line for line in league.log().splitlines() if f"<@{rl.MAX}>" in line]
    assert any("not announced" in line for line in named)
