"""Switching the image, attendance and signup modules off.

Issue #208, continuing `tests/core/test_module_enable_disable.py`. Where that file covered the
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
decision, and a league reading its log needs to see that. Both go through
`AttendanceService.switch_off_on`, on a connection the caller hands it: the plain disable is
attendance's own change on the change queue (#283), saved with its audit, and the cascade is a part
of turning Results & Standings off, saved with the switch-off on the change queue (#439), where it
has no interaction of its own to answer.

**Enabling signup writes a bare configuration row where none stands and then names the commands
that fill it in:** `/signup channel`, and whichever of `/bot base-role` and `/bot driver-role` the
league has not yet set. A module enabled with nothing configured does nothing at all, and the next
steps are the difference between a league proceeding and a league wondering what happened.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.cogs.module_cog import ModuleCog, _ConfirmDisableResultsView
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from tests.support.change_queue import (
    MEMBER_ID,
    league_bot,
    league_queue,
    member,
    member_interaction,
    queued_log_lines,
    run_queue,
)

SERVER_ID = 11408
ACTOR_ID = 77
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


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


async def _turn_results_off_with_the_cascade(db_path: str):
    """Results & Standings on, attendance on: the admin confirms turning results off with the
    cascade, on the league's bot (`league_bot`), and the change queue runs to the end. No hub is
    set, so the refresh the switch-off asks finds nothing to post."""

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await db.commit()
    bot = await league_bot(db_path)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    league_queue(bot, db_path, now=NOW)
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = bot
    view = _ConfirmDisableResultsView(cog, ACTOR_ID, cascade_attendance=True)
    await type(view).confirm(
        view, member_interaction(bot, user=member(ACTOR_ID)), view.confirm
    )
    await run_queue(bot)
    return bot


async def _attendance_flag(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config"
        )
        return (await cursor.fetchone())["module_enabled"]


# ---------------------------------------------------------------------------
# Images — the module that clears nothing
# ---------------------------------------------------------------------------


# Images' switch is image's own change on the change queue (#283, S11c): `/module disable images`
# asks `module.off:images`, the member is acknowledged at once, and the queue carries it out.
# Each test drives the command's handler on the bot the entry point builds, with the real queue
# and "now" pinned, and runs the queue.


async def _images_league(tmp_path, *, on: bool = True):
    """A league whose settings run on the queue, the image module *on* (with its default
    configuration) or off, the module cog over its bot, and `/module disable` as Admin."""
    from tests.support.settings_league import interaction, settings_league

    league = await settings_league(tmp_path, modules=("images",) if on else ())
    return league, ModuleCog(league.bot), interaction(league.bot, "module disable")


async def test_disabling_images_clears_the_flag(tmp_path):
    """The member is acknowledged at once, and once the change has run the module is off."""
    from tests.support.change_queue import acknowledgement

    league, cog, asked = await _images_league(tmp_path)

    await cog._disable_images(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the image module off. ")
    await run_queue(league.bot)

    assert await league.bot.module_service.is_images_enabled() is False


async def test_disabling_images_keeps_the_configuration(tmp_path):
    """The Principle X.6 exception, and the reason no `--preserve-config` flag exists. No
    image config value names a channel, role, message or job, so none can go stale — a
    reader making the three disables uniform would clear a league's whole image setup for
    no reason. The reply says the configuration is kept and that every aspect reverts to text,
    which is what a league will actually notice, and it and the Success line keep today's
    words; `tests/image/test_image_module_flow.py` pins the configuration surviving."""
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _images_league(tmp_path)

    await cog._disable_images(asked)
    await run_queue(league.bot)

    assert last_said(asked) == (
        "✅ Image module disabled. Every aspect reverts to text output.\n"
        "Your configuration is kept — re-enabling restores it exactly."
    )
    assert await log_lines(league) == [
        f"Admin (`<@{MEMBER_ID}>`) | /module disable images | Success"
    ]


async def test_disabling_images_twice_does_no_work(tmp_path):
    """Refused at once: nothing is queued and nothing audited."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league, cog, asked = await _images_league(tmp_path, on=False)

    await cog._disable_images(asked)

    assert "already disabled" in last_said(asked)
    assert await change_rows(league.db_path) == []
    assert await _audit_types(league.db_path) == []


