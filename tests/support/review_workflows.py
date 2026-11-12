"""Running the season review's workflows as a review's button runs them.

The approval, the mid-season placements confirmation and the configuration confirmation are
functions of `leaguebot.core.services.seasons.season_review_workflows`: `approve_season`,
`confirm_mid_season_placements` and `confirm_configuration`. Each takes the interaction pressed,
then as keyword arguments the services it uses, each named as the bot carries it (`season_service`,
`change_queue`, `db_path` and so on), the clock as `now`, the queue's (`change_queue.now`), and the
press's own particulars (`deadline`, `what`, `fingerprint`), which have defaults. The review's views
hand them their services off the bot by those names, and so does `run_workflow`, so a test never
lists which services a workflow reads. The approval's backup question is
`offer_backup_before_approving`, run the same way, and its view `BackupBeforeApprovalView`, built by
`build_view` the same way, its `timeout` given.

The gates the workflows run are functions of `leaguebot.core.services.seasons.approval_checks`, called
through the module, so that a test standing one in patches `approval_checks.<gate>`. A gate that
reads several of the league's services, as `configuration_faults` does, takes them as keyword
arguments named as the bot carries them, and `run_check` hands them over as `run_workflow` does.

A review command builds no view of its question: the review's change has the builder's factory
make it. `record_review_views` records any view of a review's question built, so a test can say
none was.
"""
from __future__ import annotations

import inspect
from typing import Any


def sign_up_confirmation_refused(bot: Any) -> None:
    """Give *bot* the hook a season's confirmation is refused through (`confirmation_refused`),
    results' refusals signed up to it as its wiring signs them: handed *bot*'s module service and
    database, then what the hook is called with. Results' function is looked up as the hook is
    called, so a stand-in bot carries the hook whether or not any workflow asks it yet. It mirrors
    results' wiring's `sign_up_to_confirmation_refused`."""
    from leaguebot.core.utils.hooks import AwaitedHook

    async def results_refusals(*args: Any, **kwargs: Any) -> Any:
        from leaguebot.results.services.points import season_points_service

        return await season_points_service.confirmation_refusals(
            bot.module_service, bot.db_path, *args, **kwargs
        )

    bot.confirmation_refused = AwaitedHook()
    bot.confirmation_refused.subscribe(results_refusals)


def sign_up_driver_names_read(bot: Any) -> None:
    """Give *bot* the hook a driver's name is read through (`driver_names_read`), signup's names
    signed up as its wiring signs them: signup's module service over *bot*'s database, then the
    drivers' profile ids. Signup's function is looked up as the hook is called, so a stand-in bot
    carries the hook whether or not any check asks it yet. It mirrors signup's wiring's
    `sign_up_to_driver_names_read`."""
    from leaguebot.core.utils.hooks import AwaitedHook

    async def signup_names(*args: Any, **kwargs: Any) -> Any:
        from leaguebot.signup.services import signup_module_service

        return await signup_module_service.driver_names(
            signup_module_service.SignupModuleService(bot.db_path), *args, **kwargs
        )

    bot.driver_names_read = AwaitedHook()
    bot.driver_names_read.subscribe(signup_names)


def sign_up_division_channel_faults_read(bot: Any) -> None:
    """Give *bot* the hook a division's channel faults are read through
    (`division_channel_faults_read`), each module's faults signed up as the builder signs them, in
    the order a division's lines are read: weather's forecast channel, results' three channels,
    then attendance's two. Weather and results are handed *bot*'s module service and database,
    attendance its service over that database and the module service; then each is handed the
    divisions judged and the server. Each module's function is looked up as the hook is called, so
    a stand-in bot carries the hook whether or not any check asks it yet. It mirrors the
    builder's `_sign_up_to_division_channel_faults_read`."""
    from leaguebot.core.utils.hooks import AwaitedHook

    async def weather_faults(*args: Any, **kwargs: Any) -> Any:
        from leaguebot.weather.services import forecast_channel

        return await forecast_channel.division_channel_faults(
            bot.module_service, bot.db_path, *args, **kwargs
        )

    async def results_faults(*args: Any, **kwargs: Any) -> Any:
        from leaguebot.results.services import division_channels

        return await division_channels.division_channel_faults(
            bot.module_service, bot.db_path, *args, **kwargs
        )

    async def attendance_faults(*args: Any, **kwargs: Any) -> Any:
        from leaguebot.attendance.services import attendance_service

        return await attendance_service.division_channel_faults(
            attendance_service.AttendanceService(bot.db_path), bot.module_service,
            *args, **kwargs,
        )

    bot.division_channel_faults_read = AwaitedHook()
    for subscriber in (weather_faults, results_faults, attendance_faults):
        bot.division_channel_faults_read.subscribe(subscriber)


