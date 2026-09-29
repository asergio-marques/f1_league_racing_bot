"""The command surface of the two bulk-import commands.

These two break the rule the rest of the setup flow follows: they do **not** defer.
`send_modal` must be an interaction's first response, and a deferred interaction cannot
open a modal — so the deferral moves into the modal's `on_submit`, where the work is. That
inversion is the thing most likely to be "corrected" by someone reading
`test_season_setup_defers.py`, so it is pinned here from both sides.

The other rule pinned here: an import of twenty rounds calls `_snapshot_pending` **once**,
not twenty times, so the whole import lands in one transaction or not at all.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import leaguebot.core.cogs.season_cog as season_cog
from leaguebot.core.cogs.season_cog import (
    BulkRoundModal,
    PendingConfig,
    PendingDivision,
    SeasonCog,
    XmlRoundModal,
)
from tests.support.undecorate import undecorate


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
        divisions=[PendingDivision(name="Pro", role_id=1)]
    )


async def _run_command(name: str, interaction, **kwargs) -> None:
    """The command body, past whatever tier guard it wears."""
    body = undecorate(getattr(SeasonCog, name))
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


async def test_the_division_is_carried_to_the_modal():
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


# ── The season is written once, not once per round ────────────────────────


async def test_a_twenty_round_import_snapshots_exactly_once(monkeypatch):
    """One write for the whole paste. Once per round would be twenty transactions, and
    a failure part-way would leave half a calendar written."""
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
    from leaguebot.core.models.round import RoundFormat

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
    assert "/round add-bulk" in cog.bot.output_router.post_log.await_args.args[0]


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
# Two rounds of one division may not share a moment. The confirmation of placements has always
# refused such a season (Gate 0b), but only at approval — after the calendar may
# already have been built and posted. The bulk commands catch it at import, and
# `/round add` now catches it at the command, so all three agree.


async def _run_round_add(interaction, cog, **kwargs):
    body = undecorate(SeasonCog.round_add)
    await body(cog, interaction, **kwargs)


def _add_cog(cfg: PendingConfig) -> MagicMock:
    cog = MagicMock()
    cog._pending = {42: cfg}
    cog._get_pending = MagicMock(return_value=cfg)
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


# ── Every refusal of the two import forms is recorded (#482) ──────────────
#
# The core specification's "The record of what changed": a refusal is one line in the log
# channel naming the member, what was refused and why. A refusal by an import form names the
# form, and an import rejected for several problems carries every one of them in its line.

_BAD_PASTE = "not a round at all\n2026-06-21T18:00, Wet, 14"
_BAD_XML = (
    '<config><division name="Pro"><round><datetime>nope</datetime>'
    "<timezone>Mars/Olympus</timezone><format>Wet</format><track>14</track>"
    "</round></division></config>"
)
_UNREADABLE_XML = "<config><division name='Pro'></config>"
_SOUND_PASTE = "2026-06-14T18:00, Normal, 14"
_SOUND_XML = (
    '<config><division name="Pro"><round>'
    "<datetime>2026-06-14T18:00</datetime><timezone>Europe/Lisbon</timezone>"
    "<format>Normal</format><track>14</track>"
    "</round></division></config>"
)
#: What the season's own rules refuse of a calendar that parsed, as `apply_round_import` says it.
_SEASON_REFUSES = [
    "Division `Am` not found in pending setup.",
    "[Pro] 2026-06-14 18:00 is already held by round 1.",
]

_NO_SETUP = "No pending season setup. Run `/season setup` first."


def _submitted(form: str, value: str):
    """The form as the manager submitted it: /round add-bulk's for Pro, or /round add-xml's."""
    if form == "bulk":
        modal = BulkRoundModal("Pro")
        modal.entries._value = value
    else:
        modal = XmlRoundModal()
        modal.payload._value = value
    return modal


@pytest.mark.xfail(
    strict=True, reason="#482: the round import forms do not yet record their refusals"
)
@pytest.mark.parametrize(
    "form, title, value, has_setup, season_refuses, reply_says, carried",
    [
        pytest.param(
            "bulk", "Add rounds in bulk", _BAD_PASTE, True, None, "Import rejected — 2 problem(s)",
            season_cog.parse_bulk_round_lines(_BAD_PASTE)[1],
            id="bulk_paste_with_bad_lines",
        ),
        pytest.param(
            "bulk", "Add rounds in bulk", "\n   \n", True, None,
            "Nothing to add — no rounds were given.",
            ["Nothing to add — no rounds were given."],
            id="bulk_paste_with_no_rounds",
        ),
        pytest.param(
            "bulk", "Add rounds in bulk", _SOUND_PASTE, False, None, _NO_SETUP, [_NO_SETUP],
            id="bulk_with_no_season_being_set_up",
        ),
        pytest.param(
            "bulk", "Add rounds in bulk", _SOUND_PASTE, True, _SEASON_REFUSES,
            "Import rejected — 2 problem(s)", _SEASON_REFUSES,
            id="bulk_refused_by_the_season",
        ),
        pytest.param(
            "xml", "Add rounds from XML", _BAD_XML, True, None, "Import rejected — 3 problem(s)",
            season_cog.parse_round_xml(_BAD_XML)[1],
            id="xml_with_bad_rounds",
        ),
        pytest.param(
            "xml", "Add rounds from XML", _UNREADABLE_XML, True, None,
            "Import rejected — 1 problem(s)",
            season_cog.parse_round_xml(_UNREADABLE_XML)[1],
            id="xml_that_cannot_be_read",
        ),
        pytest.param(
            "xml", "Add rounds from XML", _SOUND_XML, False, None, _NO_SETUP, [_NO_SETUP],
            id="xml_with_no_season_being_set_up",
        ),
        pytest.param(
            "xml", "Add rounds from XML", _SOUND_XML, True, _SEASON_REFUSES,
            "Import rejected — 2 problem(s)", _SEASON_REFUSES,
            id="xml_refused_by_the_season",
        ),
    ],
)
async def test_every_import_form_refusal_is_recorded(
    monkeypatch, form, title, value, has_setup, season_refuses, reply_says, carried
):
    """Manager (id 42) submits a round import form and it is refused: the reply is today's,
    nothing is written, and the log channel gets exactly one refusal line naming the form and
    carrying every problem."""
    interaction = _interaction()
    cog = _cog_with(_pending() if has_setup else None)
    interaction.client = cog.bot
    cog.bot.get_cog = MagicMock(return_value=cog)
    if season_refuses is not None:
        monkeypatch.setattr(
            season_cog, "apply_round_import", AsyncMock(return_value=({}, season_refuses))
        )

    await _submitted(form, value).on_submit(interaction)

    replies = "\n".join(call.args[0] for call in interaction.followup.send.await_args_list)
    assert reply_says in replies
    cog._snapshot_pending.assert_not_awaited()
    lines = [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]
    assert len(lines) == 1, lines
    line = lines[0]
    head = line.splitlines()[0]
    assert head.startswith("⛔ ")
    assert title in head
    assert " refused for Manager (<@42>) — " in head
    assert carried
    for problem in carried:
        assert problem in line, (problem, line)
