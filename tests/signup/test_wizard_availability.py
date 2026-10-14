"""Unit tests for the availability step of the signup wizard — issue #126.

A driver types the display numbers they were shown. Those numbers are chronological
positions that shift whenever the slot list changes, so the wizard converts them to
durable slot IDs at the input boundary and stores only those. Nothing a driver reads
ever shows the stored form.

On the change queue (#439, slice 6b, C81) a typed answer is judged by `judge_answer`, pure: it
gives the answers to save, or the refusal and the step its line names, and saves and posts
nothing itself; `signup.answer`'s jobs save and post what it gives.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

NO_JUDGE_ANSWER = (
    "#439: a typed availability answer is judged inside the wizard service's handler, which "
    "saves it or posts its refusal at once; there is no pure judge_answer giving the answers "
    "to save or the refusal"
)


def _typed(content: str):
    """A message the driver typed: its text, and no attachment."""
    return SimpleNamespace(content=content, attachments=[])


def _slots():
    """Mon 19:00 (#1), Wed 20:00 (#2), Fri 21:00 (#3) — the issue's worked example."""
    from leaguebot.signup.models.signup_module import AvailabilitySlot

    return [
        AvailabilitySlot(
            id=surrogate,
            slot_id=AvailabilitySlot.make_slot_id(day, time_hhmm),
            slot_sequence_id=ordinal,
            day_of_week=day,
            time_hhmm=time_hhmm,
            display_label=AvailabilitySlot.make_label(day, time_hhmm),
        )
        for surrogate, ordinal, day, time_hhmm in (
            (41, 1, 1, "19:00"),
            (42, 2, 3, "20:00"),
            (43, 3, 5, "21:00"),
        )
    ]


@pytest.fixture
def wizard():
    """A driver's wizard asking for their availability, the three slots above frozen into it,
    nothing answered yet."""
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState

    return SignupWizardRecord(
        id=1,
        discord_user_id="7",
        wizard_state=WizardState.COLLECTING_AVAILABILITY,
        signup_channel_id=99,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=_slots(),
        ),
        draft_answers={},
        current_lap_track_index=0,
        last_activity_at=None,
    )


def _judge(wizard, content: str):
    from leaguebot.signup.services.wizard_changes import judge_answer

    return judge_answer(wizard, _typed(content))


# ---------------------------------------------------------------------------
# Conversion at the input boundary
# ---------------------------------------------------------------------------


class TestAvailabilityConversion:
    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_typed_display_numbers_stored_as_durable_ids(self, wizard):
        from leaguebot.signup.services.wizard_changes import Accepted

        judged = _judge(wizard, "3")
        assert isinstance(judged, Accepted)
        assert judged.answers["availability_slot_ids"] == ["Fri_21_00"]
        assert wizard.draft_answers == {}, "judging saves nothing: its job does"

    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_multiple_numbers_converted_in_the_order_given(self, wizard):
        judged = _judge(wizard, "3, 1")
        assert judged.answers["availability_slot_ids"] == ["Fri_21_00", "Mon_19_00"]

    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_no_display_ordinal_is_ever_stored(self, wizard):
        """The stored answer must not be the position — that is the whole defect."""
        stored = _judge(wizard, "1 2 3").answers["availability_slot_ids"]
        assert all(isinstance(s, str) for s in stored)
        assert stored == ["Mon_19_00", "Wed_20_00", "Fri_21_00"]


class TestAvailabilityRejection:
    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_unknown_display_number_rejected_quoting_display_numbers(self, wizard):
        from leaguebot.signup.services.wizard_changes import Refused

        judged = _judge(wizard, "4")
        assert isinstance(judged, Refused)
        assert judged.step == "Availability"
        assert "4" in judged.reply and "1, 2, 3" in judged.reply
        assert "_" not in judged.reply, "the storage form must not leak into a driver's reply"
        assert wizard.draft_answers == {}

    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_non_numeric_rejected(self, wizard):
        from leaguebot.signup.services.wizard_changes import Refused

        judged = _judge(wizard, "Fri_21_00")
        assert isinstance(judged, Refused)
        assert "numbers" in judged.reply

    @pytest.mark.xfail(strict=True, reason=NO_JUDGE_ANSWER)
    def test_empty_selection_rejected(self, wizard):
        from leaguebot.signup.services.wizard_changes import Refused

        judged = _judge(wizard, "   ")
        assert isinstance(judged, Refused)
        assert "at least one" in judged.reply


# ---------------------------------------------------------------------------
# Review panel
# ---------------------------------------------------------------------------


def _record(slot_ids):
    from leaguebot.signup.models.signup_module import SignupRecord

    return SignupRecord(
        id=1,
        discord_user_id="7",
        discord_username="driver#1",
        server_display_name="Driver",
        nationality=None,
        platform="PC",
        platform_id="driver",
        availability_slot_ids=slot_ids,
        driver_type="Full-time",
        preferred_teams=[],
        preferred_teammate=None,
        lap_times={},
        notes=None,
        signup_channel_id=99,
    )


class TestReviewPanel:
    def test_review_panel_labels_resolve_by_slot_id(self):
        from leaguebot.signup.services.wizard_service import WizardService

        labels = {s.slot_id: s.display_label for s in _slots()}
        panel = WizardService._format_review_panel(_record(["Fri_21_00"]), labels)
        assert "**Availability:** Friday 21:00 UTC" in panel

    def test_answer_for_a_removed_slot_named_not_printed(self):
        """A slot that no longer exists has no label; the storage form is not shown."""
        from leaguebot.signup.services.wizard_service import WizardService

        labels = {s.slot_id: s.display_label for s in _slots() if s.day_of_week != 5}
        panel = WizardService._format_review_panel(_record(["Fri_21_00"]), labels)
        assert "**Availability:** Unknown slot" in panel
        assert "Fri_21_00" not in panel

    def test_no_labels_available_names_every_slot_unknown(self):
        from leaguebot.signup.services.wizard_service import WizardService

        panel = WizardService._format_review_panel(_record(["Fri_21_00", "Mon_19_00"]))
        assert "**Availability:** Unknown slot, Unknown slot" in panel

    def test_no_availability_reads_none(self):
        from leaguebot.signup.services.wizard_service import WizardService

        panel = WizardService._format_review_panel(_record([]), {})
        assert "**Availability:** None" in panel
