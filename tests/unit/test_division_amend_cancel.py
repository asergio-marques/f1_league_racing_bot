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

**Cancel's notice to the drivers cannot fail the cancellation.** The division is already stood
down and its jobs already cancelled by the time the message is attempted, so a channel that has
been deleted or that the bot cannot post in must be logged and stepped over — the alternative is
a half-cancelled division whose jobs are gone and whose status still says ACTIVE.
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import PendingConfig, PendingDivision, SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonImmutableError  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from models.season import SeasonStage  # noqa: E402

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


def _round(id: int):
    return SimpleNamespace(id=id, division_id=DIVISION_ID, round_number=id)


def _make_cog(
    db_path: str,
    *,
    cfg: PendingConfig | None = None,
    divisions=None,
    remaining=None,
    season=SimpleNamespace(id=SEASON_ID, status="ACTIVE", stage=SeasonStage.ONGOING),
    immutable: bool = False,
    rounds=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
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
    cog._get_pending_for_server = MagicMock(return_value=cfg)
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


async def _cancel(cog, interaction, *, name="Pro", confirm="CONFIRM"):
    return await undecorate(SeasonCog.division_cancel)(cog, interaction, name, confirm)


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
            "SELECT change_type, old_value, new_value FROM audit_entries WHERE server_id = ?",
            (SERVER_ID,),
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
    cfg = PendingConfig(server_id=SERVER_ID, divisions=[PendingDivision(name="Pro")])
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

    logged = str(cog.bot.output_router.post_log.await_args.args[1])
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
        server_id=SERVER_ID,
        divisions=[PendingDivision(name="Pro", tier=1), PendingDivision(name="Am", tier=2)],
    )
    cog = _make_cog(db_path, cfg=cfg)

    await _rename(cog, _interaction())

    assert [d.name for d in cfg.divisions] == ["Elite", "Am"]


async def test_only_the_renamed_division_changes_in_the_pending_config(tmp_path):
    db_path = await _make_db(tmp_path)
    cfg = PendingConfig(
        server_id=SERVER_ID,
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

    logged = str(cog.bot.output_router.post_log.await_args.args[1])
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
    cfg = PendingConfig(server_id=SERVER_ID, divisions=[PendingDivision(name="Pro")])
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

    logged = str(cog.bot.output_router.post_log.await_args.args[1])
    assert fragment in logged
    assert logged.count("\n") == 2  # the header, the division, and the one field


async def test_an_amended_role_is_logged_by_name(tmp_path):
    """An id in the log tells a manager reading it nothing; the role's name does."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _amend(cog, _interaction(), role=_role(888, "Elite drivers"))

    assert "Elite drivers" in str(cog.bot.output_router.post_log.await_args.args[1])


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
        cog.bot, interaction.guild_id
    )


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


async def test_the_drivers_are_told_in_their_division_channel(tmp_path):
    """A cancellation nobody announced leaves a division waiting for a forecast that is
    never coming."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_notice")
    channel = MagicMock()
    channel.send = AsyncMock()
    cog = _make_cog(db_path, divisions=[_division(channel=4242)])

    await _cancel(cog, _interaction(channel=channel))

    posted = str(channel.send.await_args.args[0])
    assert "Division Cancelled" in posted
    assert "Pro" in posted


async def test_a_missing_channel_does_not_stop_the_cancellation(tmp_path):
    """The division is already stood down and its jobs already gone by the time the notice
    is attempted; failing here would leave it half-cancelled."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_nochannel")
    cog = _make_cog(db_path, divisions=[_division(channel=4242)])

    await _cancel(cog, _interaction(channel=None))

    cog.bot.season_service.cancel_division.assert_awaited_once()


async def test_a_channel_that_refuses_the_notice_does_not_stop_the_cancellation(tmp_path):
    db_path = await _make_db(tmp_path, status="ACTIVE", name="cancel_refused")
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "nope"))
    cog = _make_cog(db_path, divisions=[_division(channel=4242)])
    interaction = _interaction(channel=channel)

    await _cancel(cog, interaction)

    cog.bot.season_service.cancel_division.assert_awaited_once()
    assert "cancelled" in _replied(interaction)


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

    logged = str(cog.bot.output_router.post_log.await_args.args[1])
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
