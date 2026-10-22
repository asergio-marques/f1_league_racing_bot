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

import dataclasses
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import CheckContext, Step, StepContext
from leaguebot.core.services.setting_change import After, Saved, Setting
from leaguebot.core.utils.paths import resolve_within_project_root
from leaguebot.image.models.image_constants import (
    ASSET_DIRECTORIES,
    ASSET_LABELS,
    TEMPLATE_COMMAND_NAMES,
    TEMPLATE_LABELS,
)
from leaguebot.image.services.image_config_service import set_field_on
from leaguebot.image.services.image_setting_advice import directory_verdict

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_render_service import ImageRenderService
    from leaguebot.image.services.image_validity_service import ImageValidityService

DIRECTORY = "image.setting.directory"
TEMPLATE_DIRECTORY = "image.setting.template_directory"
TEMPLATE = "image.setting.template"

#: The name of the step that advises on a saved setting.
ADVISE = "advise"

MODULE_OFF = "❌ The Image module is not enabled. Use `/module enable images` first."
NO_CONFIGURATION = "this server has no image configuration to amend."

#: What a reply adds when the advice after a saved setting faults: the setting stands.
NOT_CHECKED = (
    "⚠️ The setting is saved, but it could not be checked against your templates; "
    "`/images config view` shows how it stands."
)

#: The label of the template directory, as the replies and the line name it.
TEMPLATE_DIRECTORY_LABEL = "Template directory"


def _held(setting: str, value: str) -> str:
    """The reply to a setting found already holding the value asked for."""
    return f"ℹ️ Nothing changed: {setting} is already **{value}**."


def _with(saved: Saved, detail: str, *, advised: bool = False, **result: Any) -> Saved:
    """*saved* with the value beneath its line, what its reply reads and, where *advised*, the
    advice step planned after it (the queue plans it only where the save changed something)."""
    then = (PlannedStep(ADVISE),) if advised else ()
    return dataclasses.replace(saved, details=(detail,), result=result, then=then)


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
        problems = blocking_template_problems(proposed, await config.get_toggles())
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
    )


__all__ = [
    "ADVISE", "DIRECTORY", "MODULE_OFF", "NOT_CHECKED", "TEMPLATE", "TEMPLATE_DIRECTORY",
    "image_settings",
]
