"""The three `/results` commands a manager reaches for when a channel has gone wrong.

Issue #208. `/results standings sync`, `/results rounds sync` and `/results reserves toggle`
were uncovered. The two sync commands exist because the automatic posts can go missing — a
channel deleted, a message removed, a restart at the wrong moment — and a league should not
have to re-race a round to get its results back on the board.

**Each says which of three things happened.** "Synced", "there are no completed rounds", and
"no channel is configured" are different situations with different next steps, and a manager
told only that nothing happened cannot tell an empty division from an unconfigured one. The
service returns a status and the command translates it; each branch is tested, because
collapsing them into a single "done" is the obvious simplification and it destroys the one
useful thing the command says.

**Only a success is logged.** A sync that found nothing to post did not change anything a
league can see, and a log line saying otherwise would make the log lie about what a manager
did.

**The reserves toggle reads before it writes, and defaults to showing them.** A division with
no configuration row at all is showing reserves — that is the schema default and the league
never chose otherwise — so the first toggle must *hide* them. Reading the current value wrongly
would make the first press appear to do nothing, and the second press then does the opposite of
what the manager expects for ever after.

**The toggle writes with an upsert.** A division that has never had a results configuration
still needs the setting stored, and a plain UPDATE would silently do nothing on it.

**All three are gated on the module.** They post into channels the results module owns, and a
league with it switched off would be given a success message over a post that never happened.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.models.season import SeasonStage

from leaguebot.results.cogs.results_cog import ResultsCog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.undecorate import undecorate

SERVER_ID = 11908
SEASON_ID = 1
DIVISION_ID = 11


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path, *, name: str = "results_sync", config_row: bool = True, reserves: int | None = 1
) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        if config_row:
            await db.execute(
                "INSERT INTO division_results_config (division_id, reserves_in_standings) "
                "VALUES (?, ?)",
                (DIVISION_ID, reserves),
            )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    enabled: bool = True,
    season=SimpleNamespace(id=SEASON_ID, season_number=1, stage=SeasonStage.ONGOING),
    divisions=None,
) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions
        if divisions is not None
        else [SimpleNamespace(id=DIVISION_ID, name="Pro", tier=1)]
    )
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = 77
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _standings_sync(cog, interaction, *, division="Pro", status="ok"):
    with patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(return_value=status),
    ) as repost:
        await undecorate(ResultsCog.standings_sync)(cog, interaction, division)
    return repost


async def _rounds_sync(cog, interaction, *, division="Pro", status="ok"):
    with patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(return_value=status),
    ) as repost:
        await undecorate(ResultsCog.rounds_sync)(cog, interaction, division)
    return repost


async def _toggle(cog, interaction, *, division="Pro"):
    await undecorate(ResultsCog.reserves_toggle)(cog, interaction, division)


async def _reserves(db_path) -> int | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT reserves_in_standings FROM division_results_config WHERE division_id = ?",
            (DIVISION_ID,),
        )
        row = await cursor.fetchone()
        return row["reserves_in_standings"] if row else None


SYNCS = [("standings", _standings_sync), ("rounds", _rounds_sync)]


# ---------------------------------------------------------------------------
# What both sync commands share
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_sync_reposts_the_division(tmp_path, label, run):
    db_path = await _make_db(tmp_path, name=f"sync_{label}")
    cog = _make_cog(db_path)

    repost = await run(cog, _interaction())

    repost.assert_awaited_once()
    assert repost.await_args.args[1] == DIVISION_ID


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_successful_sync_says_so(tmp_path, label, run):
    db_path = await _make_db(tmp_path, name=f"sync_ok_{label}")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction, status="ok")

    assert "synced" in _replied(interaction)
    assert "Pro" in _replied(interaction)


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_division_with_no_completed_rounds_is_told_so(tmp_path, label, run):
    """Distinct from an unconfigured channel: there is nothing to post rather than nowhere
    to post it, and the manager's next step differs."""
    db_path = await _make_db(tmp_path, name=f"sync_norounds_{label}")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction, status="no_rounds")

    replied = _replied(interaction)
    assert "No completed rounds" in replied
    assert "synced" not in replied


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_division_with_no_channel_configured_is_told_so(tmp_path, label, run):
    """The fix is a `/division ...-channel` command, which the manager will not think of if
    they are told the division has no rounds."""
    db_path = await _make_db(tmp_path, name=f"sync_nochannel_{label}")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction, status="no_channel")

    replied = _replied(interaction)
    assert "no" in replied and "channel configured" in replied
    assert "synced" not in replied


