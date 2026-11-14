"""`/signup channel` — setting the channel, and the permissions that go with it.

The command had no test of its own, and that is how a `NameError` shipped: the guard
against reusing the bot's command channel was replaced by the server-wide
`find_channel_use` check (775443d), and the `server_cfg` that guard had fetched was left
referenced fifty lines below, where the interaction role's overwrite is built. Nothing
exercised the command past the guard, so nothing noticed.

These drive the command to its end. The permission overwrites are the part that was broken
and the part most easily broken again, since they are built from three roles read from
three different places.

**It is a signup `Setting` on the change queue** (#286, item 13): the manager is answered with
the queue's acknowledgement, the channel and its audit entry are one save, and the overwrites
follow the save, the new channel's set first and the old channel's cleared last (#481). Every
refusal the bot can foresee, its permission on either channel among them, refuses with nothing
saved; only a refusal Discord gives that the check could not foresee leaves the channel saved,
with the fault named. The tests run the settings league (`settings_league`) with signup on and its
configuration read from the database, the guild and the bot finding the channels each test gives, and run the queue.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.queue.setting_change import NOTHING_SAVED
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.signup.cogs.signup_cog import SignupCog
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    MANAGER_ROLE_ID,
    SERVER_ID,
    Clock,
    acknowledgement,
    league_bot,
    league_queue,
    run_queue,
    seed_server,
)
from tests.support.settings_league import (
    NOW,
    SettingsLeague,
    fault_in,
    interaction,
    last_said,
    log_lines,
    scheduler_double,
    switch,
)
from tests.support.undecorate import undecorate

BASE_ROLE = 555
#: The member running the command, as a log line names them.
NAMED = "Admin"
#: The signup channel the league holds before a move, and the channel it is moved to.
OLD, NEW = 650, 700

#: Why each test pinning `/signup channel` as a queued setting fails until it is one.
_ON_THE_QUEUE = "#286: /signup channel is not yet a Setting asked of the change queue"


async def _league(tmp_path, *, configured: bool = True, held: int | None = None):
    """The settings league with signup alone on (`settings_league`), its configuration read from
    the database, its base role `BASE_ROLE`, holding the signup module's configuration where
    *configured*, its signup channel *held*."""
    db_path = os.path.join(str(tmp_path), "settings.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = await league_bot(db_path, scheduler=scheduler_double(), real_config=True)
    await switch(bot, ("signup",), on=True)
    league_queue(bot, db_path, now=Clock(NOW))
    league = SettingsLeague(bot, db_path, bot.scheduler_service)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE server_configs SET base_role_id = ?", (BASE_ROLE,))
        if configured:
            await db.execute(
                "INSERT INTO signup_module_config (id, signup_channel_id) VALUES (1, ?)", (held,)
            )
        await db.commit()
    return league


def _discord(league, *channels):
    """The guild of the league holding *channels*, found by the guild and by the bot alike; every
    role is found, and the bot is a member of it. Returns the list each channel's edit appends
    `(channel id, overwrites)` to, in the order Discord was asked."""
    edits: list[tuple[int, dict]] = []
    by_id = {channel.id: channel for channel in channels}
    me = MagicMock(name="the bot's member")
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    guild.get_role = MagicMock(side_effect=lambda rid: MagicMock(spec=discord.Role, id=rid))
    guild.get_member = MagicMock(return_value=me)
    guild.default_role = MagicMock(spec=discord.Role)
    guild.me = me
    for channel in channels:
        channel.guild = guild
        fault = channel._fault

        async def _edit(*, overwrites, _channel=channel, _fault=fault):
            edits.append((_channel.id, overwrites))
            if _fault is not None:
                raise _fault

        channel.edit = AsyncMock(side_effect=_edit)
    league.bot.get_guild = MagicMock(return_value=guild)
    league.bot.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    league.guild = guild
    return edits


def _channel(channel_id: int = NEW, *, manage_channels=True, manage_roles=True, fault=None):
    """A text channel on which the bot holds Manage Channel and Manage Permissions, as
    *manage_channels* and *manage_roles* say, whose edit raises *fault* where one is given."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.name = f"signups-{channel_id}"
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(
        return_value=MagicMock(
            manage_channels=manage_channels, manage_roles=manage_roles,
            view_channel=True, send_messages=True,
        )
    )
    channel._fault = fault
    return channel


