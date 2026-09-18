"""Switching the image, attendance and signup modules off.

Issue #208, continuing `tests/unit/test_module_enable_disable.py`. Where that file covered the
dependency rules on the way *in*, this one covers what each module leaves behind on the way out
— which differs per module, deliberately, and is the part a reader would be most tempted to
make uniform.

**The image module clears its flag and nothing else** — the Principle X.6 exception. No image
configuration value names a Discord channel, role, message or scheduled job, so none can become
a stale binding while the module is off (FR-004a). That is why there is no `--preserve-config`
flag: nothing is cleared, so there is nothing to preserve (FR-004b). Every other module's
disable *does* clear something, which is what makes this one worth pinning —
`test_disabling_images_keeps_the_configuration` is the test a later reader tidying the three
into one shape would break.

**Attendance clears its division channel bindings.** Those *are* stale bindings: a channel id
recorded against a division for a module that is off would have the bot posting into it the
moment somebody turned the module back on, against a season that had moved on.

**Attendance can be disabled two ways, and the audit tells them apart.** A manager switching it
off is `ATTENDANCE_MODULE_DISABLED`; attendance going off because Results & Standings did is
`ATTENDANCE_MODULE_CASCADE_DISABLED`. The distinction matters because the second was nobody's
decision, and a league reading its log needs to see that. The cascade also skips the guard and
the reply — it has no interaction of its own to answer.

**Enabling signup writes a bare configuration row and then names the three commands that fill
it in.** A module enabled with nothing configured does nothing at all, and the next steps are
the difference between a league proceeding and a league wondering what happened.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import ModuleCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 11408
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, attendance_divisions: int = 0) -> str:
    db_path = os.path.join(str(tmp_path), "disable.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, 1)",
            (1,),
        )
        if attendance_divisions:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status) "
                "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
            )
            for index in range(1, attendance_divisions + 1):
                await db.execute(
                    "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                    "VALUES (?, 1, ?, ?, ?)",
                    (index, f"Division {index}", index, 500 + index),
                )
                await db.execute(
                    "INSERT INTO attendance_division_config "
                    "(division_id, rsvp_channel_id) VALUES (?, ?)",
                    (index, str(700000 + index)),
                )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    images_enabled: bool = True,
    attendance_enabled: bool = True,
    signup_enabled: bool = True,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=images_enabled)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.is_signup_enabled = AsyncMock(return_value=signup_enabled)
    bot.module_service.set_images_enabled = AsyncMock(return_value=None)
    bot.module_service.set_signup_enabled = AsyncMock(return_value=None)
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.save_config = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Admin"
    interaction.user.__str__ = lambda self: "Admin#0001"  # type: ignore[assignment]
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


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries ORDER BY id",
        )
        return [r["change_type"] for r in await cursor.fetchall()]


async def _division_configs(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM attendance_division_config",
        )
        return (await cursor.fetchone())["n"]


async def _attendance_flag(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config"
        )
        return (await cursor.fetchone())["module_enabled"]


# ---------------------------------------------------------------------------
# Images — the module that clears nothing
# ---------------------------------------------------------------------------


async def test_disabling_images_clears_the_flag(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._disable_images(interaction)

    cog.bot.module_service.set_images_enabled.assert_awaited_once_with(False)


async def test_disabling_images_keeps_the_configuration(tmp_path):
    """The Principle X.6 exception, and the reason no `--preserve-config` flag exists. No
    image config value names a channel, role, message or job, so none can go stale — a
    reader making the three disables uniform would clear a league's whole image setup for
    no reason."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await cog._disable_images(interaction)

    replied = _replied(interaction)
    assert "configuration is kept" in replied
    assert "re-enabling restores it exactly" in replied


