"""Deleting, renaming, amending and cancelling a division.

Issue #208. The four commands that change a division after it exists, none of which were
covered. They divide cleanly in two, and the division is the point of the file.

**Delete, rename and amend are setup-only. Cancel is for a season already running.** A season in
SETUP has nothing armed — no jobs, no roles granted, no lineups posted — so a division in it can
simply go. Once the season is ACTIVE that is no longer true, and `/division cancel` is the
irreversible, confirmation-guarded alternative: it stands the division down, cancels every
scheduled job it owns and tells the drivers, rather than removing rows that results and
standings now point at. Each command's own refusal names itself, so a manager who reached for
the wrong one is told which one they are in — four separate tests, because a reader collapsing
them into one shared guard would lose exactly that.

**A name is a division's identity in every command that follows.** They all resolve a division
by name, since a slash command takes a name and not an id, so rename and amend both refuse a
name another division already holds — and both exclude the division being renamed from that
check, or renaming `Pro` to `Pro` would refuse itself.

**Amend changes only what it was given.** It builds its `UPDATE` from the arguments that are not
`None`, so a manager amending a tier does not have their role silently rewritten with a default.
That is parametrised over each field alone and over combinations, because the failure mode of a
hand-built `SET` clause is a column nobody meant to touch.

**Amend is audited, with the old values and the new.** It writes the division's columns directly
rather than through the service, so the audit row is the only record that the tier a season was
approved with is not the tier it was built with.

**Cancel asks for `CONFIRM` typed exactly.** It is irreversible and it stands down a division's
whole calendar; a click-through would make it an accident rather than a decision.

**Cancel's announcements cannot fail the cancellation.** The division is already stood down
and its jobs already cancelled by the time the modules are told, so a channel that has been
deleted or that the bot cannot post in is stepped over and named to the admin — the alternative
is a half-cancelled division whose jobs are gone and whose status still says ACTIVE. What each
module says, and where, is `cancellation_notice_service`'s and is tested there (#175).
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.cogs.season_cog import PendingConfig, PendingDivision, SeasonCog
from leaguebot.core.services.cancellation_notice_service import CancellationReport
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.undecorate import undecorate
from leaguebot.core.models.season import SeasonStage

SERVER_ID = 10008
SEASON_ID = 1
DIVISION_ID = 11
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, status: str = "SETUP", name: str = "division_amend") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _division(name="Pro", tier=1, *, id=DIVISION_ID, status="ACTIVE", channel=None):
    return SimpleNamespace(
        id=id,
        name=name,
        tier=tier,
        status=status,
        mention_role_id=555,
        forecast_channel_id=channel,
    )


def _round(id: int, status: str = "NOT_RUN"):
    return SimpleNamespace(id=id, division_id=DIVISION_ID, round_number=id, status=status)


def _make_cog(
    db_path: str,
    *,
    cfg: PendingConfig | None = None,
    divisions=None,
    remaining=None,
    season=SimpleNamespace(
        id=SEASON_ID, status="ACTIVE", stage=SeasonStage.ONGOING, season_number=4
    ),
    immutable: bool = False,
    rounds=None,
    stage: SeasonStage = SeasonStage.PLACEMENTS,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    # The stage of the season being set up, which the setup commands ask before they act.
    bot.season_service.get_stage = AsyncMock(return_value=stage)
    divisions = divisions if divisions is not None else [_division()]
    bot.season_service.get_divisions = AsyncMock(
        side_effect=[divisions, remaining if remaining is not None else divisions] * 4
    )
    bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=SeasonImmutableError("archived") if immutable else None
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else []
    )
    bot.season_service.delete_division = AsyncMock(return_value=None)
    bot.season_service.rename_division = AsyncMock(return_value=None)
    bot.season_service.cancel_division = AsyncMock(return_value=None)
    bot.season_service.wind_down_ongoing = AsyncMock(return_value=False)
    bot.scheduler_service = MagicMock()
    bot.scheduler_service.cancel_round = MagicMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    cog._pending = {ACTOR_ID: cfg} if cfg is not None else {}
    cog._get_pending = MagicMock(return_value=cfg)
    cog._reload_pending_from_db = AsyncMock(return_value=None)
    return cog


def _interaction(*, channel=None):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild = MagicMock()
    interaction.guild.get_channel = MagicMock(return_value=channel)
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.user.__str__ = lambda self: "Manager#0001"  # type: ignore[assignment]
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _run_by_the_manager(cog, command: str):
    """The manager's interaction for *command*, connected to the cog's log channel."""
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = command
    return interaction


def _logged(cog) -> list[str]:
    return [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _role(role_id: int = 999, name: str = "Am drivers"):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.mention = f"<@&{role_id}>"
    return role


async def _delete(cog, interaction, *, name="Pro"):
    return await undecorate(SeasonCog.division_delete)(cog, interaction, name)


async def _rename(cog, interaction, *, current="Pro", new="Elite"):
    return await undecorate(SeasonCog.division_rename)(cog, interaction, current, new)


async def _amend(cog, interaction, *, name="Pro", new_name=None, tier=None, role=None):
    return await undecorate(SeasonCog.division_amend)(
        cog, interaction, name, new_name, tier, role
    )


async def _cancel(cog, interaction, *, name="Pro", confirm="CONFIRM", failures=()):
    """Run the command with the modules' announcements stubbed, returning the stub."""
    announce = AsyncMock(return_value=CancellationReport(failures=list(failures)))
    with patch("leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce):
        await undecorate(SeasonCog.division_cancel)(cog, interaction, name, confirm)
    return announce


async def _division_row(db_path: str) -> dict:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name, tier, mention_role_id FROM divisions WHERE id = ?",
            (DIVISION_ID,),
        )
        return dict(await cursor.fetchone())