async def test_an_image_disable_that_cannot_be_written_leaves_the_module_on(tmp_path):
    """The flag and the audit entry are one save, so a fault at the audit leaves the module on.
    The member gets the standard failure reply, saying nothing was saved and what to run again,
    in place of today's 'It may have been partly done'; the log channel gets the one failure
    line, and never a success."""
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _images_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()

    await cog._disable_images(asked)
    await run_queue(league.bot)

    assert await league.bot.module_service.is_images_enabled() is True
    assert last_said(asked) == (
        "❌ `/module disable` stopped on a fault in the bot, not on anything you entered, and "
        "did not finish. Nothing was saved. Run `/module disable images` again to retry. The "
        "fault is recorded in the log channel."
    )
    assert await log_lines(league) == [
        f"❌ `/module disable` failed for Admin (`<@{MEMBER_ID}>`) — IntegrityError. "
        "The details are in the host's log."
    ]


async def test_disabling_images_is_audited(tmp_path):
    league, cog, asked = await _images_league(tmp_path)

    await cog._disable_images(asked)
    await run_queue(league.bot)

    assert await _audit_types(league.db_path) == ["IMAGE_MODULE_DISABLED"]


# ---------------------------------------------------------------------------
# Attendance — the module that clears its bindings
# ---------------------------------------------------------------------------


# Attendance's switch-off is attendance's own change on the change queue (#283, S11d): `/module
# disable attendance` asks `module.off:attendance`, the member is acknowledged at once, and the
# queue carries it out. Each test drives the command's handler on the bot the entry point builds,
# with the real queue and "now" pinned, and runs the queue.

#: Admin as the league's log lines name them.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"


async def _attendance_league(tmp_path, *, on: bool = True, divisions: int = 0):
    """A league whose settings run on the queue, Results & Standings on and attendance *on* or
    off, *divisions* divisions of an active season each with its own RSVP channel bound, the
    module cog over its bot, and `/module disable` as Admin."""
    from tests.support.settings_league import interaction, settings_league

    league = await settings_league(
        tmp_path, modules=("results", "attendance") if on else ("results",)
    )
    if divisions:
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status) "
                "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
            )
            for index in range(1, divisions + 1):
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
    return league, ModuleCog(league.bot), interaction(league.bot, "module disable")


