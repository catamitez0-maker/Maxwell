"""
maxwell.hardware — Hardware Telemetry & Metering (NVML)

This module implements low-level hardware monitoring to capture real energy
consumption (Joules) and memory bandwidth usage during task execution.
Uses pynvml if an NVIDIA GPU is available; otherwise, provides theoretical
CPU fallbacks.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Any

try:
    import pynvml # type: ignore
    HAS_NVML = True
except ImportError:
    HAS_NVML = False

logger = logging.getLogger("maxwell.hardware")

@dataclass
class HardwareMetrics:
    energy_joules: float
    avg_utilization_percent: float
    max_memory_mb: float
    duration_seconds: float

class HardwareMonitor:
    def __init__(self) -> None:
        self.nvml_initialized = False
        self.device_count = 0
        self.handle: Optional[Any] = None
        
        if HAS_NVML:
            try:
                pynvml.nvmlInit()
                self.device_count = pynvml.nvmlDeviceGetCount()
                if self.device_count > 0:
                    self.handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self.nvml_initialized = True
                    logger.info("NVML Initialized. Found %d NVIDIA GPUs.", self.device_count)
            except pynvml.NVMLError as e:
                logger.warning("Failed to initialize NVML: %s. Falling back to CPU simulation.", e)

    def _get_power_watts(self) -> float:
        if self.nvml_initialized and self.handle:
            try:
                # Returns power usage in milliwatts
                power_mw = float(pynvml.nvmlDeviceGetPowerUsage(self.handle))
                return power_mw / 1000.0
            except pynvml.NVMLError:
                pass
        return 50.0  # Simulated 50W base power for CPU

    def _get_utilization(self) -> float:
        if self.nvml_initialized and self.handle:
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(self.handle)
                return float(util.gpu)
            except pynvml.NVMLError:
                pass
        return 100.0

    def _get_memory_used_mb(self) -> float:
        if self.nvml_initialized and self.handle:
            try:
                info = pynvml.nvmlDeviceGetMemoryInfo(self.handle)
                return float(info.used) / (1024 * 1024)
            except pynvml.NVMLError:
                pass
        return 1024.0

    def start_measurement(self) -> 'MeasurementSession':
        return MeasurementSession(self)

class MeasurementSession:
    _MIN_SAMPLE_INTERVAL = 0.1  # 100ms minimum between NVML calls

    def __init__(self, monitor: HardwareMonitor) -> None:
        self.monitor = monitor
        self.start_time = time.time()
        self.samples = 0
        self.total_power_watts = 0.0
        self.total_utilization = 0.0
        self.max_memory = 0.0
        self._last_sample_time = 0.0
        
        self._force_sample()

    def _force_sample(self) -> None:
        """Unconditionally record a hardware sample."""
        self.total_power_watts += self.monitor._get_power_watts()
        self.total_utilization += self.monitor._get_utilization()
        mem = self.monitor._get_memory_used_mb()
        if mem > self.max_memory:
            self.max_memory = mem
        self.samples += 1
        self._last_sample_time = time.time()

    def record_sample(self) -> None:
        """Called periodically or incrementally to sample hardware.
        Rate-limited to avoid excessive NVML calls."""
        now = time.time()
        if now - self._last_sample_time < self._MIN_SAMPLE_INTERVAL:
            return
        self._force_sample()

    def stop_and_report(self) -> HardwareMetrics:
        self._force_sample()
        duration = max(0.001, time.time() - self.start_time)
        
        avg_power = self.total_power_watts / self.samples
        avg_util = self.total_utilization / self.samples
        
        # Power (W) * Time (s) = Energy (Joules)
        energy_joules = avg_power * duration
        
        return HardwareMetrics(
            energy_joules=energy_joules,
            avg_utilization_percent=avg_util,
            max_memory_mb=self.max_memory,
            duration_seconds=duration
        )

# Global monitor instance
hardware_monitor = HardwareMonitor()
