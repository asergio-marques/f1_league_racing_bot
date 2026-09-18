"""Reposting a division's lineup, and resolving a division a manager named.

Issue #208. `PlacementService._refresh_lineup_post` and `resolve_division` were uncovered.
The lineup is what a division reads to know who is racing, and it is reposted after every
placement change — a seat filled, a driver sacked, a team rearranged.

**The image path is a guard clause in front of an untouched body.** Where the image module is
on, the `lineup` aspect is enabled and a template is configured, `image_lineup_post.try_post`
produces the graphic and this method returns. Where any of those is not true — and only then —
the text embed below runs exactly as it did before the image module existed, **delete-then-build
order included** (FR-025a). That order was specified in `specs/028-season-signup-flow/` and is
deliberately not reopened: the lineup image is an alternative output beside the text, not a
reform of it.

**A failure in the image path falls through to the text.** A placement must never be blocked by
a graphic that would not render — the seat is already assigned by the time this runs, and a
division with no lineup post at all is worse than one with a text lineup.

**Test drivers are labelled with the name the rehearsal gave them.** A mention of a user id
nobody holds renders as a raw id; the display name beside it is what makes a rehearsal's lineup
readable at all.

**Reserves are listed after a separator, not mixed in.** A reserve is in the division and not in
a team in the ordinary sense, and a lineup that reads them as a fifth constructor misrepresents
what a league is looking at.

**An empty division posts an empty lineup rather than nothing.** A division with no drivers yet
is a normal state during signups, and the post is what tells a league that the lineup exists and
is empty — silence reads as the bot not working.

**`resolve_division` takes a tier number or a name, and tries the number first.** A league that
calls its divisions "1" and "2" is naming tiers; a league that calls one "Pro" is naming names.
Matching is exact here, unlike the cog-level lookups — worth pinning, because the inconsistency
is real and a reader will assume otherwise.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 12308
SEASON_ID = 1
DIVISION_ID = 11
LINEUP_CHANNEL = 700
OLD_MESSAGE_ID = 8800
NEW_MESSAGE_ID = 9900


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "lineup",
    channel: int | None = LINEUP_CHANNEL,
    message_id: int | None = OLD_MESSAGE_ID,
    drivers=(),
) -> str:
    """*drivers* are ``(team, is_reserve, discord_user_id, is_test, display_name)``."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, "
            "lineup_channel_id, lineup_message_id) VALUES (?, ?, 'Pro', 1, 555, ?, ?)",
            (DIVISION_ID, SEASON_ID, channel, message_id),
        )
        teams: dict[str, int] = {}
        profile_id = 100
        for team, is_reserve, user_id, is_test, display in drivers:
            if team not in teams:
                cursor = await db.execute(
                    "INSERT INTO team_instances (division_id, name, is_reserve) "
                    "VALUES (?, ?, ?)",
                    (DIVISION_ID, team, int(is_reserve)),
                )
                teams[team] = cursor.lastrowid
            profile_id += 1
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, "
                "current_state, is_test_driver, test_display_name) "
                "VALUES (?, ?, 'ASSIGNED', ?, ?)",
                (profile_id, str(user_id), int(is_test), display),
            )
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (teams[team], profile_id, profile_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id) VALUES (?, ?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID, cursor.lastrowid),
            )
        await db.commit()
    return db_path


def _channel(*, fetch_fails: bool = False, send_fails: bool = False):
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = LINEUP_CHANNEL
    new = MagicMock()
    new.id = NEW_MESSAGE_ID
    channel.send = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=500), "no")
        if send_fails
        else None,
        return_value=new,
    )
    if fetch_fails:
        channel.fetch_message = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
    else:
        old = MagicMock()
        old.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=old)
        channel._old = old
    return channel


def _guild(*, channel=None, cache_miss: bool = False, fetch_fails: bool = False):
    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=None if cache_miss else channel)
    if fetch_fails:
        guild.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
    else:
        guild.fetch_channel = AsyncMock(return_value=channel)
    return guild