async def _audit_rows(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries",
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Only during setup — and each command says its own name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run,fragment",
    [
        (_delete, "/division delete"),
        (_rename, "/division rename"),
        (_amend, "/division amend"),
    ],
)
async def test_a_running_season_refuses_the_setup_commands(tmp_path, run, fragment):
    """A SETUP season has nothing armed; an ACTIVE one has jobs scheduled, roles granted and
    results pointing at these rows."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="amend_active")
    cog = _make_cog(db_path)
    interaction = _interaction()

    kwargs = {"new_name": "Elite"} if run is _amend else {}
    await run(cog, interaction, **kwargs)

    assert fragment in _replied(interaction)
    cog.bot.season_service.delete_division.assert_not_awaited()
    cog.bot.season_service.rename_division.assert_not_awaited()


@pytest.mark.parametrize("run", [_delete, _rename, _amend])
async def test_an_unknown_division_is_refused_by_name(tmp_path, run):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    kwargs = {"new_name": "Elite"} if run is _amend else {}
    if run is _rename:
        kwargs = {"current": "Rookie"}
        await run(cog, interaction, **kwargs)
    else:
        await run(cog, interaction, name="Rookie", **kwargs)

    assert "Rookie" in _replied(interaction)
    assert "not found" in _replied(interaction)


# ---------------------------------------------------------------------------
# /division delete
# ---------------------------------------------------------------------------


async def test_a_division_is_deleted(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _delete(cog, interaction)

    cog.bot.season_service.delete_division.assert_awaited_once_with(DIVISION_ID)
    assert "deleted" in _replied(interaction)


async def test_the_remaining_divisions_are_listed_after_a_delete(tmp_path):
    """So a manager sees what the season now holds rather than having to ask for it — the
    same reason `/division add` lists them."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, divisions=[_division(), _division("Am", 2, id=12)],
        remaining=[_division("Am", 2, id=12)],
    )
    interaction = _interaction()

    await _delete(cog, interaction)

    assert "Am" in _replied(interaction)


async def test_a_delete_reloads_the_pending_config(tmp_path):
    """The snapshot in memory still holds the division that was just removed; without the
    reload the next `/season placements-review` would show a division that no longer exists."""
    db_path = await _make_db(tmp_path)
    cfg = PendingConfig(divisions=[PendingDivision(name="Pro")])
    cog = _make_cog(db_path, cfg=cfg)

    await _delete(cog, _interaction())

    cog._reload_pending_from_db.assert_awaited_once_with(cfg)


async def test_a_delete_without_a_pending_config_still_works(tmp_path):
    """The rows are the truth; a missing snapshot is a restart, not a reason to refuse."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=None)

    await _delete(cog, _interaction())

    cog.bot.season_service.delete_division.assert_awaited_once()
    cog._reload_pending_from_db.assert_not_awaited()


async def test_the_delete_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _delete(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division delete" in logged
    assert "Pro" in logged


# ---------------------------------------------------------------------------
# /division rename
# ---------------------------------------------------------------------------


async def test_a_division_is_renamed(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _rename(cog, interaction)

    cog.bot.season_service.rename_division.assert_awaited_once_with(DIVISION_ID, "Elite")
    assert "renamed to" in _replied(interaction)


async def test_renaming_onto_another_divisions_name_is_refused(tmp_path):
    """Both names would then resolve to two divisions, and every command that takes a name
    would be ambiguous."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, divisions=[_division(), _division("Am", 2, id=12)])
    interaction = _interaction()

    await _rename(cog, interaction, new="Am")

    assert "already exists" in _replied(interaction)
    cog.bot.season_service.rename_division.assert_not_awaited()


async def test_a_division_may_be_renamed_to_a_variant_of_its_own_name(tmp_path):
    """The uniqueness check excludes the division being renamed, or correcting `pro` to
    `Pro` would refuse itself."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _rename(cog, _interaction(), current="Pro", new="PRO")

    cog.bot.season_service.rename_division.assert_awaited_once_with(DIVISION_ID, "PRO")


async def test_the_rename_reaches_the_pending_config(tmp_path):
    """Renamed in place rather than reloaded, because the pending division carries rounds a
    manager may have typed and not yet committed."""
    db_path = await _make_db(tmp_path)
    cfg = PendingConfig(
        divisions=[PendingDivision(name="Pro", tier=1), PendingDivision(name="Am", tier=2)],
    )
    cog = _make_cog(db_path, cfg=cfg)

    await _rename(cog, _interaction())

    assert [d.name for d in cfg.divisions] == ["Elite", "Am"]


async def test_only_the_renamed_division_changes_in_the_pending_config(tmp_path):
    db_path = await _make_db(tmp_path)
    cfg = PendingConfig(
        divisions=[PendingDivision(name="Pro"), PendingDivision(name="Pro Am")],
    )
    cog = _make_cog(db_path, cfg=cfg)

    await _rename(cog, _interaction())

    assert [d.name for d in cfg.divisions] == ["Elite", "Pro Am"]


async def test_the_rename_is_logged_with_both_names(tmp_path):
    """A log saying only the new name cannot be read back to what was renamed."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _rename(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "Pro" in logged and "Elite" in logged


