"""What a league may and may not do once every division is done (issue #224).

The specification has always said that Pending completion is nearly closed, and the code let a
league repoint team roles, resync a calendar, repost standings and results, flip the reserves
setting and run the whole season-points amendment there. Several of those commands read
*the most recent season whatever its status*, so they reached a **completed or cancelled**
season as well — an archive the specification says shall never change.

`tests/unit/test_pending_completion.py` covers the *transition* into the stage and says
nothing about what is refused once a season is in it, which is why the suite passed. This file
is the other half: one table over every command the rule touches, exercised in four seasons.

**Permitted in Pending completion, and nothing else** (decided 2026-09-20):

    1. completing the season;
    2. repairing a division's channels;
    3. amending the results of a round already final.

The third is why `/round results amend` appears in the permitted table rather than the refused
one, while everything else under `/results` is refused: it is the season's last chance to
correct its record before `/season complete` draws the final classification off it.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog  # noqa: E402
from cogs.season_cog import SeasonCog  # noqa: E402
from cogs.team_cog import TeamCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage, status_of_stage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 22400
SEASON_ID = 1
DIVISION_ID = 10


# ---------------------------------------------------------------------------
# A season in a named stage, with one division that has one FINAL round
# ---------------------------------------------------------------------------

async def _db(tmp_path, stage: SeasonStage) -> str:
    path = str(tmp_path / "gates.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-01-01', ?, 1, ?)",
            (SEASON_ID, status_of_stage(stage).value, stage.value),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status, "
            "calendar_channel_id) VALUES (?, ?, 'Pro', 1, 555, 'FINISHED', 700)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO division_results_config (division_id, reserves_in_standings) "
            "VALUES (?, 1)",
            (DIVISION_ID,),
        )
        await db.commit()
    return path


def _season(stage: SeasonStage):
    return SimpleNamespace(
        id=SEASON_ID, season_number=1, stage=stage, status=status_of_stage(stage).value
    )


def _bot(db_path: str, stage: SeasonStage | None):
    """A bot whose live-season lookup answers with a season in *stage*, or none at all.

    A COMPLETED or CANCELLED stage is *not* a live season: `get_setup_or_active_season`
    returns None for one, which is exactly how the archive becomes unreachable. Passing the
    archived stage here therefore means "the server's only season is that one".
    """
    live = None if stage in (None, SeasonStage.COMPLETED, SeasonStage.CANCELLED) else _season(stage)
    archived = _season(stage) if stage in (SeasonStage.COMPLETED, SeasonStage.CANCELLED) else live

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=live)
    # The old lookup, still answered so that a command which has *not* been moved off it
    # would go on working and the test would catch that rather than erroring.
    bot.season_service.get_season_for_server = AsyncMock(return_value=archived)
    bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(id=DIVISION_ID, name="Pro", tier=1, calendar_channel_id=700)]
    )
    bot.season_service.get_division_rounds = AsyncMock(return_value=[])
    bot.team_service = MagicMock()
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[{"name": "Red", "role_id": 900, "is_reserve": False}]
    )
    bot.placement_service = MagicMock()
    bot.placement_service.set_team_role_config = AsyncMock()
    bot.placement_service.delete_team_role_config = AsyncMock()
    bot.placement_service.swap_team_role = AsyncMock(return_value=0)
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _said(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _cog(cls, bot):
    cog = cls.__new__(cls)
    cog.bot = bot
    return cog


# ---------------------------------------------------------------------------
# The table. Each entry runs one command against the bot it is given.
# ---------------------------------------------------------------------------

async def _team_role(bot, interaction):
    cog = _cog(TeamCog, bot)
    role = MagicMock(id=901, mention="<@&901>", name="Red")
    await undecorate(TeamCog.team_role)(cog, interaction, "Red", role)


async def _team_reserve_role(bot, interaction):
    cog = _cog(TeamCog, bot)
    role = MagicMock(id=902, mention="<@&902>", name="Reserves")
    await undecorate(TeamCog.team_reserve_role)(cog, interaction, role)


async def _calendar_sync(bot, interaction):
    cog = _cog(SeasonCog, bot)
    with patch("services.calendar_post_service.tracks_by_name", new=AsyncMock(return_value={})), \
            patch(
                "services.calendar_post_service.post_division_calendar",
                new=AsyncMock(return_value=SimpleNamespace(
                    problem=None, notices=[], posted_as_image=False
                )),
            ):
        await undecorate(SeasonCog.division_calendar_sync)(cog, interaction, "Pro")


async def _reserves_toggle(bot, interaction):
    await undecorate(ResultsCog.reserves_toggle)(_cog(ResultsCog, bot), interaction, "Pro")


async def _standings_sync(bot, interaction):
    with patch(
        "services.results_post_service.repost_standings_for_division",
        new=AsyncMock(return_value="ok"),
    ):
        await undecorate(ResultsCog.standings_sync)(_cog(ResultsCog, bot), interaction, "Pro")


async def _rounds_sync(bot, interaction):
    with patch(
        "services.results_post_service.repost_results_for_division",
        new=AsyncMock(return_value="ok"),
    ):
        await undecorate(ResultsCog.rounds_sync)(_cog(ResultsCog, bot), interaction, "Pro")


async def _amend_toggle(bot, interaction):
    await undecorate(ResultsCog.amend_toggle)(_cog(ResultsCog, bot), interaction)


async def _amend_revert(bot, interaction):
    await undecorate(ResultsCog.amend_revert)(_cog(ResultsCog, bot), interaction)


async def _amend_review(bot, interaction):
    await undecorate(ResultsCog.amend_review)(_cog(ResultsCog, bot), interaction)


async def _amend_bulk_session(bot, interaction):
    from discord import app_commands

    choice = app_commands.Choice(name="Feature Race", value="FEATURE_RACE")
    interaction.response.send_modal = AsyncMock()
    await undecorate(ResultsCog.bulk_amend_session)(
        _cog(ResultsCog, bot), interaction, "Standard", choice
    )


#: Every command the rule closes. Named as a league types it, so a failure reads as the rule.
REFUSED = {
    "/team role": _team_role,
    "/team reserve-role": _team_reserve_role,
    "/division calendar-sync": _calendar_sync,
    "/results reserves toggle": _reserves_toggle,
    "/results standings sync": _standings_sync,
    "/results rounds sync": _rounds_sync,
    "/results amend toggle": _amend_toggle,
    "/results amend revert": _amend_revert,
    "/results amend review": _amend_review,
    "/results amend bulk-session": _amend_bulk_session,
}

_NAMES = sorted(REFUSED)


# ---------------------------------------------------------------------------
# Refused once every division is done
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", _NAMES)
async def test_every_closed_command_is_refused_in_pending_completion(tmp_path, name):
    db_path = await _db(tmp_path, SeasonStage.PENDING_COMPLETION)
    interaction = _interaction()

    await REFUSED[name](_bot(db_path, SeasonStage.PENDING_COMPLETION), interaction)

    said = _said(interaction)
    assert said, f"{name} said nothing at all in Pending completion"
    assert "done" in said.lower(), f"{name} did not say why it was refused: {said!r}"


async def test_the_reserves_setting_is_not_written_in_pending_completion(tmp_path):
    """The one command of the set that writes on its way past, so the one worth reading back."""
    db_path = await _db(tmp_path, SeasonStage.PENDING_COMPLETION)
    interaction = _interaction()

    await _reserves_toggle(_bot(db_path, SeasonStage.PENDING_COMPLETION), interaction)

    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT reserves_in_standings FROM division_results_config "
                "WHERE division_id = ?",
                (DIVISION_ID,),
            )
        ).fetchone()
    assert row["reserves_in_standings"] == 1


# ---------------------------------------------------------------------------
# Refused on an archived season — the rule the specification already carried
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", [SeasonStage.COMPLETED, SeasonStage.CANCELLED])
@pytest.mark.parametrize("name", _NAMES)
async def test_every_closed_command_is_refused_on_an_archived_season(tmp_path, name, stage):
    """A completed or cancelled season is never changed — core specification, *The archive*."""
    db_path = await _db(tmp_path, stage)
    interaction = _interaction()

    await REFUSED[name](_bot(db_path, stage), interaction)

    assert _said(interaction), f"{name} said nothing on a {stage.value} season"


@pytest.mark.parametrize("name", _NAMES)
async def test_no_closed_command_reads_the_most_recent_season(tmp_path, name):
    """The lookup itself is the fix: reading the live season is what makes the archive
    unreachable. A command still on `get_season_for_server` would pass the test above by
    accident on some other refusal, and this catches that.
    """
    db_path = await _db(tmp_path, SeasonStage.COMPLETED)
    bot = _bot(db_path, SeasonStage.COMPLETED)

    await REFUSED[name](bot, _interaction())

    bot.season_service.get_season_for_server.assert_not_awaited()


# ---------------------------------------------------------------------------
# Still open while the season is being raced
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", _NAMES)
async def test_every_closed_command_still_runs_while_the_season_is_ongoing(tmp_path, name):
    db_path = await _db(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()

    await REFUSED[name](_bot(db_path, SeasonStage.ONGOING), interaction)

    said = _said(interaction)
    assert "Every division of this season is done" not in said
    assert "no longer has anything to act on" not in said


async def test_the_bulk_amend_modal_is_shown_while_the_season_is_ongoing(tmp_path):
    """The gate sits in front of the modal, so the modal is what proves it let the
    command through — `_said` sees nothing either way.
    """
    db_path = await _db(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()

    await _amend_bulk_session(_bot(db_path, SeasonStage.ONGOING), interaction)

    interaction.response.send_modal.assert_awaited_once()


async def test_the_bulk_amend_modal_is_not_shown_in_pending_completion(tmp_path):
    """Refused before it is shown, so a manager never types a screenful to no purpose."""
    db_path = await _db(tmp_path, SeasonStage.PENDING_COMPLETION)
    interaction = _interaction()

    await _amend_bulk_session(_bot(db_path, SeasonStage.PENDING_COMPLETION), interaction)

    interaction.response.send_modal.assert_not_awaited()


# ---------------------------------------------------------------------------
# The three things that stay open
# ---------------------------------------------------------------------------

async def test_a_division_channel_is_still_repaired_in_pending_completion(tmp_path):
    """Completing posts the final classification and the final attendance sheet to those
    channels, so one deleted before completion has to be repointed.
    """
    db_path = await _db(tmp_path, SeasonStage.PENDING_COMPLETION)
    bot = _bot(db_path, SeasonStage.PENDING_COMPLETION)
    bot.season_service.set_division_standings_channel = AsyncMock(return_value=None)
    cog = _cog(SeasonCog, bot)
    interaction = _interaction()
    channel = MagicMock(id=808, mention="<#808>", name="standings")

    with patch(
        "services.channel_registry_service.find_channel_use", new=AsyncMock(return_value=None)
    ):
        await cog._set_division_channel(interaction, "Pro", channel, "standings")

    said = _said(interaction)
    assert "✅" in said, said
    bot.season_service.set_division_standings_channel.assert_awaited_once()


async def test_round_results_amend_is_not_turned_away_by_the_stage(tmp_path):
    """The season's last chance to correct its record before the final classification."""
    db_path = await _db(tmp_path, SeasonStage.PENDING_COMPLETION)
    bot = _bot(db_path, SeasonStage.PENDING_COMPLETION)
    bot.module_service.is_results_enabled = AsyncMock(return_value=True)
    cog = _cog(SeasonCog, bot)
    interaction = _interaction()

    await undecorate(SeasonCog.round_results_amend)(cog, interaction, "Pro", 1)

    said = _said(interaction)
    # It gets as far as looking for the round, which this season does not hold — not turned
    # away on the stage.
    assert "Round 1 not found" in said, said


async def test_round_results_amend_is_refused_on_an_archived_season(tmp_path):
    """It deletes a round's driver rows and re-inserts them; nothing puts the old ones back."""
    db_path = await _db(tmp_path, SeasonStage.COMPLETED)
    bot = _bot(db_path, SeasonStage.COMPLETED)
    cog = _cog(SeasonCog, bot)
    interaction = _interaction()

    await undecorate(SeasonCog.round_results_amend)(cog, interaction, "Pro", 1)

    said = _said(interaction)
    assert "archive" in said, said
    bot.season_service.get_season_for_server.assert_not_awaited()
