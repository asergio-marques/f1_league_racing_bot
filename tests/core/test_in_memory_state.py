"""Every in-memory store of league state is dropped by `clear_in_memory_state` (issue #247).

`/bot pack` and `/bot factory-reset` change the database under a running bot. A store in
memory that survived them would act on a league the database no longer holds, so the clear
has to name every one — and the completeness check below is what keeps that true as stores
are added.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from leaguebot.core.db.database import run_migrations
from leaguebot.core.services.in_memory_state import clear_in_memory_state
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
    ("core/cogs/season_cog.py", "_pending"),
    # The interactions the change queue holds to update each change's reply: a factory reset
    # deletes the changes they belong to (#439), and a pack finds none, being refused while the
    # queue holds a job.
    ("core/services/change_queue.py", "_held"),
    # What the queue remembers of its stopped jobs: when each stop notice was last tried, and
    # each Retry under way. `forget_held` clears them with the interactions above (#439).
    ("core/services/change_queue.py", "_notice_tried"),
    ("core/services/change_queue.py", "_retrying"),
}

#: Stores the plan deletes, and the commit that deletes them: accounted for while they stand,
#: and not looked for once they are gone, since the build may not change this file (#439).
LEAVING = {
    # The wizards' correction timeouts, cleared through the service's private store until #439
    # 6b's C88 deletes it, a correction's five minutes then being a one-off timer asking the
    # queue (C85, C87) with nothing held in memory.
    ("signup/services/wizard_service.py", "_correction_tasks"): "#439 6b C88",
    # The placements-review button's own report, held by the view until the review is posted
    # on the queue, its question's row then holding every report id (C93). Exempt while it
    # stands: it dies with the view, and once no server is claimed every press on it is refused.
    ("core/cogs/season_cog.py", "_report"): "#439 6b C93",
}

#: Stores the plan moves with no change, by (file, name): the file each moves to and the commit
#: that moves it. Accounted for at either address, and the clear's test reaches each wherever
#: it stands (#439).
MOVING = {
    # The pending reasons of Reject and Request Changes, cleared by `clear_pending_reasons`,
    # moving with the review's views into signup's services.
    ("signup/cogs/admin_review_cog.py", "_PENDING_REASONS"): (
        "signup/services/signup_views.py", "#439 6b C65a",
    ),
}

#: Stores the plan adds that hold no league state, and the commit that adds each, with why:
#: accounted for once they stand, and not looked for before (#439).
ARRIVING_EXEMPT = {
    # One lock per lane, each lane's worker holding its own while it drains its lane: the
    # queue's wiring, not the league's data (6b L4).
    ("core/services/change_queue.py", "_working"): "#439 6b L4",
    # The job each lane's worker is trying, keyed by lane, set only while a job runs: a pack is
    # refused while any lane holds a job, and a factory reset empties every lane (6b L5).
    ("core/services/change_queue.py", "_trying"): "#439 6b L5",
    # Each running lane's worker task, keyed by lane: the queue's own tasks, cancelled by
    # `stop()`, never the league's data (6b L10).
    ("core/services/change_queue.py", "_workers"): "#439 6b L10",
}

#: Stores that hold no league state, and why.
EXEMPT = {
    # The phase callables bound at start-up: the bot's wiring, not the league's data.
    ("core/services/scheduler_service.py", "_phase_callbacks"),
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


def _moving() -> set[tuple[str, str]]:
    """Both addresses of every store the plan moves."""
    return set(MOVING) | {(to, name) for (_, name), (to, _commit) in MOVING.items()}


def test_every_store_is_cleared_or_exempt():
    unaccounted = sorted(
        _stores() - CLEARED - EXEMPT - set(LEAVING) - _moving() - set(ARRIVING_EXEMPT)
    )
    assert unaccounted == [], (
        f"clear_in_memory_state does not clear {unaccounted}; clear it there, or exempt it "
        f"here with the reason it holds no league state"
    )


def test_the_register_names_no_store_that_does_not_exist():
    stores = _stores()
    assert sorted((CLEARED | EXEMPT) - stores) == []
    assert not (set(LEAVING) & (CLEARED | EXEMPT))
    assert not ((_moving() | set(ARRIVING_EXEMPT)) & (CLEARED | EXEMPT | set(LEAVING)))
    stranded = sorted(
        (old, to) for old, (to, _commit) in MOVING.items()
        if old not in stores and (to, old[1]) not in stores
    )
    assert stranded == [], f"a moving store stands at neither address: {stranded}"


def _pending_reasons() -> dict:
    """The pending reasons' store, wherever it stands: in signup's views once C65a moves it
    there, in the review cog until then."""
    import importlib

    for module in (
        "leaguebot.signup.services.signup_views",
        "leaguebot.signup.cogs.admin_review_cog",
    ):
        try:
            found = importlib.import_module(module)
        except ModuleNotFoundError as missing:
            if missing.name != module:
                raise
            continue
        if hasattr(found, "_PENDING_REASONS"):
            return found._PENDING_REASONS
    raise AssertionError("no module holds the pending reasons")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#439: the clear still reaches the wizard service's private correction timeouts, which "
        "C88 deletes with the wizard's posting handlers, and looks the change queue up on the bot"
    ),
)
async def test_the_clear_empties_every_store():
    """Every store is emptied, and a pending reason's five-minute lapse is cancelled with it,
    so none fires after a pack or a factory reset (#482). The wizards hold no correction
    timeouts in memory once a correction's five minutes are a one-off timer asking the queue
    (#439, 6b, C85/C87/C88), so the clear reaches nothing of the wizard service: a wizard
    service holding nothing at all is left untouched."""
    import asyncio

    season_cog = MagicMock()
    bot = SimpleNamespace(
        get_cog=lambda name: season_cog if name == "SeasonCog" else None,
        wizard_service=SimpleNamespace(),
    )
    lapse = asyncio.get_running_loop().create_future()
    pending = _pending_reasons()
    pending[(1, 2)] = {"action": "x", "lapse": lapse}

    clear_in_memory_state(bot, queue=MagicMock())

    season_cog.clear_pending.assert_called_once_with()
    assert pending == {}
    assert lapse.cancelled()


@pytest.mark.xfail(strict=True, reason="#439: the clearing of what the bot holds in memory looks the change queue up on the bot")
async def test_clearing_a_leagues_state_forgets_the_interactions_the_change_queue_holds(tmp_path):
    """A factory reset deletes the changes the queue holds replies for (#439), so the clear makes
    the queue forget them: a change finishing afterwards updates no member's reply.

    Without `forget_held` in the clear the reply would still be updated, as
    `test_the_acknowledgement_is_updated_with_the_outcome` shows of a change nobody cleared.
    """
    from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import ChangeType, Step

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

    # A failure stops the queue rather than ending the change, so a change type has no outcome
    # for a fault; the queue built before that rule still demands one where it declares it.
    withdrawn: dict[str, Any] = (
        {"fault_outcome": lambda _ctx: "Nothing was changed."}
        if "fault_outcome" in {f.name for f in dataclasses.fields(ChangeType)} else {}
    )
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
                **withdrawn,
            )
        ],
    )
    interaction = member_interaction(bot)
    await bot.change_queue.ask("dummy", {}, interaction=interaction, what="`/dummy`",
                               refusal_what="`/dummy`")
    assert acknowledgement(interaction).startswith("⏳ Doing the dummy thing.")

    clear_in_memory_state(bot, queue=bot.change_queue)
    await run_queue(bot)

    assert ran == ["a"]
    assert [row["state"] for row in await change_rows(db_path)] == ["DONE"]
    assert updated_reply(interaction) == ""
