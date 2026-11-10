"""Merging an imported calendar into the season being set up, on the change queue.

**`/round add-bulk` and `/round add-xml` ask the change queue for `core.round_import`** (#283),
and the queue's acknowledgement is the form's reply. One save adds every round the import names
across its divisions, renumbers each division it touches by moment, and writes an audit entry
`ROUND_ADDED` for each round, carrying its division, and the log line, stamped with the queue's
clock. It touches no other division's rounds, and deletes nothing. Every refusal is judged at the
ask and again as the change starts, in the same words.

Each test runs the form on a league whose setup the database alone holds (`setup_league`), so
none of it reads a setup kept in memory. Three rules carry the weight:

  * **All or nothing.** One bad entry rejects the whole import. Round numbers are derived
    from the whole division — sorted by datetime, renumbered 1..N — so a partial apply
    does not leave some correct rounds, it renumbers the division around whichever ones
    landed. Rejecting whole also makes the retry idempotent: fix the paste, submit it
    again.
  * **Merge, never replace.** These are add commands. A calendar too long for one modal
    is imported in two passes, and the second must not discard the first.
  * **The capacity guard is asked of the batch.** `/round add` asks whether `len + 1`
    fits; a dozen rounds can each clear that check while the dozen together outgrow the
    template.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from leaguebot.core.db.database import get_connection
from tests.support.setup_rounds import (
    FORMS,
    NAMED,
    NOW,
    acknowledged,
    calendar_xml,
    import_rounds,
    no_room,
    repeated,
    round_ids,
    round_rows,
    setup_league,
)

#: Why the tests of a round's stored moment fail until the build stores it with its offset.
_NO_OFFSET = "#283: an import still stores a round's moment without its offset"
#: Why the tests of a clash with a held round fail until the build reads it aware.
_CLASH = "#283: an import still misses a held round at the same moment, held with its offset"

#: Pro's rounds of 14 and 28 June and Am's of 21 June, mystery rounds all, at 18:00 UTC.
_PRO_14 = ("MYSTERY", None, datetime(2026, 6, 14, 18, 0))
_PRO_28 = ("MYSTERY", None, datetime(2026, 6, 28, 18, 0))
_AM_21 = ("MYSTERY", None, datetime(2026, 6, 21, 18, 0))
_HELD = (("Pro", 1, 10, (_PRO_14, _PRO_28)), ("Am", 2, 20, (_AM_21,)))


def _hungaroring(day: int, month: int = 6) -> tuple[str, str | None, datetime]:
    """A round Pro already holds at the Hungaroring, on *day* of *month* 2026 at 18:00 UTC."""
    return ("NORMAL", "Hungaroring", datetime(2026, month, day, 18, 0))


def _pro(*rounds) -> tuple:
    """Season #1 in Placements, Pro alone holding *rounds*."""
    return (("Pro", 1, 10, tuple(rounds)),)


def _paste(*days: int, track: str = "14", month: int = 6) -> str:
    """A pasted calendar: a NORMAL round at *track* on each of *days* of *month*, at 18:00 UTC."""
    return "\n".join(f"2026-{month:02d}-{day:02d}T18:00, Normal, {track}" for day in days)


def _rejected(*problems: str) -> str:
    """The rejection of an import for *problems*, word for word."""
    return (f"❌ Import rejected — {len(problems)} problem(s). **No rounds were added.**\n"
            + "\n".join(f"  • {problem}" for problem in problems))


def _success_line(source: str, *held: tuple[str, int]) -> str:
    """An import's line in the log channel: each division and how many rounds it now holds."""
    return f"{NAMED} | {source} | Success\n" + "\n".join(
        f"  division: {name}, now holds {count} round(s)" for name, count in held
    )


async def _imported(league, form: str, value: str, division: str = "Pro"):
    """The form submitted, and the queue run: the interaction."""
    from tests.support.change_queue import run_queue

    asked = await import_rounds(league, form, value, division)
    await run_queue(league.bot)
    return asked


async def _nothing_asked(league) -> bool:
    from tests.support.change_queue import change_rows

    return await change_rows(league.db_path) == []


