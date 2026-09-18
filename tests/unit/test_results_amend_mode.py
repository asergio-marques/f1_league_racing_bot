"""Amendment mode — editing a running season's points without changing them yet.

Issue #208. A points configuration attached to a season is locked once that season is approved,
because the rounds already run were scored by it. Amendment mode is how a league changes it
anyway: edits go into a *modification store* rather than the live table, and are applied in one
reviewed step or thrown away.

**Disabling is refused while uncommitted changes exist.** The store holds edits nobody has
approved, and turning the mode off would discard them silently — a manager who spent an evening
retyping a points table would lose it to a command they thought was a toggle. The refusal names
both ways out: revert to discard deliberately, or review to apply.
`test_disabling_is_refused_while_changes_are_pending` is the one that matters here.

**Reverting is refused when the mode is not on.** There is no store to revert, and doing nothing
quietly would leave a manager believing they had discarded changes that were never staged.

**Both commands need a season.** The store hangs off one, and the points being amended are that
season's — so a server between seasons has nothing to amend.

The toggle is a single command doing two opposite things, which is why each direction is tested
for its own reply *and* its own log line: a league reading its log needs to see which way the
mode went, and the two entries are written separately.
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog  # noqa: E402
from services.amendment_service import (  # noqa: E402
    AmendmentModifiedError,
    AmendmentNotActiveError,
)
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 12108
SEASON_ID = 3
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(
    *,
    results_enabled: bool = True,
    season=SimpleNamespace(id=SEASON_ID, status="ACTIVE"),
) -> ResultsCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=results_enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(return_value=season)
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)

    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
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


def _state(active: bool | None):
    """`None` where no store exists at all, which is distinct from one that is off."""
    return None if active is None else SimpleNamespace(amendment_active=active)


@contextmanager
def _amendment(*, state=None, **overrides):
    mocks = {
        "get_amendment_state": AsyncMock(return_value=state),
        "enable_amendment_mode": AsyncMock(return_value=None),
        "disable_amendment_mode": AsyncMock(return_value=None),
        "revert_modification_store": AsyncMock(return_value=None),
    }
    mocks.update(overrides)
    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch(f"services.amendment_service.{name}", new=mock))
        yield mocks


async def _toggle(cog, interaction):
    await undecorate(ResultsCog.amend_toggle)(cog, interaction)


async def _revert(cog, interaction):
    await undecorate(ResultsCog.amend_revert)(cog, interaction)


def _logged(cog) -> str:
    return "\n".join(
        str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list
    )


# ---------------------------------------------------------------------------
# The module gate and the season
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", [_toggle, _revert], ids=["toggle", "revert"])
async def test_both_commands_are_refused_while_the_module_is_disabled(command):
    cog = _make_cog(results_enabled=False)
    interaction = _interaction()

    with _amendment() as svc:
        await command(cog, interaction)

    assert "not enabled" in _replied(interaction)
    svc["enable_amendment_mode"].assert_not_awaited()
    svc["revert_modification_store"].assert_not_awaited()


@pytest.mark.parametrize("command", [_toggle, _revert], ids=["toggle", "revert"])
async def test_both_commands_need_a_season(command):
    """The store hangs off one, and the points being amended are that season's."""
    cog = _make_cog(season=None)
    interaction = _interaction()

    with _amendment() as svc:
        await command(cog, interaction)

    assert "No active season" in _replied(interaction)
    svc["enable_amendment_mode"].assert_not_awaited()


# ---------------------------------------------------------------------------
# Turning it on
# ---------------------------------------------------------------------------


async def test_amendment_mode_is_enabled_when_it_was_off():
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(False)) as svc:
        await _toggle(cog, interaction)

    svc["enable_amendment_mode"].assert_awaited_once()
    assert "enabled" in _replied(interaction)


