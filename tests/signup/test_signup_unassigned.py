"""`/signup unassigned list` and `/signup unassigned export`.

Issue #208. These are how a league manager decides who goes in which seat: every driver whose
signup was approved but who has not yet been placed, ordered by the lap time they submitted.
Neither was executed by any test.

**The seeding order is the whole point.** A league places its fastest drivers in its top
division, so a list in any other order would be useless — and both commands take the order from
the placement service rather than sorting it themselves, which means the test's job is to prove
the order *survives* the formatting rather than to re-derive it.

**Every optional answer needs a printed form for "not given".** A driver may have named no
preferred teams, no teammate and no notes, and a blank in the middle of a line reads as a bug
rather than as an answer. The list prints an em dash; the export leaves the CSV cell empty,
which is the CSV convention and is different on purpose.

**The list chunks at Discord's message limit.** A league of forty unassigned drivers produces
well over 2,000 characters, and a single send would be rejected outright — the manager would
get nothing at all, exactly when the list is longest and most needed.
`test_a_long_list_is_split_across_several_messages` holds the chunking, and the test beside it
holds that a short list still arrives as one message rather than being split for no reason.

**The export is written with a BOM.** `utf-8-sig` is what makes Excel open the file with
accented driver names intact; without it a name like "Pérez" arrives mangled, and the league
manager's spreadsheet is the whole reason the command exists.
"""
from __future__ import annotations

import csv
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.cogs.signup_cog import SignupCog
from tests.support.undecorate import undecorate

SERVER_ID = 9308


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seeded(
    seed: int,
    name: str = "Lewis Hamilton",
    *,
    preferred_teams=("Alpha",),
    teammate: str | None = "George",
    notes: str | None = None,
    availability=("Mon_19_00",),
):
    return {
        "seed": seed,
        "server_display_name": name,
        "discord_user_id": f"{4000 + seed}",
        "platform": "Steam",
        "driver_type": "Full-Time Driver",
        "total_lap_fmt": "1:23.456",
        "availability_slot_ids": list(availability),
        "preferred_teams": list(preferred_teams),
        "preferred_teammate": teammate,
        "notes": notes,
    }


def _exportable(seed: int, name: str = "Lewis Hamilton", *, slots=(1,)):
    return {
        "seed": seed,
        "display_name": name,
        "discord_user_id": f"{4000 + seed}",
        "driver_type": "Full-Time Driver",
        "total_lap_fmt": "1:23.456",
        "slot_presence": {n: True for n in slots},
        "preferred_team_1": "Alpha",
        "preferred_team_2": "",
        "preferred_team_3": "",
        "platform": "Steam",
        "platform_id": "lh44",
    }


def _slot(sequence: int, label: str, slot_id: str | None = None):
    """A configured slot.

    ``slot_id`` is the durable identity a driver's availability is stored against;
    ``slot_sequence_id`` is the display ordinal, recomputed on every read. The two are
    given separately here precisely so a test can make them disagree — which is how
    #126's rule is pinned.
    """
    return SimpleNamespace(
        slot_sequence_id=sequence,
        display_label=label,
        slot_id=slot_id if slot_id is not None else f"Slot_{sequence}",
    )


