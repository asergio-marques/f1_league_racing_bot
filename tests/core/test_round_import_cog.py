"""The command surface of the two bulk-import commands.

These two break the rule the rest of the setup flow follows: they do **not** defer.
`send_modal` must be an interaction's first response, and a deferred interaction cannot
open a modal — so the deferral moves into the modal's `on_submit`, where the work is. That
inversion is the thing most likely to be "corrected" by someone reading
`test_season_setup_defers.py`, so it is pinned here from both sides.

The other rule pinned here: an import of twenty rounds is **one** change on the change queue
(#283), not twenty, so the whole import lands in one transaction or not at all.

The forms are submitted on a league whose setup the database alone holds (`setup_league`).
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import leaguebot.core.cogs.round_cog as round_cog
from leaguebot.core.cogs.round_cog import BulkRoundModal, RoundCog, XmlRoundModal
from tests.support.setup_rounds import FORMS, NAMED, import_rounds, round_rows, setup_league
from tests.support.undecorate import undecorate

#: Why the tests of a round's stored moment fail until the build stores it with its offset.
_NO_OFFSET = "#283: /round add and add-xml still store a round's moment without its offset"
#: Why the tests of a clash with a held round fail until the build reads it aware.
_CLASH = "#283: a typed or imported moment still misses a held round at that moment, held aware"

#: Pro, holding no round, in season #1 being set up in Placements.
_PRO = (("Pro", 1, 10, ()),)


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


async def _run_command(name: str, interaction, **kwargs) -> None:
    """The command body, past whatever tier guard it wears."""
    body = undecorate(getattr(RoundCog, name))
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


async def test_the_modal_defers_before_doing_the_work(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager submits `/round add-bulk`'s
    form for Pro with one round on 14 June. The form defers once, and its reply goes as a
    follow-up."""
    league = await setup_league(tmp_path, divisions=_PRO)

    asked = await import_rounds(league, "bulk", "2026-06-14T18:00, Normal, 14")

    asked.response.defer.assert_awaited_once()
    asked.followup.send.assert_awaited()