@pytest.mark.parametrize("label,run", SYNCS)
async def test_only_a_successful_sync_is_logged(tmp_path, label, run):
    """A sync that found nothing to post did not change anything a league can see, and a
    log line saying otherwise would make the log lie about what a manager did."""
    db_path = await _make_db(tmp_path, name=f"sync_log_{label}")
    cog = _make_cog(db_path)

    await run(cog, _interaction(), status="no_rounds")
    cog.bot.output_router.post_log.assert_not_awaited()

    await run(cog, _interaction(), status="ok")
    cog.bot.output_router.post_log.assert_awaited_once()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_server_with_no_season_is_refused(tmp_path, label, run):
    db_path = await _make_db(tmp_path, name=f"sync_noseason_{label}")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    repost = await run(cog, interaction)

    # The refusal names the archive rule (issue #224): a server whose only season is
    # completed or cancelled reaches this same branch, because the command now asks for
    # the *live* season and an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)
    repost.assert_not_awaited()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_an_unknown_division_is_refused_by_name(tmp_path, label, run):
    db_path = await _make_db(tmp_path, name=f"sync_nodiv_{label}")
    cog = _make_cog(db_path)
    interaction = _interaction()

    repost = await run(cog, interaction, division="Rookie")

    assert "Rookie" in _replied(interaction)
    repost.assert_not_awaited()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_the_division_is_matched_regardless_of_case(tmp_path, label, run):
    db_path = await _make_db(tmp_path, name=f"sync_case_{label}")
    cog = _make_cog(db_path)

    repost = await run(cog, _interaction(), division="pRo")

    repost.assert_awaited_once()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_sync_is_refused_while_the_module_is_off(tmp_path, label, run):
    """It posts into channels the module owns; a league with it off would be given a
    success message over a post that never happened."""
    db_path = await _make_db(tmp_path, name=f"sync_off_{label}")
    cog = _make_cog(db_path, enabled=False)
    interaction = _interaction()
    interaction.response.send_message = AsyncMock()

    repost = await run(cog, interaction)

    repost.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_sync_defers_before_working(tmp_path, label, run):
    """Reposting a division's whole season of results is well past three seconds."""
    db_path = await _make_db(tmp_path, name=f"sync_defer_{label}")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await run(cog, interaction)

    interaction.response.defer.assert_awaited_once()


