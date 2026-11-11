"""What core asks of the image module.

`ImageDrawing` is the protocol the builder hands `TemplateCapacityService`, `LineupPoster`, the
calendar's posting, and the season approval and mid-season confirmation, so that core imports
nothing of the image module.
The season fingerprint (`take_fingerprint`) is handed it by its callers: the cogs hand it
`bot.image_drawing`, and the approval and the confirmation the drawing the builder gave them. Core
calls these methods and the image module implements them
(`leaguebot.image.services.image_drawing.ImageDrawing`). It is attached to the bot as
`bot.image_drawing` and declared on `LeagueBot`, as results declares `AttendanceAfterReview` for
attendance (architecture.md, "How modules and core fit together").

It holds the four placement capacity questions, the season review's own capacity questions, the
calendar, the lineup (of record, and as a command's output), the portrait discard and the
artwork signature. Each capacity question answers a `CapacityProblem` — the problem as a
sentence with no final stop, and the image module's own words for what to do about it — or
`None` where nothing is wrong. Core joins the two into its own refusal, adding what is core's
own to say. The lineup review question answers bare sentences instead (`list[str]`), since
today's lineup problems carry no remedy of their own. The calendar's `draw_calendar` and the
lineup's `draw_lineup` answer a `CalendarPicture` or `LineupPicture` instead, because core
itself posts what it hands back rather than wording a refusal from it.

It also answers both season reviews' image section and the configuration faults that refuse an
approval: `rasteriser_fault`, `review_section`, `template_faults`, `colour_shortfall_problems`
and `portrait_fault`. The image module words each in full; core places them beneath its own
`"**Image output**"` heading and decides which refuse. `template_faults` lets a fault reading
the templates out, for core to refuse on — a check that never ran is not a pass; `review_section`
lets a fault out, for core to state the section as unreadable; and `colour_shortfall_problems`
and `portrait_fault` are read behind core's own `try`, so a fault in either stands aside rather
than blocking a season it could not read. A switch nobody signed up for (`SwitchNotSignedUp`) is
let out of each, and core raises it: a fault in the bot's assembly is not a reading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CapacityProblem:
    """What a capacity question found wrong, and how a league puts it right.

    *problem* is the image module's sentence naming the overflow, with no final stop. *remedy*
    is the image module's own words for what to do about it, a full sentence. The caller joins
    them, adding what is its own to say.
    """

    problem: str
    remedy: str


@dataclass(frozen=True)
class TeamSeats:
    """One race team of a division, for the lineup review question: its name, and how many
    drivers are seated in it. An empty seat is not counted."""

    name: str
    seated: int


@dataclass(frozen=True)
class DivisionTeams:
    """One division's race teams, in tier order, for the lineup review question. The reserve
    team is not among them: it holds no block of the lineup template's team collection."""

    name: str
    teams: tuple[TeamSeats, ...]


@dataclass(frozen=True)
class Held:
    """What the season review asks a warning question about: the division *name* holding the
    most of whatever is counted, and the count itself."""

    name: str
    count: int


@dataclass(frozen=True)
class CalendarPicture:
    """What drawing one division's calendar produced.

    *path* is the drawn PNG's path, or None where it could not be drawn. *problem* is the image
    module's words for why, with no final stop, or None where it drew. *notices* are the
    drawing's own words for what it survived; `draw_calendar` has already reported them to the
    log channel, so the caller never posts them there a second time — only in a command's own
    output, which is the caller's to decide.
    """

    path: Path | None
    problem: str | None
    notices: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LineupPicture:
    """What drawing one division's lineup produced.

    *path* is the drawn PNG's path, or None where it could not be drawn. *problem* is the image
    module's words for why, with no final stop, or None where it drew. *notices* are the
    drawing's own words for what it survived; `draw_lineup` has already reported them to the
    log channel, so the caller never posts them there a second time.
    """

    path: Path | None
    problem: str | None
    notices: tuple[str, ...] = field(default_factory=tuple)