async def test_a_parse_failure_is_reported_and_nothing_is_written(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager submits `/round add-bulk`'s
    form for Pro with "not a round at all". The reply says no rounds were added, nothing is asked
    of the change queue, and Pro holds no round."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league = await setup_league(tmp_path, divisions=_PRO)

    asked = await import_rounds(league, "bulk", "not a round at all")

    assert "No rounds were added" in last_said(asked)
    assert await change_rows(league.db_path) == []
    assert await round_rows(league.db_path, "Pro") == []


async def test_an_empty_payload_says_so(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager submits `/round add-bulk`'s
    form for Pro holding only blank lines. The reply says there is nothing to add."""
    from tests.support.settings_league import last_said

    league = await setup_league(tmp_path, divisions=_PRO)

    asked = await import_rounds(league, "bulk", "\n   \n")

    assert "Nothing to add" in last_said(asked)


async def test_no_pending_setup_refuses_without_writing(tmp_path):
    """No season is being set up. The manager submits `/round add-bulk`'s form for Pro with one
    round on 14 June. The reply says there is no pending season setup, and nothing is asked of
    the change queue."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league = await setup_league(tmp_path, stage=None)

    asked = await import_rounds(league, "bulk", "2026-06-14T18:00, Normal, 14")

    assert "No pending season setup" in last_said(asked)
    assert await change_rows(league.db_path) == []


# ── The season is written once, not once per round ────────────────────────


async def test_a_twenty_round_import_is_one_queued_change(tmp_path):
    """One change for the whole paste. One per round would be twenty transactions, and a failure
    part-way would leave half a calendar written. Season #1 in Placements, Pro holding no round.
    The manager pastes, in `/round add-bulk` for Pro, twenty rounds at the Hungaroring on 1 to 20
    June. One change is asked of the change queue, and nothing is saved until it runs; once it
    has, Pro holds the twenty rounds, numbered 1 to 20 by moment."""
    from tests.support.change_queue import change_rows, run_queue

    league = await setup_league(tmp_path, divisions=_PRO)

    await import_rounds(league, "bulk", "\n".join(
        f"2026-06-{day:02d}T18:00, Normal, 14" for day in range(1, 21)
    ))

    assert len(await change_rows(league.db_path)) == 1
    assert await round_rows(league.db_path, "Pro") == []
    await run_queue(league.bot)
    assert [(n, at[:10]) for n, _, _, at in await round_rows(league.db_path, "Pro")] == [
        (day, f"2026-06-{day:02d}") for day in range(1, 21)
    ]


async def test_a_successful_import_reaches_the_log(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager pastes, in `/round add-bulk` for
    Pro, a round at the Hungaroring on 14 June, and the queue runs it. The log channel holds one
    line: "Admin (`<@4242>`) | /round add-bulk | Success", then "  division: Pro, now holds 1
    round(s)"."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import log_lines

    league = await setup_league(tmp_path, divisions=_PRO)

    await import_rounds(league, "bulk", "2026-06-14T18:00, Normal, 14")
    await run_queue(league.bot)

    assert await log_lines(league) == [
        f"{NAMED} | /round add-bulk | Success\n  division: Pro, now holds 1 round(s)"
    ]


# ── The XML modal takes the same path ─────────────────────────────────────


async def test_the_xml_modal_reports_a_parse_failure(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager submits `/round add-xml`'s form
    with XML that cannot be read. The reply says no rounds were added, nothing is asked of the
    change queue, and Pro holds no round."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said

    league = await setup_league(tmp_path, divisions=_PRO)

    asked = await import_rounds(league, "xml", "<config><division name='Pro'></config>")

    assert "No rounds were added" in last_said(asked)
    assert await change_rows(league.db_path) == []
    assert await round_rows(league.db_path, "Pro") == []


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_the_xml_modal_applies_a_sound_payload(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager submits `/round add-xml`'s form
    with one round for Pro at track 14 on 14 June at 18:00, Europe/Lisbon time, and the queue
    runs it. Pro holds round 1, at the Hungaroring, at 17:00 UTC."""
    from tests.support.change_queue import run_queue

    league = await setup_league(tmp_path, divisions=_PRO)

    await import_rounds(league, "xml", _SOUND_XML)
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == [
        (1, "NORMAL", "Hungaroring", "2026-06-14T17:00:00+00:00")
    ]


# ── The rejection is capped ───────────────────────────────────────────────


def test_a_long_list_of_faults_is_capped():
    """A payload wrong in one systematic way produces one error per round, and Discord
    refuses an over-long message whole rather than truncating it."""
    errors = [f"Line {n}: unknown track `x`." for n in range(1, 61)]

    rendered = round_cog._format_import_errors(errors)

    assert "60 problem(s)" in rendered
    assert "…and 35 more." in rendered
    assert len(rendered) < 2000


# ── The same rule in `/round add` ─────────────────────────────────────────
#
# Two rounds of one division may not share a moment. The confirmation of placements has always
# refused such a season (Gate 0b), but only at approval — after the calendar may
# already have been built and posted. The bulk commands catch it at import, and
# `/round add` now catches it at the command, so all three agree.


async def _run_round_add(league, **kwargs):
    """`/round add` as the league manager Admin (4242) runs it: the interaction."""
    from tests.support.settings_league import interaction

    asked = interaction(league.bot, "round add")
    await undecorate(RoundCog.round_add)(RoundCog(league.bot), asked, **kwargs)
    return asked


#: Pro, holding a round at the Hungaroring on 14 June 2026 at 18:00 UTC.
_PRO_HOLDING_THE_14TH = (
    ("Pro", 1, 10, (("NORMAL", "Hungaroring", datetime(2026, 6, 14, 18, 0)),)),
)


@pytest.mark.xfail(strict=True, reason=_CLASH)
async def test_round_add_refuses_a_datetime_the_division_already_holds(tmp_path):
    """Season #1 is being set up, in Placements, Pro holding round 1 at the Hungaroring on 14 June
    2026 at 18:00 UTC. The league manager runs `/round add` for Pro, a NORMAL round at track 14 on
    14 June at 18:00. They are refused with the clash, saying the round was not added, nothing is
    asked of the change queue, and Pro holds its one round."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import said
    from tests.support.setup_rounds import round_rows, setup_league

    league = await setup_league(tmp_path, divisions=_PRO_HOLDING_THE_14TH)

    asked = await _run_round_add(
        league, division_name="Pro", format="NORMAL", scheduled_at="2026-06-14T18:00",
        track="14",
    )

    [reply] = said(asked)
    assert "already holds round 1" in reply
    assert "not** added" in reply
    assert await change_rows(league.db_path) == []
    assert len(await round_rows(league.db_path, "Pro")) == 1


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_round_add_still_accepts_a_free_moment(tmp_path):
    """Season #1 is being set up, in Placements, Pro holding round 1 at the Hungaroring on 14 June
    2026 at 18:00 UTC. The league manager runs `/round add` for Pro, a NORMAL round at track 14 on
    21 June at 18:00, and the queue runs it. Pro holds two rounds, the new one round 2, its track
    stored by the name the bot knows it by, the Hungaroring."""
    from tests.support.change_queue import run_queue
    from tests.support.setup_rounds import round_rows, setup_league

    league = await setup_league(tmp_path, divisions=_PRO_HOLDING_THE_14TH)

    await _run_round_add(
        league, division_name="Pro", format="NORMAL", scheduled_at="2026-06-21T18:00",
        track="14",
    )
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == [
        (1, "NORMAL", "Hungaroring", "2026-06-14T18:00:00+00:00"),
        (2, "NORMAL", "Hungaroring", "2026-06-21T18:00:00+00:00"),
    ]


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
#: A paste the season refuses: a round at the moment Pro's round 1 holds, and one at track 99.
_CLASHING_PASTE = "2026-06-14T18:00, Normal, 14\n2026-06-21T18:00, Normal, 99"
#: A calendar the season refuses: a round at the moment Pro's round 1 holds, and a division Am.
_CLASHING_XML = (
    '<config><division name="Pro"><round>'
    "<datetime>2026-06-14T18:00</datetime><timezone>UTC</timezone>"
    "<format>Normal</format><track>14</track></round></division>"
    '<division name="Am"><round>'
    "<datetime>2026-06-21T18:00</datetime><timezone>UTC</timezone>"
    "<format>Normal</format><track>14</track></round></division></config>"
)
_NOT_FOUND = "[Pro]: no division of that name in the pending setup."

_NO_SETUP = "No pending season setup. Run `/season setup` first."


@pytest.mark.parametrize(
    "form, value, stage, reply_says, carried",
    [
        pytest.param(
            "bulk", _BAD_PASTE, "PLACEMENTS", "Import rejected — 2 problem(s)",
            round_cog.parse_bulk_round_lines(_BAD_PASTE)[1],
            id="bulk_paste_with_bad_lines",
        ),
        pytest.param(
            "bulk", "\n   \n", "PLACEMENTS",
            "Nothing to add — no rounds were given.",
            ["Nothing to add — no rounds were given."],
            id="bulk_paste_with_no_rounds",
        ),
        pytest.param(
            "bulk", _SOUND_PASTE, None, _NO_SETUP, [_NO_SETUP],
            id="bulk_with_no_season_being_set_up",
        ),
        pytest.param(
            "bulk", _SOUND_PASTE, "SIGNUPS", "Import rejected — 1 problem(s)", [_NOT_FOUND],
            id="bulk_with_the_season_still_in_signups",
        ),
        pytest.param(
            "bulk", _CLASHING_PASTE, "PLACEMENTS", "Import rejected — 2 problem(s)",
            ["Line 2: unknown track `99`.",
             "Line 1: duplicate datetime 2026-06-14T18:00:00 — already used by a round already "
             "in this division."],
            marks=pytest.mark.xfail(strict=True, reason=_CLASH),
            id="bulk_refused_by_the_season",
        ),
        pytest.param(
            "xml", _BAD_XML, "PLACEMENTS", "Import rejected — 3 problem(s)",
            round_cog.parse_round_xml(_BAD_XML)[1],
            id="xml_with_bad_rounds",
        ),
        pytest.param(
            "xml", _UNREADABLE_XML, "PLACEMENTS",
            "Import rejected — 1 problem(s)",
            round_cog.parse_round_xml(_UNREADABLE_XML)[1],
            id="xml_that_cannot_be_read",
        ),
        pytest.param(
            "xml", _SOUND_XML, None, _NO_SETUP, [_NO_SETUP],
            id="xml_with_no_season_being_set_up",
        ),
        pytest.param(
            "xml", _SOUND_XML, "SIGNUPS", "Import rejected — 1 problem(s)", [_NOT_FOUND],
            id="xml_with_the_season_still_in_signups",
        ),
        pytest.param(
            "xml", _CLASHING_XML, "PLACEMENTS", "Import rejected — 2 problem(s)",
            ["[Pro] line 1: duplicate datetime 2026-06-14T18:00:00 — already used by a round "
             "already in this division.",
             "[Am]: no division of that name in the pending setup."],
            marks=pytest.mark.xfail(strict=True, reason=_CLASH),
            id="xml_refused_by_the_season",
        ),
    ],
)
async def test_every_import_form_refusal_is_recorded(
    tmp_path, form, value, stage, reply_says, carried
):
    """Season #1 is being set up, in Placements, Pro holding round 1 on 14 June 2026 at 18:00 UTC;
    or no season is being set up; or the season is still in Signups, before any division exists,
    which no stage check refuses but Pro not being found does (the owner's decision on #283: no
    stage check now). Admin (4242) submits `/round add-bulk`'s form for Pro or `/round add-xml`'s,
    and it is refused: for lines that cannot be read, for no rounds at all, for no season being
    set up, for Pro not found, or for what the season refuses (a round at the moment Pro's round 1
    holds, with a track the bot does not know or a division Am that does not exist). The reply is
    today's, nothing is asked of the change queue, and the log channel gets exactly one refusal
    line naming the form and Admin and carrying every problem."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import log_lines, said

    league = await setup_league(
        tmp_path, stage=stage, divisions=_PRO_HOLDING_THE_14TH if stage == "PLACEMENTS" else (),
    )

    asked = await import_rounds(league, form, value)

    assert reply_says in "\n".join(said(asked))
    assert await change_rows(league.db_path) == []
    lines = await log_lines(league)
    assert len(lines) == 1, lines
    line = lines[0]
    head = line.splitlines()[0]
    assert head.startswith(f"⛔ {FORMS[form]} refused for {NAMED} — ")
    assert carried
    for problem in carried:
        assert problem in line, (problem, line)
