"""Confirming placements, as issue #220 specifies it.

`/season placements-review` runs only while the season is in Placements. Its button — and the
confirmation behind it — is withheld while any signup is unsettled or any division lacks its
lineup or calendar channel. Confirming commits every placement made.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import SeasonCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.season_service import SeasonService
from tests.support.undecorate import undecorate

SERVER_ID = 22070
SEASON_ID = 7
USER_ID = 42


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "confirmation.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', 'SETUP', 1, 'PLACEMENTS')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, "
            "lineup_channel_id, calendar_channel_id, status) VALUES "
            "(1, ?, 'Pro', 1, 1, 100, 101, 'SETUP'), "
            "(2, ?, 'Am', 1, 2, NULL, 201, 'SETUP'), "
            "(3, ?, 'Gone', 1, 3, NULL, NULL, 'CANCELLED')",
            (SEASON_ID, SEASON_ID, SEASON_ID),
        )
        await db.commit()
    return path


def _cog(db_path, *, weather=False, results=False, attendance=False) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    cog.bot.module_service.is_results_enabled = AsyncMock(return_value=results)
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    return cog


def _guild(*, gone: tuple[int, ...] = (), unreadable: tuple[int, ...] = ()):
    """A server holding every channel but those *gone*; *unreadable* ones fail to fetch."""
    import discord

    async def _fetch(channel_id):
        if channel_id in gone:
            raise discord.NotFound(MagicMock(status=404), "Unknown Channel")
        raise discord.HTTPException(MagicMock(status=503), "unavailable")

    guild = MagicMock()
    guild.get_channel = MagicMock(
        side_effect=lambda cid: None if cid in gone or cid in unreadable else MagicMock()
    )
    guild.fetch_channel = AsyncMock(side_effect=_fetch)
    return guild


async def _set_module_channels(db_path) -> None:
    """Every module channel of every division, set to ids of their own."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        for division_id in (1, 2):
            await db.execute(
                "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?",
                (division_id * 100 + 10, division_id),
            )
            await db.execute(
                "INSERT INTO division_results_config (division_id, results_channel_id, "
                "standings_channel_id, penalty_channel_id) VALUES (?, ?, ?, ?)",
                (division_id, division_id * 100 + 20, division_id * 100 + 21,
                 str(division_id * 100 + 22)),
            )
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, rsvp_channel_id, "
                "attendance_channel_id) VALUES (?, ?, ?)",
                (division_id, str(division_id * 100 + 30), str(division_id * 100 + 31)),
            )
        await db.commit()


async def _driver(db_path, uid: str, state: str, name: str | None = None):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, ?)",
            (uid, state),
        )
        if name:
            await db.execute(
                "INSERT INTO signup_records (season_id, discord_user_id, "
                "server_display_name) VALUES (?, ?, ?)",
                (SEASON_ID, uid, name),
            )
        await db.commit()


# ── The faults ──────────────────────────────────────────────────────────────────────


async def test_every_unsettled_signup_is_named(db_path):
    await _driver(db_path, "1", "UNASSIGNED", "Alice")
    await _driver(db_path, "2", "PENDING_ADMIN_APPROVAL", "Bob")
    await _driver(db_path, "3", "PENDING_DRIVER_CORRECTION")
    await _driver(db_path, "4", "ASSIGNED", "Placed")
    await _driver(db_path, "5", "NOT_SIGNED_UP", "Left")

    unsettled, _ = await _cog(db_path)._placement_confirmation_faults(SEASON_ID)

    joined = "\n".join(unsettled)
    assert len(unsettled) == 3
    assert "**Alice** — not yet placed" in joined
    assert "**Bob** — awaiting approval" in joined
    assert "**3** — correcting their signup" in joined


async def test_a_division_missing_its_lineup_or_calendar_channel_is_named(db_path):
    _, channels = await _cog(db_path)._placement_confirmation_faults(SEASON_ID)

    assert channels == ["**Am** has no lineup channel — `/division lineup-channel`."]


