"""A fingerprint of everything `/season review` reports, taken when it is posted.

The review is the evidence a season is approved on, and the Approve button commits on the
strength of it. A manager who moves a channel, reseats a driver or edits a round between
reading the report and pressing the button would otherwise approve a season nobody has
reviewed — the five-minute window makes that unlikely, and this makes it detectable.

The rule a league is told is the one this implements: **the report you read is the report
you approved.** Everything the review prints is fingerprinted, not merely the inputs to
the approval gates, because a manager cannot tell from the report which half would be
which.

**Per area, not one digest.** A refusal names what changed — "the rounds of Pro have
changed" is actionable where "something changed" is not — so each area is hashed alone
and compared alone.

This replaced a full trial render at approval (2026-09-07). The render only ever caught a
change that broke a *drawing*; this catches any change at all, and costs a dozen indexed
reads rather than an Inkscape pass per division.

**Ordering is explicit everywhere.** Every query carries an ``ORDER BY`` and every mapping
is serialised through ``sorted()``. A digest that depended on dict insertion order or on
what an index happened to return would differ between two machines holding identical
seasons, which is the failure mode `CLAUDE.md` warns about under host-agnostic tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from db.database import get_connection

log = logging.getLogger(__name__)

__all__ = ["SeasonFingerprint", "AREA_LABELS", "take_fingerprint"]


#: Every area, and the phrase a refusal uses for it. The keys are what a digest is stored
#: under; a new area added to `take_fingerprint` belongs here too, or the refusal naming it
#: would fall back to the bare key.
AREA_LABELS: dict[str, str] = {
    "season": "the season's own settings",
    "divisions": "the divisions",
    "rounds": "the rounds",
    "teams": "the teams and their seats",
    "drivers": "the seated drivers",
    "channels": "the channels",
    "modules": "which modules are enabled",
    "points": "the points configurations",
    "signup": "the signup configuration",
    "attendance": "the attendance configuration",
    "weather": "the weather configuration",
    "images": "the image configuration",
    "artwork": "the templates and artwork on disk",
}


@dataclass(frozen=True)
class SeasonFingerprint:
    """What the season looked like at one moment, area by area."""

    areas: dict[str, str] = field(default_factory=dict)

    def differs_from(self, other: "SeasonFingerprint") -> list[str]:
        """The areas that changed, named as a league reads them.

        An area present in one fingerprint and absent from the other counts as changed:
        that is a season that has gained or lost something wholesale.
        """
        changed: list[str] = []
        for area in sorted(set(self.areas) | set(other.areas)):
            if self.areas.get(area) != other.areas.get(area):
                changed.append(AREA_LABELS.get(area, area))
        return changed


def _digest(value) -> str:
    """A stable digest of *value*, whatever nesting it carries.

    ``sort_keys`` is what makes it stable: two runs building the same mapping in a
    different order must produce the same digest, or a fingerprint would report a change
    nobody made.
    """
    encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _rows(db, sql: str, *params) -> list[tuple]:
    """Every row as a plain tuple, so a digest does not depend on the row factory."""
    cursor = await db.execute(sql, params)
    return [tuple(row) for row in await cursor.fetchall()]


def _path_signature(path: Path) -> list:
    """A file's identity for fingerprinting: its name, its size and when it changed.

    Never its content. Hashing fifteen templates and every asset a league supplies would
    cost more than the render this exists to replace, and size-and-mtime catches every
    edit that is not a byte-identical rewrite inside the same second.
    """
    try:
        stat = path.stat()
        return [path.name, stat.st_size, int(stat.st_mtime)]
    except OSError:
        # A file that cannot be read is itself a state worth noticing: it may have been
        # deleted since the review, which is exactly the change this must catch.
        return [path.name, None, None]


def _directory_signature(directory: Path) -> list:
    """Every file directly in *directory*, sorted by name.

    Sorted rather than in whatever order the filesystem yields, so two hosts holding the
    same artwork agree. Not recursive: an asset class is a flat folder of files.
    """
    try:
        entries = sorted(
            (p for p in directory.iterdir() if p.is_file()), key=lambda p: p.name
        )
    except OSError:
        return []
    return [_path_signature(p) for p in entries]


async def take_fingerprint(bot, server_id: int, season_id: int) -> SeasonFingerprint:
    """Fingerprint everything `/season review` reports for *season_id*.

    One connection for the lot. Never raises: a fingerprint that could not be taken is an
    empty one, and an empty fingerprint differs from every other, so a fault here refuses
    an approval rather than waving it through.
    """
    areas: dict[str, str] = {}
    try:
        async with get_connection(bot.db_path) as db:
            areas["season"] = _digest(
                await _rows(
                    db,
                    "SELECT start_date, status, season_number, game_edition "
                    "FROM seasons WHERE id = ?",
                    season_id,
                )
            )

            areas["divisions"] = _digest(
                await _rows(
                    db,
                    "SELECT id, name, tier, mention_role_id, status "
                    "FROM divisions WHERE season_id = ? ORDER BY id",
                    season_id,
                )
            )

            areas["rounds"] = _digest(
                await _rows(
                    db,
                    "SELECT r.division_id, r.round_number, r.format, r.track_name, "
                    "       r.scheduled_at, r.status "
                    "FROM rounds r JOIN divisions d ON d.id = r.division_id "
                    "WHERE d.season_id = ? "
                    "ORDER BY r.division_id, r.round_number, r.id",
                    season_id,
                )
            )

            areas["teams"] = _digest(
                await _rows(
                    db,
                    "SELECT ti.division_id, ti.id, ti.name, ti.max_seats, ti.is_reserve, "
                    "       ts.seat_number "
                    "FROM team_instances ti "
                    "JOIN divisions d ON d.id = ti.division_id "
                    "LEFT JOIN team_seats ts ON ts.team_instance_id = ti.id "
                    "WHERE d.season_id = ? "
                    "ORDER BY ti.division_id, ti.id, ts.seat_number",
                    season_id,
                )
            )

            areas["drivers"] = _digest(
                await _rows(
                    db,
                    "SELECT dsa.division_id, dsa.driver_profile_id, dsa.team_seat_id, "
                    "       dp.current_state "
                    "FROM driver_season_assignments dsa "
                    "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    "WHERE dsa.season_id = ? "
                    "ORDER BY dsa.division_id, dsa.driver_profile_id",
                    season_id,
                )
            )

            # Every channel the season posts to: the three on the divisions row, the three
            # of the results config and the two of the attendance config.
            areas["channels"] = _digest(
                [
                    await _rows(
                        db,
                        "SELECT id, forecast_channel_id, lineup_channel_id, "
                        "       calendar_channel_id "
                        "FROM divisions WHERE season_id = ? ORDER BY id",
                        season_id,
                    ),
                    await _rows(
                        db,
                        "SELECT c.division_id, c.results_channel_id, "
                        "       c.standings_channel_id, c.penalty_channel_id, "
                        "       c.reserves_in_standings "
                        "FROM division_results_config c "
                        "JOIN divisions d ON d.id = c.division_id "
                        "WHERE d.season_id = ? ORDER BY c.division_id",
                        season_id,
                    ),
                    await _rows(
                        db,
                        "SELECT c.division_id, c.rsvp_channel_id, c.attendance_channel_id "
                        "FROM attendance_division_config c "
                        "JOIN divisions d ON d.id = c.division_id "
                        "WHERE d.season_id = ? ORDER BY c.division_id",
                        season_id,
                    ),
                ]
            )

            # The four module switches, which live in three places: weather and signup on
            # the server row, results and attendance each in their own config table.
            areas["modules"] = _digest(
                [
                    await _rows(
                        db,
                        "SELECT weather_module_enabled, signup_module_enabled "
                        "FROM server_configs WHERE server_id = ?",
                        server_id,
                    ),
                    await _rows(
                        db,
                        "SELECT module_enabled FROM results_module_config "
                        "WHERE server_id = ?",
                        server_id,
                    ),
                    await _rows(
                        db,
                        "SELECT module_enabled FROM attendance_config WHERE server_id = ?",
                        server_id,
                    ),
                    await _rows(
                        db,
                        "SELECT module_enabled FROM image_config WHERE server_id = ?",
                        server_id,
                    ),
                ]
            )

            areas["points"] = _digest(
                [
                    await _rows(
                        db,
                        "SELECT config_name FROM season_points_links "
                        "WHERE season_id = ? ORDER BY config_name",
                        season_id,
                    ),
                    await _rows(
                        db,
                        "SELECT config_name, session_type, position, points "
                        "FROM season_points_entries WHERE season_id = ? "
                        "ORDER BY config_name, session_type, position",
                        season_id,
                    ),
                    await _rows(
                        db,
                        "SELECT config_name, session_type, fl_points, fl_position_limit "
                        "FROM season_points_fl WHERE season_id = ? "
                        "ORDER BY config_name, session_type",
                        season_id,
                    ),
                ]
            )

            areas["signup"] = _digest(
                [
                    await _rows(
                        db,
                        "SELECT signup_channel_id, base_role_id, signed_up_role_id, "
                        "       signups_open, selected_tracks_json, close_at "
                        "FROM signup_module_config WHERE server_id = ?",
                        server_id,
                    ),
                    await _rows(
                        db,
                        "SELECT nationality_required, time_type, time_image_required "
                        "FROM signup_module_settings WHERE server_id = ?",
                        server_id,
                    ),
                    await _rows(
                        db,
                        "SELECT id, day_of_week, time_hhmm, slot_sequence_id "
                        "FROM signup_availability_slots "
                        "WHERE server_id = ? ORDER BY id",
                        server_id,
                    ),
                ]
            )

            areas["attendance"] = _digest(
                await _rows(
                    db,
                    "SELECT module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                    "       rsvp_deadline_hours, no_rsvp_penalty, absent_penalty, "
                    "       no_show_penalty, autoreserve_threshold, autosack_threshold "
                    "FROM attendance_config WHERE server_id = ?",
                    server_id,
                )
            )

            areas["weather"] = _digest(
                await _rows(
                    db,
                    "SELECT phase_1_days, phase_2_days, phase_3_hours "
                    "FROM weather_pipeline_config WHERE server_id = ?",
                    server_id,
                )
            )

            image_config = await _rows(
                db, "SELECT * FROM image_config WHERE server_id = ?", server_id
            )
            areas["images"] = _digest(
                [
                    image_config,
                    await _rows(
                        db,
                        "SELECT aspect, enabled FROM image_aspect_toggles "
                        "WHERE server_id = ? ORDER BY aspect",
                        server_id,
                    ),
                ]
            )

        areas["artwork"] = _digest(await _artwork_signature(bot, server_id))
    except Exception as exc:  # noqa: BLE001 — an unreadable season is a changed one
        log.error("season fingerprint: could not be taken: %s", exc)
        return SeasonFingerprint({})

    # Every area, every time. A query that silently stopped matching the schema would
    # otherwise leave its area out, and an area left out is a change that slips through —
    # the refusal that follows is the loud failure that catch-all above would not give.
    missing = sorted(set(AREA_LABELS) - set(areas))
    if missing:
        log.error("season fingerprint: areas missing from the digest: %s", missing)
        return SeasonFingerprint({})

    return SeasonFingerprint(areas)


async def _artwork_signature(bot, server_id: int) -> list:
    """The template files and asset folders, by size and modification time.

    The one thing a database read cannot see. A template edited or deleted between the
    review and the button would otherwise pass unnoticed, and that is precisely the change
    the render this replaced used to catch.
    """
    from models.image_constants import ASSET_DIRECTORIES, TEMPLATE_COLUMNS
    from utils.paths import resolve_within_project_root

    config = await bot.image_config_service.get_config(server_id)
    if config is None:
        return []

    signature: list = []

    directory = getattr(config, "template_directory", None)
    if directory:
        try:
            root = resolve_within_project_root(directory)
            for column in sorted(TEMPLATE_COLUMNS):
                filename = getattr(config, column, None)
                if filename:
                    signature.append([column, _path_signature(root / filename)])
        except Exception:  # noqa: BLE001 — an unresolvable directory is itself a state
            signature.append(["template_directory", directory, "unresolvable"])

    for column in sorted(ASSET_DIRECTORIES):
        value = getattr(config, column, None)
        if not value:
            continue
        try:
            signature.append([column, _directory_signature(resolve_within_project_root(value))])
        except Exception:  # noqa: BLE001
            signature.append([column, value, "unresolvable"])

    return signature
