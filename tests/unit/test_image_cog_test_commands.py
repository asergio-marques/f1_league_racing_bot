"""The `/images test` command surface (045).

Eleven previews under one nested group, each drawn against the league's own division.
These tests exercise the surface and the shared reply, with Discord stubbed throughout —
no gateway, no server, no running bot.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.image_cog import ImageCog  # noqa: E402


# ── Stubs ─────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.deferred = False
        self._done = False
        self.messages: list[str] = []

    def is_done(self) -> bool:
        return self._done

    async def defer(self, **kwargs):
        self.deferred = True
        self._done = True

    async def send_message(self, content, **kwargs):
        self._done = True
        self.messages.append(content)


class _Followup:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.files: list = []

    async def send(self, content=None, *, files=None, **kwargs):
        self.messages.append(content or "")
        self.files.extend(files or [])


class _Interaction:
    def __init__(self, guild_id: int = 99) -> None:
        self.guild_id = guild_id
        self.guild = None
        self.response = _Response()
        self.followup = _Followup()
        self.user = SimpleNamespace(display_name="Tester", id=1)


def _context(**overrides):
    """A resolved preview context, as `resolve_context` would return one."""
    from services.image_preview_service import PreviewContext

    values = dict(
        server_id=99,
        season_number=1,
        division_id=1,
        division_name="Division 1",
        division_tier=1,
    )
    values.update(overrides)
    return PreviewContext(**values)


def _outcome(*, problem=None, png_paths=(), notices=()):
    return SimpleNamespace(
        problem=problem, png_paths=list(png_paths), notices=list(notices)
    )


@pytest.fixture
def cog():
    return ImageCog(SimpleNamespace())


# ── T011: the group ───────────────────────────────────────────────────────


class TestTheGroup:
    def test_test_is_a_group_under_images(self):
        assert ImageCog.test.name == "test"
        assert ImageCog.test.parent is ImageCog.images

    def test_the_withdrawn_command_is_gone(self):
        """`/images test <kind>` is replaced, not kept alongside."""
        assert not any(
            command.name == "test" and not hasattr(command, "commands")
            for command in ImageCog.images.commands
        )

    def test_the_group_stays_within_discords_ceiling(self):
        """Eleven previews, against a limit of twenty-five."""
        assert len(ImageCog.test.commands) <= 25

    def test_the_startup_check_covers_the_new_group(self):
        """The ceiling check is worthless if it does not look at the group that grew."""
        import inspect

        from cogs import image_cog

        source = inspect.getsource(image_cog._verify_discord_group_limits)
        assert "ImageCog.test" in source

    def test_nesting_stays_within_what_discord_allows(self):
        """command -> group -> subcommand is the maximum depth; no preview may nest further."""
        for command in ImageCog.test.commands:
            assert not hasattr(command, "commands"), command.name


# ── T012: division autocomplete ───────────────────────────────────────────


class TestDivisionAutocomplete:
    async def test_it_offers_the_drawn_seasons_divisions(self, cog):
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(
                get_previewable_season=_async(SimpleNamespace(id=7)),
                get_divisions=_async(
                    [SimpleNamespace(name="Division 1"), SimpleNamespace(name="Division 2")]
                ),
            )
        )

        choices = await cog._division_autocomplete(_Interaction(), "")

        assert [c.value for c in choices] == ["Division 1", "Division 2"]

    async def test_it_filters_by_what_has_been_typed(self, cog):
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(
                get_previewable_season=_async(SimpleNamespace(id=7)),
                get_divisions=_async(
                    [SimpleNamespace(name="Premier"), SimpleNamespace(name="Academy")]
                ),
            )
        )

        choices = await cog._division_autocomplete(_Interaction(), "acad")

        assert [c.value for c in choices] == ["Academy"]

    async def test_it_offers_nothing_where_there_is_no_active_season(self, cog):
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(get_previewable_season=_async(None))
        )

        assert await cog._division_autocomplete(_Interaction(), "") == []

    async def test_a_failure_yields_no_choices_rather_than_breaking_the_command(self, cog):
        def _raise(*args, **kwargs):
            raise RuntimeError("database is away")

        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(get_previewable_season=_raise)
        )

        assert await cog._division_autocomplete(_Interaction(), "") == []

    async def test_it_never_offers_more_than_discord_accepts(self, cog):
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(
                get_previewable_season=_async(SimpleNamespace(id=7)),
                get_divisions=_async(
                    [SimpleNamespace(name=f"Division {n}") for n in range(40)]
                ),
            )
        )

        assert len(await cog._division_autocomplete(_Interaction(), "")) == 25


# ── T013: the shared reply ────────────────────────────────────────────────


class TestTheReply:
    async def test_it_names_the_division_and_attaches_the_picture(self, cog, tmp_path):
        png = tmp_path / "calendar_template.png"
        png.write_bytes(b"\x89PNG")
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(),
            outcomes=[("Calendar", "calendar_template", _outcome(png_paths=[png]))],
        )

        reply = interaction.followup.messages[0]
        assert "Division 1" in reply
        assert "✅ Calendar" in reply
        assert len(interaction.followup.files) == 1

    async def test_it_names_the_round_where_the_kind_has_one(self, cog):
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Check-in call",
            context=_context(round=SimpleNamespace(round_number=3)),
            outcomes=[("Check-in call", "rsvp_template", _outcome())],
        )

        assert "round 3" in interaction.followup.messages[0]

    async def test_a_problem_is_reported_and_no_picture_attached(self, cog):
        """A problem means no image at all, never a partial one (XIV.4)."""
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(),
            outcomes=[("Lineup", "lineup_template", _outcome(problem="the template is invalid"))],
        )

        reply = interaction.followup.messages[0]
        assert "❌ Lineup" in reply
        assert "the template is invalid" in reply
        assert interaction.followup.files == []

    async def test_fabricated_drivers_are_declared(self, cog):
        """A manager must never mistake invented names for their own roster (FR-018)."""
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(fabricated_drivers=True),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        reply = interaction.followup.messages[0]
        assert "invented" in reply
        assert "Seat your drivers" in reply

    async def test_a_real_roster_is_not_declared_invented(self, cog):
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(fabricated_drivers=False),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "invented" not in interaction.followup.messages[0]

    async def test_a_rejected_directory_is_named_with_its_value_and_reason(self, cog):
        """FR-037, FR-038 — the manager is told which value was refused, and why."""
        from services.image_preview_service import DirectoryFault

        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(
                directory_faults=[
                    DirectoryFault(
                        asset_class="flag",
                        configured_value="resources/nope",
                        reason="the configured directory does not exist",
                    )
                ]
            ),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        reply = interaction.followup.messages[0]
        assert "flag" in reply
        assert "resources/nope" in reply
        assert "does not exist" in reply

    async def test_a_clean_render_reports_no_directory_trouble(self, cog):
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        assert "Asset directories" not in interaction.followup.messages[0]

    async def test_the_reply_stays_within_discords_message_limit(self, cog):
        """A division of many drivers must not push the reply past what Discord accepts."""
        from services.image_preview_service import DirectoryFault

        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Standings",
            context=_context(
                directory_faults=[
                    DirectoryFault(
                        asset_class=f"class_{n}",
                        configured_value="x" * 80,
                        reason="y" * 80,
                    )
                    for n in range(40)
                ]
            ),
            outcomes=[("Standings", "standings_drivers_template", _outcome())],
        )

        assert len(interaction.followup.messages[0]) <= 1900


# ── The eleven, and their parameters (T016, T026, T031) ───────────────────


class TestTheElevenCommands:
    EXPECTED = {
        "calendar": ["division"],
        "lineup": ["division"],
        "results": ["division", "round"],
        "standings": ["division", "round"],
        "attendance": ["division", "round"],
        "rsvp": ["division", "round"],
        "weather-p1": ["division", "round"],
        "weather-p2": ["division", "round"],
        "weather-p3": ["division", "round"],
        "weather-mystery": ["division", "round"],
        "verdict": ["division", "round"],
    }

    def test_every_contracted_command_is_registered_and_no_other(self):
        assert {c.name for c in ImageCog.test.commands} == set(self.EXPECTED)

    def test_each_takes_the_parameters_the_contract_fixes(self):
        for command in ImageCog.test.commands:
            names = [p.name for p in command.parameters]
            assert names == self.EXPECTED[command.name], command.name

    # 045's `test_every_parameter_is_mandatory` is withdrawn at 046. Both inputs became
    # optional so that a season-less server can draw with neither, and whether a value is
    # *required* moved from the platform to resolution — see
    # `TestBothParametersAreOptional` below, which asserts the inverse.

    def test_the_division_parameter_always_completes(self):
        for command in ImageCog.test.commands:
            division = next(p for p in command.parameters if p.name == "division")
            assert division.autocomplete, command.name

    def test_the_verdict_command_is_singular(self):
        """A-003 — named as the feature description names it, not after the aspect."""
        names = {c.name for c in ImageCog.test.commands}
        assert "verdict" in names
        assert "verdicts" not in names


def _async(value):
    async def _call(*args, **kwargs):
        return value

    return _call


# ── 046 US1: which season the reply names ─────────────────────────────────


class TestTheReplyNamesTheSeason:
    """FR-004. A manager can always tell which season was drawn, without opening the
    picture — and is told plainly where that season is not yet approved."""

    async def test_the_header_names_the_season_number(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(season_number=7),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        assert "season 7" in interaction.followup.messages[0]

    async def test_a_pending_season_is_called_out(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(season_number=2, season_pending_approval=True),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        message = interaction.followup.messages[0]
        assert "pending approval" in message
        assert "/season approve" in message

    async def test_an_approved_season_says_nothing_about_approval(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(season_number=2),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        assert "pending approval" not in interaction.followup.messages[0]


class TestAutocompleteFollowsTheDrawnSeason:
    async def test_a_season_pending_approval_offers_its_divisions(self, cog):
        """The lookup is the service's; the cog must simply ask the right question."""
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(
                get_previewable_season=_async(SimpleNamespace(id=9)),
                get_divisions=_async([SimpleNamespace(name="Pending Division")]),
            )
        )

        choices = await cog._division_autocomplete(_Interaction(), "")

        assert [c.value for c in choices] == ["Pending Division"]

    async def test_a_season_less_server_offers_nothing_and_does_not_raise(self, cog):
        cog.bot = SimpleNamespace(
            season_service=SimpleNamespace(
                get_previewable_season=_async(None),
                get_divisions=_async([]),
            )
        )

        assert await cog._division_autocomplete(_Interaction(), "") == []