async def test_disabling_images_says_what_happens_to_the_output(tmp_path):
    """Every aspect reverts to text, which is what a league will actually notice."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _make_cog(db_path)._disable_images(interaction)

    assert "reverts to text output" in _replied(interaction)


async def test_disabling_images_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, images_enabled=False)
    interaction = _interaction()

    await cog._disable_images(interaction)

    assert "already disabled" in _replied(interaction)
    cog.bot.module_service.set_images_enabled.assert_not_awaited()
    assert await _audit_types(db_path) == []


async def test_disabling_images_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path)._disable_images(_interaction())

    assert await _audit_types(db_path) == ["IMAGE_MODULE_DISABLED"]


# ---------------------------------------------------------------------------
# Attendance — the module that clears its bindings
# ---------------------------------------------------------------------------


async def test_disabling_attendance_clears_the_flag(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._disable_attendance(_interaction())

    assert await _attendance_flag(db_path) == 0


async def test_disabling_attendance_clears_its_division_channels(tmp_path):
    """Those are stale bindings: a channel recorded against a division for a module that
    is off would have the bot posting into it the moment it came back on, against a season
    that had moved along."""
    db_path = await _make_db(tmp_path, attendance_divisions=2)
    cog = _make_cog(db_path)
    assert await _division_configs(db_path) == 2

    await cog._disable_attendance(_interaction())

    assert await _division_configs(db_path) == 0


async def test_disabling_attendance_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, attendance_enabled=False)
    interaction = _interaction()

    await cog._disable_attendance(interaction)

    assert "already disabled" in _replied(interaction)
    assert await _audit_types(db_path) == []


async def test_a_manager_s_disable_is_audited_as_their_decision(tmp_path):
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path)._disable_attendance(_interaction())

    assert await _audit_types(db_path) == ["ATTENDANCE_MODULE_DISABLED"]


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


async def test_a_cascade_is_audited_as_a_cascade(tmp_path):
    """Attendance going off because Results did was nobody's decision, and a league
    reading its log needs to see that rather than a disable they cannot account for."""
    db_path = await _make_db(tmp_path)

    await _make_cog(db_path)._disable_attendance(_interaction(), cascade=True)

    assert await _audit_types(db_path) == ["ATTENDANCE_MODULE_CASCADE_DISABLED"]


async def test_a_cascade_does_the_same_work(tmp_path):
    """Only the audit type and the reply differ — the module has to end up in the same
    state either way, or a cascade would leave stale bindings a manual disable clears."""
    db_path = await _make_db(tmp_path, attendance_divisions=2)

    await _make_cog(db_path)._disable_attendance(_interaction(), cascade=True)

    assert await _attendance_flag(db_path) == 0
    assert await _division_configs(db_path) == 0


async def test_a_cascade_answers_no_interaction_of_its_own(tmp_path):
    """It is reached from the Results disable, which has already answered the manager.
    A second reply would be a follow-up to a command nobody ran."""
    db_path = await _make_db(tmp_path)
    interaction = _interaction()

    await _make_cog(db_path)._disable_attendance(interaction, cascade=True)

    assert _replied(interaction) == ""


async def test_a_cascade_runs_even_where_attendance_was_already_off(tmp_path):
    """It skips the guard deliberately — the Results disable cannot know, and running the
    clear twice is harmless where refusing would leave bindings behind."""
    db_path = await _make_db(tmp_path, attendance_divisions=1)
    cog = _make_cog(db_path, attendance_enabled=False)

    await cog._disable_attendance(_interaction(), cascade=True)

    assert await _division_configs(db_path) == 0


async def test_a_cascade_is_still_logged_to_the_league(tmp_path):
    """The league must be able to see that attendance went off, even though nobody asked
    for it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await cog._disable_attendance(_interaction(), cascade=True)

    cog.bot.output_router.post_log.assert_awaited_once()


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


async def test_enabling_signup_writes_a_bare_configuration(tmp_path):
    """So the `/signup …` commands have a row to update. Without it every one of them
    would be a silent no-op against nothing."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, signup_enabled=False)

    await cog._enable_signup(_interaction())

    cog.bot.signup_module_service.save_config.assert_awaited_once()
    saved = cog.bot.signup_module_service.save_config.await_args.args[0]
    assert saved.signup_channel_id is None
    assert saved.signups_open is False


async def test_enabling_signup_names_the_three_commands_that_follow(tmp_path):
    """A module enabled with nothing configured does nothing at all, and these are the
    difference between a league proceeding and a league wondering what happened."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, signup_enabled=False)
    interaction = _interaction()

    await cog._enable_signup(interaction)

    replied = _replied(interaction)
    for command in ("/signup channel", "/signup base-role", "/signup complete-role"):
        assert command in replied


async def test_enabling_signup_twice_does_no_work(tmp_path):
    """A second enable would overwrite the configuration with a bare one, losing the
    channel and roles the league had already set."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, signup_enabled=True)
    interaction = _interaction()

    await cog._enable_signup(interaction)

    assert "already enabled" in _replied(interaction)
    cog.bot.signup_module_service.save_config.assert_not_awaited()
    assert await _audit_types(db_path) == []


async def test_disabling_signup_twice_does_no_work(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, signup_enabled=False)
    interaction = _interaction()

    await cog._disable_signup(interaction)

    assert "already disabled" in _replied(interaction)