class ImageDrawing(Protocol):
    """The image module's answers to what core asks of it in order to place a driver and to
    convey a division's calendar and its lineup."""

    async def reserve_capacity_problem(self, reserves: int) -> CapacityProblem | None:
        """Whether *reserves* reserve drivers would outgrow the configured lineup template's
        reserve block, or None. Nothing where the lineup is not drawn (the module off, the
        `lineup` aspect off, or no valid template configured)."""

    async def sheet_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers*, of either kind, would outgrow the configured attendance sheet's
        rows, or None. Nothing where the sheet is not drawn."""

    async def standings_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers*, classified drivers alone, would outgrow the configured driver
        standings template's rows, or None. Nothing where the standings are not drawn."""

    async def declared_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers* seated in the division would outgrow the smallest capacity any
        image template declares in code, or None. Nothing while the images module is off, or
        while no catalogue declares one."""

    async def lineup_review_problems(self, divisions: list[DivisionTeams]) -> list[str]:
        """Which of *divisions* the configured lineup template cannot draw, each as a bare
        sentence with no final stop, naming the division and its teams. An empty list where
        every division fits, or where the lineup is not drawn (the module off, no valid
        template configured). Not gated on the `lineup` aspect: this reports a template that
        cannot draw the season, a fault whether or not that graphic is posted."""

    async def round_capacity_problem(self, would_hold: int) -> CapacityProblem | None:
        """Whether a division holding *would_hold* rounds would outgrow the configured
        calendar template, or None. Nothing where the calendar is not drawn (the module off,
        the `calendar` aspect off, no valid template)."""

    async def calendar_review_problem(self, longest: Held) -> CapacityProblem | None:
        """Whether *longest* — the division holding the season's most rounds — would outgrow
        the configured calendar template, or None. Nothing where the calendar is not drawn."""

    async def sheet_review_problem(self, longest: Held) -> CapacityProblem | None:
        """Whether *longest* — the division holding the season's most rounds — would outgrow
        the configured attendance sheet's round columns, or None. Nothing where the sheet is
        not drawn, or where the template draws no round grid at all."""

    async def standings_review_problems(
        self, drivers: Held, teams: Held, longest: Held
    ) -> list[CapacityProblem]:
        """Which of the two standings templates cannot draw the season: *drivers* against the
        drivers template's rows, *teams* against the constructors template's rows, and
        *longest* against each template's round columns, in that order — drivers rows, drivers
        columns, constructors rows, constructors columns. Each template missing from the
        answer is either not drawn, or one that cannot be counted, which is skipped on its own
        while the other is still answered. Empty where the standings are not drawn at all."""

    async def checkin_review_problem(self, sessions: int) -> CapacityProblem | None:
        """Whether a round run over *sessions* sessions would outgrow the configured check-in
        call template, or None. Nothing where the call is not drawn."""

    async def calendar_wanted(self) -> bool:
        """Whether this league conveys its calendar as a picture: both the module and the
        `calendar` aspect must be on. A gate that cannot be read answers False, so the text
        calendar goes out and a league never loses its calendar to a fault reading a switch; a
        switch nobody signed up for (`SwitchNotSignedUp`) is raised."""

    async def draw_calendar(
        self, division: Any, rounds: Any, tracks: Any, *, season_number: Any = None,
    ) -> CalendarPicture:
        """Draw *division*'s calendar of *rounds*, named against *tracks*. Hands back the
        picture drawn, or the problem naming why it could not be, and the notices it survived —
        already reported to the log channel in one message."""

    async def lineup_wanted(self) -> bool:
        """Whether this league conveys its lineup as a picture: both the module and the
        `lineup` aspect must be on. A gate that cannot be read answers False, so the text
        lineup goes out and a league never loses its lineup to a fault reading a switch; a
        switch nobody signed up for (`SwitchNotSignedUp`) is raised."""

    async def draw_lineup(self, guild: Any, division_id: int) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild*, as the lineup of record. Hands back the
        picture drawn, or the problem naming why it could not be, and the notices it survived
        — already reported to the log channel in one message."""

    async def draw_lineup_for_command(self, guild: Any, division_id: int) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild*, as a command's own output rather than the
        lineup of record: not written to the lineup of record. Hands back the picture
        drawn, or the problem naming why it could not be — worded for the command's own
        refusal rather than the text fallback's — and the notices it survived, already
        reported to the log channel in one message. A fault reading the division itself (the
        database cannot be opened) is let out, for the command's own failure path to report,
        rather than turned into refusal words."""

    async def draw_review_lineup(
        self, guild: Any, division_id: int, *, include_uncommitted: bool,
    ) -> LineupPicture:
        """Draw *division_id*'s lineup in *guild* for a season review, as a command's own output
        rather than the lineup of record: obtaining the portrait of each seated driver who has
        none first, so a review is not judged on a placeholder the league will not see, and with
        the drivers a mid-season confirmation has not yet committed in it where
        *include_uncommitted*. Hands back the picture drawn, or the problem naming why it could
        not be, worded for the command's own refusal, and the notices it survived, already
        reported to the log channel in one message."""

    def discard_picture(self, path: Path | None) -> None:
        """Release a path `draw_calendar`, `draw_lineup`, `draw_lineup_for_command` or
        `draw_review_lineup` handed back, whichever way its posting ended. A path of None is a no-op, covering a picture
        that was never drawn."""

    async def discard_portraits(self, user_ids: Any) -> int:
        """Remove the portraits obtained for *user_ids*, accounts no driver is drawn under any
        longer: one `/driver reassign` has just replaced, or several a season's driver pass has
        deleted. Hands back how many were removed. Never raises."""

    async def artwork_signature(self) -> list:
        """What the league's artwork looks like now: every configured template file, then every
        configured asset folder, each by name, size and modification time, never by content. A
        configured directory that cannot be resolved is signed `[column, value, "unresolvable"]`
        and the rest are still signed (architecture.md, "A catch-all error handler"). An unset
        column is left out, and the answer is an empty list where the module has no
        configuration at all. The season fingerprint digests this answer unchanged, so a review
        compares equal while the artwork stands, and names the templates and artwork as changed
        once it does not."""

    # ── What the season reviews ask of the image configuration ──────────────

    def rasteriser_fault(self) -> str | None:
        """What is wrong with the host's drawing program, or None where nothing is: the line the
        approval and both confirmations refuse on, asked when each is judged so that a host
        losing the program between the review and the button is caught. Synchronous, since it
        reads nothing but the host."""

    async def review_section(self) -> list[str]:
        """The image module's lines for both season reviews, beneath core's own
        `"**Image output**"` heading: the drawing program's absence, each aspect and its
        template's state, the asset directories, and the driver-portrait settings. A fault
        reading the configuration is let out, for core to state the section as unreadable in
        its own words rather than the image module guessing at a partial section. A switch
        nobody signed up for (`SwitchNotSignedUp`) is let out too, and core raises it."""

    async def template_faults(self) -> list[str]:
        """Every broken template a switched-on output draws, each a sentence ending in a full
        stop, naming the template and its plain reason. A template beneath a switched-off
        output is no fault — that output posts as text. A fault reading the templates is let
        out, for core to refuse on: a check that never ran is not a pass."""

    async def colour_shortfall_problems(self) -> list[str]:
        """Every per-tier colour a template wants and no tier has set, flattened from the same
        map `/images config view` renders, one line each naming its template, templates in
        order of their key. A fault reading the shortfall is let out, for core to stand aside
        on: a reader that cannot answer must not be the thing that refuses a season."""

    async def portrait_fault(self) -> str | None:
        """The driver-portrait settings fault that would withhold approval, or None: portraits
        obtained from Discord with neither pre-render nor daily updates enabled would never
        fetch one, and so is a fault wherever the images module is on; every other combination,
        and the module off, is none. A fault reading the configuration is let out, for core to
        stand aside on: a season is never blocked on settings that could not be read. A switch
        nobody signed up for (`SwitchNotSignedUp`) is let out too, and core raises it."""
