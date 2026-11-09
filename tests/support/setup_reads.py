"""The season being set up, as a mock season service reads it from the database.

A command finds the season being set up by reading it at the moment it needs it (#283): the
season itself through `SeasonService.get_setup_season`, its divisions through `get_divisions`
and each division's rounds through `get_division_rounds`. A test on a mock bot hands those three
reads the setup it wants the command to find, here, rather than putting one in the cog's memory.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from leaguebot.core.models.division import Division
from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.core.models.season import Season, SeasonStatus


def hold_setup(
    season_service,
    cfg=None,
    *,
    season_id: int | None = None,
    season_number: int | None = None,
    game_edition: int | None = None,
    start_date: date | None = None,
    divisions: bool = True,
) -> None:
    """Make *season_service*'s three reads answer with the season being set up that *cfg*
    describes, or with no season being set up where *cfg* is None.

    *cfg* is a `PendingConfig`, or anything carrying its `season_id`, `season_number`,
    `game_edition`, `start_date` and `divisions`; the keywords override what it carries. Each
    division is numbered from 1 in the order given, and each of its rounds from the division's
    number times 100. With *divisions* false, `get_divisions` and `get_division_rounds` are
    left as the test set them, for a test whose divisions are its own.
    """
    if cfg is None and season_id is None:
        season_service.get_setup_season = AsyncMock(return_value=None)
        if divisions:
            season_service.get_divisions = AsyncMock(return_value=[])
            season_service.get_division_rounds = AsyncMock(return_value=[])
        return

    def _of(name: str, given, default):
        if given is not None:
            return given
        return getattr(cfg, name, default) if cfg is not None else default

    sid = _of("season_id", season_id, 0)
    season_service.get_setup_season = AsyncMock(return_value=Season(
        id=sid,
        start_date=_of("start_date", start_date, date(2026, 3, 1)),
        status=SeasonStatus.SETUP,
        season_number=_of("season_number", season_number, 0),
        game_edition=_of("game_edition", game_edition, 0),
    ))
    if not divisions:
        return

    stored: list[Division] = []
    rounds: dict[int, list[Round]] = {}
    for number, div in enumerate(getattr(cfg, "divisions", None) or [], start=1):
        stored.append(Division(
            id=number,
            season_id=sid,
            name=div.name,
            mention_role_id=div.role_id,
            forecast_channel_id=div.channel_id,
            status="ACTIVE",
            tier=div.tier,
        ))
        rounds[number] = [
            Round(
                id=number * 100 + index,
                division_id=number,
                round_number=r["round_number"],
                format=RoundFormat(r["format"]),
                track_name=r.get("track_name"),
                scheduled_at=r["scheduled_at"],
            )
            for index, r in enumerate(div.rounds, start=1)
        ]
    season_service.get_divisions = AsyncMock(return_value=stored)
    season_service.get_division_rounds = AsyncMock(
        side_effect=lambda division_id: list(rounds.get(division_id, []))
    )
