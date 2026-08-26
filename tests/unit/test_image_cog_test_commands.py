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


# ── The notice block, and its link back to the log ────────────────────────


class TestTheNoticeBlock:
    """`/images test` is the **only** surface that shows notices in a reply.

    Every posting path logs them and says nothing to the channel, because a notice is for
    whoever is configuring the module, not for the drivers reading the post. A preview is
    the one moment someone asked to be told.
    """

    @staticmethod
    def _notice(detail, field_id=None, kind="ASSET_FALLBACK_USED"):
        from models.image_module import RenderNotice

        return RenderNotice(
            image_type="standings_drivers",
            notice_kind=kind,
            detail=detail,
            field_id=field_id,
        )

    @staticmethod
    def _bot(*, jump_url="http://discord/x/1", fail=False):
        posted = []

        async def _post_log(server_id, content):
            if fail:
                raise RuntimeError("no permission to write to the log channel")
            posted.append(content)
            return SimpleNamespace(jump_url=jump_url)

        bot = SimpleNamespace(output_router=SimpleNamespace(post_log=_post_log))
        bot.posted = posted
        return bot

    async def _run(self, cog, notices):
        interaction = _Interaction()
        await cog._send_preview(
            interaction,
            title="Standings",
            context=_context(),
            outcomes=[
                ("Standings", "standings_drivers_template", _outcome(notices=notices))
            ],
        )
        return interaction.followup.messages[0]

    async def test_the_reply_links_to_the_logged_block(self, cog):
        cog.bot = self._bot()

        reply = await self._run(cog, [self._notice("a flag fell back", "row_1_flag")])

        assert "http://discord/x/1" in reply

    async def test_the_notices_are_logged_as_well_as_shown(self, cog):
        cog.bot = self._bot()

        await self._run(cog, [self._notice("a flag fell back", "row_1_flag")])

        assert len(cog.bot.posted) == 1
        assert "a flag fell back" in cog.bot.posted[0]

    async def test_a_log_failure_costs_the_link_and_nothing_else(self, cog):
        """A log-channel problem must never cost the preview, which is what was asked for."""
        cog.bot = self._bot(fail=True)

        reply = await self._run(cog, [self._notice("a flag fell back", "row_1_flag")])

        assert "Notices" in reply
        assert "a flag fell back" in reply
        assert "http" not in reply

    async def test_twenty_identical_notices_are_counted_rather_than_listed(self, cog):
        cog.bot = self._bot()
        notices = [
            self._notice("no `marker` image for “gained”", f"row_{i}_marker")
            for i in range(20)
        ]

        reply = await self._run(cog, notices)

        assert "×20" in reply
        assert reply.count("no `marker` image") == 1

    async def test_the_reply_survives_a_render_that_degraded_heavily(self, cog):
        """Grouping is what keeps a busy render's notices inside Discord's limit, rather
        than being cut off mid-list by the 1900-character trim."""
        cog.bot = self._bot()
        notices = [
            self._notice(f"no `team` image for “Team {i}”", f"row_{i}_team")
            for i in range(40)
        ]

        reply = await self._run(cog, notices)

        assert len(reply) <= 1900

    async def test_a_clean_render_says_nothing_about_notices(self, cog):
        cog.bot = self._bot()

        reply = await self._run(cog, [])

        assert "Notices" not in reply
        assert cog.bot.posted == []


# ── A preview leaves nothing on the host either ───────────────────────────
#
# `/images test` puts nothing in a league's channel, but it renders through the same
# pipeline. An evening of template-checking would otherwise litter the host exactly as a
# season of posting does.


class TestThePreviewDiscardsItsPictures:
    @staticmethod
    def _artifact(tmp_path, name="calendar_template"):
        directory = tmp_path / f"f1bot_render_{name}"
        directory.mkdir()
        png = directory / f"{name}.png"
        png.write_bytes(b"\x89PNG")
        return png

    async def test_the_pictures_are_gone_once_the_preview_has_replied(self, cog, tmp_path):
        png = self._artifact(tmp_path)
        interaction = _Interaction()

        await cog._send_preview(
            interaction,
            title="Calendar",
            context=_context(),
            outcomes=[("Calendar", "calendar_template", _outcome(png_paths=[png]))],
        )

        assert len(interaction.followup.files) == 1, "the preview still attaches them"
        assert not png.exists()
        assert not png.parent.exists()

    async def test_the_pictures_are_gone_when_the_reply_fails(self, cog, tmp_path):
        png = self._artifact(tmp_path)
        interaction = _Interaction()

        async def _boom(*_args, **_kwargs):
            raise RuntimeError("the interaction expired")

        interaction.followup.send = _boom

        with pytest.raises(RuntimeError):
            await cog._send_preview(
                interaction,
                title="Calendar",
                context=_context(),
                outcomes=[("Calendar", "calendar_template", _outcome(png_paths=[png]))],
            )

        assert not png.exists()
