"""`/test-mode toggle` answers to the season's stage and refuses a real league, on the change queue.

Test mode is chosen for a season, while that season is in Configuration (issue #220). Outside
Configuration the toggle is refused: confirming the configuration fixes test mode, and the
season's end switches it off. That retired the two refusals the toggle used to make — an open
signup window, and a started season holding fake drivers — since neither can arise while the
season is in Configuration.

Test mode and a real roster may still not share a server: switching test mode off deletes every
fake driver on it without confirmation, and while it is on the signup and placement paths refuse
real drivers outright.

The toggle is a change on the league lane (#283, S15e), carrying the state it sets, judged at the
ask and again as it starts, as `/test-mode nationality` is. Turning test mode on is one save: the
flag, the modules' part through the hook for test mode turned on, and its audit entry. Turning it
off is one save, the flag and the fake drivers together, then the clearing of the forecasts posted
under test mode and the deletion of the saved backup as jobs after it. A fault stops the lane.

Every test runs on the bot the entry point builds, its configuration read from the database, with
the real queue, its "now" pinned to `NOW`: the league's server, and season 3 in Configuration. Admin
(77) runs the command.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from leaguebot.core.cogs import test_mode_cog
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    change_rows,
    discard_job,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    stopped_job,
)
from tests.support.settings_league import audit_rows, last_said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.undecorate import undecorate

#: The league admin who runs the command, as a log line names them; and the queue's "now".
ACTOR_ID = 77
NAMED = f"Admin (`<@{ACTOR_ID}>`)"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The audit entry's change type.
AUDIT_TYPE = "TEST_MODE_SET"

ENABLED = (
    "✅ Test mode **enabled**. Use `/test-mode advance` to step through phases, or "
    "`/test-mode review` to inspect season status."
)
DISABLED = (
    "✅ Test mode **disabled**. "
    "The scheduler will resume normal operation for any remaining pending phases."
)
NOT_IN_CONFIGURATION = (
    "⛔ Test mode can only be switched while a season is in configuration. Start one with "
    "`/season setup`; a season whose configuration is confirmed keeps test mode as it stands "
    "until the season ends."
)
ONE_REAL_DRIVER = (
    "⛔ Test mode cannot be enabled while this server has **1** real driver(s).\n"
    "Test mode is for an empty league — disabling it deletes every fake driver, and while it is "
    "on no real driver may sign up or be placed."
)
BACKUP_KEPT = (
    "The saved test-mode state could not be deleted. It is deleted with the next `/test-mode "
    "toggle` that switches test mode off."
)

FAKE_DRIVER = "9000000000000000001"


# ── The league ────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    """The league's server, not in test mode, and season 3 in Configuration."""
    path = str(tmp_path / "toggle_guard.db")
    await run_migrations(path)
    await seed_server(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', 'SETUP', 3, 'CONFIGURATION')"
        )
        await db.commit()
    return path


@pytest.fixture
async def cog(db_path):
    """The test-mode cog on the bot the entry point builds, its queue's "now" pinned; the
    scheduler's job store sits beside the database, for the saved backup to be found."""
    bot = await league_bot(db_path, real_config=True)
    bot.scheduler_service.jobstore_path = str(Path(db_path).with_name("scheduler.db"))
    league_queue(bot, db_path, now=Clock(NOW))
    made = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    made.bot = bot
    return made


async def _ask(cog) -> Any:
    """Admin runs `/test-mode toggle`, and nothing more: the queue is not run."""
    interaction = member_asks(
        cog.bot, "test-mode toggle", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )
    await undecorate(test_mode_cog.TestModeCog.toggle)(cog, interaction)
    return interaction


async def _toggle(cog) -> Any:
    """Admin runs `/test-mode toggle`, and the queue runs what it asked."""
    interaction = await _ask(cog)
    await run_queue(cog.bot)
    return interaction


