"""`/results config add` and `remove` — creating a points configuration and destroying one.

Issue #208. `results_cog.py` was at 47.4%. A points configuration is what turns a finishing
position into a championship point, so it is the thing a league's whole season is scored by.

**Removing one is irreversible and is a league *admin's*.** Rebuilding a configuration means
retyping every position of every session type by hand, which puts it under the core
specification's rule that a command destroying what a league is built from, where nothing puts
it back, belongs to the higher tier. Adding, appending, detaching and editing stay a league
manager's, because each of those has another command that reverses it.

**It asks first where a season in setup is attached to it** (decided 2026-09-15, issue #132).
The removal takes the attachment with it, so the season quietly stops being the season the
manager built and is refused at approval. Naming the season and waiting is the difference
between a deliberate teardown and a surprise found at the next `/season placements-review`. With nothing
attached there is nothing to lose and the command acts straight away —
`test_a_configuration_nothing_depends_on_is_removed_without_asking` and its counterpart sit
either side of that, and collapsing them would either nag on every removal or take a season's
scoring away silently.

**The confirmation belongs to whoever opened it.** The button is `danger`-styled and says what
it does rather than "Confirm", and both buttons refuse anybody else — a second admin pressing
another's confirmation would destroy a configuration they had not been asked about.

**One change does the removal** (#439, slice 7). The straight path and the confirmation button
ask the same settings change on the change queue, so the two cannot come to differ about what
removing one does or about what the log records; both routes are tested through to the same log
line.

**Adding and removing are changes on the queue.** Where the state when asked refuses it, the
refusal is given at once and nothing is queued; otherwise the member is acknowledged at once, and
the reply is updated with today's once the change has run, its line written with it. A
configuration removed by somebody else between a confirmation and its run is refused when it
runs. The tests drive the real commands and buttons past their tier guards, on a league built by
the migrations with the real services and queue, and run the queue.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.results.cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView
from tests.support import saves
from tests.support.change_queue import MEMBER_ID, change_rows, member, run_queue
from tests.support.settings_league import interaction, last_said, log_lines, said, settings_league
from tests.support.undecorate import undecorate

#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
OTHER_ADMIN = 88
CONFIG = "100%"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(*, results_enabled: bool = True) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


async def _league(tmp_path: Any, *, results: bool = True, config: bool = True,
                  standing: int | None = None) -> Any:
    """A league with results on (*results*), holding the configuration CONFIG where *config*
    says so, and, where *standing* names a season number, that season in setup attached to it."""
    league = await settings_league(tmp_path, modules=("results",) if results else ())
    if config:
        await saves.create_config(league.db_path, CONFIG)
    if standing is not None:
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status, stage) "
                "VALUES (1, ?, '2026-01-01', 'SETUP', 'CONFIGURATION')",
                (standing,),
            )
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (1, ?)",
                (CONFIG,),
            )
            await db.commit()
    return league


async def _held(db_path: str) -> list[str]:
    """The names of the points configurations the league holds."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT config_name FROM points_config_store ORDER BY config_name")
        return [row["config_name"] for row in await cursor.fetchall()]


async def _add(league: Any, name: str = CONFIG) -> Any:
    """Run `/results config add <name>` as Admin; the queue is not run."""
    asked = interaction(league.bot, "results config add")
    await undecorate(ResultsCog.config_add)(ResultsCog(league.bot), asked, name)
    return asked


async def _remove(league: Any, name: str = CONFIG) -> Any:
    """Run `/results config remove <name>` as Admin; the queue is not run."""
    asked = interaction(league.bot, "results config remove")
    await undecorate(ResultsCog.config_remove)(ResultsCog(league.bot), asked, name)
    return asked


def _view_of(asked: Any) -> Any:
    """The view the member was answered with, where one was."""
    calls = asked.response.send_message.await_args_list + asked.followup.send.await_args_list
    for call in calls:
        if call.kwargs.get("view") is not None:
            return call.kwargs["view"]
    return None


async def _press(league: Any, button: str, *, user: Any = None, view: Any = None) -> Any:
    """Press *button* ("confirm" or "cancel") of the removal's confirmation of CONFIG, which Admin
    opened, as *user* (Admin by default); the queue is not run."""
    view = view or _ConfirmRemoveConfigView(ResultsCog(league.bot), MEMBER_ID, CONFIG)
    pressed = interaction(league.bot, user=user)
    label = {"confirm": "\u2705 Remove it anyway", "cancel": "\u274c Cancel"}[button]
    await getattr(type(view), button)(view, pressed, type("Button", (), {"label": label})())
    return pressed


# ---------------------------------------------------------------------------
# The module gate
# ---------------------------------------------------------------------------


async def test_adding_is_refused_while_the_module_is_disabled(tmp_path):
    """A points configuration is meaningless without the module that scores by it: refused at
    once, nothing queued, nothing created."""
    league = await _league(tmp_path, results=False, config=False)

    asked = await _add(league)

    assert "not enabled" in " ".join(said(asked))
    assert await change_rows(league.db_path) == []
    assert await _held(league.db_path) == []


