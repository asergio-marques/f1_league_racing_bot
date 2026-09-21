"""Adding a division during setup, and copying one with its whole calendar shifted.

Issue #208. Both commands were entirely uncovered. They are the two ways a division comes into
existence, and between them they hold the rules that make a season's divisions well-formed
before anything is scheduled.

**Tiers and names are unique within a season, and a tier starts at 1.** A duplicate name makes
every later command that resolves a division by name ambiguous — and they all do, because a
slash command takes a name, not an id. A duplicate tier breaks the sequential-integrity check
the approval runs, but it breaks it at *approval*, after the whole calendar has been built; both
commands therefore refuse it at the point it is typed. Each of the four refusals is a separate
test because a reader tidying four near-identical branches into one loses the wording that tells
a manager which of them they hit.

**`/division add` writes into the pending snapshot, `/division duplicate` writes to the
database.** Add is part of building a season that does not exist yet, so it edits the in-memory
config and rewrites the snapshot; duplicate copies rounds that are already rows, so it goes
through `season_service.duplicate_division` and then reloads the pending config from what it
wrote. The reload is the subtle half: without it the snapshot the manager sees next would not
contain the division they just created.

**An empty division slot is filled before a new one is appended.** `/season setup` lays out
blank slots for the number of divisions a league said it wanted, and adding must consume one
rather than growing the list past it — otherwise a league that asked for three divisions and
added three has six, half of them nameless.

**Duplicate warns rather than refuses.** A shifted calendar landing in the past, or two rounds
landing on the same datetime, is a mistake a manager makes with the offset arguments and is
usually one they want to know about and then fix — refusing would make them delete a division to
retry. The warnings are appended to a success, which is why they have to be pinned: a reader
seeing a warning in a success message is liable to "fix" it into a refusal.

**Duplicate defers only once the refusals are past.** Every gate above answers with
`response.send_message`, and the defer comes immediately before the copy itself — the expensive
part. Deferring at the top would be harmless but the reverse, replying through `response` after
a defer, is a 404, so both halves are pinned.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import PendingConfig, PendingDivision, SeasonCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 9908
SEASON_ID = 1
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, status: str = "SETUP", name: str = "division_add") -> str:
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
        await db.commit()
    return db_path


def _pending(*divisions: PendingDivision) -> PendingConfig:
    return PendingConfig(divisions=list(divisions), season_id=7)


def _division(name="Pro", tier=1, *, id=11):
    return SimpleNamespace(
        id=id, name=name, tier=tier, mention_role_id=555, forecast_channel_id=None
    )


def _round(offset_days: int = 7, *, id=1, at=None, number=1):
    return SimpleNamespace(
        id=id,
        division_id=11,
        round_number=number,
        track_name="Silverstone",
        status="SCHEDULED",
        format=SimpleNamespace(value="FEATURE"),
        scheduled_at=at
        if at is not None
        else datetime.now(timezone.utc) + timedelta(days=offset_days),
    )


def _make_cog(
    db_path: str,
    *,
    cfg: PendingConfig | None = None,
    divisions=None,
    rounds=None,
    duplicate_error: Exception | None = None,
    stage=None,
) -> SeasonCog:
    from models.season import SeasonStage

    bot = MagicMock()
    bot.db_path = db_path
    bot.season_service = MagicMock()
    bot.season_service.get_stage = AsyncMock(
        return_value=stage if stage is not None else SeasonStage.PLACEMENTS
    )
    bot.season_service.get_divisions = AsyncMock(
        return_value=divisions if divisions is not None else [_division()]
    )
    bot.season_service.get_division_rounds = AsyncMock(
        return_value=rounds if rounds is not None else [_round()]
    )
    bot.season_service.duplicate_division = AsyncMock(
        return_value=_division("Am", tier=2, id=12), side_effect=duplicate_error
    )
    bot.team_service = MagicMock()
    bot.team_service.seed_division_teams = AsyncMock(return_value=None)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    cog._pending = {ACTOR_ID: cfg} if cfg is not None else {}
    cog._get_pending = MagicMock(return_value=cfg)
    cog._snapshot_pending = AsyncMock(return_value=None)
    cog._reload_pending_from_db = AsyncMock(return_value=None)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
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


def _role(role_id: int = 555):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    return role


async def _add(cog, interaction, *, name="Am", tier=2, role=None):
    body = undecorate(SeasonCog.division_add)
    return await body(cog, interaction, name, role or _role(), tier)


async def _duplicate(
    cog,
    interaction,
    *,
    source="Pro",
    new_name="Am",
    tier=2,
    day_offset=0,
    hour_offset=0.0,
    role=None,
):
    body = undecorate(SeasonCog.division_duplicate)
    return await body(
        cog,
        interaction,
        source,
        new_name,
        role or _role(),
        tier,
        day_offset,
        hour_offset,
    )


# ---------------------------------------------------------------------------
# /division add
# ---------------------------------------------------------------------------


async def test_a_division_is_added_to_the_pending_setup(tmp_path):
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(name="Pro", role_id=1, tier=1))
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction)

    assert [d.name for d in cfg.divisions] == ["Pro", "Am"]
    assert cfg.divisions[1].tier == 2
    assert cfg.divisions[1].role_id == 555
    assert "Am" in _replied(interaction)


async def test_adding_defers_before_any_work(tmp_path):
    """Every season-setup command defers first, so no reply lands on an expired token."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=_pending())
    interaction = _interaction()

    await _add(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


async def test_an_empty_slot_is_filled_before_a_new_one_is_appended(tmp_path):
    """`/season setup` lays out blank slots for the number of divisions a league asked for.
    Appending past them would give a league that wanted three and added three six, half of
    them nameless."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(
        PendingDivision(name="Pro", role_id=1, tier=1),
        PendingDivision(),
    )
    cog = _make_cog(db_path, cfg=cfg)

    await _add(cog, _interaction())

    assert len(cfg.divisions) == 2
    assert [d.name for d in cfg.divisions] == ["Pro", "Am"]


async def test_the_first_empty_slot_is_the_one_filled(tmp_path):
    """So divisions come out in the order the slots were laid down, not in the order a
    manager happened to name them."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(), PendingDivision())
    cog = _make_cog(db_path, cfg=cfg)

    await _add(cog, _interaction())

    assert cfg.divisions[0].name == "Am"
    assert cfg.divisions[1].name == ""


@pytest.mark.parametrize("stage_name", ["CONFIGURATION", "WAITING", "SIGNUPS"])
async def test_adding_before_placements_is_refused(tmp_path, stage_name):
    """Issue #220: divisions are built only once the signups are in."""
    from models.season import SeasonStage

    db_path = await _make_db(tmp_path)
    cfg = _pending()
    cog = _make_cog(db_path, cfg=cfg, stage=SeasonStage(stage_name))
    interaction = _interaction()

    await _add(cog, interaction)

    assert "only be added while the season is in placements" in _replied(interaction)
    assert not any(d.name == "Am" for d in cfg.divisions)
    cog._snapshot_pending.assert_not_awaited()


async def test_adding_without_a_setup_is_refused(tmp_path):
    """There is nothing to add the division to, and creating one would leave it orphaned
    from any season."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=None)
    interaction = _interaction()

    await _add(cog, interaction)

    assert "/season setup" in _replied(interaction)
    cog._snapshot_pending.assert_not_awaited()


@pytest.mark.parametrize("tier", [0, -1])
async def test_a_tier_below_one_is_refused(tmp_path, tier):
    """Tier 1 is the top division and the sequence is what the approval validates; a zero
    or a negative would pass setup and fail at the gate, after the calendar is built."""
    db_path = await _make_db(tmp_path)
    cfg = _pending()
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, tier=tier)

    assert "Tier must be 1 or higher" in _replied(interaction)
    assert cfg.divisions == []


async def test_a_duplicate_name_is_refused(tmp_path):
    """Every later command resolves a division by name, because a slash command takes a
    name and not an id — two divisions called the same thing make all of them ambiguous."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(name="Am", role_id=1, tier=1))
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, name="Am", tier=2)

    assert "already exists" in _replied(interaction)
    assert len(cfg.divisions) == 1