async def _flag(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        row = await cursor.fetchone()
    return bool(row["test_mode_active"])


async def _write(db_path: str, sql: str, *args: Any) -> None:
    async with get_connection(db_path) as db:
        await db.execute(sql, args)
        await db.commit()


async def _in_test_mode(db_path: str) -> None:
    await _write(db_path, "UPDATE server_configs SET test_mode_active = 1")


async def _fake_drivers(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles WHERE is_test_driver = 1"
        )
        return (await cursor.fetchone())["n"]


async def _add_driver(db_path: str, user_id: str, state: str, *, test: bool = False) -> None:
    await _write(
        db_path,
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver) "
        "VALUES (?, ?, ?)",
        user_id, state, 1 if test else 0,
    )


async def _seeded(db_path: str) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT config_name FROM points_config_store ORDER BY id")
        return [row["config_name"] for row in await cursor.fetchall()]


def _lines(cog) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in cog.bot.log_channel.sent]


def _success_lines(cog) -> list[str]:
    return [line for line in _lines(cog) if "| /test-mode toggle | Success" in line]


def _save_backup(db_path: str, jobstore: str) -> None:
    from leaguebot.core.services import backup_service

    Path(jobstore).write_bytes(b"")
    backup_service.backup_path(db_path).write_bytes(b"saved")
    backup_service.backup_path(jobstore).write_bytes(b"saved jobs")
    backup_service.set_lock(db_path, who="Maintainer")


def _as_naive(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=None)


# ── Enabling ──────────────────────────────────────────────────────────────────


class TestEnabling:
    async def test_a_clean_server_may_enter_test_mode(self, cog, db_path):
        """The server is not in test mode, season 3 is in Configuration with no points
        configuration, and the queue's clock is five minutes on from `NOW` when it runs. Admin
        runs the command: they are answered at once with the queue's acknowledgement, turning test
        mode on, and one change is asked. Once the queue has run it, test mode is on and "Standard"
        and "Half Points" are seeded, through results' part of the builder's hook; one
        TEST_MODE_SET entry names Admin, test mode going from off to on, stamped with the queue's
        clock; Admin's reply and the one line name both configurations, word for word, and the
        queue is not stopped."""
        interaction = await _ask(cog)
        assert acknowledgement(interaction).startswith("⏳ Turning test mode on. ")
        assert len(await change_rows(db_path)) == 1
        cog.bot.change_queue._clock.advance(minutes=5)

        await run_queue(cog.bot)

        assert last_said(interaction) == (
            f"{ENABLED}\n📌 Added default point configs: **Standard, Half Points**"
        )
        assert _success_lines(cog) == [
            f"{NAMED} | /test-mode toggle | Success\n"
            "  test_mode: enabled\n"
            "  seeded_configs: Standard, Half Points"
        ]
        assert await _flag(db_path) is True
        assert await _seeded(db_path) == ["Standard", "Half Points"]
        [audit] = await audit_rows(db_path)
        assert (audit["change_type"], audit["actor_id"]) == (AUDIT_TYPE, ACTOR_ID)
        assert audit["old_value"]["test_mode_active"] is False
        assert audit["new_value"]["test_mode_active"] is True
        assert _as_naive(audit["timestamp"]) == datetime(2026, 10, 1, 12, 5)
        assert await stopped_job(db_path) is None

    async def test_a_real_driver_refuses_it(self, cog, db_path):
        """A real driver is assigned. Admin runs the command: it is refused at once in the
        command's words, naming the one driver, seen by Admin alone, one refusal line names Admin
        and the reply's first line, and nothing is asked of the queue."""
        await _add_driver(db_path, "4001", "ASSIGNED")

        interaction = await _ask(cog)

        assert last_said(interaction) == ONE_REAL_DRIVER
        [reply] = interaction.response.send_message.await_args_list
        assert reply.kwargs.get("ephemeral") is True
        assert _lines(cog) == [
            f"⛔ `/test-mode toggle` refused for {NAMED} — Test mode cannot be enabled while "
            "this server has **1** real driver(s)."
        ]
        assert await change_rows(db_path) == []

    async def test_the_flag_is_left_alone_when_refused(self, cog, db_path):
        """A refusal must not flip the flag: the toggle flips before it branches."""
        await _add_driver(db_path, "4002", "PENDING_ADMIN_APPROVAL")

        await _toggle(cog)

        assert await _flag(db_path) is False

    async def test_a_former_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, "4003", "NOT_SIGNED_UP")

        interaction = await _toggle(cog)

        assert "**enabled**" in last_said(interaction)
        assert await _flag(db_path) is True

    async def test_a_fake_driver_does_not_stand_in_the_way(self, cog, db_path):
        await _add_driver(db_path, FAKE_DRIVER, "ASSIGNED", test=True)

        interaction = await _toggle(cog)

        assert "**enabled**" in last_said(interaction)

    async def test_the_count_is_named(self, cog, db_path):
        await _add_driver(db_path, "4004", "UNASSIGNED")
        await _add_driver(db_path, "4005", "ASSIGNED")

        interaction = await _toggle(cog)

        assert "**2** real driver" in last_said(interaction)