async def test_disabling_attendance_clears_the_flag(tmp_path):
    """The member is acknowledged at once, and once the change has run the module is off, and
    the reply and the Success line keep today's words."""
    from tests.support.change_queue import acknowledgement
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _attendance_league(tmp_path)

    await cog._disable_attendance(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the attendance module off. ")
    await run_queue(league.bot)

    assert await _attendance_flag(league.db_path) == 0
    assert last_said(asked) == "✅ Attendance module disabled."
    assert await log_lines(league) == [f"{NAMED} | /module disable attendance | Success"]


async def test_disabling_attendance_clears_its_division_channels(tmp_path):
    """Those are stale bindings: a channel recorded against a division for a module that
    is off would have the bot posting into it the moment it came back on, against a season
    that had moved along."""
    league, cog, asked = await _attendance_league(tmp_path, divisions=2)
    assert await _division_configs(league.db_path) == 2

    await cog._disable_attendance(asked)
    await run_queue(league.bot)

    assert await _division_configs(league.db_path) == 0


async def test_disabling_attendance_twice_does_no_work(tmp_path):
    """Refused at once: nothing is queued and nothing audited."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league, cog, asked = await _attendance_league(tmp_path, on=False)

    await cog._disable_attendance(asked)

    assert "already disabled" in last_said(asked)
    assert await change_rows(league.db_path) == []
    assert await _audit_types(league.db_path) == []


async def test_a_manager_s_disable_is_audited_as_their_decision(tmp_path):
    league, cog, asked = await _attendance_league(tmp_path)

    await cog._disable_attendance(asked)
    await run_queue(league.bot)

    assert await _audit_types(league.db_path) == ["ATTENDANCE_MODULE_DISABLED"]


async def test_an_attendance_disable_that_cannot_be_written_leaves_the_module_on(tmp_path):
    """The switch-off and its audit entry are one save, so a fault at the audit leaves the module
    on and its division channels bound. The member gets the standard failure reply, saying
    nothing was saved and what to run again, and the log channel gets the one failure line, and
    never a success."""
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _attendance_league(tmp_path, divisions=1)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()

    await cog._disable_attendance(asked)
    await run_queue(league.bot)

    assert await _attendance_flag(league.db_path) == 1
    assert await _division_configs(league.db_path) == 1
    assert last_said(asked) == (
        "❌ `/module disable` stopped on a fault in the bot, not on anything you entered, and "
        "did not finish. Nothing was saved. Run `/module disable attendance` again to retry. The "
        "fault is recorded in the log channel."
    )
    assert await log_lines(league) == [
        f"❌ `/module disable` failed for {NAMED} — IntegrityError. "
        "The details are in the host's log."
    ]


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


async def test_a_cascade_is_audited_as_a_cascade(tmp_path):
    """Attendance going off because Results did was nobody's decision, and a league
    reading its log needs to see that rather than a disable they cannot account for."""
    db_path = await _make_db(tmp_path)

    await _turn_results_off_with_the_cascade(db_path)

    types = await _audit_types(db_path)
    assert types.count("ATTENDANCE_MODULE_CASCADE_DISABLED") == 1
    assert "ATTENDANCE_MODULE_DISABLED" not in types


async def test_a_cascade_does_the_same_work(tmp_path):
    """The cascade and the manual disable switch attendance off through the one call, so the
    module ends up in the same state either way, or a cascade would leave stale bindings a
    manual disable clears."""
    db_path = await _make_db(tmp_path, attendance_divisions=2)

    async with get_connection(db_path) as db:
        assert await AttendanceService(db_path).switch_off_on(db) is True
        await db.commit()

    assert await _attendance_flag(db_path) == 0
    assert await _division_configs(db_path) == 0


async def test_a_cascade_runs_even_where_attendance_was_already_off(tmp_path):
    """It skips the guard deliberately — clearing twice is harmless where refusing would leave
    bindings behind — and says attendance was already off."""
    db_path = await _make_db(tmp_path, attendance_divisions=1)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE attendance_config SET module_enabled = 0")
        await db.commit()

    async with get_connection(db_path) as db:
        assert await AttendanceService(db_path).switch_off_on(db) is False
        await db.commit()

    assert await _division_configs(db_path) == 0


async def test_a_cascade_is_still_logged_to_the_league(tmp_path):
    """The league must be able to see that attendance went off, even though nobody asked
    for it: the cascade's own line, beside the switch-off's."""
    db_path = await _make_db(tmp_path)

    bot = await _turn_results_off_with_the_cascade(db_path)

    lines = list(bot.log_channel.sent) + await queued_log_lines(db_path)
    cascade = [line for line in lines if "| /module disable attendance | Success" in line]
    assert len(cascade) == 1, lines


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


# Signup's switch-on is signup's own change on the change queue (#283, S11e): `/module enable
# signup` asks `module.on:signup`, the member is acknowledged at once, and the queue carries it out;
# its switch-off is `module.off:signup` already. Each test drives the command's handler on the bot
# the entry point builds, with the real queue and "now" pinned, and runs the queue.

#: The fault reply of a signup switch-on that could not be saved, word for word.
SIGNUP_STILL_OFF = (
    "❌ `/module enable` stopped on a fault in the bot, not on anything you entered, and did not "
    "finish. The module is still off. Run `/module enable signup` again once the fault is "
    "cleared. The fault is recorded in the log channel."
)


async def _signup_league(tmp_path, verb: str = "enable", *, on: bool = False):
    """A league whose settings run on the queue, the signup module *on* or off with no
    configuration row, core's server configuration read from the database (no base role or
    driver role set), the module cog over its bot, and `/module <verb>` as Admin."""
    from tests.support.settings_league import interaction, settings_league

    league = await settings_league(tmp_path, modules=("signup",) if on else ())
    league.bot.config_service.get_server_config = ConfigService(league.db_path).get_server_config
    return league, ModuleCog(league.bot), interaction(league.bot, f"module {verb}")


async def _signup_config(db_path: str) -> dict | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT * FROM signup_module_config")
        row = await cursor.fetchone()
    return None if row is None else dict(row)


@pytest.mark.xfail(strict=True, reason="#283: signup's switch-on is not its own change on the queue")
async def test_enabling_signup_writes_a_bare_configuration(tmp_path):
    """So the `/signup …` commands have a row to update. Without it every one of them would be a
    silent no-op against nothing. The member is acknowledged at once; once the change has run the
    module is on, its configuration names no channel and holds the window closed, and the log
    channel has the one Success line."""
    from tests.support.change_queue import acknowledgement
    from tests.support.settings_league import log_lines

    league, cog, asked = await _signup_league(tmp_path)

    await cog._enable_signup(asked)
    assert acknowledgement(asked).startswith("⏳ Turning the signup module on. ")
    await run_queue(league.bot)

    assert await league.bot.module_service.is_signup_enabled() is True
    config = await _signup_config(league.db_path)
    assert config is not None
    assert config["signup_channel_id"] is None
    assert not config["signups_open"]
    assert config["signup_button_message_id"] is None
    assert await _audit_types(league.db_path) == ["MODULE_ENABLE"]
    assert await log_lines(league) == [f"{NAMED} | /module enable signup | Success"]


@pytest.mark.xfail(strict=True, reason="#283: signup's switch-on replaces a standing configuration row")
async def test_re_enabling_signup_keeps_a_standing_row(tmp_path):
    """A configuration row standing while the module is off (a restored database could hold one)
    is kept, not replaced by a bare one: the switch-on creates only what is missing. The signup
    channel it names is still named once the module is on again."""
    league, cog, asked = await _signup_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (id, signup_channel_id, signups_open) "
            "VALUES (1, 620, 0)"
        )
        await db.commit()

    await cog._enable_signup(asked)
    await run_queue(league.bot)

    assert await league.bot.module_service.is_signup_enabled() is True
    config = await _signup_config(league.db_path)
    assert config is not None and str(config["signup_channel_id"]) == "620"


async def test_enabling_signup_names_the_three_commands_that_follow(tmp_path):
    """A module enabled with nothing configured does nothing at all, and these are the
    difference between a league proceeding and a league wondering what happened. With neither
    role set, the reply names all three, word for word."""
    from tests.support.settings_league import last_said

    league, cog, asked = await _signup_league(tmp_path)

    await cog._enable_signup(asked)
    await run_queue(league.bot)

    assert last_said(asked) == (
        "✅ Signup module enabled.\n"
        "Next steps — configure with:\n"
        "  • `/signup channel <channel>`\n"
        "  • `/bot base-role <role>`\n"
        "  • `/bot driver-role <role>`"
    )


async def test_enabling_signup_does_not_ask_for_roles_the_league_already_set(tmp_path):
    """The two roles are the league's (issue #276) and outlive the module, so a league
    re-enabling signup has them already; naming them would send it to redo work."""
    from tests.support.settings_league import last_said

    league, cog, asked = await _signup_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE server_configs SET base_role_id = 1, driver_role_id = 2")
        await db.commit()

    await cog._enable_signup(asked)
    await run_queue(league.bot)

    assert last_said(asked) == (
        "✅ Signup module enabled.\n"
        "Next steps — configure with:\n"
        "  • `/signup channel <channel>`"
    )


async def test_enabling_signup_twice_does_no_work(tmp_path):
    """A second enable would do the switch-on's work again: a second audit entry for a module
    already on. Refused at once: nothing is queued, nothing audited, the configuration is left as
    it stands, and the refusal reaches the log channel."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _signup_league(tmp_path, on=True)

    await cog._enable_signup(asked)

    assert last_said(asked) == "⚠️ Signup module is already enabled."
    assert await change_rows(league.db_path) == []
    assert await _audit_types(league.db_path) == []
    assert await _signup_config(league.db_path) is None
    assert await log_lines(league) == [
        f"⛔ `/module enable` refused for {NAMED} — Signup module is already enabled."
    ]


@pytest.mark.xfail(strict=True, reason="#283: signup's switch-on is three saves off the queue")
async def test_a_signup_enable_that_cannot_be_written_says_the_module_is_still_off(tmp_path):
    """The flag, the configuration row and the audit entry are one save, so a fault at the audit
    leaves the module off with no row created. The member gets the standard failure reply, saying
    the module is still off and what to run again, in place of today's 'It may have been partly
    done'; the log channel gets the one failure line, and never a success."""
    from tests.support.settings_league import last_said, log_lines

    league, cog, asked = await _signup_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_audit BEFORE INSERT ON audit_entries "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()

    await cog._enable_signup(asked)
    await run_queue(league.bot)

    assert await league.bot.module_service.is_signup_enabled() is False
    assert await _signup_config(league.db_path) is None
    assert last_said(asked) == SIGNUP_STILL_OFF
    assert await log_lines(league) == [
        f"❌ `/module enable` failed for {NAMED} — IntegrityError. "
        "The details are in the host's log."
    ]