async def test_a_duplicate_name_is_refused_regardless_of_case(tmp_path):
    """Division lookup is case-insensitive everywhere else, so "am" and "Am" would be the
    same ambiguity with none of the refusal."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(name="Am", role_id=1, tier=1))
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, name="aM", tier=2)

    assert "already exists" in _replied(interaction)
    assert len(cfg.divisions) == 1


async def test_a_duplicate_tier_is_refused(tmp_path):
    """Refused where it is typed rather than at approval, which is after the whole calendar
    has been built."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(name="Pro", role_id=1, tier=2))
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, name="Am", tier=2)

    assert "tier **2** already exists" in _replied(interaction)
    assert len(cfg.divisions) == 1


async def test_an_empty_slot_holds_neither_a_name_nor_a_tier(tmp_path):
    """The uniqueness checks skip unnamed slots; a slot left at tier 0 must not block the
    first real division a manager adds."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(), PendingDivision())
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, tier=1)

    assert "already exists" not in _replied(interaction)
    assert cfg.divisions[0].tier == 1


async def test_the_addition_is_snapshotted(tmp_path):
    """The pending config is memory; a restart between adding a division and approving the
    season would otherwise lose it."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=_pending())

    await _add(cog, _interaction())

    cog._snapshot_pending.assert_awaited_once()