class TestTheSeasonsStage:
    async def test_it_is_refused_with_no_season(self, cog, db_path):
        """No season is set up. Admin runs the command: it is refused at once in the command's
        words, seen by Admin alone, one refusal line names Admin and the reason, and test mode
        stays off."""
        await _write(db_path, "DELETE FROM seasons")

        interaction = await _toggle(cog)

        assert last_said(interaction) == NOT_IN_CONFIGURATION
        [reply] = interaction.response.send_message.await_args_list
        assert reply.kwargs.get("ephemeral") is True
        assert _lines(cog) == [
            f"⛔ `/test-mode toggle` refused for {NAMED} — {NOT_IN_CONFIGURATION[2:]}"
        ]
        assert await _flag(db_path) is False

    @pytest.mark.parametrize(
        "stage, status",
        [("WAITING", "SETUP"), ("PLACEMENTS", "SETUP"), ("ONGOING", "ACTIVE"),
         ("PENDING_COMPLETION", "ACTIVE")],
    )
    async def test_it_is_refused_once_the_configuration_is_confirmed(
        self, cog, db_path, stage, status
    ):
        await _write(db_path, "UPDATE seasons SET status = ?, stage = ?", status, stage)
        await _in_test_mode(db_path)

        interaction = await _toggle(cog)

        assert "only be switched while a season is in configuration" in last_said(interaction)
        assert await _flag(db_path) is True, "test mode stays as the season fixed it"

    async def test_it_may_be_switched_off_again_in_configuration(self, cog, db_path):
        await _in_test_mode(db_path)

        interaction = await _toggle(cog)

        assert "**disabled**" in last_said(interaction)
        assert await _flag(db_path) is False


async def test_switching_test_mode_on_names_what_the_modules_handed_back(cog, db_path):
    """Three parts are signed up to test mode turned on in place of results', in this order: one
    hands back that it created "Sprint Ladder", the next that it created nothing, the last that it
    created "Wet Ladder". Switching test mode on hands each part the save's own connection,
    already reading test mode on, and season 3's id; it names "Sprint Ladder, Wet Ladder", in the
    order the parts signed up, in the reply and in the log line, and nothing else is seeded."""
    hook = cog.bot.test_mode_turned_on
    hook._subscribers.clear()
    handed: list[tuple[bool, int]] = []

    async def _record(db: Any, season_id: int) -> None:
        cursor = await db.execute("SELECT test_mode_active FROM server_configs")
        handed.append((bool((await cursor.fetchone())["test_mode_active"]), season_id))

    async def created_one(db: Any, season_id: int) -> list[str]:
        await _record(db, season_id)
        return ["Sprint Ladder"]

    async def created_none(db: Any, season_id: int) -> list[str]:
        await _record(db, season_id)
        return []

    async def created_another(db: Any, season_id: int) -> list[str]:
        await _record(db, season_id)
        return ["Wet Ladder"]

    hook.subscribe(created_one)
    hook.subscribe(created_none)
    hook.subscribe(created_another)

    interaction = await _toggle(cog)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM seasons WHERE season_number = 3")
        season_id = (await cursor.fetchone())["id"]
    assert handed == [(True, season_id)] * 3
    assert last_said(interaction).endswith(
        "\n📌 Added default point configs: **Sprint Ladder, Wet Ladder**"
    )
    [line] = _success_lines(cog)
    assert line.endswith("\n  seeded_configs: Sprint Ladder, Wet Ladder")
    assert await _flag(db_path) is True
    assert await _seeded(db_path) == [], "the toggle seeded beside the hook's parts"


