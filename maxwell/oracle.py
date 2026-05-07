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

from .hardware import hardware_monitor, HardwareMetrics

__all__ = ["estimate_flops", "FLOPsLimiter", "FLOPsExceeded", "TaskBudget", "ModelConfig", "MODELS", "ComputeReceipt"]

logger = logging.getLogger("maxwell.oracle")

@dataclass
class ModelConfig:
    name: str
    total_params: int
    active_params: int
    is_moe: bool

# ── Well-known model configurations ──────────────────────────────

MODELS: dict[str, ModelConfig] = {
    "gpt2": ModelConfig("gpt2", 117_000_000, 117_000_000, False),
    "llama-7b": ModelConfig("llama-7b", 7_000_000_000, 7_000_000_000, False),
    "llama-13b": ModelConfig("llama-13b", 13_000_000_000, 13_000_000_000, False),
    "llama-70b": ModelConfig("llama-70b", 70_000_000_000, 70_000_000_000, False),
    "mixtral-8x7b": ModelConfig("mixtral-8x7b", 47_000_000_000, 13_000_000_000, True),
}


@dataclass
class FLOPsEstimate:
    """Result of a FLOPs estimation."""
    model_params: int
    seq_length: int
    forward_flops: float
    total_flops: float         # includes backward pass if training
    estimated_cost_usd: float  # at a given $/PetaFLOP rate


@dataclass
class ComputeReceipt:
    """Hybrid receipt merging theoretical math with real hardware telemetry."""
    theoretical_flops: float
    energy_joules: float
    duration_seconds: float
    max_memory_mb: float
    avg_utilization_percent: float


class FLOPsExceeded(Exception):
    """Raised when a task exceeds its compute budget."""
    def __init__(self, limit: float, estimated: float) -> None:
        self.limit = limit
        self.estimated = estimated
        super().__init__(
            f"FLOPs budget exceeded: {estimated:.2e} > limit {limit:.2e}"
        )


def estimate_flops(
    model: ModelConfig,
    seq_length: int,
    is_training: bool = False,
    cost_per_petaflop: float = 0.03,
) -> FLOPsEstimate:
    """
    Estimate FLOPs for a Transformer inference/training pass.

    The forward pass formula: FLOPs_fwd ≈ 2 × N × S
    Training adds ~3× overhead (forward + backward + optimizer).

    Args:
        model: ModelConfig object specifying the architecture
        seq_length: total sequence length in tokens (S)
        is_training: if True, estimate includes backward pass
        cost_per_petaflop: USD cost per PetaFLOP for pricing

    Returns:
        FLOPsEstimate with breakdown
    """
    # For a dense model or prefill of MoE, we typically use total params for simplicity.
    # A true MoE prefill might still use all params or just active. For this metering,
    # we assume decoding uses active_params, and standard formula uses active_params
    # for simplification, but prefill might be more expensive.
    # To keep it standard: 2 * active_params * seq_length.
    forward_flops = 2.0 * model.active_params * seq_length

    if is_training:
        total_flops = forward_flops * 3.0  # fwd + bwd + optimizer step
    else:
        total_flops = forward_flops

    # 1 PetaFLOP = 1e15 FLOPs
    cost = (total_flops / 1e15) * cost_per_petaflop

    return FLOPsEstimate(
        model_params=model.total_params,
        seq_length=seq_length,
        forward_flops=forward_flops,
        total_flops=total_flops,
        estimated_cost_usd=cost,
    )


class TaskBudget:
    """
    Stateful budget tracker for a single streaming task.
    """
    def __init__(self, limit: float, model: ModelConfig, input_tokens: int) -> None:
        self.limit = limit
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = 0
        
        # Start hardware measurement
        self.hw_session = hardware_monitor.start_measurement()
        
        # Initial consumption based on prefill
        est = estimate_flops(model, input_tokens)
        self.consumed_flops = est.total_flops
        
        if self.consumed_flops > self.limit:
            raise FLOPsExceeded(self.limit, self.consumed_flops)
            
    def consume_tokens(self, num_tokens: int = 1) -> None:
        """Consume FLOPs for generated output tokens."""
        self.output_tokens += num_tokens
        # Decoding FLOPs: 2 * active_params * tokens
        flops = 2.0 * self.model.active_params * num_tokens
        self.consumed_flops += flops
        self.hw_session.record_sample()
        if self.consumed_flops > self.limit:
            raise FLOPsExceeded(self.limit, self.consumed_flops)

    def finalize(self) -> ComputeReceipt:
        """Stop hardware measurement and produce a final hybrid receipt."""
        hw_metrics = self.hw_session.stop_and_report()
        return ComputeReceipt(
            theoretical_flops=self.consumed_flops,
            energy_joules=hw_metrics.energy_joules,
            duration_seconds=hw_metrics.duration_seconds,
            max_memory_mb=hw_metrics.max_memory_mb,
            avg_utilization_percent=hw_metrics.avg_utilization_percent,
        )


class FLOPsLimiter:
    """
    Per-task compute budget enforcer.

    Sets a FLOPs ceiling based on model size and max sequence length.
    Tasks exceeding the budget are rejected before execution.
    """

    def __init__(
        self,
        model: ModelConfig,
        max_seq_length: int = 8192,
        safety_margin: float = 1.2,
    ) -> None:
        self.model = model
        self.max_seq_length = max_seq_length
        self.safety_margin = safety_margin

        # Pre-compute the ceiling
        base = estimate_flops(model, max_seq_length)
        self.flops_limit: float = base.total_flops * safety_margin

        logger.info(
            "FLOPs limiter: model=%s (active %d), max_seq=%d, limit=%.2e FLOPs",
            model.name, model.active_params, max_seq_length, self.flops_limit,
        )

    def check(self, seq_length: int) -> FLOPsEstimate:
        """
        Estimate FLOPs for a given sequence and enforce the budget.

        Raises FLOPsExceeded if the estimate exceeds the limit.
        """
        est = estimate_flops(self.model, seq_length)
        if est.total_flops > self.flops_limit:
            raise FLOPsExceeded(self.flops_limit, est.total_flops)
        return est

    def remaining_budget(self, seq_length: int) -> float:
        """Return remaining FLOPs budget for a given sequence."""
        est = estimate_flops(self.model, seq_length)
        return max(0.0, self.flops_limit - est.total_flops)
        
    def create_task_budget(self, input_tokens: int) -> TaskBudget:
        """Create a stateful tracker for a streaming task."""
        return TaskBudget(self.flops_limit, self.model, input_tokens)
