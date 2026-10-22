"""ImageConfigService — CRUD over image_config and image_aspect_toggles.

Configuration retention (FR-004a): disabling the module clears only ``module_enabled``.
Nothing in this service deletes a configuration row or resets a toggle, which is the
Principle X.6 exception for configuration that cannot go stale — no value here names a
Discord channel, role, message or scheduled job.
"""
from __future__ import annotations

import dataclasses
from typing import Any
import logging

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import AuditRecord
from leaguebot.core.services.setting_change import Saved
from leaguebot.image.models.image_constants import ASPECTS, ASSET_DIRECTORIES, TEMPLATE_COLUMNS
from leaguebot.image.models.image_module import ImageConfig

log = logging.getLogger(__name__)


#: Columns a slash command may write. Anything outside this set is rejected, so a caller
#: cannot reach `module_enabled` or `id` through the generic setter.
SETTABLE_COLUMNS: frozenset[str] = frozenset(
    {"template_directory"}
    | set(TEMPLATE_COLUMNS)
    | set(ASSET_DIRECTORIES)
    | {"time_zone", "time_format", "date_format", "fastest_lap_colour"}
    | {"pfp_daily_time"}
)

#: The three portrait toggles. Booleans, so they are written through `set_pfp_flag` rather
#: than the string-valued `set_field`, and are deliberately absent from SETTABLE_COLUMNS.
PFP_FLAG_COLUMNS: frozenset[str] = frozenset({"use_pfp", "pfp_prerender", "pfp_daily"})

#: Every boolean column a command may write, portraits included. Booleans go through
#: `set_flag` for the same reason the portraits always did — `set_field` is string-valued —
#: and are likewise absent from SETTABLE_COLUMNS.
FLAG_COLUMNS: frozenset[str] = PFP_FLAG_COLUMNS | {"per_tier_colour_enabled"}

#: Ordered column list used to build an ImageConfig from a row.
_CONFIG_COLUMNS: tuple[str, ...] = (
    ("module_enabled", "template_directory")
    + tuple(TEMPLATE_COLUMNS)
    + tuple(ASSET_DIRECTORIES)
    + ("use_pfp", "pfp_prerender", "pfp_daily", "pfp_daily_time")
    + ("time_zone", "time_format", "date_format", "fastest_lap_colour")
    + ("per_tier_colour_enabled",)
)


class UnknownConfigField(ValueError):
    """Raised when a write targets a column outside SETTABLE_COLUMNS."""