# ── Disabling ─────────────────────────────────────────────────────────────────


async def test_turning_test_mode_off_is_one_save_with_its_audit_entry_and_line(cog, db_path):
    """The server is in test mode with one fake driver, and the queue's clock is five minutes on
    from `NOW` when it runs. Admin runs the command: they are answered at once with the queue's
    acknowledgement, turning test mode off, and one change is asked. Once the queue has run it,
    test mode is off and the fake driver deleted; one TEST_MODE_SET entry names Admin, test mode
    going from on to off, stamped with the queue's clock; Admin's reply and the one line name the
    driver removed, word for word, and the queue is not stopped."""
    await _in_test_mode(db_path)
    await _add_driver(db_path, FAKE_DRIVER, "UNASSIGNED", test=True)

    interaction = await _ask(cog)
    assert acknowledgement(interaction).startswith("⏳ Turning test mode off. ")
    assert len(await change_rows(db_path)) == 1
    cog.bot.change_queue._clock.advance(minutes=5)

    await run_queue(cog.bot)

    assert last_said(interaction) == f"{DISABLED}\n🗑️ Removed **1** fake driver(s)."
    assert _success_lines(cog) == [
        f"{NAMED} | /test-mode toggle | Success\n"
        "  test_mode: disabled\n"
        "  removed_fake_drivers: 1"
    ]
    assert await _flag(db_path) is False
    assert await _fake_drivers(db_path) == 0
    [audit] = await audit_rows(db_path)
    assert (audit["change_type"], audit["actor_id"]) == (AUDIT_TYPE, ACTOR_ID)
    assert audit["old_value"]["test_mode_active"] is True
    assert audit["new_value"]["test_mode_active"] is False
    assert _as_naive(audit["timestamp"]) == datetime(2026, 10, 1, 12, 5)
    assert await stopped_job(db_path) is None


async def test_switching_test_mode_off_hands_the_bots_delete_hook(cog, db_path):
    """The server is in test mode with one fake driver. Switching test mode off deletes the fake
    driver through the bot's own delete hook, so each module deletes its own rows of them
    (#283): a part signed up to it is called once."""
    called: list[Any] = []

    async def watching(*args: Any) -> None:
        called.append(args)

    cog.bot.profiles_deleted.subscribe(watching)
    await _in_test_mode(db_path)
    await _add_driver(db_path, FAKE_DRIVER, "UNASSIGNED", test=True)

    await _toggle(cog)  # off

    assert len(called) == 1
    assert await _fake_drivers(db_path) == 0


# ── The saved backup goes with test mode ──────────────────────────────────────


async def test_toggling_off_deletes_the_saved_backup(cog, db_path):
    """Decided 2026-09-17: the backup commands run in test mode alone, so a state kept past
    it is one nothing could restore. The lock does not protect it."""
    from leaguebot.core.services import backup_service

    await _toggle(cog)  # on
    jobstore = cog.bot.scheduler_service.jobstore_path
    _save_backup(db_path, jobstore)

    interaction = await _toggle(cog)  # off

    assert not backup_service.backup_path(db_path).exists()
    assert not backup_service.backup_path(jobstore).exists()
    assert not backup_service.lock_path(db_path).exists()
    assert "Deleted the saved test-mode backup" in last_said(interaction)
    assert "backup: deleted" in _success_lines(cog)[-1]


async def test_toggling_off_with_nothing_saved_says_nothing_about_a_backup(cog, db_path):
    await _toggle(cog)  # on

    interaction = await _toggle(cog)  # off

    assert "backup" not in last_said(interaction).lower()