# ── The happy path ────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_rounds_are_sorted_and_numbered_from_one(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager pastes, in `/round add-bulk` for
    Pro, rounds at the Hungaroring on 28, 14 and 21 June at 18:00 UTC, in that order, and the
    queue runs it. Pro holds three rounds numbered 1, 2 and 3 by moment: 14, 21 and 28 June."""
    league = await setup_league(tmp_path, divisions=_pro())

    await _imported(league, "bulk", _paste(28, 14, 21))

    assert [(n, at) for n, _, _, at in await round_rows(league.db_path, "Pro")] == [
        (1, "2026-06-14T18:00:00+00:00"), (2, "2026-06-21T18:00:00+00:00"),
        (3, "2026-06-28T18:00:00+00:00"),
    ]


@pytest.mark.parametrize("written", ["14", "Hungaroring", "hungaroring", "14 – Hungaroring"])
async def test_a_track_may_be_named_any_way_the_bot_accepts(tmp_path, written):
    """Each is stored under its canonical name. `resolve_track_name` owns the rule; this is the
    seam, not the rule. Season #1 in Placements, Pro holding no round. The manager pastes one
    round on 14 June, its track written as 14, Hungaroring, hungaroring or "14 – Hungaroring",
    and the queue runs it. Pro's round is stored at the Hungaroring."""
    league = await setup_league(tmp_path, divisions=_pro())

    await _imported(league, "bulk", _paste(14, track=written))

    assert [track for _, _, track, _ in await round_rows(league.db_path, "Pro")] == ["Hungaroring"]


