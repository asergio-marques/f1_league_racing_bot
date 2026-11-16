"""The verdict picture's two readers of the team a driver drove for, which results reads from core's
team readers and hands the drawing: a stated exception each (architecture.md, "A catch-all error
handler"), an optional field not being worth a failed picture."""
from __future__ import annotations

import pytest

from leaguebot.core.services.drivers import team_service
from leaguebot.results.services.review import verdict_announcement_service as vas


@pytest.mark.parametrize(("reader", "core_reader"), [
    pytest.param(vas.team_name_for_entry, "team_names_for_instances", id="name"),
    pytest.param(vas.team_key_for_entry, "team_artwork_keys_for_instances", id="shorthand"),
])
async def test_a_verdict_s_team_that_cannot_be_read_is_left_off_its_picture(
    monkeypatch, reader, core_reader,
):
    """Team 7 recorded on the verdict's entry, and core's reader of the teams raising: the reader
    answers None, so the verdict is drawn without the team, rather than raising and failing the
    picture. An entry recording no team answers None without a read."""
    reads: list[list[int]] = []

    async def _unreadable(_db_path, ids):
        reads.append(list(ids))
        raise RuntimeError("database is locked")

    monkeypatch.setattr(team_service, core_reader, _unreadable)

    assert await reader(":unused:", team_id=7) is None
    assert reads == [[7]]
    assert await reader(":unused:", team_id=None) is None
    assert reads == [[7]]
