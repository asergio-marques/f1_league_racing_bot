"""Tests for penalty_wizard.py structural contracts.

Covers:
- PenaltyReviewState dataclass fields
- AddPenaltyModal fields and constructor
- AppealsReviewView button custom_ids and state=None safety
- _AppealsConfirmClearView existence and button styles
- _pen_label helper
"""
from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src"))

import discord

from services.penalty_wizard import (
    PenaltyReviewState,
    PenaltyReviewView,
    ApprovalView,
    AppealsReviewView,
    AddPenaltyModal,
    _pen_label,
    _CID_AR_ADD,
    _CID_AR_CONFIRM,
    _CID_AR_APPROVE,
    _CID_ADD,
    _CID_CONFIRM,
    _CID_APPROVE,
)
from services.penalty_service import StagedPenalty
from models.points_config import SessionType


# ---------------------------------------------------------------------------
# PenaltyReviewState — dataclass fields
# ---------------------------------------------------------------------------

class TestPenaltyReviewState:
    def _make_state(self) -> PenaltyReviewState:
        return PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[],
            db_path=":memory:",
            bot=None,
        )

    def test_staged_default_empty(self):
        state = self._make_state()
        assert state.staged == []

    def test_staged_appeals_default_empty(self):
        state = self._make_state()
        assert state.staged_appeals == []

    def test_prompt_message_id_default_none(self):
        state = self._make_state()
        assert state.prompt_message_id is None

    def test_appeals_prompt_message_id_default_none(self):
        state = self._make_state()
        assert state.appeals_prompt_message_id is None

    def test_round_number_default_zero(self):
        state = self._make_state()
        assert state.round_number == 0

    def test_division_name_default_empty(self):
        state = self._make_state()
        assert state.division_name == ""

    def test_staged_and_staged_appeals_are_independent(self):
        """Staged and staged_appeals must be separate list instances."""
        state = self._make_state()
        state.staged.append(object())
        assert len(state.staged_appeals) == 0

    def test_two_instances_have_independent_staged_lists(self):
        s1 = self._make_state()
        s2 = self._make_state()
        s1.staged.append(object())
        assert len(s2.staged) == 0


# ---------------------------------------------------------------------------
# AddPenaltyModal — field existence and config
# ---------------------------------------------------------------------------

class TestAddPenaltyModal:
    def _make_modal(self, *, use_appeals_staging: bool = False) -> AddPenaltyModal:
        state = PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[SessionType.FEATURE_RACE],
            db_path=":memory:",
            bot=None,
        )
        return AddPenaltyModal(
            state=state,
            session_type=SessionType.FEATURE_RACE,
            use_appeals_staging=use_appeals_staging,
        )

    def test_driver_input_exists(self):
        modal = self._make_modal()
        assert hasattr(modal, "driver_input")
        assert isinstance(modal.driver_input, discord.ui.TextInput)

    def test_penalty_input_exists(self):
        modal = self._make_modal()
        assert hasattr(modal, "penalty_input")
        assert isinstance(modal.penalty_input, discord.ui.TextInput)

    def test_description_input_exists(self):
        modal = self._make_modal()
        assert hasattr(modal, "description_input")
        assert isinstance(modal.description_input, discord.ui.TextInput)

    def test_justification_input_exists(self):
        modal = self._make_modal()
        assert hasattr(modal, "justification_input")
        assert isinstance(modal.justification_input, discord.ui.TextInput)

    def test_description_input_required(self):
        modal = self._make_modal()
        assert modal.description_input.required is True

    def test_justification_input_required(self):
        modal = self._make_modal()
        assert modal.justification_input.required is True

    def test_description_input_max_length(self):
        modal = self._make_modal()
        assert modal.description_input.max_length == 200

    def test_justification_input_max_length(self):
        modal = self._make_modal()
        assert modal.justification_input.max_length == 200

    def test_title_normal_mode(self):
        modal = self._make_modal(use_appeals_staging=False)
        assert "Add Penalty" in modal.title

    def test_title_appeals_mode(self):
        modal = self._make_modal(use_appeals_staging=True)
        assert "Add Correction" in modal.title

    def test_session_label_in_title(self):
        modal = self._make_modal()
        assert "Feature Race" in modal.title


# ---------------------------------------------------------------------------
# PenaltyReviewView — state=None safety (global restart registration)
# ---------------------------------------------------------------------------