async def _ask(league, channel):
    """The member runs `/signup channel` for *channel*, the queue not yet run."""
    asked = interaction(league.bot, "signup channel")
    if getattr(league, "guild", None) is not None:
        asked.guild = league.guild
    await undecorate(SignupCog.signup_channel)(SignupCog(league.bot), asked, channel)
    return asked


async def _run(league, channel):
    """The member runs `/signup channel` for *channel*, then the queue."""
    asked = await _ask(league, channel)
    await run_queue(league.bot)
    return asked


async def _stored(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
        row = await cursor.fetchone()
    return None if row is None else row[0]


async def _audits(db_path) -> list[tuple[str, int]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT change_type, actor_id FROM audit_entries ORDER BY id")
        return [tuple(row) for row in await cursor.fetchall()]


def _forbidden():
    return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")


#: The hub's words for a channel whose permissions the bot may not set.
def _may_not_edit(channel) -> str:
    return (
        f"❌ The bot needs **Manage Channel** and **Manage Permissions** on {channel.mention} "
        "to set who may see it. The signup channel was not changed."
    )


async def _assert_one_refusal_line(league, command: str, said: str) -> None:
    [line] = await log_lines(league)
    assert line.startswith(f"⛔ `{command}` refused for {NAMED} (")
    assert said in line


async def test_setting_a_free_channel_stores_it(tmp_path):
    """The regression: this raised `NameError` before reaching the store."""
    league = await _league(tmp_path)
    channel = _channel()
    _discord(league, channel)

    await _run(league, channel)

    assert await _stored(league.db_path) == NEW


async def test_the_interaction_role_is_read_for_the_overwrites(tmp_path):
    """What the orphaned `server_cfg` was for.

    The role is fetched from the guild by the id on the server config; losing that read
    is what raised, and a silent `None` here would leave the stewards unable to see the
    channel they had just configured.
    """
    league = await _league(tmp_path)
    channel = _channel()
    _discord(league, channel)

    await _run(league, channel)

    asked = [call.args[0] for call in league.guild.get_role.call_args_list]
    assert MANAGER_ROLE_ID in asked, "the interaction role was never looked up"
    assert BASE_ROLE in asked, "the base role was never looked up"


async def test_a_channel_already_in_use_is_refused(tmp_path):
    """The guard that replaced the old one: the league's interaction channel is the bot's
    command channel."""
    league = await _league(tmp_path)
    channel = _channel(INTERACTION_CHANNEL_ID)
    _discord(league, channel)

    asked = await _run(league, channel)

    assert "already the" in last_said(asked)
    assert await _stored(league.db_path) is None, "a refused channel was stored"


async def test_the_command_runs_to_the_end_without_an_unbound_name(tmp_path):
    """A blunt guard against the class of fault, not the instance, and the answer every
    settings command gives (criterion 6).

    The body reads three roles and two configurations from four places, and an editing
    slip that drops one of the reads raises only when the command is run all the way
    through. The manager sets 700 as the signup channel: before the queue runs they are told
    it is under way, and nothing is stored or edited; once it runs, they are told the channel
    is set, and the channel and one audit entry by them are saved.
    """
    league = await _league(tmp_path)
    channel = _channel()
    edits = _discord(league, channel)

    asked = await _ask(league, channel)

    assert acknowledgement(asked).startswith("⏳ ")
    assert await _stored(league.db_path) is None
    assert edits == []

    await run_queue(league.bot)

    assert last_said(asked) == f"✅ Signup channel set to <#{NEW}>."
    assert await _stored(league.db_path) == NEW
    assert [kind for kind, _actor in await _audits(league.db_path)] == ["SIGNUP_CHANNEL_SET"]


async def test_a_fault_in_the_save_saves_no_channel_and_says_to_run_it_again(tmp_path):
    """Criterion 6: the manager sets 700 as the signup channel while the audit entry's write
    faults. Nothing is saved, no channel's permissions are touched, the manager is told
    nothing was saved and to run the command again, and no Success line is written."""
    league = await _league(tmp_path)
    channel = _channel()
    edits = _discord(league, channel)

    asked = await _ask(league, channel)
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert last_said(asked) == failure_reply(
        "`/signup channel`", NOTHING_SAVED.format(command="/signup channel")
    )
    assert await _stored(league.db_path) is None
    assert await _audits(league.db_path) == []
    assert edits == []
    assert not any(" | Success" in line for line in await log_lines(league))


# ---------------------------------------------------------------------------
# What the bot may not do: foreseen and refused, or met late and named (D1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "refused, manage_channels, manage_roles",
    [
        pytest.param("new", True, False, id="new-only-manage-channels"),
        pytest.param("new", False, True, id="new-only-manage-roles"),
        pytest.param("old", True, False, id="old-only-manage-channels"),
        pytest.param("old", False, True, id="old-only-manage-roles"),
    ],
)
async def test_signup_channel_refuses_early_without_both_permissions(
    tmp_path, refused, manage_channels, manage_roles
):
    """The channel's permissions are set with both, which Discord shows on a channel as Manage
    Channel and Manage Permissions. The league's signup channel is 650 and the manager moves it
    to 700, the bot holding only one of the two on the *refused* channel (700, the new, or 650,
    the old). The move is refused before anything is edited, in the hub's words naming that
    channel; neither channel's permissions are touched, 650 stays the signup channel, and the
    refusal is logged. Takes the place of the planned
    `test_a_foreseeable_permission_refusal_saves_nothing`."""
    league = await _league(tmp_path, held=OLD)
    lacking = {"manage_channels": manage_channels, "manage_roles": manage_roles}
    old = _channel(OLD, **(lacking if refused == "old" else {}))
    new = _channel(NEW, **(lacking if refused == "new" else {}))
    edits = _discord(league, old, new)

    asked = await _run(league, new)

    refused_channel = new if refused == "new" else old
    assert last_said(asked) == _may_not_edit(refused_channel)
    assert edits == []
    assert await _stored(league.db_path) == OLD
    await _assert_one_refusal_line(league, "/signup channel", "Manage Permissions")


async def test_an_unforeseen_refusal_keeps_the_channel_and_names_the_fault(tmp_path):
    """The bot holds both permissions on 700, so the check foresees nothing; Discord refuses
    the overwrites all the same. 700 stays saved as the signup channel, and both the reply and
    the one Success line name 700 and the fault, in plain words rather than Discord's error."""
    league = await _league(tmp_path)
    channel = _channel(fault=_forbidden())
    _discord(league, channel)

    asked = await _run(league, channel)

    assert await _stored(league.db_path) == NEW
    replied = last_said(asked)
    assert f"Signup channel set to <#{NEW}>" in replied
    assert "Discord refused or failed a request from the bot" in replied
    assert "Missing Permissions" not in replied
    [line] = await log_lines(league)
    assert line.startswith(f"{NAMED} (")
    assert "| /signup channel | Success" in line
    assert "Discord refused or failed a request from the bot" in line


async def test_moving_the_signup_channel_sets_the_new_before_clearing_the_old(tmp_path):
    """#481: the league's signup channel is 650 and the manager moves it to 700. Discord is
    asked to set 700's overwrites first and only then to clear 650's, so a refusal on 700 can
    never leave 650 already stripped; 700 is saved."""
    league = await _league(tmp_path, held=OLD)
    old, new = _channel(OLD), _channel(NEW)
    edits = _discord(league, old, new)

    await _run(league, new)

    assert [channel_id for channel_id, _overwrites in edits] == [NEW, OLD]
    assert edits[0][1] != {}
    assert edits[1][1] == {}
    assert await _stored(league.db_path) == NEW


@pytest.mark.parametrize(
    "fault",
    [
        _forbidden,
        lambda: discord.HTTPException(MagicMock(status=500, reason="Server Error"), "boom"),
    ],
    ids=["forbidden", "http-error"],
)
async def test_a_move_that_cannot_unlock_the_old_channel_says_so_and_stands(tmp_path, fault):
    """The signup channel is <#650>, and the manager moves it to <#700>. Discord will not clear
    the old channel's permissions, but the new channel is set: the move stands, and both the
    reply and the one Success line name <#650> as left as it was, to put right by hand."""
    league = await _league(tmp_path, held=OLD)
    old, new = _channel(OLD, fault=fault()), _channel(NEW)
    _discord(league, old, new)

    asked = await _run(league, new)

    assert await _stored(league.db_path) == NEW
    replied = last_said(asked)
    assert f"Signup channel set to <#{NEW}>" in replied
    assert f"<#{OLD}>" in replied
    [line] = await log_lines(league)
    assert line.startswith(f"{NAMED} (")
    assert "| /signup channel | Success" in line
    assert f"<#{OLD}>" in line


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_signup_channel_edit_fault_goes_to_the_failure_path(tmp_path):
    """Anything but Discord's refusal is a fault in the bot. The manager sets 700, and setting
    its overwrites raises an error of the bot's own once the channel is saved. It is work after
    the save, so 700 stays saved as the signup channel; the manager is not told the channel was
    set as if all went well, but that its permissions on <#700> were not set; the log channel
    carries one failure line for `/signup channel` naming the kind of fault; and the error's own
    text reaches neither the reply nor the log."""
    league = await _league(tmp_path)
    channel = _channel(fault=RuntimeError("gateway closed"))
    _discord(league, channel)

    asked = await _run(league, channel)

    assert await _stored(league.db_path) == NEW
    replied = last_said(asked)
    assert replied != f"✅ Signup channel set to <#{NEW}>."
    assert f"<#{NEW}>" in replied and "permission" in replied.lower()
    assert "gateway closed" not in replied
    lines = await log_lines(league)
    [failed] = [line for line in lines if line.startswith("❌ ")]
    assert failed.startswith(f"❌ `/signup channel` failed for {NAMED} — RuntimeError.")
    assert not any("gateway closed" in line for line in lines)


def _day(value: int = 5):
    choice = MagicMock()
    choice.value = str(value)
    choice.name = "Friday"
    return choice


#: Every refusal `/signup channel` makes, and the fixed-configuration refusal it shares with
#: the signup settings commands: the command's body, its arguments, the setup, the command
#: as the line names it, and a fragment of the reply.
SIGNUP_CHANNEL_REFUSALS = [
    pytest.param("signup_channel", lambda: (_channel(),), "unconfigured", "/signup channel",
                 "not configured", id="signup-channel-not-configured"),
    pytest.param("signup_channel", lambda: (_channel(INTERACTION_CHANNEL_ID),), None,
                 "/signup channel", "already the", id="signup-channel-already-in-use"),
    pytest.param("signup_channel", lambda: (_channel(),), "fixed", "/signup channel",
                 "fixed for Season 3", id="signup-channel-fixed"),
    pytest.param("nationality", lambda: (), "fixed", "/signup nationality",
                 "fixed for Season 3", id="signup-nationality-fixed"),
    pytest.param("time_type", lambda: (), "fixed", "/signup time-type",
                 "fixed for Season 3", id="signup-time-type-fixed"),
    pytest.param("time_image", lambda: (), "fixed", "/signup time-image",
                 "fixed for Season 3", id="signup-time-image-fixed"),
    pytest.param("time_slot_add", lambda: (_day(), "21:00"), "fixed", "/signup time-slot add",
                 "fixed for Season 3", id="signup-time-slot-add-fixed"),
    pytest.param("time_slot_remove", lambda: (1,), "fixed", "/signup time-slot remove",
                 "fixed for Season 3", id="signup-time-slot-remove-fixed"),
]


@pytest.mark.parametrize("command, args, setup, named, said", SIGNUP_CHANNEL_REFUSALS)
async def test_every_signup_channel_refusal_reaches_the_log_channel(
    tmp_path, command, args, setup, named, said
):
    """Each refusal answers the manager as before and writes one line in the standard form."""
    league = await _league(tmp_path, configured=setup != "unconfigured")
    if setup == "fixed":
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', 'SETUP', 3, 'WAITING')"
            )
            await db.commit()
    arguments = args()
    _discord(league, *(a for a in arguments if isinstance(a, discord.TextChannel)))
    asked = interaction(league.bot, named.lstrip("/"))
    asked.guild = league.guild

    await undecorate(getattr(SignupCog, command))(SignupCog(league.bot), asked, *arguments)
    await run_queue(league.bot)

    assert said in last_said(asked)
    await _assert_one_refusal_line(league, named, said)


# ---------------------------------------------------------------------------
# The withdrawn alias (#124)
# ---------------------------------------------------------------------------


def test_the_signup_config_group_holds_only_view():
    """`/signup config channel` called the `/signup channel` command object, which is not
    callable, so it raised on every use and never set a channel (#124). It is withdrawn;
    `/signup channel` is the way to set it, and `view` is all the group holds."""
    assert [command.name for command in SignupCog.config_group.commands] == ["view"]


def test_nothing_current_offers_the_withdrawn_config_channel():
    """A guide or reply naming it would send a league to a command Discord no longer offers.
    `specs/` is a historical record and is not read."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    paths = [
        *sorted((root / "src").rglob("*.py")),
        root / "README.md",
        *sorted((root / "docs" / "how-to").glob("*.md")),
        *sorted((root / "docs" / "wip-specs").glob("*.md")),
    ]
    offenders = sorted(
        str(path.relative_to(root))
        for path in paths
        if "/signup config channel" in path.read_text(encoding="utf-8")
    )
    assert offenders == []
