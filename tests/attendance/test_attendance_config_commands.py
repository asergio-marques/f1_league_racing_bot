"""`/attendance config show`, and the names the setters are typed by — issue #119.

`/attendance config rsvp-absent-penalty` called `update_rsvp_absent_penalty`, a name that
lived only between migrations 034 and 038. Migration 038 renamed the column back to
`no_show_penalty` and carried the service with it; the cog was left behind, and the penalty a
driver pays for accepting a check-in and then not appearing was unreachable by any command.

The eight setters are changes on the queue (#285), and what each does — its change, its audit
entry, its log line, its reply, a value already held, a fault in the save and every refusal — is
pinned against the real queue in `test_attendance_setting_changes.py`, which drives every one of
the eight, so the next rename that stops halfway is caught on whichever command it lands. What is
left here is `config show`, which changes nothing and records nothing, and the setters' names.

**`spec=` is the whole mechanism of these tests.** The service double is built with
`AsyncMock(spec=AttendanceService)`, so calling a method the real service does not define
raises `AttributeError` instead of being silently recorded. Do not relax the `spec=` to make a
future test easier to write.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
from leaguebot.attendance.models.attendance import AttendanceConfig
from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService

SERVER_ID = 9119


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _config(**overrides) -> AttendanceConfig:
    """A default attendance configuration, with the timing invariant satisfied."""
    values = dict(
        module_enabled=True,
        rsvp_notice_days=5,
        rsvp_last_notice_hours=24,
        rsvp_deadline_hours=2,
        no_rsvp_penalty=1,
        absent_penalty=1,
        no_show_penalty=1,
        autoreserve_threshold=None,
        autosack_threshold=None,
    )
    values.update(overrides)
    return AttendanceConfig(**values)

def _make_cog(*, cfg: AttendanceConfig | None = None) -> AttendanceCog:
    """An `AttendanceCog` whose bot carries a spec-bound attendance service.

    The module reads as enabled and no season is active, so `config show` runs past its guard
    and on to the read the test is actually about.
    """
    bot = MagicMock()
    bot.attendance_service = AsyncMock(spec=AttendanceService)
    bot.attendance_service.get_config.return_value = cfg if cfg is not None else _config()
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.season_service = MagicMock()
    bot.season_service.get_confirmed_season = AsyncMock(return_value=None)
    bot.db_path = "unused.db"
    bot.change_queue.wait_out_the_hold = AsyncMock(return_value=False)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return AttendanceCog(bot)


def _interaction() -> MagicMock:
    """A Discord interaction stub. `response` is sync-attribute, async-method, as discord.py's is."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.display_name = "Manager"
    interaction.user.id = 42
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(
            interaction.response.defer.await_count
            or interaction.response.send_message.await_count
        )
    )
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _invoke(command, cog: AttendanceCog, interaction, *args) -> None:
    """Call a slash command's body, stepping past the `@league_manager_only` guard.

    `_tier_guard` wraps with `functools.wraps`, so `__wrapped__` is the undecorated function.
    Permission is covered by the channel-guard tests; these tests are about what the body does.
    """
    interaction.command = command
    interaction.client = cog.bot
    await command.callback.__wrapped__(cog, interaction, *args)


# ---------------------------------------------------------------------------
# The names
# ---------------------------------------------------------------------------


async def test_the_no_show_penalty_command_is_named_for_the_column_it_writes():
    """The command, the service method and the column all say "no-show" (decided 2026-09-15).

    It shipped as `rsvp-absent-penalty`, the name of a column that existed only between
    migrations 034 and 038, and nothing else in the module used that vocabulary.
    """
    assert AttendanceCog.config_no_show_penalty.name == "no-show-penalty"



# ---------------------------------------------------------------------------
# `/attendance config show`
# ---------------------------------------------------------------------------


async def test_the_configuration_is_shown_with_every_setting_a_command_can_write():
    """`show` is how a league checks what it has set, so a setting missing from it is a
    setting nobody can confirm. Each of the eight is named."""
    cog = _make_cog(
        cfg=_config(
            rsvp_notice_days=6,
            rsvp_last_notice_hours=12,
            rsvp_deadline_hours=3,
            no_rsvp_penalty=2,
            absent_penalty=4,
            no_show_penalty=6,
            autoreserve_threshold=10,
            autosack_threshold=20,
        )
    )
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    shown = interaction.response.send_message.await_args.args[0]
    for value in ("6", "12", "3", "2", "4", "10", "20"):
        assert value in shown
    assert "Attendance Configuration" in shown


async def test_an_unset_threshold_is_shown_as_disabled_rather_than_none():
    """`None` reaching a league as the word "None" reads as a bug; the thresholds are the
    only two settings that can be unset."""
    cog = _make_cog(cfg=_config(autoreserve_threshold=None, autosack_threshold=None))
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    shown = interaction.response.send_message.await_args.args[0]
    assert "disabled" in shown
    assert "None" not in shown


async def test_a_last_notice_of_zero_is_shown_as_disabled():
    """Zero hours is a legal value meaning "do not send one", and the league needs to be
    able to tell that from "zero hours before the race"."""
    cog = _make_cog(cfg=_config(rsvp_last_notice_hours=0))
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "*(disabled)*" in interaction.response.send_message.await_args.args[0]


async def test_show_refuses_a_server_with_no_configuration():
    cog = _make_cog()
    cog.bot.attendance_service.get_config.return_value = None
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "No attendance configuration found" in interaction.response.send_message.await_args.args[0]


async def test_show_is_refused_while_the_module_is_disabled():
    cog = _make_cog()
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    cog.bot.attendance_service.get_config.assert_not_awaited()


async def test_show_records_nothing_while_the_module_is_disabled():
    """A2: `config show` changes nothing, so its refusal is answered and not recorded."""
    cog = _make_cog()
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    interaction = _interaction()

    await _invoke(AttendanceCog.config_show, cog, interaction)

    assert "not enabled" in interaction.response.send_message.await_args.args[0]
    cog.bot.output_router.post_log.assert_not_awaited()