@pytest.mark.parametrize(
    "module,labels",
    [
        ("weather", ["weather channel"]),
        ("results", ["results channel", "standings channel", "verdicts channel"]),
        ("attendance", ["RSVP channel", "attendance channel"]),
    ],
)
async def test_a_module_s_channels_are_needed_only_while_it_is_enabled(db_path, module, labels):
    """Every channel the season will post to (#374) — and no channel a module that is off
    would never post to."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        await db.commit()

    _, off = await _cog(db_path)._placement_confirmation_faults(SEASON_ID)
    _, on = await _cog(db_path, **{module: True})._placement_confirmation_faults(SEASON_ID)

    assert off == []
    assert len(on) == 2 * len(labels), "each of the two live divisions lacks each of them"
    for label in labels:
        assert f"**Pro** has no {label}" in "\n".join(on)


def _named_by_its_module(label: str, command: str):
    return pytest.param(
        label,
        command,
        id=label,
        marks=pytest.mark.xfail(
            strict=True, reason=f"#462: the {label} is still named with its /division command"
        ),
    )


@pytest.mark.parametrize(
    "label,command",
    [
        _named_by_its_module("weather channel", "/weather channel"),
        _named_by_its_module("results channel", "/results channel results"),
        _named_by_its_module("standings channel", "/results channel standings"),
        _named_by_its_module("verdicts channel", "/results channel verdicts"),
        _named_by_its_module("RSVP channel", "/attendance channel rsvp"),
        _named_by_its_module("attendance channel", "/attendance channel attendance"),
    ],
)
async def test_a_missing_module_channel_is_named_with_the_command_that_now_sets_it(
    db_path, label, command
):
    """Each missing channel is named with its division and the command that sets it, and a
    module's channels are set under that module's own group (#462)."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        await db.commit()
    cog = _cog(db_path, weather=True, results=True, attendance=True)

    _, channels = await cog._placement_confirmation_faults(SEASON_ID)

    assert f"**Pro** has no {label} — `{command}`." in channels


async def test_a_channel_deleted_from_the_server_is_named_with_its_command(db_path):
    """Nothing clears a channel's id when Discord deletes it, so mid-season every channel is
    still set — whether it is still there is the question worth asking (#374)."""
    await _set_module_channels(db_path)
    cog = _cog(db_path, weather=True, results=True, attendance=True)

    _, channels = await cog._placement_confirmation_faults(
        SEASON_ID, _guild(gone=(100, 222, 231))
    )

    assert channels == [
        "**Pro**'s lineup channel is no longer on the server — `/division lineup-channel`.",
        "**Am**'s verdicts channel is no longer on the server — `/division verdicts-channel`.",
        "**Am**'s attendance channel is no longer on the server — "
        "`/division attendance-channel`.",
    ]


async def test_a_channel_that_could_not_be_fetched_is_not_called_gone(db_path):
    """Only NotFound proves a channel gone; a check that could not tell refuses nothing."""
    await _set_module_channels(db_path)
    cog = _cog(db_path, weather=True, results=True, attendance=True)

    _, channels = await cog._placement_confirmation_faults(
        SEASON_ID, _guild(unreadable=(100, 120))
    )

    assert channels == []


async def test_a_cancelled_division_is_not_asked_for_its_channels(db_path):
    """`Gone` is cancelled and holds none: it posts nothing."""
    await _set_module_channels(db_path)
    cog = _cog(db_path, weather=True, results=True, attendance=True)

    _, channels = await cog._placement_confirmation_faults(SEASON_ID, _guild())

    assert channels == []


async def test_a_settled_season_with_its_channels_has_no_faults(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        await db.commit()
    await _driver(db_path, "4", "ASSIGNED")

    assert await _cog(db_path)._placement_confirmation_faults(SEASON_ID) == ([], [])


# ── The review and the confirmation refuse ─────────────────────────────────────────


@pytest.mark.parametrize("stage", [SeasonStage.CONFIGURATION, SeasonStage.SIGNUPS])
async def test_the_review_is_refused_outside_placements(db_path, stage):
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=stage)
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert "only be reviewed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    interaction.response.defer.assert_not_awaited()


async def test_the_confirmation_refuses_an_unsettled_signup_and_commits_nothing(db_path):
    await _driver(db_path, "1", "UNASSIGNED", "Alice")
    cog = _cog(db_path)
    pending = SimpleNamespace(season_id=SEASON_ID, season_number=1)
    cog._pending = {USER_ID: pending}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog.bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(status="SETUP")]
    )
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    assert "Alice" in interaction.followup.send.await_args.args[0]
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_confirmation_names_every_missing_channel_at_once_and_commits_nothing(db_path):
    """Four channels wrong is one refusal, not four attempts at starting a season. The
    results module's channels are judged here with every other division channel (#374); the
    gate that once judged them beside the points could no longer be reached."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE divisions SET lineup_channel_id = 200 WHERE id = 2")
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id, "
            "standings_channel_id, penalty_channel_id) VALUES (1, NULL, 701, '702'), "
            "(2, 800, NULL, NULL)"
        )
        await db.commit()
    cog = _cog(db_path, results=True)
    cog._pending = {USER_ID: SimpleNamespace(season_id=SEASON_ID, season_number=1)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog.bot.season_service.get_divisions = AsyncMock(
        return_value=[SimpleNamespace(status="SETUP")]
    )
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild = _guild()
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    (call,) = interaction.followup.send.await_args_list
    refusal = call.args[0]
    assert "**Pro** has no results channel — `/division results-channel`." in refusal
    assert "**Am** has no standings channel" in refusal
    assert "**Am** has no verdicts channel — `/division verdicts-channel`." in refusal
    cog.bot.season_service.transition_to_active.assert_not_awaited()


@pytest.mark.parametrize("divisions", [[], [SimpleNamespace(status="CANCELLED")]])
async def test_the_confirmation_refuses_a_season_with_no_division(db_path, divisions):
    """A season with nothing to race would never reach Pending completion (issue #220)."""
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.PLACEMENTS)
    cog.bot.season_service.get_divisions = AsyncMock(return_value=divisions)
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    reply = interaction.followup.send.await_args.args[0]
    assert "no divisions" in reply and "Nothing has been approved" in reply
    cog.bot.season_service.transition_to_active.assert_not_awaited()


async def test_the_confirmation_refuses_a_season_no_longer_in_placements(db_path):
    cog = _cog(db_path)
    cog._pending = {USER_ID: SimpleNamespace(season_id=SEASON_ID)}
    cog.bot.season_service.get_stage = AsyncMock(return_value=SeasonStage.ONGOING)
    cog.bot.season_service.transition_to_active = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = USER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await SeasonCog._do_approve(cog, interaction)

    assert "no longer in placements" in interaction.followup.send.await_args.args[0]
    cog.bot.season_service.transition_to_active.assert_not_awaited()


# ── Committing ─────────────────────────────────────────────────────────────────────


async def test_confirming_commits_every_placement_of_the_season(db_path):
    async with get_connection(db_path) as db:
        for profile_id in (1, 2):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, str(profile_id)),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, committed) VALUES (?, ?, 1, 0)",
                (profile_id, SEASON_ID),
            )
        await db.commit()

    assert await SeasonService(db_path).commit_placements(SEASON_ID) == 2

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT committed FROM driver_season_assignments")
        assert [row["committed"] for row in await cursor.fetchall()] == [1, 1]
