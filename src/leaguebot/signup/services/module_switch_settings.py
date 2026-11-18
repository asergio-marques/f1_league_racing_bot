"""Signup's own switch-on, as a setting on the change queue.

`/module enable signup` asks one change built from the `Setting` below by `setting_change`
(`framework/services/queue/setting_change.py`, which holds what every settings change does with a value
already held and with a fault): `module_on("signup")` (`framework/models/change.py`), so core's cog
names the change without importing signup, as `docs/design/architecture.md`, "How modules and
core fit together", designs it: weather's switch
(`weather/services/module_switch_settings.py`) is the model. Signup's switch-off is
`signup_off_change.py`'s own change; the switch-on here is its match.

**The switch-on is one save: the flag, a bare configuration row created only where none stands,
the clearing of a kept channel another setting took, and the audit entries.** Creating only what is
missing, rather than replacing a standing row's values, keeps the configuration the switch-off kept
intact — the channel, the time slots and the question settings stand again as they were (issue
#127), and a configuration row restored from a backup is kept rather than blanked. The switch-on
finds the channel and settings as they stood, and checks that channel first
(`kept_channels_check`, handed `channel_uses.kept_channels`): the bare row this creates where none
exists is what the module needs to be usable, not a reset of what is there.

**The reply asks for `/signup channel` only where no channel stands after the save**: the check's
heading asks for one it named (gone or another setting's, cleared), so it is not asked for twice,
and a kept channel that is usable is not asked for at all.

**The check before the save names a kept channel that is gone, or that another setting took while
the module was off, and refuses one the bot cannot use** (`core/services/modules/kept_channels.py`
holds the words): the save clears a taken one through `clear_signup_channel_on`, audited as the
channel's own change (`SIGNUP_CHANNEL_SET`, from the channel to none), with a line beneath the
Success line. The channel is the server's, not a division's, so it is "the signup channel".

**The bot's permissions on the channel are recorded before the save** (`RECORD_PERMISSIONS`): for
each target `/signup channel` sets one on (`signup_channel_overwrites`, read as the league's roles
stand now, so a base role changed while signup was off is the one put back), the overwrite it holds
there now and the one wanted. The record changes nothing on Discord, so it declares no undo. It is
a step of its own, marked done before the permissions are put back, so that a restore run again
after a stop reads the first record: what a take-back restores is what stood before the first
attempt, not what the stop left. The channel the check named, or none kept, is skipped.

**The permissions are then put back, a step before the save** (`RESTORE_PERMISSIONS`, with the
undo `TAKE_BACK_PERMISSIONS`): the recorded prior overwrite of each target, merged with the wanted
one, so what a league set by hand and the bot sets nothing for stands. A fault part-way takes back
what it set before it raises, and the switch-on stays off ("run it again"); where the save then
faults, the undo takes back all of it. Where Discord refuses a take-back, the failure line and the
reply say that permissions were left on the channel, and what to do. **A stop between two targets**
leaves some set and the step not done: it runs again from the start and takes back, on a fault,
to the first record.

**The flag is signup's own column on core's table, `server_configs.signup_module_enabled`**
(architecture.md, "The database"; `OWN_COLUMNS` in `tests/repository/test_architecture_rules.py`),
as weather's is.

**The check reads the season's stage again as the change starts,** not only at the ask
(`module_cog.py`'s stage refusal): the signup module is fixed from a season's configuration being
confirmed to its end, so a switch-on asked before that and run after it is refused, in the words
the ask already gives (`configuration_fixed_refusal`), rather than switching signup on behind what
the league was told.

**Once it has changed something it asks the hub's panel to be refreshed, once, as an `After`
step** (`hub_service.hub_refresh_after`): the panel offers what a module offers while it is on, so
the switch asks for that itself rather than the command doing it after every press.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import discord

from leaguebot.signup.models.switch import SWITCH_KEY
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import (
    AuditRecord,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
    module_on,
)
from leaguebot.framework.services.queue.change_types import (
    CheckContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.framework.services.queue.setting_change import Before
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.core.services.modules.kept_channels import (
    CHECK_KEPT,
    found_in,
    kept_channels_check,
    named_reply,
)
from leaguebot.core.services.modules.hub_service import REFRESH_HUB, hub_refresh_after
from leaguebot.core.services.seasons.season_lifecycle_service import (
    configuration_fixed,
    configuration_fixed_refusal,
)
from leaguebot.framework.services.queue.setting_change import Saved, Setting
from leaguebot.signup.services.channel_uses import kept_channels
from leaguebot.signup.services.signup_module_service import (
    clear_signup_channel_on,
    signup_channel_overwrites,
)

if TYPE_CHECKING:
    import aiosqlite

    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.framework.utils.league_bot import LeagueBot
    from leaguebot.framework.utils.hooks import AwaitedHook

log = logging.getLogger(__name__)

#: The name of the step, before the save, that records the bot's permissions on the signup channel
#: as they stand, and as they are to be put back.
RECORD_PERMISSIONS = "record_permissions"

#: The name of the step, before the save, that puts the bot's permissions on the signup channel,
#: and of its undo, which takes them back where the save does not go through.
RESTORE_PERMISSIONS = "restore_permissions"
TAKE_BACK_PERMISSIONS = "take_back_permissions"

#: The kinds of target the record names: @everyone, the bot's own member, and a role by its id.
EVERYONE, BOT, ROLE = "everyone", "bot", "role"

_ENABLE_COMMAND = "/module enable signup"


def _pair(overwrite: discord.PermissionOverwrite) -> dict[str, int]:
    """*overwrite* as the two numbers Discord holds it as, to keep in a step's result."""
    allow, deny = overwrite.pair()
    return {"allow": allow.value, "deny": deny.value}


