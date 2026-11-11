"""The test-mode command surface for driver nationality.

`/test-mode nationality` is the test-mode counterpart of `/signup nationality`, and
`/test-mode roster list` prints the nationality each fake driver holds; `/test-mode roster add`
takes one on the change queue, in test_test_mode_roster_add. These tests run the callbacks
against a migrated database with Discord stubbed — no gateway, no server, no running bot.

The guards are unwrapped as the other cog suites unwrap them: the tier guards have their
own cover, and a stubbed interaction is not a `discord.Member`.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest


# Imported as a module: pytest tries to collect a bare `TestModeCog` name as a test class.
from leaguebot.core.cogs import test_mode_cog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.config_service import ConfigService
from tests.support.change_queue import (
    Clock,
    acknowledgement,
    change_rows,
    league_bot,
    league_queue,
    member,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
)
from tests.support.settings_league import audit_rows, last_said, said, unruled
from tests.support.settings_league import interaction as member_asks
from tests.support.undecorate import undecorate

SERVER_ID = 6161
DIVISION = "Division 1"


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return bool(self.messages)

    async def defer(self, **kwargs):
        pass

    async def send_message(self, content, **kwargs):
        self.messages.append(content)


class _Followup:
    """Where a deferred command's replies land — one call per message sent."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content, **kwargs):
        self.messages.append(content)


class _Interaction:
    def __init__(self) -> None:
        self.guild_id = SERVER_ID
        self.guild = None
        self.response = _Response()
        self.followup = _Followup()
        self.user = SimpleNamespace(display_name="Tester", id=1)
        # The bot a refusal or a failure reaches the log channel through, as Discord's
        # interaction carries its client.
        self.client = SimpleNamespace(output_router=SimpleNamespace(post_log=AsyncMock()))

    @property
    def sent(self) -> list[str]:
        """Every message the command sent, however it sent it, in order."""
        return self.response.messages + self.followup.messages

    @property
    def reply(self) -> str:
        assert self.sent, "the command replied with nothing"
        return self.sent[-1]


def _unwrap(cmd):
    """The innermost callback, bypassing whatever tier guard the command wears."""
    return undecorate(cmd)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "testmode_cog.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', 'SETUP', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, DIVISION),
        )
        division_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Redline', 'Redline', 2, 0)",
            (division_id,),
        )
        team_id = cursor.lastrowid
        for seat_number in (1, 2):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )
        await db.commit()
    return path


@pytest.fixture
def cog(db_path):
    return test_mode_cog.TestModeCog(
        SimpleNamespace(
            db_path=db_path,
            config_service=ConfigService(db_path),
            output_router=SimpleNamespace(post_log=AsyncMock()),
        )
    )


async def _add(cog, *, name="Mock Alpha", nationality=None, team="Redline"):
    """Mock driver *name* seated at *team* in Division 1 by the roster's writer: the listing's
    tests need fake drivers seated, not the command that seats them."""
    from leaguebot.core.services.test_roster_service import add_test_driver_on

    async with get_connection(cog.bot.db_path) as db:
        result = await add_test_driver_on(
            db, driver_name=name, team_name=team, division_name=DIVISION,
            nationality=nationality,
        )
        await db.commit()
    assert not isinstance(result, str), result


# ── /test-mode nationality ────────────────────────────────────────────────


#: The switch the command flips, as its audit entry names it, and that entry's change type.
_FLAG = "test_mode_nationality_required"
_AUDIT_TYPE = "TEST_MODE_NATIONALITY_SET"

#: The league admin who runs the command, as a log line names them, the mention wrapped so it
#: notifies nobody; and the queue's "now".
ACTOR_ID = 77
_NAMED = f"Admin (`<@{ACTOR_ID}>`)"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: The command's refusal outside test mode, at the ask and as the change starts.
_REFUSAL = "⛔ This command is only available when test mode is enabled."


@pytest.fixture
async def league(tmp_path):
    """The league's server set up and in test mode, nationality on (its default), on the bot the
    entry point builds, its configuration read from the database and its real change queue's
    "now" pinned to `NOW`; `cog` is the test-mode cog on it."""
    db_path = str(tmp_path / "nationality.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = 1")
        await db.commit()
    bot = await league_bot(db_path, real_config=True)
    league_queue(bot, db_path, now=Clock(NOW))
    cog = test_mode_cog.TestModeCog.__new__(test_mode_cog.TestModeCog)
    cog.bot = bot
    return SimpleNamespace(bot=bot, cog=cog, db_path=db_path)


