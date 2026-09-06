"""Merging an imported calendar into a pending season.

`apply_round_import` is the half of the import that needs a database — it resolves each
track — and the half that decides whether anything is written at all. It takes primitives
rather than an `Interaction`, and its capacity guard is injected, so none of this needs
Discord.

Three rules carry the weight:

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

import os
import sys
from datetime import datetime

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import PendingConfig, PendingDivision, apply_round_import  # noqa: E402
from models.round import RoundFormat  # noqa: E402
from utils.round_import import ParsedDivisionRounds, ParsedRound  # noqa: E402


@pytest.fixture
async def db_path(tmp_path):
    """Just the tracks table — it is all the applier reads."""
    path = str(tmp_path / "rounds.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE tracks (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)"
        )
        await db.executemany(
            "INSERT INTO tracks (id, name) VALUES (?, ?)",
            [(4, "Bahrain International Circuit"), (14, "Hungaroring"), (3, "Albert Park")],
        )
        await db.commit()
    return path


async def _no_overflow(_division_name: str, _would_hold: int) -> str | None:
    return None


def _cfg(*rounds: dict, name: str = "Pro") -> PendingConfig:
    return PendingConfig(
        server_id=1,
        divisions=[PendingDivision(name=name, role_id=1, rounds=list(rounds))],
    )


def _existing(day: int, number: int) -> dict:
    return {
        "round_number": number,
        "format": RoundFormat.NORMAL,
        "track_name": "Hungaroring",
        "scheduled_at": datetime(2026, 6, day, 18, 0),
    }


def _parsed(day: int, *, track: str | None = "14", name: str = "Pro", month: int = 6):
    return ParsedRound(
        location=f"Line {day}",
        scheduled_at=datetime(2026, month, day, 18, 0),
        format=RoundFormat.NORMAL,
        track_raw=track,
    )


def _payload(*rounds: ParsedRound, name: str = "Pro") -> list[ParsedDivisionRounds]:
    return [ParsedDivisionRounds(division_name=name, rounds=list(rounds))]


# ── The happy path ────────────────────────────────────────────────────────


async def test_rounds_are_sorted_and_numbered_from_one(db_path):
    cfg = _cfg()

    applied, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(28), _parsed(14), _parsed(21)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert [r["round_number"] for r in cfg.divisions[0].rounds] == [1, 2, 3]
    assert [r["scheduled_at"].day for r in cfg.divisions[0].rounds] == [14, 21, 28]
    assert applied["Pro"] == cfg.divisions[0].rounds


async def test_a_track_is_stored_under_its_canonical_name(db_path):
    cfg = _cfg()

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14, track="14")),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert cfg.divisions[0].rounds[0]["track_name"] == "Hungaroring"


@pytest.mark.parametrize("written", ["14", "Hungaroring", "hungaroring", "14 – Hungaroring"])
async def test_a_track_may_be_named_any_way_the_bot_accepts(db_path, written):
    """`resolve_track_name` owns the rule; this is the seam, not the rule."""
    cfg = _cfg()

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14, track=written)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert cfg.divisions[0].rounds[0]["track_name"] == "Hungaroring"


async def test_a_division_is_matched_whatever_its_casing(db_path):
    cfg = _cfg(name="Pro")

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), name="  pRo  "),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert len(cfg.divisions[0].rounds) == 1


# ── Merging with what is already there ────────────────────────────────────


async def test_existing_rounds_are_kept_and_renumbered_around_the_new_ones(db_path):
    """The second pass of a calendar too long for one modal must not discard the first."""
    cfg = _cfg(_existing(7, 1), _existing(21, 2), _existing(28, 3))

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), _parsed(4, month=7)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    rounds = cfg.divisions[0].rounds
    assert len(rounds) == 5
    assert [r["round_number"] for r in rounds] == [1, 2, 3, 4, 5]
    assert [(r["scheduled_at"].month, r["scheduled_at"].day) for r in rounds] == [
        (6, 7), (6, 14), (6, 21), (6, 28), (7, 4)
    ]


async def test_an_imported_round_sorts_beside_ones_added_by_hand(db_path):
    """The regression guard for the tzinfo strip.

    A round added by `/round add` is naive; one converted from a zone would be aware
    unless the parser strips it, and a division holding both raises `TypeError` the
    moment it is sorted — which is on every import.
    """
    cfg = _cfg(_existing(7, 1))

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert {r["scheduled_at"].tzinfo for r in cfg.divisions[0].rounds} == {None}


# ── All or nothing ────────────────────────────────────────────────────────


async def test_one_unknown_track_rejects_the_whole_import(db_path):
    cfg = _cfg()

    applied, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), _parsed(21, track="99"), _parsed(28)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert applied == {}
    assert cfg.divisions[0].rounds == [], "a rejected import must change nothing"
    assert len(errors) == 1
    assert "unknown track `99`" in errors[0]


async def test_a_rejected_import_leaves_existing_rounds_untouched(db_path):
    cfg = _cfg(_existing(7, 1), _existing(21, 2))
    before = [dict(r) for r in cfg.divisions[0].rounds]

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14, track="99")),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors
    assert cfg.divisions[0].rounds == before


async def test_a_division_that_fails_rejects_the_others_too(db_path):
    """A season calendar is one artefact; a manager wanting them separate runs it twice."""
    cfg = PendingConfig(
        server_id=1,
        divisions=[
            PendingDivision(name="Pro", role_id=1),
            PendingDivision(name="Am", role_id=2),
        ],
    )

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=[
            ParsedDivisionRounds("Pro", [_parsed(14)]),
            ParsedDivisionRounds("Am", [_parsed(21, track="99")]),
        ],
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors
    assert cfg.divisions[0].rounds == [], "the sound division was written anyway"
    assert cfg.divisions[1].rounds == []


async def test_a_division_not_in_the_setup(db_path):
    cfg = _cfg(name="Pro")

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), name="Elite"),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert "no division of that name" in errors[0]


async def test_a_division_named_twice_in_one_payload(db_path):
    cfg = _cfg(name="Pro")

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=[
            ParsedDivisionRounds("Pro", [_parsed(14)]),
            ParsedDivisionRounds("Pro", [_parsed(21)]),
        ],
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert "named more than once" in errors[0]


# ── Duplicate datetimes ───────────────────────────────────────────────────


async def test_two_imported_rounds_may_not_share_a_moment(db_path):
    cfg = _cfg()

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), _parsed(14)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert len(errors) == 1
    assert "duplicate datetime" in errors[0]
    assert cfg.divisions[0].rounds == []


async def test_an_imported_round_may_not_land_on_an_existing_one(db_path):
    """`/season approve` refuses such a season; catching it here saves finding out later."""
    cfg = _cfg(_existing(14, 1))

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert len(errors) == 1
    assert "already in this division" in errors[0]


# ── The capacity guard ────────────────────────────────────────────────────


async def test_the_guard_is_asked_of_the_whole_batch(db_path):
    """The hazard: each of five rounds clears a `+ 1` check that the five together fail."""
    cfg = _cfg(*[_existing(day, n) for n, day in enumerate(range(1, 9), start=1)])
    asked: list[int] = []

    async def _capacity_ten(_name: str, would_hold: int) -> str | None:
        asked.append(would_hold)
        return None if would_hold <= 10 else "the calendar template draws 10."

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(*[_parsed(day, month=7) for day in range(1, 6)]),
        db_path=db_path,
        overflow_check=_capacity_ten,
    )

    assert asked == [13], "the guard must be asked len + N, not len + 1"
    assert errors and "draws 10" in errors[0]
    assert len(cfg.divisions[0].rounds) == 8, "nothing was added"


async def test_a_batch_that_fits_is_allowed(db_path):
    cfg = _cfg(_existing(7, 1))

    async def _capacity_ten(_name: str, would_hold: int) -> str | None:
        return None if would_hold <= 10 else "too many"

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14), _parsed(21)),
        db_path=db_path,
        overflow_check=_capacity_ten,
    )

    assert errors == []
    assert len(cfg.divisions[0].rounds) == 3


# ── Mystery rounds ────────────────────────────────────────────────────────


async def test_a_mystery_round_without_a_track_is_stored_with_none(db_path):
    cfg = _cfg()
    mystery = ParsedRound(
        location="Line 1",
        scheduled_at=datetime(2026, 6, 14, 18, 0),
        format=RoundFormat.MYSTERY,
        track_raw=None,
    )

    _, errors = await apply_round_import(
        cfg=cfg,
        parsed=_payload(mystery),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    assert errors == []
    assert cfg.divisions[0].rounds[0]["track_name"] is None
    assert cfg.divisions[0].rounds[0]["format"] is RoundFormat.MYSTERY


async def test_the_round_dict_carries_exactly_what_the_season_expects(db_path):
    """Four keys, an enum and a datetime — the shape `/round add` builds and the
    snapshot writes."""
    cfg = _cfg()

    await apply_round_import(
        cfg=cfg,
        parsed=_payload(_parsed(14)),
        db_path=db_path,
        overflow_check=_no_overflow,
    )

    stored = cfg.divisions[0].rounds[0]
    assert set(stored) == {"round_number", "format", "track_name", "scheduled_at"}
    assert isinstance(stored["format"], RoundFormat)
    assert isinstance(stored["scheduled_at"], datetime)
