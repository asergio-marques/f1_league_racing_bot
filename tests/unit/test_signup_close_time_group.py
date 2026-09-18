"""An armed signup auto-close time can be cancelled, replaced, or armed after the fact.

`/signup close` refuses while a timer is armed, and used to send the manager to
`/signup cancel-timer` — a command that had never been written. The only thing that
actually cleared `close_at` was `/module disable signup`, which takes the channel, both
roles and every setting with it, so a league that mistyped the close time had to tear the
module down to close signups early (issue #125).

`/signup close-time add`, `cancel` and `modify` are the way out. The refusal on
`/signup close` stays — closing early is two deliberate steps — but it now names a command
that exists, and `test_the_close_refusal_names_a_command_that_exists` holds it to that
against the registered command list rather than against a string a reader has to trust.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 7731

# The slash commands read the wall clock — they have no `now` to inject — so the times a
# test arms are taken relative to it rather than written as literals. A literal future
# date would pass today and start failing on its own the day it went past.
def _future(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        microsecond=0
    ).isoformat()


ARMED = _future(7)
LATER = _future(14)

# `_parse_close_time` does take a `now`, so the tests that are about the rule itself pin
# both ends and use fixed literals.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
FIXED_FUTURE = "2026-06-08T20:00:00+00:00"


# ── Fixtures ──────────────────────────────────────────────────────────────


async def _seed(tmp_path, *, signups_open: bool = True, close_at: str | None = None):
    path = str(tmp_path / "close_time.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signups_open, close_at) "
            "VALUES (?, ?, ?)",
            (1, 1 if signups_open else 0, close_at),
        )
        # A season awaiting its window, so `/signup open` reaches the checks under test.
        await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', 'SETUP', 1, ?)",
            ("SIGNUPS" if signups_open else "WAITING",),
        )
        await db.commit()
    return path


def _cog(db_path):
    from services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.scheduler_service = MagicMock()
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    return interaction


def _reply(interaction) -> str:
    return interaction.response.send_message.await_args.args[0]


async def _add(cog, interaction, close_time: str = LATER):
    await undecorate(SignupCog.close_time_add)(cog, interaction, close_time)


async def _cancel(cog, interaction):
    await undecorate(SignupCog.close_time_cancel)(cog, interaction)


async def _modify(cog, interaction, close_time: str = LATER):
    await undecorate(SignupCog.close_time_modify)(cog, interaction, close_time)


async def _close(cog, interaction):
    await undecorate(SignupCog.signup_close)(cog, interaction)


async def _close_at(db_path) -> str | None:
    from services.signup_module_service import SignupModuleService

    cfg = await SignupModuleService(db_path).get_config()
    return cfg.close_at


async def _audit_types(db_path) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries "
            "ORDER BY change_type",
        )
        return [row["change_type"] for row in await cursor.fetchall()]


def _registered_signup_commands() -> set[str]:
    """Every `/signup …` command the cog actually registers, fully qualified."""

    def walk(group):
        for command in group.commands:
            if hasattr(command, "commands"):
                yield from walk(command)
            else:
                yield f"/{command.qualified_name}"

    return set(walk(SignupCog.signup))


# ── The defect: a refusal naming a command that does not exist ────────────


class TestTheCloseRefusal:
    async def test_the_close_refusal_names_a_command_that_exists(self, tmp_path):
        """The regression test for #125.

        Asserted against the registered command list, not against a literal: a refusal
        that sends a manager somewhere is only useful if somewhere is there, and the
        previous wording pointed at `/signup cancel-timer`, which never was.
        """
        cog = _cog(await _seed(tmp_path, close_at=ARMED))
        interaction = _interaction()

        await _close(cog, interaction)

        named = set(re.findall(r"`(/signup [a-z- ]+)`", _reply(interaction)))
        assert named, "the refusal names no command at all"
        assert named <= _registered_signup_commands()

    async def test_close_is_still_refused_while_a_timer_is_armed(self, tmp_path):
        """Closing early stays two deliberate steps (decided 2026-09-15)."""
        cog = _cog(await _seed(tmp_path, close_at=ARMED))
        interaction = _interaction()

        await _close(cog, interaction)

        assert "auto-close" in _reply(interaction)
        assert "`/signup close-time cancel`" in _reply(interaction)

    async def test_cancel_unblocks_a_manual_close(self, tmp_path, monkeypatch):
        """The whole point of the fix: after a cancel, closing by hand actually closes."""
        from cogs import signup_cog

        forced_close = AsyncMock()
        monkeypatch.setattr(signup_cog, "execute_forced_close", forced_close)

        db_path = await _seed(tmp_path, close_at=ARMED)
        cog = _cog(db_path)

        await _cancel(cog, _interaction())
        closing = _interaction()
        closing.response.defer = AsyncMock()
        closing.followup.send = AsyncMock()
        await _close(cog, closing)

        forced_close.assert_awaited_once()
        assert not closing.response.send_message.await_args_list


# ── /signup close-time cancel ─────────────────────────────────────────────


class TestCancel:
    async def test_it_clears_the_stored_time_and_the_scheduled_job(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=ARMED)
        cog = _cog(db_path)

        await _cancel(cog, _interaction())

        assert await _close_at(db_path) is None
        cog.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()

    async def test_it_leaves_signups_open(self, tmp_path):
        """The window survives the cancel — only the timer goes."""
        from services.signup_module_service import SignupModuleService

        db_path = await _seed(tmp_path, close_at=ARMED)

        await _cancel(_cog(db_path), _interaction())

        cfg = await SignupModuleService(db_path).get_config()
        assert cfg.signups_open is True

    async def test_it_is_refused_when_nothing_is_armed(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)
        cog = _cog(db_path)
        interaction = _interaction()

        await _cancel(cog, interaction)

        assert "No auto-close time is set" in _reply(interaction)
        cog.bot.scheduler_service.cancel_signup_close_timer.assert_not_called()

    async def test_it_is_audited(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=ARMED)

        await _cancel(_cog(db_path), _interaction())

        assert await _audit_types(db_path) == ["SIGNUP_CLOSE_TIME_CANCEL"]


# ── /signup close-time add ────────────────────────────────────────────────


class TestAdd:
    async def test_it_arms_the_timer(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)
        cog = _cog(db_path)

        await _add(cog, _interaction(), LATER)

        assert await _close_at(db_path) == LATER
        cog.bot.scheduler_service.schedule_signup_close_timer.assert_called_once_with(
            LATER
        )

    async def test_it_is_refused_when_one_is_already_armed(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=ARMED)
        cog = _cog(db_path)
        interaction = _interaction()

        await _add(cog, interaction, LATER)

        assert "`/signup close-time modify`" in _reply(interaction)
        assert await _close_at(db_path) == ARMED
        cog.bot.scheduler_service.schedule_signup_close_timer.assert_not_called()

    async def test_it_rejects_a_past_time(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)
        cog = _cog(db_path)
        interaction = _interaction()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        await _add(cog, interaction, past)

        assert "must be a future datetime" in _reply(interaction)
        assert await _close_at(db_path) is None

    async def test_it_rejects_an_unparseable_time(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)
        cog = _cog(db_path)
        interaction = _interaction()

        await _add(cog, interaction, "next Tuesday-ish")

        assert "not a valid ISO 8601 datetime" in _reply(interaction)
        assert await _close_at(db_path) is None

    async def test_it_is_audited(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)

        await _add(_cog(db_path), _interaction(), LATER)

        assert await _audit_types(db_path) == ["SIGNUP_CLOSE_TIME_ADD"]


# ── /signup close-time modify ─────────────────────────────────────────────


class TestModify:
    async def test_it_cancels_the_old_job_and_arms_the_new(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=ARMED)
        cog = _cog(db_path)

        await _modify(cog, _interaction(), LATER)

        assert await _close_at(db_path) == LATER
        cog.bot.scheduler_service.cancel_signup_close_timer.assert_called_once_with()
        cog.bot.scheduler_service.schedule_signup_close_timer.assert_called_once_with(
            LATER
        )

    async def test_it_is_refused_when_nothing_is_armed(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=None)
        cog = _cog(db_path)
        interaction = _interaction()

        await _modify(cog, interaction, LATER)

        assert "`/signup close-time add`" in _reply(interaction)
        assert await _close_at(db_path) is None
        cog.bot.scheduler_service.schedule_signup_close_timer.assert_not_called()

    async def test_a_rejected_time_leaves_the_armed_one_alone(self, tmp_path):
        """A mistyped replacement must not disarm what is already there."""
        db_path = await _seed(tmp_path, close_at=ARMED)
        cog = _cog(db_path)

        await _modify(cog, _interaction(), "the Thursday after next")

        assert await _close_at(db_path) == ARMED
        cog.bot.scheduler_service.cancel_signup_close_timer.assert_not_called()

    async def test_it_records_the_time_it_replaced(self, tmp_path):
        db_path = await _seed(tmp_path, close_at=ARMED)

        await _modify(_cog(db_path), _interaction(), LATER)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT old_value, new_value FROM audit_entries",
            )
            row = await cursor.fetchone()
        assert (row["old_value"], row["new_value"]) == (ARMED, LATER)


# ── All three need an open window ─────────────────────────────────────────


class TestTheyRequireAnOpenWindow:
    @pytest.mark.parametrize("run", [_add, _cancel, _modify])
    async def test_refused_while_signups_are_closed(self, tmp_path, run):
        """`close_at` means nothing without a window: `set_window_closed` nulls it, so a
        timer armed against a closed window could only fire a forced close on nothing."""
        db_path = await _seed(tmp_path, signups_open=False, close_at=None)
        cog = _cog(db_path)
        interaction = _interaction()

        await run(cog, interaction)

        assert "Signups are not currently open" in _reply(interaction)
        cog.bot.scheduler_service.schedule_signup_close_timer.assert_not_called()
        cog.bot.scheduler_service.cancel_signup_close_timer.assert_not_called()


# ── One parser, two entry points ──────────────────────────────────────────


class TestOneRuleForTheCloseTime:
    """`/signup open close_time:` was kept when the group was added (decided 2026-09-15)
    on the condition that both routes share `_parse_close_time`, so a league cannot get
    two answers to the same question."""

    @pytest.mark.parametrize(
        "bad, expected",
        [
            ("not a datetime", "not a valid ISO 8601 datetime"),
            ("2020-01-01T20:00:00", "must be a future datetime"),
        ],
    )
    async def test_open_and_add_refuse_the_same_input_the_same_way(self, bad, expected):
        from cogs import signup_cog

        iso, error = signup_cog._parse_close_time(bad, now=NOW)

        assert iso is None
        assert expected in error

    async def test_a_naive_time_is_read_as_utc(self):
        from cogs import signup_cog

        iso, error = signup_cog._parse_close_time("2026-06-08T20:00:00", now=NOW)

        assert error is None
        assert iso == FIXED_FUTURE

    async def test_open_routes_through_the_shared_parser(self, monkeypatch, tmp_path):
        """Not just the same rule by coincidence — the same function."""
        from cogs import signup_cog

        calls: list[str] = []

        def _spy(raw, *, now=None):
            calls.append(raw)
            return None, "❌ refused"

        monkeypatch.setattr(signup_cog, "_parse_close_time", _spy)

        db_path = await _seed(tmp_path, signups_open=False)
        cog = _cog(db_path)
        cog.bot.config_service.get_server_config = AsyncMock(
            return_value=MagicMock(test_mode_active=False)
        )
        cog.bot.signup_module_service.get_slots = AsyncMock(
            return_value=[MagicMock(display_label="Friday 20:00")]
        )
        cfg = await cog.bot.signup_module_service.get_config()
        cfg.signup_channel_id, cfg.base_role_id, cfg.signed_up_role_id = 1, 2, 3
        cog.bot.signup_module_service.get_config = AsyncMock(return_value=cfg)

        interaction = _interaction()
        await undecorate(SignupCog.signup_open)(cog, interaction, None, LATER)

        assert calls == [LATER]
        assert _reply(interaction) == "❌ refused"
