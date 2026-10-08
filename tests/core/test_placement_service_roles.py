"""A driver's roles granted, and a lineup posted as text, as jobs on the change queue (#439, slice 4a).

Approving a season grants each placed driver their division's and team's roles as a job of its
own, and posts each division's lineup as another. A job stops the queue where it fails, so the
grant raises where today's `_grant_roles` logs and carries on: a role no longer on the server, or
Discord refusing it. Only a member Discord reports absent is passed over (README, "A driver who has
left the server is simply passed over"). A lineup tried again after a failure goes as text
(Constitution XIV rule 8), drawing nothing.

The functions are reached inside each test, so this file collects while they are unbuilt.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.services.placement_service import PlacementService

USER_ID = 101
DIVISION_ROLE = 801
TEAM_ROLE = 811
DIVISION_ID = 11


def _response(status: int, reason: str) -> MagicMock:
    return MagicMock(status=status, reason=reason)


def _guild(*, roles=(DIVISION_ROLE, TEAM_ROLE), member=None, fetch_error=None):
    """A server holding *roles*, whose member 101 is *member* (fetched), or *fetch_error*."""
    guild = MagicMock()
    guild.id = 10439
    known = {role_id: MagicMock(id=role_id, name=f"role {role_id}") for role_id in roles}
    guild.get_role = MagicMock(side_effect=known.get)
    if member is None:
        member = MagicMock(id=USER_ID, guild=guild)
        member.add_roles = AsyncMock()
    if fetch_error is not None:
        guild.fetch_member = AsyncMock(side_effect=fetch_error)
    else:
        guild.fetch_member = AsyncMock(return_value=member)
    return guild, member, known


def _granted(member) -> set[int]:
    return {role.id for call in member.add_roles.await_args_list for role in call.args}


async def test_both_roles_are_granted(tmp_path):
    """Lewis (101) is fetched and given Pro's role (801) and Ferrari's (811)."""
    guild, member, _ = _guild()

    granted = await PlacementService(str(tmp_path / "db.sqlite")).grant_roles(
        guild, USER_ID, DIVISION_ROLE, TEAM_ROLE
    )

    assert granted is True
    guild.fetch_member.assert_awaited_once_with(USER_ID)
    assert _granted(member) == {DIVISION_ROLE, TEAM_ROLE}


async def test_a_role_not_on_the_server_raises(tmp_path):
    """Ferrari's role (811) has been deleted from the server: the grant raises for the queue to
    stop on, rather than giving Lewis Pro's role alone and saying nothing."""
    guild, _, _ = _guild(roles=(DIVISION_ROLE,))

    with pytest.raises(StepFailedOnDiscord):
        await PlacementService(str(tmp_path / "db.sqlite")).grant_roles(
            guild, USER_ID, DIVISION_ROLE, TEAM_ROLE
        )


async def test_a_role_discord_refuses_raises(tmp_path):
    """Discord refuses to give Lewis a role (the bot's own role sits below it): the grant raises."""
    guild, member, _ = _guild()
    member.add_roles = AsyncMock(
        side_effect=discord.Forbidden(_response(403, "Forbidden"), "Missing Permissions")
    )

    with pytest.raises(StepFailedOnDiscord):
        await PlacementService(str(tmp_path / "db.sqlite")).grant_roles(
            guild, USER_ID, DIVISION_ROLE, TEAM_ROLE
        )


async def test_a_member_discord_reports_absent_is_passed_over(tmp_path):
    """Lewis has left the server, Discord answering Not Found: the grant returns False, gives
    nothing and raises nothing, so the job is done."""
    guild, member, _ = _guild(
        fetch_error=discord.NotFound(_response(404, "Not Found"), "Unknown Member")
    )

    granted = await PlacementService(str(tmp_path / "db.sqlite")).grant_roles(
        guild, USER_ID, DIVISION_ROLE, TEAM_ROLE
    )

    assert granted is False
    member.add_roles.assert_not_awaited()


async def _lineup_db(tmp_path) -> str:
    """Season 3 in Placements, with Pro given lineup channel 600."""
    db_path = os.path.join(str(tmp_path), "lineup.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (10439, 900, 100, 101)"
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) "
            "VALUES (7, 3, '2026-11-01', 'SETUP', 'PLACEMENTS')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status, "
            "lineup_channel_id) VALUES (?, 7, 'Pro', 1, 801, 'SETUP', 600)",
            (DIVISION_ID,),
        )
        await db.commit()
    return db_path


async def test_a_lineup_refreshed_as_text_draws_nothing(tmp_path, monkeypatch):
    """Pro's lineup tried again after a failure, the bot able to draw it: the picture is not
    attempted, and the textual lineup is posted in channel 600."""
    import leaguebot.image.services.image_lineup_post as image_lineup_post

    try_post = AsyncMock(return_value=MagicMock(applicable=True))
    monkeypatch.setattr(image_lineup_post, "try_post", try_post)
    db_path = await _lineup_db(tmp_path)
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=MagicMock(id=700))
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=channel)

    await PlacementService(db_path, bot=MagicMock()).refresh_lineup(
        guild, DIVISION_ID, as_text=True
    )

    try_post.assert_not_awaited()
    channel.send.assert_awaited_once()
    assert "Pro Lineup" in channel.send.await_args.kwargs["embed"].title