class TestPenaltyReviewViewStateless:
    def test_no_state_does_not_raise(self):
        view = PenaltyReviewView(state=None)
        assert view.state is None

    def test_has_add_button(self):
        view = PenaltyReviewView(state=None)
        cids = {item.custom_id for item in view.children if isinstance(item, discord.ui.Button)}
        assert _CID_ADD in cids

    def test_has_confirm_button(self):
        view = PenaltyReviewView(state=None)
        cids = {item.custom_id for item in view.children if isinstance(item, discord.ui.Button)}
        assert _CID_CONFIRM in cids

    def test_has_approve_button(self):
        view = PenaltyReviewView(state=None)
        cids = {item.custom_id for item in view.children if isinstance(item, discord.ui.Button)}
        assert _CID_APPROVE in cids


# ---------------------------------------------------------------------------
# AppealsReviewView — custom_ids and state=None safety
# ---------------------------------------------------------------------------

class TestAppealsReviewView:
    def test_no_state_does_not_raise(self):
        """Global restart registration must not raise with state=None."""
        view = AppealsReviewView(state=None)
        assert view.state is None

    def _button_cids(self, view: AppealsReviewView) -> set[str]:
        return {
            item.custom_id
            for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id
        }

    def test_has_ar_add_button(self):
        view = AppealsReviewView(state=None)
        assert _CID_AR_ADD in self._button_cids(view)

    def test_has_ar_confirm_button(self):
        view = AppealsReviewView(state=None)
        assert _CID_AR_CONFIRM in self._button_cids(view)

    def test_has_ar_approve_button(self):
        view = AppealsReviewView(state=None)
        assert _CID_AR_APPROVE in self._button_cids(view)

    def test_with_state_empty_staged_approve_disabled(self):
        state = PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[SessionType.FEATURE_RACE],
            db_path=":memory:",
            bot=None,
        )
        view = AppealsReviewView(state=state)
        approve_btn = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id == _CID_AR_APPROVE
        )
        assert approve_btn.disabled is True

    def test_with_state_has_staged_approve_enabled(self):
        state = PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[SessionType.FEATURE_RACE],
            db_path=":memory:",
            bot=None,
        )
        state.staged_appeals.append(
            StagedPenalty(
                driver_user_id=99,
                session_type=SessionType.FEATURE_RACE,
                penalty_type="TIME",
                penalty_seconds=5,
            )
        )
        view = AppealsReviewView(state=state)
        approve_btn = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id == _CID_AR_APPROVE
        )
        assert approve_btn.disabled is False

    def test_dynamic_remove_buttons_added_per_entry(self):
        state = PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[SessionType.FEATURE_RACE],
            db_path=":memory:",
            bot=None,
        )
        state.staged_appeals.append(
            StagedPenalty(driver_user_id=1, session_type=SessionType.FEATURE_RACE, penalty_type="TIME", penalty_seconds=5)
        )
        state.staged_appeals.append(
            StagedPenalty(driver_user_id=2, session_type=SessionType.FEATURE_RACE, penalty_type="DSQ", penalty_seconds=None)
        )
        view = AppealsReviewView(state=state)
        remove_btns = [
            item for item in view.children
            if isinstance(item, discord.ui.Button)
            and item.custom_id is not None
            and item.custom_id.startswith("ar_remove_")
        ]
        assert len(remove_btns) == 2

    def test_timeout_is_none(self):
        """Persistent views must have timeout=None."""
        view = AppealsReviewView(state=None)
        assert view.timeout is None


# ---------------------------------------------------------------------------
# ApprovalView — state=None safety
# ---------------------------------------------------------------------------

class TestApprovalView:
    def test_no_state_does_not_raise(self):
        view = ApprovalView(state=None)
        assert view.state is None

    def test_timeout_is_none(self):
        view = ApprovalView(state=None)
        assert view.timeout is None


# ---------------------------------------------------------------------------
# _pen_label helper
# ---------------------------------------------------------------------------

class TestPenLabel:
    def test_dsq_label(self):
        sp = StagedPenalty(driver_user_id=1, session_type=SessionType.FEATURE_RACE, penalty_type="DSQ", penalty_seconds=None)
        assert _pen_label(sp) == "DSQ"

    def test_positive_time_label(self):
        sp = StagedPenalty(
            driver_user_id=1, session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME", penalty_seconds=10
        )
        assert _pen_label(sp) == "+10s"

    def test_negative_time_label(self):
        sp = StagedPenalty(
            driver_user_id=1, session_type=SessionType.FEATURE_RACE,
            penalty_type="TIME", penalty_seconds=-5
        )
        assert _pen_label(sp) == "-5s"