class ImageConfigService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get_config(self) -> ImageConfig | None:
        async with get_connection(self._db_path) as db:
            return await read_config_on(db)

    async def get_toggles(self) -> dict[str, bool]:
        """Return every aspect's state. Aspects with no row read as disabled."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT aspect, enabled FROM image_aspect_toggles"
            )
            rows = await cursor.fetchall()
        stored = {row["aspect"]: bool(row["enabled"]) for row in rows}
        return {aspect: stored.get(aspect, False) for aspect in ASPECTS}

    async def is_aspect_enabled(self, aspect: str) -> bool:
        return (await self.get_toggles()).get(aspect, False)

    # ── Writes ────────────────────────────────────────────────────────────

    async def create_with_defaults(self) -> ImageConfig:
        """Create the config row and all nine toggle rows in one transaction.

        Idempotent: an existing configuration is left exactly as it is, which is what
        makes re-enabling after a disable lossless (FR-004a).
        """
        async with get_connection(self._db_path) as db:
            await db.execute("INSERT OR IGNORE INTO image_config (id) VALUES (1)")
            await db.executemany(
                "INSERT OR IGNORE INTO image_aspect_toggles (aspect, enabled) VALUES (?, 0)",
                [(aspect,) for aspect in ASPECTS],
            )
            await db.commit()

            cursor = await db.execute("SELECT * FROM image_config")
            row = await cursor.fetchone()

        return _row_to_config(row)

    async def candidate_config(
        self, column: str, value: str
    ) -> ImageConfig | None:
        """The stored configuration with *column* overridden — **not** persisted.

        This is what makes validate-then-store possible (FR-005). The validity engine
        already takes an ``ImageConfig``, so evaluating a copy reuses it exactly and no
        second code path can drift from what ``/images config view`` reports.

        Writing first and rolling back on failure was rejected: a concurrent read could
        observe the bad value, and a crash between write and rollback would leave it
        stored — the outcome FR-005 exists to prevent.
        """
        if column not in SETTABLE_COLUMNS:
            raise UnknownConfigField(f"`{column}` is not a settable image config field.")

        current = await self.get_config()
        if current is None:
            return None
        # The column is named at run time, so what it takes is only known then.
        changes: dict[str, Any] = {column: value}
        return dataclasses.replace(current, **changes)

    # ── Per-tier colours (051) ────────────────────────────────────────────
    #
    # A table of their own rather than columns here, because the set of slots is the
    # league's to invent: a slot is whatever its own template marks. Keyed on the
    # division's normalised name so a tier's palette survives the season rollover that
    # replaces its `divisions` row — the same reasoning, and the same `normalise()`, that
    # makes a tier's logo `division_1.svg`.

    async def get_tier_palette(self, division_name: str) -> dict[str, str]:
        """Every slot this tier has a colour for. The render path's only reader."""
        from leaguebot.image.utils.asset_resolver import normalise

        key = normalise(division_name or "")
        if not key:
            return {}
        async with get_connection(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT slot, colour FROM image_tier_colour WHERE division_slug = ?",
                    (key,),
                )
            ).fetchall()
        return {row["slot"]: row["colour"] for row in rows}

    async def season_division_names(self) -> list[str]:
        """The divisions the per-tier colour check measures against, by tier.

        The season a league is working on: ACTIVE if there is one, else SETUP — the same
        choice `SeasonService.get_previewable_divisions` makes, so what `/season placements-review`
        validates is what `/images test` would draw.

        The fastest-lap contrast check reads the same list (#165), so the tiers it measures
        the plate in are the tiers a colour is demanded of — "the season under way" means
        one thing in the image module, not two.

        It sits on **this** service, though it reads season data, because this is the
        service that owns `image_tier_colour` and the one `ImageValidityService` already
        holds. The alternative was a fourth constructor dependency on the validity service
        for a single query, which buys nothing and costs every caller that builds one.

        Empty where the server has no such season: a league with no divisions cannot be
        short of a colour for one, and a check that said otherwise would block a
        configuration nobody could complete.
        """
        async with get_connection(self._db_path) as db:
            season = await (
                await db.execute(
                    "SELECT id FROM seasons "
                    "WHERE status IN ('ACTIVE', 'SETUP') "
                    "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                )
            ).fetchone()
            if season is None:
                return []
            rows = await (
                await db.execute(
                    "SELECT name FROM divisions WHERE season_id = ? ORDER BY tier",
                    (season[0],),
                )
            ).fetchall()
        return [row["name"] for row in rows]

    async def get_all_tier_colours(self) -> dict[str, dict[str, str]]:
        """Every tier's palette, keyed by division slug — for validation and the view.

        Ordered, so the configuration view and the season review list tiers and slots the
        same way on every run: a report whose lines move between two readings of one
        configuration is a report a manager cannot compare by eye.
        """
        async with get_connection(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT division_slug, slot, colour FROM image_tier_colour "
                    "ORDER BY division_slug, slot"
                )
            ).fetchall()
        palettes: dict[str, dict[str, str]] = {}
        for row in rows:
            palettes.setdefault(row["division_slug"], {})[row["slot"]] = row["colour"]
        return palettes



# ── Writes on a connection ────────────────────────────────────────────────
#
# The settings' saves (`image_settings`) write through these, on the connection the change
# queue hands a step, never committing: the value and its audit entry stand or fall together.
# Each judges "held" on the connection that writes (a change ahead may have moved the value),
# keeps the allow-list of its committing twin above, and returns what `Saved` carries.


async def read_config_on(db: aiosqlite.Connection) -> ImageConfig | None:
    """The stored configuration read on *db*, or None before the module has one."""
    row = await (await db.execute("SELECT * FROM image_config")).fetchone()
    if row is None:
        return None
    return _row_to_config(row)


async def set_field_on(db: aiosqlite.Connection, column: str, value: str) -> Saved:
    """Write one text configuration column on *db*, guarded by the allow-list.

    A value already held writes nothing and comes back unchanged; otherwise one
    `IMAGE_CONFIG_SET` entry records `{column: old}` → `{column: value}`.
    """
    if column not in SETTABLE_COLUMNS:
        raise UnknownConfigField(f"`{column}` is not a settable image config field.")

    # Column name is interpolated because SQLite cannot parameterise identifiers;
    # it is safe only because it was checked against the allow-list above.
    row = await (await db.execute(f"SELECT {column} FROM image_config")).fetchone()
    old = None if row is None else row[0]
    if row is not None and old == value:
        return Saved(changed=False)
    await db.execute(f"UPDATE image_config SET {column} = ?", (value,))
    return Saved(
        changed=True, audits=(AuditRecord("IMAGE_CONFIG_SET", {column: old}, {column: value}),)
    )