async def _refresh(db_path, guild, *, image_applicable: bool = False, image_error=None):
    service = PlacementService(db_path, bot=MagicMock())
    outcome = SimpleNamespace(applicable=image_applicable)
    with patch(
        "services.image_lineup_post.try_post",
        new=AsyncMock(return_value=outcome, side_effect=image_error),
    ) as try_post:
        await service._refresh_lineup_post(guild, DIVISION_ID)
    return try_post


def _embed(channel):
    return channel.send.await_args.kwargs["embed"]


async def _stored_message_id(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT lineup_message_id FROM divisions WHERE id = ?", (DIVISION_ID,)
        )
        return (await cursor.fetchone())["lineup_message_id"]


async def _audit(db_path) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, new_value, actor_name FROM audit_entries "
            "WHERE server_id = ?",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


ONE_DRIVER = (("Red", False, 4242, False, None),)


# ---------------------------------------------------------------------------
# The image path in front
# ---------------------------------------------------------------------------


async def test_a_configured_graphic_replaces_the_text_lineup(tmp_path):
    """The image path produces the PNG before deleting the message it replaces (FR-025),
    and returns — the text body below is not reached at all."""
    db_path = await _make_db(tmp_path, name="lineup_image", drivers=ONE_DRIVER)
    channel = _channel()

    try_post = await _refresh(db_path, _guild(channel=channel), image_applicable=True)

    try_post.assert_awaited_once()
    channel.send.assert_not_awaited()


async def test_an_inapplicable_graphic_falls_through_to_the_text(tmp_path):
    """The module off, the aspect off, or no template — the embed runs exactly as it did
    before the image module existed."""
    db_path = await _make_db(tmp_path, name="lineup_fallthrough", drivers=ONE_DRIVER)
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel), image_applicable=False)

    channel.send.assert_awaited_once()


async def test_a_failing_graphic_falls_through_to_the_text(tmp_path):
    """A placement must never be blocked by a graphic that would not render: the seat is
    already assigned by the time this runs, and no lineup at all is worse than a text one."""
    db_path = await _make_db(tmp_path, name="lineup_imgfail", drivers=ONE_DRIVER)
    channel = _channel()

    await _refresh(
        db_path, _guild(channel=channel), image_error=RuntimeError("no rasteriser")
    )

    channel.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# The text lineup
# ---------------------------------------------------------------------------


