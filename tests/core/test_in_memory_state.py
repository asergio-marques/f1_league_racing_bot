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
    ("core/cogs/season_cog.py", "selected"),
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
    import asyncio

    season_cog = MagicMock()
    task = asyncio.get_running_loop().create_future()
    bot = SimpleNamespace(
        get_cog=lambda name: season_cog if name == "SeasonCog" else None,
        wizard_service=SimpleNamespace(_correction_tasks={"7": task}),
    )
    admin_review_cog._PENDING_REASONS[(1, 2)] = {"action": "x"}

    clear_in_memory_state(bot)

    season_cog.clear_pending.assert_called_once_with()
    assert admin_review_cog._PENDING_REASONS == {}
    assert bot.wizard_service._correction_tasks == {}
    assert task.cancelled()
