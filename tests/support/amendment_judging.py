"""An amendment judged as core judges it with both modules signed up (#287).

Core judges its own rules and gathers each module's part of the judgement, which the module gives
through `amendment_judged`: attendance's check-in, then weather's forecasts, in the order they sign
up. `judged` asks both modules' pure judges for their parts and hands them to `judge_amendment`,
so that a test of a rule spanning core and the modules reads as it did when core judged them all.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


def judged(
    rnd: Any,
    changes: dict[str, Any],
    *,
    now: datetime,
    attendance: Any = None,
    weather: Any = None,
    drawn: Mapping[int, bool] | None = None,
    called: bool = False,
    notified: bool = False,
) -> Any:
    """*changes* to *rnd* judged at *now*, with attendance's check-in judged at the lead times
    *attendance* (none where the module is off), whether its call is *called* and its last notice
    *notified*, and whether *changes* moves the round; weather's forecasts at the horizons
    *weather* (none where it is off), the phases *drawn* as weather records them; the
    `AmendmentVerdict`."""
    from leaguebot.attendance.services.check_in.check_in_amendment_rules import judge_check_in
    from leaguebot.core.services.rounds.amendment_rules_service import judge_amendment
    from leaguebot.weather.services.forecast_amendment import judge_forecasts

    moment = changes.get("scheduled_at", rnd.scheduled_at).astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
    moved = moment != rnd.scheduled_at.astimezone(timezone.utc)
    parts = (
        judge_check_in(
            moment=moment, now=now, windows=attendance, called=called, notified=notified,
            moved=moved,
        ).part,
        judge_forecasts(changes, moment=moment, now=now, windows=weather, drawn=drawn or {}).part,
    )
    return judge_amendment(rnd, changes, now=now, parts=parts)
