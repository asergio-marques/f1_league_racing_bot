"""A refusal is caught by its name, and a fault is never shown to a member as one.

architecture.md, "Errors and failures": a command catches only the errors it expects by name, and
only to turn them into a refusal the member can act on. A member's refusal is raised as `Refused`;
a `ValueError` that is not one is what Python raises for a bug (`int("x")`, a bad enum value), and
a catch that took it for a refusal would answer a fault with its text as if the member had asked
for something the league does not allow.

Each case here raises a bare `ValueError` where a bug would raise it, inside the call a catch
guards, and asserts it goes on as a fault: raised out of the check or the service, not turned
into a refusal. The refusals themselves, in their words, are each service's own tests'.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import run_migrations

#: The fault each case raises, as `int()` words one.
BUG = "invalid literal for int() with base 10: 'x'"

#: Why each case fails until #283's build catches refusals by name.
CAUGHT_AS_A_REFUSAL = "#283: a catch still takes any ValueError for a member's refusal"

#: A placement check's payload, every id a number, as the cog asks it.
_PAYLOAD = {
    "season_id": 1, "division_id": 11, "division": "Pro", "from_division_id": 11,
    "from_division": "Pro", "to_division_id": 12, "to_division": "Am", "driver_profile_id": 31,
    "discord_user_id": "4242", "driver_name": "Lewis", "team_name": "Ferrari",
    "team_full_name": "Scuderia Ferrari",
}


def _faulty_placement() -> MagicMock:
    """A placement service whose every guard and judge raises a bug's `ValueError`."""
    placement = MagicMock()
    for name in (
        "guard_move", "guard_assign", "guard_roster_capacity", "judge_move_on", "judge_sack_on",
        "judge_release_on", "judge_assign_on", "judge_unassign_on",
    ):
        setattr(placement, name, AsyncMock(side_effect=ValueError(BUG)))
    return placement


def _placement_check(factory: str) -> Callable[..., Awaitable[None]]:
    """The check of `driver_changes.<factory>`'s change, every refusal read from the database
    standing aside, judged with a placement service that raises a bug's `ValueError`."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.models.change import ChangeOrigin
        from leaguebot.core.services.change_queue import CheckContext
        from leaguebot.core.services import driver_changes

        for refusal in ("ongoing_refusal", "placing_refusal", "division_refusal",
                        "profile_refusal"):
            monkeypatch.setattr(driver_changes, refusal, AsyncMock(return_value=None))
        db_path = str(tmp_path / "check.db")
        await run_migrations(db_path)
        change = getattr(driver_changes, factory)(
            placement=_faulty_placement(),
            now=lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
        )
        await change.check(CheckContext(
            payload=dict(_PAYLOAD), bot=MagicMock(), db_path=db_path,
            origin=ChangeOrigin.MEMBER,
        ))

    return run


async def _test_driver_added(tmp_path, monkeypatch) -> None:
    """`/test-mode driver add` of a driver to Team 1 in Alpha, the capacity guard raising."""
    from leaguebot.core.services.test_roster_service import add_test_driver
    from tests.core.test_test_roster_capacity_guards import TEAMS, _seed

    path, _divisions = await _seed(tmp_path)
    await add_test_driver(
        driver_name="Mock", team_name=TEAMS[0], division_name="Alpha", db_path=path,
        placement_service=_faulty_placement(),
    )


async def _test_roster_imported(tmp_path, monkeypatch) -> None:
    """A test roster of one driver for Team 1 in Alpha imported, the capacity guard raising."""
    from leaguebot.core.services.test_roster_service import add_test_drivers_in_bulk
    from tests.core.test_test_roster_capacity_guards import TEAMS, _roster, _seed

    path, _divisions = await _seed(tmp_path)
    await add_test_drivers_in_bulk(
        _roster((TEAMS[0], "Alpha")), path, placement_service=_faulty_placement(),
    )


#: Ferrari, as the server's team list holds it, with no role mapped.
_FERRARI = {"name": "Ferrari", "full_name": "Scuderia Ferrari", "max_seats": 2,
            "is_reserve": False, "role_id": None}


def _faulty_teams() -> MagicMock:
    """A team service holding Ferrari, whose every judge raises a bug's `ValueError`."""
    teams = MagicMock()
    teams.server_team_on = AsyncMock(return_value=dict(_FERRARI))
    teams.teams_with_roles_on = AsyncMock(return_value=[dict(_FERRARI)])
    for name in ("judge_add_on", "judge_modify_on", "judge_remove_on"):
        setattr(teams, name, AsyncMock(side_effect=ValueError(BUG)))
    return teams