async def test_a_backup_that_cannot_be_deleted_stops_the_queue_after_test_mode_is_off(
    cog, db_path, monkeypatch
):
    """The server is in test mode with one fake driver and a saved backup, which the host refuses
    to delete. Admin turns test mode off: the queue stops at the backup's deletion, after the
    save, so test mode is already off, the fake driver deleted and the one TEST_MODE_SET entry
    written, and the backup is still there. A league admin presses Discard: Admin's reply reads,
    word for word, "✅ Test mode **disabled**. The scheduler will resume normal operation for any
    remaining pending phases.", then "🗑️ Removed **1** fake driver(s).", then, after a blank line,
    "⚠️ **Not everything could be done**" and "• The saved test-mode state could not be deleted.
    It is deleted with the next `/test-mode toggle` that switches test mode off.", as the season
    end words it; the log channel holds the toggle's one success line, naming Admin, test mode
    disabled and the one fake driver removed, with no backup deleted; the backup stays, test mode
    stays off, and the queue is no longer stopped."""
    from leaguebot.core.services import backup_service

    await _in_test_mode(db_path)
    await _add_driver(db_path, FAKE_DRIVER, "UNASSIGNED", test=True)
    _save_backup(db_path, cog.bot.scheduler_service.jobstore_path)
    saved = Path(backup_service.backup_path(db_path))
    unlinking = Path.unlink

    def _unlink(self: Path, *args: Any, **kwargs: Any) -> None:
        if Path(self) == saved:
            raise PermissionError(13, "Permission denied", str(self))
        unlinking(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink)

    interaction = await _toggle(cog)

    stopped = await stopped_job(db_path)
    assert stopped is not None and stopped["name"] == "discard_backup"
    assert await _flag(db_path) is False
    assert await _fake_drivers(db_path) == 0
    assert len(await audit_rows(db_path)) == 1
    assert saved.exists()

    await discard_job(cog.bot)

    assert last_said(interaction) == (
        f"{DISABLED}\n🗑️ Removed **1** fake driver(s)."
        f"\n\n⚠️ **Not everything could be done**\n• {BACKUP_KEPT}"
    )
    assert _success_lines(cog) == [
        f"{NAMED} | /test-mode toggle | Success\n"
        "  test_mode: disabled\n"
        "  removed_fake_drivers: 1"
    ]
    assert saved.exists()
    assert await _flag(db_path) is False
    assert await stopped_job(db_path) is None


# ── The forecasts posted under test mode are cleared after the save ───────────


async def test_switching_test_mode_off_clears_the_forecasts_once_the_flag_reads_off(cog, db_path):
    """The season is in Configuration, and a part is signed up to the bot's hook for clearing what
    test mode posted (weather's deletion of each forecast). Switching test mode on calls it not
    at all; switching it off calls it once, as a job of the toggle's own change after its save, so
    the flag already reads off, and asks no change of its own of the queue."""
    flag_when_called: list[bool] = []

    async def watching(*_args: Any) -> tuple[()]:
        flag_when_called.append(await _flag(db_path))
        return ()

    cog.bot.test_mode_flushed.subscribe(watching)

    await _toggle(cog)  # on
    assert flag_when_called == []

    await _toggle(cog)  # off

    assert flag_when_called == [False]
    assert "weather.forecast.flush" not in [row["kind"] for row in await change_rows(db_path)]


# ── Judged again as it starts, a repeat, and a fault ─────────────────────────


async def _leave_configuration(db_path: str) -> None:
    await _write(db_path, "UPDATE seasons SET stage = 'WAITING'")


async def _a_real_driver_signs_up(db_path: str) -> None:
    await _add_driver(db_path, "4010", "UNASSIGNED")


@pytest.mark.parametrize(
    "test_mode, meanwhile, refusal",
    [
        pytest.param(False, _leave_configuration, NOT_IN_CONFIGURATION, id="on_moved_on"),
        pytest.param(False, _a_real_driver_signs_up, ONE_REAL_DRIVER, id="on_real_driver"),
        pytest.param(True, _leave_configuration, NOT_IN_CONFIGURATION, id="off_moved_on"),
    ],
)
async def test_a_refusal_found_as_the_toggle_starts_changes_nothing(
    cog, db_path, test_mode, meanwhile, refusal
):
    """Test mode is off (on, for *off_moved_on*), with one fake driver where it is on. Admin runs
    the command and the change is asked; before the queue runs it, season 3 moves on to Waiting,
    or a real driver signs up. As it starts it is refused in the command's words: Admin's reply
    becomes the refusal, one refusal line names Admin and the refusal's first line, as at the ask,
    test mode is as it was, nothing is seeded, no fake driver deleted, nothing audited, and the
    queue is not stopped."""
    if test_mode:
        await _in_test_mode(db_path)
        await _add_driver(db_path, FAKE_DRIVER, "UNASSIGNED", test=True)
    interaction = await _ask(cog)
    assert len(await change_rows(db_path)) == 1
    await meanwhile(db_path)

    await run_queue(cog.bot)

    assert last_said(interaction) == refusal
    assert [line for line in _lines(cog) if line.startswith("⛔")] == [
        f"⛔ `/test-mode toggle` refused for {NAMED} — {refusal[2:].splitlines()[0]}"
    ]
    assert await _flag(db_path) is test_mode
    assert await _seeded(db_path) == []
    assert await _fake_drivers(db_path) == (1 if test_mode else 0)
    assert await audit_rows(db_path) == []
    assert await stopped_job(db_path) is None