async def set_flag_on(db: aiosqlite.Connection, column: str, enabled: bool) -> Saved:
    """Write one boolean configuration column on *db*, guarded by its own allow-list.

    Held means standing as *enabled* already: nothing is written. Otherwise one
    `IMAGE_CONFIG_SET` entry records `{column: old}` → `{column: enabled}`, as booleans.
    """
    if column not in FLAG_COLUMNS:
        raise UnknownConfigField(f"`{column}` is not a settable image config flag.")

    row = await (await db.execute(f"SELECT {column} FROM image_config")).fetchone()
    old = None if row is None else bool(row[0])
    if old is not None and old == enabled:
        return Saved(changed=False)
    await db.execute(f"UPDATE image_config SET {column} = ?", (1 if enabled else 0,))
    return Saved(
        changed=True,
        audits=(AuditRecord("IMAGE_CONFIG_SET", {column: old}, {column: enabled}),),
    )


async def set_aspect_on(db: aiosqlite.Connection, aspect: str, enabled: bool) -> Saved:
    """Switch one image output on or off on *db*.

    An output with no row reads as off. Held writes nothing; otherwise one `IMAGE_ASPECT_SET`
    entry records `{"aspect", "enabled": old}` → `{"aspect", "enabled": new}`.
    """
    if aspect not in ASPECTS:
        raise UnknownConfigField(f"`{aspect}` is not a known image aspect.")
    row = await (
        await db.execute("SELECT enabled FROM image_aspect_toggles WHERE aspect = ?", (aspect,))
    ).fetchone()
    old = False if row is None else bool(row[0])
    if old == enabled:
        return Saved(changed=False)
    await db.execute(
        "INSERT INTO image_aspect_toggles (aspect, enabled) VALUES (?, ?) "
        "ON CONFLICT(aspect) DO UPDATE SET enabled = excluded.enabled",
        (aspect, int(enabled)),
    )
    return Saved(
        changed=True,
        audits=(
            AuditRecord(
                "IMAGE_ASPECT_SET",
                {"aspect": aspect, "enabled": old},
                {"aspect": aspect, "enabled": enabled},
            ),
        ),
    )


async def set_tier_colours_on(
    db: aiosqlite.Connection, division_name: str, colours: dict[str, str]
) -> Saved:
    """Set one or several slots for one tier on *db*; one entry per slot changed.

    **Merged, not replaced** (decided 2026-09-08): a slot the caller does not name keeps the
    colour it had. A league pasting a partial palette is correcting part of a scheme, not
    declaring the whole of it, and losing the rest to an omission would be a silent cost.
    A slot already holding its colour is not changed and not written; where every slot named is
    held, nothing is written at all.

    *division_name* and each slot are normalised and validated here as well as at the command,
    because this is the last point before a CSS selector and a service is not entitled to assume
    its caller checked. Each colour is expected to have been through ``normalise_hex`` already.
    One `IMAGE_TIER_COLOUR_SET` entry per slot changed records the division as it was named,
    the canonical slot and the colour before and after; it carries no division id, a tier's
    colours outliving the season that holds its division.
    """
    from leaguebot.image.utils.asset_resolver import normalise
    from leaguebot.image.utils.svg_palette import normalise_slot

    key = normalise(division_name or "")
    if not key:
        raise UnknownConfigField("a division name is required.")
    if not colours:
        return Saved(changed=False)

    held = {
        row["slot"]: row["colour"]
        for row in await (
            await db.execute(
                "SELECT slot, colour FROM image_tier_colour WHERE division_slug = ?", (key,)
            )
        ).fetchall()
    }
    changes = [
        (canonical, colour)
        for canonical, colour in sorted(
            (normalise_slot(slot), colour) for slot, colour in colours.items()
        )
        if held.get(canonical) != colour
    ]
    if not changes:
        return Saved(changed=False)
    await db.executemany(
        "INSERT INTO image_tier_colour (division_slug, slot, colour) VALUES (?, ?, ?) "
        "ON CONFLICT(division_slug, slot) DO UPDATE SET colour = excluded.colour",
        [(key, canonical, colour) for canonical, colour in changes],
    )
    return Saved(
        changed=True,
        audits=tuple(
            AuditRecord(
                "IMAGE_TIER_COLOUR_SET",
                {"division": division_name, "slot": canonical, "colour": held.get(canonical)},
                {"division": division_name, "slot": canonical, "colour": colour},
            )
            for canonical, colour in changes
        ),
    )


