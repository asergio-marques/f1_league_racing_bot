"""The image settings as settings on the change queue (#439, slice 7).

Each `/images` command that changes the module's configuration asks one `image.setting.…` change,
built from a `Setting` below by `setting_change` (`core/services/setting_change.py`, which holds
what every settings change does with a value already held and with a fault). The check of each is
the command's, in today's order and words, run at the ask and again as the change starts, so a
module switched off, a template broken by a directory set ahead of it or an output whose drawings
broke meanwhile is taken into account. Every check refuses first where the Image module is off.

**What a change carries.** Every payload is plain JSON, as the queue stores it: the column or
aspect it sets and the value as it is stored, validated and normalised at the command (a path
inside the project root, a colour in its canonical form), with the name a reply shows beside it
where the member chose it from a list.

**The advice is a step after the save.** What a reply says beside the value (what a directory
resolves to, what the fastest-lap colour reads against the plate) reads the templates and the
files, which a save must not await: it runs as a step of its own, planned only where the save
changed something, and may fail without holding up the queue. A setting is saved first and
advised after, so a fault in the advice leaves it saved and the reply says it could not be checked
(`NOT_CHECKED`). The fastest-lap colour is stored before it is measured (specification FR-026,
FR-027): an unmeasurable contrast must never cost the manager their input.

**A flip is resolved when it is asked.** The cog reads the state and asks for the state opposite
the one it read (image specification :180); a flip found already done when it runs changes
nothing and says so.

The two rules the cog used to hold, `aspect_blocking_reasons` and `daily_portraits_refusal`, are
plain functions here, handed the services they read and never reaching through the bot
(`docs/design/architecture.md`, "Composition").
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.setting_change import NOTHING_SAVED, After, Saved, Setting
from leaguebot.core.utils.paths import resolve_within_project_root
from leaguebot.core.utils.timezones import is_known_zone
from leaguebot.image.models.image_constants import (
    ASPECT_LABELS,
    ASSET_DIRECTORIES,
    ASSET_LABELS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from leaguebot.core.utils.interaction_errors import failure_reply
from leaguebot.image.services.image_config_service import (
    pfp_change_refusal,
    read_config_on,
    set_aspect_on,
    set_daily_portraits_on,
    set_field_on,
    set_flag_on,
    set_tier_colours_on,
)
from leaguebot.image.services.image_setting_advice import (
    colour_shortfall_lines,
    declared_colour_slots,
    directory_verdict,
    fastest_lap_contrast_lines,
    measure_fastest_lap_contrast,
    toggle_enabled_lines,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_render_service import ImageRenderService
    from leaguebot.image.services.image_validity_service import ImageValidityService

ASPECT = "image.setting.aspect"
DIRECTORY = "image.setting.directory"
FASTEST_LAP_COLOUR = "image.setting.fastest_lap_colour"
DAILY_PORTRAITS = "image.setting.daily_portraits"
PER_TIER = "image.setting.per_tier"
PORTRAITS = "image.setting.portraits"
PRESENTATION = "image.setting.presentation"
TEMPLATE_DIRECTORY = "image.setting.template_directory"
TEMPLATE = "image.setting.template"
TIER_COLOURS = "image.setting.tier_colours"
TIER_IMPORT = "image.setting.tier_import"

#: The name of the step that advises on a saved setting.
ADVISE = "advise"
#: The names of the steps that arm and remove the daily portrait job after the save.
ARM_DAILY = "arm_daily"
DISARM_DAILY = "disarm_daily"

MODULE_OFF = "❌ The Image module is not enabled. Use `/module enable images` first."
#: The refusal of a form submitted after the module was switched off while it stood open.
FORM_MODULE_OFF = (
    "❌ The Image module was switched off while this form was open. Nothing was stored."
)
#: What a fault in the save of a pasted palette, or of a colour import, says became of it.
PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
IMPORT_NOT_SAVED = "Nothing from the import was saved. Run the import again to retry."
NO_CONFIGURATION = "this server has no image configuration to amend."

#: What a reply adds when the advice after a saved setting faults: the setting stands.
NOT_CHECKED = (
    "⚠️ The setting is saved, but it could not be checked against your templates; "
    "`/images config view` shows how it stands."
)

#: What a daily job that cannot be armed says became of the change: the setting is stored before
#: the job is scheduled, and the start-up recovery arms it.
DAILY_JOB_NOT_SCHEDULED = (
    "The setting is stored, and the daily job will be scheduled when the bot next starts."
)
#: What a reply adds when the daily job cannot be removed: the setting stands, and the job does
#: nothing while it is off.
DAILY_JOB_NOT_REMOVED = (
    "⚠️ The setting is saved, but the daily job could not be removed; it does nothing while "
    "the setting is off."
)

#: The command the daily portrait confirmation belongs to, as the log channel names it.
DAILY_TOGGLE = "/images use-pfp daily-toggle"

#: The label of the template directory, as the replies and the line name it.
TEMPLATE_DIRECTORY_LABEL = "Template directory"


def _held(setting: str, value: str) -> str:
    """The reply to a setting found already holding the value asked for."""
    return f"ℹ️ Nothing changed: {setting} is already **{value}**."


def _with(saved: Saved, *details: str, advised: bool = False, **result: Any) -> Saved:
    """*saved* with the values beneath its line, what its reply reads and, where *advised*, the
    advice step planned after it (the queue plans it only where the save changed something)."""
    then = (PlannedStep(ADVISE),) if advised else ()
    return dataclasses.replace(saved, details=details, result=result, then=then)


def _advice_after(run: Callable[[StepContext], Awaitable[StepResult]]) -> Mapping[str, After]:
    """The `advise` step running *run*: it may fail, leaving the setting saved and saying so."""
    return {
        ADVISE: After(
            step=Step(ADVISE, StepKind.ACT, run),
            fault_reply=lambda _payload, reply: f"{reply}\n{NOT_CHECKED}",
        )
    }


def _advice(results: Mapping[str, dict[str, Any]]) -> list[str]:
    """The lines the advice step returned, none where it has not run or faulted."""
    return list(results.get(ADVISE, {}).get("lines", []))


async def _module_refusal(modules: "ModuleService") -> Verdict | None:
    """The refusal where the Image module is off, else None."""
    if await modules.is_images_enabled():
        return None
    return Verdict.refuse(MODULE_OFF)


def _template_refusal(label: str, reason: str) -> Verdict:
    """A template not changed, naming the fault and the filename still in force."""
    return Verdict.refuse(
        f"❌ **{label}** template was **not** changed — {reason}\n"
        f"The previously configured filename is still in force."
    )


async def aspect_blocking_reasons(
    aspect: str, *, config: "ImageConfigService", validity: "ImageValidityService"
) -> list[str]:
    """What would stop *aspect* drawing, asked while it is still switched off.

    `aspect_statuses` reads the stored toggles, and an aspect that is off reports its problems
    as ``disabled_reasons`` rather than ``blocking_reasons``, so asking the ordinary way, before
    the toggle is written, always answers "nothing blocks it". The toggle is therefore
    overridden for this one question, which is what lets the setting refuse *before* storing
    rather than storing and then complaining. The configuration is left as it stands.
    """
    from leaguebot.image.services.image_render_service import converter_available
    from leaguebot.image.services.image_validity_service import build_aspect_statuses

    toggles = dict(await config.get_toggles())
    toggles[aspect] = True

    reports = await validity.template_reports()
    statuses = build_aspect_statuses(
        toggles,
        reports,
        disabled_source_modules=await validity.disabled_source_modules(),
        converter_available=converter_available(),
        # The shortfall is passed here too, and must be: this builds its own status list rather
        # than calling `aspect_statuses`, so an aspect could otherwise be switched on while a
        # template of it wanted a colour no tier had set (051).
        colour_shortfall=await validity.colour_shortfall(reports),
    )
    for status in statuses:
        if status.aspect == aspect:
            return list(status.blocking_reasons)
    return []


async def daily_portraits_refusal(
    *, modules: "ModuleService", config: "ImageConfigService", form: bool
) -> str | None:
    """Why daily portrait updates can no longer be enabled, or None if they still can.

    Asked again when the time-of-day form is submitted, when its confirmation is pressed and as
    the change runs, since any may come after another manager switched the module or portraits
    off, or enabled the daily updates. *form* says which is asking, so that a confirmation does
    not call itself a form.
    """
    if not await modules.is_images_enabled():
        return (
            f"❌ The Image module was switched off while this {'form ' if form else ''}"
            f"was open. Nothing was stored."
        )
    current = await config.get_config()
    if current is None or not current.use_pfp:
        return (
            "❌ Driver portraits stopped being obtained from Discord while this was open. "
            "Nothing was changed."
        )
    if current.pfp_daily:
        return (
            "ℹ️ Daily driver-portrait updates were already enabled while this was open. "
            "Nothing was changed."
        )
    return None


def _directory(modules: "ModuleService") -> Setting:
    """The eight asset directories: `{"column", "value"}`, the value as stored, inside the root.

    Containment is the command's, at once. The reply says whether the folder resolves, which is
    read after the save.
    """

    def label(payload: dict[str, Any]) -> str:
        return ASSET_LABELS[str(payload["column"])]

    async def check(_ctx: CheckContext) -> Verdict:
        return await _module_refusal(modules) or Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = str(payload["value"])
        saved = await set_field_on(db, str(payload["column"]), value)
        return _with(saved, f"{label(payload)}: {value}", advised=True)

    async def advise(ctx: StepContext) -> StepResult:
        resolved = resolve_within_project_root(str(ctx.payload["value"]))
        return StepResult(
            result={"lines": [directory_verdict(resolved), f"Searched: `{resolved}`"]}
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = str(payload["value"])
        if not results["save"]["changed"]:
            return _held(label(payload), f"`{value}`")
        return "\n".join([f"✅ **{label(payload)}** set to `{value}`.", *_advice(results)])

    return Setting(
        kind=DIRECTORY,
        command=lambda payload: f"/images config {ASSET_DIRECTORIES[str(payload['column'])][0]}",
        doing=lambda payload: f"Setting **{label(payload)}** to `{payload['value']}`",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after=_advice_after(advise),
    )


def _template_directory(modules: "ModuleService", config: "ImageConfigService") -> Setting:
    """The template directory: `{"value"}`, validated before it is stored (FR-005).

    This is the one directory with no packaged second tier, so a folder that does not hold every
    template the switched-on outputs need is refused, naming each fault. The survey is the one
    that backs `/season placements-review` and the confirmation of placements, so the three
    surfaces cannot disagree about whether a template is usable. It is scoped to the outputs that
    are switched on: switching an output on checks its own drawings at that moment.
    """
    from leaguebot.image.services.image_validity_service import (
        blocking_template_problems,
        describe,
    )

    def refusal(reason: str, *, problems: list[Any] | None = None, searched: Any = None) -> Verdict:
        lines = [
            f"❌ **{TEMPLATE_DIRECTORY_LABEL}** was **not** changed — {reason}",
            "The previously configured folder is still in force.",
        ]
        if problems:
            lines.append("")
            lines += [f"  ↳ {describe(problem)}" for problem in problems]
        if searched is not None:
            lines += ["", f"Searched: `{searched}`"]
        return Verdict.refuse("\n".join(lines))

    async def check(ctx: CheckContext) -> Verdict:
        off = await _module_refusal(modules)
        if off is not None:
            return off
        stored = str(ctx.payload["value"])
        proposed = await config.candidate_config("template_directory", stored)
        if proposed is None:
            return refusal(NO_CONFIGURATION)
        # The survey parses every template the switched-on outputs need, seconds on the Pi:
        # it runs off the event loop, which `blocking_template_problems` is pure enough for.
        problems = await asyncio.to_thread(
            blocking_template_problems, proposed, await config.get_toggles()
        )
        if problems:
            return refusal(
                f"`{stored}` does not hold every template the bot needs for the outputs you "
                f"have switched on.",
                problems=problems,
                searched=resolve_within_project_root(stored),
            )
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = str(payload["value"])
        saved = await set_field_on(db, "template_directory", value)
        return _with(saved, f"{TEMPLATE_DIRECTORY_LABEL}: {value}")

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = str(payload["value"])
        if not results["save"]["changed"]:
            return _held(TEMPLATE_DIRECTORY_LABEL, f"`{value}`")
        return (
            f"✅ **{TEMPLATE_DIRECTORY_LABEL}** set to `{value}`.\n"
            f"✅ Every drawing the outputs you have switched on need is present and valid.\n"
            f"Searched: `{resolve_within_project_root(value)}`"
        )

    return Setting(
        kind=TEMPLATE_DIRECTORY,
        command=lambda _payload: "/images config template-directory",
        doing=lambda payload: f"Setting **{TEMPLATE_DIRECTORY_LABEL}** to `{payload['value']}`",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
    )


def _template(modules: "ModuleService", config: "ImageConfigService") -> Setting:
    """The sixteen template filenames: `{"column", "value"}`, validated before it is stored.

    A configuration that cannot be used is refused at the moment it is named, the one moment the
    manager is present, holding the file, and able to fix it: nothing is written until every
    check passes. A filename carrying a path separator is refused at once by the command.
    """
    from leaguebot.image.services.image_validity_service import check_template

    async def check(ctx: CheckContext) -> Verdict:
        off = await _module_refusal(modules)
        if off is not None:
            return off
        column = str(ctx.payload["column"])
        label = TEMPLATE_LABELS[column]
        proposed = await config.candidate_config(column, str(ctx.payload["value"]))
        if proposed is None:
            return _template_refusal(label, NO_CONFIGURATION)
        problem = check_template(proposed, column)
        if problem is not None:
            return _template_refusal(label, problem.message())
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = str(payload["value"])
        label = TEMPLATE_LABELS[str(payload["column"])]
        saved = await set_field_on(db, str(payload["column"]), value)
        return _with(saved, f"{label} template: {value}")

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = str(payload["value"])
        label = TEMPLATE_LABELS[str(payload["column"])]
        if not results["save"]["changed"]:
            return _held(f"the {label} template", f"`{value}`")
        return f"✅ **{label}** template set to `{value}`.\n✅ Valid."

    return Setting(
        kind=TEMPLATE,
        command=lambda payload: (
            f"/images template {TEMPLATE_COMMAND_NAMES[str(payload['column'])]}"
        ),
        doing=lambda payload: (
            f"Setting the **{TEMPLATE_LABELS[str(payload['column'])]}** template to "
            f"`{payload['value']}`"
        ),
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
    )


def _aspect(
    modules: "ModuleService", config: "ImageConfigService", validity: "ImageValidityService"
) -> Setting:
    """`/images config toggle`: `{"aspect", "enabled"}`, the flip resolved when it was asked.

    Switching an output *off* is never refused: it reverts to text, which needs no drawing at
    all. Switching one *on* is refused while the drawings behind it are unusable, the output
    left off, since an output that cannot draw withholds the season's approval and posts nothing
    where a driver would otherwise have read text. The check is made against the configured
    directory, so it answers for the files the league actually has, and again as the change
    starts. An output found already in the state asked for, where two presses raced, changes
    nothing and says so.
    """

    def label(payload: dict[str, Any]) -> str:
        return ASPECT_LABELS[str(payload["aspect"])]

    def state(payload: dict[str, Any]) -> str:
        return "on" if payload["enabled"] else "off"

    async def check(ctx: CheckContext) -> Verdict:
        off = await _module_refusal(modules)
        if off is not None:
            return off
        if not ctx.payload["enabled"]:
            return Verdict.go()
        blocking = await aspect_blocking_reasons(
            str(ctx.payload["aspect"]), config=config, validity=validity
        )
        if not blocking:
            return Verdict.go()
        body = "\n".join(f"  • {reason}" for reason in blocking)
        return Verdict.refuse(
            f"⛔ **{label(ctx.payload)}** image output was **not** switched on — "
            f"it cannot be drawn as things stand:\n{body}\n"
            f"Put that right and run this command again. The output is still posted "
            f"as text in the meantime.",
            "\n".join(blocking),
        )

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        saved = await set_aspect_on(db, str(payload["aspect"]), bool(payload["enabled"]))
        shown = "enabled" if payload["enabled"] else "disabled"
        return _with(saved, f"{label(payload)} image output: {shown}")

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        if not results["save"]["changed"]:
            return _held(label(payload), state(payload))
        if not payload["enabled"]:
            return f"❌ **{label(payload)}** image output **disabled**. Posting stays as text."
        return "\n".join(toggle_enabled_lines(str(payload["aspect"]), label(payload), []))

    return Setting(
        kind=ASPECT,
        command=lambda _payload: "/images config toggle",
        doing=lambda payload: f"Switching **{label(payload)}** image output {state(payload)}",
        check=check,
        save=save,
        reply=reply,
        writes=("image_aspect_toggles",),
    )


#: Each presentation column: its command, the words a line and a held reply give it, and how the
#: acknowledgement says what is being set. The zone is shown as stored, the two formats as the
#: member chose them.
_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "time_zone": ("time-zone", "Time zone", "the time zone"),
    "time_format": ("time-format", "Clock format", "the clock format"),
    "date_format": ("date-format", "Date format", "the date format"),
}


def _presentation(modules: "ModuleService") -> Setting:
    """The time zone, the clock format and the date format: `{"column", "value", "shown"}`.

    *shown* is how the member sees the value: the name of the choice picked for the two formats,
    the zone itself for the zone. A zone is checked against the memoised list of IANA names, not
    `available_timezones()`, which walks the whole TZPATH tree on every call (325 ms cold on the
    Pi, for one membership test).
    """

    def shown(payload: dict[str, Any]) -> str:
        return str(payload["shown"])

    async def check(ctx: CheckContext) -> Verdict:
        off = await _module_refusal(modules)
        if off is not None:
            return off
        if ctx.payload["column"] == "time_zone" and not is_known_zone(str(ctx.payload["value"])):
            return Verdict.refuse(
                f"❌ `{ctx.payload['value']}` is not a recognised time zone. "
                f"Use an IANA name such as `Europe/Lisbon` or `UTC`."
            )
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        column = str(payload["column"])
        value = str(payload["value"])
        saved = await set_field_on(db, column, value)
        name = _PRESENTATION[column][1]
        if column == "time_zone":
            return _with(saved, f"{name}: {value}")
        return _with(saved, f"{name}: {shown(payload)} ({value})")

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        column = str(payload["column"])
        if column == "time_zone":
            if not results["save"]["changed"]:
                return _held("the time zone", f"`{payload['value']}`")
            return (
                f"✅ Time zone set to `{payload['value']}`.\n"
                f"Times are shown in the offset that zone carries **on the date displayed**, "
                f"so a season spanning a daylight-saving change stays correct."
            )
        if not results["save"]["changed"]:
            return _held(_PRESENTATION[column][2], shown(payload))
        return f"✅ {_PRESENTATION[column][1]} set to **{shown(payload)}**."

    def doing(payload: dict[str, Any]) -> str:
        if payload["column"] == "time_zone":
            return f"Setting the time zone to `{payload['value']}`"
        return f"Setting {_PRESENTATION[str(payload['column'])][2]} to **{shown(payload)}**"

    return Setting(
        kind=PRESENTATION,
        command=lambda payload: f"/images config {_PRESENTATION[str(payload['column'])][0]}",
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
    )


def _fastest_lap_colour(
    modules: "ModuleService",
    config: "ImageConfigService",
    validity: "ImageValidityService",
    render: "ImageRenderService",
    seasons: "SeasonService",
) -> Setting:
    """`/images config fastest-lap-colour`: `{"value"}`, the colour in its canonical form.

    A malformed value is refused at once by the command, leaving the stored colour untouched
    (FR-025). The contrast against the template's plate is measured after the save, in the
    `advise` step, so that an unmeasurable contrast never costs the manager their input
    (FR-026, FR-027).
    """

    async def check(_ctx: CheckContext) -> Verdict:
        return await _module_refusal(modules) or Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        value = str(payload["value"])
        saved = await set_field_on(db, "fastest_lap_colour", value)
        return _with(saved, f"Fastest-lap colour: {value}", advised=True)

    async def advise(ctx: StepContext) -> StepResult:
        reading = await measure_fastest_lap_contrast(
            str(ctx.payload["value"]),
            config=config,
            render=render,
            validity=validity,
            seasons=seasons,
        )
        return StepResult(result={"lines": fastest_lap_contrast_lines(reading)})

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        value = str(payload["value"])
        if not results["save"]["changed"]:
            return _held("the fastest-lap colour", f"`{value}`")
        return "\n".join([f"✅ Fastest-lap colour set to `{value}`.", *_advice(results)])

    return Setting(
        kind=FASTEST_LAP_COLOUR,
        command=lambda _payload: "/images config fastest-lap-colour",
        doing=lambda payload: f"Setting the fastest-lap colour to `{payload['value']}`",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after=_advice_after(advise),
    )


def _per_tier(modules: "ModuleService", validity: "ImageValidityService") -> Setting:
    """`/images config per-tier-colour-toggle`: `{"enabled"}`, the flip resolved when asked.

    What the reply says beneath the flip (the colour shortfall when switching on, which reads the
    sixteen-template sweep, or what off means) is advice after the save.
    """

    def state(payload: dict[str, Any]) -> str:
        return "on" if payload["enabled"] else "off"

    async def check(_ctx: CheckContext) -> Verdict:
        return await _module_refusal(modules) or Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        saved = await set_flag_on(db, "per_tier_colour_enabled", bool(payload["enabled"]))
        return _with(saved, f"Per-tier colours: {state(payload)}", advised=True)

    async def advise(ctx: StepContext) -> StepResult:
        lines = await colour_shortfall_lines(validity, bool(ctx.payload["enabled"]))
        return StepResult(result={"lines": lines})

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        if not results["save"]["changed"]:
            return _held("the per-tier colours setting", state(payload))
        return "\n".join([f"✅ Per-tier colours are now **{state(payload)}**.", *_advice(results)])

    return Setting(
        kind=PER_TIER,
        command=lambda _payload: "/images config per-tier-colour-toggle",
        doing=lambda payload: f"Turning per-tier colours {state(payload)}",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after=_advice_after(advise),
    )


#: The commands a tier's colours are set through, by whether the form was used.
_TIER_COMMANDS = {False: "/images config per-tier-set-colour", True: "/images config per-tier-bulk-colour"}


def _tier_colours(
    modules: "ModuleService", config: "ImageConfigService", validity: "ImageValidityService"
) -> Setting:
    """One tier's colours: `{"division", "colours", "form"}`, slot to canonical colour.

    The division is as the member typed it, the slots and colours already validated and
    canonical (the command refuses what it cannot read, storing nothing). *form* says the colours
    were pasted into the palette form, which refuses in its own words when the module was switched
    off while it stood open and says "paste it again" of a fault. A slot is merged into the tier's
    palette, never replacing the rest; the slots no template marks and per-tier colours being off
    are said after the save, once the sweep has been read.
    """

    def colours(payload: dict[str, Any]) -> dict[str, str]:
        return dict(payload["colours"])

    async def check(ctx: CheckContext) -> Verdict:
        if await modules.is_images_enabled():
            return Verdict.go()
        return Verdict.refuse(FORM_MODULE_OFF if ctx.payload["form"] else MODULE_OFF)

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        division = str(payload["division"])
        named = colours(payload)
        saved = await set_tier_colours_on(db, division, named)
        if payload["form"]:
            details = (
                f"Tier colours: {division}",
                *(f"{slot} = {colour}" for slot, colour in named.items()),
            )
        else:
            ((slot, colour),) = named.items()
            details = (f"Tier colour: {division} / {slot} = {colour}",)
        return _with(saved, *details, advised=True, written=len(saved.audits))

    async def advise(ctx: StepContext) -> StepResult:
        declared = await declared_colour_slots(validity)
        unknown = sorted(set(colours(ctx.payload)) - declared)
        lines: list[str] = []
        if ctx.payload["form"]:
            if unknown:
                lines.append(
                    f"ℹ️ No template of yours marks {', '.join(f'`{s}`' for s in unknown)} yet."
                )
        else:
            if unknown:
                lines.append(
                    f"ℹ️ No template of yours marks `{unknown[0]}` yet, so nothing will "
                    f"change until one does."
                )
            current = await config.get_config()
            if current is not None and not current.per_tier_colour_enabled:
                lines.append(
                    "ℹ️ Per-tier colours are **off**, so this is stored but not drawn. "
                    "Turn them on with `/images config per-tier-colour-toggle`."
                )
        return StepResult(result={"lines": lines})

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        division = str(payload["division"])
        named = colours(payload)
        if not payload["form"]:
            ((slot, colour),) = named.items()
            if not results["save"]["changed"]:
                return _held(f"`{slot}` for **{division}**", f"`{colour}`")
            return "\n".join(
                [f"✅ **{division}** — `{slot}` set to `{colour}`.", *_advice(results)]
            )
        written = int(results["save"]["written"])
        if not results["save"]["changed"]:
            return f"ℹ️ Nothing changed: every colour named is already held for **{division}**."
        held = ""
        if written < len(named):
            held = f" ({written} changed, {len(named) - written} already held)"
        return "\n".join(
            [
                f"✅ **{division}** — {len(named)} colour(s) set{held}.",
                *(f"  • `{slot}` = `{colour}`" for slot, colour in named.items()),
                *_advice(results),
            ]
        )

    def doing(payload: dict[str, Any]) -> str:
        division = payload["division"]
        if payload["form"]:
            return f"Setting {len(payload['colours'])} colour(s) for **{division}**"
        ((slot, colour),) = colours(payload).items()
        return f"Setting `{slot}` for **{division}** to `{colour}`"

    return Setting(
        kind=TIER_COLOURS,
        command=lambda payload: _TIER_COMMANDS[bool(payload["form"])],
        doing=doing,
        check=check,
        save=save,
        reply=reply,
        writes=("image_tier_colour",),
        not_saved=lambda payload: PASTE_NOT_SAVED if payload["form"] else NOTHING_SAVED,
        after=_advice_after(advise),
    )


def _tier_import(modules: "ModuleService") -> Setting:
    """`/images config colour-xml-import`: `{"blocks", "passed_over"}`, every tier in one save.

    *blocks* are the divisions the document named whose colours could be read, each
    `{"division", "colours"}`; *passed_over* are the reasons for the blocks the command passed
    over. A division is never half applied, and neither is the import: one fault saves none of
    its tiers and the member is told to run the import again.
    """

    async def check(_ctx: CheckContext) -> Verdict:
        return await _module_refusal(modules) or Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        blocks = list(payload["blocks"])
        audits: list[AuditRecord] = []
        for block in blocks:
            written = await set_tier_colours_on(db, str(block["division"]), block["colours"])
            audits.extend(written.audits)
        passed_over = list(payload["passed_over"])
        details = [f"{block['division']}: {len(block['colours'])} colour(s)" for block in blocks]
        if passed_over:
            details.append("passed over:")
            details += [f"  {problem}" for problem in passed_over]
        saved = Saved(changed=bool(audits), audits=tuple(audits))
        return _with(saved, *details, written=len(audits))

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        blocks = list(payload["blocks"])
        passed_over = list(payload["passed_over"])
        written = int(results["save"]["written"])
        named = sum(len(block["colours"]) for block in blocks)
        lines: list[str] = []
        if blocks and not written:
            lines.append("ℹ️ Nothing changed: every colour named is already held.")
        elif blocks:
            held = f" ({written} changed, {named - written} already held)" if written < named else ""
            lines.append(f"✅ Imported {named} colour(s) across {len(blocks)} tier(s){held}.")
            lines += [
                f"  • **{block['division']}** — {len(block['colours'])} colour(s)"
                for block in blocks
            ]
        if passed_over:
            lines.append(f"⚠️ {len(passed_over)} block(s) were not imported:")
            lines += [f"  • {problem}" for problem in passed_over]
        return "\n".join(lines)

    return Setting(
        kind=TIER_IMPORT,
        command=lambda _payload: "/images config colour-xml-import",
        doing=lambda payload: f"Importing colours for {len(payload['blocks'])} tier(s)",
        check=check,
        save=save,
        reply=reply,
        writes=("image_tier_colour",),
        not_saved=IMPORT_NOT_SAVED,
    )


def _arm_daily(scheduler: "SchedulerService", command: Callable[[dict[str, Any]], str]) -> After:
    """The step that arms the daily portrait job once the time and the flag are saved.

    It carries the success line: a job that cannot be armed records no success (image
    specification :132). The setting stays stored and the lane goes on; the reply says the job
    will be scheduled when the bot next starts, as it always has.
    """

    async def run(ctx: StepContext) -> StepResult:
        kept = next((view.result or {} for view in ctx.steps if view.name == "save"), {})
        scheduler.schedule_portrait_refresh(str(ctx.payload.get("time") or kept["daily_time"]))
        return StepResult()

    return After(
        step=Step(ARM_DAILY, StepKind.ACT, run),
        fault_reply=lambda payload, _reply: failure_reply(
            f"`{command(payload)}`", DAILY_JOB_NOT_SCHEDULED
        ),
        carries_line=True,
    )


def _disarm_daily(scheduler: "SchedulerService") -> After:
    """The step that removes the daily portrait job once portraits or daily updates are off.

    It may fail without holding up the queue: the setting stays saved and the reply says the job
    could not be removed and does nothing while the setting is off.
    """

    async def run(_ctx: StepContext) -> StepResult:
        scheduler.cancel_portrait_refresh()
        return StepResult()

    return After(
        step=Step(DISARM_DAILY, StepKind.ACT, run),
        fault_reply=lambda _payload, reply: f"{reply}\n{DAILY_JOB_NOT_REMOVED}",
    )


#: Each portrait toggle: its command and the label its replies and its line give it.
_PORTRAITS: dict[str, tuple[str, str]] = {
    "use_pfp": ("/images use-pfp toggle", "Discord profile pictures"),
    "pfp_prerender": ("/images use-pfp prerender-toggle", "Pre-render portrait updates"),
    "pfp_daily": ("/images use-pfp daily-toggle", "Daily driver-portrait updates"),
}

NOT_OBTAINED = (
    "❌ Driver portraits are not being obtained from Discord. "
    "Use `/images use-pfp toggle` first."
)


def _portraits(
    modules: "ModuleService", config: "ImageConfigService", scheduler: "SchedulerService"
) -> Setting:
    """The three portrait toggles: `{"column", "enabled"}`, a flip resolved when asked.

    The two sub-toggles govern how portraits are kept up to date, which is no question while the
    bot is not obtaining them at all, and a change that would leave neither update on is refused
    (`pfp_change_refusal`), both at the ask and as the change runs. The daily job follows the
    save: armed after portraits are switched on with daily updates set, removed after portraits
    or the daily updates are switched off. The arming carries the success line.
    """

    def label(payload: dict[str, Any]) -> str:
        return _PORTRAITS[str(payload["column"])][1]

    def command(payload: dict[str, Any]) -> str:
        return _PORTRAITS[str(payload["column"])][0]

    def state(payload: dict[str, Any]) -> str:
        return "on" if payload["enabled"] else "off"

    async def check(ctx: CheckContext) -> Verdict:
        off = await _module_refusal(modules)
        if off is not None:
            return off
        column = str(ctx.payload["column"])
        current = await config.get_config()
        if column == "use_pfp":
            if current is None:
                return Verdict.refuse("❌ The image module has no configuration yet.")
        elif current is None or not current.use_pfp:
            return Verdict.refuse(NOT_OBTAINED)
        refusal = pfp_change_refusal(current, column, bool(ctx.payload["enabled"]))
        if refusal is not None:
            return Verdict.refuse(f"❌ {refusal}")
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        column = str(payload["column"])
        enabled = bool(payload["enabled"])
        saved = await set_flag_on(db, column, enabled)
        shown = "enabled" if enabled else "disabled"
        stored = await read_config_on(db)
        daily_time = None if stored is None else stored.pfp_daily_time
        then: tuple[PlannedStep, ...] = ()
        if not enabled and column in ("use_pfp", "pfp_daily"):
            then = (PlannedStep(DISARM_DAILY),)
        elif enabled and column == "use_pfp" and stored is not None and stored.pfp_daily:
            then = (PlannedStep(ARM_DAILY),)
        return dataclasses.replace(
            _with(saved, f"{label(payload)}: {shown}", daily_time=daily_time), then=then
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        if not results["save"]["changed"]:
            return _held(label(payload), state(payload))
        shown = "enabled" if payload["enabled"] else "disabled"
        return f"{'✅' if payload['enabled'] else '❌'} **{label(payload)}** {shown}."

    return Setting(
        kind=PORTRAITS,
        command=command,
        doing=lambda payload: f"Turning **{label(payload)}** {state(payload)}",
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after={ARM_DAILY: _arm_daily(scheduler, command), DISARM_DAILY: _disarm_daily(scheduler)},
    )


def _daily_portraits(
    modules: "ModuleService", config: "ImageConfigService", scheduler: "SchedulerService"
) -> Setting:
    """Confirming daily portrait updates at a time: `{"time"}`, normalised and in UTC.

    The time and the flag are saved together, as one change with one audit entry, and the job is
    armed after the save by the step that carries the success line. The refusal is asked again as
    the change runs, the confirmation having stood open while another manager changed the module
    or the setting.
    """

    async def check(_ctx: CheckContext) -> Verdict:
        refusal = await daily_portraits_refusal(modules=modules, config=config, form=False)
        return Verdict.go() if refusal is None else Verdict.refuse(refusal)

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        time_of_day = str(payload["time"])
        saved = await set_daily_portraits_on(db, time_of_day)
        return dataclasses.replace(
            _with(saved, f"Time of day: {time_of_day} UTC"), then=(PlannedStep(ARM_DAILY),)
        )

    def reply(payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        if not results["save"]["changed"]:
            return _held("Daily driver-portrait updates", "on")
        return (
            f"✅ **Daily driver-portrait updates** enabled, running at "
            f"**{payload['time']} UTC** each day."
        )

    command = lambda _payload: DAILY_TOGGLE  # noqa: E731
    return Setting(
        kind=DAILY_PORTRAITS,
        command=command,
        doing=lambda payload: (
            f"Enabling daily driver-portrait updates at **{payload['time']} UTC**"
        ),
        check=check,
        save=save,
        reply=reply,
        writes=("image_config",),
        after={ARM_DAILY: _arm_daily(scheduler, command)},
    )


def image_settings(
    *,
    modules: "ModuleService",
    config: "ImageConfigService",
    validity: "ImageValidityService",
    render: "ImageRenderService",
    seasons: "SeasonService",
    scheduler: "SchedulerService",
) -> tuple[Setting, ...]:
    """Every image setting, built from the services handed in.

    The services are values, never read through the bot (`docs/design/architecture.md`,
    "Composition"), so a setting's check and its steps are testable with doubles.
    """
    return (
        _directory(modules),
        _template_directory(modules, config),
        _template(modules, config),
        _aspect(modules, config, validity),
        _presentation(modules),
        _fastest_lap_colour(modules, config, validity, render, seasons),
        _per_tier(modules, validity),
        _tier_colours(modules, config, validity),
        _tier_import(modules),
        _portraits(modules, config, scheduler),
        _daily_portraits(modules, config, scheduler),
    )


__all__ = [
    "ADVISE", "ARM_DAILY", "ASPECT", "DAILY_JOB_NOT_REMOVED", "DAILY_JOB_NOT_SCHEDULED",
    "DAILY_PORTRAITS", "DAILY_TOGGLE", "DIRECTORY", "DISARM_DAILY", "FASTEST_LAP_COLOUR", "FORM_MODULE_OFF", "IMPORT_NOT_SAVED",
    "MODULE_OFF", "NOT_CHECKED", "PASTE_NOT_SAVED", "PER_TIER", "PORTRAITS", "PRESENTATION", "TEMPLATE",
    "TEMPLATE_DIRECTORY", "TIER_COLOURS", "TIER_IMPORT", "aspect_blocking_reasons",
    "daily_portraits_refusal", "image_settings",
]
