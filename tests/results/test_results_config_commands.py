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

**One code path does the removal.** `_apply_config_remove` is shared by the straight path and
the confirmation button so the two cannot come to differ about what removing one does or about
what the log records; both routes are tested through to the same log line.
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.cogs.results_cog import ResultsCog, _ConfirmRemoveConfigView  # noqa: E402
from leaguebot.results.services.points_config_service import (  # noqa: E402
    ConfigAlreadyExistsError,
    ConfigNotFoundError,
)
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 10208
ACTOR_ID = 77
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


def _interaction(user_id: int = ACTOR_ID):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Admin"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _view_of(interaction):
    for call in interaction.followup.send.await_args_list:
        if "view" in call.kwargs:
            return call.kwargs["view"]
    return None


@contextmanager
def _service(**overrides):
    """Patch the points config service, with every call succeeding by default.

    Yields the mocks by name so a test can assert on them. `patch.multiple` only hands
    them back when its replacements are `DEFAULT` sentinels, which would lose the return
    values these need, so the patches are stacked explicitly.
    """
    mocks = {
        "create_config": AsyncMock(return_value=None),
        "config_exists": AsyncMock(return_value=True),
        "setup_seasons_linking": AsyncMock(return_value=[]),
        "remove_config": AsyncMock(return_value=None),
    }
    mocks.update(overrides)
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(
                patch(f"leaguebot.results.services.points_config_service.{name}", new=mock)
            )
        yield mocks


async def _add(cog, interaction, name: str = CONFIG):
    await undecorate(ResultsCog.config_add)(cog, interaction, name)


async def _remove(cog, interaction, name: str = CONFIG):
    await undecorate(ResultsCog.config_remove)(cog, interaction, name)


# ---------------------------------------------------------------------------
# The module gate
# ---------------------------------------------------------------------------


async def test_adding_is_refused_while_the_module_is_disabled():
    """A points configuration is meaningless without the module that scores by it."""
    cog = _make_cog(results_enabled=False)
    interaction = _interaction()

    with _service() as svc:
        await _add(cog, interaction)

    assert "not enabled" in _replied(interaction)
    svc["create_config"].assert_not_awaited()