def _still_off(_payload: dict[str, Any]) -> str:
    return "The module is still off. Run `/module enable signup` again once the fault is cleared."


async def _signup_channel(
    bot: "LeagueBot", channel_id: int
) -> tuple[discord.Guild, discord.TextChannel]:
    """The league's server and its signup channel *channel_id*, through `league_guild`; raises
    `StepFailedOnDiscord` where the server is out of the bot's reach or the channel is not on it."""
    guild = await league_guild(bot)
    if guild is None:
        raise StepFailedOnDiscord("the league's server is out of the bot's reach")
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise StepFailedOnDiscord(f"the signup channel {channel_id} is not on the server")
    return guild, channel


def _recorded(steps: Sequence[StepView]) -> dict[str, Any] | None:
    """What the record step found, or None where it skipped: no channel is kept, or the check
    named it."""
    found = next((view.result for view in steps if view.name == RECORD_PERMISSIONS), None) or {}
    return None if found.get("skip") or "targets" not in found else found


def _target_of(
    guild: discord.Guild, entry: dict[str, Any]
) -> discord.Role | discord.Member | None:
    """The role or member a record's *entry* names on *guild*, None where it is gone."""
    kind = entry["kind"]
    if kind == EVERYONE:
        return guild.default_role
    if kind == BOT:
        return guild.me
    return guild.get_role(int(entry["id"]))


def _overwrite(pair: dict[str, int]) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite.from_pair(
        discord.Permissions(pair["allow"]), discord.Permissions(pair["deny"])
    )


def _left(channel_id: int) -> str:
    """What the league is told of permissions the bot put on its signup channel and could not take
    off again."""
    return (
        f"Permissions the bot put on the signup channel <#{channel_id}> were left there: "
        f"run `{_ENABLE_COMMAND}` again, or remove them by hand."
    )


async def _take_back(
    guild: discord.Guild,
    channel: discord.TextChannel,
    record: dict[str, Any],
    applied: Sequence[int],
) -> bool:
    """Set each of the record's targets at the indices *applied* back to what stood before the
    first record (none, where it held no overwrite), and say whether every one went back. A target
    that cannot is logged, and the rest are still taken back."""
    every = True
    for index in applied:
        entry = record["targets"][index]
        try:
            target = _target_of(guild, entry)
            if target is None:
                raise LookupError(f"the {entry['kind']} {entry['id']} is not on the server")
            prior = entry["prior"]
            await channel.set_permissions(
                target, overwrite=None if prior is None else _overwrite(prior)
            )
        except Exception:
            log.exception(
                "could not take back the bot's permissions for %s on the signup channel %s",
                entry["kind"], record["channel_id"],
            )
            every = False
    return every


