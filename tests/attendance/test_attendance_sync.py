"""`/attendance sync` — the command's gate (decided 2026-09-18, issue #239).

A sanction that did not apply used to be recoverable only by hand, through `/driver sack` or
`/driver move`, which post no verdict and mark no sheet. The sync is the recovery: every
finalised round from the one named on is recomputed from its results, the division's sheet is
posted for the latest of them, and the sanctions are enforced against that latest round, where
the running totals stand.

The sync is one change on the queue (#285): what it does, and that a second run and a stop part
way through are harmless, is pinned through the queue in `test_attendance_sync_change.py`. Here
are the command's refusals, each answered and recorded with no change asked of the queue.
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

