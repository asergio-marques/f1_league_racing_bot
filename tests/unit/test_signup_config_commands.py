"""`/signup config view` and the three signup settings toggles.

Issue #208. These are the commands a league manager uses to set up signups before opening them,
and none was executed by any test.

**The three toggles all write an audit entry, and that is what these tests are chiefly about.**
Each flips a boolean or swaps a string, which is barely worth pinning on its own — but each
also records *what it was and what it became* in `audit_entries`, and that record is the
league's only account of who changed a signup rule mid-season. The three insert statements are
copied between the commands rather than shared, so each is tested for both halves of its own
entry: a toggle recording the same value as old and new would look right in every other
respect.

They run against a real migrated database for exactly that reason. The audit insert is raw SQL
with eight bound parameters and a JSON payload; a double would accept any of them.

**`config view` is a report, and the thing that makes it hard is that every setting has three
states, not two.** A channel or role can be unset, set-and-present, or set-and-since-deleted —
and the third is the one a league needs told about, because a signup channel pointing at a
deleted channel looks configured and works like nothing. `test_a_deleted_channel_is_reported_
as_not_found` and its role counterpart sit on that distinction; reporting "not configured" for
a deleted channel would send a manager looking for a setting they had already made.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import SignupCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 8908
ACTOR_ID = 4242
CHANNEL_ID = 777001
BASE_ROLE_ID = 555001
SIGNED_UP_ROLE_ID = 555002


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, config: bool = True) -> str:
    db_path = os.path.join(str(tmp_path), "signup_config.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        if config:
            await db.execute(
                "INSERT INTO signup_module_config "
                "(id, signup_channel_id, base_role_id, signed_up_role_id, signups_open) "
                "VALUES (?, ?, ?, ?, 0)",
                (1, CHANNEL_ID, BASE_ROLE_ID, SIGNED_UP_ROLE_ID),
            )
        await db.commit()
    return db_path


def _make_cog(db_path: str) -> SignupCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.signup_module_service = SignupModuleService(db_path)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    return SignupCog(bot)


def _interaction(*, channel=..., role=...):
    """An interaction whose guild resolves the configured channel and roles.

    `...` means "present"; `None` means the object was deleted after being configured,
    which is the third state `config view` has to distinguish.
    """
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]

    resolved_channel = MagicMock() if channel is ... else channel
    if resolved_channel is not None:
        resolved_channel.mention = "#signups"
    resolved_role = MagicMock() if role is ... else role
    if resolved_role is not None:
        resolved_role.mention = "@role"

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=resolved_channel)
    guild.get_role = MagicMock(return_value=resolved_role)
    interaction.guild = guild

    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_id FROM audit_entries "
            "WHERE server_id = ? ORDER BY id",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


def _fields(interaction) -> dict[str, str]:
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    return {f.name: f.value for f in embed.fields}


# ---------------------------------------------------------------------------
# The toggles
# ---------------------------------------------------------------------------


TOGGLES = [
    ("nationality", "nationality_required"),
    ("time_image", "time_image_required"),
]


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_flips_the_setting(tmp_path, command, field):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = getattr(await cog.bot.signup_module_service.get_settings(), field)

    await undecorate(getattr(SignupCog, command))(cog, _interaction())

    after = getattr(await cog.bot.signup_module_service.get_settings(), field)
    assert after is not before


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_flips_back(tmp_path, command, field):
    """A toggle that only ever turned something on would leave a league unable to undo it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = getattr(await cog.bot.signup_module_service.get_settings(), field)

    await undecorate(getattr(SignupCog, command))(cog, _interaction())
    await undecorate(getattr(SignupCog, command))(cog, _interaction())

    after = getattr(await cog.bot.signup_module_service.get_settings(), field)
    assert after is before


