"""Tests for penalty_wizard.py structural contracts.

Covers:
- PenaltyReviewState dataclass fields
- AddPenaltyModal fields and constructor
- AppealsReviewView button custom_ids and state=None safety
- _AppealsConfirmClearView existence and button styles

`_pen_label` is covered in `test_penalty_wizard_rendering.py`.

Every test that constructs a Modal or a View is ``async def``, and must stay that way.
discord.py 2.5.0 builds a View's ``__stopped`` future in ``View.__init__`` with a bare
``asyncio.get_running_loop()``, so constructing one without a live loop raises
``RuntimeError: no running event loop``. 2.6 onwards wraps that call in ``try/except`` and
falls back to ``None``, which is why these pass as plain ``def`` against a recent discord.py
and fail against an older one — a host installing the library from Debian's apt packages
rather than from ``requirements.txt`` gets 2.5.0 and sees all of them fail at once.

``pytest.ini`` sets ``asyncio_mode = auto``, so an ``async def`` body runs inside a live loop
and satisfies every discord.py 2.x. Pinning the dependency would not do this: the pin governs
what pip installs, not what a system-packaged interpreter imports.
"""
from __future__ import annotations

import pytest

import discord

from leaguebot.results.services.penalty_wizard import (
    PenaltyReviewState,
    PenaltyReviewView,
    ApprovalView,
    AppealsReviewView,
    AddPenaltyModal,
    _CID_AR_ADD,
    _CID_AR_CONFIRM,
    _CID_AR_APPROVE,
    _CID_ADD,
    _CID_CONFIRM,
    _CID_APPROVE,
)
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.models.points_config import SessionType


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

    @pytest.mark.parametrize(
        "field, default",
        [
            ("staged", []),
            ("staged_appeals", []),
            ("prompt_message_id", None),
            ("appeals_prompt_message_id", None),
            ("round_number", 0),
            ("division_name", ""),
        ],
        ids=[
            "staged",
            "staged_appeals",
            "prompt_message_id",
            "appeals_prompt_message_id",
            "round_number",
            "division_name",
        ],
    )
    def test_each_field_has_its_default(self, field, default):
        state = self._make_state()
        if default is None:
            assert getattr(state, field) is None
        else:
            assert getattr(state, field) == default

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

    @pytest.mark.parametrize(
        "field", ["driver_input", "penalty_input", "description_input", "justification_input"]
    )
    async def test_each_input_is_a_text_input(self, field):
        modal = self._make_modal()
        assert hasattr(modal, field)
        assert isinstance(getattr(modal, field), discord.ui.TextInput)

    @pytest.mark.parametrize("field", ["description_input", "justification_input"])
    async def test_the_text_input_is_required(self, field):
        modal = self._make_modal()
        assert getattr(modal, field).required is True

    @pytest.mark.parametrize("field", ["description_input", "justification_input"])
    async def test_the_text_input_takes_at_most_200_characters(self, field):
        modal = self._make_modal()
        assert getattr(modal, field).max_length == 200

    @pytest.mark.parametrize(
        "use_appeals_staging, word",
        [(False, "Add Penalty"), (True, "Add Correction")],
        ids=["penalty", "appeals"],
    )
    async def test_the_title_names_the_mode(self, use_appeals_staging, word):
        modal = self._make_modal(use_appeals_staging=use_appeals_staging)
        assert word in modal.title

    async def test_session_label_in_title(self):
        modal = self._make_modal()
        assert "Feature Race" in modal.title


# ---------------------------------------------------------------------------
# PenaltyReviewView — state=None safety (global restart registration)
# ---------------------------------------------------------------------------

class TestPenaltyReviewViewStateless:
    async def test_no_state_does_not_raise(self):
        view = PenaltyReviewView(state=None)
        assert view.state is None

    @pytest.mark.parametrize(
        "cid", [_CID_ADD, _CID_CONFIRM, _CID_APPROVE], ids=["add", "confirm", "approve"]
    )
    async def test_has_each_button(self, cid):
        view = PenaltyReviewView(state=None)
        cids = {item.custom_id for item in view.children if isinstance(item, discord.ui.Button)}
        assert cid in cids


# ---------------------------------------------------------------------------
# AppealsReviewView — custom_ids and state=None safety
# ---------------------------------------------------------------------------

class TestAppealsReviewView:
    async def test_no_state_does_not_raise(self):
        """Global restart registration must not raise with state=None."""
        view = AppealsReviewView(state=None)
        assert view.state is None

    def _button_cids(self, view: AppealsReviewView) -> set[str]:
        return {
            item.custom_id
            for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id
        }

    @pytest.mark.parametrize(
        "cid",
        [_CID_AR_ADD, _CID_AR_CONFIRM, _CID_AR_APPROVE],
        ids=["add", "confirm", "approve"],
    )
    async def test_has_each_ar_button(self, cid):
        view = AppealsReviewView(state=None)
        assert cid in self._button_cids(view)

    @pytest.mark.parametrize(
        "staged_appeals, disabled",
        [
            ([], True),
            (
                [
                    StagedPenalty(
                        driver_user_id=99,
                        session_type=SessionType.FEATURE_RACE,
                        penalty_type="TIME",
                        penalty_seconds=5,
                    )
                ],
                False,
            ),
        ],
        ids=["nothing-staged", "a-correction-staged"],
    )
    async def test_approve_is_enabled_only_with_a_correction_staged(
        self, staged_appeals, disabled
    ):
        state = PenaltyReviewState(
            round_id=1,
            division_id=2,
            submission_channel_id=3,
            session_types_present=[SessionType.FEATURE_RACE],
            db_path=":memory:",
            bot=None,
        )
        state.staged_appeals.extend(staged_appeals)
        view = AppealsReviewView(state=state)
        approve_btn = next(
            item for item in view.children
            if isinstance(item, discord.ui.Button) and item.custom_id == _CID_AR_APPROVE
        )
        assert approve_btn.disabled is disabled

    async def test_dynamic_remove_buttons_added_per_entry(self):
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

    async def test_timeout_is_none(self):
        """Persistent views must have timeout=None."""
        view = AppealsReviewView(state=None)
        assert view.timeout is None


# ---------------------------------------------------------------------------
# ApprovalView — state=None safety
# ---------------------------------------------------------------------------

class TestApprovalView:
    async def test_no_state_does_not_raise(self):
        view = ApprovalView(state=None)
        assert view.state is None

    async def test_timeout_is_none(self):
        view = ApprovalView(state=None)
        assert view.timeout is None
