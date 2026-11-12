"""`/attendance sync` — the command's gate (decided 2026-09-18, issue #239).

A sanction that did not apply used to be recoverable only by hand, through `/driver sack` or
`/driver move`, which post no verdict and mark no sheet. The sync is the recovery: every
finalised round from the one named on is recomputed from its results, the division's sheet is
posted for the latest of them, and the sanctions are enforced against that latest round, where
the running totals stand.

The sync is one change on the queue (#285): what it does, and that a second run and a stop part
way through are harmless, is pinned through the queue in `test_attendance_sync_change.py`. Here
are the command's refusals, each answered and recorded with no change asked of the queue, and
the cards of the sanctions announced by the change the sync asks for them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection
from tests.attendance.test_attendance_sanctions import (
    DIVISION_ID,
    ROUND_ID,
    SEASON_ID,
    _make_db,
)


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
                "VALUES (?, ?, ?, 'NORMAL', 'Monza', '2026-06-01T18:00:00+00:00', ?)",
                (number, DIVISION_ID, number, status),
            )
        await db.commit()


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
    # The queue the sync is asked of, recording what it is asked.
    bot.change_queue.ask = AsyncMock(return_value=1)
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


async def _invoke(cog, interaction, *, division="division 1", round=1, faults=()):
    """Run the command; give the queue's `ask`, which records any change the sync asked for."""
    interaction.command = AttendanceCog.sync
    interaction.client = cog.bot
    with patch(
        "leaguebot.attendance.cogs.attendance_cog.recalculation_faults",
        new=AsyncMock(return_value=list(faults)),
    ):
        await undecorate(AttendanceCog.sync)(cog, interaction, division, round)
    return cog.bot.change_queue.ask


def _replied(interaction) -> str:
    return "\n".join(str(c.args[0]) for c in interaction.followup.send.await_args_list)


async def test_the_command_is_a_league_manager_s():
    """Decided 2026-09-18: it applies only thresholds the league has already configured."""
    assert getattr(AttendanceCog.sync.callback, TIER_ATTRIBUTE) == LEAGUE_MANAGER


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
    amendment were then cancelled. No change is asked of the queue."""
    db_path = await _make_db(tmp_path, autosack=20)
    await _amendment_open(db_path)
    interaction = _interaction()

    asked = await _invoke(_cog(db_path), interaction)

    asked.assert_not_awaited()
    replied = _replied(interaction)
    assert "of **Division 1** is being amended in <#8200>" in replied
    assert "Run this again then." in replied


async def test_a_channel_fault_refuses_it_with_nothing_changed(tmp_path):
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})
    interaction = _interaction()

    asked = await _invoke(
        _cog(db_path), interaction, faults=["Division 1: the attendance channel is gone"]
    )

    asked.assert_not_awaited()
    replied = _replied(interaction)
    assert "Nothing was changed" in replied
    assert "the attendance channel is gone" in replied


# ---------------------------------------------------------------------------
# Every refusal is recorded in the log channel (#482)
# ---------------------------------------------------------------------------


async def _refused_while_disabled(tmp_path):
    cog = _cog(await _make_db(tmp_path, autosack=20))
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    return cog, {}


async def _refused_outside_ongoing(tmp_path):
    return _cog(await _make_db(tmp_path, autosack=20), stage=None), {}


async def _refused_in_placements(tmp_path):
    return _cog(await _make_db(tmp_path, autosack=20), stage=SeasonStage.PLACEMENTS), {}


async def _refused_pending_completion(tmp_path):
    return _cog(await _make_db(tmp_path, autosack=20), stage=SeasonStage.PENDING_COMPLETION), {}


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


# *via* is the reply that carries the refusal: the module gate answers before the command
# defers, by `response.send_message`; every later refusal follows the defer, by `followup.send`.
@pytest.mark.parametrize(
    "arrange,reason,via",
    [
        (_refused_while_disabled, "The Attendance module is not enabled.", "response"),
        (_refused_outside_ongoing, "only while the season is ongoing", "followup"),
        (_refused_in_placements, "only while the season is ongoing", "followup"),
        (_refused_pending_completion, "only while the season is ongoing", "followup"),
        (_refused_unknown_division, "Division 'Nowhere' not found.", "followup"),
        (_refused_while_amended, "is being amended", "followup"),
        (_refused_unknown_round, "has no round 9", "followup"),
        (_refused_not_finalised, "has not had its penalties approved", "followup"),
        (_refused_for_a_channel_fault, "the attendance channel is gone", "followup"),
    ],
    ids=[
        "module-disabled", "not-ongoing", "not-ongoing-placements",
        "not-ongoing-pending-completion", "unknown-division", "amended", "unknown-round",
        "not-finalised", "channel-fault",
    ],
)
async def test_every_refusal_of_the_sync_is_recorded(tmp_path, arrange, reason, via):
    """A5: each refusal asks no change of the queue, is answered once, by the reply *via* names,
    carrying its reason, and writes one "⛔ `/attendance sync` refused" line, giving the reason;
    a channel fault's line names the channel at fault."""
    cog, kwargs = await arrange(tmp_path)
    interaction = _interaction()

    asked = await _invoke(cog, interaction, **kwargs)

    asked.assert_not_awaited()
    replies = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(replies) == 1
    assert reason in str(replies[0].args[0])
    carried = {
        "response": interaction.response.send_message,
        "followup": interaction.followup.send,
    }[via]
    assert carried.await_count == 1
    lines = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert len(lines) == 1
    assert lines[0].startswith("\u26d4 `/attendance sync` refused for Manager (<@77>) \u2014 ")
    assert reason in lines[0]


# ---------------------------------------------------------------------------
# The sync's announcements follow on the change queue (#439, slice 6b, C37)
# ---------------------------------------------------------------------------

# The announcement's own jobs, run by a real queue. The league is `review_league`'s: Pro's
# round 3 at Silverstone, its verdicts channel 704 with no heading standing, attendance on, and
# the card of each sanction posted through the attendance hook the builder hands the change types
# (`AttendanceDouble`, which records each card and refuses one where told). The change is asked
# as the sync asks it: as the bot's, its payload naming the round, the division and each
# sanction applied, as the candidate the sanctions were owed as (`review_league.candidate`).

from leaguebot.core.models.change import StepFailedOnDiscord  # noqa: E402
from leaguebot.results.services.review import verdict_announcement_service as vas  # noqa: E402
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
