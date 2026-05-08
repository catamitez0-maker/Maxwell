"""
Tests for maxwell.hardware — GPU/CPU hardware telemetry.
"""

import time

import pytest

from maxwell.hardware import HardwareMonitor, MeasurementSession, HardwareMetrics


class TestHardwareMonitor:
    def test_init_fallback_without_gpu(self) -> None:
        """HardwareMonitor should initialize without GPU (CPU fallback)."""
        monitor = HardwareMonitor()
        # Should not raise; fallback to CPU simulation
        assert monitor.device_count >= 0

    def test_power_returns_positive(self) -> None:
        monitor = HardwareMonitor()
        power = monitor._get_power_watts()
        assert power > 0

    def test_utilization_in_range(self) -> None:
        monitor = HardwareMonitor()
        util = monitor._get_utilization()
        assert 0 <= util <= 100

    def test_memory_non_negative(self) -> None:
        monitor = HardwareMonitor()
        mem = monitor._get_memory_used_mb()
        assert mem >= 0


class TestMeasurementSession:
    def test_initial_sample_on_create(self) -> None:
        monitor = HardwareMonitor()
        session = monitor.start_measurement()
        assert session.samples >= 1
        assert session.total_power_watts > 0

    def test_record_sample_rate_limited(self) -> None:
        monitor = HardwareMonitor()
        session = monitor.start_measurement()
        initial_samples = session.samples
        # Rapid consecutive calls should be rate-limited (<100ms apart)
        session.record_sample()
        session.record_sample()
        session.record_sample()
        # At most 1 additional sample should have been recorded (within 100ms)
        assert session.samples <= initial_samples + 1

    def test_force_sample_always_records(self) -> None:
        monitor = HardwareMonitor()
        session = monitor.start_measurement()
        initial_samples = session.samples
        session._force_sample()
        session._force_sample()
        assert session.samples == initial_samples + 2

    def test_stop_and_report_returns_metrics(self) -> None:
        monitor = HardwareMonitor()
        session = monitor.start_measurement()
        time.sleep(0.05)  # Ensure nonzero duration
        metrics = session.stop_and_report()
        assert isinstance(metrics, HardwareMetrics)
        assert metrics.energy_joules > 0
        assert metrics.duration_seconds > 0

    def test_energy_calculation(self) -> None:
        monitor = HardwareMonitor()
        session = monitor.start_measurement()
        time.sleep(0.05)
        metrics = session.stop_and_report()
        # energy_joules = avg_power_watts * duration_seconds
        avg_power = session.total_power_watts / session.samples
        expected_energy = avg_power * metrics.duration_seconds
        assert abs(metrics.energy_joules - expected_energy) < 1.0


class TestHardwareMetrics:
    def test_fields_present(self) -> None:
        m = HardwareMetrics(
            energy_joules=100.0,
            avg_utilization_percent=75.0,
            max_memory_mb=2048.0,
            duration_seconds=5.0,
        )
        assert m.energy_joules == 100.0
        assert m.avg_utilization_percent == 75.0
        assert m.max_memory_mb == 2048.0
        assert m.duration_seconds == 5.0
