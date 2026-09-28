"""Switching the image module on, with or without a rasteriser.

Issue #208. Enabling images is the one module toggle that depends on a program outside the bot,
and the way it handles that absence is the point of the file.

**The module enables even without the rasteriser** (FR-007, FR-008), so an administrator can
finish configuring while waiting for Inkscape to be installed. It says so rather than refusing —
refusing would leave a league unable to set up the module they are trying to install the
rasteriser *for*.

**Re-enabling is lossless.** `create_with_defaults` is idempotent, so an existing configuration
is left exactly as it was (FR-004a) — which is the other half of the disable clearing nothing.
Between them a league can switch the module off and on again and find every aspect, template and
toggle as they left it.

**Every aspect starts disabled.** A league enabling the module does not thereby start posting
graphics; each output aspect is opted into separately, so enabling cannot surprise a division
with a picture where it expected text.

**A failed enable puts the flag back down.** The configuration write and the flag are separate
calls, so a failure between them would leave the module reading as on with nothing configured —
on-but-broken, which is the state the rollback exists to prevent.

`converter_available` is **monkeypatched rather than marked**. `CLAUDE.md`: a test that merely
passes through the rasteriser check on its way to something else is not a rasteriser test, and
marking it would silently stop it running in CI — where it would then never exercise the enable
at all.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.cogs.module_cog import ModuleCog
from leaguebot.core.db.database import get_connection, run_migrations

SERVER_ID = 13008
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "images_enable.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


def _make_cog(
    db_path: str,
    *,
    images_enabled: bool = False,
    create_error: Exception | None = None,
) -> ModuleCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_images_enabled = AsyncMock(return_value=images_enabled)
    bot.module_service.set_images_enabled = AsyncMock(return_value=None)
    bot.image_config_service = MagicMock()
    bot.image_config_service.create_with_defaults = AsyncMock(side_effect=create_error)
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
            "SELECT change_type FROM audit_entries"
        )
        return [r["change_type"] for r in await cursor.fetchall()]


def _rasteriser(present: bool):
    """Answer the rasteriser check without needing one.

    Patched rather than marked: a test that merely passes through this check on its way to
    something else is not a rasteriser test, and marking it would stop it running in CI —
    where it would then never exercise the enable at all.
    """
    return patch(
        "leaguebot.image.services.image_render_service.converter_available",
        new=MagicMock(return_value=present),
    )


async def _enable(cog, interaction, *, rasteriser: bool = True):
    with _rasteriser(rasteriser):
        await cog._enable_images(interaction)


# ---------------------------------------------------------------------------
# Enabling
# ---------------------------------------------------------------------------


async def test_the_module_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _enable(cog, interaction)

    cog.bot.module_service.set_images_enabled.assert_awaited_once_with(True)
    assert "enabled" in _replied(interaction)


async def test_the_configuration_is_created_with_defaults(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _enable(cog, _interaction())

    cog.bot.image_config_service.create_with_defaults.assert_awaited_once_with()


async def test_every_output_aspect_starts_disabled(tmp_path):
    """Enabling the module does not thereby start posting graphics — each aspect is opted
    into separately, so a division is not surprised by a picture where it expected text."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _enable(cog, interaction)

    replied = _replied(interaction)
    assert "aspects start disabled" in replied
    assert "/images config toggle" in replied


async def test_enabling_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)

    await _enable(_make_cog(db_path), _interaction())

    assert await _audit_types(db_path) == ["IMAGE_MODULE_ENABLED"]


async def test_enabling_twice_does_no_work(tmp_path):
    """The second call would re-run the defaults write for no reason, and the audit would
    record an event that did not happen."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, images_enabled=True)
    interaction = _interaction()

    await _enable(cog, interaction)

    assert "already enabled" in _replied(interaction)
    cog.bot.image_config_service.create_with_defaults.assert_not_awaited()
    assert await _audit_types(db_path) == []


# ---------------------------------------------------------------------------
# Without a rasteriser
# ---------------------------------------------------------------------------


async def test_the_module_enables_without_a_rasteriser(tmp_path):
    """FR-007 / FR-008. Refusing would leave a league unable to set up the module they are
    installing the rasteriser for."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _enable(cog, interaction, rasteriser=False)

    cog.bot.module_service.set_images_enabled.assert_awaited_once_with(True)
    assert "enabled" in _replied(interaction)