async def set_daily_portraits_on(db: aiosqlite.Connection, time_of_day: str) -> Saved:
    """Turn daily portrait updates on at *time_of_day* on *db*: the time and the flag in one write.

    Held means both already stand so. Otherwise one `IMAGE_CONFIG_SET` entry holds both columns,
    `{"pfp_daily": old, "pfp_daily_time": old}` → `{True, new}`: the confirmation is one change.
    """
    row = await (
        await db.execute("SELECT pfp_daily, pfp_daily_time FROM image_config")
    ).fetchone()
    old_flag = None if row is None else bool(row["pfp_daily"])
    old_time = None if row is None else row["pfp_daily_time"]
    if old_flag is True and old_time == time_of_day:
        return Saved(changed=False)
    await db.execute(
        "UPDATE image_config SET pfp_daily = 1, pfp_daily_time = ?", (time_of_day,)
    )
    return Saved(
        changed=True,
        audits=(
            AuditRecord(
                "IMAGE_CONFIG_SET",
                {"pfp_daily": old_flag, "pfp_daily_time": old_time},
                {"pfp_daily": True, "pfp_daily_time": time_of_day},
            ),
        ),
    )


def portrait_configuration_fault(config) -> str | None:
    """Why this configuration's driver-portrait settings are invalid, or None.

    The standing form of the rule `pfp_change_refusal` enforces on each change. The commands
    make this state unreachable -- both sub-toggles refuse while portraits are disabled, and
    `pfp_prerender` defaults on -- so this is defence in depth against a hand-edited database
    or a future path that writes the columns directly, and is what `/season placements-review` and
    the confirmation of placements read.

    Total, and tolerant of a configuration object without the portrait settings: a missing field
    reads as off, which is what its default says.
    """
    if config is None or not getattr(config, "use_pfp", False):
        return None
    if getattr(config, "pfp_prerender", False) or getattr(config, "pfp_daily", False):
        return None
    return (
        "Driver portraits are set to be obtained from Discord, but neither pre-render "
        "nor daily updates are enabled, so no portrait would ever be fetched. Enable "
        "`/images use-pfp prerender-toggle` or `/images use-pfp daily-toggle`, or turn "
        "off `/images use-pfp toggle`."
    )


def pfp_change_refusal(config: ImageConfig, column: str, enabled: bool) -> str | None:
    """Why this change to a portrait toggle must be refused, or None where it may proceed.

    Where portraits are enabled at all, at least one of the two update triggers must be
    enabled too. A configuration with neither obtains a portrait at no moment whatever, which
    is precisely what disabling `use_pfp` already provides, so the module does not hold it: a
    change that would leave neither is refused and the configuration left as it stood.

    Pure and total, taking the configuration as it stands and the change proposed. The rule is
    therefore stated once and tested without a database, rather than restated in each of the
    three commands that must honour it.

    Only a change that *reduces* cover is ever refused. Enabling anything is always allowed,
    and disabling `use_pfp` is always allowed -- turning the feature off wholesale is the very
    thing the invalid configuration was an awkward spelling of.
    """
    if enabled:
        return None

    if column == "use_pfp":
        return None

    other = "pfp_daily" if column == "pfp_prerender" else "pfp_prerender"
    if not config.use_pfp or getattr(config, other):
        return None

    names = {
        "pfp_prerender": ("pre-render updates", "daily updates"),
        "pfp_daily": ("daily updates", "pre-render updates"),
    }
    this_one, other_one = names[column]
    return (
        f"Cannot disable {this_one}: {other_one} are off, so this is the only way "
        f"profile pictures would ever be fetched. Enable {other_one} first, or turn "
        f"off `/images use-pfp toggle` entirely."
    )


def tier_name_refusal(division_name: str) -> str | None:
    """Why colours cannot be stored under *division_name*, or None where they can.

    A tier's colours are keyed on the division's normalised name, as its logo's file name is,
    so a name with no letter or digit in it has no key to be stored under. That is the entry of
    whoever typed it, not a fault in the bot, and is said before anything is written: the setter
    and the pasted form refuse with it, the import passes the block over with it.

    Pure and total, as `pfp_change_refusal` is, and bare: the reason alone, for each caller to
    put its own words around.
    """
    from leaguebot.image.utils.asset_resolver import normalise

    if normalise(division_name or ""):
        return None
    return f"`{division_name}` is not a division name the bot can store colours under."


def _row_to_config(row) -> ImageConfig:
    values = {name: row[name] for name in _CONFIG_COLUMNS}
    values["module_enabled"] = bool(values["module_enabled"])
    for name in ("use_pfp", "pfp_prerender", "pfp_daily", "per_tier_colour_enabled"):
        values[name] = bool(values[name])
    return ImageConfig(**values)