# ---------------------------------------------------------------------------
# /division amend
# ---------------------------------------------------------------------------


async def test_amending_nothing_is_refused(tmp_path):
    """Every argument is optional, so the command is callable with none of them — and an
    `UPDATE` with an empty `SET` clause is a syntax error, not a no-op."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction)

    assert "at least one of" in _replied(interaction)


async def test_a_new_name_is_written(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), new_name="Elite")

    assert (await _division_row(db_path))["name"] == "Elite"


async def test_a_new_tier_is_written(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), tier=3)

    assert (await _division_row(db_path))["tier"] == 3


async def test_a_new_role_is_written(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), role=_role(888))

    assert (await _division_row(db_path))["mention_role_id"] == 888


@pytest.mark.parametrize(
    "kwargs,untouched",
    [
        ({"new_name": "Elite"}, {"tier": 1, "mention_role_id": 555}),
        ({"tier": 3}, {"name": "Pro", "mention_role_id": 555}),
        ({"role": "role"}, {"name": "Pro", "tier": 1}),
    ],
)
async def test_amending_one_field_leaves_the_others_alone(tmp_path, kwargs, untouched):
    """The `SET` clause is built by hand from the arguments that are not None. A field
    slipping into it with a default is how a manager amending a tier finds the division
    mentioning a role nobody chose."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    if kwargs.get("role") == "role":
        kwargs["role"] = _role(888)

    await _amend(cog, _interaction(), **kwargs)

    row = await _division_row(db_path)
    for column, value in untouched.items():
        assert row[column] == value


async def test_all_three_fields_may_be_amended_at_once(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), new_name="Elite", tier=3, role=_role(888))

    assert await _division_row(db_path) == {
        "name": "Elite",
        "tier": 3,
        "mention_role_id": 888,
    }


async def test_amending_onto_another_divisions_name_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, divisions=[_division(), _division("Am", 2, id=12)])
    interaction = _interaction()

    await _amend(cog, interaction, new_name="Am")

    assert "already exists" in _replied(interaction)
    assert (await _division_row(db_path))["name"] == "Pro"


async def test_a_division_may_be_amended_to_a_variant_of_its_own_name(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), new_name="PRO")

    assert (await _division_row(db_path))["name"] == "PRO"


async def test_the_amendment_records_what_it_replaced(tmp_path):
    """Written directly to the columns rather than through the service, so this row is the
    only record that the tier a season was approved with is not the one it was built with."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), tier=3)

    rows = await _audit_rows(db_path)
    assert [r["change_type"] for r in rows] == ["DIVISION_AMENDED"]
    assert json.loads(rows[0]["old_value"]) == {
        "name": "Pro",
        "tier": 1,
        "mention_role_id": 555,
    }


async def test_the_audit_carries_the_unchanged_fields_too(tmp_path):
    """So the row reads as the division's whole state either side of the amendment, rather
    than as a diff a reader has to reconstruct against whatever it is today."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), tier=3)

    new = json.loads((await _audit_rows(db_path))[0]["new_value"])
    assert new == {"name": "Pro", "tier": 3, "mention_role_id": 555}


async def test_an_amendment_reloads_the_pending_config(tmp_path):
    db_path = await _make_db(tmp_path)
    cfg = PendingConfig(divisions=[PendingDivision(name="Pro")])
    cog = _make_cog(db_path, cfg=cfg)

    await _amend(cog, _interaction(), tier=3)

    cog._reload_pending_from_db.assert_awaited_once_with(cfg)


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        ({"new_name": "Elite"}, "new_name: Elite"),
        ({"tier": 3}, "tier: 3"),
    ],
)
async def test_the_log_names_only_what_was_amended(tmp_path, kwargs, fragment):
    """A log listing every field would report an unchanged tier as though it had moved."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), **kwargs)

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert fragment in logged
    assert logged.count("\n") == 2  # the header, the division, and the one field


async def test_an_amended_role_is_logged_by_name(tmp_path):
    """An id in the log tells a manager reading it nothing; the role's name does."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), role=_role(888, "Elite drivers"))

    assert "Elite drivers" in str(cog.bot.output_router.post_log.await_args.args[0])


# ---------------------------------------------------------------------------
# /division cancel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("confirm", ["", "confirm", "CONFIRM ", "yes"])
async def test_cancelling_without_the_exact_word_is_refused(tmp_path, confirm):
    """Irreversible, and it stands down a whole calendar. Accepting anything close would
    make it an accident rather than a decision."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_confirm")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _cancel(cog, interaction, confirm=confirm)

    assert "CONFIRM" in _replied(interaction)
    cog.bot.season_service.cancel_division.assert_not_awaited()


async def test_a_division_is_cancelled(tmp_path):
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_ok")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _cancel(cog, interaction)

    cog.bot.season_service.cancel_division.assert_awaited_once()
    kwargs = cog.bot.season_service.cancel_division.await_args.kwargs
    assert kwargs["division_id"] == DIVISION_ID
    assert kwargs["actor_id"] == ACTOR_ID
    assert "cancelled" in _replied(interaction)


async def test_cancelling_a_division_winds_a_finished_season_down(tmp_path):
    """The division may have been the season's last (issue #220)."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="division_cancel_wind_down")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _cancel(cog, interaction)

    cog.bot.season_service.wind_down_ongoing.assert_awaited_once_with(
        cog.bot
    )