async def test_the_lineup_lists_each_team_and_its_drivers(tmp_path):
    db_path = await _make_db(
        tmp_path,
        name="lineup_teams",
        drivers=(
            ("Red", False, 101, False, None),
            ("Red", False, 102, False, None),
            ("Blue", False, 201, False, None),
        ),
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    description = _embed(channel).description
    assert "**Red**: <@101>, <@102>" in description
    assert "**Blue**: <@201>" in description


async def test_the_lineup_is_titled_with_the_divisions_name(tmp_path):
    """A league with four divisions posts four of these, and they are told apart by nothing
    else."""
    db_path = await _make_db(tmp_path, name="lineup_title", drivers=ONE_DRIVER)
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    assert "Pro" in _embed(channel).title


async def test_reserves_are_listed_after_a_separator(tmp_path):
    """A reserve is in the division and not in a team in the ordinary sense; a lineup that
    reads them as a fifth constructor misrepresents what a league is looking at."""
    db_path = await _make_db(
        tmp_path,
        name="lineup_reserves",
        drivers=(
            ("Red", False, 101, False, None),
            ("Reserves", True, 301, False, None),
        ),
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    description = _embed(channel).description
    assert description.index("**Red**") < description.index("---")
    assert description.index("---") < description.index("**Reserves**")


async def test_a_division_of_reserves_alone_needs_no_separator(tmp_path):
    """A leading "---" would read as a section with nothing above it."""
    db_path = await _make_db(
        tmp_path,
        name="lineup_reserves_only",
        drivers=(("Reserves", True, 301, False, None),),
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    assert not _embed(channel).description.startswith("---")


async def test_a_test_driver_is_labelled_with_their_rehearsal_name(tmp_path):
    """A mention of a user id nobody holds renders as a raw id; the name beside it is what
    makes a rehearsal's lineup readable at all."""
    db_path = await _make_db(
        tmp_path,
        name="lineup_testdriver",
        drivers=(("Red", False, 900000001, True, "Mock Hamilton"),),
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    assert "(Mock Hamilton)" in _embed(channel).description


async def test_a_test_driver_with_no_name_is_mentioned_plainly(tmp_path):
    """An empty bracket after a mention is worse than no bracket."""
    db_path = await _make_db(
        tmp_path,
        name="lineup_testnoname",
        drivers=(("Red", False, 900000001, True, None),),
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    assert "()" not in _embed(channel).description


async def test_an_empty_division_posts_an_empty_lineup(tmp_path):
    """A division with no drivers yet is normal during signups, and the post is what tells
    a league the lineup exists and is empty — silence reads as the bot not working."""
    db_path = await _make_db(tmp_path, name="lineup_empty", drivers=())
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    assert "no drivers assigned" in _embed(channel).description


# ---------------------------------------------------------------------------
# Replacing the old post
# ---------------------------------------------------------------------------


async def test_the_previous_lineup_is_deleted(tmp_path):
    """Not edited, and deleted *before* the new one is built — the order specified in
    028-season-signup-flow and deliberately not reopened by the image feature."""
    db_path = await _make_db(tmp_path, name="lineup_delete", drivers=ONE_DRIVER)
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    channel.fetch_message.assert_awaited_once_with(OLD_MESSAGE_ID)
    channel._old.delete.assert_awaited_once()


async def test_a_division_with_no_previous_post_deletes_nothing(tmp_path):
    """The first lineup of a season; asking Discord for message `None` would raise."""
    db_path = await _make_db(
        tmp_path, name="lineup_first", message_id=None, drivers=ONE_DRIVER
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    channel.fetch_message.assert_not_awaited()
    channel.send.assert_awaited_once()


async def test_a_previous_post_already_gone_does_not_stop_the_new_one(tmp_path):
    """Deleted by hand, or with a channel purge — the division still needs its lineup."""
    db_path = await _make_db(tmp_path, name="lineup_msggone", drivers=ONE_DRIVER)
    channel = _channel(fetch_fails=True)

    await _refresh(db_path, _guild(channel=channel))

    channel.send.assert_awaited_once()


async def test_the_new_message_id_is_stored(tmp_path):
    """The next refresh deletes by it; without it the channel accumulates one lineup per
    placement change."""
    db_path = await _make_db(tmp_path, name="lineup_store", drivers=ONE_DRIVER)

    await _refresh(db_path, _guild(channel=_channel()))

    assert await _stored_message_id(db_path) == NEW_MESSAGE_ID


async def test_the_post_is_audited(tmp_path):
    """Attributed to the system rather than a person: the lineup is reposted by a placement
    change, not by someone running a command."""
    db_path = await _make_db(tmp_path, name="lineup_audit", drivers=ONE_DRIVER)

    await _refresh(db_path, _guild(channel=_channel()))

    rows = await _audit(db_path)
    assert [r["change_type"] for r in rows] == ["SIGNUP_LINEUP_POSTED"]
    assert rows[0]["actor_name"] == "system"
    assert json.loads(rows[0]["new_value"])["division"] == "Pro"


async def test_a_failed_post_stores_no_message_id(tmp_path):
    """Storing one would have the next refresh try to delete a message that was never
    sent, and then post as though the first had succeeded."""
    db_path = await _make_db(tmp_path, name="lineup_sendfail", drivers=ONE_DRIVER)

    await _refresh(db_path, _guild(channel=_channel(send_fails=True)))

    assert await _stored_message_id(db_path) == OLD_MESSAGE_ID
    assert await _audit(db_path) == []


# ---------------------------------------------------------------------------
# When there is nowhere to post
# ---------------------------------------------------------------------------


async def test_a_division_with_no_lineup_channel_posts_nothing(tmp_path):
    """Configuring one is optional, and a placement must not fail for want of it."""
    db_path = await _make_db(
        tmp_path, name="lineup_nochannel", channel=None, drivers=ONE_DRIVER
    )
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    channel.send.assert_not_awaited()


async def test_a_division_that_does_not_exist_posts_nothing(tmp_path):
    """Called from a cascade that may be removing the division itself."""
    db_path = await _make_db(tmp_path, name="lineup_nodiv", drivers=())
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM divisions WHERE id = ?", (DIVISION_ID,))
        await db.commit()
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel))

    channel.send.assert_not_awaited()


async def test_a_channel_missing_from_the_cache_is_fetched(tmp_path):
    """A channel the bot has not seen this session is ordinary, and treating a cache miss
    as a deleted channel would silently stop posting a division's lineup."""
    db_path = await _make_db(tmp_path, name="lineup_cachemiss", drivers=ONE_DRIVER)
    channel = _channel()
    guild = _guild(channel=channel, cache_miss=True)

    await _refresh(db_path, guild)

    guild.fetch_channel.assert_awaited_once()
    channel.send.assert_awaited_once()


async def test_a_channel_that_cannot_be_fetched_posts_nothing(tmp_path):
    """Genuinely deleted. The placement has already happened and must not be undone for
    it."""
    db_path = await _make_db(tmp_path, name="lineup_chgone", drivers=ONE_DRIVER)
    channel = _channel()

    await _refresh(db_path, _guild(channel=channel, cache_miss=True, fetch_fails=True))

    channel.send.assert_not_awaited()


async def test_a_lineup_channel_that_is_not_a_text_channel_posts_nothing(tmp_path):
    """A voice or forum channel configured by mistake; `send` on one would raise or behave
    differently."""
    db_path = await _make_db(tmp_path, name="lineup_notext", drivers=ONE_DRIVER)
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.send = AsyncMock()

    await _refresh(db_path, _guild(channel=channel))

    channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Resolving a division a manager named
# ---------------------------------------------------------------------------


async def test_a_division_resolves_by_tier_number(tmp_path):
    """A league that calls its divisions "1" and "2" is naming tiers."""
    db_path = await _make_db(tmp_path, name="resolve_tier")

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "1") == (
        DIVISION_ID,
        "Pro",
    )


async def test_a_division_resolves_by_name(tmp_path):
    db_path = await _make_db(tmp_path, name="resolve_name")

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "Pro") == (
        DIVISION_ID,
        "Pro",
    )


async def test_the_number_is_tried_first(tmp_path):
    """A division literally named "2" and a tier 2 in the same season would be ambiguous;
    the tier wins, which is what a manager typing a bare number means."""
    db_path = await _make_db(tmp_path, name="resolve_ambiguous")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (12, ?, '1', 2, 556)",
            (SEASON_ID,),
        )
        await db.commit()

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "1") == (
        DIVISION_ID,
        "Pro",
    )


async def test_an_unknown_division_resolves_to_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="resolve_unknown")

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "Rookie") is None


async def test_a_tier_no_division_holds_resolves_to_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="resolve_notier")

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "9") is None


async def test_a_name_is_matched_exactly(tmp_path):
    """Unlike the cog-level lookups, which are case-insensitive. Pinned because the
    inconsistency is real and a reader will assume otherwise."""
    db_path = await _make_db(tmp_path, name="resolve_case")

    assert await PlacementService(db_path).resolve_division(SEASON_ID, "pro") is None


async def test_another_seasons_division_is_not_resolved(tmp_path):
    """Divisions are per season, and a tier number repeats in every one of them."""
    db_path = await _make_db(tmp_path, name="resolve_season")

    assert await PlacementService(db_path).resolve_division(999, "1") is None