def _team_placement() -> MagicMock:
    """A placement service for which no team holds a role, and every role write goes through."""
    placement = MagicMock()
    placement.team_holding_role = AsyncMock(return_value=None)
    placement.set_team_role_config_on = AsyncMock(return_value=None)
    placement.delete_team_role_config_on = AsyncMock(return_value=None)
    placement.seated_user_ids_on = AsyncMock(return_value=[])
    return placement


def _team_bot() -> MagicMock:
    """A bot with no league server set up, so a role is judged by its holder alone."""
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=None)
    return bot


def _team_change(factory: str):
    """`team_changes.<factory>`'s change, made with the doubles above and the queue's clock."""
    from leaguebot.core.services import team_changes

    made = getattr(team_changes, factory)
    kwargs = {"placement": _team_placement()}
    if factory != "team_reserve_role_change":
        kwargs["teams"] = _faulty_teams()
    if factory != "team_remove_change":
        kwargs["now"] = lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    return made(**kwargs)


def _team_check(factory: str, payload: dict) -> Callable[..., Awaitable[None]]:
    """The check of `team_changes.<factory>`'s change asked with *payload*, no season live, judged
    with a team service that raises a bug's `ValueError`."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.models.change import ChangeOrigin
        from leaguebot.core.services.change_queue import CheckContext

        db_path = str(tmp_path / "check.db")
        await run_migrations(db_path)
        await _team_change(factory).check(CheckContext(
            payload=dict(payload), bot=_team_bot(), db_path=db_path,
            origin=ChangeOrigin.MEMBER,
        ))

    return run


def _team_save(factory: str, payload: dict) -> Callable[..., Awaitable[None]]:
    """The save of `team_changes.<factory>`'s change asked by Admin with *payload*, no season
    live, run with a team service that raises a bug's `ValueError`."""

    async def run(tmp_path, monkeypatch) -> None:
        from leaguebot.core.db.database import get_connection
        from leaguebot.core.services.change_queue import StepContext
        from leaguebot.core.services.team_changes import SAVE

        db_path = str(tmp_path / "save.db")
        await run_migrations(db_path)
        change = _team_change(factory)
        async with get_connection(db_path) as db:
            await change.steps[SAVE].run(db, StepContext(
                bot=_team_bot(), db_path=db_path, change_id=1, kind=change.kind,
                payload=dict(payload), what="the change", actor_id=77, actor_name="Admin",
                named="Admin (<@77>)", steps=(), step_name=SAVE,
            ))

    return run


#: `/team add` of Alpine, "Alpine Racing", with role 813.
_TEAM_ADDED = {"shorthand": "Alpine", "full_name": "Alpine Racing", "role_id": 813,
               "role_name": "Alpine"}
#: `/team modify` of Ferrari to "Ferrari F1" with its role left as it is.
_TEAM_RENAMED = {"was_shorthand": "Ferrari", "was_full_name": "Scuderia Ferrari",
                 "shorthand": "Ferrari", "full_name": "Ferrari F1", "role_id": None,
                 "role_name": None}
#: `/team modify` of Ferrari's role, the role id stored as no number: a bug's `int()` refuses it.
_TEAM_ROLE_GARBLED = {"was_shorthand": "Ferrari", "was_full_name": "Scuderia Ferrari",
                      "shorthand": "Ferrari", "full_name": "Scuderia Ferrari", "role_id": "x",
                      "role_name": "Ferrari"}
#: `/team remove` of Ferrari.
_TEAM_REMOVED = {"shorthand": "Ferrari", "full_name": "Scuderia Ferrari"}
#: `/team reserve-role`, the role id stored as no number: a bug's `int()` refuses it.
_RESERVE_ROLE_GARBLED = {"role_id": "x", "role_name": "Reserve"}


def _reassign_change(driver, profiles_merged=None):
    """`driver_changes.driver_reassign_change`'s change, judged and saved through *driver*, a
    merge calling *profiles_merged*, and every Discord job a double."""
    from leaguebot.core.services.driver_changes import driver_reassign_change
    from leaguebot.core.utils.hooks import AwaitedHook

    return driver_reassign_change(
        driver=driver, placement=MagicMock(), drawing=MagicMock(),
        profiles_merged=profiles_merged or AwaitedHook(),
        driver_account_changed=AwaitedHook(), driver_account_changed_steps=MagicMock(),
        now=lambda: datetime(2026, 9, 17, 12, tzinfo=timezone.utc),
    )


async def _reassign_checked(tmp_path, monkeypatch) -> None:
    """`/driver reassign` of 6501 to 6502 checked, the service's judge raising."""
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.change_queue import CheckContext

    db_path = str(tmp_path / "check.db")
    await run_migrations(db_path)
    driver = MagicMock()
    driver.judge_reassign_on = AsyncMock(side_effect=ValueError(BUG))
    await _reassign_change(driver).check(CheckContext(
        payload={"old_user_id": "6501", "new_user_id": "6502", "new_name": "Lewis"},
        bot=MagicMock(), db_path=db_path, origin=ChangeOrigin.MEMBER,
    ))


