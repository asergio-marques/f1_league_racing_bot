"""The image module's answers to core's drawing protocol.

`ImageDrawing` implements `leaguebot.core.services.image_drawing.ImageDrawing`: the builder
makes one, attaches it to the bot as `bot.image_drawing`, and hands it to `PlacementService`, to
every change that posts a division's calendar, and to the season approval and mid-season
confirmation; the season fingerprint (`take_fingerprint`) is handed it by its callers, the cogs
included. So core never imports this module. The capacity questions ask whether the aspect is drawn at
all, whether the template is valid, and whether it holds that many — answered off the event
loop, since parsing and measuring a template takes real time. ``calendar_wanted``/
``draw_calendar`` and ``lineup_wanted``/``draw_lineup`` answer in the same shape: the two gates,
the asset directories resolved absolutely, the file named, the render, and the notices reported
to the log channel in one message. ``artwork_signature`` answers what the league's artwork
looks like now, for the fingerprint to digest.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from leaguebot.core.services.image_drawing import CalendarPicture, CapacityProblem, LineupPicture
from leaguebot.core.services.module_service import ModuleService
from leaguebot.core.utils.paths import resolve_within_project_root
from leaguebot.image.models.image_catalogues import (
    declared_capacities,
    reserve_capacity_problem,
    row_capacity_problem,
)
from leaguebot.image.models.image_constants import (
    ASSET_DIRECTORIES,
    TEMPLATE_COLUMNS,
    TEMPLATE_LABELS,
)
from leaguebot.image.services.image_attendance_post import (
    ATTENDANCE_ASPECT,
    ATTENDANCE_TEMPLATE_KEY,
)
from leaguebot.image.services.image_calendar_service import (
    TEMPLATE_KEY as CALENDAR_TEMPLATE_KEY,
    build_fill_spec,
    resolve_drawing,
)
from leaguebot.image.services.image_config_service import ImageConfigService
from leaguebot.image.services.image_lineup_post import LINEUP_TEMPLATE_KEY
from leaguebot.image.services.image_render_service import (
    ImageRenderService,
    discard_render,
    resolve_configured_directories,
    spec_builder_with_faults,
)
from leaguebot.image.services.image_standings_post import DRIVERS_TEMPLATE_KEY
from leaguebot.image.services.image_validity_service import ImageValidityService
from leaguebot.image.utils.image_naming import stem_for_drawing
from leaguebot.image.utils.svg_document import off_the_loop

log = logging.getLogger(__name__)

_RESERVE_REMEDY = (
    "Enlarge the template, or turn the `lineup` image aspect off with `/images config toggle`."
)
_SHEET_REMEDY = "Enlarge the template, or the sheet would silently drop a driver."
_STANDINGS_REMEDY = "Enlarge the template, or the standings would silently drop a driver."
_DECLARED_REMEDY = (
    "Enlarge that template, or disable the images module, before adding another driver."
)

#: The calendar's own aspect.
CALENDAR_ASPECT = "calendar"


def _path_signature(path: Path) -> list:
    """A file's identity for fingerprinting: its name, its size and when it changed.

    Never its content. Hashing sixteen templates and every asset a league supplies would cost
    more than the render this exists to replace, and size-and-mtime catches every edit that is
    not a byte-identical rewrite inside the same second.
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

    Sorted rather than in whatever order the filesystem yields, so two hosts holding the same
    artwork agree. Not recursive: an asset class is a flat folder of files.
    """
    try:
        entries = sorted(
            (p for p in directory.iterdir() if p.is_file()), key=lambda p: p.name
        )
    except OSError:
        return []
    return [_path_signature(p) for p in entries]


class ImageDrawing:
    """Core's drawing protocol, implemented over the image module's own services."""

    def __init__(
        self,
        modules: ModuleService,
        config: ImageConfigService,
        validity: ImageValidityService,
        *,
        renderer: ImageRenderService,
        post_log: Callable[[str], Awaitable[object]],
        db_path: str,
    ) -> None:
        self._modules = modules
        self._config = config
        self._validity = validity
        self._renderer = renderer
        self._post_log = post_log
        #: The database the lineup's drawing reads: the division, its committed seats, and
        #: whether nationality is collected. Required like `renderer` and `post_log`, even
        #: though the calendar and the capacity questions read none of it, so that a drawing
        #: is never built unable to do what `draw_lineup` needs.
        self._db_path = db_path

    async def _drawn_template(self, aspect: str, template_key: str) -> Path | None:
        """The template's path where *aspect* is drawn and the template is valid, else None."""
        if not await self._modules.is_images_enabled():
            return None
        toggles = await self._config.get_toggles()
        if not toggles.get(aspect):
            return None
        reports = await self._validity.template_reports()
        report = reports.get(template_key)
        if report is None or not report.valid:
            return None
        return report.resolved_path

    async def reserve_capacity_problem(self, reserves: int) -> CapacityProblem | None:
        path = await self._drawn_template("lineup", LINEUP_TEMPLATE_KEY)
        if path is None:
            return None
        # Imported here, not at module level, so a test's `monkeypatch.setattr` on
        # `svg_document.load_svg` is picked up: a name bound at import time would keep the
        # unpatched function (as `season_cog.py`'s own template reads do, for the same reason).
        from leaguebot.image.utils.svg_document import load_svg

        problem = await off_the_loop(
            lambda: reserve_capacity_problem(load_svg(path), reserves)
        )
        if problem is None:
            return None
        return CapacityProblem(problem=problem, remedy=_RESERVE_REMEDY)

    async def sheet_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        path = await self._drawn_template(ATTENDANCE_ASPECT, ATTENDANCE_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        problem = await off_the_loop(
            lambda: row_capacity_problem(ATTENDANCE_TEMPLATE_KEY, load_svg(path), drivers)
        )
        if problem is None:
            return None
        return CapacityProblem(problem=problem, remedy=_SHEET_REMEDY)

    async def standings_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        path = await self._drawn_template("standings", DRIVERS_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        problem = await off_the_loop(
            lambda: row_capacity_problem(DRIVERS_TEMPLATE_KEY, load_svg(path), drivers)
        )
        if problem is None:
            return None
        return CapacityProblem(problem=problem, remedy=_STANDINGS_REMEDY)

    async def declared_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        if not await self._modules.is_images_enabled():
            return None
        capacities = declared_capacities()
        if not capacities:
            return None
        smallest = min(capacities.values())
        if drivers <= smallest:
            return None
        template_key = min(capacities, key=lambda key: capacities[key])
        label = TEMPLATE_LABELS.get(template_key, template_key)
        problem = (
            f"This would seat {drivers} drivers in the division, but the "
            f"**{label}** image template provides only {smallest} rows"
        )
        return CapacityProblem(problem=problem, remedy=_DECLARED_REMEDY)

    # ── The calendar ───────────────────────────────────────────────────────

    async def calendar_wanted(self) -> bool:
        """Whether this league conveys its calendar as a picture: both the module and the
        `calendar` aspect must be on.

        A stated exception (architecture.md, "A catch-all error handler"): a gate that cannot
        be read must never lose a league its calendar, so a fault here answers False, falling to
        the text, and is recorded only in the host's log — never the log channel, since nothing
        was drawn to report a notice about.

        *Rejected:* treating a calendar gate that cannot be read as a picture that could not be
        drawn: it would add a line to the log channel and refuse `/division calendar-sync` for a
        fault in reading a switch.
        """
        try:
            if not await self._modules.is_images_enabled():
                return False
            return await self._config.is_aspect_enabled(CALENDAR_ASPECT)
        except Exception:  # noqa: BLE001 — a stated exception, see the docstring
            log.exception("calendar: could not read the image gates")
            return False

    async def draw_calendar(
        self, division: Any, rounds: Any, tracks: Any, *, season_number: Any = None,
    ) -> CalendarPicture:
        """Draw *division*'s calendar of *rounds*, named against *tracks*: the two gates
        already asked through `calendar_wanted`, the asset directories resolved absolutely, the
        file named, and the render. The notices are reported to the log channel in one message
        before this returns, so the caller never posts them there again."""
        config = await self._config.get_config()

        drawing = resolve_drawing(
            division_name=division.name,
            division_tier=getattr(division, "tier", None),
            season_number=season_number,
            rounds=rounds,
            tracks=tracks,
            date_format=getattr(config, "date_format", None),
            time_format=getattr(config, "time_format", None),
            time_zone=getattr(config, "time_zone", None),
        )

        # The calendar draws both imagery classes, so it needs both directories (044). They
        # are resolved by the helper every other posting path uses, which anchors them to the
        # project root and keeps the reason any rejection was made. Resolving them by hand
        # produced *relative* paths, which resolve against the temporary directory the
        # rasteriser reads the SVG from rather than against the project — so every circuit map
        # and every country flag a league supplied was silently absent from the drawing, while
        # the packaged `mystery.svg`, resolved absolutely, appeared beside them.
        directories, directory_faults = resolve_configured_directories(
            config,
            (("track", "track_image_directory"), ("flag", "flag_directory")),
            image_type=CALENDAR_TEMPLATE_KEY,
        )

        outcome = await self._renderer.render(
            CALENDAR_TEMPLATE_KEY,
            spec_builder_with_faults(build_fill_spec, drawing, directories, directory_faults),
            filename_stem=stem_for_drawing(drawing, CALENDAR_TEMPLATE_KEY),
            division_name=drawing.division_name,
        )

        # Every other posting path reaches the log channel through `render_for_posting`, which
        # this one cannot use: its two callers differ in origin — the calendar of record is
        # scheduled and falls back to text, the review's copy is commanded and rejects — and
        # both read a `RenderOutcome` rather than a `PostingDecision`. So the report is made
        # here instead, because a notice that is not reported reaches nobody at all — the log
        # channel and a commanding command's own output are the only places one is seen
        # (XIV.4), and the command's output is the caller's to add from what is handed back
        # below. `post_log` (`OutputRouter.post_log`) already contains its own failures, so no
        # catch is needed around it here.
        if outcome.notices:
            await self._post_log(
                ImageRenderService.format_notices(
                    outcome.notices, subject=f"calendar — {division.name}"
                )
            )

        problem = outcome.problem.detail if outcome.problem is not None else None
        notices = tuple(each.detail for each in outcome.notices)
        path = outcome.png_paths[0] if outcome.png_paths else None
        return CalendarPicture(path=path, problem=problem, notices=notices)

    # ── The lineup ───────────────────────────────────────────────────────────

    async def lineup_wanted(self) -> bool:
        """Whether this league conveys its lineup as a picture: both the module and the
        `lineup` aspect must be on, and a valid template configured.

        A stated exception (architecture.md, "A catch-all error handler"), worded as the
        calendar gate's: a fault here must never lose a league its lineup, so it answers False,
        falling to the text, and is recorded only in the host's log — never the log channel,
        since nothing was drawn to report a notice about.

        *Rejected:* treating a lineup gate that cannot be read as a picture that could not be
        drawn: it would add a line to the log channel for a fault in reading a switch.
        """
        try:
            path = await self._drawn_template("lineup", LINEUP_TEMPLATE_KEY)
            return path is not None
        except Exception:  # noqa: BLE001 — a stated exception, see the docstring
            log.exception("lineup: could not read the image gates")
            return False

    async def draw_lineup(self, guild: Any, division_id: int) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild*: the gates already asked through
        `lineup_wanted`, the division's committed seats read from the database, the portraits
        refreshed at the league's own trigger, and the render. The notices are reported to the
        log channel in one message before this returns, so the caller never posts them there
        again."""
        from leaguebot.image.services.driver_portrait_service import refresh_before_render
        from leaguebot.image.services.image_lineup_post import build_drawing
        from leaguebot.image.services.image_lineup_service import (
            build_fill_spec as build_lineup_fill_spec,
        )

        _division, drawing, members = await build_drawing(self._db_path, guild, division_id)

        config = await self._config.get_config()
        directories, directory_faults = resolve_configured_directories(
            config,
            (
                ("team", "team_image_directory"),
                ("flag", "flag_directory"),
                ("driver", "driver_image_directory"),
            ),
            image_type=LINEUP_TEMPLATE_KEY,
        )

        await refresh_before_render(
            self._db_path,
            members,
            config=config,
            directory=directories.get("driver"),
            obtain_missing=False,
        )

        outcome = await self._renderer.render(
            LINEUP_TEMPLATE_KEY,
            spec_builder_with_faults(
                build_lineup_fill_spec, drawing, directories, directory_faults
            ),
            filename_stem=stem_for_drawing(drawing, LINEUP_TEMPLATE_KEY),
            division_name=drawing.division_name,
        )

        if outcome.notices:
            await self._post_log(
                ImageRenderService.format_notices(
                    outcome.notices, subject=f"lineup — {_division['name']}"
                )
            )

        problem = outcome.problem.detail if outcome.problem is not None else None
        notices = tuple(each.detail for each in outcome.notices)
        path = outcome.png_paths[0] if outcome.png_paths else None
        return LineupPicture(path=path, problem=problem, notices=notices)

    def discard_picture(self, path: Path | None) -> None:
        """Release a drawn calendar's or lineup's file, whichever way its posting ended."""
        discard_render(path)

    async def artwork_signature(self) -> list:
        """What the league's artwork looks like now, for the season fingerprint's "artwork" area
        (core/services/image_drawing.py).

        A stated exception (architecture.md, "A catch-all error handler"): a configured directory
        that cannot be resolved is signed `[column, value, "unresolvable"]`, with a warning to
        the host's log, and the rest is still signed.

        *Rejected:* narrowing the catch to the errors it expects by name: a fault of any other
        kind would then empty the whole fingerprint, and the review would read as changed in
        every area, when the one part that cannot be resolved must not cost the rest their own
        signature.
        """
        config = await self._config.get_config()
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
            except Exception:  # noqa: BLE001 — a stated exception, see the docstring
                log.warning(
                    "artwork signature: the template directory %s could not be read", directory,
                    exc_info=True,
                )
                signature.append(["template_directory", directory, "unresolvable"])

        for column in sorted(ASSET_DIRECTORIES):
            value = getattr(config, column, None)
            if not value:
                continue
            try:
                signature.append(
                    [column, _directory_signature(resolve_within_project_root(value))]
                )
            except Exception:  # noqa: BLE001 — a stated exception, see the docstring
                log.warning(
                    "artwork signature: the %s directory %s could not be read", column, value,
                    exc_info=True,
                )
                signature.append([column, value, "unresolvable"])

        return signature