@pytest.mark.parametrize(
    "test_mode, doing",
    [
        pytest.param(False, "Turning test mode on", id="on"),
        pytest.param(True, "Turning test mode off", id="off"),
    ],
)
async def test_a_second_press_while_the_first_waits_is_refused_as_a_repeat(
    cog, db_path, test_mode, doing
):
    """Test mode is off (on, for *off*). Admin runs the command, and runs it again before the
    queue has run the first: the second asks the same thing and is refused at once as a repeat,
    in the queue's words, and only the one change is asked. Once the queue has run it, test mode
    has moved once, with one audit entry."""
    if test_mode:
        await _in_test_mode(db_path)
    await _ask(cog)

    second = await _ask(cog)

    assert acknowledgement(second) == (
        f"⚠️ {doing} has already been asked for and has not started yet, "
        "so it was not asked for again."
    )
    assert len(await change_rows(db_path)) == 1
    await run_queue(cog.bot)
    assert await _flag(db_path) is (not test_mode)
    assert len(await audit_rows(db_path)) == 1


async def _a_module_part_fails(cog) -> None:
    async def failing(*_args: Any) -> None:
        raise RuntimeError("disk I/O error")

    cog.bot.test_mode_turned_on.subscribe(failing)


async def _a_driver_deletion_fails(cog) -> None:
    async def failing(*_args: Any) -> None:
        raise RuntimeError("disk I/O error")

    cog.bot.profiles_deleted.subscribe(failing)


@pytest.mark.parametrize(
    "test_mode, fault, nothing_changed",
    [
        pytest.param(
            False, _a_module_part_fails,
            "Nothing was changed: test mode is still **off**. Run `/test-mode toggle` again.",
            id="on",
        ),
        pytest.param(
            True, _a_driver_deletion_fails,
            "Nothing was changed: test mode is still **on**. Run `/test-mode toggle` again.",
            id="off",
        ),
    ],
)
async def test_a_fault_in_the_save_leaves_test_mode_as_it_was(
    cog, db_path, test_mode, fault, nothing_changed
):
    """Test mode is off, with results' part signed up to test mode turned on and a second part after
    it that fails (*on*); or on, with one fake driver, and a part signed up to the bot's delete hook
    that fails (*off*). Admin runs the command and the queue runs it: it stops at the save, and test
    mode is as it was, nothing seeded or attached to season 3, the fake driver not deleted, nothing
    audited and no success line written. A league admin presses Discard: Admin's reply says nothing
    was changed and to run the command again, test mode is still as it was, and the queue is no
    longer stopped."""
    if test_mode:
        await _in_test_mode(db_path)
        await _add_driver(db_path, FAKE_DRIVER, "UNASSIGNED", test=True)
    await fault(cog)

    interaction = await _toggle(cog)

    assert await stopped_job(db_path) is not None
    assert await _flag(db_path) is test_mode
    assert await _seeded(db_path) == []
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT config_name FROM season_points_links")
        assert await cursor.fetchall() == []
    assert await _fake_drivers(db_path) == (1 if test_mode else 0)
    assert await audit_rows(db_path) == []
    assert _success_lines(cog) == []

    await discard_job(cog.bot)

    assert last_said(interaction) == nothing_changed
    assert await _flag(db_path) is test_mode
    assert await _fake_drivers(db_path) == (1 if test_mode else 0)
    assert await stopped_job(db_path) is None
