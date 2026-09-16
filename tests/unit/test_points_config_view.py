"""Reading a points configuration back, before a season starts and after it has.

Issue #208. `/results config view` was uncovered, and it is the one command that answers "what
will this round actually be worth?" — which is what a league argues about.

**It reads from two different places, and which one depends on the season's status.** A season
in SETUP is still attached to server-level configurations that can be edited; once it is ACTIVE
the points are snapshotted onto the season, and the server-level store may since have been
changed for *next* season. Reading the wrong one would show a manager points their league is not
racing for. `test_an_active_season_reads_its_own_snapshot` and
`test_a_setup_season_reads_the_server_store` are the pair that holds it, and the two paths do
not otherwise resemble each other — they even report "not found" differently, because in one
case the configuration is missing from the *season* and in the other from the *server*.

**Trailing zeros are collapsed into a sentinel on the setup path.** A configuration stores a
row for every scoring position, and a table listing "P11: 0, P12: 0 …" down to P20 buries the
positions that score — so they become one `"11+"` row worth nothing. Collapsed rather than
dropped, because "everything from P11 down scores nothing" is a statement a league wants and
an absence is not. Positions come back as *strings* for that reason, since `"11+"` is not a
number. The active path gets the same treatment from the season view; the setup path does it
here.

**The session filter is applied after the read, not inside it.** A manager asking about the
feature race gets the feature race, and only that — a table of all four sessions when one was
asked for is the same information a manager was trying to narrow down.

**The four sessions are labelled as a league says them.** `FEATURE_RACE` is how the database
spells it; "Feature Race" is what a manager reads. Anything unrecognised falls back to the raw
value rather than being dropped, so a session type added to the model shows up unlabelled rather
than invisibly missing.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.results_cog import ResultsCog  # noqa: E402
from models.points_config import (  # noqa: E402
    PointsConfigEntry,
    PointsConfigFastestLap,
    SessionType,
)
from services.points_config_service import ConfigNotFoundError  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 12008
SEASON_ID = 1
CONFIG = "Standard"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cog(*, enabled: bool = True, season_status: str | None = "ACTIVE") -> ResultsCog:
    bot = MagicMock()
    bot.db_path = "/tmp/not-read.db"
    bot.module_service = MagicMock()
    bot.module_service.is_results_enabled = AsyncMock(return_value=enabled)
    bot.season_service = MagicMock()
    bot.season_service.get_season_for_server = AsyncMock(
        return_value=None
        if season_status is None
        else SimpleNamespace(id=SEASON_ID, status=season_status)
    )
    cog = ResultsCog.__new__(ResultsCog)
    cog.bot = bot
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user = MagicMock()
    interaction.user.id = 77
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


def _choice(session_type: SessionType | None):
    if session_type is None:
        return None
    return SimpleNamespace(name=session_type.value, value=session_type.value)


def _entry(session_type: SessionType, position: int, points: int):
    return PointsConfigEntry(
        id=0, config_id=1, session_type=session_type, position=position, points=points
    )


def _fl(session_type: SessionType, points: int = 1, limit: int | None = None):
    return PointsConfigFastestLap(
        id=0,
        config_id=1,
        session_type=session_type,
        fl_points=points,
        fl_position_limit=limit,
    )


async def _view(
    cog,
    interaction,
    *,
    session=None,
    name=CONFIG,
    season_view=None,
    store=None,
    store_error=None,
):
    """Run the command with both read paths stubbed, returning what was formatted."""
    captured: dict = {}

    def _format(config_name, entries_by_session, fl_by_session):
        captured["name"] = config_name
        captured["entries"] = entries_by_session
        captured["fl"] = fl_by_session
        return "formatted table"

    with patch(
        "services.season_points_service.get_season_points_view",
        new=AsyncMock(return_value=season_view if season_view is not None else {}),
    ), patch(
        "services.points_config_service.get_config_entries",
        new=AsyncMock(
            return_value=store if store is not None else ([], []),
            side_effect=store_error,
        ),
    ), patch("utils.results_formatter.format_config_view", new=_format):
        await undecorate(ResultsCog.config_view)(cog, interaction, name, _choice(session))
    return captured


# ---------------------------------------------------------------------------
# Which store is read
# ---------------------------------------------------------------------------


async def test_an_active_season_reads_its_own_snapshot():
    """Once a season is ACTIVE the points are snapshotted onto it, and the server-level
    store may since have been edited for *next* season."""
    cog = _make_cog(season_status="ACTIVE")
    interaction = _interaction()

    with patch(
        "services.season_points_service.get_season_points_view",
        new=AsyncMock(return_value={"FEATURE_RACE": {"entries": [(1, 25)], "fl": None}}),
    ) as season_view, patch(
        "services.points_config_service.get_config_entries", new=AsyncMock()
    ) as store, patch(
        "utils.results_formatter.format_config_view", new=MagicMock(return_value="x")
    ):
        await undecorate(ResultsCog.config_view)(cog, interaction, CONFIG, None)

    season_view.assert_awaited_once()
    store.assert_not_awaited()


@pytest.mark.parametrize("status", ["SETUP", "COMPLETED", "CANCELLED"])
async def test_a_season_not_yet_active_reads_the_server_store(status):
    """A season in setup is still attached to configurations that can be edited, and the
    snapshot does not exist yet."""
    cog = _make_cog(season_status=status)
    interaction = _interaction()

    with patch(
        "services.season_points_service.get_season_points_view", new=AsyncMock()
    ) as season_view, patch(
        "services.points_config_service.get_config_entries",
        new=AsyncMock(return_value=([_entry(SessionType.FEATURE_RACE, 1, 25)], [])),
    ) as store, patch(
        "utils.results_formatter.format_config_view", new=MagicMock(return_value="x")
    ):
        await undecorate(ResultsCog.config_view)(cog, interaction, CONFIG, None)

    store.assert_awaited_once()
    season_view.assert_not_awaited()


async def test_a_server_with_no_season_is_refused():
    """There is nothing to view a configuration *for*, and the command would otherwise read
    a season id off `None`."""
    cog = _make_cog(season_status=None)
    interaction = _interaction()

    await _view(cog, interaction)

    assert "No active or setup season" in _replied(interaction)


# ---------------------------------------------------------------------------
# The active path
# ---------------------------------------------------------------------------


async def test_the_seasons_points_are_shown_by_session():
    cog = _make_cog()
    interaction = _interaction()

    captured = await _view(
        cog,
        interaction,
        season_view={
            "FEATURE_RACE": {"entries": [(1, 25), (2, 18)], "fl": None},
            "SPRINT_RACE": {"entries": [(1, 8)], "fl": None},
        },
    )

    assert captured["entries"]["Feature Race"] == [(1, 25), (2, 18)]
    assert captured["entries"]["Sprint Race"] == [(1, 8)]


async def test_a_fastest_lap_bonus_is_shown_where_the_season_has_one():
    cog = _make_cog()

    captured = await _view(
        cog,
        _interaction(),
        season_view={"FEATURE_RACE": {"entries": [(1, 25)], "fl": (1, 10)}},
    )

    assert captured["fl"]["Feature Race"] == (1, 10)


async def test_a_session_with_no_bonus_is_not_given_one():
    """`None` is "this session has no fastest-lap bonus", and rendering it as a row would
    tell a league it scores something it does not."""
    cog = _make_cog()

    captured = await _view(
        cog, _interaction(), season_view={"FEATURE_RACE": {"entries": [(1, 25)], "fl": None}}
    )

    assert captured["fl"] == {}


async def test_a_config_not_attached_to_the_season_says_so():
    """Distinct from one that does not exist at all — this configuration may be perfectly
    real and simply not the one this season is racing under."""
    cog = _make_cog()
    interaction = _interaction()

    await _view(cog, interaction, season_view={})

    replied = _replied(interaction)
    assert CONFIG in replied
    assert "not found in the current season" in replied


async def test_an_unrecognised_session_key_is_shown_rather_than_dropped():
    """A session type added to the model shows up unlabelled rather than invisibly
    missing — which is how a league comes to race a session nobody can see the points for."""
    cog = _make_cog()

    captured = await _view(
        cog, _interaction(), season_view={"ENDURANCE_RACE": {"entries": [(1, 40)], "fl": None}}
    )

    assert "ENDURANCE_RACE" in captured["entries"]


# ---------------------------------------------------------------------------
# The setup path
# ---------------------------------------------------------------------------


async def test_the_server_stores_points_are_shown_by_session():
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        store=(
            [
                _entry(SessionType.FEATURE_RACE, 1, 25),
                _entry(SessionType.FEATURE_RACE, 2, 18),
                _entry(SessionType.SPRINT_RACE, 1, 8),
            ],
            [],
        ),
    )

    assert captured["entries"]["Feature Race"] == [("1", 25), ("2", 18)]
    assert captured["entries"]["Sprint Race"] == [("1", 8)]


async def test_the_positions_are_ordered(tmp_path):
    """Stored order is whatever the database hands back; a table that runs P3, P1, P2 is
    not a points table."""
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        store=(
            [
                _entry(SessionType.FEATURE_RACE, 3, 15),
                _entry(SessionType.FEATURE_RACE, 1, 25),
                _entry(SessionType.FEATURE_RACE, 2, 18),
            ],
            [],
        ),
    )

    assert [p for p, _ in captured["entries"]["Feature Race"]] == ["1", "2", "3"]


async def test_trailing_zeros_are_collapsed_into_a_sentinel():
    """A configuration stores a row for every scoring position; a table listing "P11: 0,
    P12: 0 …" down to P20 buries the positions that actually score. Collapsed rather than
    dropped, because "everything from here down scores nothing" is a statement a league
    wants and an absence is not."""
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        store=(
            [
                _entry(SessionType.FEATURE_RACE, 1, 25),
                _entry(SessionType.FEATURE_RACE, 2, 18),
                _entry(SessionType.FEATURE_RACE, 3, 0),
                _entry(SessionType.FEATURE_RACE, 4, 0),
            ],
            [],
        ),
    )

    assert captured["entries"]["Feature Race"] == [("1", 25), ("2", 18), ("3+", 0)]


async def test_a_session_with_no_entries_is_omitted():
    """A league that scores only the two races should not be shown two empty qualifying
    tables."""
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog, _interaction(), store=([_entry(SessionType.FEATURE_RACE, 1, 25)], [])
    )

    assert list(captured["entries"]) == ["Feature Race"]


async def test_a_fastest_lap_row_is_shown_with_its_limit():
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        store=(
            [_entry(SessionType.FEATURE_RACE, 1, 25)],
            [_fl(SessionType.FEATURE_RACE, 1, 10)],
        ),
    )

    assert captured["fl"]["Feature Race"] == (1, 10)


async def test_a_config_that_does_not_exist_says_so():
    """The other "not found": missing from the *server*, not merely unattached to the
    season. The fix is `/results config add`, not `/results config append`."""
    cog = _make_cog(season_status="SETUP")
    interaction = _interaction()

    await _view(cog, interaction, store_error=ConfigNotFoundError("nope"))

    replied = _replied(interaction)
    assert CONFIG in replied
    assert "not found" in replied
    assert "in the current season" not in replied


# ---------------------------------------------------------------------------
# Narrowing to one session
# ---------------------------------------------------------------------------


async def test_a_session_filter_reaches_the_season_read():
    """Applied inside the read on the active path, so the season view returns only what was
    asked for."""
    cog = _make_cog()
    interaction = _interaction()

    with patch(
        "services.season_points_service.get_season_points_view",
        new=AsyncMock(return_value={"FEATURE_RACE": {"entries": [(1, 25)], "fl": None}}),
    ) as season_view, patch(
        "utils.results_formatter.format_config_view", new=MagicMock(return_value="x")
    ):
        await undecorate(ResultsCog.config_view)(
            cog, interaction, CONFIG, _choice(SessionType.FEATURE_RACE)
        )

    assert season_view.await_args.args[3] == SessionType.FEATURE_RACE


async def test_a_session_filter_narrows_the_setup_read():
    """Applied after the read here, because the server store returns everything — a table
    of all four sessions when one was asked for is the information a manager was trying to
    narrow down."""
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        session=SessionType.FEATURE_RACE,
        store=(
            [
                _entry(SessionType.FEATURE_RACE, 1, 25),
                _entry(SessionType.SPRINT_RACE, 1, 8),
            ],
            [],
        ),
    )

    assert list(captured["entries"]) == ["Feature Race"]


async def test_a_session_filter_narrows_the_fastest_lap_rows_too():
    """Otherwise a manager asking about qualifying is shown the race's bonus beside it."""
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        session=SessionType.FEATURE_RACE,
        store=(
            [_entry(SessionType.FEATURE_RACE, 1, 25)],
            [_fl(SessionType.FEATURE_RACE, 1), _fl(SessionType.SPRINT_RACE, 1)],
        ),
    )

    assert list(captured["fl"]) == ["Feature Race"]


