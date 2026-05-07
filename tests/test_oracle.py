"""
Unit tests for maxwell.oracle — FLOPs estimation.
"""

import pytest

from maxwell.oracle import estimate_flops, FLOPsLimiter, FLOPsExceeded


class TestEstimateFlops:
    def test_basic_formula(self) -> None:
        """FLOPs_fwd = 2 * N * S"""
        est = estimate_flops(model_params=1_000_000, seq_length=1024)
        expected = 2.0 * 1_000_000 * 1024
        assert est.forward_flops == expected
        assert est.total_flops == expected  # inference only

    def test_training_multiplier(self) -> None:
        """Training should be ~3x forward pass."""
        est = estimate_flops(model_params=1_000_000, seq_length=1024, is_training=True)
        expected_fwd = 2.0 * 1_000_000 * 1024
        assert est.total_flops == expected_fwd * 3.0

    def test_cost_estimation(self) -> None:
        """Cost = total_flops / 1e15 * rate."""
        est = estimate_flops(
            model_params=7_000_000_000,
            seq_length=4096,
            cost_per_petaflop=0.03,
        )
        assert est.estimated_cost_usd > 0

    def test_llama_7b_scale(self) -> None:
        """Sanity check: LLaMA 7B at 4096 tokens."""
        est = estimate_flops(model_params=7_000_000_000, seq_length=4096)
        # 2 * 7e9 * 4096 = ~5.7e13 FLOPs
        assert 5e13 < est.forward_flops < 6e13


class TestFLOPsLimiter:
    def test_within_budget(self) -> None:
        limiter = FLOPsLimiter(model_params=1_000_000, max_seq_length=2048)
        est = limiter.check(seq_length=1024)
        assert est.total_flops > 0

    def test_exceeds_budget(self) -> None:
        limiter = FLOPsLimiter(
            model_params=1_000_000,
            max_seq_length=1024,
            safety_margin=1.0,
        )
        with pytest.raises(FLOPsExceeded):
            limiter.check(seq_length=2048)

    def test_remaining_budget(self) -> None:
        limiter = FLOPsLimiter(model_params=1_000_000, max_seq_length=2048)
        remaining = limiter.remaining_budget(seq_length=1024)
        assert remaining > 0
