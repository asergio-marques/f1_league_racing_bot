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

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.queue.setting_change import NOTHING_SAVED
from leaguebot.core.utils.interaction_errors import failure_reply
from tests.support.change_queue import change_rows, member_interaction, run_queue, tier_member
from tests.support.season_league import setup_league
from tests.support.settings_league import fault_in, last_said, log_lines, record_followups, said
from tests.support.undecorate import undecorate

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
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.signup_module_service = SignupModuleService(db_path)
    bot.scheduler_service = MagicMock()
    bot.output_router.post_log = AsyncMock()

    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    """An interaction whose response knows whether it has been used, as Discord's does, so a
    refusal sent through `refuse` goes by the route a test reads."""
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _reply(interaction) -> str:
    """The first reply the member was sent, by whichever route it went."""
    calls = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    return calls[0].args[0]


async def _add(cog, interaction, close_time: str = LATER):
    await undecorate(SignupCog.close_time_add)(cog, interaction, close_time)


async def _cancel(cog, interaction):
    await undecorate(SignupCog.close_time_cancel)(cog, interaction)


async def _modify(cog, interaction, close_time: str = LATER):
    await undecorate(SignupCog.close_time_modify)(cog, interaction, close_time)


async def _close(cog, interaction):
    await undecorate(SignupCog.signup_close)(cog, interaction)


async def _close_at(db_path) -> str | None:
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    cfg = await SignupModuleService(db_path).get_config()
    return cfg.close_at


async def _audit_types(db_path) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type FROM audit_entries "
            "ORDER BY change_type",
        )
        return [row["change_type"] for row in await cursor.fetchall()]


# ── The close, on the league's queue ──────────────────────────────────────

CLOSE_KIND = "signup.close"


async def _close_league(tmp_path, *, close_at: str | None, signups_open: bool = True) -> Any:
    """`setup_league` at Signups with the window open (or, where not *signups_open*, closed) and
    its close time *close_at*, on a real change queue: nobody part-way through signing up."""
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await league.write(
        "UPDATE signup_module_config SET close_at = ?, signups_open = ?", close_at, int(signups_open)
    )
    return league


def _manager(league, command: str = "signup close") -> Any:
    interaction = member_interaction(league.bot, user=tier_member("manager", member_id=77))
    interaction.guild = league.guild
    interaction.command = MagicMock(qualified_name=command)
    return interaction


#: The kind of the one-off timer that closes signups at their set time (#286, item 12).
CLOSE_TIMER = "signup.close_timer"

#: What the close-time commands do not yet do before #286's item 12.
_CLOSE_TIME_SETTINGS = (
    "#286: the close-time commands are not yet Settings that arm and disarm the "
    "signup.close_timer from the change queue"
)


async def _asked(league, run, *args) -> Any:
    """Manager 77 runs the close-time command *run* (`_add`, `_cancel` or `_modify`) on
    *league*, the queue not yet run; gives his interaction, its follow-ups recorded for `said`."""
    name = {_add: "add", _cancel: "cancel", _modify: "modify"}[run]
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = record_followups(_manager(league, f"signup close-time {name}"))
    await run(cog, interaction, *args)
    return interaction


async def _ran(league, run, *args) -> Any:
    """Manager 77 runs the close-time command *run* on *league*, then the queue runs."""
    interaction = await _asked(league, run, *args)
    await run_queue(league.bot)
    return interaction


def _armed(league) -> list[tuple]:
    """Each arming of the close timer, as `arm_once(kind, key, run_at)` was called."""
    return [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
            if call.args and call.args[0] == CLOSE_TIMER]


def _disarmed(league) -> list[tuple]:
    """Each disarming of the close timer, as `disarm(kind, key)` was called."""
    return [call.args for call in league.bot.scheduler_service.disarm.call_args_list
            if call.args and call.args[0] == CLOSE_TIMER]


async def _close_time_audits(db_path) -> list[str]:
    return [t for t in await _audit_types(db_path) if t.startswith("SIGNUP_CLOSE_TIME")]