def _record_permissions(config: "ConfigService") -> Before:
    """The step that records, before the save, what is to be put on the signup channel and what
    stands there: `{"channel_id", "targets": [{"kind": `EVERYONE`, `BOT` or `ROLE`, "id": the
    role's, None for the other two, which the server names itself, "prior": the overwrite held
    now, None where there is none, "wanted": the overwrite `/signup channel` sets}]}`, in the order `signup_channel_overwrites` gives the targets, or `{"skip": True}` where
    no channel is kept or the check named the one that is (gone, or another setting's), which is
    set again by hand. It reaches the server through `league_guild` and raises where the server or
    the channel is missing. It changes nothing on Discord, and so declares no undo."""

    async def run(ctx: StepContext) -> StepResult:
        checked = next(
            (view.result or {} for view in ctx.steps if view.name == CHECK_KEPT), {}
        )
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
            row = await cursor.fetchone()
        if row is None or row["signup_channel_id"] is None or checked.get("named"):
            return StepResult(result={"skip": True})
        channel_id = int(row["signup_channel_id"])
        guild, channel = await _signup_channel(ctx.bot, channel_id)
        wanted = signup_channel_overwrites(guild, await config.get_server_config())
        targets: list[dict[str, Any]] = []
        for target, overwrite in wanted.items():
            held = channel.overwrites_for(target)
            kind = (
                EVERYONE if target is guild.default_role else BOT if target is guild.me else ROLE
            )
            targets.append({
                "kind": kind,
                "id": target.id if kind == ROLE else None,
                "prior": None if held.is_empty() else _pair(held),
                "wanted": _pair(overwrite),
            })
        return StepResult(result={"channel_id": channel_id, "targets": targets})

    return Before(Step(RECORD_PERMISSIONS, StepKind.ACT, run))


def _restore_permissions() -> Before:
    """The step that puts the bot's permissions on the signup channel back, and its undo.

    For each target in the record (`_record_permissions`) it sets the overwrite the channel held
    (`prior`) merged with the permissions `/signup channel` sets (`wanted`): a permission the
    league set by hand and the bot sets nothing for stands. It does nothing where the record
    skipped, and returns `{"applied": [the indices of the targets set]}`.

    **A fault part-way takes back its own partial work** before it raises again, for the undo
    below is for finished work only: each target set is set back to `prior`. Where all went back it
    raises the fault itself, and the switch-on says the module is still off; where Discord refuses
    one it raises `StepFailedOnDiscord` from the fault with the notice that permissions were left
    on the channel (`_left`), which the failure line and the reply carry.

    **A stop part-way** (a restart between two targets) leaves some targets set and the step not
    done: it runs again from the start, reading the record the record step marked done before it,
    so it sets every target again and takes back, on a fault, to what stood before the first
    attempt, never to what the stop left.

    **The undo** (`TAKE_BACK_PERMISSIONS`) runs only where the save does not go through, and sets
    each target the step reports applied back to `prior`, raising with the same notice where any is
    missing or refused."""

    async def run(ctx: StepContext) -> StepResult:
        record = _recorded(ctx.steps)
        if record is None:
            return StepResult(result={"skip": True})
        guild, channel = await _signup_channel(ctx.bot, int(record["channel_id"]))
        resolved = [(_target_of(guild, entry), entry) for entry in record["targets"]]
        if any(target is None for target, _entry in resolved):
            raise StepFailedOnDiscord(
                "a role the bot sets a signup permission for is not on the server"
            )
        applied: list[int] = []
        try:
            for index, (target, entry) in enumerate(resolved):
                merged = (
                    discord.PermissionOverwrite()
                    if entry["prior"] is None
                    else _overwrite(entry["prior"])
                )
                merged.update(
                    **{name: value for name, value in _overwrite(entry["wanted"]) if value is not None}
                )
                assert target is not None
                await channel.set_permissions(target, overwrite=merged)
                applied.append(index)
        except Exception as error:
            if not await _take_back(guild, channel, record, applied):
                raise StepFailedOnDiscord(
                    "the bot's permissions on the signup channel could not all be taken back",
                    notice=_left(int(record["channel_id"])),
                ) from error
            raise
        return StepResult(result={"applied": applied})

    async def undo(ctx: StepContext) -> StepResult:
        record = _recorded(ctx.steps)
        restored = next(
            (view.result for view in ctx.steps if view.name == RESTORE_PERMISSIONS), None
        ) or {}
        applied = restored.get("applied") or []
        if record is None or not applied:
            return StepResult(result={"skip": True})
        notice = _left(int(record["channel_id"]))
        try:
            guild, channel = await _signup_channel(ctx.bot, int(record["channel_id"]))
        except StepFailedOnDiscord as error:
            raise StepFailedOnDiscord(error.reason, notice=notice) from error
        if not await _take_back(guild, channel, record, applied):
            raise StepFailedOnDiscord(
                "the bot's permissions on the signup channel could not all be taken back",
                notice=notice,
            )
        return StepResult(result={"taken_back": applied})

    return Before(
        Step(RESTORE_PERMISSIONS, StepKind.ACT, run),
        undo=Step(TAKE_BACK_PERMISSIONS, StepKind.ACT, undo),
    )