async def test_a_wind_down_that_fails_after_a_division_cancel_is_named_as_not_done(tmp_path):
    """The core specification's record of what changed: an outcome is recorded as it is. Division
    Pro of a season being raced is cancelled, and the season it may have finished cannot then
    be wound down (the wind-down raises). The cancellation stands; the reply and the one success
    line each name the wind-down as not done, saying the season could not be moved to pending
    completion and that /season complete does it."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="division_cancel_wind_down_fails")
    cog = _make_cog(db_path)
    cog.bot.season_service.wind_down_ongoing = AsyncMock(side_effect=RuntimeError("disk full"))
    interaction = _interaction()

    await _cancel(cog, interaction)

    cog.bot.season_service.cancel_division.assert_awaited_once()
    replied = _replied(interaction)
    assert replied.startswith("\u2705 Division **Pro** cancelled."), replied
    assert "pending completion" in replied.lower(), replied
    assert "/season complete" in replied, replied
    [line] = _logged(cog)
    assert line.startswith(f"Manager (<@{ACTOR_ID}>) | /division cancel | Success"), line
    [not_done] = [row for row in line.splitlines() if row.strip().startswith("not done:")]
    assert "pending completion" in not_done.lower(), line
    assert "/season complete" in not_done, line


async def test_cancelling_needs_an_active_season(tmp_path):
    """There is no running division to stand down; in setup `/division delete` is the one
    that applies."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_noseason")
    cog = _make_cog(db_path, season=None)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "only while the season is ongoing" in _replied(interaction)
    cog.bot.season_service.cancel_division.assert_not_awaited()


async def test_an_archived_season_cannot_be_cancelled_into(tmp_path):
    """A COMPLETED season is the league's record of what happened, and cancelling a division
    inside one would rewrite a result already published."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_archived")
    cog = _make_cog(db_path, immutable=True)
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "archived" in _replied(interaction)
    cog.bot.season_service.cancel_division.assert_not_awaited()


async def test_an_already_cancelled_division_is_refused(tmp_path):
    """Running it twice would cancel jobs that are already gone and post a second notice to
    drivers who have had one."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_twice")
    cog = _make_cog(db_path, divisions=[_division(status="CANCELLED")])
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "already cancelled" in _replied(interaction)
    cog.bot.season_service.cancel_division.assert_not_awaited()


async def test_every_scheduled_round_is_cancelled_with_the_division(tmp_path):
    """Otherwise the division is stood down and its sessions still fire — posting forecasts
    and opening check-ins for rounds nobody is racing."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_rounds")
    cog = _make_cog(db_path, rounds=[_round(1), _round(2), _round(3)])

    await _cancel(cog, _interaction())

    cancelled = [c.args[0] for c in cog.bot.scheduler_service.cancel_round.call_args_list]
    assert cancelled == [1, 2, 3]


async def test_the_jobs_go_before_the_division_is_stood_down(tmp_path):
    """A job that fires between the two would find a division mid-cancellation."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_order")
    cog = _make_cog(db_path, rounds=[_round(1)])
    order: list[str] = []
    cog.bot.scheduler_service.cancel_round.side_effect = lambda *_: order.append("job")
    cog.bot.season_service.cancel_division.side_effect = (
        lambda **_: order.append("division")
    )

    await _cancel(cog, _interaction())

    assert order == ["job", "division"]


async def test_the_modules_are_told_the_division_is_off(tmp_path):
    """Each enabled module says so in its own channel — never core, and never the forecast
    channel regardless of the weather module (#175)."""
    from leaguebot.core.services import cancellation_notice_service as cns

    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_notice")
    channel = MagicMock()
    channel.send = AsyncMock()
    cog = _make_cog(db_path, divisions=[_division(channel=4242)])

    announce = await _cancel(cog, _interaction(channel=channel))

    announce.assert_awaited_once()
    assert announce.await_args.kwargs["scope"] == cns.SCOPE_DIVISION
    assert announce.await_args.kwargs["season_number"] == 4
    assert [d.id for d in announce.await_args.args[2]] == [DIVISION_ID]
    channel.send.assert_not_awaited()


async def test_only_the_rounds_the_cascade_calls_off_are_named(tmp_path):
    """A round already raced keeps its results and its check-in; one already cancelled was
    announced when it was."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_round_ids")
    cog = _make_cog(
        db_path,
        rounds=[
            _round(1, "FINAL"),
            _round(2, "AWAITING_RESULTS"),
            _round(3, "NOT_RUN"),
            _round(4, "CANCELLED"),
            _round(5, "AWAITING_REPORT_VERDICTS"),
        ],
    )

    announce = await _cancel(cog, _interaction())

    assert announce.await_args.kwargs["round_ids"] == frozenset({2, 3})


async def test_the_check_in_audit_reaches_the_log(tmp_path):
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_audit")
    cog = _make_cog(db_path)
    announce = AsyncMock(return_value=CancellationReport(audit="\n  check-in, Pro, Round 2"))
    with patch("leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce):
        await undecorate(SeasonCog.division_cancel)(cog, _interaction(), "Pro", "CONFIRM")
    assert "check-in, Pro, Round 2" in cog.bot.output_router.post_log.await_args.args[0]


async def test_the_modules_are_told_after_the_division_is_recorded_cancelled(tmp_path):
    """The calendar is posted again as it now stands, so the rounds must already be
    recorded cancelled when it is read."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_notice_order")
    cog = _make_cog(db_path)
    order: list[str] = []
    cog.bot.season_service.cancel_division.side_effect = (
        lambda **_: order.append("division")
    )
    announce = AsyncMock(side_effect=lambda *a, **kw: order.append("announce") or CancellationReport())
    with patch("leaguebot.core.services.cancellation_notice_service.announce_cancellation", new=announce):
        await undecorate(SeasonCog.division_cancel)(cog, _interaction(), "Pro", "CONFIRM")
    assert order == ["division", "announce"]