async def test_removing_is_refused_while_the_module_is_disabled(tmp_path):
    league = await _league(tmp_path, results=False)

    asked = await _remove(league)

    assert "not enabled" in " ".join(said(asked))
    assert await change_rows(league.db_path) == []
    assert await _held(league.db_path) == [CONFIG]


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


async def test_a_configuration_is_created(tmp_path):
    """Admin adds 100%: acknowledged at once, nothing created until the change runs; then the
    configuration stands and the reply names it and says its positions start at zero — a
    manager who did not know would think the command had half-worked, and would go looking for
    the points it had invented."""
    league = await _league(tmp_path, config=False)

    asked = await _add(league)

    assert said(asked)[0].startswith("⏳ ")
    assert await _held(league.db_path) == []
    await run_queue(league.bot)
    assert await _held(league.db_path) == [CONFIG]
    assert last_said(asked) == f"✅ Config **{CONFIG}** created. All positions default to 0 points."


async def test_a_created_configuration_is_logged_by_name(tmp_path):
    """Nothing is recorded when Admin asks; once the change has run, one line names the command
    and the configuration."""
    league = await _league(tmp_path, config=False)

    await _add(league)
    assert await log_lines(league) == []
    await run_queue(league.bot)

    assert await log_lines(league) == [f"{NAMED} | /results config add | Success\n  config: {CONFIG}"]


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


async def test_removing_a_configuration_that_does_not_exist_says_so(tmp_path):
    league = await _league(tmp_path, config=False)

    asked = await _remove(league)

    assert said(asked) == [f"❌ Config **{CONFIG}** not found."]
    assert await change_rows(league.db_path) == []


async def test_a_configuration_nothing_depends_on_is_removed_without_asking(tmp_path):
    """With nothing attached there is nothing to lose, and nagging on every removal is how
    a confirmation stops being read: acknowledged at once, no question, and removed when the
    change runs."""
    league = await _league(tmp_path)

    asked = await _remove(league)

    assert _view_of(asked) is None
    assert said(asked)[0].startswith("⏳ ")
    assert await _held(league.db_path) == [CONFIG]
    await run_queue(league.bot)
    assert await _held(league.db_path) == []
    assert last_said(asked) == f"✅ Config **{CONFIG}** removed."


async def test_a_configuration_a_season_is_built_on_asks_first(tmp_path):
    """Issue #132. The removal detaches it, so the season quietly stops being the season
    the manager built and is refused at approval: Admin is asked, nothing is queued."""
    league = await _league(tmp_path, standing=3)

    asked = await _remove(league)
    await run_queue(league.bot)

    assert _view_of(asked) is not None
    assert await change_rows(league.db_path) == []
    assert await _held(league.db_path) == [CONFIG]


async def test_the_question_names_the_season_at_risk(tmp_path):
    """Naming it is the difference between a deliberate teardown and a surprise found at
    the next `/season placements-review`."""
    league = await _league(tmp_path, standing=3)

    asked = await _remove(league)

    assert "Season #3" in " ".join(said(asked))


async def test_the_question_names_every_season_at_risk(tmp_path):
    """More than one season can be in setup across a server's history of attachments; a league
    holds one live season at a time, so the second is given by the reader."""
    league = await _league(tmp_path)

    with patch(
        "leaguebot.results.services.points_config_service.setup_seasons_linking",
        AsyncMock(return_value=[(1, 3), (2, 4)]),
    ):
        asked = await _remove(league)

    replied = " ".join(said(asked))
    assert "Season #3" in replied
    assert "Season #4" in replied


async def test_the_question_says_what_removing_costs(tmp_path):
    """Every position of every session type, with no undo — a manager cannot weigh the
    decision without being told the price."""
    league = await _league(tmp_path, standing=3)

    asked = await _remove(league)

    replied = " ".join(said(asked))
    assert "no undo" in replied
    assert "refused for approval" in replied


# ---------------------------------------------------------------------------
# The confirmation
# ---------------------------------------------------------------------------


async def test_confirming_removes_the_configuration(tmp_path):
    """Season #3, in setup, stands on 100%; Admin was asked and presses "Remove it anyway": the
    press is acknowledged at once, and the configuration is removed when the change runs."""
    league = await _league(tmp_path, standing=3)

    pressed = await _press(league, "confirm")

    assert said(pressed)[0].startswith("⏳ ")
    assert await _held(league.db_path) == [CONFIG]
    await run_queue(league.bot)
    assert await _held(league.db_path) == []
    assert last_said(pressed) == f"✅ Config **{CONFIG}** removed."


#: What a cancelled or lapsed removal says beneath its line: what became of the change and what
#: to do next.
_NOTHING_REMOVED = (
    f"Nothing was removed; {CONFIG} is still attached. Run /results config remove again to "
    "remove it."
)