def module_on_signup(
    *, modules: "ModuleService", config: "ConfigService", channel_uses_read: "AwaitedHook"
) -> Setting:
    """`module.on:signup`: check the signup channel the module kept, then raise the flag, create a
    bare configuration row where missing and clear a kept channel another setting took, with the
    audit entries, in one save.

    *channel_uses_read* is core's hook, which the check asks who holds the kept channel through.
    """

    async def check(ctx: CheckContext) -> Verdict:
        if await modules.is_enabled(SWITCH_KEY):
            return Verdict.refuse("⚠️ Signup module is already enabled.")
        season_number = await configuration_fixed(ctx.db_path)
        if season_number is not None:
            return Verdict.refuse(configuration_fixed_refusal(season_number, "enable"))
        return Verdict.go()

    async def save(db: "aiosqlite.Connection", payload: dict[str, Any]) -> Saved:
        named, taken, details = found_in(payload)
        await db.execute(
            "INSERT INTO signup_module_config "
            "(id, signup_channel_id, signups_open, signup_button_message_id, "
            "selected_tracks_json) "
            "VALUES (1, NULL, 0, NULL, '[]') "
            "ON CONFLICT(id) DO NOTHING"
        )
        await db.execute("UPDATE server_configs SET signup_module_enabled = 1")
        audits = [
            AuditRecord(change_type="MODULE_ENABLE", old_value={}, new_value={"module": "signup"})
        ]
        if taken:
            old_id = await clear_signup_channel_on(db)
            if old_id is not None:
                audits.append(
                    AuditRecord("SIGNUP_CHANNEL_SET", {"channel_id": old_id}, {"channel_id": None})
                )
        cursor = await db.execute("SELECT signup_channel_id FROM signup_module_config")
        kept = await cursor.fetchone()
        server_cfg = await config.get_server_config_on(db)
        return Saved(
            changed=True,
            audits=tuple(audits),
            details=tuple(details),
            result={
                "named": named,
                "channel_kept": kept is not None and kept["signup_channel_id"] is not None,
                "base_role_set": server_cfg is not None and server_cfg.base_role_id is not None,
                "driver_role_set": server_cfg is not None and server_cfg.driver_role_id is not None,
            },
            then=(PlannedStep(REFRESH_HUB),),
        )

    def reply(_payload: dict[str, Any], results: Mapping[str, dict[str, Any]]) -> str:
        saved = results["save"]
        named = saved["named"]
        steps = [] if named or saved["channel_kept"] else ["  • `/signup channel <channel>`"]
        if not saved["base_role_set"]:
            steps.append("  • `/bot base-role <role>`")
        if not saved["driver_role_set"]:
            steps.append("  • `/bot driver-role <role>`")
        text = named_reply("✅ Signup module enabled.", named)
        if not steps:
            return text
        return text + "\nNext steps — configure with:\n" + "\n".join(steps)

    return Setting(
        kind=module_on("signup"),
        command=lambda _payload: _ENABLE_COMMAND,
        doing=lambda _payload: "Turning the signup module on",
        check=check,
        save=save,
        reply=reply,
        writes=("server_configs", "signup_module_config"),
        not_saved=_still_off,
        before=(
            kept_channels_check(
                kept_channels, command=_ENABLE_COMMAND, channel_uses_read=channel_uses_read
            ),
            _record_permissions(config),
            _restore_permissions(),
        ),
        after={REFRESH_HUB: hub_refresh_after(_ENABLE_COMMAND)},
    )


__all__ = ["RECORD_PERMISSIONS", "RESTORE_PERMISSIONS", "TAKE_BACK_PERMISSIONS", "module_on_signup"]
