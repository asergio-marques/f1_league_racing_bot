"""A mention inside a steward's text names the driver it addresses (#142).

`build_drawing` is exercised **for real** here. Every other test of the verdict path
monkeypatches it out, and the stubs that stand in its place reimplemented the very defect this
module pins — which is why a graphic spent months naming the penalised driver in place of
everyone a steward mentioned and the suite stayed green.

No rasteriser and no Discord: a guild is faked exactly as `_driver_names` reads one. The round
is deliberately left unseeded — `_round_context` answers `{}` for a round it cannot find, so
nothing here depends on a calendar it is not testing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.image.services.image_verdict_service import VerdictKind

SERVER_ID = 1001
ROUND_ID = 77

#: The driver the verdict is against — the name on the driver line.
ALICE = 4815162342
#: A second driver, mentioned *inside* the steward's prose. The whole point of #142.
BOB = 90210
#: A third driver, for the test that counts the reads.
CAROL = 27182818
#: Mentioned by a steward, but the league holds no name of any kind for them.
STRANGER = 31415926


# ── Fakes ─────────────────────────────────────────────────────────────────


class _Member:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


class _Guild:
    """A guild `_driver_names` can read, cache miss and all.

    `fetch_member` is implemented rather than left off: absent it the reader raises
    AttributeError, the resolution collapses to its fallback and a test passes for the wrong
    reason — the very thing these tests exist to catch.
    """

    def __init__(self, members: dict[int, _Member] | None = None) -> None:
        self.id = SERVER_ID
        self._members = members or {}

    def get_member(self, user_id):
        return self._members.get(int(user_id))

    async def fetch_member(self, _user_id):
        import discord

        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "Unknown Member"
        )


class _Bot:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path


# ── Seeding ───────────────────────────────────────────────────────────────


async def _seed(db_path: str, drivers) -> None:
    """A database holding a driver profile per entry, and a signup record where given.

    *drivers* is an iterable of ``(user_id, signup_display_name, signup_username)``.
    """
    from leaguebot.core.db.database import get_connection, run_migrations

    await run_migrations(db_path)

    async with get_connection(db_path) as db:
        # `signup_records.server_id` is a foreign key onto `server_configs`.
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 10, 20, 30)",
            (SERVER_ID,),
        )
        for user_id, display_name, username in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state, "
                "is_test_driver) VALUES (?, 'FULL_TIME', 0)",
                (str(user_id),),
            )
            if display_name is not None or username is not None:
                await db.execute(
                    "INSERT INTO signup_records (discord_user_id, "
                    "discord_username, server_display_name) VALUES (?, ?, ?)",
                    (str(user_id), username, display_name),
                )
        await db.commit()


async def _draw(
    db_path,
    guild,
    *,
    description="Collision in the braking zone.",
    justification="Contact at turn 3.",
    driver_name="Alice Smith",
):
    """One verdict drawing, built by the real `build_drawing`."""
    from leaguebot.image.services.image_verdict_post import build_drawing

    return await build_drawing(
        _Bot(db_path),
        guild=guild,
        db_path=db_path,
        round_id=ROUND_ID,
        kind=VerdictKind.PENALTY,
        season_number=1,
        division_name="Pro Division",
        round_number=8,
        session_label="Race",
        driver_name=driver_name,
        driver_discord_id=ALICE,
        penalty_description="5 seconds added",
        description_text=description,
        justification_text=justification,
    )


# ── The defect (#142) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_mention_of_another_driver_draws_that_driver_s_name(tmp_path):
    """The regression test for #142.

    A collision justification names the other car, which is the ordinary thing for one to do.
    Before the fix this drew "Contact with Alice Smith at turn 3" — the graphic stating that
    the penalised driver collided with themselves.
    """
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None)])

    drawing = await _draw(
        db_path,
        _Guild(),
        justification=f"Contact with <@{BOB}> at turn 3.",
    )

    assert drawing.justification == "Contact with Bob Jones at turn 3."
    assert "Alice" not in drawing.justification


@pytest.mark.asyncio
async def test_each_mention_draws_the_driver_it_addresses(tmp_path):
    """Two mentions of two drivers are two different names, not one repeated."""
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None)])

    drawing = await _draw(
        db_path,
        _Guild(),
        justification=f"<@{BOB}> was hit by <@{ALICE}> at turn 3.",
    )

    assert drawing.justification == "Bob Jones was hit by Alice Smith at turn 3."


@pytest.mark.asyncio
async def test_the_description_and_the_justification_are_both_resolved(tmp_path):
    """Both free-text blocks are drawn, so both are resolved."""
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None)])

    drawing = await _draw(
        db_path,
        _Guild(),
        description=f"Collision with <@{BOB}>.",
        justification=f"<@{BOB}> had the racing line.",
    )

    assert drawing.description == "Collision with Bob Jones."
    assert drawing.justification == "Bob Jones had the racing line."


# ── The name each mention resolves to ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_mention_of_the_penalised_driver_draws_the_name_on_the_driver_line(
    tmp_path,
):
    """One driver is one name within a single graphic.

    The driver line is drawn from the name the announcement service resolved. A mention of
    that same driver in the prose beneath it draws that name and never a second rendering
    of it.
    """
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Signed Up As Alice", None)])

    # No member for Alice, so resolving the mention through the chain on its own would answer
    # "Signed Up As Alice" — a second rendering of the driver already named above it.
    drawing = await _draw(
        db_path,
        _Guild(),
        driver_name="Alice On Server",
        justification=f"<@{ALICE}> admitted fault.",
    )

    assert drawing.driver_name == "Alice On Server"
    assert drawing.justification == "Alice On Server admitted fault."


@pytest.mark.asyncio
async def test_a_server_display_name_beats_the_recorded_signup_name(tmp_path):
    """The module's shared chain is genuinely used, not a bare read of the signup table."""
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Signed Up As Bob", "bobby")])

    drawing = await _draw(
        db_path,
        _Guild({BOB: _Member("Bob On Server")}),
        justification=f"Contact with <@{BOB}> at turn 3.",
    )

    assert drawing.justification == "Contact with Bob On Server at turn 3."


