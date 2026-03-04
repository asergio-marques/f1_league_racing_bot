"""Unit tests for math_utils.py — all formulas are pure functions."""

from __future__ import annotations

import math
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.math_utils import (
    compute_rpc,
    compute_ir,
    compute_im,
    compute_is,
    build_slot_pool,
    clamp_weight,
    get_phase3_weights,
    draw_weighted,
    weights_sunny,
    weights_mixed,
    weights_rain,
)


class TestComputeRpc:
    def test_basic_calculation(self) -> None:
        # btrack=0.05, dice=1,1: (0.05 * 1 * 1) / 3.025 ≈ 0.0165 → rounds to 0.02
        result = compute_rpc(0.05, 1, 1)
        assert result == round((0.05 * 1 * 1) / 3.025, 2)

    def test_clamps_to_zero(self) -> None:
        # Artificially: btrack extreme values that might push < 0
        result = compute_rpc(0.0, 1, 1)
        assert result == 0.0

    def test_clamps_to_one(self) -> None:
        # Very large inputs should clamp to 1.0
        result = compute_rpc(1.0, 98, 98)
        assert result == 1.0

    def test_result_within_bounds_for_typical_values(self) -> None:
        for btrack in [0.05, 0.10, 0.25, 0.30]:
            for r in range(1, 99, 20):
                rpc = compute_rpc(btrack, r, r)
                assert 0.0 <= rpc <= 1.0, f"Out of bounds: btrack={btrack}, r={r}, rpc={rpc}"


class TestSlotDistribution:
    def test_ir_non_negative(self) -> None:
        for rpc in [0.0, 0.05, 0.5, 1.0]:
            assert compute_ir(rpc) >= 0

    def test_im_non_negative(self) -> None:
        for rpc in [0.0, 0.05, 0.5, 1.0]:
            ir = compute_ir(rpc)
            assert compute_im(rpc, ir) >= 0

    def test_is_non_negative(self) -> None:
        for rpc in [0.0, 0.05, 0.5, 1.0]:
            ir = compute_ir(rpc)
            im = compute_im(rpc, ir)
            assert compute_is(im, ir) >= 0

    def test_pool_is_always_1000(self) -> None:
        for rpc in [0.0, 0.1, 0.5, 0.9, 1.0]:
            ir = compute_ir(rpc)
            im = compute_im(rpc, ir)
            is_ = compute_is(im, ir)
            pool = build_slot_pool(ir, im, is_)
            assert len(pool) == 1000, f"Pool length {len(pool)} for rpc={rpc}"

    def test_pool_contains_only_valid_types(self) -> None:
        pool = build_slot_pool(200, 500, 300)
        assert set(pool) <= {"rain", "mixed", "sunny"}

    def test_ir_formula(self) -> None:
        # Rpc = 0.5: floor((1000 * 0.5 * (1+0.5)^2) / 5)
        #          = floor((1000 * 0.5 * 2.25) / 5) = floor(1125 / 5) = 225
        assert compute_ir(0.5) == 225

    def test_im_formula(self) -> None:
        # Rpc = 0.5: floor(500) - 150 = 350
        assert compute_im(0.5, 150) == 350

    def test_is_formula(self) -> None:
        # 1000 - 350 - 150 = 500
        assert compute_is(350, 150) == 500


class TestClampWeight:
    def test_clamp_negative(self) -> None:
        assert clamp_weight(-5.0) == 0.0

    def test_clamp_zero(self) -> None:
        assert clamp_weight(0.0) == 0.0

    def test_positive_unchanged(self) -> None:
        assert clamp_weight(42.5) == 42.5


class TestPhase3WeightFunctions:
    """Verify spec formulas produce correct values and all results are >= 0."""

    _WEATHER_TYPES = {"Clear", "Light Cloud", "Overcast", "Wet", "Very Wet"}

    def test_sunny_keys(self) -> None:
        assert set(weights_sunny(0.5).keys()) == self._WEATHER_TYPES

    def test_mixed_keys(self) -> None:
        assert set(weights_mixed(0.5).keys()) == self._WEATHER_TYPES

    def test_rain_keys(self) -> None:
        assert set(weights_rain(0.5).keys()) == self._WEATHER_TYPES

    def test_sunny_zero_prain(self) -> None:
        w = weights_sunny(0.0)
        # prain=0: Clear=60, Light Cloud=25, Overcast=15, Wet=0, Very Wet=0
        assert w["Clear"] == pytest.approx(60.0)
        assert w["Light Cloud"] == pytest.approx(25.0)
        assert w["Overcast"] == pytest.approx(15.0)
        assert w["Wet"] == 0.0
        assert w["Very Wet"] == 0.0

    def test_rain_has_no_dry_weather(self) -> None:
        for prain in [0.0, 0.3, 0.7, 1.0]:
            w = weights_rain(prain)
            assert w["Clear"] == 0.0
            assert w["Light Cloud"] == 0.0
            assert w["Overcast"] == 0.0

    def test_sunny_has_no_wet_weather(self) -> None:
        for prain in [0.0, 0.3, 0.7, 1.0]:
            w = weights_sunny(prain)
            assert w["Wet"] == 0.0
            assert w["Very Wet"] == 0.0

    def test_all_weights_non_negative(self) -> None:
        funcs = [weights_sunny, weights_mixed, weights_rain]
        for fn in funcs:
            for prain in [0.0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
                w = fn(prain)
                for label, val in w.items():
                    assert val >= 0.0, f"{fn.__name__}(prain={prain})[{label!r}] = {val}"

    def test_overcast_mixed_formula(self) -> None:
        # From spec: Overcast (mixed) = 40 + (30*p) - (70*p^1.7), clamped >= 0
        prain = 0.5
        expected = max(0.0, 40 + (30 * 0.5) - (70 * (0.5 ** 1.7)))
        assert weights_mixed(prain)["Overcast"] == pytest.approx(expected)

    def test_wet_rain_formula(self) -> None:
        # From spec: Wet (rain) = 100 - (40*p^2) - (13*p^4), clamped >= 0
        prain = 0.5
        expected = max(0.0, 100 - (40 * 0.5 ** 2) - (13 * 0.5 ** 4))
        assert weights_rain(prain)["Wet"] == pytest.approx(expected)


class TestPhase3Weights:
    def test_returns_correct_keys(self) -> None:
        expected = {"Clear", "Light Cloud", "Overcast", "Wet", "Very Wet"}
        for slot in ["rain", "mixed", "sunny"]:
            w = get_phase3_weights(slot, 0.5)
            assert set(w.keys()) == expected

    def test_all_weights_non_negative(self) -> None:
        for slot in ["rain", "mixed", "sunny"]:
            for prain in [0.0, 0.3, 0.7, 1.0]:
                weights = get_phase3_weights(slot, prain)
                for label, w in weights.items():
                    assert w >= 0.0, (
                        f"Negative weight {w} for {label!r} "
                        f"(slot={slot}, prain={prain})"
                    )

    def test_unknown_slot_raises(self) -> None:
        with pytest.raises(ValueError):
            get_phase3_weights("unknown", 0.5)


class TestDrawWeighted:
    def test_returns_label_from_weights(self) -> None:
        weights = {"Sunny": 80.0, "Cloudy": 20.0}
        for _ in range(100):
            result = draw_weighted(weights)
            assert result in weights

    def test_all_zero_fallback(self) -> None:
        import random
        rng = random.Random(42)
        weights = {"A": 0.0, "B": 0.0}
        # Should not raise; falls back to equal distribution
        result = draw_weighted(weights, rng)
        assert result in {"A", "B"}
