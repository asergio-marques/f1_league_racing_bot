"""The test-mode `/signup` group: a fake driver's signup started, cancelled and run as any (#600).

Discord's member field cannot carry a fake driver, so while test mode is on `/signup` is a group of
its own, the test-mode `/signup` cog, as the test-mode `/driver` is. Its `cancel` takes a driver as
text, a mention or an ID, offered by name; its `start` asks `signup.start` for a fake driver in the
stead of the Sign Up press it cannot make, a league admin's; every other subcommand runs the member
group's body, so it acts as the member one does. Every one of them is refused while test mode is
off, and the group is kept off the global tree as its cog loads.

The league is `test_wizard_changes`'s: `setup_league` at Signups, the window open in the signup
channel (620) inside the category "Signups" with room to spare and the bot allowed to manage
channels there, and test mode on unless said otherwise. Mock Alpha (`MOCK`) is a fake driver, no
member of the server holding its ID. Admin (77) holds the league admin role.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from tests.signup.test_wizard_changes import MOCK, MOCK_NAME, START_KIND
from tests.support.season_league import ADMIN_ID, LEWIS, SIGNING_UP, SeasonLeague

_NOT_BUILT = "#600: the test-mode /signup group is not yet built"

#: Every subcommand of the member `/signup`, by its qualified name, read off the member group.
_MEMBER_COMMANDS = sorted(
    command.qualified_name
    for command in SignupCog.signup.walk_commands()
    if not hasattr(command, "walk_commands")
)
_OTHER_COMMANDS = [name for name in _MEMBER_COMMANDS if name != "signup cancel"]


async def _league(tmp_path: Any, *, test_mode: bool = True) -> SeasonLeague:
    """The league of the module docstring, Mock Alpha created at Not Signed Up."""
    from tests.signup.test_wizard_changes import _league as signups_league

    league = await signups_league(tmp_path)
    if test_mode:
        await league.set_test_mode(True)
    await league.write(
        "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, 'NOT_SIGNED_UP', 1, ?)",
        str(MOCK), MOCK_NAME,
    )
    league.absent.add(MOCK)
    return league


def _asked_by_admin(league: SeasonLeague, command: str) -> Any:
    """The interaction of the league admin Admin (77) running `/<command>`, a form it opens kept
    on `send_modal`."""
    from tests.support.change_queue import tier_member
    from tests.support.settings_league import interaction

    asked = interaction(
        league.bot, command,
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    asked.response.send_modal = AsyncMock()
    return asked


def _test_cog(league: SeasonLeague) -> Any:
    from leaguebot.signup.cogs.signup_test_cog import SignupTestCog

    return SignupTestCog(league.bot)


def _command(cog: Any, qualified: str) -> Any:
    """The command *qualified* ("signup start", "signup config view") of *cog*'s `/signup`."""
    [group] = [c for c in cog.get_app_commands() if c.name == "signup"]
    found = group
    for part in qualified.split()[1:]:
        found = found.get_command(part)
    assert found is not None, qualified
    return found


async def _run_test_command(league: SeasonLeague, qualified: str, *args: Any) -> Any:
    """`/<qualified>` of the test-mode group, run by Admin with *args*: the interaction."""
    from tests.support.undecorate import undecorate

    cog = _test_cog(league)
    asked = _asked_by_admin(league, qualified.removeprefix("signup "))
    await undecorate(_command(cog, qualified))(cog, asked, *args)
    return asked


async def _driver_state(league: SeasonLeague, user_id: int) -> str | None:
    rows = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(user_id)
    )
    return rows[0]["current_state"] if rows else None


# ---------------------------------------------------------------------------
# /signup start
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_signup_start_asks_a_fake_driver_s_signup_with_the_admin_as_its_actor(tmp_path):
    """Test mode is on, at Signups with the window open, and Mock Alpha a fake driver at Not
    Signed Up. Admin runs the test-mode `/signup start` naming Mock Alpha by its ID, and the queue
    runs. One `signup.start` was asked, for Mock Alpha's ID, recorded as asked by Admin, and ran
    to its end; and the log channel's "Signup | Started" line names Admin as the one who acted
    and Mock Alpha by its test name."""
    import json

    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import log_lines

    league = await _league(tmp_path)

    await _run_test_command(league, "signup start", str(MOCK))
    await run_queue(league.bot)

    [change] = [row for row in await change_rows(league.db_path) if row["kind"] == START_KIND]
    assert json.loads(change["payload"])["user_id"] == str(MOCK)
    assert change["actor_id"] == ADMIN_ID
    assert change["state"] == "DONE"
    [started] = [line for line in await log_lines(league) if "Signup | Started" in line]
    assert f"<@{ADMIN_ID}>" in started and MOCK_NAME in started