async def test_the_addition_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=_pending())

    await _add(cog, _interaction())

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division add" in logged
    assert "Am" in logged


async def test_another_managers_setup_is_found_by_server(tmp_path):
    """Setup is a league's, not a person's — a second manager adding a division to a season
    someone else started is ordinary, not an error."""
    db_path = await _make_db(tmp_path)
    cfg = _pending()
    cog = _make_cog(db_path, cfg=cfg)
    cog._pending = {}  # nothing under this user; only the server-wide lookup answers

    await _add(cog, _interaction())

    assert [d.name for d in cfg.divisions] == ["Am"]


# ---------------------------------------------------------------------------
# /division duplicate
# ---------------------------------------------------------------------------


async def test_a_division_is_duplicated_with_its_offset(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1, hour_offset=2.5)

    cog.bot.season_service.duplicate_division.assert_awaited_once()
    kwargs = cog.bot.season_service.duplicate_division.await_args.kwargs
    assert kwargs["division_id"] == 11
    assert kwargs["name"] == "Am"
    assert kwargs["day_offset"] == 1
    assert kwargs["hour_offset"] == 2.5
    assert kwargs["tier"] == 2


async def test_duplicating_outside_setup_is_refused(tmp_path):
    """It copies rounds into a season that is already running, where the schedule has been
    armed and the roles granted."""
    db_path = await _make_db(tmp_path, status="ACTIVE", name="duplicate_active")
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction)

    assert "only be used while the season is in placements" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_an_unknown_source_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, source="Rookie")

    assert "Rookie" in _replied(interaction)
    assert "not found" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_the_source_division_is_matched_regardless_of_case(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction(), source="pRo")

    cog.bot.season_service.duplicate_division.assert_awaited_once()


@pytest.mark.parametrize("tier", [0, -3])
async def test_duplicating_into_a_tier_below_one_is_refused(tmp_path, tier):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, tier=tier)

    assert "Tier must be 1 or higher" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_duplicating_onto_an_existing_name_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, new_name="Pro", tier=2)

    assert "already exists" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_duplicating_onto_an_existing_tier_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, tier=1)

    assert "tier **1** already exists" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()


async def test_every_refusal_answers_before_the_defer(tmp_path):
    """The gates reply through `response.send_message`, so none of them may run after the
    defer — a fresh response after a defer is a 404, and the manager sees nothing at all."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, tier=1)

    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


async def test_the_copy_itself_is_deferred(tmp_path):
    """It copies every round of a division and seeds its teams, which is well past three
    seconds on a full calendar."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_the_new_division_is_seeded_with_teams(tmp_path):
    """A division with no teams has nowhere to seat a driver, and the seats are what the
    signup wizard fills."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction())

    cog.bot.team_service.seed_division_teams.assert_awaited_once_with(12)


async def test_the_pending_config_is_reloaded_from_what_was_written(tmp_path):
    """Duplicate writes to the database, not to the snapshot — without the reload the
    manager's next `/season placements-review` would not contain the division they just made."""
    db_path = await _make_db(tmp_path)
    cfg = _pending(PendingDivision(name="Pro", role_id=1, tier=1))
    cog = _make_cog(db_path, cfg=cfg)

    await _duplicate(cog, _interaction())

    cog._reload_pending_from_db.assert_awaited_once_with(cfg)


