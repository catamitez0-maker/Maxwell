"""
maxwell-oracle — Compute metering engine (Phase 2).

Provides FLOPs estimation for Transformer-based AI inference tasks
and a limiter to enforce per-task compute budgets.

Core formula (Kaplan et al., 2020):
    FLOPs ≈ 2 × N × S
Where:
    N = number of model parameters
    S = sequence length (input + output tokens)

This module is the "precision ruler" for fair compute pricing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

__all__ = ["estimate_flops", "FLOPsLimiter", "FLOPsExceeded", "TaskBudget"]

logger = logging.getLogger("maxwell.oracle")


# ── Well-known model parameter counts ──────────────────────────────

MODEL_PARAMS: dict[str, int] = {
    "gpt2": 117_000_000,
    "gpt2-medium": 345_000_000,
    "gpt2-large": 774_000_000,
    "gpt2-xl": 1_500_000_000,
    "llama-7b": 7_000_000_000,
    "llama-13b": 13_000_000_000,
    "llama-70b": 70_000_000_000,
    "mixtral-8x7b": 47_000_000_000,
}


@dataclass
class FLOPsEstimate:
    """Result of a FLOPs estimation."""
    model_params: int
    seq_length: int
    forward_flops: float
    total_flops: float         # includes backward pass if training
    estimated_cost_usd: float  # at a given $/PetaFLOP rate


class FLOPsExceeded(Exception):
    """Raised when a task exceeds its compute budget."""
    def __init__(self, limit: float, estimated: float) -> None:
        self.limit = limit
        self.estimated = estimated
        super().__init__(
            f"FLOPs budget exceeded: {estimated:.2e} > limit {limit:.2e}"
        )


def estimate_flops(
    model_params: int,
    seq_length: int,
    is_training: bool = False,
    cost_per_petaflop: float = 0.03,
) -> FLOPsEstimate:
    """
    Estimate FLOPs for a Transformer inference/training pass.

    The forward pass formula: FLOPs_fwd ≈ 2 × N × S
    Training adds ~3× overhead (forward + backward + optimizer).

    Args:
        model_params: number of model parameters (N)
        seq_length: total sequence length in tokens (S)
        is_training: if True, estimate includes backward pass
        cost_per_petaflop: USD cost per PetaFLOP for pricing

    Returns:
        FLOPsEstimate with breakdown
    """
    forward_flops = 2.0 * model_params * seq_length

    if is_training:
        total_flops = forward_flops * 3.0  # fwd + bwd + optimizer step
    else:
        total_flops = forward_flops

    # 1 PetaFLOP = 1e15 FLOPs
    cost = (total_flops / 1e15) * cost_per_petaflop

    return FLOPsEstimate(
        model_params=model_params,
        seq_length=seq_length,
        forward_flops=forward_flops,
        total_flops=total_flops,
        estimated_cost_usd=cost,
    )


class TaskBudget:
    """
    Stateful budget tracker for a single streaming task.
    """
    def __init__(self, limit: float, model_params: int, input_tokens: int) -> None:
        self.limit = limit
        self.model_params = model_params
        self.input_tokens = input_tokens
        self.output_tokens = 0
        
        # Initial consumption based on prefill
        est = estimate_flops(model_params, input_tokens)
        self.consumed_flops = est.total_flops
        
        if self.consumed_flops > self.limit:
            raise FLOPsExceeded(self.limit, self.consumed_flops)
            
    def consume_tokens(self, num_tokens: int = 1) -> None:
        """Consume FLOPs for generated output tokens."""
        self.output_tokens += num_tokens
        # Decoding FLOPs: 2 * N * tokens
        flops = 2.0 * self.model_params * num_tokens
        self.consumed_flops += flops
        
        if self.consumed_flops > self.limit:
            raise FLOPsExceeded(self.limit, self.consumed_flops)


class FLOPsLimiter:
    """
    Per-task compute budget enforcer.

    Sets a FLOPs ceiling based on model size and max sequence length.
    Tasks exceeding the budget are rejected before execution.
    """

    def __init__(
        self,
        model_params: int,
        max_seq_length: int = 8192,
        safety_margin: float = 1.2,
    ) -> None:
        self.model_params = model_params
        self.max_seq_length = max_seq_length
        self.safety_margin = safety_margin

        # Pre-compute the ceiling
        base = estimate_flops(model_params, max_seq_length)
        self.flops_limit: float = base.total_flops * safety_margin

        logger.info(
            "FLOPs limiter: model=%d params, max_seq=%d, limit=%.2e FLOPs",
            model_params, max_seq_length, self.flops_limit,
        )

    def check(self, seq_length: int) -> FLOPsEstimate:
        """
        Estimate FLOPs for a given sequence and enforce the budget.

        Raises FLOPsExceeded if the estimate exceeds the limit.
        """
        est = estimate_flops(self.model_params, seq_length)
        if est.total_flops > self.flops_limit:
            raise FLOPsExceeded(self.flops_limit, est.total_flops)
        return est

    def remaining_budget(self, seq_length: int) -> float:
        """Return remaining FLOPs budget for a given sequence."""
        est = estimate_flops(self.model_params, seq_length)
        return max(0.0, self.flops_limit - est.total_flops)
        
    def create_task_budget(self, input_tokens: int) -> TaskBudget:
        """Create a stateful tracker for a streaming task."""
        return TaskBudget(self.flops_limit, self.model_params, input_tokens)