#: Each refusal of `/signup start`: how the league stands, the driver named, and words the reply
#: carries (None where the plan fixes none).
_START_REFUSALS = [
    pytest.param("real", str(LEWIS), "only a fake driver is signed up this way", id="a real driver"),
    pytest.param("test mode off", str(MOCK), "test mode", id="test mode off"),
    pytest.param("closed", str(MOCK), "Signups are closed.", id="signups closed"),
    pytest.param("PENDING_SIGNUP_COMPLETION", str(MOCK), None, id="its signup under way"),
    pytest.param("UNASSIGNED", str(MOCK), None, id="its signup approved"),
]


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize(("standing", "named", "words"), _START_REFUSALS)
async def test_signup_start_is_refused(tmp_path, standing, named, words):
    """Admin runs the test-mode `/signup start` naming *named*, where: it is Lewis, a real driver;
    test mode is off; the signup window is closed; or Mock Alpha's own signup is under way, or
    already approved. The reply refuses it (with *words*, where given; naming the restart, too,
    where test mode is off); after the queue runs no signup channel was created and the driver's
    state is as it was; and the log channel takes the refusal as a line."""
    from leaguebot.framework.utils.log_lines import reply_reason
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path, test_mode=standing != "test mode off")
    if standing == "closed":
        await league.write("UPDATE signup_module_config SET signups_open = 0")
    elif standing.isupper():
        await league.write(
            "UPDATE driver_profiles SET current_state = ? WHERE discord_user_id = ?",
            standing, str(MOCK),
        )
    before = await _driver_state(league, int(named))

    asked = await _run_test_command(league, "signup start", named)
    await run_queue(league.bot)

    reply = last_said(asked)
    assert reply.startswith("⛔")
    if words is not None:
        assert words.casefold() in reply.casefold()
    if standing == "test mode off":
        assert "restart" in reply.casefold()
    assert league.created == []
    assert await _driver_state(league, int(named)) == before
    assert [line for line in await log_lines(league)
            if line.startswith("⛔") and reply_reason(reply) in line]


# ---------------------------------------------------------------------------
# /signup cancel
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_cancel_of_a_fake_driver_by_id_asks_what_the_member_cancel_asks(
    tmp_path, monkeypatch
):
    """Test mode is on. Kimi, a real driver, and Mock Alpha, a fake one, each have a signup
    awaiting review. Admin runs the member `/signup cancel` of Kimi and the test-mode one of Mock
    Alpha by its ID, submitting each form with the same reason. Each asks the queue for the same
    change with the same payload, but for its own driver: Mock Alpha's ID in place of Kimi's."""
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from tests.signup.test_wizard_changes import _submit_cancel
    from tests.support.season_league import signing_up
    from tests.support.undecorate import undecorate

    league = await _league(tmp_path)
    await signing_up(league, SIGNING_UP, state="PENDING_ADMIN_APPROVAL")
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_ADMIN_APPROVAL' "
        "WHERE discord_user_id = ?", str(MOCK),
    )
    ask = AsyncMock()
    monkeypatch.setattr(league.bot.change_queue, "ask", ask)

    member = _asked_by_admin(league, "cancel")
    await undecorate(SignupCog.signup_cancel)(
        SignupCog(league.bot), member, league._member(SIGNING_UP)
    )
    await _submit_cancel(league, member, "Signed up twice")
    fake = await _run_test_command(league, "signup cancel", str(MOCK))
    await _submit_cancel(league, fake, "Signed up twice")

    [(real_kind, real_payload), (fake_kind, fake_payload)] = [
        call.args[:2] for call in ask.await_args_list
    ]
    assert fake_kind == real_kind
    assert fake_payload == {**real_payload, "user_id": str(MOCK)}


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize("text", ["nobody", "555"])
async def test_cancel_naming_no_driver_is_refused_naming_the_text(tmp_path, text):
    """Test mode is on. Admin runs the test-mode `/signup cancel` naming *text*, which is neither
    a mention nor an ID of any driver. The reply refuses it, naming what was typed; no form is
    opened; the log channel takes the refusal as a line; and nothing is asked of the queue."""
    from leaguebot.framework.utils.log_lines import reply_reason
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path)

    asked = await _run_test_command(league, "signup cancel", text)

    reply = last_said(asked)
    assert reply.startswith("⛔") and text in reply
    asked.response.send_modal.assert_not_awaited()
    assert [line for line in await log_lines(league)
            if line.startswith("⛔") and reply_reason(reply) in line]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize("qualified", ["signup cancel", "signup start"])
async def test_each_driver_field_offers_drivers_by_name(tmp_path, qualified):
    """Test mode is on. Admin starts typing in the driver field of the test-mode `/<qualified>`:
    the field is a text field, and it offers the drivers the server's name list gives, by name,
    for what is typed, nothing typed and "mOCK" alike, "mOCK" offering Mock Alpha alone."""
    from types import SimpleNamespace

    import discord

    from leaguebot.core.services.drivers.driver_reference import driver_choices

    league = await _league(tmp_path)
    command = _command(_test_cog(league), qualified)
    [parameter] = [p for p in command.parameters if p.type is discord.AppCommandOptionType.string]

    assert parameter.autocomplete
    for typed in ("", "mOCK"):
        asked = _asked_by_admin(league, qualified)
        asked.response.is_done = MagicMock(return_value=False)
        asked.response.autocomplete = AsyncMock()
        await command._invoke_autocomplete(
            asked, parameter.name, SimpleNamespace(**{parameter.name: typed})
        )
        [offered] = asked.response.autocomplete.await_args.args
        listed = await driver_choices(league.db_path, league.bot.driver_names_read, typed)
        assert [(c.name, c.value) for c in offered] == [(c.name, c.value) for c in listed]
        if typed:
            assert [(c.name, c.value) for c in offered] == [(MOCK_NAME, str(MOCK))]