def _make_cog(*, listed=None, exportable=None, slots=None) -> SignupCog:
    bot = MagicMock()
    bot.placement_service = MagicMock()
    bot.placement_service.get_unassigned_drivers_seeded = AsyncMock(
        return_value=listed if listed is not None else []
    )
    bot.placement_service.get_unassigned_drivers_for_export = AsyncMock(
        return_value=exportable if exportable is not None else []
    )
    bot.signup_module_service = MagicMock()
    bot.signup_module_service.get_slots = AsyncMock(
        return_value=slots
        if slots is not None
        else [_slot(1, "Monday 19:00 UTC", "Mon_19_00")]
    )
    cog = SignupCog.__new__(SignupCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _messages(interaction) -> list[str]:
    return [
        str(call.args[0])
        for call in interaction.followup.send.await_args_list
        if call.args
    ]


def _sent(interaction) -> str:
    return "\n".join(_messages(interaction))


async def _list(cog, interaction):
    await undecorate(SignupCog.signup_unassigned_list)(cog, interaction)


async def _export(cog, interaction):
    await undecorate(SignupCog.signup_unassigned_export)(cog, interaction)


def _csv_rows(interaction) -> list[list[str]]:
    """Read back the attached CSV exactly as a spreadsheet would."""
    file = interaction.followup.send.await_args.kwargs["file"]
    raw = file.fp.getvalue() if hasattr(file.fp, "getvalue") else file.fp.read()
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def test_no_unassigned_drivers_says_so(tmp_path):
    """A real and common state — every driver placed — and distinct from a failure."""
    interaction = _interaction()

    await _list(_make_cog(listed=[]), interaction)

    assert "No unsettled signups found" in _sent(interaction)


async def test_the_list_counts_the_drivers(tmp_path):
    interaction = _interaction()

    await _list(_make_cog(listed=[_seeded(1), _seeded(2)]), interaction)

    assert "2 total" in _sent(interaction)


async def test_the_seeding_order_survives_the_formatting(tmp_path):
    """A league places its fastest drivers in its top division; the order is the command's
    reason for existing and is taken from the placement service, not re-sorted here."""
    interaction = _interaction()
    cog = _make_cog(
        listed=[_seeded(1, "Fastest"), _seeded(2, "Middle"), _seeded(3, "Slowest")]
    )

    await _list(cog, interaction)

    sent = _sent(interaction)
    assert sent.index("Fastest") < sent.index("Middle") < sent.index("Slowest")


async def test_a_driver_s_details_are_all_shown(tmp_path):
    """The manager is choosing seats from this alone."""
    interaction = _interaction()

    await _list(_make_cog(listed=[_seeded(1)]), interaction)

    sent = _sent(interaction)
    for detail in (
        "Lewis Hamilton",
        "Steam",
        "Full-Time Driver",
        "1:23.456",
        "Monday 19:00 UTC",
        "Alpha",
        "George",
    ):
        assert detail in sent


async def test_a_driver_with_no_preferences_shows_a_dash_rather_than_a_blank(tmp_path):
    """A blank mid-line reads as a bug rather than as an answer."""
    interaction = _interaction()
    cog = _make_cog(listed=[_seeded(1, preferred_teams=(), teammate=None)])

    await _list(cog, interaction)

    assert "—" in _sent(interaction)


async def test_notes_are_shown_when_given(tmp_path):
    interaction = _interaction()
    cog = _make_cog(listed=[_seeded(1, notes="Cannot race Mondays")])

    await _list(cog, interaction)

    assert "Cannot race Mondays" in _sent(interaction)


async def test_a_driver_with_no_notes_gets_no_notes_line(tmp_path):
    """An empty "Notes:" label would take a line per driver for nothing, and the list is
    already close to the message limit."""
    interaction = _interaction()

    await _list(_make_cog(listed=[_seeded(1, notes=None)]), interaction)

    assert "Notes:" not in _sent(interaction)


# ---------------------------------------------------------------------------
# Availability on the list (issue #184)
# ---------------------------------------------------------------------------


async def test_a_driver_s_availability_is_shown(tmp_path):
    """Issue #184. Availability is the question divisions are built around, and the spec
    has always required it on this command — it was simply never rendered, though the
    placement service returns it on every call."""
    interaction = _interaction()
    slots = [
        _slot(1, "Monday 19:00 UTC", "Mon_19_00"),
        _slot(2, "Friday 21:00 UTC", "Fri_21_00"),
    ]
    cog = _make_cog(
        listed=[_seeded(1, availability=("Mon_19_00", "Fri_21_00"))], slots=slots
    )

    await _list(cog, interaction)

    sent = _sent(interaction)
    assert "Monday 19:00 UTC" in sent
    assert "Friday 21:00 UTC" in sent


async def test_availability_is_matched_on_the_durable_slot_id_not_the_ordinal(tmp_path):
    """#126's rule, for this command. The ordinal is a chronological position recomputed on
    every read, so matching on it would show a driver against somebody else's time as soon
    as a slot is added or removed. Here the driver's one answer is the slot whose ordinal is
    2, so a position-matched render would name Monday instead."""
    interaction = _interaction()
    slots = [
        _slot(1, "Monday 19:00 UTC", "Mon_19_00"),
        _slot(2, "Friday 21:00 UTC", "Fri_21_00"),
    ]
    cog = _make_cog(listed=[_seeded(1, availability=("Fri_21_00",))], slots=slots)

    await _list(cog, interaction)

    sent = _sent(interaction)
    assert "Friday 21:00 UTC" in sent
    assert "Monday 19:00 UTC" not in sent


async def test_availability_reads_in_the_league_s_slot_order(tmp_path):
    """Two drivers' lines have to be comparable at a glance, so the labels follow the
    league's own chronological order rather than the order the answers happen to sit in
    the driver's stored JSON."""
    interaction = _interaction()
    slots = [
        _slot(1, "Monday 19:00 UTC", "Mon_19_00"),
        _slot(2, "Wednesday 20:00 UTC", "Wed_20_00"),
        _slot(3, "Friday 21:00 UTC", "Fri_21_00"),
    ]
    cog = _make_cog(
        listed=[_seeded(1, availability=("Fri_21_00", "Mon_19_00", "Wed_20_00"))],
        slots=slots,
    )

    await _list(cog, interaction)

    sent = _sent(interaction)
    assert sent.index("Monday 19:00 UTC") < sent.index("Wednesday 20:00 UTC")
    assert sent.index("Wednesday 20:00 UTC") < sent.index("Friday 21:00 UTC")


async def test_an_availability_naming_a_removed_slot_says_unknown_slot(tmp_path):
    """The signup spec: availability naming a slot that no longer exists is reported as an
    unknown slot. The durable ID is a storage form no league should ever be shown."""
    interaction = _interaction()
    slots = [_slot(1, "Monday 19:00 UTC", "Mon_19_00")]
    cog = _make_cog(
        listed=[_seeded(1, availability=("Mon_19_00", "Sat_18_00"))], slots=slots
    )

    await _list(cog, interaction)

    sent = _sent(interaction)
    assert "Unknown slot" in sent
    assert "Sat_18_00" not in sent


async def test_two_removed_slots_read_as_two_unknowns(tmp_path):
    """One entry per answer, as the wizard's review panel already does
    (``test_wizard_availability`` pins the same shape there). Collapsing them would
    understate how much of a driver's availability has been lost."""
    interaction = _interaction()
    slots = [_slot(1, "Monday 19:00 UTC", "Mon_19_00")]
    cog = _make_cog(
        listed=[_seeded(1, availability=("Sat_18_00", "Sun_20_00"))], slots=slots
    )

    await _list(cog, interaction)

    assert "Unknown slot, Unknown slot" in _sent(interaction)


async def test_a_driver_with_no_availability_shows_a_dash(tmp_path):
    """Consistent with how the same line renders an absent team or teammate: a blank
    mid-line reads as a bug rather than as an answer."""
    interaction = _interaction()
    cog = _make_cog(listed=[_seeded(1, availability=())])

    await _list(cog, interaction)

    assert "Available: —" in _sent(interaction)


async def test_every_slot_is_listed_however_many_there_are(tmp_path):
    """Decided 2026-09-20: every label, never elided. The spec caps a league at 25 slots,
    and a driver who ticks all of them still renders in full — measured at some 700
    characters for the block, well inside the 1900-character chunk budget, so nothing is
    hidden and the chunker is not disturbed."""
    interaction = _interaction()
    slots = [
        _slot(n, f"Slot {n:02d} label UTC", f"Day_{n:02d}_00") for n in range(1, 26)
    ]
    cog = _make_cog(
        listed=[_seeded(1, availability=tuple(s.slot_id for s in slots))], slots=slots
    )

    await _list(cog, interaction)

    sent = _sent(interaction)
    for slot in slots:
        assert slot.display_label in sent
    assert "+" not in sent.split("Available:")[1].split("\n")[0]
    assert len(_messages(interaction)) == 1


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


async def test_a_short_list_arrives_as_one_message(tmp_path):
    interaction = _interaction()

    await _list(_make_cog(listed=[_seeded(1), _seeded(2)]), interaction)

    assert len(_messages(interaction)) == 1


async def test_a_long_list_is_split_across_several_messages(tmp_path):
    """A league of forty unassigned drivers is well over Discord's limit, and a single
    send would be rejected outright — the manager would get nothing at all, exactly when
    the list is longest and most needed."""
    interaction = _interaction()
    cog = _make_cog(listed=[_seeded(n, f"Driver Number {n}") for n in range(1, 41)])

    await _list(cog, interaction)

    messages = _messages(interaction)
    assert len(messages) > 1
    assert all(len(m) <= 1900 for m in messages)


async def test_no_driver_is_lost_in_the_chunking(tmp_path):
    """The chunker builds messages line by line; an off-by-one would drop whichever driver
    sat on a boundary, and nothing else would notice."""
    interaction = _interaction()
    cog = _make_cog(listed=[_seeded(n, f"Driver Number {n}") for n in range(1, 41)])

    await _list(cog, interaction)

    sent = _sent(interaction)
    for n in range(1, 41):
        assert f"Driver Number {n}" in sent


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------


async def test_nothing_to_export_says_so(tmp_path):
    interaction = _interaction()

    await _export(_make_cog(exportable=[]), interaction)

    assert "No unsettled signups found" in _sent(interaction)
    assert "file" not in interaction.followup.send.await_args.kwargs


async def test_the_export_attaches_a_csv(tmp_path):
    interaction = _interaction()

    await _export(_make_cog(exportable=[_exportable(1)]), interaction)

    file = interaction.followup.send.await_args.kwargs["file"]
    assert file.filename == "unassigned_drivers.csv"
    assert "1 Unassigned driver(s) exported" in _sent(interaction)


async def test_the_csv_names_every_column_a_manager_needs(tmp_path):
    interaction = _interaction()

    await _export(_make_cog(exportable=[_exportable(1)]), interaction)

    header = _csv_rows(interaction)[0]
    for column in (
        "Seed",
        "Display Name",
        "Discord User ID",
        "Driver Type",
        "Lap Total",
        "Platform",
        "Platform ID",
    ):
        assert column in header


async def test_each_availability_slot_becomes_its_own_column(tmp_path):
    """The manager sorts by slot to find who can race when, which needs a column per slot
    rather than one cell listing them."""
    interaction = _interaction()
    cog = _make_cog(
        exportable=[_exportable(1, slots=(1,))],
        slots=[_slot(1, "Monday 19:00 UTC"), _slot(2, "Friday 21:00 UTC")],
    )

    await _export(cog, interaction)

    header = _csv_rows(interaction)[0]
    assert "Monday 19:00 UTC" in header
    assert "Friday 21:00 UTC" in header


async def test_a_slot_the_driver_gave_is_marked_and_one_they_did_not_is_blank(tmp_path):
    interaction = _interaction()
    cog = _make_cog(
        exportable=[_exportable(1, slots=(1,))],
        slots=[_slot(1, "Monday 19:00 UTC"), _slot(2, "Friday 21:00 UTC")],
    )

    await _export(cog, interaction)

    rows = _csv_rows(interaction)
    header, row = rows[0], rows[1]
    assert row[header.index("Monday 19:00 UTC")] == "X"
    assert row[header.index("Friday 21:00 UTC")] == ""


async def test_the_slot_columns_follow_the_league_s_own_order(tmp_path):
    """Sorted by the slot's sequence, not by however the query returned them, so the
    columns match the order the league sees everywhere else."""
    interaction = _interaction()
    cog = _make_cog(
        exportable=[_exportable(1)],
        slots=[_slot(2, "Friday 21:00 UTC"), _slot(1, "Monday 19:00 UTC")],
    )

    await _export(cog, interaction)

    header = _csv_rows(interaction)[0]
    assert header.index("Monday 19:00 UTC") < header.index("Friday 21:00 UTC")


async def test_every_driver_reaches_the_csv_in_seed_order(tmp_path):
    interaction = _interaction()
    cog = _make_cog(
        exportable=[_exportable(1, "Fastest"), _exportable(2, "Slowest")]
    )

    await _export(cog, interaction)

    rows = _csv_rows(interaction)
    assert rows[1][1] == "Fastest"
    assert rows[2][1] == "Slowest"


async def test_the_csv_carries_a_byte_order_mark(tmp_path):
    """`utf-8-sig` is what makes Excel open the file with accented driver names intact.
    Without it "Pérez" arrives mangled, and the manager's spreadsheet is the whole reason
    the command exists."""
    interaction = _interaction()

    await _export(_make_cog(exportable=[_exportable(1, "Sergio Pérez")]), interaction)

    file = interaction.followup.send.await_args.kwargs["file"]
    raw = file.fp.getvalue() if hasattr(file.fp, "getvalue") else file.fp.read()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Pérez" in raw.decode("utf-8-sig")


# ---------------------------------------------------------------------------
# Signups still in review (issue #220)
# ---------------------------------------------------------------------------


async def test_a_signup_in_review_is_listed_by_its_state_rather_than_a_seed(tmp_path):
    in_review = dict(_seeded(2, "Max Verstappen"), seed=None, state="PENDING_ADMIN_APPROVAL")
    cog = _make_cog(listed=[_seeded(1), in_review])
    interaction = _interaction()

    await undecorate(SignupCog.signup_unassigned_list)(cog, interaction)

    sent = _sent(interaction)
    assert "**#1** **Lewis Hamilton**" in sent
    assert "**Awaiting approval** **Max Verstappen**" in sent


async def test_a_signup_in_review_exports_with_no_seed(tmp_path):
    import csv

    in_review = dict(_exportable(2, "Max Verstappen"), seed=None, state="PENDING_DRIVER_CORRECTION")
    cog = _make_cog(exportable=[_exportable(1), in_review])
    interaction = _interaction()

    await undecorate(SignupCog.signup_unassigned_export)(cog, interaction)

    attachment = interaction.followup.send.await_args.kwargs["file"]
    attachment.fp.seek(0)
    rows = list(csv.reader(io.StringIO(attachment.fp.read().decode("utf-8-sig"))))
    assert rows[1][0] == "1"
    assert rows[2][0] == ""