async def test_what_could_not_be_told_is_named_to_the_admin(tmp_path):
    from leaguebot.core.services.cancellation_notice_service import NoticeFailure

    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_refused")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _cancel(
        cog, interaction,
        failures=[NoticeFailure("Pro", "forecast channel", "the channel could not be found")],
    )

    cog.bot.season_service.cancel_division.assert_awaited_once()
    replied = _replied(interaction)
    assert "cancelled" in replied
    assert "forecast channel: the channel could not be found" in replied
    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "not notified: **Pro** — forecast channel" in logged


async def test_the_cancellation_is_deferred_only_once_the_gates_are_past(tmp_path):
    """Every refusal above answers through `response.send_message`, which after a defer is
    a 404 — so the defer sits immediately before the work and not at the top."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_defer")
    cog = _make_cog(db_path)
    refused = _interaction()
    allowed = _interaction()

    await _cancel(cog, refused, confirm="no")
    await _cancel(_make_cog(db_path), allowed)

    refused.response.defer.assert_not_awaited()
    allowed.response.defer.assert_awaited_once()


async def test_the_cancellation_is_logged(tmp_path):
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_log")
    cog = _make_cog(db_path)

    await _cancel(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division cancel" in logged
    assert "Pro" in logged


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_a_division_is_cancelled_only_while_the_season_is_ongoing(tmp_path, stage_name):
    """Issue #220: in Pending completion every division is already done."""
    from types import SimpleNamespace as _NS

    db_path = await _make_db(tmp_path, status="ACTIVE", name=f"cancel_{stage_name}")
    cog = _make_cog(db_path, season=_NS(id=SEASON_ID, status="ACTIVE", stage=SeasonStage(stage_name)))
    interaction = _interaction()

    await _cancel(cog, interaction)

    assert "only while the season is ongoing" in _replied(interaction)


# ---------------------------------------------------------------------------
# A new name is held to the rules every typed name is (#362)
# ---------------------------------------------------------------------------


async def test_a_division_renamed_with_a_role_mention_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _rename(cog, interaction, new="<@&987654321098765432> Elite")

    assert "division name" in _replied(interaction)
    cog.bot.season_service.rename_division.assert_not_awaited()


async def test_a_division_amended_to_a_name_with_everyone_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _amend(cog, interaction, new_name="@everyone Elite")

    assert "division name" in _replied(interaction)
    assert (await _division_row(db_path))["name"] == "Pro"


# ---------------------------------------------------------------------------
# /division amend holds a tier to the rule /division add does (#482)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True, reason="#482: /division amend does not yet hold a new tier to the tier rule"
)
@pytest.mark.parametrize(
    "tier,reply",
    [
        pytest.param(0, "\u26d4 Tier must be 1 or higher.", id="zero"),
        pytest.param(-1, "\u26d4 Tier must be 1 or higher.", id="negative"),
        pytest.param(
            2,
            "\u26d4 A division with tier **2** already exists in this setup.",
            id="another_divisions_tier",
        ),
    ],
)
async def test_amending_to_a_tier_the_rule_refuses_is_refused_in_adds_words(tmp_path, tier, reply):
    """The core specification's Divisions: a tier is unique within its season and no lower than
    1. Pro holds tier 1 and Am tier 2; the manager amends Pro's tier. The refusal is
    `/division add`'s, word for word, and is recorded; Pro keeps its tier and nothing is
    audited."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, divisions=[_division(), _division("Am", 2, id=12)])
    interaction = _run_by_the_manager(cog, "division amend")

    await _amend(cog, interaction, tier=tier)

    assert _replied(interaction) == reply
    assert (await _division_row(db_path))["tier"] == 1
    assert await _audit_rows(db_path) == []
    assert _logged(cog) == [
        f"\u26d4 `/division amend` refused for Manager (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]


# ---------------------------------------------------------------------------
# Asking for what already stands changes nothing, and says so (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a command that changes nothing because
# nothing was asked of it records that nothing was changed. No audit entry is written, since no
# value moved; the reply says nothing changed; and the log holds one line, in the success form,
# saying so. Only an exact match is nothing: "Pro" to "PRO" is a rename (above).

_NO_OP = pytest.mark.xfail(
    strict=True, reason="#482: /division rename and /division amend do not yet tell a no-op apart"
)


def _says_nothing_changed(text: str) -> bool:
    return "nothing" in text.lower() and "chang" in text.lower()


@_NO_OP
async def test_renaming_a_division_to_its_own_name_changes_nothing(tmp_path):
    """Pro, in a season in placements, is renamed to Pro."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _run_by_the_manager(cog, "division rename")

    await _rename(cog, interaction, current="Pro", new="Pro")

    cog.bot.season_service.rename_division.assert_not_awaited()
    assert _says_nothing_changed(_replied(interaction))
    assert "renamed to" not in _replied(interaction)
    assert await _audit_rows(db_path) == []
    [line] = _logged(cog)
    assert line.startswith(f"Manager (<@{ACTOR_ID}>) | /division rename |")
    assert _says_nothing_changed(line)


