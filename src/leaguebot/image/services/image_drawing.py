"""The image module's answers to core's drawing protocol.

`ImageDrawing` implements `leaguebot.core.services.image_drawing.ImageDrawing`: the builder
makes one, attaches it to the bot as `bot.image_drawing`, and hands it to `PlacementService` and
to every change that posts a division's calendar, so that core never imports this module. The
capacity questions ask whether the aspect is drawn at all, whether the template is valid, and
whether it holds that many — answered off the event loop, since parsing and measuring a
template takes real time. ``calendar_wanted`` and ``draw_calendar`` answer in the same shape:
the two gates, the asset directories resolved absolutely, the file named, the render, and the
notices reported to the log channel in one message.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from leaguebot.core.services.image_drawing import CalendarPicture, CapacityProblem
from leaguebot.core.services.module_service import ModuleService
from leaguebot.image.models.image_catalogues import (
    declared_capacities,
    reserve_capacity_problem,
    row_capacity_problem,
)
from leaguebot.image.models.image_constants import TEMPLATE_LABELS
from leaguebot.image.services.image_attendance_post import (
    ATTENDANCE_ASPECT,
    ATTENDANCE_TEMPLATE_KEY,
)
from leaguebot.image.services.image_config_service import ImageConfigService
from leaguebot.image.services.image_lineup_post import LINEUP_TEMPLATE_KEY
from leaguebot.image.services.image_render_service import (
    ImageRenderService,
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

#: The calendar's own aspect and template key.
CALENDAR_ASPECT = "calendar"
CALENDAR_TEMPLATE_KEY = "calendar_template"


class ImageDrawing:
    """Core's drawing protocol, implemented over the image module's own services."""

    def __init__(
        self,
        modules: ModuleService,
        config: ImageConfigService,
        validity: ImageValidityService,
        *,
        renderer: Any = None,
        post_log: Any = None,
    ) -> None:
        self._modules = modules
        self._config = config
        self._validity = validity
        self._renderer = renderer
        self._post_log = post_log

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
        except Exception:  # noqa: BLE001
            log.exception("calendar: could not read the image gates")
            return False

    async def draw_calendar(
        self, division: Any, rounds: Any, tracks: Any, *, season_number: Any = None,
    ) -> CalendarPicture:
        """Draw *division*'s calendar of *rounds*, named against *tracks*: the two gates
        already asked through `calendar_wanted`, the asset directories resolved absolutely, the
        file named, and the render. The notices are reported to the log channel in one message
        before this returns, so the caller never posts them there again."""
        from leaguebot.image.services.image_calendar_service import (
            build_fill_spec,
            resolve_drawing,
        )

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

    def discard_picture(self, path: Path | None) -> None:
        """Release a drawn calendar's file, whichever way its posting ended."""
        from leaguebot.image.services.image_render_service import discard_render

        discard_render(path)