async def test_no_filter_shows_every_session():
    cog = _make_cog(season_status="SETUP")

    captured = await _view(
        cog,
        _interaction(),
        store=(
            [
                _entry(SessionType.FEATURE_RACE, 1, 25),
                _entry(SessionType.SPRINT_RACE, 1, 8),
            ],
            [],
        ),
    )

    assert set(captured["entries"]) == {"Feature Race", "Sprint Race"}


# ---------------------------------------------------------------------------
# Around the edges
# ---------------------------------------------------------------------------


async def test_the_configs_name_is_carried_into_the_table():
    """A manager viewing two configurations in a row needs each table to say which it is."""
    cog = _make_cog()

    captured = await _view(
        cog,
        _interaction(),
        name="Sprint points",
        season_view={"FEATURE_RACE": {"entries": [(1, 25)], "fl": None}},
    )

    assert captured["name"] == "Sprint points"


async def test_the_command_is_refused_while_the_module_is_off():
    cog = _make_cog(enabled=False)
    interaction = _interaction()

    await _view(cog, interaction)

    interaction.response.defer.assert_not_awaited()


async def test_the_command_defers_before_reading():
    """Two database reads and a format; it answers through `followup` throughout."""
    cog = _make_cog()
    interaction = _interaction()

    await _view(
        cog, interaction, season_view={"FEATURE_RACE": {"entries": [(1, 25)], "fl": None}}
    )

    interaction.response.defer.assert_awaited_once()
    interaction.followup.send.assert_awaited()