@_NO_OP
@pytest.mark.parametrize(
    "asked",
    [
        pytest.param({"new_name": "Pro"}, id="its_own_name"),
        pytest.param({"tier": 1}, id="its_own_tier"),
        pytest.param({"role": 555}, id="its_own_role"),
        pytest.param({"new_name": "Pro", "tier": 1, "role": 555}, id="every_value_as_it_stands"),
    ],
)
async def test_amending_a_division_to_the_values_that_stand_changes_nothing(tmp_path, asked):
    """Pro, tier 1, role 555, in a season in placements, is amended to values it already holds."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _run_by_the_manager(cog, "division amend")
    if "role" in asked:
        asked = {**asked, "role": _role(asked["role"])}

    await _amend(cog, interaction, **asked)

    assert await _division_row(db_path) == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert await _audit_rows(db_path) == []
    assert _says_nothing_changed(_replied(interaction))
    assert "amended" not in _replied(interaction)
    [line] = _logged(cog)
    assert line.startswith(f"Manager (<@{ACTOR_ID}>) | /division amend |")
    assert _says_nothing_changed(line)


# ---------------------------------------------------------------------------
# A division is named as it is named, not as it was typed (#482, F8)
# ---------------------------------------------------------------------------
#
# Rename and amend find the division without regard to case, so a manager may type `pro` for
# Pro. The core specification's Divisions: "its name shall be what the bot displays". The reply
# and the log line name the division as it stands, never as the manager typed it.


@pytest.mark.xfail(
    strict=True,
    reason="#482: /division rename and /division amend name the division as the manager typed it",
)
@pytest.mark.parametrize(
    "command,run,done",
    [
        pytest.param(
            "division rename",
            lambda cog, interaction: _rename(cog, interaction, current="pro", new="Elite"),
            "renamed to",
            id="rename",
        ),
        pytest.param(
            "division amend",
            lambda cog, interaction: _amend(cog, interaction, name="pro", tier=3),
            "amended",
            id="amend",
        ),
    ],
)
async def test_a_division_typed_in_another_case_is_named_as_it_is_named(
    tmp_path, command, run, done
):
    """Pro, in a season in placements, is renamed to Elite, or amended to tier 3, by a manager
    who typed its name as `pro`."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _run_by_the_manager(cog, command)

    await run(cog, interaction)

    replied = _replied(interaction)
    first = replied.splitlines()[0]
    assert done in first
    assert "**Pro**" in first
    assert "pro" not in {word.strip("*`:,.()") for word in replied.split()}
    [line] = _logged(cog)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert f"/{command}" in line
    assert "Pro" in words
    assert "pro" not in words


# ---------------------------------------------------------------------------
# The setup commands run in Placements alone (#482, F4)
# ---------------------------------------------------------------------------
#
# The core specification: "Divisions shall be created and deleted, and rounds added and deleted,
# only while the season is in Placements", and "A division may be renamed, and its name, tier
# and role amended, while its season is in Placements alone". A season being set up but not yet
# in Placements is refused in the words each command uses today, and the refusal is recorded.
# In Placements each still works: every test above runs there.

_PLACEMENTS_ONLY = pytest.mark.xfail(
    strict=True,
    reason="#482: /division delete, rename and amend still act on a season being set up "
    "before it reaches placements",
)


async def _amend_the_tier(cog, interaction):
    return await _amend(cog, interaction, tier=2)