async def _open_amendment(db_path, *, ended=False):
    """An amendment of round 2 of the division, open — or *ended*, its channel left behind."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (20, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "closed_at) VALUES (20, 8200, '[\"FEATURE_RACE\"]', '2026-02-01T00:00:00+00:00', ?)",
            ("2026-02-01T00:20:00+00:00" if ended else None,),
        )
        await db.commit()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_a_sync_waits_while_a_round_of_the_division_is_amended(tmp_path, label, run):
    """#345, decided 2026-09-21. The amendment's corrections are in the database, unapproved;
    a sync reposts from it, so it would publish them, and leave them published if the amendment
    were then cancelled or lapsed."""
    db_path = await _make_db(tmp_path, name=f"sync_held_{label}")
    await _open_amendment(db_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    repost = await run(cog, interaction)

    repost.assert_not_awaited()
    replied = _replied(interaction)
    assert "Round 2 of **Pro** is being amended in <#8200>" in replied
    assert "Run this again then." in replied
    cog.bot.output_router.post_log.assert_not_awaited()


@pytest.mark.parametrize("label,run", SYNCS)
async def test_an_ended_amendment_does_not_hold_a_sync(tmp_path, label, run):
    """One that ended but could not delete its channel keeps its row; it is not open. The
    `RESULT_AMENDED | Incomplete` entry sends a manager to exactly these commands."""
    db_path = await _make_db(tmp_path, name=f"sync_not_held_{label}")
    await _open_amendment(db_path, ended=True)
    cog = _make_cog(db_path)

    repost = await run(cog, _interaction())

    repost.assert_awaited_once()


async def test_the_two_syncs_call_different_services(tmp_path):
    """They sit beside each other and read almost identically; one calling the other's
    service would repost standings where a manager asked for results."""
    db_path = await _make_db(tmp_path, name="sync_distinct")
    cog = _make_cog(db_path)

    with patch(
        "leaguebot.results.services.results_post_service.repost_standings_for_division",
        new=AsyncMock(return_value="ok"),
    ) as standings, patch(
        "leaguebot.results.services.results_post_service.repost_results_for_division",
        new=AsyncMock(return_value="ok"),
    ) as rounds:
        await undecorate(ResultsCog.standings_sync)(cog, _interaction(), "Pro")

    standings.assert_awaited_once()
    rounds.assert_not_awaited()


# ---------------------------------------------------------------------------
# The reserves toggle
# ---------------------------------------------------------------------------


async def test_a_division_showing_reserves_is_toggled_to_hidden(tmp_path):
    db_path = await _make_db(tmp_path, name="reserves_hide", reserves=1)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _toggle(cog, interaction)

    assert await _reserves(db_path) == 0
    assert "hidden" in _replied(interaction)


async def test_a_division_hiding_reserves_is_toggled_to_visible(tmp_path):
    db_path = await _make_db(tmp_path, name="reserves_show", reserves=0)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _toggle(cog, interaction)

    assert await _reserves(db_path) == 1
    assert "visible" in _replied(interaction)


async def test_a_division_with_no_configuration_is_showing_reserves(tmp_path):
    """The schema default, and the league never chose otherwise — so the first toggle must
    *hide* them. Reading it wrongly makes the first press appear to do nothing, and every
    press after that does the opposite of what the manager expects."""
    db_path = await _make_db(tmp_path, name="reserves_default", config_row=False)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _toggle(cog, interaction)

    assert await _reserves(db_path) == 0
    assert "hidden" in _replied(interaction)


async def test_the_setting_is_written_for_a_division_that_had_no_configuration(tmp_path):
    """A plain UPDATE would silently do nothing on a division that has never had a results
    configuration, and the toggle would report a change it did not make."""
    db_path = await _make_db(tmp_path, name="reserves_upsert", config_row=False)
    cog = _make_cog(db_path)

    await _toggle(cog, _interaction())

    assert await _reserves(db_path) is not None


async def test_toggling_twice_returns_to_where_it_started(tmp_path):
    """Which is what makes it a toggle rather than a switch that only goes one way."""
    db_path = await _make_db(tmp_path, name="reserves_twice", reserves=1)
    cog = _make_cog(db_path)

    await _toggle(cog, _interaction())
    await _toggle(cog, _interaction())

    assert await _reserves(db_path) == 1


async def test_the_toggle_leaves_the_divisions_other_settings_alone(tmp_path):
    """The upsert names one column; a row rebuilt from scratch would clear the channels the
    division posts into."""
    db_path = await _make_db(tmp_path, name="reserves_keeps")
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE division_results_config SET results_channel_id = 700, "
            "standings_channel_id = 701 WHERE division_id = ?",
            (DIVISION_ID,),
        )
        await db.commit()
    cog = _make_cog(db_path)

    await _toggle(cog, _interaction())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id, standings_channel_id FROM division_results_config "
            "WHERE division_id = ?",
            (DIVISION_ID,),
        )
        row = dict(await cursor.fetchone())
    assert row == {"results_channel_id": 700, "standings_channel_id": 701}


async def test_the_toggle_is_logged_with_the_state_it_reached(tmp_path):
    """"Toggled" tells a reader nothing; which way it went is the whole content."""
    db_path = await _make_db(tmp_path, name="reserves_log", reserves=1)
    cog = _make_cog(db_path)

    await _toggle(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/results reserves toggle" in logged
    assert "hidden" in logged
    assert "Pro" in logged


async def test_an_unknown_division_is_not_toggled(tmp_path):
    db_path = await _make_db(tmp_path, name="reserves_nodiv")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _toggle(cog, interaction, division="Rookie")

    assert "Rookie" in _replied(interaction)
    assert await _reserves(db_path) == 1


async def test_a_server_with_no_season_is_not_toggled(tmp_path):
    db_path = await _make_db(tmp_path, name="reserves_noseason")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _toggle(cog, interaction)

    # The refusal names the archive rule (issue #224): a server whose only season is
    # completed or cancelled reaches this same branch, because the command now asks for
    # the *live* season and an archived one is never returned.
    assert "there is none" in _replied(interaction)
    assert "archive" in _replied(interaction)
    assert await _reserves(db_path) == 1


async def test_the_toggle_is_refused_while_the_module_is_off(tmp_path):
    """The setting only affects standings the results module posts."""
    db_path = await _make_db(tmp_path, name="reserves_off")
    cog = _make_cog(db_path, enabled=False)
    interaction = _interaction()

    await _toggle(cog, interaction)

    assert await _reserves(db_path) == 1
    interaction.response.defer.assert_not_awaited()