def _assert_removal_abandoned(line: str, ending: str) -> None:
    first, *detail = line.splitlines()
    assert first.startswith(("↩️ ", "⌛ ")), line
    assert "/results config remove" in first, line
    assert first.endswith(ending), line
    assert " ".join(text.strip() for text in detail) == _NOTHING_REMOVED, line


async def test_cancelling_leaves_the_configuration_alone(tmp_path):
    """Admin asked to remove the configuration "100%", which Season #3, in setup, is built on,
    and was asked to confirm. Admin presses Cancel. Nothing is removed or queued, Admin is told
    it is untouched, and exactly one cancel line records it, naming the command and Admin, with
    "Nothing was removed; 100% is still attached. Run /results config remove again to remove
    it." beneath it."""
    league = await _league(tmp_path, standing=3)

    pressed = await _press(league, "cancel")
    await run_queue(league.bot)

    assert "untouched" in " ".join(said(pressed))
    assert await change_rows(league.db_path) == []
    assert await _held(league.db_path) == [CONFIG]
    (line,) = await log_lines(league)
    _assert_removal_abandoned(line, f"cancelled by {NAMED}")


async def test_a_removal_left_to_lapse_is_recorded(tmp_path):
    """Admin asks to remove the configuration "100%", which Season #3, in setup, is built on,
    and is asked to confirm, then answers nothing until the question lapses. Nothing is removed
    or queued, the buttons are taken down through the command's own reply, and exactly one
    lapse line records it, naming the command and Admin as the one who started it, with
    "Nothing was removed; 100% is still attached. Run /results config remove again to remove
    it." beneath it."""
    league = await _league(tmp_path, standing=3)

    asked = await _remove(league)
    question = _view_of(asked)
    asked.edit_original_response.reset_mock()
    await question.on_timeout()
    await run_queue(league.bot)

    assert await _held(league.db_path) == [CONFIG]
    assert await change_rows(league.db_path) == []
    asked.edit_original_response.assert_awaited_once()
    (line,) = await log_lines(league)
    assert line.startswith("⌛ "), line
    _assert_removal_abandoned(line, f"lapsed unconfirmed (started by {NAMED})")


@pytest.mark.parametrize("button", ["confirm", "cancel"], ids=["confirm", "cancel"])
async def test_only_the_admin_who_asked_may_answer(tmp_path, button):
    """A second admin pressing another's confirmation would destroy a configuration they
    had not been asked about: refused, nothing queued or removed."""
    league = await _league(tmp_path, standing=3)

    pressed = await _press(league, button, user=member(OTHER_ADMIN, "Sam", "Sam#0001"))
    await run_queue(league.bot)

    assert "Not your action" in " ".join(said(pressed))
    assert await change_rows(league.db_path) == []
    assert await _held(league.db_path) == [CONFIG]


async def test_the_confirmation_button_says_what_it_does():
    """`Confirm` on its own is what somebody clicks through; the label names the outcome,
    and the style marks it as destructive."""
    import discord

    cog = _make_cog()
    view = _ConfirmRemoveConfigView(cog, MEMBER_ID, CONFIG)

    confirm = next(c for c in view.children if "Remove" in (c.label or ""))
    assert confirm.style is discord.ButtonStyle.danger


# ---------------------------------------------------------------------------
# One change for the removal
# ---------------------------------------------------------------------------


async def test_both_routes_log_the_removal_identically(tmp_path):
    """The straight path and the confirmation ask the same change, so the two cannot come to
    differ about what the log records — the league's account of a destroyed configuration must
    not depend on whether a confirmation happened to be shown."""
    (tmp_path / "straight").mkdir()
    (tmp_path / "confirmed").mkdir()
    straight = await _league(tmp_path / "straight")
    await _remove(straight)
    await run_queue(straight.bot)

    confirmed = await _league(tmp_path / "confirmed", standing=3)
    await _press(confirmed, "confirm")
    await run_queue(confirmed.bot)

    assert await log_lines(straight) == await log_lines(confirmed)
    assert len(await log_lines(straight)) == 1


async def test_a_removal_that_finds_nothing_is_reported_not_raised(tmp_path):
    """The configuration can go between the confirmation and the change's run — another admin
    removing it in the meantime must not raise at this one: Admin's press is acknowledged, and
    when it runs the removal is refused as not found, recorded as refused, not as done."""
    league = await _league(tmp_path, standing=3)

    pressed = await _press(league, "confirm")
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM season_points_links WHERE config_name = ?", (CONFIG,))
        await db.execute("DELETE FROM points_config_store WHERE config_name = ?", (CONFIG,))
        await db.commit()
    await run_queue(league.bot)

    assert said(pressed)[0].startswith("⏳ ")
    assert last_said(pressed) == f"❌ Config **{CONFIG}** not found."
    (line,) = await log_lines(league)
    assert line.startswith("⛔ "), line
    assert "`/results config remove`" in line, line
    assert line.endswith(f"refused for {NAMED} — Config **{CONFIG}** not found."), line