async def test_a_missing_rasteriser_is_said_rather_than_left_to_be_discovered(tmp_path):
    """Otherwise the first graphic a league expects simply does not arrive, with nothing
    connecting that to a program nobody told them about."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _enable(cog, interaction, rasteriser=False)

    assert len(_replied(interaction)) > len("✅ Image module enabled.")


async def test_a_present_rasteriser_adds_no_notice(tmp_path):
    """Nothing to warn about, and a notice every time would be noise on the common path."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    with_rasteriser = _interaction()
    without = _interaction()

    await _enable(_make_cog(db_path), with_rasteriser, rasteriser=True)
    await _enable(cog, without, rasteriser=False)

    assert len(_replied(with_rasteriser)) < len(_replied(without))


async def test_the_rasteriser_check_is_not_cached(tmp_path):
    """An administrator installing Inkscape and re-running the command must see the new
    answer, not one cached from before they installed it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    with patch(
        "leaguebot.image.services.image_render_service.converter_available",
        new=MagicMock(return_value=True),
    ) as available:
        await cog._enable_images(_interaction())

    assert available.call_args.kwargs.get("use_cache") is False


# ---------------------------------------------------------------------------
# A failed enable
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="#442: a failed image enable still names the error rather than the standard reply",
)
async def test_a_failed_enable_leaves_the_module_off(tmp_path):
    """The configuration write and the flag are separate calls, so a failure between them
    would leave the module reading as on with nothing configured. The flag goes back down, and
    the standard failure reply says the module is still off, without the error's text."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, create_error=RuntimeError("disk full"))
    interaction = _interaction()

    await _enable(cog, interaction)

    cog.bot.module_service.set_images_enabled.assert_awaited_with(False)
    replied = _replied(interaction)
    assert "The module is still off." in replied
    assert "disk full" not in replied


@pytest.mark.xfail(
    strict=True,
    reason="#442: a failed image enable writes no failure line to the log channel",
)
async def test_a_failed_enable_is_not_logged_as_a_success(tmp_path):
    """It is logged as the failure it is, once, and never as a success."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, create_error=RuntimeError("disk full"))
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "module enable"

    await _enable(cog, interaction)

    lines = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert not any("Success" in line for line in lines)
    [line] = lines
    assert "/module enable" in line
    assert f"failed for <@{ACTOR_ID}>" in line
    assert "RuntimeError" in line
    assert "disk full" not in line


@pytest.mark.parametrize(
    "switch_off_fails",
    [
        pytest.param(
            False,
            id="the-write-fails",
            marks=pytest.mark.xfail(
                strict=True,
                reason="#442: a failed image enable still names the error in its reply",
            ),
        ),
        pytest.param(True, id="the-switch-off-also-fails"),
    ],
)
async def test_an_image_enable_that_cannot_be_written_is_switched_back_off_and_says_so(
    tmp_path, switch_off_fails
):
    """The audit write fails after the flag went up. The flag is put back down, and the
    member told in the standard failure reply that the module is still off — with one failure
    line in the log channel and no error text anywhere a league reads.

    Where putting the flag back down fails as well, the bot cannot say the module is off: that
    error goes to the command's own failure path, and no reply says the module is still off."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()
    cog = _make_cog(db_path)
    switch_off_error = RuntimeError("the flag will not go down")
    if switch_off_fails:
        cog.bot.module_service.set_images_enabled = AsyncMock(
            side_effect=[None, switch_off_error]
        )
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "module enable"

    if switch_off_fails:
        with pytest.raises(RuntimeError) as raised:
            await _enable(cog, interaction)
        assert raised.value is switch_off_error
        assert "The module is still off" not in _replied(interaction)
        return

    await _enable(cog, interaction)

    cog.bot.module_service.set_images_enabled.assert_awaited_with(False)
    replied = _replied(interaction)
    assert "stopped on a fault in the bot" in replied
    assert "The module is still off." in replied
    assert "again once the fault is cleared" in replied
    assert "disk I/O error" not in replied
    [line] = [str(c.args[0]) for c in cog.bot.output_router.post_log.await_args_list]
    assert "/module enable" in line
    assert f"failed for <@{ACTOR_ID}>" in line
    assert "IntegrityError" in line
    assert "disk I/O error" not in line