async def test_a_season_with_no_pending_config_still_duplicates(tmp_path):
    """The rows are the truth; a missing in-memory snapshot is a restart, not a reason to
    refuse a copy the database can perfectly well take."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, cfg=None)
    interaction = _interaction()

    await _duplicate(cog, interaction)

    cog.bot.season_service.duplicate_division.assert_awaited_once()
    cog._reload_pending_from_db.assert_not_awaited()
    assert "created from" in _replied(interaction)


async def test_a_refused_copy_reports_the_reason_the_service_gave(tmp_path):
    """`duplicate_division` enforces rules of its own, and a manager cannot act on a copy
    that simply did not happen."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, duplicate_error=ValueError("tier 2 is not sequential"))
    interaction = _interaction()

    await _duplicate(cog, interaction)

    assert "tier 2 is not sequential" in _replied(interaction)
    cog.bot.team_service.seed_division_teams.assert_not_awaited()


async def test_the_copy_is_logged(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)

    await _duplicate(cog, _interaction(), day_offset=2, hour_offset=1.0)

    logged = str(cog.bot.output_router.post_log.await_args.args[0])
    assert "/division duplicate" in logged
    assert "Pro" in logged and "Am" in logged
    assert "+2d" in logged


# ---------------------------------------------------------------------------
# The warnings a shifted calendar earns
# ---------------------------------------------------------------------------


async def test_a_shifted_round_landing_in_the_past_is_warned_about(tmp_path):
    """A mistake made with the offset arguments, and one a manager wants to know about
    before they start filling seats for a round that has already been."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=1)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    assert "in the past" in _replied(interaction)


async def test_a_past_round_does_not_refuse_the_copy(tmp_path):
    """Warning rather than refusing: refusing would make a manager delete the division and
    start again to correct an offset they can simply amend."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=1)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    cog.bot.season_service.duplicate_division.assert_awaited_once()
    assert "created from" in _replied(interaction)


async def test_only_one_past_warning_is_given(tmp_path):
    """A thirty-round calendar shifted backwards would otherwise post thirty identical
    warnings, which is the same information and a wall of text."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(
        db_path, rounds=[_round(offset_days=1, id=1), _round(offset_days=2, id=2)]
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=-30)

    assert _replied(interaction).count("in the past") == 1


async def test_two_rounds_shifted_onto_one_datetime_are_warned_about(tmp_path):
    """The approval refuses a division whose rounds share a datetime, so this warning is
    what stops a manager discovering the clash at the end of building the season."""
    db_path = await _make_db(tmp_path)
    same = datetime.now(timezone.utc) + timedelta(days=10)
    cog = _make_cog(
        db_path,
        rounds=[_round(at=same, id=1, number=1), _round(at=same, id=2, number=2)],
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1)

    assert "share the same shifted datetime" in _replied(interaction)


async def test_a_clean_copy_carries_no_warnings(tmp_path):
    """The warnings have to mean something; one on every copy would be read past."""
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path, rounds=[_round(offset_days=7), _round(offset_days=14, id=2)])
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=1)

    replied = _replied(interaction)
    assert "⚠️" not in replied


async def test_a_naive_round_datetime_is_read_as_utc(tmp_path):
    """SQLite hands back datetimes without a timezone, so comparing one to an aware "now"
    raises rather than warns — which would turn a warning into a failed command."""
    db_path = await _make_db(tmp_path)
    naive = datetime.now() - timedelta(days=5)
    cog = _make_cog(
        db_path,
        rounds=[_round(at=naive)],
    )
    interaction = _interaction()

    await _duplicate(cog, interaction, day_offset=0)

    assert "in the past" in _replied(interaction)


# ---------------------------------------------------------------------------
# A division's name is held to the rules every typed name is (#362)
# ---------------------------------------------------------------------------


async def test_a_division_name_with_an_emoji_is_refused(tmp_path):
    """It heads every posting and every graphic of the division, and a graphic cannot draw
    an emoji."""
    db_path = await _make_db(tmp_path)
    cfg = _pending()
    cog = _make_cog(db_path, cfg=cfg)
    interaction = _interaction()

    await _add(cog, interaction, name="Pro \U0001F3C6")

    assert "division name" in _replied(interaction)
    assert not any(d.name for d in cfg.divisions)


async def test_a_duplicated_division_named_with_markup_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    cog = _make_cog(db_path)
    interaction = _interaction()

    await _duplicate(cog, interaction, new_name="**Am**")

    assert "division name" in _replied(interaction)
    cog.bot.season_service.duplicate_division.assert_not_awaited()
