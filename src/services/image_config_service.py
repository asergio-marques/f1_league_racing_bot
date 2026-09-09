"""ImageConfigService — CRUD over image_config and image_aspect_toggles.

Configuration retention (FR-004a): disabling the module clears only ``module_enabled``.
Nothing in this service deletes a configuration row or resets a toggle, which is the
Principle X.6 exception for configuration that cannot go stale — no value here names a
Discord channel, role, message or scheduled job.
"""
from __future__ import annotations

import dataclasses
import logging

from db.database import get_connection
from models.image_constants import ASPECTS, ASSET_DIRECTORIES, TEMPLATE_COLUMNS
from models.image_module import ImageConfig

log = logging.getLogger(__name__)


#: Columns a slash command may write. Anything outside this set is rejected, so a caller
#: cannot reach `module_enabled` or `server_id` through the generic setter.
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
    ("server_id", "module_enabled", "template_directory")
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

    async def get_config(self, server_id: int) -> ImageConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM image_config WHERE server_id = ?", (server_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_config(row)

    async def get_toggles(self, server_id: int) -> dict[str, bool]:
        """Return every aspect's state. Aspects with no row read as disabled."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT aspect, enabled FROM image_aspect_toggles WHERE server_id = ?",
                (server_id,),
            )
            rows = await cursor.fetchall()
        stored = {row["aspect"]: bool(row["enabled"]) for row in rows}
        return {aspect: stored.get(aspect, False) for aspect in ASPECTS}

    async def is_aspect_enabled(self, server_id: int, aspect: str) -> bool:
        return (await self.get_toggles(server_id)).get(aspect, False)

    # ── Writes ────────────────────────────────────────────────────────────

    async def create_with_defaults(self, server_id: int) -> ImageConfig:
        """Create the config row and all eight toggle rows in one transaction.

        Idempotent: an existing configuration is left exactly as it is, which is what
        makes re-enabling after a disable lossless (FR-004a).
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO image_config (server_id) VALUES (?)",
                (server_id,),
            )
            await db.executemany(
                "INSERT OR IGNORE INTO image_aspect_toggles (server_id, aspect, enabled) "
                "VALUES (?, ?, 0)",
                [(server_id, aspect) for aspect in ASPECTS],
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM image_config WHERE server_id = ?", (server_id,)
            )
            row = await cursor.fetchone()

        return _row_to_config(row)

    async def candidate_config(
        self, server_id: int, column: str, value: str
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

        current = await self.get_config(server_id)
        if current is None:
            return None
        return dataclasses.replace(current, **{column: value})

    async def set_field(self, server_id: int, column: str, value: str) -> None:
        """Write a single configuration column, guarded by the allow-list."""
        if column not in SETTABLE_COLUMNS:
            raise UnknownConfigField(f"`{column}` is not a settable image config field.")

        # Column name is interpolated because SQLite cannot parameterise identifiers;
        # it is safe only because it was checked against the allow-list above.
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE image_config SET {column} = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def set_pfp_flag(self, server_id: int, column: str, enabled: bool) -> None:
        """Write one of the three portrait toggles.

        Separate from `set_field` because these are booleans and that setter is string-valued.
        The at-least-one rule between `pfp_prerender` and `pfp_daily` is **not** enforced here:
        it is a rule about a command being refused, and lives in `pfp_change_refusal` so that
        the caller can state the refusal rather than catching an exception to do it.
        """
        if column not in PFP_FLAG_COLUMNS:
            raise UnknownConfigField(column)
        await self.set_flag(server_id, column, enabled)

    async def set_flag(self, server_id: int, column: str, enabled: bool) -> None:
        """Write any boolean configuration column, guarded by its own allow-list.

        The generalisation of `set_pfp_flag`, which now delegates here: a second boolean
        arrived with the per-tier colours (051) and two identical setters differing only in
        the set they check would be one of them going stale.
        """
        if column not in FLAG_COLUMNS:
            raise UnknownConfigField(f"`{column}` is not a settable image config flag.")

        # Column name is interpolated because SQLite cannot parameterise identifiers;
        # it is safe only because it was checked against the allow-list above.
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE image_config SET {column} = ? WHERE server_id = ?",
                (1 if enabled else 0, server_id),
            )
            await db.commit()

    async def set_aspect(self, server_id: int, aspect: str, enabled: bool) -> None:
        if aspect not in ASPECTS:
            raise UnknownConfigField(f"`{aspect}` is not a known image aspect.")
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT INTO image_aspect_toggles (server_id, aspect, enabled) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(server_id, aspect) DO UPDATE SET enabled = excluded.enabled",
                (server_id, aspect, int(enabled)),
            )
            await db.commit()

    async def toggle_aspect(self, server_id: int, aspect: str) -> bool:
        """Flip an aspect and return its new state."""
        current = await self.is_aspect_enabled(server_id, aspect)
        await self.set_aspect(server_id, aspect, not current)
        return not current

    # ── Per-tier colours (051) ────────────────────────────────────────────
    #
    # A table of their own rather than columns here, because the set of slots is the
    # league's to invent: a slot is whatever its own template marks. Keyed on the
    # division's normalised name so a tier's palette survives the season rollover that
    # replaces its `divisions` row — the same reasoning, and the same `normalise()`, that
    # makes a tier's logo `division_1.svg`.

    async def set_tier_colour(
        self, server_id: int, division_name: str, slot: str, colour: str
    ) -> None:
        """Set one slot's colour for one tier, replacing whatever stood there.

        *slot* is normalised and validated here as well as at the command, because this is
        the last point before it reaches a CSS selector and a service is not entitled to
        assume its caller checked. *colour* is expected to have been through
        ``normalise_hex`` already and is stored as given.
        """
        from utils.asset_resolver import normalise
        from utils.svg_palette import normalise_slot

        key = normalise(division_name or "")
        if not key:
            raise UnknownConfigField("a division name is required.")
        canonical = normalise_slot(slot)
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT INTO image_tier_colour (server_id, division_slug, slot, colour) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(server_id, division_slug, slot) "
                "DO UPDATE SET colour = excluded.colour",
                (server_id, key, canonical, colour),
            )
            await db.commit()

    async def set_tier_colours(
        self, server_id: int, division_name: str, colours: dict[str, str]
    ) -> int:
        """Set several slots for one tier at once, returning how many were written.

        **Merged, not replaced** (decided 2026-09-08): a slot the caller does not name keeps
        the colour it had. A league pasting a partial palette is correcting part of a scheme,
        not declaring the whole of it, and losing the rest to an omission would be a silent
        cost. It is also what makes this consistent with `set_tier_colour`, which upserts.

        One transaction, so a bulk set is all-or-nothing at the database even though the
        import above it decides atomicity per division.
        """
        from utils.asset_resolver import normalise
        from utils.svg_palette import normalise_slot

        key = normalise(division_name or "")
        if not key:
            raise UnknownConfigField("a division name is required.")
        if not colours:
            return 0

        rows = [
            (server_id, key, normalise_slot(slot), colour)
            for slot, colour in sorted(colours.items())
        ]
        async with get_connection(self._db_path) as db:
            await db.executemany(
                "INSERT INTO image_tier_colour (server_id, division_slug, slot, colour) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(server_id, division_slug, slot) "
                "DO UPDATE SET colour = excluded.colour",
                rows,
            )
            await db.commit()
        return len(rows)

    async def get_tier_palette(self, server_id: int, division_name: str) -> dict[str, str]:
        """Every slot this tier has a colour for. The render path's only reader."""
        from utils.asset_resolver import normalise

        key = normalise(division_name or "")
        if not key:
            return {}
        async with get_connection(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT slot, colour FROM image_tier_colour "
                    "WHERE server_id = ? AND division_slug = ?",
                    (server_id, key),
                )
            ).fetchall()
        return {row["slot"]: row["colour"] for row in rows}

    async def season_division_names(self, server_id: int) -> list[str]:
        """The divisions the per-tier colour check measures against, by tier.

        The season a league is working on: ACTIVE if there is one, else SETUP — the same
        choice `SeasonService.get_previewable_divisions` makes, so what `/season review`
        validates is what `/images test` would draw.

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
                    "WHERE server_id = ? AND status IN ('ACTIVE', 'SETUP') "
                    "ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, id DESC LIMIT 1",
                    (server_id,),
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

    async def get_all_tier_colours(self, server_id: int) -> dict[str, dict[str, str]]:
        """Every tier's palette, keyed by division slug — for validation and the view.

        Ordered, so the configuration view and the season review list tiers and slots the
        same way on every run: a report whose lines move between two readings of one
        configuration is a report a manager cannot compare by eye.
        """
        async with get_connection(self._db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT division_slug, slot, colour FROM image_tier_colour "
                    "WHERE server_id = ? ORDER BY division_slug, slot",
                    (server_id,),
                )
            ).fetchall()
        palettes: dict[str, dict[str, str]] = {}
        for row in rows:
            palettes.setdefault(row["division_slug"], {})[row["slot"]] = row["colour"]
        return palettes



def portrait_configuration_fault(config) -> str | None:
    """Why this configuration's driver-portrait settings are invalid, or None.

    The standing form of the rule `pfp_change_refusal` enforces on each change. The commands
    make this state unreachable -- both sub-toggles refuse while portraits are disabled, and
    `pfp_prerender` defaults on -- so this is defence in depth against a hand-edited database
    or a future path that writes the columns directly, and is what `/season review` and
    `/season approve` read.

    Total, and tolerant of a configuration object predating migration 047: a missing field
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


def _row_to_config(row) -> ImageConfig:
    values = {name: row[name] for name in _CONFIG_COLUMNS}
    values["module_enabled"] = bool(values["module_enabled"])
    for name in ("use_pfp", "pfp_prerender", "pfp_daily", "per_tier_colour_enabled"):
        values[name] = bool(values[name])
    return ImageConfig(**values)