@pytest.mark.asyncio
async def test_a_signup_username_stands_where_the_league_recorded_no_display_name(tmp_path):
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, None, "bobby")])

    drawing = await _draw(
        db_path,
        _Guild(),
        justification=f"Contact with <@{BOB}> at turn 3.",
    )

    assert drawing.justification == "Contact with bobby at turn 3."


@pytest.mark.asyncio
async def test_a_mention_the_league_holds_no_name_for_falls_back_to_the_id(tmp_path):
    """The end of the chain, and it is the honest answer.

    A driver who left the server, or somebody who was never a driver at all, draws as the
    number a reader can look up — never as the name of whoever is being penalised.
    """
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None)])

    drawing = await _draw(
        db_path,
        _Guild(),
        justification=f"Contact with <@{STRANGER}> at turn 3.",
    )

    assert drawing.justification == f"Contact with {STRANGER} at turn 3."
    assert "Alice" not in drawing.justification


@pytest.mark.asyncio
async def test_text_holding_no_mention_is_drawn_as_written(tmp_path):
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None)])

    drawing = await _draw(
        db_path,
        _Guild(),
        description="Collision in the braking zone.",
        justification="Video evidence reviewed.",
    )

    assert drawing.description == "Collision in the braking zone."
    assert drawing.justification == "Video evidence reviewed."


