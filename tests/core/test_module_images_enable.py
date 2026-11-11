"""Switching the image module on, with or without a rasteriser.

Issue #208. Enabling images is the one module toggle that depends on a program outside the bot,
and the way it handles that absence is the point of the file.

Images' switch is image's own change on the change queue (#283, S11c): `/module enable images`
asks `module.on:images`, the member is acknowledged at once, and the queue carries it out. Each
test drives the command's handler on the bot the entry point builds, with the real queue and
"now" pinned, and runs the queue.

**The module enables even without the rasteriser** (FR-007, FR-008), so an administrator can
finish configuring while waiting for Inkscape to be installed. It says so rather than refusing —
refusing would leave a league unable to set up the module they are trying to install the
rasteriser *for*.

**Re-enabling is lossless.** The configuration is created only where it is missing, so an
existing configuration is left exactly as it was (FR-004a) — which is the other half of the
disable clearing nothing; `tests/image/test_image_module_flow.py` pins both halves.

**Every aspect starts disabled.** A league enabling the module does not thereby start posting
graphics; each output aspect is opted into separately, so enabling cannot surprise a division
with a picture where it expected text.

**A failed enable leaves nothing.** The configuration, the flag and the audit entry are one
save, so a fault anywhere in it leaves the module off with no configuration behind it — never
on-but-broken, and never a half-made configuration.

`converter_available` is **monkeypatched rather than marked**. `CLAUDE.md`: a test that merely
passes through the rasteriser check on its way to something else is not a rasteriser test, and
marking it would silently stop it running in CI — where it would then never exercise the enable
at all.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from leaguebot.core.cogs.module_cog import ModuleCog
from leaguebot.core.db.database import get_connection
from tests.support.change_queue import MEMBER_ID

#: The member as a saved log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: The enable's reply where the host carries the rasteriser.
ENABLED = "✅ Image module enabled. All output aspects start disabled — use `/images config toggle`."

#: The notice a host without Inkscape adds to the enable's reply, in today's words.
NO_RASTERISER = (
    "⛔ **Inkscape is not installed on the machine running the bot.**\n"
    "Image generation is disabled entirely until it is. No package dependency installs it — it "
    "is a separate program the host must carry.\n"
    "If it is installed somewhere unusual, set the `INKSCAPE` environment variable to the "
    "executable's full path."
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _league(tmp_path, *, on: bool = False):
    """A league whose settings run on the queue, the image module *on* (with its default
    configuration) or off (with none), and the module cog over its bot."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=("images",) if on else ())
    return league, ModuleCog(league.bot)


def _asked(league):
    """`/module enable` as Admin, the member the league's lines name."""
    from tests.support.settings_league import interaction

    return interaction(league.bot, "module enable")


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


async def _enable(league, cog, asked, *, rasteriser: bool = True) -> None:
    """Run `/module enable images` and the queue behind it, the rasteriser *present* or not."""
    from tests.support.change_queue import run_queue

    with _rasteriser(rasteriser):
        await cog._enable_images(asked)
        await run_queue(league.bot)


