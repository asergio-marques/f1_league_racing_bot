"""Taking a season's roles back from its drivers when the season ends.

Issue #208. `_revoke_season_roles` was patched out wherever it was reached, so its own body never
ran. It is called on both `/season complete` and `/season cancel`, and it is what stops a
finished season's drivers carrying its division and team roles into the next one.

**Every real driver assigned in the season loses their placement roles and the signed-up role.**
Placement roles are the division and the team; the signed-up role was granted at approval and
is not a placement role, so it is taken separately — otherwise a league would enter its next
signup window with half its server still marked as signed up.

**Test drivers are skipped.** They are a rehearsal's fiction with no Discord member behind them,
and asking Discord for one would be a failed fetch per mock driver.

**A driver no longer in the server is stepped over.** The roles went with them, and one departed
member must not stop the rest of the season being cleared.

**A driver without the signed-up role is not asked to lose it**, and a league with no such role
configured revokes placement roles only. Each is a Discord call that would fail or do nothing.

**Only this season's assignments are read.** A driver who raced last season and has not been
placed this season keeps whatever roles the current season gave them.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_end_service import _revoke_season_roles  # noqa: E402

SERVER_ID = 13908
SEASON_ID = 1
OTHER_SEASON_ID = 2
SIGNED_UP_ROLE = 555


async def _make_db(tmp_path, *, name="revoke_roles", signed_up_role=SIGNED_UP_ROLE, drivers=None):
    """*drivers* are ``(profile_id, discord_user_id, is_test, season_id)``."""
    drivers = drivers if drivers is not None else [(31, 101, 0, SEASON_ID), (32, 102, 0, SEASON_ID)]
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for season_id, status in ((SEASON_ID, "ACTIVE"), (OTHER_SEASON_ID, "COMPLETED")):
            await db.execute(
                "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
                "VALUES (?, ?, ?, '2026-01-01', ?)",
                (season_id, SERVER_ID, season_id, status),
            )
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, 'Pro', 1, 600)",
                (season_id * 10, season_id),
            )
        for profile_id, uid, is_test, season_id in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state, "
                "is_test_driver) VALUES (?, ?, ?, 'ASSIGNED', ?)",
                (profile_id, SERVER_ID, str(uid), is_test),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id) VALUES (?, ?, ?)",
                (profile_id, season_id, season_id * 10),
            )
        if signed_up_role is not None:
            await db.execute(
                "INSERT INTO signup_module_config (id, signed_up_role_id) VALUES (?, ?)",
                (1, signed_up_role),
            )
        await db.commit()
    return db_path


#: One object for the role, as Discord's own role equality is by id: a member "holds" it by
#: carrying this same object, which is what `signed_up_role in member.roles` tests.
_SIGNED_UP = MagicMock()
_SIGNED_UP.id = SIGNED_UP_ROLE


def _member(uid, *, has_signed_up=True):
    member = MagicMock()
    member.id = uid
    member.roles = [_SIGNED_UP] if has_signed_up else []
    return member


def _guild(members: dict, *, missing=(), fetch_fails=()):
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: None if uid in missing else members.get(uid))

    async def _fetch(uid):
        if uid in fetch_fails:
            raise discord.HTTPException(MagicMock(status=404), "unknown member")
        return members.get(uid)

    guild.fetch_member = AsyncMock(side_effect=_fetch)
    guild.get_role = MagicMock(return_value=_SIGNED_UP)
    return guild


def _bot(db_path):
    bot = MagicMock()
    bot.db_path = db_path
    bot.placement_service = MagicMock()
    bot.placement_service.revoke_all_placement_roles = AsyncMock()
    bot.placement_service._revoke_roles = AsyncMock()
    return bot


def _revoked_placement(bot) -> list[int]:
    return sorted(c.args[1] for c in bot.placement_service.revoke_all_placement_roles.await_args_list)


async def test_every_driver_loses_their_placement_roles(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild(members), bot)

    assert _revoked_placement(bot) == [31, 32]
    assert all(c.args[2] == SEASON_ID for c in bot.placement_service.revoke_all_placement_roles.await_args_list)


async def test_the_signed_up_role_is_taken_back_too(tmp_path):
    """Otherwise a league enters its next signup window with half its server still marked
    as signed up."""
    db_path = await _make_db(tmp_path, name="revoke_signedup")
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild(members), bot)

    assert bot.placement_service._revoke_roles.await_count == 2
    assert all(c.args[1] == SIGNED_UP_ROLE for c in bot.placement_service._revoke_roles.await_args_list)


async def test_a_driver_without_the_signed_up_role_is_not_asked_to_lose_it(tmp_path):
    db_path = await _make_db(tmp_path, name="revoke_norole")
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102, has_signed_up=False)}

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild(members), bot)

    assert bot.placement_service._revoke_roles.await_count == 1


async def test_a_league_with_no_signed_up_role_revokes_placement_only(tmp_path):
    db_path = await _make_db(tmp_path, name="revoke_noconfig", signed_up_role=None)
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild(members), bot)

    assert _revoked_placement(bot) == [31, 32]
    bot.placement_service._revoke_roles.assert_not_awaited()


async def test_a_test_driver_is_skipped(tmp_path):
    """No Discord member behind them."""
    db_path = await _make_db(
        tmp_path, name="revoke_test", drivers=[(31, 101, 0, SEASON_ID), (32, 900000001, 1, SEASON_ID)]
    )
    bot = _bot(db_path)

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild({101: _member(101)}), bot)

    assert _revoked_placement(bot) == [31]


async def test_a_member_missing_from_the_cache_is_fetched(tmp_path):
    db_path = await _make_db(tmp_path, name="revoke_fetch")
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}
    guild = _guild(members, missing={102})

    await _revoke_season_roles(SERVER_ID, SEASON_ID, guild, bot)

    guild.fetch_member.assert_awaited_once_with(102)
    assert _revoked_placement(bot) == [31, 32]


async def test_a_driver_who_has_left_the_server_is_stepped_over(tmp_path):
    """The roles went with them, and one departed member must not stop the rest."""
    db_path = await _make_db(tmp_path, name="revoke_left")
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}
    guild = _guild(members, missing={101}, fetch_fails={101})

    await _revoke_season_roles(SERVER_ID, SEASON_ID, guild, bot)

    assert _revoked_placement(bot) == [32]


async def test_another_seasons_drivers_are_left_alone(tmp_path):
    """A driver placed only last season keeps whatever the current season gave them."""
    db_path = await _make_db(
        tmp_path, name="revoke_other", drivers=[(31, 101, 0, SEASON_ID), (32, 102, 0, OTHER_SEASON_ID)]
    )
    bot = _bot(db_path)
    members = {101: _member(101), 102: _member(102)}

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild(members), bot)

    assert _revoked_placement(bot) == [31]


async def test_a_season_with_no_drivers_revokes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="revoke_empty", drivers=[])
    bot = _bot(db_path)

    await _revoke_season_roles(SERVER_ID, SEASON_ID, _guild({}), bot)

    bot.placement_service.revoke_all_placement_roles.assert_not_awaited()
