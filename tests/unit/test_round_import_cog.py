"""The command surface of the two bulk-import commands.

These two break the rule the rest of the setup flow follows: they do **not** defer.
`send_modal` must be an interaction's first response, and a deferred interaction cannot
open a modal — so the deferral moves into the modal's `on_submit`, where the work is. That
inversion is the thing most likely to be "corrected" by someone reading
`test_season_setup_defers.py`, so it is pinned here from both sides.

The other rule pinned here is the expensive one: `_snapshot_pending` rebuilds the entire
SETUP season, so an import of twenty rounds must call it **once**, not twenty times.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import cogs.season_cog as season_cog  # noqa: E402
from cogs.season_cog import (  # noqa: E402
    BulkRoundModal,
    PendingConfig,
    PendingDivision,
    SeasonCog,
    XmlRoundModal,
)


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock(
        side_effect=AssertionError("replied through response; after a defer that is a 404")
    )
    interaction.followup.send = AsyncMock()
    return interaction


def _cog_with(cfg: PendingConfig | None, db_path: str = ":memory:") -> MagicMock:
    """A stub SeasonCog, registered so the modals can find it through the client."""
    cog = MagicMock()
    cog.resolve_pending = MagicMock(return_value=cfg)
    cog.bot.db_path = db_path
    cog.bot.output_router.post_log = AsyncMock()
    cog._snapshot_pending = AsyncMock()
    cog._calendar_round_overflow = AsyncMock(return_value=None)
    return cog


def _bind(interaction: MagicMock, cog: MagicMock) -> None:
    interaction.client.get_cog = MagicMock(return_value=cog)


def _pending() -> PendingConfig:
    return PendingConfig(
        server_id=1, divisions=[PendingDivision(name="Pro", role_id=1)]
    )


async def _run_command(name: str, interaction, **kwargs) -> None:
    """The command body, past `channel_guard` and `admin_only`."""
    body = getattr(SeasonCog, name).callback.__wrapped__.__wrapped__
    await body(MagicMock(), interaction, **kwargs)


# ── The commands open a modal and do not defer ────────────────────────────


async def test_add_bulk_opens_a_modal_without_deferring():
    interaction = _interaction()
    interaction.response.defer = AsyncMock(
        side_effect=AssertionError("deferred — send_modal can no longer open a modal")
    )

    await _run_command("round_add_bulk", interaction, division_name="Pro")

    interaction.response.send_modal.assert_awaited_once()
    assert isinstance(interaction.response.send_modal.await_args.args[0], BulkRoundModal)


async def test_add_xml_opens_a_modal_without_deferring():
    interaction = _interaction()
    interaction.response.defer = AsyncMock(
        side_effect=AssertionError("deferred — send_modal can no longer open a modal")
    )

    await _run_command("round_add_xml", interaction)

    interaction.response.send_modal.assert_awaited_once()
    assert isinstance(interaction.response.send_modal.await_args.args[0], XmlRoundModal)


def test_the_division_is_carried_to_the_modal():
    """It is resolved when the modal is submitted, not when the command runs — the two
    are separated by however long the manager spends typing."""
    modal = BulkRoundModal("Pro")

    assert modal._division_name == "Pro"


# ── The modal defers, then replies through followup ───────────────────────


async def test_the_modal_defers_before_doing_the_work(monkeypatch):
    interaction = _interaction()
    cog = _cog_with(_pending())
    _bind(interaction, cog)
    monkeypatch.setattr(
        season_cog, "apply_round_import", AsyncMock(return_value=({"Pro": []}, []))
    )

    modal = BulkRoundModal("Pro")
    modal.entries._value = "2026-06-14T18:00, Normal, 14"
    await modal.on_submit(interaction)

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()


async def test_a_parse_failure_is_reported_and_nothing_is_written():
    interaction = _interaction()
    cog = _cog_with(_pending())
    _bind(interaction, cog)

    modal = BulkRoundModal("Pro")
    modal.entries._value = "not a round at all"
    await modal.on_submit(interaction)

    reply = interaction.followup.send.await_args.args[0]
    assert "No rounds were added" in reply
    cog._snapshot_pending.assert_not_awaited()


async def test_an_empty_payload_says_so():
    interaction = _interaction()
    _bind(interaction, _cog_with(_pending()))

    modal = BulkRoundModal("Pro")
    modal.entries._value = "\n   \n"
    await modal.on_submit(interaction)

    assert "Nothing to add" in interaction.followup.send.await_args.args[0]


async def test_no_pending_setup_refuses_without_writing():
    interaction = _interaction()
    cog = _cog_with(None)
    _bind(interaction, cog)

    modal = BulkRoundModal("Pro")
    modal.entries._value = "2026-06-14T18:00, Normal, 14"
    await modal.on_submit(interaction)

    assert "No pending season setup" in interaction.followup.send.await_args.args[0]
    cog._snapshot_pending.assert_not_awaited()


# ── The season is rebuilt once, not once per round ────────────────────────


async def test_a_twenty_round_import_snapshots_exactly_once(monkeypatch):
    """`_snapshot_pending` tears down and rebuilds the whole season. Once per round
    would be twenty teardowns for one paste."""
    interaction = _interaction()
    cfg = _pending()
    cog = _cog_with(cfg)
    _bind(interaction, cog)
    monkeypatch.setattr(
        season_cog,
        "apply_round_import",
        AsyncMock(return_value=({"Pro": [_round(day) for day in range(1, 21)]}, [])),
    )

    modal = BulkRoundModal("Pro")
    modal.entries._value = "\n".join(
        f"2026-06-{day:02d}T18:00, Normal, 14" for day in range(1, 21)
    )
    await modal.on_submit(interaction)

    assert cog._snapshot_pending.await_count == 1


def _round(day: int) -> dict:
    from models.round import RoundFormat

    return {
        "round_number": day,
        "format": RoundFormat.NORMAL,
        "track_name": "Hungaroring",
        "scheduled_at": datetime(2026, 6, day, 18, 0),
    }


async def test_a_successful_import_reaches_the_log(monkeypatch):
    interaction = _interaction()
    cog = _cog_with(_pending())
    _bind(interaction, cog)
    monkeypatch.setattr(
        season_cog,
        "apply_round_import",
        AsyncMock(return_value=({"Pro": [_round(14)]}, [])),
    )

    modal = BulkRoundModal("Pro")
    modal.entries._value = "2026-06-14T18:00, Normal, 14"
    await modal.on_submit(interaction)

    cog.bot.output_router.post_log.assert_awaited_once()
    assert "/round add-bulk" in cog.bot.output_router.post_log.await_args.args[1]


# ── The XML modal takes the same path ─────────────────────────────────────


async def test_the_xml_modal_reports_a_parse_failure():
    interaction = _interaction()
    cog = _cog_with(_pending())
    _bind(interaction, cog)

    modal = XmlRoundModal()
    modal.payload._value = "<config><division name='Pro'></config>"
    await modal.on_submit(interaction)

    assert "No rounds were added" in interaction.followup.send.await_args.args[0]
    cog._snapshot_pending.assert_not_awaited()


async def test_the_xml_modal_applies_a_sound_payload(monkeypatch):
    interaction = _interaction()
    cog = _cog_with(_pending())
    _bind(interaction, cog)
    applied = AsyncMock(return_value=({"Pro": [_round(14)]}, []))
    monkeypatch.setattr(season_cog, "apply_round_import", applied)

    modal = XmlRoundModal()
    modal.payload._value = (
        '<config><division name="Pro"><round>'
        "<datetime>2026-06-14T18:00</datetime><timezone>Europe/Lisbon</timezone>"
        "<format>Normal</format><track>14</track>"
        "</round></division></config>"
    )
    await modal.on_submit(interaction)

    applied.assert_awaited_once()
    assert cog._snapshot_pending.await_count == 1


# ── The rejection is capped ───────────────────────────────────────────────


def test_a_long_list_of_faults_is_capped():
    """A payload wrong in one systematic way produces one error per round, and Discord
    refuses an over-long message whole rather than truncating it."""
    errors = [f"Line {n}: unknown track `x`." for n in range(1, 61)]

    rendered = season_cog._format_import_errors(errors)

    assert "60 problem(s)" in rendered
    assert "…and 35 more." in rendered
    assert len(rendered) < 2000


# ── The same rule in `/round add` ─────────────────────────────────────────
#
# Two rounds of one division may not share a moment. `/season approve` has always
# refused such a season (Gate 0b), but only at approval — after the calendar may
# already have been built and posted. The bulk commands catch it at import, and
# `/round add` now catches it at the command, so all three agree.


async def _run_round_add(interaction, cog, **kwargs):
    body = SeasonCog.round_add.callback.__wrapped__.__wrapped__
    await body(cog, interaction, **kwargs)


def _add_cog(cfg: PendingConfig) -> MagicMock:
    cog = MagicMock()
    cog._pending = {42: cfg}
    cog._get_pending_for_server = MagicMock(return_value=cfg)
    cog.bot.db_path = ":memory:"
    cog.bot.output_router.post_log = AsyncMock()
    cog._snapshot_pending = AsyncMock()
    cog._calendar_round_overflow = AsyncMock(return_value=None)
    return cog


async def test_round_add_refuses_a_datetime_the_division_already_holds(monkeypatch):
    cfg = _pending()
    cfg.divisions[0].rounds = [_round(14)]
    cog = _add_cog(cfg)
    interaction = _interaction()
    monkeypatch.setattr(
        season_cog.track_service,
        "resolve_track_name",
        AsyncMock(return_value="Hungaroring"),
    )

    await _run_round_add(
        interaction,
        cog,
        division_name="Pro",
        format="NORMAL",
        scheduled_at="2026-06-14T18:00",
        track="14",
    )

    reply = interaction.followup.send.await_args.args[0]
    assert "already holds round 1" in reply
    assert "not** added" in reply
    assert len(cfg.divisions[0].rounds) == 1, "the clashing round was added anyway"
    cog._snapshot_pending.assert_not_awaited()


async def test_round_add_still_accepts_a_free_moment(monkeypatch):
    cfg = _pending()
    cfg.divisions[0].rounds = [_round(14)]
    cog = _add_cog(cfg)
    interaction = _interaction()
    monkeypatch.setattr(
        season_cog.track_service,
        "resolve_track_name",
        AsyncMock(return_value="Hungaroring"),
    )

    await _run_round_add(
        interaction,
        cog,
        division_name="Pro",
        format="NORMAL",
        scheduled_at="2026-06-21T18:00",
        track="14",
    )

    assert len(cfg.divisions[0].rounds) == 2
    cog._snapshot_pending.assert_awaited_once()


# ── Discord's own limits on a modal ───────────────────────────────────────
#
# These are not style rules. Discord refuses a modal that breaks one with
# `400 Invalid Form Body`, and refuses it *whole* — the command raises where it opens
# the modal, so the manager gets nothing at all and only the log carries the reason.
# The XML placeholder shipped at 220 characters against the 100 limit and did exactly
# that, which is why the limits are pinned rather than trusted.

_MODAL_FIELDS = [
    ("BulkRoundModal.entries", BulkRoundModal.entries),
    ("XmlRoundModal.payload", XmlRoundModal.payload),
]


@pytest.mark.parametrize("name, field", _MODAL_FIELDS)
def test_a_placeholder_fits_discords_limit(name, field):
    assert len(field.placeholder or "") <= 100, (
        f"{name}: a placeholder over 100 characters makes Discord refuse the modal"
    )


@pytest.mark.parametrize(
    "name, label",
    [
        ("BulkRoundModal.entries", "datetime UTC, format, track — one per line"),
        ("XmlRoundModal.payload", "XML payload"),
    ],
)
def test_a_label_fits_discords_limit(name, label):
    """Stated rather than read back: `TextInput.label` is deprecated, and reading it
    for an assertion adds a warning to every run for no gain."""
    assert len(label) <= 45, f"{name}: a label is capped at 45 characters"


@pytest.mark.parametrize("name, field", _MODAL_FIELDS)
def test_a_field_does_not_ask_for_more_than_discord_carries(name, field):
    assert field.max_length <= 4000, f"{name}: a text input is capped at 4000 characters"


@pytest.mark.parametrize(
    "title", ["Add rounds in bulk", "Add rounds from XML"]
)
def test_a_modal_title_fits_discords_limit(title):
    assert len(title) <= 45
