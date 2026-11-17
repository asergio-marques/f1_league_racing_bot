"""The channels a division's check-in call and attendance sheet are posted in.

`/attendance channel rsvp` and `/attendance channel attendance` set them, each in its own column of
`attendance_division_config`, which the attendance module owns
(`attendance/services/settings/channel_settings.py` holds the settings that call these). Two separate
upserts naming one column each, so that setting one never blanks its neighbour.

**Each writes on the connection it is handed and never commits**: the setting's save owns the
transaction, so the channel and its audit entry stand or fall together. Each gives back the channel
it replaced, and **writes nothing where the channel is already the one held**, so that a save can
tell that nothing changed. Both columns are TEXT; each stores the id as text and gives its previous
value back as an integer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.framework.db.database import get_connection
from leaguebot.core.models.channel_needs import ATTACH, EMBED, READ_HISTORY, SEND, VIEW, Needs
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    DiscordTargets,
    channel_ids_in,
)

if TYPE_CHECKING:
    from leaguebot.image.services.render.image_drawing import ImageDrawing


async def set_rsvp_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert attendance_division_config.rsvp_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT rsvp_channel_id FROM attendance_division_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET rsvp_channel_id = excluded.rsvp_channel_id",
            (division_id, None if channel_id is None else str(channel_id)),
        )
    return old_id


async def set_attendance_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert attendance_division_config.attendance_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT attendance_channel_id FROM attendance_division_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET "
            "attendance_channel_id = excluded.attendance_channel_id",
            (division_id, None if channel_id is None else str(channel_id)),
        )
    return old_id


#: The settings of the check-in call and attendance channels, by the column that holds each, in
#: the order a channel is looked for: what core's `channel_uses_read` names each.
_SETTINGS = (
    ("rsvp_channel_id", "rsvp"),
    ("attendance_channel_id", "attendance"),
)


async def channel_uses(
    db_path: str, *, channel_id: int, divisions: Mapping[int, str]
) -> list[ChannelUse]:
    """The check-in call and attendance channels of those of *divisions* (the live season's, by
    id, naming each) that are *channel_id*, each as the use of it, naming its division, in that
    order; empty where none is.

    Subscribed to core's `channel_uses_read`, which asks it as a channel is set, so that a
    channel does one job. Asked no switch: a channel attendance holds is held whether attendance
    is on or off. Reads only.
    """
    uses: list[ChannelUse] = []
    async with get_connection(db_path) as db:
        for column, setting in _SETTINGS:
            cursor = await db.execute(
                f"SELECT division_id FROM attendance_division_config WHERE {column} = ? "
                f"ORDER BY division_id",
                (channel_id,),
            )
            uses += [
                ChannelUse(setting, divisions[int(row[0])])
                for row in await cursor.fetchall()
                if int(row[0]) in divisions
            ]
    return uses


async def setting_channels(db_path: str, *, division_ids: Sequence[int]) -> DiscordTargets:
    """Every channel a division's check-in call or attendance channel names, in any season, and
    every channel a check-in call was posted in; the module creates none.

    Subscribed to core's `setting_channels_read`, which the factory reset asks for the channels
    it cleans up. The divisions' ids are handed, as they are every subscriber, and not used. Reads
    only.
    """
    found: set[int] = set()
    async with get_connection(db_path) as db:
        for column, _setting in _SETTINGS:
            found |= await channel_ids_in(
                db,
                f"SELECT {column} FROM attendance_division_config WHERE {column} IS NOT NULL",
                f"attendance_division_config.{column}",
            )
        found |= await channel_ids_in(
            db,
            "SELECT channel_id FROM rsvp_embed_messages WHERE channel_id IS NOT NULL",
            "rsvp_embed_messages.channel_id",
        )
    return DiscordTargets(created=(), channels=tuple(sorted(found)))


async def channel_needs(drawing: "ImageDrawing | None") -> dict[str, Needs]:
    """What the check-in call's channel, `rsvp`, and the attendance sheet's, `attendance`, need of
    the bot: the one source of it within attendance, read by the faults a season's confirmation
    names and by the gate before a recalculation.

    The check-in call is an embed posted in the channel, so it needs View Channel, Send Messages
    and Embed Links. The sheet is reposted, which finds what the bot posted last and takes it
    down, so it needs View Channel, Send Messages and Read Message History, and no embed: it is
    text or a picture. Each also needs Attach Files where the image module answers that its
    pictures could attach a file (`aspect_attaches_files`, a question it answers by its switches
    and not by the state of a template): `rsvp` for the call, `attendance` for the sheet. With no
    *drawing*, nothing is drawn and no channel needs Attach Files.
    """

    async def attaches(aspect: str) -> Needs:
        if drawing is not None and await drawing.aspect_attaches_files(aspect):
            return (ATTACH,)
        return ()

    return {
        "rsvp": (VIEW, SEND, EMBED) + await attaches("rsvp"),
        "attendance": (VIEW, SEND, READ_HISTORY) + await attaches("attendance"),
    }