async def test_removing_is_refused_while_the_module_is_disabled():
    cog = _make_cog(results_enabled=False)
    interaction = _interaction()

    with _service() as svc:
        await _remove(cog, interaction)

    assert "not enabled" in _replied(interaction)
    svc["remove_config"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------


async def test_a_configuration_is_created():
    cog = _make_cog()
    interaction = _interaction()

    with _service() as svc:
        await _add(cog, interaction)

    svc["create_config"].assert_awaited_once()
    assert CONFIG in _replied(interaction)


async def test_a_new_configuration_says_its_positions_start_at_zero():
    """A manager who did not know would think the command had half-worked, and would go
    looking for the points it had invented."""
    cog = _make_cog()
    interaction = _interaction()

    with _service():
        await _add(cog, interaction)

    assert "default to 0" in _replied(interaction)


async def test_a_duplicate_name_is_refused():
    cog = _make_cog()
    interaction = _interaction()

    with _service(create_config=AsyncMock(side_effect=ConfigAlreadyExistsError(CONFIG))):
        await _add(cog, interaction)

    assert "already exists" in _replied(interaction)


async def test_a_refused_creation_is_not_logged():
    """The log is the league's record of what changed, and nothing did."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(create_config=AsyncMock(side_effect=ConfigAlreadyExistsError(CONFIG))):
        await _add(cog, interaction)

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_created_configuration_is_logged_by_name():
    cog = _make_cog()
    interaction = _interaction()

    with _service():
        await _add(cog, interaction)

    logged = cog.bot.output_router.post_log.await_args.args[0]
    assert "config add" in logged
    assert CONFIG in logged


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


async def test_removing_a_configuration_that_does_not_exist_says_so():
    cog = _make_cog()
    interaction = _interaction()

    with _service(config_exists=AsyncMock(return_value=False)) as svc:
        await _remove(cog, interaction)

    assert "not found" in _replied(interaction)
    svc["remove_config"].assert_not_awaited()


async def test_a_configuration_nothing_depends_on_is_removed_without_asking():
    """With nothing attached there is nothing to lose, and nagging on every removal is how
    a confirmation stops being read."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(setup_seasons_linking=AsyncMock(return_value=[])) as svc:
        await _remove(cog, interaction)

    svc["remove_config"].assert_awaited_once()
    assert _view_of(interaction) is None
    assert "removed" in _replied(interaction)


async def test_a_configuration_a_season_is_built_on_asks_first():
    """Issue #132. The removal detaches it, so the season quietly stops being the season
    the manager built and is refused at approval."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(setup_seasons_linking=AsyncMock(return_value=[(1, 3)])) as svc:
        await _remove(cog, interaction)

    svc["remove_config"].assert_not_awaited()
    assert _view_of(interaction) is not None


async def test_the_question_names_the_season_at_risk():
    """Naming it is the difference between a deliberate teardown and a surprise found at
    the next `/season placements-review`."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(setup_seasons_linking=AsyncMock(return_value=[(1, 3)])):
        await _remove(cog, interaction)

    assert "Season #3" in _replied(interaction)


async def test_the_question_names_every_season_at_risk():
    """More than one season can be in setup across a server's history of attachments."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(setup_seasons_linking=AsyncMock(return_value=[(1, 3), (2, 4)])):
        await _remove(cog, interaction)

    replied = _replied(interaction)
    assert "Season #3" in replied
    assert "Season #4" in replied


async def test_the_question_says_what_removing_costs():
    """Every position of every session type, with no undo — a manager cannot weigh the
    decision without being told the price."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(setup_seasons_linking=AsyncMock(return_value=[(1, 3)])):
        await _remove(cog, interaction)

    replied = _replied(interaction)
    assert "no undo" in replied
    assert "refused for approval" in replied


# ---------------------------------------------------------------------------
# The confirmation
# ---------------------------------------------------------------------------


async def test_confirming_removes_the_configuration():
    cog = _make_cog()
    view = _ConfirmRemoveConfigView(cog, ACTOR_ID, CONFIG)
    interaction = _interaction()

    with _service() as svc:
        await type(view).confirm(view, interaction, MagicMock())

    svc["remove_config"].assert_awaited_once()
    assert "removed" in _replied(interaction)


async def test_cancelling_leaves_the_configuration_alone():
    cog = _make_cog()
    view = _ConfirmRemoveConfigView(cog, ACTOR_ID, CONFIG)
    interaction = _interaction()

    with _service() as svc:
        await type(view).cancel(view, interaction, MagicMock())

    svc["remove_config"].assert_not_awaited()
    assert "untouched" in _replied(interaction)


@pytest.mark.parametrize("button", ["confirm", "cancel"], ids=["confirm", "cancel"])
async def test_only_the_admin_who_asked_may_answer(button):
    """A second admin pressing another's confirmation would destroy a configuration they
    had not been asked about."""
    cog = _make_cog()
    view = _ConfirmRemoveConfigView(cog, ACTOR_ID, CONFIG)
    interaction = _interaction(user_id=OTHER_ADMIN)

    with _service() as svc:
        await getattr(type(view), button)(view, interaction, MagicMock())

    assert "Not your action" in _replied(interaction)
    svc["remove_config"].assert_not_awaited()


async def test_the_confirmation_button_says_what_it_does():
    """`Confirm` on its own is what somebody clicks through; the label names the outcome,
    and the style marks it as destructive."""
    import discord

    cog = _make_cog()
    view = _ConfirmRemoveConfigView(cog, ACTOR_ID, CONFIG)

    confirm = next(c for c in view.children if "Remove" in (c.label or ""))
    assert confirm.style is discord.ButtonStyle.danger


# ---------------------------------------------------------------------------
# One code path for the removal
# ---------------------------------------------------------------------------


async def test_both_routes_log_the_removal_identically():
    """`_apply_config_remove` is shared so the two cannot come to differ about what the
    log records — the league's account of a destroyed configuration must not depend on
    whether a confirmation happened to be shown."""
    straight_cog = _make_cog()
    straight = _interaction()
    with _service(setup_seasons_linking=AsyncMock(return_value=[])):
        await _remove(straight_cog, straight)

    confirmed_cog = _make_cog()
    view = _ConfirmRemoveConfigView(confirmed_cog, ACTOR_ID, CONFIG)
    confirmed = _interaction()
    with _service():
        await type(view).confirm(view, confirmed, MagicMock())

    assert (
        straight_cog.bot.output_router.post_log.await_args.args[0]
        == confirmed_cog.bot.output_router.post_log.await_args.args[0]
    )


async def test_a_removal_that_finds_nothing_is_reported_not_raised():
    """The configuration can go between the existence check and the removal — another
    admin removing it in the meantime must not raise at this one."""
    cog = _make_cog()
    interaction = _interaction()

    with _service(remove_config=AsyncMock(side_effect=ConfigNotFoundError(CONFIG))):
        await _remove(cog, interaction)

    assert "not found" in _replied(interaction)
    cog.bot.output_router.post_log.assert_not_awaited()
