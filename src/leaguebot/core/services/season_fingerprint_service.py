"""A fingerprint of everything `/season placements-review` reports, taken when it is posted.

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

A full trial render at approval would only ever catch a change that broke a *drawing*; this
catches any change at all, and costs a dozen indexed reads rather than an Inkscape pass per
division.

**Core reads its own tables and no module's.** What the review reports of a module is asked of
the module, through the `fingerprint_areas_read` hook, on the connection the fingerprint holds
open: each subscriber hands back the rows of its own configuration by area, whatever its switch,
and this digests them. Which modules are on is asked of each module's switch reader through the
module service, and the artwork on disk of the image module's drawing.

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
from typing import TYPE_CHECKING, Any

from leaguebot.core.db.database import get_connection, tuple_rows
from leaguebot.core.services.image_drawing import ImageDrawing
from leaguebot.core.services.module_service import SwitchNotSignedUp

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.utils.hooks import AwaitedHook

log = logging.getLogger(__name__)

__all__ = ["SeasonFingerprint", "AREA_LABELS", "take_fingerprint"]


#: Every area, and the phrase a refusal uses for it. The keys are what a digest is stored
#: under, and what a module hands the `fingerprint_areas_read` hook its rows under; a new area,
#: core's or a module's, belongs here too, or the refusal naming it would fall back to the bare
#: key.
AREA_LABELS: dict[str, str] = {
    "season": "the season's own settings",
    "divisions": "the divisions",
    "rounds": "the rounds",
    "teams": "the teams and their seats",
    "drivers": "the seated drivers",
    "unsettled signups": "the unsettled signups",
    "channels": "the channels",
    "modules": "which modules are enabled",
    "test mode": "test mode",
    "team list": "the team list and its roles",
    "points": "the points configurations",
    "signup": "the signup configuration",
    "roles": "the league's base role and driver role",
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

        A fingerprint that could not be taken (empty) differs from every fingerprint, another
        empty one included, every area named: a fault at both the review and the press must
        refuse the approval, not read as a season unchanged.
        """
        if not self.areas or not other.areas:
            return [AREA_LABELS[area] for area in sorted(AREA_LABELS)]
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