async def _audit_types(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT change_type FROM audit_entries ORDER BY id")
        return [r["change_type"] for r in await cursor.fetchall()]


async def _images_flag(db_path: str) -> int | None:
    """The image module's switch, None where no configuration row holds it."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT module_enabled FROM image_config")
        row = await cursor.fetchone()
    return None if row is None else row["module_enabled"]


async def _configuration_rows(db_path: str) -> dict[str, int]:
    """How many rows the image configuration and its aspect toggles hold."""
    async with get_connection(db_path) as db:
        counts = {}
        for table in ("image_config", "image_aspect_toggles"):
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = (await cursor.fetchone())["n"]
    return counts


async def _refuse_the_audit(db_path: str) -> None:
    """Make the audit entry, the last write of the enable's save, fail as a full disk would."""
    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Enabling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rasteriser", [True, False], ids=["with a rasteriser", "without"])
async def test_the_module_is_enabled(tmp_path, rasteriser):
    """With or without a rasteriser (FR-007 / FR-008). Refusing would leave a league unable to
    set up the module they are installing the rasteriser for. The member is acknowledged at
    once, and once the change has run the module is on, and the reply and the Success line keep
    today's words.

    The reply says every output aspect starts disabled: enabling the module does not thereby
    start posting graphics, so a division is not surprised by a picture where it expected text.
    A missing rasteriser is said, and a present one adds nothing: otherwise the first graphic a
    league expects simply does not arrive, with nothing connecting that to a program nobody told
    them about; and with nothing to warn about, a notice every time would be noise on the common
    path."""
    from tests.support.change_queue import acknowledgement
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    asked = _asked(league)

    await _enable(league, cog, asked, rasteriser=rasteriser)

    assert acknowledgement(asked).startswith("⏳ Turning the image module on. ")
    assert await _images_flag(league.db_path) == 1
    expected = ENABLED if rasteriser else f"{ENABLED}\n\n{NO_RASTERISER}"
    assert last_said(asked) == expected
    assert await log_lines(league) == [f"{NAMED} | /module enable images | Success"]


async def test_enabling_is_audited(tmp_path):
    league, cog = await _league(tmp_path)

    await _enable(league, cog, _asked(league))

    assert await _audit_types(league.db_path) == ["IMAGE_MODULE_ENABLED"]


async def test_enabling_twice_does_no_work(tmp_path):
    """The second call would re-run the defaults write for no reason, and the audit would
    record an event that did not happen. The member is refused at once, nothing is queued, and
    the refusal reaches the log channel as one line in the standard form."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path, on=True)
    asked = _asked(league)

    await _enable(league, cog, asked)

    assert last_said(asked) == "⚠️ Image module is already enabled."
    assert await change_rows(league.db_path) == []
    assert await _audit_types(league.db_path) == []
    assert await log_lines(league) == [
        f"⛔ `/module enable` refused for {NAMED} — Image module is already enabled."
    ]


# ---------------------------------------------------------------------------
# Without a rasteriser
# ---------------------------------------------------------------------------


async def test_the_rasteriser_check_is_not_cached(tmp_path):
    """An administrator installing Inkscape and re-running the command must see the new
    answer, not one cached from before they installed it."""
    from tests.support.change_queue import run_queue

    league, cog = await _league(tmp_path)

    with patch(
        "leaguebot.image.services.image_render_service.converter_available",
        new=MagicMock(return_value=True),
    ) as available:
        await cog._enable_images(_asked(league))
        await run_queue(league.bot)

    assert available.call_args.kwargs.get("use_cache") is False


# ---------------------------------------------------------------------------
# A failed enable
# ---------------------------------------------------------------------------


async def test_a_failed_enable_leaves_the_module_off(tmp_path):
    """The configuration, the flag and the audit entry are one save, so a fault at its last
    write leaves the module off with no configuration behind it: never on with nothing
    configured, and never a half-made configuration a later enable would build on."""
    league, cog = await _league(tmp_path)
    await _refuse_the_audit(league.db_path)

    await _enable(league, cog, _asked(league))

    assert await _images_flag(league.db_path) in (None, 0)
    assert await _configuration_rows(league.db_path) == {
        "image_config": 0,
        "image_aspect_toggles": 0,
    }


async def test_an_image_enable_that_cannot_be_written_says_the_module_is_still_off(tmp_path):
    """The audit write fails inside the enable's one save. The member gets the standard
    failure reply, saying the module is still off and what to do next, with no error text; the
    log channel gets the one failure line, and never a success."""
    from tests.support.settings_league import last_said, log_lines

    league, cog = await _league(tmp_path)
    await _refuse_the_audit(league.db_path)
    asked = _asked(league)

    await _enable(league, cog, asked)

    replied = last_said(asked)
    assert replied == (
        "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did "
        "not finish. The module is still off. Run `/module enable images` again once the fault "
        "is cleared. The fault is recorded in the log channel."
    )
    assert "disk I/O error" not in replied
    assert await log_lines(league) == [
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. The details are in the host's log."
    ]