# ── The read failing costs no verdict ─────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unreadable_name_read_leaves_the_raw_id_and_still_draws(
    tmp_path, monkeypatch
):
    """A name is never worth a lost verdict (the precedent `_graphic_name` set for #141).

    The mention degrades to the id the chain ends at anyway, and the penalised driver keeps
    the name already resolved for the driver line.
    """
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None)])

    from leaguebot.image.services import image_results_post

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(image_results_post, "_driver_names", _boom)

    drawing = await _draw(
        db_path,
        _Guild(),
        justification=f"<@{ALICE}> hit <@{BOB}> at turn 3.",
    )

    assert drawing.justification == f"Alice Smith hit {BOB} at turn 3."


@pytest.mark.asyncio
async def test_a_verdict_without_a_guild_still_resolves_from_the_recorded_names(tmp_path):
    """No guild is a missing display name, not a failed resolution."""
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None)])

    drawing = await _draw(
        db_path,
        None,
        justification=f"Contact with <@{BOB}> at turn 3.",
    )

    assert drawing.justification == "Contact with Bob Jones at turn 3."


# ── One read, however many mentions ───────────────────────────────────────


@pytest.mark.asyncio
async def test_every_mention_is_resolved_in_one_read(tmp_path, monkeypatch):
    """A steward naming five drivers costs one query, not five.

    Pinned because the obvious implementation — resolving inside the substitution callback —
    is synchronous and would have to read once per mention.
    """
    db_path = str(tmp_path / "test.db")
    await _seed(
        db_path,
        [(ALICE, "Alice Smith", None), (BOB, "Bob Jones", None), (CAROL, "Carol King", None)],
    )

    from leaguebot.image.services import image_results_post

    calls = []
    real = image_results_post._driver_names

    async def _counting(bot, guild, user_ids):
        calls.append(list(user_ids))
        return await real(bot, guild, user_ids)

    monkeypatch.setattr(image_results_post, "_driver_names", _counting)

    drawing = await _draw(
        db_path,
        _Guild(),
        description=f"<@{BOB}> and <@{CAROL}> were involved.",
        justification=f"<@{BOB}> was at fault, <@{CAROL}> was unsighted.",
    )

    assert len(calls) == 1, f"one read for every mention, got {len(calls)}"
    assert sorted(calls[0]) == sorted([BOB, CAROL])
    assert drawing.description == "Bob Jones and Carol King were involved."
    assert drawing.justification == "Bob Jones was at fault, Carol King was unsighted."


@pytest.mark.asyncio
async def test_text_mentioning_nobody_reads_no_names_at_all(tmp_path, monkeypatch):
    """The common verdict — no mention in either field — adds no query to the path."""
    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None)])

    from leaguebot.image.services import image_results_post

    calls = []

    async def _counting(_bot, _guild, user_ids):
        calls.append(list(user_ids))
        return {}

    monkeypatch.setattr(image_results_post, "_driver_names", _counting)

    await _draw(db_path, _Guild(), justification="Video evidence reviewed.")

    assert calls == [], "no mention is no read"


# ── The name drawn and the artwork key part company (#381) ────────────────


@pytest.mark.asyncio
async def test_the_badge_is_found_by_the_shorthand_while_the_full_name_is_drawn(tmp_path):
    """A verdict names the team whose car was driven by its full name, and finds its badge by
    the shorthand — the divergence ``team_slug_source`` was kept for."""
    from leaguebot.image.services.image_verdict_post import build_drawing

    db_path = str(tmp_path / "test.db")
    await _seed(db_path, [(ALICE, "Alice Smith", None)])

    drawing = await build_drawing(
        _Bot(db_path),
        guild=_Guild(),
        db_path=db_path,
        round_id=ROUND_ID,
        kind=VerdictKind.PENALTY,
        season_number=1,
        division_name="Pro Division",
        round_number=8,
        session_label="Race",
        driver_name="Alice Smith",
        driver_discord_id=ALICE,
        penalty_description="5 seconds added",
        description_text="Collision.",
        justification_text="Contact at turn 3.",
        team_name="Oracle Red Bull Racing",
        team_key="RBR",
    )

    assert drawing.team_name == "Oracle Red Bull Racing"
    assert drawing.team_datum == "RBR"
