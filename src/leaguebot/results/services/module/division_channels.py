"""The channels a division's results, standings and penalty verdicts are posted in.

`/results channel results`, `standings` and `verdicts` set them, each in its own column of
`division_results_config`, which the results module owns (`results/services/module/channel_settings.py`
holds the settings that call these). Three separate upserts naming one column each, so that setting
one never blanks its neighbour.

**Each writes on the connection it is handed and never commits**: the setting's save owns the
transaction, so the channel and its audit entry stand or fall together. Each gives back the channel
it replaced, and **writes nothing where the channel is already the one held**, so that a save can
tell that nothing changed. `penalty_channel_id` is a TEXT column, the other two INTEGER; each gives
its previous value back as an integer.

The module hands core its channels, too, through two of core's hooks: the lines the placements
review shows for each division (`division_lines`, the review showing them whether the module is on
or off) and the faults a season's confirmation judges them by (`division_channel_faults`, asking
the module's switch of itself).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.results.models.switch import SWITCH_KEY
from leaguebot.framework.db.database import get_connection
from leaguebot.core.models.channel_needs import ATTACH, READ_HISTORY, SEND, VIEW, Needs
from leaguebot.core.services.modules.kept_channels import KeptChannel, KeptReader
from leaguebot.core.services.seasons.approval_checks import channel_fault
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    DiscordTargets,
    channel_ids_in,
)

if TYPE_CHECKING:
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.image.services.render.image_drawing import ImageDrawing

#: Results' three channels, in the order a division's lines read and a channel is looked for: the
#: column that holds it, its setting (what core's `channel_uses_read` names it, and the key of
#: `channel_needs`), what a league calls it, the command that sets it, and what the review shows
#: where it is not set.
_CHANNELS = (
    ("results_channel_id", "results", "results channel", "/results channel results", "*(none)*"),
    ("standings_channel_id", "standings", "standings channel", "/results channel standings",
     "*(none)*"),
    ("penalty_channel_id", "verdicts", "verdicts channel", "/results channel verdicts",
     "*(not configured)*"),
)


async def _channels_of(db_path: str) -> dict[int, Any]:
    """Each division's row of results' channel configuration, by division id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id, results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config"
        )
        return {row["division_id"]: row for row in await cursor.fetchall()}


async def verdicts_channels_on(db: aiosqlite.Connection) -> dict[int, int]:
    """The verdicts channel of each division that has one, by division id, read on *db*; a
    division with none is absent.

    Asked of results by a module that checks a division's channels before posting to them
    (architecture.md, "The database"). It writes nothing and commits nothing.
    """
    cursor = await db.execute(
        "SELECT division_id, penalty_channel_id FROM division_results_config "
        "WHERE penalty_channel_id IS NOT NULL AND penalty_channel_id != ''"
    )
    return {int(row["division_id"]): int(row["penalty_channel_id"]) for row in await cursor.fetchall()}


async def set_division_results_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.results_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id",
            (division_id, channel_id),
        )
    return old_id


async def set_division_standings_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.standings_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, standings_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET standings_channel_id = excluded.standings_channel_id",
            (division_id, channel_id),
        )
    return old_id


async def set_division_penalty_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.penalty_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET penalty_channel_id = excluded.penalty_channel_id",
            (division_id, channel_id),
        )
    return old_id


async def division_lines(
    db_path: str, *, season_id: int, division_ids: Sequence[int]
) -> dict[int, list[str]]:
    """The three channel lines the placements review shows for each division in *division_ids*:
    results, standings, then verdicts, a channel set named and one not set shown as the review
    always showed it. Asked no switch: the review shows them whether results is on or off.

    Subscribed to core's `division_lines_read`, which hands the season (as it does every
    subscriber; the divisions named are what is read) and its divisions. Reads only.
    """
    channels = await _channels_of(db_path)
    lines: dict[int, list[str]] = {}
    for division_id in division_ids:
        row = channels.get(division_id)
        lines[division_id] = [
            f"  {label.capitalize()}: "
            + (f"<#{row[column]}>" if row is not None and row[column] else unset)
            for column, _setting, label, _command, unset in _CHANNELS
        ]
    return lines