def sign_up_division_lines_read(bot: Any) -> None:
    """Give *bot* the hook the placements review gathers each division's module lines through
    (`division_lines_read`), made by the builder's own `_sign_up_to_division_lines_read`, so that
    the modules signed up to it, and the order the review lays their lines out in, are the bot's
    and not a copy."""
    from leaguebot.__main__ import _sign_up_to_division_lines_read

    _sign_up_to_division_lines_read(bot)


def _services(target: Any, bot: Any, given: dict[str, Any]) -> dict[str, Any]:
    """Every keyword-only parameter of *target* that has no default and is not in *given*, each
    taken off *bot* by its name, but `now`, which is the queue's clock as the views hand it."""
    return {
        param: bot.change_queue.now if param == "now" else getattr(bot, param)
        for param, spec in inspect.signature(target).parameters.items()
        if spec.kind is inspect.Parameter.KEYWORD_ONLY
        and spec.default is inspect.Parameter.empty
        and param not in given
    }


async def run_workflow(name: str, bot: Any, interaction: Any, **given: Any) -> Any:
    """Run the workflow *name* on *interaction*, handing it every service it asks for off *bot*
    by name, and *given* as the press's own particulars."""
    from leaguebot.core.services.seasons import season_review_workflows

    workflow = getattr(season_review_workflows, name)
    return await workflow(interaction, **_services(workflow, bot, given), **given)


def build_view(name: str, bot: Any, **given: Any) -> Any:
    """Build the view *name* of `season_review_workflows`, handing it every service it asks for
    off *bot* by name, and *given*."""
    from leaguebot.core.services.seasons import season_review_workflows

    view = getattr(season_review_workflows, name)
    return view(**_services(view, bot, given), **given)


async def take_season_fingerprint(bot: Any, db_path: str, season_id: int) -> Any:
    """The fingerprint of season *season_id* in the database at *db_path*, taken as a review's post
    takes it: handed the image module's drawing (`bot.image_drawing`), and every other service
    `take_fingerprint` asks for off *bot* by name, as `run_workflow` hands a workflow its own, so
    that a test never lists which readers the fingerprint asks."""
    from leaguebot.core.services.seasons import season_fingerprint_service

    take = season_fingerprint_service.take_fingerprint
    given = {"drawing": bot.image_drawing}
    return await take(db_path, season_id, **_services(take, bot, given), **given)


async def run_check(name: str, bot: Any, *args: Any, **given: Any) -> Any:
    """Run the gate *name* of `approval_checks` on *args*, handing it every service it asks for
    off *bot* by name, and *given*."""
    from leaguebot.core.services.seasons import approval_checks

    check = getattr(approval_checks, name)
    return await check(*args, **_services(check, bot, given), **given)


#: Each family of review question the builder's factory makes a view for.
REVIEW_FAMILIES = ("approve", "mid_season", "configuration")


def record_review_views(monkeypatch: Any) -> list[Any]:
    """Record every view of a review's question built while *monkeypatch* holds, and return the
    list they are recorded in.

    The view classes are found by building one of each family through the builder's factory
    (`_approve_view_for`) on a bot that is a mock, so a test reads no module the views live in;
    each class's constructor is then wrapped to record the view and build it as before. A view
    of a class deriving from another is recorded once."""
    from unittest.mock import MagicMock

    from leaguebot.__main__ import _approve_view_for

    made: list[Any] = []
    make = _approve_view_for(MagicMock())
    classes = [type(make({"view": family, "reviewer_id": 1})) for family in REVIEW_FAMILIES]
    for cls in classes:
        original = cls.__init__

        def __init__(self: Any, *args: Any, __original: Any = original, **kwargs: Any) -> None:
            if not any(view is self for view in made):
                made.append(self)
            __original(self, *args, **kwargs)

        monkeypatch.setattr(cls, "__init__", __init__)
    return made