def _asks(league):
    """Admin's (77) interaction for `/test-mode nationality`, recording every word they are told."""
    return member_asks(
        league.bot, "test-mode nationality", user=member(ACTOR_ID, "Admin", "Admin#0001")
    )


async def _ask_nationality(league, interaction) -> None:
    """Admin runs the command, and nothing more: the queue is not run."""
    await _unwrap(test_mode_cog.TestModeCog.nationality)(league.cog, interaction)


async def _flags(db_path):
    """The nationality switch and test mode as the server's configuration holds them, None where
    it holds none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {_FLAG}, test_mode_active FROM server_configs"
        )
        row = await cursor.fetchone()
    return None if row is None else dict(row)


def _lines(league) -> list[str]:
    """Every line the log channel took, without the rule drawn beneath each."""
    return [unruled(str(line)) for line in league.bot.log_channel.sent]


class TestTheNationalityCommand:
    """`/test-mode nationality` is a core `Setting` on the change queue (#283): the command
    answers with the queue's acknowledgement, and the queue flips the switch and writes its audit
    entry in one save, stamped with the queue's clock, then updates the acknowledgement to the
    reply and writes the line, both in the words the command used before the queue. Test mode is
    judged at the ask and again as the change starts. These run on the bot the entry point
    builds, its configuration read from the database, with the real queue and "now" pinned."""

    def test_it_sits_on_the_test_mode_group(self):
        assert "nationality" in {c.name for c in test_mode_cog.TestModeCog.test_mode.commands}

    async def test_it_starts_on(self, cog, db_path):
        """Migration 042 defaults it on, as the setting it parallels defaults on."""
        config = await cog.bot.config_service.get_server_config()

        assert config.test_mode_nationality_required is True

    async def test_it_toggles_off_and_back_on(self, league):
        """The server is in test mode with nationality on. Admin (77) runs the command, and runs
        it again before the queue has run the first: the first is acknowledged as turning it off,
        the second asks the same thing and is refused as a repeat, seen by Admin alone, in the
        queue's words, the log channel taking that refusal as a line, and only the one change is
        queued. The queue runs it: the switch is saved off, with one audit entry recording
        on → off. Admin runs the command once more: it is acknowledged as turning it back on, and
        once the queue has run it the switch is on again, a second audit entry recording
        off → on; the replies read "OFF" and then "ON", and the lines "disabled" and then
        "enabled", word for word."""
        first, repeat, again = _asks(league), _asks(league), _asks(league)

        await _ask_nationality(league, first)
        await _ask_nationality(league, repeat)

        assert acknowledgement(first).startswith("⏳ Turning test-mode nationality off. ")
        [refusal] = repeat.response.send_message.await_args_list
        assert refusal.args[0] == (
            "⚠️ Turning test-mode nationality off has already been asked for and has not "
            "started yet, so it was not asked for again."
        )
        assert refusal.kwargs.get("ephemeral") is True
        assert len(await change_rows(league.db_path)) == 1
        await run_queue(league.bot)

        await _ask_nationality(league, again)
        assert acknowledgement(again).startswith("⏳ Turning test-mode nationality on. ")
        await run_queue(league.bot)

        rows = await audit_rows(league.db_path)
        assert [
            (row["change_type"], row["old_value"], row["new_value"])
            for row in rows
        ] == [
            (_AUDIT_TYPE, {_FLAG: True}, {_FLAG: False}),
            (_AUDIT_TYPE, {_FLAG: False}, {_FLAG: True}),
        ]
        assert await _flags(league.db_path) == {_FLAG: 1, "test_mode_active": 1}
        assert last_said(first) == (
            "✅ Test-mode nationality: **OFF**.\n"
            "Mock drivers already holding one keep it, and graphics draw no flag at all."
        )
        assert last_said(again) == (
            "✅ Test-mode nationality: **ON**.\n"
            "Mock drivers may be given one with `/test-mode roster add`."
        )
        assert _lines(league) == [
            f"⛔ `/test-mode nationality` refused for {_NAMED} — Turning test-mode nationality "
            "off has already been asked for and has not started yet, so it was not asked for "
            "again.",
            f"{_NAMED} | /test-mode nationality | Success\n  test_mode_nationality: disabled",
            f"{_NAMED} | /test-mode nationality | Success\n  test_mode_nationality: enabled",
        ]

    async def test_a_switch_already_where_it_was_asked_to_go_is_answered_nothing_changed(
        self, league
    ):
        """The server is in test mode with nationality on. Admin (77) runs the command, and is
        acknowledged as turning it off; before the queue runs it, the switch is already turned
        off, as a change of the same asked earlier would leave it. When the queue runs it, nothing
        is saved and nothing audited: Admin's reply becomes "Nothing changed", word for word, and
        the log channel takes a "Nothing changed" line in the place of a success line."""
        interaction = _asks(league)

        await _ask_nationality(league, interaction)
        assert acknowledgement(interaction).startswith("⏳ Turning test-mode nationality off. ")
        async with get_connection(league.db_path) as db:
            await db.execute(f"UPDATE server_configs SET {_FLAG} = 0")
            await db.commit()
        await run_queue(league.bot)

        assert last_said(interaction) == (
            "ℹ️ Nothing changed: test-mode nationality is already **OFF**."
        )
        assert await _flags(league.db_path) == {_FLAG: 0, "test_mode_active": 1}
        assert await audit_rows(league.db_path) == []
        assert _lines(league) == [
            f"{_NAMED} | /test-mode nationality | Nothing changed\n"
            "  test_mode_nationality: disabled"
        ]
        assert await stopped_job(league.db_path) is None

    async def test_the_flip_is_persisted(self, league):
        """The server is in test mode with nationality on. Admin (77) runs the command and the
        queue runs it: the switch is saved off, with one audit entry beside it recording on →
        off, naming Admin and no division, stamped with the queue's clock; test mode itself is
        still on."""
        await _ask_nationality(league, _asks(league))
        await run_queue(league.bot)

        assert await _flags(league.db_path) == {_FLAG: 0, "test_mode_active": 1}
        rows = await audit_rows(league.db_path)
        assert [
            (row["change_type"], row["old_value"], row["new_value"],
             row["actor_id"], row["division_id"], row["timestamp"])
            for row in rows
        ] == [(_AUDIT_TYPE, {_FLAG: True}, {_FLAG: False}, ACTOR_ID, None, NOW.isoformat())]

    async def test_it_is_logged(self, league):
        """The server is in test mode with nationality on. Admin (77) runs the command: they are
        answered at once with the queue's acknowledgement, naming the change's first job, and
        nothing is written until the queue runs it. Once it has, the acknowledgement reads the
        reply word for word, and the log channel takes the one line, word for word."""
        interaction = _asks(league)

        await _ask_nationality(league, interaction)

        first_job = (await step_rows(league.db_path))[0]["id"]
        assert acknowledgement(interaction) == (
            "⏳ Turning test-mode nationality off. This message will be updated when it is done; "
            "if it takes longer, the log channel will say so. "
            f"It begins with job #{first_job}."
        )
        assert await _flags(league.db_path) == {_FLAG: 1, "test_mode_active": 1}
        assert await audit_rows(league.db_path) == []

        await run_queue(league.bot)

        assert last_said(interaction) == (
            "✅ Test-mode nationality: **OFF**.\n"
            "Mock drivers already holding one keep it, and graphics draw no flag at all."
        )
        assert _lines(league) == [
            f"{_NAMED} | /test-mode nationality | Success\n  test_mode_nationality: disabled"
        ]

    @pytest.mark.parametrize("configured", [True, False], ids=["test_mode_off", "unconfigured"])
    async def test_it_is_refused_while_test_mode_is_off(self, league, configured):
        """Admin (77) runs the command with test mode off, or on a server with no configuration
        at all. They are refused at once, in one reply seen by them alone, in the words the
        command has always used; nothing is queued, the switch is not touched and nothing is
        audited; where the server is configured its log channel takes the standard refusal line,
        and where it is not there is no log channel to take one."""
        async with get_connection(league.db_path) as db:
            if configured:
                await db.execute("UPDATE server_configs SET test_mode_active = 0")
            else:
                await db.execute("DELETE FROM server_configs")
            await db.commit()
        interaction = _asks(league)

        await _ask_nationality(league, interaction)

        assert said(interaction) == [_REFUSAL]
        [reply] = interaction.response.send_message.await_args_list
        assert reply.kwargs.get("ephemeral") is True
        assert await change_rows(league.db_path) == []
        assert await audit_rows(league.db_path) == []
        if configured:
            assert await _flags(league.db_path) == {_FLAG: 1, "test_mode_active": 0}
            assert _lines(league) == [
                f"⛔ `/test-mode nationality` refused for {_NAMED} — {_REFUSAL[2:]}"
            ]
        else:
            assert await _flags(league.db_path) is None
            assert _lines(league) == []

    async def test_it_is_refused_as_it_starts_where_test_mode_was_turned_off(self, league):
        """The server is in test mode with nationality on. Admin (77) runs the command and is
        acknowledged; before the queue runs it, test mode is switched off. The change judges again
        as it starts and is refused: the switch stays on, nothing is audited, Admin's reply
        becomes the refusal the command gives at the ask, word for word, the log channel takes
        that refusal as its only line, and the queue is not stopped."""
        interaction = _asks(league)

        await _ask_nationality(league, interaction)
        assert acknowledgement(interaction).startswith("⏳ ")
        async with get_connection(league.db_path) as db:
            await db.execute("UPDATE server_configs SET test_mode_active = 0")
            await db.commit()
        await run_queue(league.bot)

        assert last_said(interaction) == _REFUSAL
        assert await _flags(league.db_path) == {_FLAG: 1, "test_mode_active": 0}
        assert await audit_rows(league.db_path) == []
        assert _lines(league) == [
            f"⛔ `/test-mode nationality` refused for {_NAMED} — {_REFUSAL[2:]}"
        ]
        assert await stopped_job(league.db_path) is None

    async def test_a_fault_in_the_save_saves_neither_the_flag_nor_its_audit(
        self, league, monkeypatch
    ):
        """The server is in test mode with nationality on. Admin (77) runs the command, and the
        audit entry's write fails on the database as the queue runs the change. The switch is not
        saved either: it is still on and no audit entry stands. Admin is told in
        `setting_change`'s words that nothing was saved and to run it again; the log channel
        gets the one failure line and no success line; the queue is not stopped."""
        from leaguebot.core.services.queue import change_queue

        monkeypatch.setattr(
            change_queue, "record_change_on",
            AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
        )
        interaction = _asks(league)

        await _ask_nationality(league, interaction)
        await run_queue(league.bot)

        assert await _flags(league.db_path) == {_FLAG: 1, "test_mode_active": 1}
        assert await audit_rows(league.db_path) == []
        assert last_said(interaction) == (
            "❌ `/test-mode nationality` stopped on a fault in the bot, not on anything you "
            "entered, and did not finish. Nothing was saved. Run `/test-mode nationality` again "
            "to retry. The fault is recorded in the log channel."
        )
        assert _lines(league) == [
            f"❌ `/test-mode nationality` failed for {_NAMED} — OperationalError. The details "
            f"are in the host's log."
        ]
        assert await stopped_job(league.db_path) is None


# ── /test-mode roster list ────────────────────────────────────────────────


class TestRosterListShowsIt:
    async def _list(self, cog) -> str:
        """The whole listing, however many messages it took to send it."""
        interaction = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.roster_list)(cog, interaction, DIVISION)
        return "\n".join(interaction.sent)

    async def test_the_column_is_headed(self, cog):
        await _add(cog, nationality="Dutch")

        assert "Nationality" in await self._list(cog)

    async def test_a_drivers_own_is_printed(self, cog):
        await _add(cog, name="Mock Alpha", nationality="brazil")

        rendered = await self._list(cog)

        assert "Brazilian" in rendered

    async def test_a_driver_without_one_prints_a_dash(self, cog):
        await _add(cog, name="Mock Alpha")

        rendered = await self._list(cog)

        assert "Mock Alpha" in rendered
        assert "—" in rendered

    async def test_the_team_column_still_stands_beside_it(self, cog):
        await _add(cog, name="Mock Alpha", nationality="Dutch")

        rendered = await self._list(cog)

        assert "Redline" in rendered
        assert rendered.index("Redline") < rendered.index("Dutch")

    async def test_a_roster_too_large_for_one_message_is_sent_as_several(
        self, cog, db_path
    ):
        """The bug: past about twenty drivers Discord refused the whole listing.

        A body over 2000 characters is answered with a 400, not truncated, so the
        manager got no roster at all and a traceback in the log. The reserve team is
        used because it seats without limit — the point is the length of the reply,
        not how the drivers came to be seated.
        """
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM divisions WHERE name = ?", (DIVISION,)
            )
            division_id = (await cursor.fetchone())[0]
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, 'Reserve', 'Reserve', 0, 1)",
                (division_id,),
            )
            await db.commit()

        for i in range(25):
            await _add(cog, name=f"Mock Driver {i:02d}", nationality="Dutch", team="Reserve")

        interaction = _Interaction()
        await _unwrap(test_mode_cog.TestModeCog.roster_list)(
            cog, interaction, DIVISION
        )

        assert len(interaction.sent) > 1, "a roster this size must span several messages"
        for message in interaction.sent:
            assert len(message) <= 2000, f"a page of {len(message)} chars would 400"
        # Every driver still reaches the manager, across however many messages it took.
        whole = "\n".join(interaction.sent)
        for i in range(25):
            assert f"Mock Driver {i:02d}" in whole