async def channel_uses(
    modules: "ModuleService", db_path: str, *, channel_id: int, divisions: Mapping[int, str]
) -> list[ChannelUse]:
    """The results, standings and verdicts channels of those of *divisions* (the live season's, by
    id, naming each) that are *channel_id*, each as the use of it, naming its division, in that
    order; empty where none is, and always where the results module is off.

    Subscribed to core's `channel_uses_read`, which asks it as a channel is set, so that a
    channel does one job. A switched-off module answers for none of its channels, which it keeps:
    another setting may take one, and the module clears it when it is switched on again
    (`core/services/modules/kept_channels.py`). Reads only.
    """
    if not await modules.is_enabled(SWITCH_KEY):
        return []
    uses: list[ChannelUse] = []
    async with get_connection(db_path) as db:
        for column, setting, _label, _command, _unset in _CHANNELS:
            cursor = await db.execute(
                f"SELECT division_id FROM division_results_config WHERE {column} = ? "
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
    """Every channel a division's results, standings or verdicts channel names, in any season,
    and, apart from them, every submission and amendment channel the bot created for a round.

    Subscribed to core's `setting_channels_read`, which the factory reset asks for the channels
    it cleans up. The divisions' ids are handed, as they are every subscriber, and not used. Reads
    only.
    """
    found: set[int] = set()
    async with get_connection(db_path) as db:
        for column, _setting, _label, _command, _unset in _CHANNELS:
            found |= await channel_ids_in(
                db,
                f"SELECT {column} FROM division_results_config WHERE {column} IS NOT NULL",
                f"division_results_config.{column}",
            )
        created = await channel_ids_in(
            db,
            "SELECT channel_id FROM round_submission_channels WHERE channel_id IS NOT NULL",
            "round_submission_channels.channel_id",
        )
        created |= await channel_ids_in(
            db,
            "SELECT channel_id FROM round_amend_channels WHERE channel_id IS NOT NULL",
            "round_amend_channels.channel_id",
        )
    return DiscordTargets(created=tuple(sorted(created)), channels=tuple(sorted(found)))


async def channel_needs(drawing: "ImageDrawing | None") -> dict[str, Needs]:
    """What results' three channels, `results`, `standings` and `verdicts`, need of the bot: the
    one source of it within results, read by the faults a season's confirmation names and by the
    gate before a repost, which attendance's recalculation reads for the verdicts channel too.

    Each is posted in, and reposted: the bot finds what it posted last and takes it down, which
    needs View Channel, Send Messages and Read Message History (`manage_messages` is deliberately
    absent: the bot deletes only its own posts, which needs no permission). Each also needs Attach
    Files where the image module answers that its pictures could attach a file
    (`aspect_attaches_files`, a question the module answers by its switches and not by the state of
    a template): `results` for results, `standings` for standings, and `verdicts` or
    `verdict_banner` for the verdicts channel, where attendance's sanction cards go as well. With
    no *drawing*, nothing is drawn and no channel needs Attach Files.
    """
    reposted: Needs = (VIEW, SEND, READ_HISTORY)

    async def attaches(*aspects: str) -> Needs:
        for aspect in aspects:
            if drawing is not None and await drawing.aspect_attaches_files(aspect):
                return reposted + (ATTACH,)
        return reposted

    return {
        "results": await attaches("results"),
        "standings": await attaches("standings"),
        "verdicts": await attaches("verdicts", "verdict_banner"),
    }


async def kept_channels(drawing: "ImageDrawing | None") -> KeptReader:
    """The reader of the channels results keeps, for the check its switch-on makes
    (`core/services/modules/kept_channels.py`): given a connection and the live season's divisions
    (id to name), it gives each one's results, standings and verdicts channel that is set, in the
    order `_CHANNELS` gives, with what each needs of the bot (`channel_needs`, asked of the
    *drawing* now and not when the switch-on is built, as a picture switched on while results was
    off may attach a file). Reads only."""
    needs = await channel_needs(drawing)

    async def read(db: aiosqlite.Connection, divisions: Mapping[int, str]) -> list[KeptChannel]:
        cursor = await db.execute(
            "SELECT division_id, results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config ORDER BY division_id"
        )
        kept: list[KeptChannel] = []
        for row in await cursor.fetchall():
            if int(row["division_id"]) not in divisions:
                continue
            for column, setting, label, command, _unset in _CHANNELS:
                if row[column] is not None and row[column] != "":
                    kept.append(
                        KeptChannel(
                            setting, int(row["division_id"]), divisions[int(row["division_id"])],
                            int(row[column]), label, command, needs[setting],
                        )
                    )
        return kept

    return read


async def division_channel_faults(
    modules: "ModuleService",
    db_path: str,
    *,
    drawing: "ImageDrawing",
    divisions: Mapping[int, str],
    guild: Any = None,
) -> dict[int, list[str]]:
    """Each division's results, standings and verdicts channels, in that order, that are not set,
    not on the *guild* or lacking a permission their use needs (`channel_needs`, asked of the
    *drawing*), by division id; nothing where the results module is off.

    Subscribed to core's `division_channel_faults_read`, which hands the divisions the
    confirmation judges (id to name) and the server. A division with no fault is given an empty
    list. With no *guild*, only whether each channel is set is judged. Reads only.
    """
    if not await modules.is_enabled(SWITCH_KEY):
        return {}
    channels = await _channels_of(db_path)
    needs = await channel_needs(drawing)
    found: dict[int, list[str]] = {}
    for division_id, name in divisions.items():
        row = channels.get(division_id)
        lines: list[str] = []
        for column, setting, label, command, _unset in _CHANNELS:
            fault = await channel_fault(
                guild, name, row[column] if row is not None else None, label, command,
                needs[setting],
            )
            if fault:
                lines.append(fault)
        found[division_id] = lines
    return found
