"""Unit tests for the availability step of the signup wizard — issue #126.

A driver types the display numbers they were shown. Those numbers are chronological
positions that shift whenever the slot list changes, so the wizard converts them to
durable slot IDs at the input boundary and stores only those. Nothing a driver reads
ever shows the stored form.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Channel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content, *args, **kwargs):
        self.sent.append(content)


class _Message:
    def __init__(self, content: str) -> None:
        self.content = content
        self.channel = _Channel()


def _slots():
    """Mon 19:00 (#1), Wed 20:00 (#2), Fri 21:00 (#3) — the issue's worked example."""
    from models.signup_module import AvailabilitySlot

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
def wizard_and_service():
    """A WizardService with _advance_wizard stubbed, and a wizard record to drive."""
    from models.signup_module import ConfigSnapshot, SignupWizardRecord, WizardState
    from services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    advanced: list[bool] = []

    async def _advance(wizard, message):
        advanced.append(True)

    svc._advance_wizard = _advance  # type: ignore[method-assign]

    wizard = SignupWizardRecord(
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
    return svc, wizard, advanced


# ---------------------------------------------------------------------------
# Conversion at the input boundary
# ---------------------------------------------------------------------------


class TestAvailabilityConversion:
    async def test_typed_display_numbers_stored_as_durable_ids(self, wizard_and_service):
        svc, wizard, advanced = wizard_and_service
        message = _Message("3")
        await svc._handle_availability(wizard, message)
        assert wizard.draft_answers["availability_slot_ids"] == ["Fri_21_00"]
        assert advanced == [True]

    async def test_multiple_numbers_converted_in_the_order_given(self, wizard_and_service):
        svc, wizard, _ = wizard_and_service
        await svc._handle_availability(wizard, _Message("3, 1"))
        assert wizard.draft_answers["availability_slot_ids"] == ["Fri_21_00", "Mon_19_00"]

    async def test_no_display_ordinal_is_ever_stored(self, wizard_and_service):
        """The stored answer must not be the position — that is the whole defect."""
        svc, wizard, _ = wizard_and_service
        await svc._handle_availability(wizard, _Message("1 2 3"))
        stored = wizard.draft_answers["availability_slot_ids"]
        assert all(isinstance(s, str) for s in stored)
        assert stored == ["Mon_19_00", "Wed_20_00", "Fri_21_00"]


class TestAvailabilityRejection:
    async def test_unknown_display_number_rejected_quoting_display_numbers(
        self, wizard_and_service
    ):
        svc, wizard, advanced = wizard_and_service
        message = _Message("4")
        await svc._handle_availability(wizard, message)
        assert "availability_slot_ids" not in wizard.draft_answers
        assert advanced == []
        reply = message.channel.sent[0]
        assert "4" in reply and "1, 2, 3" in reply
        assert "_" not in reply, "the storage form must not leak into a driver's reply"

    async def test_non_numeric_rejected(self, wizard_and_service):
        svc, wizard, advanced = wizard_and_service
        message = _Message("Fri_21_00")
        await svc._handle_availability(wizard, message)
        assert "availability_slot_ids" not in wizard.draft_answers
        assert advanced == []
        assert "numbers" in message.channel.sent[0]

    async def test_empty_selection_rejected(self, wizard_and_service):
        svc, wizard, advanced = wizard_and_service
        message = _Message("   ")
        await svc._handle_availability(wizard, message)
        assert "availability_slot_ids" not in wizard.draft_answers
        assert advanced == []
        assert "at least one" in message.channel.sent[0]


# ---------------------------------------------------------------------------
# Review panel
# ---------------------------------------------------------------------------


def _record(slot_ids):
    from models.signup_module import SignupRecord

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
        from services.wizard_service import WizardService

        labels = {s.slot_id: s.display_label for s in _slots()}
        panel = WizardService._format_review_panel(_record(["Fri_21_00"]), labels)
        assert "**Availability:** Friday 21:00 UTC" in panel

    def test_answer_for_a_removed_slot_named_not_printed(self):
        """A slot that no longer exists has no label; the storage form is not shown."""
        from services.wizard_service import WizardService

        labels = {s.slot_id: s.display_label for s in _slots() if s.day_of_week != 5}
        panel = WizardService._format_review_panel(_record(["Fri_21_00"]), labels)
        assert "**Availability:** Unknown slot" in panel
        assert "Fri_21_00" not in panel

    def test_no_labels_available_names_every_slot_unknown(self):
        from services.wizard_service import WizardService

        panel = WizardService._format_review_panel(_record(["Fri_21_00", "Mon_19_00"]))
        assert "**Availability:** Unknown slot, Unknown slot" in panel

    def test_no_availability_reads_none(self):
        from services.wizard_service import WizardService

        panel = WizardService._format_review_panel(_record([]), {})
        assert "**Availability:** None" in panel