async def test_a_division_is_matched_whatever_its_casing(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager runs `/round add-bulk` for
    "  pRo  " and pastes one round, and the queue runs it. Pro holds the round."""
    league = await setup_league(tmp_path, divisions=_pro())

    await _imported(league, "bulk", _paste(14), division="  pRo  ")

    assert len(await round_rows(league.db_path, "Pro")) == 1


# ── Merging with what is already there ────────────────────────────────────


async def test_existing_rounds_are_kept_and_renumbered_around_the_new_ones(tmp_path):
    """The second pass of a calendar too long for one modal must not discard the first. Season #1
    in Placements, Pro holding rounds at the Hungaroring on 7, 21 and 28 June. The manager pastes
    rounds on 14 June and 4 July, and the queue runs it. Pro holds five rounds, numbered 1 to 5 by
    moment: 7, 14, 21 and 28 June, and 4 July."""
    league = await setup_league(
        tmp_path, divisions=_pro(_hungaroring(7), _hungaroring(21), _hungaroring(28)),
    )

    await _imported(league, "bulk", _paste(14) + "\n" + _paste(4, month=7))

    assert [(n, at[:10]) for n, _, _, at in await round_rows(league.db_path, "Pro")] == [
        (1, "2026-06-07"), (2, "2026-06-14"), (3, "2026-06-21"), (4, "2026-06-28"),
        (5, "2026-07-04"),
    ]


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_an_imported_round_sorts_beside_ones_added_by_hand(tmp_path):
    """The regression guard for the zone. A round is stored in UTC with its offset; one given
    in a zone must be converted and stored the same way, or the two cannot be sorted together.
    Season #1 in Placements, Pro holding a round at the Hungaroring on 7 June at 18:00 UTC. The
    manager imports by XML a round for Pro at 19:00 on 14 June, Europe/Lisbon time, and the queue
    runs it. Pro holds two rounds, the new one round 2, stored as 18:00 UTC on 14 June."""
    league = await setup_league(tmp_path, divisions=_pro(_hungaroring(7)))

    await _imported(league, "xml", calendar_xml(
        ("Pro", (("2026-06-14T19:00", "Europe/Lisbon", "Normal", "14"),)),
    ))

    assert [(n, at) for n, _, _, at in await round_rows(league.db_path, "Pro")] == [
        (1, "2026-06-07T18:00:00+00:00"), (2, "2026-06-14T18:00:00+00:00"),
    ]


# ── All or nothing ────────────────────────────────────────────────────────


async def test_one_unknown_track_rejects_the_whole_import(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager pastes rounds on 14, 21 and 28
    June, the second at track 99, which the bot does not know. They are refused at once, naming
    that one problem, nothing is asked of the change queue, and Pro holds no round."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro())
    asked = await _imported(league, "bulk", _paste(14) + "\n" + _paste(21, track="99")
                            + "\n" + _paste(28))

    assert said(asked) == [_rejected("Line 2: unknown track `99`.")]
    assert await _nothing_asked(league)
    assert await round_rows(league.db_path, "Pro") == []


async def test_a_rejected_import_leaves_existing_rounds_untouched(tmp_path):
    """Season #1 in Placements, Pro holding rounds at the Hungaroring on 7 and 21 June. The
    manager pastes a round on 14 June at track 99. They are refused, and Pro holds its two rounds
    as they were."""
    league = await setup_league(tmp_path, divisions=_pro(_hungaroring(7), _hungaroring(21)))
    before = await round_rows(league.db_path, "Pro")

    await _imported(league, "bulk", _paste(14, track="99"))

    assert await round_rows(league.db_path, "Pro") == before


async def test_a_division_that_fails_rejects_the_others_too(tmp_path):
    """A season calendar is one artefact; a manager wanting them separate runs it twice. Season #1
    in Placements, Pro and Am holding no round. The manager imports by XML a sound round for Pro
    on 14 June and one for Am on 21 June at track 99. They are refused, and neither division
    holds a round."""
    league = await setup_league(tmp_path, divisions=(("Pro", 1, 10, ()), ("Am", 2, 20, ())))

    await _imported(league, "xml", calendar_xml(
        ("Pro", (("2026-06-14T18:00", "UTC", "Normal", "14"),)),
        ("Am", (("2026-06-21T18:00", "UTC", "Normal", "99"),)),
    ))

    assert await round_rows(league.db_path, "Pro") == []
    assert await round_rows(league.db_path, "Am") == []


async def test_a_division_not_in_the_setup(tmp_path):
    """Season #1 in Placements, Pro alone. The manager runs `/round add-bulk` for Elite and pastes
    one round. They are refused, the season having no division Elite, and nothing is asked of the
    change queue."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro())

    asked = await _imported(league, "bulk", _paste(14), division="Elite")

    assert said(asked) == [_rejected("[Elite]: no division of that name in the pending setup.")]
    assert await _nothing_asked(league)


async def test_a_division_named_twice_in_one_payload(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager imports by XML Pro twice, a
    round on 14 June under the first and one on 21 June under the second. They are refused, Pro
    named more than once, and Pro holds no round."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro())

    asked = await _imported(league, "xml", calendar_xml(
        ("Pro", (("2026-06-14T18:00", "UTC", "Normal", "14"),)),
        ("Pro", (("2026-06-21T18:00", "UTC", "Normal", "14"),)),
    ))

    assert said(asked) == [_rejected("[Pro]: named more than once in this import.")]
    assert await round_rows(league.db_path, "Pro") == []


# ── Duplicate datetimes ───────────────────────────────────────────────────


async def test_two_imported_rounds_may_not_share_a_moment(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager pastes two rounds, both on 14
    June at 18:00. They are refused, the second line repeating the first's moment, and Pro holds
    no round."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro())

    asked = await _imported(league, "bulk", _paste(14, 14))

    assert said(asked) == [_rejected(
        "Line 2: duplicate datetime 2026-06-14T18:00:00 — already used by Line 1."
    )]
    assert await round_rows(league.db_path, "Pro") == []


@pytest.mark.xfail(strict=True, reason=_CLASH)
async def test_an_imported_round_may_not_land_on_an_existing_one(tmp_path):
    """The confirmation of placements refuses such a season; catching it here saves finding out
    later. Season #1 in Placements, Pro holding a round on 14 June at 18:00. The manager pastes a
    round at that moment. They are refused, the moment held by a round already in the division,
    and Pro holds its one round."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro(_hungaroring(14)))

    asked = await _imported(league, "bulk", _paste(14))

    assert said(asked) == [_rejected(
        "Line 1: duplicate datetime 2026-06-14T18:00:00 — already used by a round already in "
        "this division."
    )]
    assert len(await round_rows(league.db_path, "Pro")) == 1


# ── The capacity guard ────────────────────────────────────────────────────

_FULL = ("That would give the division 13 rounds, but the configured calendar template draws 10",
         "Enlarge the template, or turn the `calendar` image aspect off with "
         "`/images config toggle`.")


async def test_the_guard_is_asked_of_the_whole_batch(tmp_path, monkeypatch):
    """The hazard: each of five rounds clears a `+ 1` check that the five together fail. Season
    #1 in Placements, Pro holding eight rounds, on 1 to 8 June. The image module's drawing says
    the calendar template draws ten. The manager pastes five rounds, on 1 to 5 July. The drawing
    is asked once, of thirteen rounds; the manager is refused with its problem, saying the round
    was not added; and Pro holds its eight rounds."""
    from tests.support.settings_league import said

    league = await setup_league(tmp_path, divisions=_pro(*(_hungaroring(d) for d in range(1, 9))))
    drawing = no_room(*_FULL)
    monkeypatch.setattr(league.bot.image_drawing, "round_capacity_problem", drawing)

    asked = await _imported(league, "bulk", _paste(1, 2, 3, 4, 5, month=7))

    assert [call.args[0] for call in drawing.await_args_list] == [13]
    assert said(asked) == [_rejected(
        f"[Pro]: ❌ {_FULL[0]}. The round was **not** added.\n{_FULL[1]}"
    )]
    assert len(await round_rows(league.db_path, "Pro")) == 8


async def test_a_batch_that_fits_is_allowed(tmp_path, monkeypatch):
    """Season #1 in Placements, Pro holding a round on 7 June. The image module's drawing has room
    for ten rounds. The manager pastes rounds on 14 and 21 June, and the queue runs it. Pro holds
    three rounds."""
    from unittest.mock import AsyncMock

    league = await setup_league(tmp_path, divisions=_pro(_hungaroring(7)))
    monkeypatch.setattr(league.bot.image_drawing, "round_capacity_problem",
                        AsyncMock(return_value=None))

    await _imported(league, "bulk", _paste(14, 21))

    assert len(await round_rows(league.db_path, "Pro")) == 3


# ── Mystery rounds ────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_a_mystery_round_without_a_track_is_stored_with_none(tmp_path):
    """Season #1 in Placements, Pro holding no round. The manager pastes a mystery round on 14
    June with no track, and the queue runs it. Pro holds round 1, a MYSTERY round with no
    track."""
    league = await setup_league(tmp_path, divisions=_pro())

    await _imported(league, "bulk", "2026-06-14T18:00, Mystery")

    assert await round_rows(league.db_path, "Pro") == [
        (1, "MYSTERY", None, "2026-06-14T18:00:00+00:00")
    ]


# ── On the change queue (#283) ────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=_NO_OFFSET)
async def test_an_import_is_one_save_across_its_divisions(tmp_path):
    """The core specification's record of what changed. Season #1 in Placements, as the database
    alone holds it: Pro holds mystery rounds on 14 and 28 June at 18:00 UTC, Am one on 21 June,
    and Rookie one on 21 June too. The manager imports by XML, in UTC, rounds at the Hungaroring
    for Pro on 21 June and 5 July and for Am on 14 June, all at 18:00. They are answered with the
    queue's acknowledgement, "Adding 3 rounds to **Pro** and **Am**", naming its first job, and
    nothing is saved until the queue runs it. Once it has, Pro holds four rounds numbered by
    moment (14, 21 and 28 June, 5 July) and Am two (14 and 21 June); Rookie's round is untouched;
    three audit entries `ROUND_ADDED`, one for each round added, by Admin, each on its own
    division, stamped with the queue's clock, record each round as replacing nothing; the log
    channel holds today's success line alone, naming each division and how many rounds it holds;
    and the acknowledgement is updated to today's reply."""
    from tests.support.change_queue import run_queue, step_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines, said

    league = await setup_league(
        tmp_path, divisions=(*_HELD, ("Rookie", 3, 30, (_AM_21,))),
    )
    rookie = await round_rows(league.db_path, "Rookie")
    asked = await import_rounds(league, "xml", calendar_xml(
        ("Pro", (("2026-06-21T18:00", "UTC", "Normal", "14"),
                 ("2026-07-05T18:00", "UTC", "Normal", "14"))),
        ("Am", (("2026-06-14T18:00", "UTC", "Normal", "14"),)),
    ))

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == (
        f"{acknowledged('Adding 3 rounds to **Pro** and **Am**')} It begins with job #{first_job}."
    )
    assert len(await round_rows(league.db_path, "Pro")) == 2
    await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == [
        (1, "MYSTERY", None, "2026-06-14T18:00:00+00:00"),
        (2, "NORMAL", "Hungaroring", "2026-06-21T18:00:00+00:00"),
        (3, "MYSTERY", None, "2026-06-28T18:00:00+00:00"),
        (4, "NORMAL", "Hungaroring", "2026-07-05T18:00:00+00:00"),
    ]
    assert await round_rows(league.db_path, "Am") == [
        (1, "NORMAL", "Hungaroring", "2026-06-14T18:00:00+00:00"),
        (2, "MYSTERY", None, "2026-06-21T18:00:00+00:00"),
    ]
    assert await round_rows(league.db_path, "Rookie") == rookie
    pro, am = await round_ids(league.db_path, "Pro"), await round_ids(league.db_path, "Am")
    added = [
        (pro, 2, "2026-06-21T18:00:00+00:00"), (pro, 4, "2026-07-05T18:00:00+00:00"),
        (am, 1, "2026-06-14T18:00:00+00:00"),
    ]
    expected = [
        {"round_id": ids[at], "division_id": ids["division"], "round_number": number,
         "format": "NORMAL", "track_name": "Hungaroring", "scheduled_at": at}
        for ids, number, at in added
    ]
    entries = await audit_rows(league.db_path)
    assert sorted((e["new_value"] for e in entries), key=lambda v: v["round_id"]) == sorted(
        expected, key=lambda v: v["round_id"]
    )
    for entry in entries:
        assert (entry["change_type"], entry["actor_id"]) == ("ROUND_ADDED", 4242)
        assert entry["division_id"] == entry["new_value"]["division_id"]
        assert entry["old_value"] == dict.fromkeys(entry["new_value"])
        assert entry["timestamp"] == NOW.isoformat()
    assert await log_lines(league) == [_success_line("/round add-xml", ("Pro", 4), ("Am", 2))]
    assert last_said(asked) == (
        "✅ Added rounds to 2 division(s).\n"
        "  **Pro**: 4 round(s) in total\n"
        "  **Am**: 2 round(s) in total"
    )


@pytest.mark.parametrize("form, value, command", [
    ("bulk", _paste(21), "/round add-bulk"),
    ("xml", calendar_xml(("Pro", (("2026-06-21T18:00", "UTC", "Normal", "14"),))),
     "/round add-xml"),
])
async def test_a_failed_import_save_adds_nothing(tmp_path, form, value, command):
    """Season #1 in Placements, Pro holding its rounds of 14 and 28 June. The manager imports,
    by `/round add-bulk` or by `/round add-xml`, a round for Pro on 21 June at 18:00, and as the
    queue runs it the database fails writing the audit entry. Nothing of the change is saved, the
    rounds, the renumbering and the audit entries being one save: Pro holds its two rounds,
    numbered 1 and 2, and the log channel holds no success line. The league's lane stops at the
    job. Once a league admin discards it, the manager's reply says that nothing was added and to
    run the command they used again, and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    before = await round_rows(league.db_path, "Pro")
    asked = await import_rounds(league, form, value)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await round_rows(league.db_path, "Pro") == before
    assert await audit_rows(league.db_path) == []
    assert not any(f"| {command} | Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == f"Nothing was added: no round was imported. Run `{command}` again."
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, problem",
    [
        pytest.param(
            "a-round-added-at-that-moment",
            "Line 1: duplicate datetime 2026-06-21T18:00:00 — already used by a round already in "
            "this division.",
            marks=pytest.mark.xfail(strict=True, reason=_CLASH),
            id="a-round-added-at-that-moment-meanwhile",
        ),
        pytest.param(
            "the-division-deleted",
            "[Pro]: no division of that name in the pending setup.",
            id="the-division-gone-by-the-time-it-runs",
        ),
        pytest.param(
            "the-setup-deleted", None,
            id="the-setup-gone-by-the-time-it-runs",
        ),
        pytest.param(
            "the-calendar-full",
            "[Pro]: ❌ That would give the division 3 rounds, but the configured calendar "
            "template draws 2. The round was **not** added.\nEnlarge the template, or turn the "
            "`calendar` image aspect off with `/images config toggle`.",
            id="no-room-in-the-calendar-by-the-time-it-runs",
        ),
    ],
)
async def test_an_import_is_judged_again_as_it_starts(tmp_path, monkeypatch, meanwhile, problem):
    """Season #1 in Placements, Pro holding its rounds of 14 and 28 June. The manager pastes, in
    `/round add-bulk` for Pro, a round at the Hungaroring on 21 June at 18:00, and nothing is
    saved at the ask. Before the queue runs it, one of four things happens: the manager runs
    `/round add` for Pro, a mystery round on 21 June at 18:00, which is acknowledged and runs
    first; Pro is deleted; the season being set up is deleted; or the image module's drawing
    comes to say the calendar template draws two rounds. Then the queue runs. The import is
    refused as it starts, in the words of the ask, its reply updated to them: the rejection naming
    the clash, the division not found, or the calendar full, or "❌ No pending season setup. Run
    `/season setup` first.". After the clash Pro holds rounds on 14, 21 and 28 June, the 21 June
    round the mystery round `/round add` added; after the calendar full, its rounds of 14 and 28
    June; where Pro or the setup was deleted, no round. Only the clash case has an audit entry,
    `/round add`'s; the log channel holds that one's success line, where it ran, then a refusal
    line naming the form and carrying the problem. The queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, interaction, last_said, log_lines

    league = await setup_league(tmp_path, divisions=_HELD)
    added = 0
    if meanwhile == "a-round-added-at-that-moment":
        from leaguebot.core.cogs.round_cog import RoundCog
        from tests.support.undecorate import undecorate

        await undecorate(RoundCog.round_add)(
            RoundCog(league.bot), interaction(league.bot, "round add"), "Pro", "MYSTERY",
            "2026-06-21T18:00:00", "",
        )
        added = 1
    asked = await import_rounds(league, "bulk", _paste(21))
    if meanwhile == "the-calendar-full":
        monkeypatch.setattr(league.bot.image_drawing, "round_capacity_problem", no_room(
            "That would give the division 3 rounds, but the configured calendar template draws 2",
            "Enlarge the template, or turn the `calendar` image aspect off with "
            "`/images config toggle`.",
        ))
    elif meanwhile in ("the-division-deleted", "the-setup-deleted"):
        async with get_connection(league.db_path) as db:
            # Whatever an import saved at the ask, before the build, is left behind.
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute("DELETE FROM rounds WHERE division_id IN "
                             "(SELECT id FROM divisions WHERE name = 'Pro')")
            await db.execute("DELETE FROM divisions WHERE name = 'Pro'")
            if meanwhile == "the-setup-deleted":
                await db.execute("DELETE FROM rounds")
                await db.execute("DELETE FROM divisions")
                await db.execute("DELETE FROM seasons")
            await db.commit()
    await run_queue(league.bot)

    reply = (_rejected(problem) if problem is not None
             else "❌ No pending season setup. Run `/season setup` first.")
    assert last_said(asked) == reply
    held = [at for _, _, _, at in await round_rows(league.db_path, "Pro")]
    if meanwhile in ("the-division-deleted", "the-setup-deleted"):
        assert held == []
    else:
        assert held == ["2026-06-14T18:00:00+00:00", *(["2026-06-21T18:00:00+00:00"] * added),
                        "2026-06-28T18:00:00+00:00"]
    if added:
        assert (await round_rows(league.db_path, "Pro"))[1][1] == "MYSTERY"
    assert len(await audit_rows(league.db_path)) == added
    lines = await log_lines(league)
    assert len(lines) == added + 1
    assert all("| /round add | Success" in line for line in lines[:added])
    assert lines[-1].startswith(f"⛔ {FORMS['bulk']} refused for {NAMED} — ")
    assert (problem or "No pending season setup") in lines[-1]
    assert await stopped_job(league.db_path) is None


async def test_an_import_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. Season
    #1 in Placements, Pro holding its rounds of 14 and 28 June. The manager pastes, in
    `/round add-bulk` for Pro, a round on 21 June, and is acknowledged, "Adding 1 round to
    **Pro**"; before the queue runs it, they submit the same paste again. The second is refused at
    once, in the queue's words, and the log channel takes that refusal as a line; only the one
    change is queued. The queue runs it: Pro holds the round once, with its one audit entry, and
    the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said
    from tests.support.setup_rounds import refusal_line

    league = await setup_league(tmp_path, divisions=_HELD)
    first = await import_rounds(league, "bulk", _paste(21))
    repeat = await import_rounds(league, "bulk", _paste(21))

    assert said(first)[0].startswith(acknowledged("Adding 1 round to **Pro**"))
    assert said(repeat) == [repeated("Adding 1 round to **Pro**")]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert len(await round_rows(league.db_path, "Pro")) == 3
    assert len(await audit_rows(league.db_path)) == 1
    assert await log_lines(league) == [
        refusal_line(FORMS["bulk"], repeated("Adding 1 round to **Pro**")),
        _success_line("/round add-bulk", ("Pro", 3)),
    ]