async def _reassign_merge_hook_faulted(tmp_path, monkeypatch) -> None:
    """`/driver reassign` of 6501 to 6502 saved, two drivers neither of whom holds the live
    season and who share no division, so the two are merged: a module's part of the merge, called
    through `profiles_merged`, raises."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services.change_queue import StepContext
    from leaguebot.core.services.driver_changes import SAVE
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.utils.hooks import AwaitedHook
    from tests.core.test_driver_merge import A, B, _make_db, _profile

    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _profile(db_path, B)
    hook = AwaitedHook()
    hook.subscribe(AsyncMock(side_effect=ValueError(BUG)))
    change = _reassign_change(DriverService(db_path), hook)
    async with get_connection(db_path) as db:
        await change.steps[SAVE].run(db, StepContext(
            bot=MagicMock(), db_path=db_path, change_id=1, kind=change.kind,
            payload={"old_user_id": A, "new_user_id": B, "new_name": "Lewis"},
            what="the change", actor_id=77, actor_name="Admin", named="Admin (<@77>)",
            steps=(), step_name=SAVE,
        ))


async def _former_driver_set(tmp_path, monkeypatch) -> None:
    """`/test-mode set-former-driver` of Racer in test mode, the service raising."""
    from tests.core.test_test_mode_cog_refusals import _cog, _command, _interaction, _member

    cog = _cog()
    cog.bot.driver_service.set_former_driver = AsyncMock(side_effect=ValueError(BUG))
    await _command("set_former_driver", _member(), True)(
        cog, _interaction(cog, "test-mode set-former-driver")
    )


async def _tiers_checked(tmp_path, monkeypatch) -> None:
    """✅ Approve pressed on season 11's placements review, the tier check raising."""
    from leaguebot.core.db.database import get_connection
    from tests.core.test_do_approve_gates import (
        SEASON_ID, SERVER_ID, _cog, _interaction, _run,
    )

    db_path = str(tmp_path / "approve.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SEASON_ID,),
        )
        await db.commit()
    cog = _cog(db_path)
    cog.bot.season_service.validate_division_tiers = AsyncMock(side_effect=ValueError(BUG))
    await _run(cog, _interaction())


#: One case per catch: what it guards, and how to raise a bug's `ValueError` inside it.
CATCHES = [
    pytest.param(_placement_check("driver_move_change"), id="driver move check"),
    pytest.param(_placement_check("driver_sack_change"), id="driver sack check"),
    pytest.param(_placement_check("driver_release_change"), id="driver release check"),
    pytest.param(_placement_check("driver_assign_change"), id="driver assign check"),
    pytest.param(_placement_check("driver_unassign_change"), id="driver unassign check"),
    pytest.param(_test_driver_added, id="test driver added"),
    pytest.param(_test_roster_imported, id="test roster imported"),
    pytest.param(_team_check("team_add_change", _TEAM_ADDED), id="team add check"),
    pytest.param(_team_save("team_add_change", _TEAM_ADDED), id="team add save"),
    pytest.param(_team_check("team_remove_change", _TEAM_REMOVED), id="team remove check"),
    pytest.param(_team_save("team_remove_change", _TEAM_REMOVED), id="team remove save"),
    pytest.param(_team_check("team_modify_change", _TEAM_RENAMED), id="team modify check"),
    pytest.param(_team_save("team_modify_change", _TEAM_ROLE_GARBLED),
                 id="team modify save, a role id no number"),
    pytest.param(_team_save("team_reserve_role_change", _RESERVE_ROLE_GARBLED),
                 id="team reserve-role save, a role id no number"),
    pytest.param(_reassign_checked, id="driver reassign check"),
    pytest.param(_reassign_merge_hook_faulted, id="driver reassign save, a merge hook's fault"),
    pytest.param(_former_driver_set, id="test-mode set-former-driver"),
    pytest.param(_tiers_checked, id="season approval's tier check",
                 marks=pytest.mark.xfail(strict=True, reason=CAUGHT_AS_A_REFUSAL)),
]


@pytest.mark.parametrize("provoke", CATCHES)
async def test_an_unexpected_value_error_is_a_failure_not_a_refusal(
    tmp_path, monkeypatch, provoke,
):
    """A bug's `ValueError` raised inside a refusal catch goes on as a fault, in its own words,
    rather than being answered to the member as a refusal."""
    with pytest.raises(ValueError, match="invalid literal") as raised:
        await provoke(tmp_path, monkeypatch)
    assert type(raised.value) is ValueError