# ── 046 US2: the command surface on a season-less server ──────────────────


class TestBothParametersAreOptional:
    """FR-021. Optionality is a platform relaxation; whether a value is *required* is
    decided at resolution, by whether a season exists."""

    def test_no_preview_command_declares_a_required_parameter(self):
        for command in ImageCog.test.commands:
            for parameter in command.parameters:
                assert not parameter.required, f"{command.name}.{parameter.name}"

    def test_every_round_scoped_command_still_offers_a_round(self):
        from models.image_constants import PREVIEW_KINDS

        for command in ImageCog.test.commands:
            names = {p.name for p in command.parameters}
            expected = {"division"}
            if PREVIEW_KINDS[command.name]["needs_round"]:
                expected.add("round")
            assert names == expected, command.name


class TestTheFabricatedLeagueBanner:
    """FR-024. A manager cannot mistake an invented league for their own."""

    async def test_it_says_the_league_is_invented(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(fabricated_league=True),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        message = interaction.followup.messages[0]
        assert "no season" in message
        assert "invented" in message
        assert "Nothing has been saved" in message

    async def test_it_distinguishes_the_teams_as_the_servers_own(self, cog):
        """The one part of a fabricated league that is not made up."""
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(fabricated_league=True),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "team names are your own" in interaction.followup.messages[0]

    async def test_a_real_league_gets_no_banner(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(),
            outcomes=[("Calendar", "calendar_template", _outcome())],
        )

        assert "invented" not in interaction.followup.messages[0]

    async def test_the_seat_note_is_not_repeated_on_a_fabricated_league(self, cog):
        """It would tell a manager to seat drivers in a division that does not exist."""
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(fabricated_league=True, fabricated_drivers=True),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "Seat your drivers" not in interaction.followup.messages[0]

    async def test_a_real_division_with_empty_seats_still_gets_the_seat_note(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(fabricated_drivers=True),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "Seat your drivers" in interaction.followup.messages[0]


# ── 046 US3: the nationality tally in the reply ───────────────────────────


class TestTheNoNationalityTally:
    """FR-028. Blank flags because the drivers record no nationality, not because the
    flag directory is broken — and a maintainer must be able to tell which."""

    async def test_the_tally_is_reported(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(drivers_without_nationality=4),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        message = interaction.followup.messages[0]
        assert "4 seated drivers record no nationality" in message
        assert "test-mode driver" in message

    async def test_one_driver_reads_as_singular(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(drivers_without_nationality=1),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "1 seated driver records no nationality" in interaction.followup.messages[0]

    async def test_nothing_is_said_where_every_driver_records_one(self, cog):
        interaction = _Interaction()
        await interaction.response.defer()

        await cog._send_preview(
            interaction,
            title="Lineup",
            context=_context(),
            outcomes=[("Lineup", "lineup_template", _outcome())],
        )

        assert "no nationality" not in interaction.followup.messages[0]
