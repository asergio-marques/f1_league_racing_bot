"""Post one verdict as a graphic (043, US2 and US3).

**A verdict is static** (Constitution XIV.17), on the second ground that rule admits: it draws
a **record of a decision taken**, not a view of a state. Every value was settled at the moment
the decision was issued, and a later change of the world does not falsify it — a driver
renaming their Discord account does not make the verdict wrong. A correction arrives as a
verdict of its own, beside the first.

Two consequences shape this module:

* it **persists nothing**. It records no message id (the job that posts the verdict records the
  message it was posted as, for the review's replay), and nothing is edited, replaced or deleted,
  so XIV.8's delete-and-repost does not arise in any form. There is no state to reconcile on
  the fallback path either — a failed render simply leaves the textual announcement to be sent
  at the same point.
* the graphic **displaces the whole announcement but the mention** (XIV.7). Heading, driver
  line, sanction, description and justification all move onto the canvas; the message keeps the
  mention alone, a mention being the one thing a picture cannot carry (XIV.16).

**A verdict never gates a sanction.** Nothing here is reached before the review is finalised or
the sanction enforced. The graphic is downstream of every state change it depicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from leaguebot.core.services.drivers.driver_names import (
    read_driver_names,
    read_driver_records,
    read_nationality_collected,
)
from leaguebot.core.models.fault_source import FaultWords, source_of
from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.services.aspects.image_verdict_service import VerdictDrawing, VerdictKind

if TYPE_CHECKING:
    from leaguebot.framework.utils.hooks import AwaitedHook
    from leaguebot.image.services.config.image_config_service import ImageConfigService
    from leaguebot.image.services.render.image_render_service import ImageRenderService

log = logging.getLogger(__name__)

VERDICTS_ASPECT = "verdicts"
VERDICTS_TEMPLATE_KEY = "verdicts_template"

#: The toggle's name in `/images config toggle` ("Verdicts") and what it draws, which heads a
#: fault's line.
VERDICTS_TOGGLE_NAME = "Verdicts image"


@dataclass
class VerdictRender:
    """What the image path produced for one verdict.

    *png* is None wherever the textual announcement should be posted instead — the module off,
    the aspect off, the template invalid, or the render having failed. The caller need not know
    which: every one means "post the text you were going to post anyway".
    """

    png: Path | None = None
    notices: list = field(default_factory=list)
    problem: str | None = None
    rejects: bool = False

    @property
    def draws(self) -> bool:
        return self.png is not None


async def _round_context(db_path: str, round_id: int) -> dict:
    """The tier, format and grand prix name of a round — what the announcement omits.

    Read exactly as the weather graphic reads them, so the two cannot disagree about what a
    round is called.

    A stated exception (architecture.md, "A catch-all error handler"): a round whose context
    cannot be read answers nothing, and the verdict is drawn without the round's tier, format
    and grand prix, because an optional field is not worth a failed picture. The full error goes
    to the host's log. It reads no switch.
    """
    from leaguebot.core.services.rounds.round_context import round_context

    try:
        context = await round_context(db_path, round_id)
    except Exception as exc:  # noqa: BLE001 — an optional field is not worth a failed render
        log.warning("verdicts: round context unreadable for round %s: %s", round_id, exc, exc_info=True)
        return {}
    if context is None:
        return {}
    return {
        "division_tier": context.division.tier,
        "round_format": context.format,
        "race_name": context.gp_name,
        "season_id": context.division.season_id,
    }


async def _driver_nationality(
    db_path: str,
    driver_names_read: AwaitedHook,
    discord_user_id: int,
    season_id: int | None = None,
) -> str | None:
    """The nationality recorded for the driver — the datum a flag is resolved from.

    Read through core's reader of a driver's record, from the driver's signup to *season_id*,
    where one is named, or their latest where they have none to it (issue #220); a fake driver's
    is read from its signup as any driver's.

    The driver is found by any account they have held, as a verdict names the account the
    result was recorded under, which may be one they have since left (issue #243).

    A stated exception (architecture.md, "A catch-all error handler"): a nationality that cannot
    be read answers None, and the verdict is drawn without the driver's flag, because an optional
    field is not worth a failed picture. The full error goes to the host's log. It reads no
    switch.
    """
    try:
        records = await read_driver_records(
            db_path, driver_names_read, [discord_user_id], season_id=season_id, any_account=True
        )
    except Exception:  # noqa: BLE001 — the verdict is drawn without the driver's flag
        log.warning(
            "verdict image: the nationality of %s could not be read", discord_user_id,
            exc_info=True,
        )
        return None
    record = records.get(str(discord_user_id))
    return record.nationality if record is not None else None


async def _mention_names(
    db_path: str, driver_names_read: AwaitedHook, guild, *, driver_discord_id: int, driver_name: str, texts
) -> dict[str, str]:
    """The name each mention in *texts* is drawn as, keyed by the id it addresses (#142).

    A graphic cannot carry a live mention, so a `<@id>` a steward wrote into the description
    or the justification is swapped for a name before it is drawn. The name is **that person's**
    — the driver the mention addresses — resolved by the module's own reader, called rather
    than restated, so one driver is one name wherever the module names them.

    Resolving every mention to the penalised driver instead, which is what this replaces, drew
    a justification reading "contact with @Bob at turn 3" as a statement that the penalised
    driver had collided with themselves: right as text, wrong as a picture, and confidently
    wrong once #141 made the substituted name a real one.

    Three things it holds to, each pinned in tests/image/test_image_verdict_mentions.py:

    * **The penalised driver keeps the name on the driver line.** Seeded here rather than read,
      so a mention of them beneath their own name never draws a second rendering of it.
    * **One read serves every mention**, the ids being collected before anything is substituted
      — `resolve_mentions` is synchronous and could otherwise only read one mention at a time.
    * **An unknown id draws as itself.** The chain ends at the user id anyway, and a number a
      reader can look up beats the wrong person's name. This is also what an unreadable lookup
      degrades to: a name is not worth a lost verdict, as `_graphic_name` already has it.

    A stated exception (architecture.md, "A catch-all error handler"): names that cannot be read
    leave the mentions drawn as the ids they address, because an optional field is not worth a
    failed picture. The full error goes to the host's log. It reads no switch.
    """
    from leaguebot.image.services.aspects.image_verdict_service import mention_ids

    names: dict[str, str] = {}
    if driver_name and str(driver_name).strip():
        names[str(driver_discord_id)] = str(driver_name).strip()

    wanted = [user_id for user_id in mention_ids(*texts) if user_id not in names]
    if not wanted:
        return names

    try:
        resolved = await read_driver_names(
            db_path, driver_names_read, guild, [int(user_id) for user_id in wanted]
        )
    except Exception as exc:  # noqa: BLE001 — a name is not worth a failed render
        log.warning("verdicts: mention names unreadable: %s", exc, exc_info=True)
        return names

    for user_id in wanted:
        value = resolved.get(int(user_id))
        if value and str(value).strip():
            names[user_id] = str(value).strip()
    return names


async def build_drawing(
    *,
    guild,
    db_path: str,
    driver_names_read: AwaitedHook,
    round_id: int,
    kind: VerdictKind,
    season_number,
    division_name: str,
    round_number,
    session_label: str | None,
    driver_name: str,
    driver_discord_id: int,
    penalty_description: str,
    description_text: str,
    justification_text: str,
    team_name: str | None = None,
    team_key: str | None = None,
) -> VerdictDrawing:
    """Resolve one verdict into the values its graphic draws.

    Everything here is *read*. Nothing is computed and nothing decided: the sanction is the
    rendering the announcement service produced, the session label is the one it used, and the
    name is resolved by the chain every graphic of the module resolves a person by.

    *guild* is the server the verdict is posted to, and is what a mention in a steward's text
    is resolved against. Keyword-only, as everything else here is, and required rather than
    defaulted: a caller that forgets it should say so at the call site and not by quietly
    naming drivers worse.
    """
    from leaguebot.image.services.aspects.image_verdict_service import resolve_mentions

    context = await _round_context(db_path, round_id)
    nationality = await _driver_nationality(
        db_path, driver_names_read, driver_discord_id, context.get("season_id")
    )

    # Whether the league collects nationality at all. A league that switched it off draws no
    # flag and is told nothing (XIV.4's configured absence); one that collects it and holds
    # none for this driver has an ordinary emptied optional field, and is told.
    collected = await read_nationality_collected(db_path, driver_names_read)

    # A mention a person wrote into free text is resolved in place to the name it addresses —
    # the driver that mention names, and not the driver being sanctioned (#142). The graphic
    # mentions nobody (XIV.16, v4.8.0).
    names = await _mention_names(
        db_path,
        driver_names_read,
        guild,
        driver_discord_id=driver_discord_id,
        driver_name=driver_name,
        texts=(description_text, justification_text),
    )

    def _name_for(user_id: str) -> str:
        return names.get(str(user_id), str(user_id))

    race_name = context.get("race_name")
    if str(context.get("round_format") or "").upper() == "MYSTERY":
        race_name = "Mystery Grand Prix"

    return VerdictDrawing(
        kind=kind,
        season_number=season_number,
        division_name=division_name,
        division_tier=context.get("division_tier"),
        round_number=round_number,
        race_name=race_name,
        session_name=session_label if kind is not VerdictKind.ATTENDANCE_SANCTION else None,
        driver_name=driver_name,
        driver_nationality=nationality,
        team_name=team_name if kind is not VerdictKind.ATTENDANCE_SANCTION else None,
        team_slug_source=team_key if kind is not VerdictKind.ATTENDANCE_SANCTION else None,
        penalty=penalty_description,
        description=resolve_mentions(description_text, _name_for),
        justification=resolve_mentions(justification_text, _name_for),
        nationality_collected=collected,
    )


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {VERDICTS_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, render: VerdictRender) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to.

    The problem's line is marked with where its fault lies (`FaultWords`): the posting module that
    writes the line adds what puts it right, as the picture's fault calls for.
    """
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if render.notices:
        lines.append(ImageRenderService.format_notices(render.notices, subject=what))
    if render.problem:
        lines.append(FaultWords(fault(what, render.problem), source_of(render.problem)))
    return tuple(lines)


async def render_verdict(
    config_service: ImageConfigService,
    renderer: ImageRenderService,
    drawing: VerdictDrawing,
    *,
    origin: PostingOrigin = PostingOrigin.SCHEDULED,
) -> VerdictRender:
    """Render *drawing* to a PNG, or report why the textual announcement should stand.

    A fault in resolving the directories or building the fill is raised, for the verdict's own
    post to say why the text stands.
    """
    from leaguebot.image.services.aspects.image_verdict_service import build_fill_spec
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.utils.image_naming import stem_for_drawing

    config = await config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("flag", "flag_directory"),
            ("team", "team_image_directory"),
        ),
        image_type=VERDICTS_TEMPLATE_KEY,
    )

    decision = await renderer.render_for_posting(
        VERDICTS_TEMPLATE_KEY,
        spec_builder_with_faults(build_fill_spec, drawing, directories, directory_faults),
        posting_origin=origin,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing, VERDICTS_TEMPLATE_KEY),
    )

    if decision.rejects:
        return VerdictRender(
            problem=(decision.problem.words if decision.problem else None),
            rejects=True,
            notices=decision.notices,
        )

    if not decision.posts_image:
        return VerdictRender(
            problem=(decision.problem.words if decision.problem else None),
            notices=decision.notices,
        )

    return VerdictRender(png=decision.png_paths[0], notices=decision.notices)


def describe(
    *,
    division_name: str,
    round_number,
    session_name: str | None,
    driver_name: str,
    season_number=None,
) -> str:
    """Name the subject a notice pertains to: season, division, round, session and driver."""
    parts = []
    if season_number is not None:
        parts.append(f"Season {season_number}")
    parts.append(str(division_name))
    parts.append(f"Round {round_number}")
    parts.append(str(session_name) if session_name else "Attendance Sanction")
    parts.append(str(driver_name))
    return " · ".join(parts)
