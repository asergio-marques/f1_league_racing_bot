"""Every in-memory store of league state is dropped by `clear_in_memory_state` (issue #247).

`/bot pack` and `/bot factory-reset` change the database under a running bot. A store in
memory that survived them would act on a league the database no longer holds, so the clear
has to name every one — and the completeness check below is what keeps that true as stores
are added.
"""
from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from leaguebot.signup.cogs import admin_review_cog
from leaguebot.core.services.in_memory_state import clear_in_memory_state

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "leaguebot"

#: A mutable container set up empty at module level, or on `self`, typed or not. A
#: function's own locals are indented and carry no `self.`, and die with the call.
_STORE = re.compile(
    r"^(?:\s+self\.)?([A-Za-z_]\w*)\s*(?::\s*[^=\n]+)?="
    r"\s*(?:\{\}|set\(\)|\[\]|dict\(\)|list\(\)|defaultdict\([^)]*\))\s*(?:#.*)?$",
    re.MULTILINE,
)

#: Stores `clear_in_memory_state` clears, by (file, name).
CLEARED = {
    ("signup/cogs/admin_review_cog.py", "_PENDING_REASONS"),
    ("core/cogs/season_cog.py", "_pending"),
    ("signup/services/wizard_service.py", "_correction_tasks"),
    # The interactions the change queue holds to update each change's reply: a pack or a
    # factory reset deletes the changes they belong to (#439).
    ("core/services/change_queue.py", "_held"),
}

#: Stores that hold no league state, and why.
EXEMPT = {
    # The phase callables bound at start-up: the bot's wiring, not the league's data.
    ("core/services/scheduler_service.py", "_phase_callbacks"),
    # The placements-review button's own report. It dies with the view, and once no server
    # is claimed every press on it is refused.
    ("core/cogs/season_cog.py", "_report"),
    # The amendment's session chooser: what the member ticked, read once when they press
    # Continue. It dies with the ephemeral view it belongs to (#345).
    ("results/cogs/results_cog.py", "selected"),
    # A season review's poster: the messages one review command posted, handed to its button
    # and gone when the command returns (#228). `_report` above is where they then live.
    ("core/cogs/season_cog.py", "posted"),
    # One parsed SVG document's index of its own elements.
    ("image/utils/svg_document.py", "by_id"),
    ("image/utils/svg_document.py", "by_label"),
    # One parsed stylesheet's record of the order its rules were declared in, and what each
    # class combination resolved to under it (#165). Both die with the stylesheet, which
    # lives no longer than the render or check that parsed its template.
    ("image/utils/svg_document.py", "positions"),
    ("image/utils/svg_document.py", "_by_class"),
    # The hub's options, registered by modules at import (#279): the bot's code, not the
    # league's data. A pack keeps the modules, so it keeps what they offer.
    ("core/services/hub_service.py", "_OPTIONS"),
    # The change types the builder registers at start-up: the bot's wiring, not the league's
    # data (#439).
    ("core/services/change_queue.py", "_types"),
    # The log-channel warnings being sent to members, each task dropping itself when done
    # (#439).
    ("core/services/output_router.py", "_tasks"),
    # The interactions whose member has been warned that the log channel failed, by id; each
    # lapses with its interaction's 14 minutes (#439).
    ("core/services/output_router.py", "_warned"),
}


def _stores() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in _STORE.finditer(text):
            found.add((path.relative_to(SRC).as_posix(), match.group(1)))
    return found


def test_every_store_is_cleared_or_exempt():
    unaccounted = sorted(_stores() - CLEARED - EXEMPT)
    assert unaccounted == [], (
        f"clear_in_memory_state does not clear {unaccounted}; clear it there, or exempt it "
        f"here with the reason it holds no league state"
    )


def test_the_register_names_no_store_that_does_not_exist():
    assert sorted((CLEARED | EXEMPT) - _stores()) == []


async def test_the_clear_empties_every_store():
    """Every store is emptied, and a pending reason's five-minute lapse is cancelled with it,
    so none fires after a pack or a factory reset (#482)."""
    import asyncio

    season_cog = MagicMock()
    task = asyncio.get_running_loop().create_future()
    bot = SimpleNamespace(
        get_cog=lambda name: season_cog if name == "SeasonCog" else None,
        wizard_service=SimpleNamespace(_correction_tasks={"7": task}),
    )
    lapse = asyncio.get_running_loop().create_future()
    admin_review_cog._PENDING_REASONS[(1, 2)] = {"action": "x", "lapse": lapse}

    clear_in_memory_state(bot)

    season_cog.clear_pending.assert_called_once_with()
    assert admin_review_cog._PENDING_REASONS == {}
    assert bot.wizard_service._correction_tasks == {}
    assert task.cancelled()
    assert lapse.cancelled()


async def test_clearing_a_leagues_state_forgets_the_interactions_the_change_queue_holds(tmp_path):
    """A pack or a factory reset deletes the changes the queue holds replies for (#439), so the
    clear makes the queue forget them: a change finishing afterwards updates no member's reply.

    Without `forget_held` in the clear the reply would still be updated, as
    `test_the_acknowledgement_is_updated_with_the_outcome` shows of a change nobody cleared.
    """
    from datetime import datetime, timezone

    from leaguebot.core.db.database import run_migrations
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step
    from tests.support.change_queue import (
        acknowledgement,
        attach_queue,
        change_rows,
        league_double,
        member_interaction,
        run_queue,
        seed_server,
        updated_reply,
    )

    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.get_cog = lambda name: None
    bot.wizard_service = None
    ran: list[str] = []

    async def _go(_ctx):
        return Verdict.go()

    async def _act(_ctx):
        ran.append("a")
        return StepResult()

    attach_queue(
        bot,
        db_path,
        now=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        types=[
            ChangeType(
                kind="dummy",
                opening=(PlannedStep("a", {}),),
                steps={"a": Step("a", StepKind.ACT, _act)},
                check=_go,
                key=lambda payload: "dummy",
                doing=lambda _payload: "Doing the dummy thing",
                outcome=lambda _ctx: "✅ Done.",
                fault_outcome=lambda _ctx: "Nothing was changed.",
            )
        ],
    )
    interaction = member_interaction(bot)
    await bot.change_queue.ask("dummy", {}, interaction=interaction, what="`/dummy`",
                               refusal_what="`/dummy`")
    assert acknowledgement(interaction).startswith("⏳ Doing the dummy thing.")

    clear_in_memory_state(bot)
    await run_queue(bot)

    assert ran == ["a"]
    assert [row["state"] for row in await change_rows(db_path)] == ["DONE"]
    assert updated_reply(interaction) == ""
