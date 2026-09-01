"""Obtaining a driver's portrait from the server profile picture of their Discord account.

The lineup already keys a portrait on the driver's Discord user ID and looks for it in the
configured driver image directory (see the image module specification, "Lineup image
generation"). This service is what puts a file there.

Four engineering decisions live here rather than in a specification, because a league
experiences none of them:

**The file written is an SVG wrapping the PNG as a base64 data URI**, not a `.png`.
`asset_resolver.ASSET_EXTENSION` is deliberately single-valued -- resolution is one computed
name and one existence test, and admitting a second extension would make a missing file look
like a present one. Wrapping keeps every one of those guarantees: the resolver finds an
ordinary `.svg`, `svg_fill` gives it a `file://` URI as it does any other asset, and
`_unreachable_links` is satisfied because the outer file genuinely exists. The payload is
base64 rather than a relative link because the rasteriser reads the filled drawing from a
directory of its own, where a relative href resolves to nothing and is drawn as nothing.

**The avatar hash decides whether to download.** `Member.display_avatar.key` is carried on the
Member object the lineup already fetches for its display names, so comparing it costs no HTTP
whatever. A render that changes nothing downloads nothing.

**`driver_portraits` is the ownership register.** A portrait file with no row was placed by
the league itself; the bot never overwrites it and never fetches over it. This is what makes
writing into `resources/league/drivers/` safe, that directory being the league's own.

**A failure never reaches the render.** `Asset.read` goes through `HTTPClient.get_from_cdn`,
which issues a bare session GET rather than passing through `HTTPClient.request` -- so it
consumes no REST rate-limit budget, but equally gets none of discord.py's 429 handling: a 429
raises a bare `HTTPException` with no backoff and no retry. Any `HTTPException` therefore
abandons the rest of the batch rather than hammering the queue behind it, and writes no row,
so the next occasion tries again. Pinned by
`test_driver_portrait_service.py::test_an_http_error_abandons_the_rest_of_the_batch`.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import discord

from db.database import get_connection
from utils.asset_resolver import filename_for

log = logging.getLogger(__name__)

#: Discord serves a square avatar; 128 is the smallest size that still reads well in a 44px
#: lineup slot on a 2x display, and keeps a forty-driver division under a megabyte.
PORTRAIT_SIZE = 128

#: How many avatars are fetched at once. The CDN would tolerate more, but the bot runs on a
#: Raspberry Pi and there is nothing to be gained by opening forty sockets to save a second.
DEFAULT_CONCURRENCY = 8

#: How long a *render* will wait for portraits before drawing with what it has. A cold
#: division is not meant to complete in one render: what did not arrive is fetched on the
#: next, and the division converges over two or three postings. The daily job passes None,
#: nothing waiting on it.
DEFAULT_RENDER_BUDGET_SECONDS = 2.0

#: Distinguishes "the caller did not resolve a directory" from "the caller resolved one and
#: it was rejected". The two must not be conflated: a rejected directory reaches
#: `refresh_before_render` as None, and re-resolving it there would obtain portraits into the
#: very directory the render had just refused.
_UNSET = object()


def wrap_png(data: bytes) -> str:
    """The SVG that carries *data* as a driver portrait.

    Authored at 1:1 with `preserveAspectRatio="xMidYMid meet"`, as the driver class requires
    of every file in it. No `clipPath` and no filter, per the authoring rules in
    `resources/README.md`: the wrapper is generated, but it is not exempt from them.
    """
    payload = base64.b64encode(data).decode("ascii")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {PORTRAIT_SIZE} {PORTRAIT_SIZE}" '
        f'width="{PORTRAIT_SIZE}" height="{PORTRAIT_SIZE}">\n'
        f'  <image width="{PORTRAIT_SIZE}" height="{PORTRAIT_SIZE}" '
        'preserveAspectRatio="xMidYMid meet" '
        f'xlink:href="data:image/png;base64,{payload}" '
        f'href="data:image/png;base64,{payload}"/>\n'
        "</svg>\n"
    )


def portrait_path(directory: Path, discord_user_id: str) -> Path:
    """Where the portrait for *discord_user_id* lives.

    Named through `filename_for` rather than by formatting a string, so the name this service
    writes can never drift from the name `resolve_asset` looks for.
    """
    return Path(directory) / filename_for(str(discord_user_id))


def has_own_avatar(member) -> bool:
    """Whether *member* presents a picture of their own, rather than Discord's generated one.

    A generated avatar stands for the absence of a portrait, not for a portrait, so nothing is
    obtained for such a driver and their seat draws the class fallback exactly as an
    unsupplied portrait does.
    """
    return getattr(member, "avatar", None) is not None or (
        getattr(member, "guild_avatar", None) is not None
    )


def _write_atomically(path: Path, text: str) -> None:
    """Write *text* to *path* without ever leaving a half-written portrait behind.

    A render reading the directory concurrently sees either the old file or the new one. The
    temporary lands in the same directory so that `os.replace` is a rename within one
    filesystem, which is the only form of it that is atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.partial")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