@pytest.mark.parametrize("command,field", TOGGLES, ids=[c for c, _ in TOGGLES])
async def test_a_toggle_records_what_it_was_and_what_it_became(tmp_path, command, field):
    """The league's only account of who changed a signup rule mid-season. An entry naming
    the same value twice would pass any test that merely counted the rows."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await undecorate(getattr(SignupCog, command))(cog, _interaction())

    entries = await _audit(db_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["change_type"] == "SIGNUP_SETTINGS_CHANGE"
    assert entry["actor_id"] == ACTOR_ID
    old = json.loads(entry["old_value"])
    new = json.loads(entry["new_value"])
    assert old["field"] == field
    assert new["field"] == field
    assert old["value"] is not new["value"]


async def test_the_time_type_command_swaps_between_the_two_kinds(tmp_path):
    """Not a boolean — the two values are the two ways a league can ask for a lap time,
    and they are set in different places in the game."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = (await cog.bot.signup_module_service.get_settings()).time_type

    await undecorate(SignupCog.time_type)(cog, _interaction())
    after = (await cog.bot.signup_module_service.get_settings()).time_type

    assert {before, after} == {"TIME_TRIAL", "SHORT_QUALIFICATION"}


async def test_the_time_type_command_records_both_values(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await undecorate(SignupCog.time_type)(cog, _interaction())

    entry = (await _audit(db_path))[0]
    assert json.loads(entry["old_value"])["field"] == "time_type"
    assert json.loads(entry["old_value"])["value"] != json.loads(entry["new_value"])["value"]


@pytest.mark.parametrize(
    "command", ["nationality", "time_type", "time_image"],
)
async def test_every_toggle_tells_the_manager_and_the_log(tmp_path, command):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(getattr(SignupCog, command))(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    cog.bot.output_router.post_log.assert_awaited_once()


@pytest.mark.parametrize(
    "command", ["nationality", "time_type", "time_image"],
)
async def test_a_server_with_no_settings_row_starts_from_the_defaults(tmp_path, command):
    """`get_settings` invents a default row rather than returning None, so the first
    toggle a league ever runs has something to flip."""
    db_path = await _make_db(tmp_path, config=False)
    cog = _make_cog(db_path)

    await undecorate(getattr(SignupCog, command))(cog, _interaction())

    assert len(await _audit(db_path)) == 1


# ---------------------------------------------------------------------------
# config view
# ---------------------------------------------------------------------------


async def test_the_configuration_is_reported(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert fields["Channel"] == "#signups"
    assert fields["Base Role"] == "@role"
    assert fields["Signed-Up Role"] == "@role"
    assert fields["Signups Open"] == "No"


async def test_a_deleted_channel_is_reported_as_not_found(tmp_path):
    """The third state. A channel that was configured and has since been deleted looks
    configured and works like nothing — reporting it as "not configured" would send a
    manager looking for a setting they had already made."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(channel=None)

    await undecorate(SignupCog.config_view)(cog, interaction)

    assert "not found" in _fields(interaction)["Channel"]


async def test_a_deleted_role_is_reported_as_not_found(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction(role=None)

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert "not found" in fields["Base Role"]
    assert "not found" in fields["Signed-Up Role"]


async def test_an_unconfigured_channel_is_reported_as_not_configured(tmp_path):
    """Distinct from "not found" — nothing was ever set here."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signup_channel_id = NULL, base_role_id = NULL "
            "",
        )
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert "not configured" in fields["Channel"]
    assert "not configured" in fields["Base Role"]


async def test_a_server_that_has_configured_nothing_reports_not_set(tmp_path):
    """No configuration row at all — the state a league is in before `/signup channel`."""
    db_path = await _make_db(tmp_path, config=False)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert fields["Channel"] == "Not set"
    assert fields["Signups Open"] == "No"


async def test_the_report_names_the_settings_as_well_as_the_channels(tmp_path):
    """The three toggles have no other read-back, so this report is the only way a league
    can confirm what it set."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    fields = _fields(interaction)
    assert fields["Nationality Required"] in ("ON", "OFF")
    assert fields["Time Type"] in ("Time Trial", "Short Qualification")
    assert fields["Time Image Required"] in ("ON", "OFF")


async def test_the_report_reflects_a_toggle_that_has_been_flipped(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    before = _interaction()
    await undecorate(SignupCog.config_view)(cog, before)
    was = _fields(before)["Nationality Required"]

    await undecorate(SignupCog.nationality)(cog, _interaction())
    after = _interaction()
    await undecorate(SignupCog.config_view)(cog, after)

    assert _fields(after)["Nationality Required"] != was


async def test_open_signups_are_reported_as_open(tmp_path):
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE signup_module_config SET signups_open = 1",
        )
        await db.commit()
    cog = _make_cog(db_path)
    interaction = _interaction()

    await undecorate(SignupCog.config_view)(cog, interaction)

    assert _fields(interaction)["Signups Open"] == "Yes"