async def _close_on(league) -> Any:
    """League manager 77 runs `/signup close` on *league*; gives his interaction."""
    cog = SignupCog.__new__(SignupCog)
    cog.bot = league.bot
    interaction = _manager(league)
    await undecorate(SignupCog.signup_close)(cog, interaction)
    return interaction


async def _close_changes(league) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == CLOSE_KIND]


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
        previous wording pointed at `/signup cancel-timer`, which never was. The league's
        window is open with its close time armed a week on; manager 77 runs `/signup close`
        on the league's queue (#439, slice 6b: the refusal is the close's own check, made as
        it is asked, so nothing is queued).
        """
        league = await _close_league(tmp_path, close_at=ARMED)
        interaction = await _close_on(league)

        named = set(re.findall(r"`(/signup [a-z- ]+)`", _reply(interaction)))
        assert named, "the refusal names no command at all"
        assert named <= _registered_signup_commands()
        assert await _close_changes(league) == []

    async def test_close_is_still_refused_while_a_timer_is_armed(self, tmp_path):
        """Closing early stays two deliberate steps (decided 2026-09-15). The window is open
        with its close time armed; manager 77 runs `/signup close` on the league's queue: he
        is refused, told of the auto-close and pointed at `/signup close-time cancel`, and
        no close is asked of the queue."""
        league = await _close_league(tmp_path, close_at=ARMED)
        interaction = await _close_on(league)

        assert "auto-close" in _reply(interaction)
        assert "`/signup close-time cancel`" in _reply(interaction)
        assert await _close_changes(league) == []

    async def test_cancel_unblocks_a_manual_close(self, tmp_path):
        """The whole point of the fix: after a cancel, closing by hand actually closes. The
        window is open with its close time armed; manager 77 cancels the close time, then runs
        `/signup close` with nobody part-way through signing up. The close is asked of the
        queue (#439, slice 6b, C69) and, once the queue runs, the window is closed."""
        league = await _close_league(tmp_path, close_at=ARMED)
        cog = SignupCog.__new__(SignupCog)
        cog.bot = league.bot
        await _cancel(cog, _manager(league))

        await _close_on(league)

        assert len(await _close_changes(league)) == 1
        await run_queue(league.bot)
        [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
        assert not window["signups_open"]


# ── /signup close-time cancel ─────────────────────────────────────────────


class TestCancel:
    @pytest.mark.xfail(strict=True, reason=_CLOSE_TIME_SETTINGS)
    async def test_it_clears_the_stored_time_and_the_scheduled_job(self, tmp_path):
        """The window is open with its close time armed a week on. Manager 77 runs `/signup
        close-time cancel`: he is answered as under way and nothing is saved as it is asked; when
        the queue runs, the close time is cleared and the close timer disarmed."""
        league = await _close_league(tmp_path, close_at=ARMED)

        asked = await _asked(league, _cancel)
        assert said(asked)[0].startswith("⏳ ")
        assert await _close_at(league.db_path) == ARMED
        await run_queue(league.bot)

        assert await _close_at(league.db_path) is None
        assert len(_disarmed(league)) == 1

    async def test_it_leaves_signups_open(self, tmp_path):
        """The window survives the cancel — only the timer goes."""
        league = await _close_league(tmp_path, close_at=ARMED)

        await _ran(league, _cancel)

        [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
        assert window["signups_open"]

    async def test_it_is_refused_when_nothing_is_armed(self, tmp_path):
        league = await _close_league(tmp_path, close_at=None)

        interaction = await _ran(league, _cancel)

        assert "No auto-close time is set" in _reply(interaction)
        assert _disarmed(league) == []

    async def test_it_is_audited(self, tmp_path):
        league = await _close_league(tmp_path, close_at=ARMED)

        await _ran(league, _cancel)

        assert await _close_time_audits(league.db_path) == ["SIGNUP_CLOSE_TIME_CANCEL"]


# ── /signup close-time add ────────────────────────────────────────────────


class TestAdd:
    @pytest.mark.xfail(strict=True, reason=_CLOSE_TIME_SETTINGS)
    async def test_it_arms_the_timer(self, tmp_path):
        """The window is open with no close time. Manager 77 runs `/signup close-time add` for
        two weeks on: he is answered as under way and nothing is saved or armed as it is asked;
        when the queue runs, the close time is saved and the close timer armed once, for that
        time."""
        league = await _close_league(tmp_path, close_at=None)

        asked = await _asked(league, _add, LATER)
        assert said(asked)[0].startswith("⏳ ")
        assert await _close_at(league.db_path) is None
        assert _armed(league) == []
        await run_queue(league.bot)

        assert await _close_at(league.db_path) == LATER
        [(_, _key, run_at)] = _armed(league)
        assert run_at == datetime.fromisoformat(LATER)

    async def test_it_is_refused_when_one_is_already_armed(self, tmp_path):
        league = await _close_league(tmp_path, close_at=ARMED)

        interaction = await _ran(league, _add, LATER)

        assert "`/signup close-time modify`" in _reply(interaction)
        assert await _close_at(league.db_path) == ARMED
        assert _armed(league) == []

    async def test_it_rejects_a_past_time(self, tmp_path):
        league = await _close_league(tmp_path, close_at=None)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        interaction = await _ran(league, _add, past)

        assert "must be a future datetime" in _reply(interaction)
        assert await _close_at(league.db_path) is None

    async def test_it_rejects_an_unparseable_time(self, tmp_path):
        league = await _close_league(tmp_path, close_at=None)

        interaction = await _ran(league, _add, "next Tuesday-ish")

        assert "not a valid ISO 8601 datetime" in _reply(interaction)
        assert await _close_at(league.db_path) is None

    async def test_it_is_audited(self, tmp_path):
        league = await _close_league(tmp_path, close_at=None)

        await _ran(league, _add, LATER)

        assert await _close_time_audits(league.db_path) == ["SIGNUP_CLOSE_TIME_ADD"]


# ── /signup close-time modify ─────────────────────────────────────────────


class TestModify:
    @pytest.mark.xfail(strict=True, reason=_CLOSE_TIME_SETTINGS)
    async def test_it_cancels_the_old_job_and_arms_the_new(self, tmp_path):
        """The window is open with its close time armed a week on. Manager 77 runs `/signup
        close-time modify` for two weeks on: he is answered as under way and nothing changes as
        it is asked; when the queue runs, the new time is saved and the close timer armed again
        for it, replacing the old."""
        league = await _close_league(tmp_path, close_at=ARMED)

        asked = await _asked(league, _modify, LATER)
        assert said(asked)[0].startswith("⏳ ")
        assert await _close_at(league.db_path) == ARMED
        assert _armed(league) == []
        await run_queue(league.bot)

        assert await _close_at(league.db_path) == LATER
        [(_, _key, run_at)] = _armed(league)
        assert run_at == datetime.fromisoformat(LATER)

    async def test_it_is_refused_when_nothing_is_armed(self, tmp_path):
        league = await _close_league(tmp_path, close_at=None)

        interaction = await _ran(league, _modify, LATER)

        assert "`/signup close-time add`" in _reply(interaction)
        assert await _close_at(league.db_path) is None
        assert _armed(league) == []

    async def test_a_rejected_time_leaves_the_armed_one_alone(self, tmp_path):
        """A mistyped replacement must not disarm what is already there."""
        league = await _close_league(tmp_path, close_at=ARMED)

        await _ran(league, _modify, "the Thursday after next")

        assert await _close_at(league.db_path) == ARMED
        assert _disarmed(league) == [] and _armed(league) == []

    @pytest.mark.parametrize(
        "typed", [ARMED, ARMED.removesuffix("+00:00")], ids=["as-stored", "naive-utc"]
    )
    async def test_modify_to_the_armed_instant_changes_nothing_and_says_so(
        self, tmp_path, typed
    ):
        """The manager gives the instant already armed, as stored or as a naive time read as
        UTC: they are told nothing changed, no audit row is written, the timer is neither
        disarmed nor armed again, and one line records that nothing changed."""
        from tests.support.change_queue import queued_log_lines

        league = await _close_league(tmp_path, close_at=ARMED)

        interaction = await _ran(league, _modify, typed)

        assert re.search(r"[Nn]othing (was )?changed", _reply(interaction))
        assert await _close_at(league.db_path) == ARMED
        assert await _close_time_audits(league.db_path) == []
        assert _disarmed(league) == [] and _armed(league) == []
        lines = list(league.bot.log_channel.sent) + await queued_log_lines(league.db_path)
        [line] = [line for line in lines if "/signup close-time modify" in line]
        assert line.startswith("Admin (`<@77>`) | /signup close-time modify | Nothing changed")

    async def test_it_records_the_time_it_replaced(self, tmp_path):
        league = await _close_league(tmp_path, close_at=ARMED)

        await _ran(league, _modify, LATER)

        [row] = await league.rows(
            "SELECT old_value, new_value FROM audit_entries "
            "WHERE change_type = 'SIGNUP_CLOSE_TIME_MODIFY'"
        )
        assert (row["old_value"], row["new_value"]) == (ARMED, LATER)


# ── A fault in the save, or in the arming after it ────────────────────────

#: Each close-time command as its test runs it: the command, its name, the close time stored
#: before it, and the close time stored once it has saved.
_CLOSE_TIME_COMMANDS = [
    pytest.param(_add, "add", None, LATER, id="add"),
    pytest.param(_cancel, "cancel", ARMED, None, id="cancel"),
    pytest.param(_modify, "modify", ARMED, LATER, id="modify"),
]


@pytest.mark.xfail(strict=True, reason=_CLOSE_TIME_SETTINGS)
@pytest.mark.parametrize("run, name, before, _saved", _CLOSE_TIME_COMMANDS)
async def test_a_close_time_faulted_in_its_save_stores_nothing(
    tmp_path, run, name, before, _saved
):
    """The window is open, its close time unset (add) or armed a week on (cancel, modify).
    Manager 77 runs `/signup close-time add` or `modify` for two weeks on, or `cancel`, and the
    audit entry's write faults as the queue runs: the close time stays as it was, the timer is
    neither armed nor disarmed, no audit entry and no Success line are written, and he is told
    nothing was saved and to run the command again."""
    league = await _close_league(tmp_path, close_at=before)
    args = () if run is _cancel else (LATER,)

    asked = await _asked(league, run, *args)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    command = f"/signup close-time {name}"
    assert last_said(asked) == failure_reply(f"`{command}`", NOTHING_SAVED.format(command=command))
    assert await _close_at(league.db_path) == before
    assert _armed(league) == [] and _disarmed(league) == []
    assert await _close_time_audits(league.db_path) == []
    assert not any(" | Success" in line for line in await log_lines(league))


@pytest.mark.xfail(strict=True, reason=_CLOSE_TIME_SETTINGS)
@pytest.mark.parametrize("run, name, before, saved", _CLOSE_TIME_COMMANDS)
async def test_a_close_timer_that_cannot_be_armed_or_removed_leaves_the_time_saved(
    tmp_path, run, name, before, saved
):
    """The window is open, its close time unset (add) or armed a week on (cancel, modify), and
    the scheduler fails with an error of the bot's own when asked to arm or remove the close
    timer. Manager 77 runs `/signup close-time add` or `modify` for two weeks on, or `cancel`,
    and the queue runs: the close time is saved as asked, with its audit entry; he is not told it
    all went well, but that the auto-close was not armed (add, modify) or not removed (cancel);
    the log channel carries one failure line for the command naming the kind of fault; and the
    error's own text reaches neither the reply nor the log."""
    league = await _close_league(tmp_path, close_at=before)
    scheduler = league.bot.scheduler_service
    scheduler.arm_once.side_effect = RuntimeError("scheduler down")
    scheduler.disarm.side_effect = RuntimeError("scheduler down")
    args = () if run is _cancel else (LATER,)

    asked = await _ran(league, run, *args)

    assert await _close_at(league.db_path) == saved
    assert len(await _close_time_audits(league.db_path)) == 1
    replied = last_said(asked)
    assert not replied.startswith("✅")
    assert "auto-close" in replied.lower()
    assert ("not removed" if run is _cancel else "not armed") in replied
    assert "scheduler down" not in replied
    lines = await log_lines(league)
    [failed] = [line for line in lines if line.startswith("❌ ")]
    assert failed.startswith(f"❌ `/signup close-time {name}` failed for ")
    assert "— RuntimeError." in failed
    assert not any("scheduler down" in line for line in lines)


# ── All three need an open window ─────────────────────────────────────────


class TestTheyRequireAnOpenWindow:
    @pytest.mark.parametrize("run", [_add, _cancel, _modify])
    async def test_refused_while_signups_are_closed(self, tmp_path, run):
        """`close_at` means nothing without a window: `set_window_closed` nulls it, so a
        timer armed against a closed window could only fire a forced close on nothing."""
        league = await _close_league(tmp_path, close_at=None, signups_open=False)

        interaction = await _ran(league, run)

        assert "Signups are not currently open" in _reply(interaction)
        assert _armed(league) == [] and _disarmed(league) == []


# ── The close timer, and the close time missed while the bot was down ─────


@pytest.mark.xfail(strict=True, reason=(
    "#286: no signup.close_timer is armed by the close-time commands, nor a handler for it "
    "that judges the stored close time"
))
async def test_a_close_time_cancelled_does_not_close_signups(tmp_path):
    """The window is open with no close time. Manager 77 adds one two weeks on and the queue
    runs; he then cancels it and the queue runs. The close timer fires anyway for the key it was
    armed with, as one whose disarming came too late would, the queue's clock past the old time,
    and the queue runs: the stored close time is gone, so signups stay open and no automatic
    close is audited."""
    from tests.support.change_queue import maybe_await

    league = await _close_league(tmp_path, close_at=None)
    await _ran(league, _add, LATER)
    [(_, key, _run_at)] = _armed(league)
    await _ran(league, _cancel)
    league.clock.advance(days=15)

    [handler] = [call.args[1]
                 for call in league.bot.scheduler_service.register_timer.call_args_list
                 if call.args and call.args[0] == CLOSE_TIMER]
    await maybe_await(handler(key))
    await run_queue(league.bot)

    [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
    assert window["signups_open"]
    assert "SIGNUP_AUTO_CLOSE" not in await _audit_types(league.db_path)


@pytest.mark.xfail(strict=True, reason=(
    "#286: no signup.close_timer is armed by the close-time commands, nor a handler for it "
    "that judges the stored close time"
))
async def test_the_close_timer_closes_signups_at_their_set_time(tmp_path):
    """The window is open with no close time. Manager 77 adds one two weeks on and the queue
    runs. The queue's clock moves past it and the close timer fires for the key it was armed
    with; the queue runs: signups are closed, audited once by the system as an automatic close."""
    from tests.support.change_queue import maybe_await

    league = await _close_league(tmp_path, close_at=None)
    await _ran(league, _add, LATER)
    [(_, key, _run_at)] = _armed(league)
    league.clock.advance(days=15)

    [handler] = [call.args[1]
                 for call in league.bot.scheduler_service.register_timer.call_args_list
                 if call.args and call.args[0] == CLOSE_TIMER]
    await maybe_await(handler(key))
    await run_queue(league.bot)

    [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
    assert not window["signups_open"]
    assert (await _audit_types(league.db_path)).count("SIGNUP_AUTO_CLOSE") == 1


@pytest.mark.xfail(strict=True, reason=(
    "#286: signup's start-up step judges the close time against the host's clock, not the "
    "queue's, and re-arms rather than catching up"
))
async def test_a_close_time_missed_while_down_closes_on_start(tmp_path):
    """The window is open with its close time an hour on by the host's clock; the bot was down
    and the queue's clock now reads two hours on, past it. Signup's "signup close timer"
    start-up step runs, and the queue runs: signups are closed, audited once as an automatic
    close, and no close timer is armed for a time already gone."""
    from leaguebot.signup.wiring import start_up_steps

    close_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    league = await _close_league(tmp_path, close_at=close_at.isoformat())
    league.clock.advance(hours=2)

    await dict(start_up_steps(league.bot))["signup close timer"]()
    await run_queue(league.bot)

    [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
    assert not window["signups_open"]
    assert (await _audit_types(league.db_path)).count("SIGNUP_AUTO_CLOSE") == 1
    assert _armed(league) == []


@pytest.mark.xfail(strict=True, reason=(
    "#286: signup's start-up step arms core's signup close job, not a signup.close_timer, for "
    "a close time still ahead"
))
async def test_a_close_time_still_ahead_is_armed_on_start(tmp_path):
    """The window is open with its close time a day on, and the bot starts with no close timer
    standing, as after a discarded arming. Signup's "signup close timer" start-up step runs, and
    the queue runs: signups stay open, nothing is closed or audited as an automatic close, and
    the close timer is armed once, for the close time; what the reply to a discarded arming
    promises ("the bot also arms it when it next starts")."""
    from leaguebot.signup.wiring import start_up_steps

    close_at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    league = await _close_league(tmp_path, close_at=close_at.isoformat())

    await dict(start_up_steps(league.bot))["signup close timer"]()
    await run_queue(league.bot)

    [window] = await league.rows("SELECT signups_open FROM signup_module_config WHERE id = 1")
    assert window["signups_open"]
    assert "SIGNUP_AUTO_CLOSE" not in await _audit_types(league.db_path)
    [(_, _key, run_at)] = _armed(league)
    assert run_at == close_at


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
        from leaguebot.signup.cogs import signup_cog

        iso, error = signup_cog._parse_close_time(bad, now=NOW)

        assert iso is None
        assert expected in error

    async def test_a_naive_time_is_read_as_utc(self):
        from leaguebot.signup.cogs import signup_cog

        iso, error = signup_cog._parse_close_time("2026-06-08T20:00:00", now=NOW)

        assert error is None
        assert iso == FIXED_FUTURE

    async def test_open_routes_through_the_shared_parser(self, monkeypatch, tmp_path):
        """Not just the same rule by coincidence — the same function."""
        from leaguebot.signup.cogs import signup_cog

        calls: list[str] = []

        def _spy(raw, *, now=None):
            calls.append(raw)
            return None, "❌ refused"

        monkeypatch.setattr(signup_cog, "_parse_close_time", _spy)

        db_path = await _seed(tmp_path, signups_open=False)
        cog = _cog(db_path)
        cog.bot.config_service.get_server_config = AsyncMock(
            return_value=MagicMock(test_mode_active=False, base_role_id=2, driver_role_id=3)
        )
        cog.bot.signup_module_service.get_slots = AsyncMock(
            return_value=[MagicMock(display_label="Friday 20:00")]
        )
        cfg = await cog.bot.signup_module_service.get_config()
        cfg.signup_channel_id = 1
        cog.bot.signup_module_service.get_config = AsyncMock(return_value=cfg)

        interaction = _interaction()
        # Nothing has answered it yet, as with any interaction Discord delivers.
        interaction.response.is_done = MagicMock(return_value=False)
        # Both roles on the server and grantable: the roles are not what this pins (#374).
        role = MagicMock(managed=False)
        role.is_default.return_value = False
        role.guild.me.top_role.__gt__ = lambda _self, _other: True
        interaction.guild.get_role = MagicMock(return_value=role)
        await undecorate(SignupCog.signup_open)(cog, interaction, None, LATER)

        assert calls == [LATER]
        assert _reply(interaction) == "❌ refused"