async def test_a_season_that_has_never_amended_starts_a_store():
    """No state row at all is the same as off — a league amending for the first time must
    not be refused for never having done it before."""
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(None)) as svc:
        await _toggle(cog, interaction)

    svc["enable_amendment_mode"].assert_awaited_once()


async def test_enabling_says_the_store_was_initialised():
    """The store is the thing the manager is about to type into, and a bare "enabled"
    would not tell them where their edits are going."""
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(False)):
        await _toggle(cog, interaction)

    assert "Modification store initialised" in _replied(interaction)


async def test_enabling_is_logged_as_enabled():
    """The toggle is one command doing two opposite things, so the log has to say which."""
    cog = _make_cog()

    with _amendment(state=_state(False)):
        await _toggle(cog, _interaction())

    assert "amendment_mode: enabled" in _logged(cog)


# ---------------------------------------------------------------------------
# Turning it off
# ---------------------------------------------------------------------------


async def test_amendment_mode_is_disabled_when_it_was_on():
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(True)) as svc:
        await _toggle(cog, interaction)

    svc["disable_amendment_mode"].assert_awaited_once()
    assert "disabled" in _replied(interaction)


async def test_disabling_is_logged_as_disabled():
    cog = _make_cog()

    with _amendment(state=_state(True)):
        await _toggle(cog, _interaction())

    assert "amendment_mode: disabled" in _logged(cog)


async def test_disabling_is_refused_while_changes_are_pending():
    """The store holds edits nobody has approved. Turning the mode off would discard them
    silently — a manager who spent an evening retyping a points table would lose it to a
    command they thought was a toggle."""
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(
        state=_state(True),
        disable_amendment_mode=AsyncMock(side_effect=AmendmentModifiedError("pending")),
    ):
        await _toggle(cog, interaction)

    assert "uncommitted changes exist" in _replied(interaction)


async def test_the_refusal_names_both_ways_out():
    """Discarding deliberately and applying are genuinely different decisions, and a
    manager blocked here needs to be told they have both."""
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(
        state=_state(True),
        disable_amendment_mode=AsyncMock(side_effect=AmendmentModifiedError("pending")),
    ):
        await _toggle(cog, interaction)

    replied = _replied(interaction)
    assert "/results amend revert" in replied
    assert "/results amend review" in replied


async def test_a_refused_disable_is_not_logged_as_a_success():
    """The mode is still on, and a log saying otherwise would have a league believe their
    staged edits were gone."""
    cog = _make_cog()

    with _amendment(
        state=_state(True),
        disable_amendment_mode=AsyncMock(side_effect=AmendmentModifiedError("pending")),
    ):
        await _toggle(cog, _interaction())

    assert "disabled" not in _logged(cog)


# ---------------------------------------------------------------------------
# Reverting
# ---------------------------------------------------------------------------


async def test_the_store_is_reverted_to_the_season_s_points():
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(True)) as svc:
        await _revert(cog, interaction)

    svc["revert_modification_store"].assert_awaited_once()
    assert "reverted" in _replied(interaction)


@pytest.mark.parametrize("active", [False, None], ids=["mode-off", "no-store"])
async def test_reverting_is_refused_when_the_mode_is_not_on(active):
    """There is no store to revert, and doing nothing quietly would leave a manager
    believing they had discarded changes that were never staged."""
    cog = _make_cog()
    interaction = _interaction()

    with _amendment(state=_state(active)) as svc:
        await _revert(cog, interaction)

    assert "not active" in _replied(interaction)
    svc["revert_modification_store"].assert_not_awaited()


async def test_a_refused_revert_is_not_logged():
    cog = _make_cog()

    with _amendment(state=_state(False)):
        await _revert(cog, _interaction())

    cog.bot.output_router.post_log.assert_not_awaited()


async def test_a_successful_revert_is_logged():
    cog = _make_cog()

    with _amendment(state=_state(True)):
        await _revert(cog, _interaction())

    assert "amend revert" in _logged(cog)
