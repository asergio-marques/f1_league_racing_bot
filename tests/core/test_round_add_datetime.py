"""`/round add` reads its moment by the shared datetime rule (#362).

A round is stored naive, meaning UTC. The XML import converted a time given with a zone to that
form, but `/round add` kept the zone as typed, so the same moment could be stored two ways and
two rounds at one instant would not be seen to clash. Both now read by
`leaguebot.core.utils.input_validator.parse_datetime`.

A mystery round is added throughout, which needs no track and so no database.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.season_cog import PendingConfig, PendingDivision, SeasonCog
from leaguebot.core.models.round import RoundFormat
from tests.support.undecorate import undecorate

ACTOR_ID = 77


def _cog(cfg: PendingConfig) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.output_router.post_log = AsyncMock(return_value=None)
    cog._pending = {ACTOR_ID: cfg}
    cog._get_pending = MagicMock(return_value=cfg)
    cog._snapshot_pending = AsyncMock(return_value=None)
    cog._calendar_round_overflow = AsyncMock(return_value=None)
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.user.id = ACTOR_ID
    interaction.user.display_name = "Manager"
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0]) for call in interaction.followup.send.await_args_list if call.args
    )


async def _add(division: PendingDivision, scheduled_at: str):
    interaction = _interaction()
    cog = _cog(PendingConfig(divisions=[division], season_id=7))
    await undecorate(SeasonCog.round_add)(
        cog, interaction, division.name, "MYSTERY", scheduled_at, ""
    )
    return interaction


async def test_round_add_stores_a_zoned_time_as_utc():
    division = PendingDivision(name="Pro", role_id=1, tier=1)

    await _add(division, "2026-06-14T20:00+02:00")

    stored = division.rounds[0]["scheduled_at"]
    assert stored == datetime(2026, 6, 14, 18, 0)
    assert stored.tzinfo is None


async def test_a_zoned_time_clashes_with_the_same_instant_given_without_one():
    """Stored alike, the two are seen to be one moment and the second is refused."""
    division = PendingDivision(
        name="Pro",
        role_id=1,
        tier=1,
        rounds=[
            {
                "round_number": 1,
                "format": RoundFormat.MYSTERY,
                "track_name": None,
                "scheduled_at": datetime(2026, 6, 14, 18, 0),
            }
        ],
    )

    interaction = await _add(division, "2026-06-14T20:00+02:00")

    assert len(division.rounds) == 1
    assert "cannot share a moment" in _replied(interaction)


async def test_an_unreadable_time_is_refused():
    division = PendingDivision(name="Pro", role_id=1, tier=1)

    interaction = await _add(division, "14/06/2026 18:00")

    assert division.rounds == []
    assert "Invalid datetime" in _replied(interaction)


# ---------------------------------------------------------------------------
# Every refusal of /round add is recorded (#482)
# ---------------------------------------------------------------------------

#: The moment the division's one standing round is held at, which a clashing round asks for.
_STANDING_AT = datetime(2026, 6, 14, 18, 0)

_OVERFLOW = (
    "❌ That would give the division 2 rounds, but the configured calendar template draws "
    "1. The round was **not** added.\n"
    "Enlarge the template, or turn the `calendar` image aspect off with `/images config toggle`."
)


@pytest.mark.xfail(strict=True, reason="#482: /round add's refusals are not yet recorded")
@pytest.mark.parametrize(
    "arranged, asked, reply",
    [
        pytest.param(
            {"setup": False}, {},
            "❌ No pending season setup. Run `/season setup` first.",
            id="no_season_being_set_up",
        ),
        pytest.param(
            {}, {"format": "WET"},
            "❌ Invalid format `WET`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
            id="an_invalid_format",
        ),
        pytest.param(
            {}, {"format": "NORMAL"},
            "❌ A track is required for `NORMAL` rounds. Leave track blank only for "
            "`MYSTERY` rounds.",
            id="a_track_required",
        ),
        pytest.param(
            {}, {"format": "NORMAL", "track": "Atlantis"},
            "❌ Unknown track `Atlantis`.\n"
            "Use `/round add` and type a number or name — autocomplete will guide you.",
            id="an_unknown_track",
        ),
        pytest.param(
            {}, {"scheduled_at": "14/06/2026 18:00"},
            "❌ Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
            id="an_invalid_datetime",
        ),
        pytest.param(
            {}, {"division_name": "Am"},
            "❌ Division `Am` not found in pending setup.",
            id="an_unknown_division",
        ),
        pytest.param(
            {}, {"scheduled_at": "2026-06-14T18:00:00"},
            "❌ **Pro** already holds round 1 at <t:1781460000:F>. Two rounds of one "
            "division cannot share a moment — the season could not be approved. The round "
            "was **not** added.",
            id="a_round_clash",
        ),
        pytest.param(
            {"overflow": _OVERFLOW}, {},
            _OVERFLOW,
            id="a_calendar_overflow",
        ),
    ],
)
async def test_every_round_add_refusal_is_recorded(monkeypatch, arranged, asked, reply):
    """The core specification's record of what changed: a refusal is one line naming the member,
    what was refused and why. A season being set up holds division Pro with one mystery round on
    14 June 2026 at 18:00 UTC, unless the case says otherwise. The manager (id 77) runs /round add
    for Pro, a mystery round on 21 June 2026 at 18:00, but with one thing wrong: no season being
    set up; format WET; a NORMAL round with no track; track Atlantis, which the bot does not know;
    a moment typed 14/06/2026 18:00; division Am, which does not exist; the moment Pro's round 1
    already holds; or a round the calendar template has no room for. The manager gets today's
    reply word for word and nothing else, no round is added, and the log channel gets exactly one
    line, "⛔ `/round add` refused for Manager (<@77>) — " and the reply's first line."""
    division = PendingDivision(
        name="Pro",
        role_id=1,
        tier=1,
        rounds=[
            {
                "round_number": 1,
                "format": RoundFormat.MYSTERY,
                "track_name": None,
                "scheduled_at": _STANDING_AT,
            }
        ],
    )
    cog = _cog(PendingConfig(divisions=[division], season_id=7))
    if arranged.get("setup") is False:
        cog._pending = {}
        cog._get_pending = MagicMock(return_value=None)
    if "overflow" in arranged:
        cog._calendar_round_overflow = AsyncMock(return_value=arranged["overflow"])
    cog.bot.db_path = ":memory:"
    monkeypatch.setattr(
        "leaguebot.core.services.track_service.resolve_track_name",
        AsyncMock(return_value=None),
    )
    interaction = _interaction()
    interaction.client = cog.bot
    interaction.command.qualified_name = "round add"
    called = {
        "division_name": "Pro",
        "format": "MYSTERY",
        "scheduled_at": "2026-06-21T18:00:00",
        "track": "",
        **asked,
    }

    await undecorate(SeasonCog.round_add)(
        cog,
        interaction,
        called["division_name"],
        called["format"],
        called["scheduled_at"],
        called["track"],
    )

    assert [call.args[0] for call in interaction.followup.send.await_args_list] == [reply]
    assert [r["scheduled_at"] for r in division.rounds] == [_STANDING_AT]
    cog._snapshot_pending.assert_not_awaited()
    logged = [str(call.args[0]) for call in cog.bot.output_router.post_log.await_args_list]
    assert logged == [
        f"⛔ `/round add` refused for Manager (<@{ACTOR_ID}>) — "
        f"{reply.splitlines()[0][2:]}"
    ]