@_PLACEMENTS_ONLY
@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.SIGNUPS],
    ids=lambda stage: stage.value.lower(),
)
@pytest.mark.parametrize(
    "command, run, reply",
    [
        pytest.param(
            "division delete",
            _delete,
            "\u274c `/division delete` can only be used while the season is in placements.",
            id="delete",
        ),
        pytest.param(
            "division rename",
            _rename,
            "\u274c `/division rename` can only be used while the season is in placements.",
            id="rename",
        ),
        pytest.param(
            "division amend",
            _amend_the_tier,
            "\u274c `/division amend` is only permitted while the season is in placements.",
            id="amend",
        ),
    ],
)
async def test_a_setup_command_before_placements_is_refused_and_recorded(
    tmp_path, command, run, reply, stage
):
    """A season being set up, still in configuration, waiting or signups, holds division Pro at
    tier 1. The manager deletes Pro, renames it Elite, or amends it to tier 2. Each is refused
    in today's words and recorded, and Pro stands as it was."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, stage=stage)
    interaction = _run_by_the_manager(cog, command)

    await run(cog, interaction)

    assert _replied(interaction) == reply
    cog.bot.season_service.delete_division.assert_not_awaited()
    cog.bot.season_service.rename_division.assert_not_awaited()
    assert await _division_row(db_path) == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert await _audit_rows(db_path) == []
    assert _logged(cog) == [
        f"\u26d4 `/{command}` refused for Manager (<@{ACTOR_ID}>) \u2014 {reply[2:]}"
    ]



# ---------------------------------------------------------------------------
# Every other refusal of /division delete, rename and amend is recorded (#482)
# ---------------------------------------------------------------------------
#
# The core specification's "The record of what changed": a refusal is one line naming the
# member, what was refused and why. Each reply stays word for word as today, and goes as today's
# does, as the first answer to the interaction. /division amend with no option stays a refusal:
# something was asked for and nothing given.


def _as_discord(interaction):
    """Make *interaction* read as Discord's does: not answered until the command replies or
    defers, and answered from then on."""
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    return interaction


async def _rename_to_a_mention(cog, interaction):
    return await _rename(cog, interaction, new="<@123456789012345678>")


async def _rename_onto_am(cog, interaction):
    return await _rename(cog, interaction, new="am")


async def _amend_nothing(cog, interaction):
    return await _amend(cog, interaction)


async def _amend_to_a_mention(cog, interaction):
    return await _amend(cog, interaction, new_name="<@123456789012345678>")


async def _amend_onto_am(cog, interaction):
    return await _amend(cog, interaction, new_name="AM")


async def _delete_elite(cog, interaction):
    return await _delete(cog, interaction, name="Elite")


async def _rename_elite(cog, interaction):
    return await _rename(cog, interaction, current="Elite", new="Rookie")


async def _amend_elite(cog, interaction):
    return await _amend(cog, interaction, name="Elite", tier=3)


_A_MENTION_REFUSED = (
    "❌ A mention of a member in the division name would notify them wherever it is "
    "posted. Remove it, then try again."
)


@pytest.mark.parametrize(
    "command, run, arranged, reply",
    [
        pytest.param(
            "division delete", _delete, {"status": "ACTIVE"},
            "❌ `/division delete` can only be used while the season is in placements.",
            id="delete-no_season_being_set_up",
        ),
        pytest.param(
            "division delete", _delete_elite, {},
            "❌ Division `Elite` not found.",
            id="delete-an_unknown_division",
        ),
        pytest.param(
            "division rename", _rename, {"status": "ACTIVE"},
            "❌ `/division rename` can only be used while the season is in placements.",
            id="rename-no_season_being_set_up",
        ),
        pytest.param(
            "division rename", _rename_elite, {},
            "❌ Division `Elite` not found.",
            id="rename-an_unknown_division",
        ),
        pytest.param(
            "division rename", _rename_to_a_mention, {},
            _A_MENTION_REFUSED,
            id="rename-a_mention_in_the_name",
        ),
        pytest.param(
            "division rename", _rename_onto_am, {},
            "❌ A division named **am** already exists.",
            id="rename-a_name_already_taken",
        ),
        pytest.param(
            "division amend", _amend_nothing, {},
            "❌ Provide at least one of: `new_name`, `tier`, `role`.",
            id="amend-no_option",
        ),
        pytest.param(
            "division amend", _amend_the_tier, {"status": "ACTIVE"},
            "❌ `/division amend` is only permitted while the season is in placements.",
            id="amend-no_season_being_set_up",
        ),
        pytest.param(
            "division amend", _amend_elite, {},
            "❌ Division `Elite` not found.",
            id="amend-an_unknown_division",
        ),
        pytest.param(
            "division amend", _amend_to_a_mention, {},
            _A_MENTION_REFUSED,
            id="amend-a_mention_in_the_name",
        ),
        pytest.param(
            "division amend", _amend_onto_am, {},
            "❌ A division named **AM** already exists.",
            id="amend-a_name_already_taken",
        ),
    ],
)
async def test_every_other_division_delete_rename_and_amend_refusal_is_recorded(
    tmp_path, command, run, arranged, reply
):
    """A season in placements holding division Pro at tier 1 with role 555, and division Am at
    tier 2, unless the case says otherwise. The manager (id 77) is refused in eleven cases:
    /division delete, rename or amend with no season being set up (the season is being raced) or
    naming division Elite, which does not exist; a rename or an amend of Pro to a member's
    mention, or onto Am's name in another case; and an amend asking for nothing. The manager gets
    today's reply word for word and nothing else. Pro stands as it was, nothing is deleted or
    renamed, and no audit entry is written. The log channel gets exactly one line, "⛔
    `/division …` refused for Manager (<@77>) — " and the reply's words."""
    db_path = await _make_db(tmp_path, status=arranged.get("status", "SETUP"))
    cog = _make_cog(db_path, divisions=[_division(), _division("Am", 2, id=12)])
    interaction = _as_discord(_run_by_the_manager(cog, command))

    await run(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    cog.bot.season_service.delete_division.assert_not_awaited()
    cog.bot.season_service.rename_division.assert_not_awaited()
    assert await _division_row(db_path) == {"name": "Pro", "tier": 1, "mention_role_id": 555}
    assert await _audit_rows(db_path) == []
    assert _logged(cog) == [
        f"⛔ `/{command}` refused for Manager (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


async def _cancel_as_typed_lower(cog, interaction):
    return await _cancel(cog, interaction, confirm="confirm")


async def _cancel_elite(cog, interaction):
    return await _cancel(cog, interaction, name="Elite")


@pytest.mark.parametrize(
    "run, arranged, reply",
    [
        pytest.param(
            _cancel_as_typed_lower, {},
            "❌ Type exactly `CONFIRM` in the `confirm` field to proceed.",
            id="without_the_exact_word",
        ),
        pytest.param(
            _cancel, {"season": None},
            "❌ `/division cancel` is available only while the season is ongoing.",
            id="no_season_being_raced",
        ),
        pytest.param(
            _cancel,
            {"season": SimpleNamespace(
                id=SEASON_ID, status="ACTIVE", stage=SeasonStage.PENDING_COMPLETION,
                season_number=4,
            )},
            "❌ `/division cancel` is available only while the season is ongoing.",
            id="a_season_pending_completion",
        ),
        pytest.param(
            _cancel, {"immutable": True},
            "❌ This season is archived (COMPLETED) and cannot be modified.",
            id="an_archived_season",
        ),
        pytest.param(
            _cancel_elite, {},
            "❌ Division `Elite` not found.",
            id="an_unknown_division",
        ),
        pytest.param(
            _cancel, {"divisions": [_division(status="CANCELLED")]},
            "❌ Division **Pro** is already cancelled.",
            id="a_division_already_cancelled",
        ),
    ],
)
async def test_every_division_cancel_refusal_is_recorded(tmp_path, run, arranged, reply):
    """A season being raced holds division Pro, unless the case says otherwise. The admin (the
    manager here, id 77) runs /division cancel on Pro and is refused in six cases: typing
    'confirm' rather than CONFIRM; with no season being raced; on a season whose divisions are all
    done (pending completion); on a season archived as completed; naming division Elite, which
    does not exist; and on Pro already cancelled. The member gets today's reply word for word and
    nothing else; nothing is deferred, cancelled or announced. The log channel gets exactly one
    line, "⛔ `/division cancel` refused for Manager (<@77>) — " and the reply's words."""
    db_path = await _make_db(tmp_path, status="ACTIVE")
    cog = _make_cog(db_path, **arranged)
    interaction = _as_discord(_run_by_the_manager(cog, "division cancel"))

    announce = await run(cog, interaction)

    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)
    interaction.followup.send.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    cog.bot.season_service.cancel_division.assert_not_awaited()
    announce.assert_not_awaited()
    assert _logged(cog) == [
        f"⛔ `/division cancel` refused for Manager (<@{ACTOR_ID}>) — {reply[2:]}"
    ]


# ---------------------------------------------------------------------------
# /division cancel names the division as it is named (#482, F9)
# ---------------------------------------------------------------------------
#
# The division is found without regard to case, so a manager may type `pro` for Pro. The core
# specification's Divisions: "its name shall be what the bot displays". The reply and the log
# line name the division as it stands, never as the manager typed it, as rename and amend do.


@pytest.mark.xfail(
    strict=True,
    reason="#482: /division cancel names the division as the manager typed it",
)
@pytest.mark.parametrize(
    "arranged, answered",
    [
        pytest.param({}, "✅ Division **Pro** cancelled.", id="cancelled"),
        pytest.param(
            {"divisions": [_division(status="CANCELLED")]},
            "❌ Division **Pro** is already cancelled.",
            id="already_cancelled",
        ),
    ],
)
async def test_a_division_cancelled_as_typed_in_another_case_is_named_as_it_is_named(
    tmp_path, arranged, answered
):
    """A season being raced holds division Pro. The manager (id 77) runs /division cancel with
    CONFIRM, typing the division's name as 'pro', in two cases: Pro is cancelled, or Pro was
    already cancelled and the command is refused. The reply opens by naming the division as it
    is named, **Pro**, and nowhere shows 'pro'; the one log line (the success line, or the
    refusal line) names Pro and never 'pro'."""
    db_path = await _make_db(tmp_path, status="ACTIVE")
    cog = _make_cog(db_path, **arranged)
    interaction = _as_discord(_run_by_the_manager(cog, "division cancel"))

    await _cancel(cog, interaction, name="pro")

    replied = _replied(interaction)
    assert replied.startswith(answered), replied
    assert "pro" not in {word.strip("*`:,.()") for word in replied.split()}, replied
    [line] = _logged(cog)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert "/division cancel" in line, line
    assert "Pro" in words, line
    assert "pro" not in words, line


# ---------------------------------------------------------------------------
# /division delete names the division as it is named (#482, F12)
# ---------------------------------------------------------------------------
#
# As rename, amend and cancel: the division is found without regard to case, and the reply and
# the log line name it as it stands, never as the manager typed it.


@pytest.mark.xfail(
    strict=True,
    reason="#482: /division delete names the division as the manager typed it",
)
async def test_a_division_deleted_as_typed_in_another_case_is_named_as_it_is_named(tmp_path):
    """A season in placements holds divisions Pro and Am. The manager (id 77) runs /division
    delete typing the division's name as 'pro'. Pro is deleted; the reply opens '✅ Division
    **Pro** deleted.' and nowhere shows 'pro', and the one log line, naming /division delete,
    names Pro and never 'pro'."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, remaining=[_division("Am", 2, id=DIVISION_ID + 1)])
    interaction = _as_discord(_run_by_the_manager(cog, "division delete"))

    await _delete(cog, interaction, name="pro")

    cog.bot.season_service.delete_division.assert_awaited_once_with(DIVISION_ID)
    replied = _replied(interaction)
    assert replied.startswith("✅ Division **Pro** deleted."), replied
    assert "pro" not in {word.strip("*`:,.()") for word in replied.split()}, replied
    [line] = _logged(cog)
    words = {word.strip("*`:,.()") for word in line.split()}
    assert "/division delete" in line, line
    assert "Pro" in words, line
    assert "pro" not in words, line
