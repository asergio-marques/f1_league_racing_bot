"""The image module's answers to core's drawing protocol.

`ImageDrawing` implements `leaguebot.core.services.modules.image_drawing.ImageDrawing`: the builder
makes one, attaches it to the bot as `bot.image_drawing`, and hands it to
`TemplateCapacityService` and `LineupPoster`, to every change that posts a division's calendar,
to `/driver reassign`'s change, and to the season approval and mid-season confirmation; the
season fingerprint (`take_fingerprint`) is handed it by its callers, and the season review's and
`/team lineup`'s cogs reach it on the bot. So core never imports this module. The capacity
questions ask whether the aspect is drawn at all, whether the template is valid, and whether it
holds that many — answered off the event loop, since parsing and measuring a template takes real
time. ``calendar_wanted``/
``draw_calendar`` and ``lineup_wanted``/``draw_lineup`` answer in the same shape: the two gates,
the asset directories resolved absolutely, the file named, the render, and the notices reported
to the log channel in one message. ``artwork_signature`` answers what the league's artwork
looks like now, for the fingerprint to digest. ``draw_lineup_for_command`` draws a lineup as a
command's output, ``draw_review_lineup`` draws it for a season review (through
`image_lineup_post.render_for_command`, so it holds the bot), and ``discard_portraits`` removes the
portraits of accounts no longer drawn.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot
    from leaguebot.image.models.image_catalogues import FieldCatalogue

from leaguebot.core.services.modules.image_drawing import (
    CalendarPicture,
    CapacityProblem,
    DivisionTeams,
    Held,
    LineupPicture,
)
from leaguebot.core.services.modules.module_service import ModuleService, SwitchNotSignedUp
from leaguebot.core.utils.paths import resolve_within_project_root
from leaguebot.image.models.image_catalogues import (
    catalogue_for,
    declared_capacities,
    reserve_capacity_problem,
    row_capacity_problem,
)
from leaguebot.image.models.image_constants import (
    ASPECT_LABELS,
    ASSET_DIRECTORIES,
    ASSET_LABELS,
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
from leaguebot.image.services.image_lineup_post import LINEUP_TEMPLATE_KEY, render_for_command
from leaguebot.image.services.image_render_service import (
    ImageRenderService,
    discard_render,
    resolve_configured_directories,
    spec_builder_with_faults,
)
from leaguebot.image.services.image_rsvp_post import RSVP_ASPECT, RSVP_TEMPLATE_KEY
from leaguebot.image.services.image_standings_post import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
)
from leaguebot.image.services.image_validity_service import (
    ImageValidityService,
    plain_reason,
    plain_remedy,
    templates_of_enabled_aspects,
)
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

#: The season review's own remedies (XIV.9): a warning names the one way out, enlarging the
#: template, since the aspect is already on and turning it off would lose the picture outright
#: rather than just the one division it cannot draw.
_REVIEW_REMEDY = "Enlarge the template to draw it."

#: The round guard's own remedy (XIV.12): naming both ways out, since enlarging the template
#: and turning the aspect off are genuinely different decisions a league may reasonably take.
_ROUND_REMEDY = (
    "Enlarge the template, or turn the `calendar` image aspect off with `/images config toggle`."
)

#: The calendar's own aspect.
CALENDAR_ASPECT = "calendar"

#: The standings' own aspect.
STANDINGS_ASPECT = "standings"


def _rows_and_rounds(catalogue: FieldCatalogue, path: Path) -> tuple[int, int]:
    """The rows and the round columns a standings template at *path* draws, nought for
    none.

    Parsed and counted here, in the worker thread the review runs it in, so that the tree
    never leaves the thread that parsed it (`load_svg`). Raises `CapacityError` for a
    template that cannot be counted.
    """
    from leaguebot.image.utils.svg_document import load_svg

    root = load_svg(path)
    return catalogue.capacity(root) or 0, catalogue.column_capacity(root) or 0


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
        bot: LeagueBot | None = None,
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
        #: The bot, which the review's lineup alone needs: `render_for_command` reads the image
        #: services off it as it draws. Optional so that a drawing built for the other answers
        #: (the capacity questions, the calendar, the lineup of record) needs none. It stays
        #: until image's design pass (#288) removes `render_for_command`'s lookups on the bot.
        self._bot = bot

    async def _drawn_template(self, aspect: str, template_key: str) -> Path | None:
        """The template's path where the module and *aspect* are both drawn and the template
        is valid, else None.

        For the questions that gate what is actually posted: a graphic that will not be drawn
        has no capacity to outgrow.
        """
        if not await self._modules.is_images_enabled():
            return None
        return await self._reviewed_template(aspect, template_key)

    async def _reviewed_template(self, aspect: str, template_key: str) -> Path | None:
        """The template's path where *aspect* is drawn and the template is valid, else None —
        not gated on the module.

        For the review's warnings: they report a template that cannot draw the season
        whether or not the images module itself ends up posting anything, since the warning
        is about what the artwork declares, not what is drawn this time (as
        `standings_review_problems` is gated already).
        """
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
        # unpatched function (as this module's review questions do, for the same reason).
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

    # ── The season review's capacity questions ──────────────────────────────

    async def lineup_review_problems(self, divisions: list[DivisionTeams]) -> list[str]:
        """Not gated on the `lineup` aspect (FR-023): this reports a template that cannot
        draw the season, a fault whether or not that graphic is posted, and gated on the
        module alone — a league with no templates configured has nothing to measure."""
        if not await self._modules.is_images_enabled():
            return []
        reports = await self._validity.template_reports()
        report = reports.get(LINEUP_TEMPLATE_KEY)
        if report is None or not report.valid or report.resolved_path is None:
            # An unusable template is already reported by the review's own render, which
            # withholds the approve button; naming it twice would tell a manager nothing new.
            return []

        from leaguebot.image.utils.svg_document import FieldIndex, load_svg

        def measure(path: Path) -> tuple[int, Any]:
            # Parsed and read in one worker thread: the tree never leaves it (`load_svg`).
            root = load_svg(path)
            catalogue = catalogue_for(LINEUP_TEMPLATE_KEY)
            return catalogue.capacity(root) or 0, FieldIndex(root).declared()

        blocks, declared = await off_the_loop(measure, report.resolved_path)
        catalogue = catalogue_for(LINEUP_TEMPLATE_KEY)
        nest = catalogue.rows.nested if catalogue.rows is not None else None

        problems: list[str] = []
        for division in divisions:
            teams = division.teams
            if len(teams) > blocks:
                dropped = ", ".join(f"**{team.name}**" for team in teams[blocks:][:8])
                problems.append(
                    f"division **{division.name}** fields {len(teams)} teams but the "
                    f"lineup template declares {blocks} "
                    f"{'block' if blocks == 1 else 'blocks'}; {dropped} would be dropped"
                )
                continue

            if nest is None:
                continue
            for ordinal, team in enumerate(teams, start=1):
                slots = nest.declared_capacity(f"team_{ordinal}", declared)
                if team.seated > slots:
                    problems.append(
                        f"division **{division.name}**: **{team.name}** seats "
                        f"{team.seated} drivers but block {ordinal} of the lineup "
                        f"template declares {slots} seat "
                        f"{'slot' if slots == 1 else 'slots'}"
                    )
        return problems

    async def round_capacity_problem(self, would_hold: int) -> CapacityProblem | None:
        """The round guard (XIV.12): naming both ways out, since enlarging the template and
        turning the `calendar` aspect off are genuinely different decisions."""
        path = await self._drawn_template(CALENDAR_ASPECT, CALENDAR_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        capacity = await off_the_loop(
            lambda: catalogue_for(CALENDAR_TEMPLATE_KEY).capacity(load_svg(path))
        )
        if not capacity or would_hold <= capacity:
            return None
        problem = (
            f"That would give the division {would_hold} rounds, but the configured "
            f"calendar template draws {capacity}"
        )
        return CapacityProblem(problem=problem, remedy=_ROUND_REMEDY)

    async def calendar_review_problem(self, longest: Held) -> CapacityProblem | None:
        """The review's warning (XIV.9): the calendar is posted as text rather than not at
        all, so the division holding the longest calendar is named, never refused.

        Gated on the `calendar` aspect and a valid template, not on the module: the warning
        is about what the configured template declares, whether or not a calendar is drawn
        this time (`_reviewed_template`)."""
        path = await self._reviewed_template(CALENDAR_ASPECT, CALENDAR_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        capacity = await off_the_loop(
            lambda: catalogue_for(CALENDAR_TEMPLATE_KEY).capacity(load_svg(path))
        )
        if not capacity or longest.count <= capacity:
            return None
        problem = (
            f"Calendar template draws {capacity} round(s); `{longest.name}` holds "
            f"{longest.count}. That division's calendar cannot be drawn as an image and "
            f"will be posted as text"
        )
        return CapacityProblem(problem=problem, remedy=_REVIEW_REMEDY)

    async def sheet_review_problem(self, longest: Held) -> CapacityProblem | None:
        """The sheet's round columns against the season's longest calendar (XIV.9). A
        template drawing no round grid at all is a legitimate choice (XIV.3), not a problem.

        Gated on the `attendance` aspect and a valid template, not on the module
        (`_reviewed_template`), as `calendar_review_problem` is."""
        path = await self._reviewed_template(ATTENDANCE_ASPECT, ATTENDANCE_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        declared = await off_the_loop(
            lambda: catalogue_for(ATTENDANCE_TEMPLATE_KEY).column_capacity(load_svg(path))
        )
        if not declared or longest.count <= declared:
            return None
        problem = (
            f"Attendance sheet template draws {declared} round column(s); "
            f"`{longest.name}` holds {longest.count}. That division's sheet cannot be "
            f"drawn as an image and will be posted as text"
        )
        return CapacityProblem(problem=problem, remedy=_REVIEW_REMEDY)

    async def standings_review_problems(
        self, drivers: Held, teams: Held, longest: Held
    ) -> list[CapacityProblem]:
        """The two standings templates against the season (FR-043, FR-045): each reported
        apart, never as one pair — a manager told 'the standings template' would not know
        which to enlarge. An uncountable template is skipped on its own; the other is still
        answered."""
        if not await self._config.is_aspect_enabled(STANDINGS_ASPECT):
            return []
        reports = await self._validity.template_reports()

        from leaguebot.image.models.image_catalogues import CapacityError

        problems: list[CapacityProblem] = []
        for template_key, held in (
            (DRIVERS_TEMPLATE_KEY, drivers),
            (CONSTRUCTORS_TEMPLATE_KEY, teams),
        ):
            report = reports.get(template_key)
            if report is None or not report.valid or not report.resolved_path:
                continue
            label = TEMPLATE_LABELS.get(template_key, template_key)
            catalogue = catalogue_for(template_key)

            try:
                declared_rows, declared_rounds = await off_the_loop(
                    _rows_and_rounds, catalogue, report.resolved_path
                )
            except CapacityError:
                continue  # an uncountable template is skipped; the other is still answered

            if declared_rows and held.count > declared_rows:
                problems.append(CapacityProblem(
                    problem=(
                        f"**{label}** draws {declared_rows} row(s); `{held.name}` holds "
                        f"{held.count}. That division's standings cannot be drawn as an "
                        f"image and will be posted as text"
                    ),
                    remedy=_REVIEW_REMEDY,
                ))

            # Nought round columns means the template draws no season grid, which is a
            # legitimate choice (XIV.3) and never a divergence.
            if declared_rounds and longest.count > declared_rounds:
                problems.append(CapacityProblem(
                    problem=(
                        f"**{label}** draws {declared_rounds} round column(s); "
                        f"`{longest.name}` holds {longest.count}. That division's "
                        f"standings cannot be drawn as an image and will be posted as text"
                    ),
                    remedy=_REVIEW_REMEDY,
                ))
        return problems

    async def checkin_review_problem(self, sessions: int) -> CapacityProblem | None:
        """The check-in call's sessions against the season's most demanding round (XIV.9).

        Gated on the `rsvp` aspect and a valid template, not on the module
        (`_reviewed_template`), as `calendar_review_problem` is."""
        path = await self._reviewed_template(RSVP_ASPECT, RSVP_TEMPLATE_KEY)
        if path is None:
            return None
        from leaguebot.image.utils.svg_document import load_svg

        declared = await off_the_loop(
            lambda: catalogue_for(RSVP_TEMPLATE_KEY).capacity(load_svg(path))
        )
        if not declared or sessions <= declared:
            return None
        problem = (
            f"Check-in template names {declared} session(s); this season holds a round "
            f"run over {sessions}. That call cannot be drawn as an image and will be "
            f"posted without one"
        )
        return CapacityProblem(problem=problem, remedy=_REVIEW_REMEDY)

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
        except SwitchNotSignedUp:
            raise  # a module that never signed up is a fault in the bot's assembly, not a reading
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
        except SwitchNotSignedUp:
            raise  # a module that never signed up is a fault in the bot's assembly, not a reading
        except Exception:  # noqa: BLE001 — a stated exception, see the docstring
            log.exception("lineup: could not read the image gates")
            return False

    async def _render_lineup(self, guild: Any, division_id: int) -> Any:
        """The gates already asked through `lineup_wanted`, the division's committed seats
        read from the database, the portraits refreshed at the league's own trigger, and the
        render: what `draw_lineup` and `draw_lineup_for_command` both need before them part.
        The notices are reported to the log channel in one message before this returns, so
        neither caller posts them there again. Hands back the division row and the render's
        outcome, for each to word the problem its own way."""
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

        return _division, outcome

    async def draw_lineup(self, guild: Any, division_id: int) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild*, as the lineup of record: the problem, where
        one is met, is its bare detail — the words the text lineup's fallback line carries."""
        _division, outcome = await self._render_lineup(guild, division_id)

        problem = outcome.problem.detail if outcome.problem is not None else None
        notices = tuple(each.detail for each in outcome.notices)
        path = outcome.png_paths[0] if outcome.png_paths else None
        return LineupPicture(path=path, problem=problem, notices=notices)

    async def draw_lineup_for_command(self, guild: Any, division_id: int) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild*, as a command's output: the problem, where
        one is met, names the lineup and the field at fault before the detail, and an internal
        fault, which no league can act on, is never detailed (`Problem.message`)."""
        _division, outcome = await self._render_lineup(guild, division_id)

        problem = outcome.problem.message("the lineup") if outcome.problem is not None else None
        notices = tuple(each.detail for each in outcome.notices)
        path = outcome.png_paths[0] if outcome.png_paths else None
        return LineupPicture(path=path, problem=problem, notices=notices)

    async def draw_review_lineup(
        self, guild: Any, division_id: int, *, include_uncommitted: bool,
    ) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild* for a season review: `render_for_command`
        asked to obtain each seated driver's missing portrait, and to include the uncommitted
        drivers where *include_uncommitted*. It is the one method that holds the bot, because
        `render_for_command` looks the image services up on it; a drawing built without the bot
        cannot, and says so."""
        if self._bot is None:
            raise RuntimeError("this drawing was built without the bot, so it cannot draw a review's lineup")
        outcome = await render_for_command(
            self._bot, guild, division_id,
            include_uncommitted=include_uncommitted, obtain_missing_portraits=True,
        )
        return LineupPicture(
            path=outcome.png_path, problem=outcome.message,
            notices=tuple(each.detail for each in outcome.notices),
        )

    def discard_picture(self, path: Path | None) -> None:
        """Release a drawn calendar's or lineup's file, whichever way its posting ended."""
        discard_render(path)

    async def discard_portraits(self, user_ids: Any) -> int:
        """Remove the portraits obtained for *user_ids*, through
        `driver_portrait_service.discard_portraits`."""
        from leaguebot.image.services.driver_portrait_service import discard_portraits

        return await discard_portraits(self._db_path, self._config, user_ids)

    async def artwork_signature(self) -> list:
        """What the league's artwork looks like now, for the season fingerprint's "artwork" area
        (core/services/modules/image_drawing.py).

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

    # ── What the season reviews ask of the image configuration ──────────────

    def rasteriser_fault(self) -> str | None:
        """What is wrong with the host's drawing program, in these words, or None where there is
        nothing wrong (core/services/modules/image_drawing.py)."""
        from leaguebot.image.services.image_render_service import (
            CONVERTER_NAME,
            converter_available,
        )

        if converter_available():
            return None
        return f"{CONVERTER_NAME} is not installed on this host."

    async def review_section(self) -> list[str]:
        """Both season reviews' image section, beneath core's own heading
        (core/services/modules/image_drawing.py): built from the same `AspectStatus` list
        `/images config view` renders, so a divergence between the two surfaces is impossible
        by construction. Template detail is summarised rather than repeated in full — a season
        review is already long, and `/images config view` is where the sixteen lines belong.

        Lets a fault reading the configuration out, for the caller to state the section as
        unreadable in its own words.
        """
        from leaguebot.image.models.image_module import STATE_DISABLED, STATE_ENABLED

        lines: list[str] = []

        # Imported here, not at module level, so a test's `monkeypatch.setattr` on
        # `image_render_service.converter_available` is picked up (as `rasteriser_fault`'s
        # own import is, for the same reason).
        from leaguebot.image.services.image_render_service import (
            CONVERTER_NAME,
            converter_available,
        )

        if not converter_available():
            lines.append(
                f"  ⛔ {CONVERTER_NAME} is not installed on this host — "
                f"no image will be generated until it is."
            )

        statuses = await self._validity.aspect_statuses()
        reports = await self._validity.template_reports()
        directories = await self._validity.directory_reports()
        config = await self._config.get_config()

        icons = {STATE_ENABLED: "✅", STATE_DISABLED: "❌"}
        for status in statuses:
            icon = icons.get(status.state, "⚠️")
            lines.append(f"  {icon} {ASPECT_LABELS[status.aspect]}")
            for reason in status.reasons:
                lines.append(f"      ↳ {reason}")

        invalid = [r for r in reports.values() if not r.valid]
        lines.append(
            f"  Templates: {len(reports) - len(invalid)}/{len(reports)} resolve"
        )

        # FR-008: name each one. A count tells a manager nothing about what to fix, and
        # this is the report they read before approving — the approval refuses on the
        # same findings, so seeing them here is what lets them act first.
        #
        # Split by whether the aspect drawing the template is switched on, because that
        # is what decides whether the fault stops the season. The confirmation of placements
        # blocks on exactly the first list (`template_faults`), so the heading is a promise
        # the gate keeps rather than a guess. A template beneath a
        # switched-off aspect is still named: it is a real fault a manager will meet the
        # moment they switch that aspect on, and finding it then — after the season is
        # running — is worse than reading it here.
        if invalid:
            drawn = templates_of_enabled_aspects(await self._config.get_toggles())

            def _detail(report) -> str:
                label = TEMPLATE_LABELS.get(report.template_key, report.template_key)
                return (
                    f"      • **{label}**: {plain_reason(report)}. "
                    f"{plain_remedy(report)}"
                )

            blocking = [r for r in reports.values() if not r.valid and r.template_key in drawn]
            dormant = [r for r in reports.values() if not r.valid and r.template_key not in drawn]

            if blocking:
                lines.append("  ⛔ These block approval:")
                lines += [_detail(report) for report in blocking]
            if dormant:
                lines.append(
                    "  ⚠️ These do not block approval — the output that draws them is "
                    "switched off, so nothing posts them. Fix them before switching it on:"
                )
                lines += [_detail(report) for report in dormant]

        # FR-033: where the assets are read from. The paths are configuration a manager
        # sets once and then cannot see anywhere in this report, and a graphic that draws
        # placeholders because a folder was renamed looks exactly like one whose artwork
        # is missing. Naming the directory is what separates the two.
        #
        # Summarised, as the templates above are: the path and whether it resolves. The
        # fault and its remedy belong to `/images config view`, which has room for them.
        if config is not None and directories:
            lines.append("  Asset directories:")
            for column, report in directories.items():
                icon = "✅" if report.valid else "⚠️"
                label = ASSET_LABELS.get(column, column)
                lines.append(f"      {icon} {label}: `{getattr(config, column)}`")
            unresolved = sum(1 for r in directories.values() if not r.valid)
            if unresolved:
                # Deliberately not the "↳" the aspect reasons use: that marker belongs to
                # a reason hanging off one of the eight aspects, and `/images config view`
                # is compared against this report line for line by
                # `test_season_review_and_config_view_agree`. Borrowing it here would make
                # the two surfaces look divergent when they are not.
                lines.append(
                    f"      • {unresolved} of these cannot be read — "
                    f"`/images config view` names the fault."
                )

        lines += await self._portrait_review_lines(config)

        lines.append(f"  _{ImageValidityService.depth_summary(reports)}_")
        return lines

    async def _portrait_review_lines(self, config: Any) -> list[str]:
        """How driver portraits are being obtained, for `review_section`.

        Always stated, including when the feature is off: a manager reading the review is
        deciding whether the season is configured, and "the bot is not obtaining portraits"
        is an answer to that rather than an absence of one.
        """
        from leaguebot.image.services.image_config_service import portrait_configuration_fault

        if config is None or not getattr(config, "use_pfp", False):
            return ["  ❌ Driver portraits from Discord: disabled"]

        triggers = []
        if getattr(config, "pfp_prerender", False):
            triggers.append("before each render")
        if getattr(config, "pfp_daily", False):
            triggers.append(f"daily at {getattr(config, 'pfp_daily_time', '?')} UTC")

        lines = [
            "  ✅ Driver portraits from Discord: enabled"
            + (f" — updated {', and '.join(triggers)}" if triggers else "")
        ]
        fault = portrait_configuration_fault(config)
        if fault is not None:
            lines.append(f"      ⛔ {fault}")
        return lines

    async def template_faults(self) -> list[str]:
        """Every broken template a switched-on output draws (core/services/modules/image_drawing.py).

        Lets a fault reading the templates out, for the caller to refuse on: a check that
        never ran is not a pass.
        """
        reports = await self._validity.template_reports()
        drawn = templates_of_enabled_aspects(await self._config.get_toggles())

        faults: list[str] = []
        for key in sorted(reports):
            report = reports[key]
            if not report.valid and key in drawn:
                label = TEMPLATE_LABELS.get(key, key)
                faults.append(f"Template **{label}**: {plain_reason(report)}.")
        return faults

    async def colour_shortfall_problems(self) -> list[str]:
        """Every per-tier colour a template wants and no tier has set
        (core/services/modules/image_drawing.py), flattened from the same map `/images config view`
        renders, so all three surfaces name the same shortfall in the same words. One answer
        because two surfaces ask it: the review reports what would block an approval and the
        approval refuses on it, so a review that did not look for a missing colour would send a
        manager to an approval that then refused for a fault the review never named.

        Lets a fault reading the shortfall out, for the caller to stand aside on.
        """
        shortfall = await self._validity.colour_shortfall()
        return [
            f"`{template_key}` — {line}"
            for template_key in sorted(shortfall)
            for line in shortfall[template_key]
        ]

    async def portrait_fault(self) -> str | None:
        """The driver-portrait settings fault that would withhold approval, or None
        (core/services/modules/image_drawing.py).

        Lets a fault reading the configuration out, for the caller to stand aside on.
        """
        from leaguebot.image.services.image_config_service import portrait_configuration_fault

        if not await self._modules.is_images_enabled():
            return None
        config = await self._config.get_config()
        return portrait_configuration_fault(config)