async def take_fingerprint(
    db_path: str,
    season_id: int,
    *,
    drawing: ImageDrawing,
    module_service: ModuleService,
    fingerprint_areas_read: AwaitedHook,
) -> SeasonFingerprint:
    """Fingerprint everything `/season placements-review` reports for *season_id*, read from
    *db_path*. Core's own areas are read here; each module's are asked of it, through
    *fingerprint_areas_read* (`bot.fingerprint_areas_read`), handed the connection held and the
    season; which modules are on is asked of each switch reader through *module_service*; and
    *drawing* is the image module's own drawing (`bot.image_drawing`), asked for the league's
    artwork signature, the one area a database read cannot see.

    One connection for the lot. A fingerprint that could not be taken is an empty one, and an empty
    fingerprint differs from every fingerprint, another empty one included, so a fault here, a
    module's included, refuses an approval whose review was read rather than waving it through: its
    catch-all logs the fault on the host and gives that empty one. The one error it raises is a
    switch nobody signed up for (`SwitchNotSignedUp`), a fault in the bot's assembly and not a
    reading.
    """
    areas: dict[str, str] = {}
    try:
        async with get_connection(db_path) as db:
            # What each module hands of its own configuration, by area, in the order the
            # modules signed up.
            handed: dict[str, list[Any]] = {}
            for each in await fingerprint_areas_read.call(db, season_id):
                for area, value in each.items():
                    handed.setdefault(area, []).append(value)

            # The stage as well as the status: confirming a configuration, or mid-season
            # placements, moves the stage alone, and a review of the stage left behind no
            # longer describes the season. An approval's own review, check and
            # re-judgement all read it in Placements, so it never moves between them.
            areas["season"] = _digest(
                await tuple_rows(
                    db,
                    "SELECT start_date, status, stage, season_number, game_edition "
                    "FROM seasons WHERE id = ?",
                    season_id,
                )
            )

            areas["divisions"] = _digest(
                await tuple_rows(
                    db,
                    "SELECT id, name, tier, mention_role_id, status "
                    "FROM divisions WHERE season_id = ? ORDER BY id",
                    season_id,
                )
            )

            areas["rounds"] = _digest(
                await tuple_rows(
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
                await tuple_rows(
                    db,
                    "SELECT ti.division_id, ti.id, ti.name, ti.full_name, ti.max_seats, "
                    "       ti.is_reserve, "
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
                await tuple_rows(
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

            # Every signup still unsettled on the server: the review names each, and
            # confirming placements waits on them, so a signup approved, rejected or sent back
            # after the report is a change to what would be confirmed.
            from leaguebot.core.services.season_lifecycle_service import UNSETTLED_STATES

            areas["unsettled signups"] = _digest(
                await tuple_rows(
                    db,
                    "SELECT id, discord_user_id, current_state FROM driver_profiles "
                    f"WHERE current_state IN ({','.join('?' for _ in UNSETTLED_STATES)}) "
                    "ORDER BY id",
                    *UNSETTLED_STATES,
                )
            )

            # Every channel the season posts to: the lineup and calendar channels on the divisions
            # row, which are core's, then each module's own, in the order they signed up.
            areas["channels"] = _digest(
                [
                    await tuple_rows(
                        db,
                        "SELECT id, lineup_channel_id, calendar_channel_id "
                        "FROM divisions WHERE season_id = ? ORDER BY id",
                        season_id,
                    ),
                    *handed.pop("channels", []),
                ]
            )

            # Which modules are on, each asked of its own switch reader on this connection.
            areas["modules"] = _digest(
                {
                    module: await module_service.is_enabled_on(db, module)
                    for module in ("weather", "signup", "results", "attendance", "images")
                }
            )

            # Test mode and the server's team list are what confirming a configuration
            # fixes for the season, so a change to either after the report
            # is a change to what would be confirmed.
            areas["test mode"] = _digest(
                await tuple_rows(
                    db,
                    "SELECT test_mode_active FROM server_configs",
                )
            )
            areas["team list"] = _digest(
                [
                    await tuple_rows(
                        db,
                        "SELECT name, full_name, max_seats, is_reserve FROM default_teams "
                        " ORDER BY name",
                    ),
                    await tuple_rows(
                        db,
                        "SELECT team_name, role_id FROM team_role_configs "
                        " ORDER BY team_name",
                    ),
                ]
            )

            # The league's two roles, core's rather than the signup module's.
            # Only the configuration review reports them; confirming the configuration fixes
            # them, so at placements they cannot differ and this area never trips.
            areas["roles"] = _digest(
                await tuple_rows(db, "SELECT base_role_id, driver_role_id FROM server_configs")
            )

            # Every other area a module handed, each the digest of what the modules handed under
            # it.
            for area, values in handed.items():
                areas[area] = _digest(values)

        areas["artwork"] = _digest(await drawing.artwork_signature())
    except SwitchNotSignedUp:
        raise  # a module that never signed up is a fault in the bot's assembly, not a reading
    except Exception as exc:  # noqa: BLE001 — an unreadable season is a changed one
        log.error("season fingerprint: could not be taken: %s", exc, exc_info=True)
        return SeasonFingerprint({})

    # Every area, every time. A query that silently stopped matching the schema would
    # otherwise leave its area out, and an area left out is a change that slips through —
    # the refusal that follows is the loud failure that catch-all above would not give.
    missing = sorted(set(AREA_LABELS) - set(areas))
    if missing:
        log.error("season fingerprint: areas missing from the digest: %s", missing)
        return SeasonFingerprint({})

    return SeasonFingerprint(areas)
