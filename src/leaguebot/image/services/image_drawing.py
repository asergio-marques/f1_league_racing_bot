"""The image module's answers to core's drawing protocol (#283, plan S12).

`ImageDrawing` implements `leaguebot.core.services.image_drawing.ImageDrawing`: the builder
makes one, attaches it to the bot as `bot.image_drawing`, and hands it to `PlacementService` and
the other callers S12 brings to it, so that core never imports this module. Each method is a
capacity question: is the aspect drawn at all, is the template valid, and does it hold that many?
Answered off the event loop, since parsing and measuring a template takes real time.
"""
from __future__ import annotations

from leaguebot.core.services.image_drawing import CapacityProblem
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
from leaguebot.image.services.image_standings_post import DRIVERS_TEMPLATE_KEY
from leaguebot.image.services.image_validity_service import ImageValidityService
from leaguebot.image.utils.svg_document import load_svg, off_the_loop

#: The lineup's one template, named here as the other aspects name their own (image_lineup_post's
#: LINEUP_TEMPLATE_KEY, image_attendance_post's ATTENDANCE_TEMPLATE_KEY).
LINEUP_TEMPLATE_KEY = "lineup_template"

_RESERVE_REMEDY = "Enlarge the template, or turn the `lineup` image aspect off with `/images config toggle`."
_SHEET_REMEDY = "Enlarge the template, or the sheet would silently drop a driver."
_STANDINGS_REMEDY = "Enlarge the template, or the standings would silently drop a driver."
_DECLARED_REMEDY = "Enlarge that template, or disable the images module, before adding another driver."


class ImageDrawing:
    """Core's drawing protocol, implemented over the image module's own services."""

    def __init__(
        self,
        modules: ModuleService,
        config: ImageConfigService,
        validity: ImageValidityService,
    ) -> None:
        self._modules = modules
        self._config = config
        self._validity = validity

    async def _valid_report(self, aspect: str, template_key: str):
        """The template's report where *aspect* is drawn and the template is valid, else None."""
        if not await self._modules.is_images_enabled():
            return None
        toggles = await self._config.get_toggles()
        if not toggles.get(aspect):
            return None
        reports = await self._validity.template_reports()
        report = reports.get(template_key)
        if report is None or not report.valid or report.resolved_path is None:
            return None
        return report

    async def reserve_capacity_problem(self, reserves: int) -> CapacityProblem | None:
        report = await self._valid_report("lineup", LINEUP_TEMPLATE_KEY)
        if report is None:
            return None
        path = report.resolved_path
        problem = await off_the_loop(
            lambda: reserve_capacity_problem(load_svg(path), reserves)
        )
        if problem is None:
            return None
        return CapacityProblem(problem=problem, remedy=_RESERVE_REMEDY)

    async def sheet_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        report = await self._valid_report(ATTENDANCE_ASPECT, ATTENDANCE_TEMPLATE_KEY)
        if report is None:
            return None
        path = report.resolved_path
        problem = await off_the_loop(
            lambda: row_capacity_problem(ATTENDANCE_TEMPLATE_KEY, load_svg(path), drivers)
        )
        if problem is None:
            return None
        return CapacityProblem(problem=problem, remedy=_SHEET_REMEDY)

    async def standings_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        report = await self._valid_report("standings", DRIVERS_TEMPLATE_KEY)
        if report is None:
            return None
        path = report.resolved_path
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