# ---------------------------------------------------------------------------
# Every other subcommand, and test mode off
# ---------------------------------------------------------------------------


def _record_bodies(monkeypatch: Any) -> list[tuple[str, tuple, dict]]:
    """Every command body `signup_commands` holds, swapped for one that records its call (its name
    and arguments, a cog given as "cog") and does nothing, wherever a cog module binds it."""
    import inspect

    from discord.ext import commands

    from leaguebot.signup.cogs import signup_cog, signup_commands, signup_test_cog

    calls: list[tuple[str, tuple, dict]] = []
    for name, body in vars(signup_commands).items():
        if name.startswith("_") or not inspect.iscoroutinefunction(body):
            continue
        if getattr(body, "__module__", None) != signup_commands.__name__:
            continue

        async def _recorded(*args: Any, _name: str = name, **kwargs: Any) -> None:
            shown = tuple("cog" if isinstance(a, commands.Cog) else a for a in args)
            calls.append((_name, shown, kwargs))

        for module in (signup_commands, signup_cog, signup_test_cog):
            if getattr(module, name, None) is body:
                monkeypatch.setattr(module, name, _recorded)
    return calls


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize("qualified", _OTHER_COMMANDS)
async def test_every_other_subcommand_runs_the_member_one_s_body(tmp_path, monkeypatch, qualified):
    """Test mode is on. Admin runs `/<qualified>` of the member group and then of the test-mode
    group, with the same values. Each runs one and the same body, with the same arguments: the
    test-mode subcommand acts as the member one does."""
    from tests.support.undecorate import undecorate

    league = await _league(tmp_path)
    calls = _record_bodies(monkeypatch)
    member_cog, test_cog = SignupCog(league.bot), _test_cog(league)
    member_command = _command(member_cog, qualified)
    values = {p.name: MagicMock(name=p.name) for p in member_command.parameters}
    asked = _asked_by_admin(league, qualified.removeprefix("signup "))

    await undecorate(member_command)(member_cog, asked, **values)
    member_calls = list(calls)
    calls.clear()
    await undecorate(_command(test_cog, qualified))(test_cog, asked, **values)

    assert len(member_calls) == 1
    assert calls == member_calls


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
@pytest.mark.parametrize("qualified", sorted([*_MEMBER_COMMANDS, "signup start"]))
async def test_every_test_mode_subcommand_is_refused_while_test_mode_is_off(
    tmp_path, monkeypatch, qualified
):
    """Test mode is off (a swap having failed, the test-mode group still shows). Admin runs the
    test-mode `/<qualified>`. The reply refuses it, saying test mode is off and that a restart
    puts `/signup` right; no command body runs; and nothing is asked of the queue."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said
    from tests.support.undecorate import undecorate

    league = await _league(tmp_path, test_mode=False)
    calls = _record_bodies(monkeypatch)
    cog = _test_cog(league)
    command = _command(cog, qualified)
    values = {p.name: MagicMock(name=p.name) for p in command.parameters}
    asked = _asked_by_admin(league, qualified.removeprefix("signup "))

    await undecorate(command)(cog, asked, **values)

    reply = last_said(asked)
    assert reply.startswith("⛔")
    assert "test mode" in reply.casefold() and "restart" in reply.casefold()
    assert "/signup" in reply
    assert calls == []
    assert await change_rows(league.db_path) == []


# ---------------------------------------------------------------------------
# Both groups on one bot
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=_NOT_BUILT)
async def test_both_signup_groups_load_and_the_global_tree_carries_the_member_group():
    """Both `/signup` cogs are added to one bot, the member group's first: neither clashes with
    the other, and the global command tree carries `/signup` once, the member group's, which has
    no `start`. The test-mode group carries every subcommand of the member group, and `start`."""
    import discord
    from discord.ext import commands

    from leaguebot.signup.cogs.signup_test_cog import SignupTestCog

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    member_cog, test_cog = SignupCog(bot), SignupTestCog(bot)
    [test_group] = [c for c in test_cog.get_app_commands() if c.name == "signup"]
    test_commands = sorted(
        c.qualified_name for c in test_group.walk_commands() if not hasattr(c, "walk_commands")
    )

    await bot.add_cog(member_cog)
    await bot.add_cog(test_cog)

    assert [c.name for c in bot.tree.get_commands()].count("signup") == 1
    assert bot.tree.get_command("signup") is member_cog.signup
    assert member_cog.signup.get_command("start") is None
    assert test_commands == sorted([*_MEMBER_COMMANDS, "signup start"])