async def _load_owned(db_path: str, server_id: int) -> dict[str, str]:
    """The portraits this bot owns for *server_id*, as user id -> avatar key."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, avatar_key FROM driver_portraits WHERE server_id = ?",
            (server_id,),
        )
        rows = await cursor.fetchall()
    return {str(row["discord_user_id"]): row["avatar_key"] for row in rows}


async def _record(db_path: str, server_id: int, user_id: str, key: str, now) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_portraits (server_id, discord_user_id, avatar_key, "
            "fetched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(server_id, discord_user_id) DO UPDATE SET "
            "avatar_key = excluded.avatar_key, fetched_at = excluded.fetched_at",
            (server_id, str(user_id), key, now.isoformat()),
        )
        await db.commit()


async def _disown(db_path: str, server_id: int, user_id: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM driver_portraits WHERE server_id = ? AND discord_user_id = ?",
            (server_id, str(user_id)),
        )
        await db.commit()


async def refresh_before_render(
    bot,
    server_id: int,
    members,
    *,
    config=None,
    directory=_UNSET,
    now: datetime | None = None,
) -> int:
    """Refresh the portraits a graphic is about to draw, where the league asked for that.

    The gate for the pre-render trigger, in one place: both the posting path and the preview
    path call this rather than each deciding for itself whether the feature is on.

    *config* and *directory* are accepted because the posting path has already read and
    resolved both, and resolving twice per render would be waste; either may be omitted and
    is then worked out here.

    Passing ``directory=None`` is **not** the same as omitting it. None means the caller
    resolved the driver directory and it was rejected, and the gate shuts: obtaining
    portraits into a directory the render has just refused would be worse than obtaining
    none. Omitting the argument entirely means the caller has not looked, and it is resolved
    here.

    Returns the number of portraits written, and never raises.
    """
    if config is None:
        config = await bot.image_config_service.get_config(server_id)
    # `getattr` rather than attribute access: this function promises never to raise, and a
    # configuration object predating migration 047 carries neither field. Absent reads as
    # off, which is the same answer the defaults give.
    if config is None or not getattr(config, "use_pfp", False):
        return 0
    if not getattr(config, "pfp_prerender", False):
        return 0
    if not members:
        return 0

    if directory is _UNSET:
        from services.image_render_service import resolve_configured_directories

        directories, _faults = resolve_configured_directories(
            config,
            (("driver", "driver_image_directory"),),
            image_type="driver_portraits",
        )
        directory = directories.get("driver")
    if directory is None:
        # The configured directory was rejected. The render reports that fault itself, in
        # the terms a manager can act on; obtaining portraits into it is not this module's
        # problem to solve twice.
        return 0

    return await refresh_portraits(bot.db_path, server_id, members, directory, now=now)


async def refresh_portraits(
    db_path: str,
    server_id: int,
    members,
    directory,
    *,
    budget_seconds: float | None = DEFAULT_RENDER_BUDGET_SECONDS,
    concurrency: int = DEFAULT_CONCURRENCY,
    now: datetime | None = None,
) -> int:
    """Bring the portraits of *members* up to date. Returns how many were written.

    Never raises for anything a portrait can do: the caller is drawing a graphic, and a
    portrait that cannot be obtained resolves exactly as it would have resolved had this
    never run.
    """
    directory = Path(directory)
    now = now or datetime.now(timezone.utc)
    owned = await _load_owned(db_path, server_id)

    stale: list = []
    for member in members:
        user_id = str(member.id)
        path = portrait_path(directory, user_id)

        if not has_own_avatar(member):
            # Removing an avatar reverts the seat to the placeholder, but only where the file
            # is ours to remove.
            if user_id in owned:
                path.unlink(missing_ok=True)
                await _disown(db_path, server_id, user_id)
            continue

        if user_id not in owned and path.exists():
            continue  # the league drew this one themselves

        if owned.get(user_id) == member.display_avatar.key and path.is_file():
            continue  # unchanged since it was obtained

        stale.append(member)

    if not stale:
        return 0

    semaphore = asyncio.Semaphore(max(1, concurrency))
    abort = asyncio.Event()
    written = 0

    async def fetch(member) -> None:
        nonlocal written
        if abort.is_set():
            return
        async with semaphore:
            if abort.is_set():
                return
            asset = member.display_avatar
            try:
                data = await asset.with_format("png").with_size(PORTRAIT_SIZE).read()
            except discord.HTTPException as exc:
                # See the module docstring: there is no 429 handling beneath us, so stop
                # rather than send the rest of the queue into the same wall.
                abort.set()
                log.warning(
                    "driver portraits: abandoning the batch for server %s after %s",
                    server_id,
                    exc,
                )
                return
            except Exception:  # noqa: BLE001 -- a portrait never fails a render
                log.warning(
                    "driver portraits: could not obtain one for %s", member.id, exc_info=True
                )
                return

            try:
                _write_atomically(portrait_path(directory, str(member.id)), wrap_png(data))
            except OSError:
                log.warning(
                    "driver portraits: could not write one for %s", member.id, exc_info=True
                )
                return

            await _record(db_path, server_id, str(member.id), asset.key, now)
            written += 1

    tasks = [asyncio.create_task(fetch(member)) for member in stale]
    done, pending = await asyncio.wait(tasks, timeout=budget_seconds)
    for task in pending:
        # Out of budget rather than in error: what did not arrive is fetched next time.
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        log.info(
            "driver portraits: %s of %s obtained for server %s within the budget",
            written,
            len(stale),
            server_id,
        )
    return written
