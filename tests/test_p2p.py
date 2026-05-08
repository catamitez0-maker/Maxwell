"""
Tests for maxwell.p2p — P2P discovery and routing.
"""

import asyncio
import time

import pytest

from maxwell.p2p import P2PManager, ProviderInfo


class TestProviderInfo:
    def test_default_values(self) -> None:
        p = ProviderInfo(
            node_id="node-1", host="10.0.0.1", port=8080, price=1.5, model="7B",
        )
        assert p.node_id == "node-1"
        assert p.reputation == 100.0
        assert p.last_seen <= time.time()

    def test_custom_values(self) -> None:
        p = ProviderInfo(
            node_id="node-2", host="192.168.1.1", port=9090, price=0.5, model="13B",
        )
        assert p.port == 9090
        assert p.price == 0.5
        assert p.model == "13B"


class TestP2PManagerRouting:
    def _make_manager(self) -> P2PManager:
        mgr = P2PManager(
            node_id="consumer-1", role="consumer", api_port=8080,
        )
        return mgr

    def test_get_best_provider_empty(self) -> None:
        mgr = self._make_manager()
        assert mgr.get_best_provider() is None

    def test_get_best_provider_by_reputation(self) -> None:
        mgr = self._make_manager()
        mgr.providers["a"] = ProviderInfo("a", "1.1.1.1", 8080, 1.0, "7B")
        mgr.providers["b"] = ProviderInfo("b", "2.2.2.2", 8080, 1.0, "7B")
        mgr.providers["a"].reputation = 80.0
        mgr.providers["b"].reputation = 95.0
        best = mgr.get_best_provider()
        assert best is not None
        assert best.node_id == "b"

    def test_get_best_provider_by_price(self) -> None:
        mgr = self._make_manager()
        mgr.providers["a"] = ProviderInfo("a", "1.1.1.1", 8080, 10.0, "7B")
        mgr.providers["b"] = ProviderInfo("b", "2.2.2.2", 8080, 1.0, "7B")
        # Same reputation, different price → cheaper one wins (reputation/price)
        best = mgr.get_best_provider()
        assert best is not None
        assert best.node_id == "b"

    def test_get_best_provider_skips_zero_reputation(self) -> None:
        mgr = self._make_manager()
        mgr.providers["a"] = ProviderInfo("a", "1.1.1.1", 8080, 1.0, "7B")
        mgr.providers["a"].reputation = 0.0
        mgr.providers["b"] = ProviderInfo("b", "2.2.2.2", 8080, 1.0, "7B")
        best = mgr.get_best_provider()
        assert best is not None
        assert best.node_id == "b"


class TestReputationSystem:
    def _make_manager_with_provider(self) -> tuple[P2PManager, str]:
        mgr = P2PManager(
            node_id="consumer-1", role="consumer", api_port=8080,
        )
        mgr.providers["p1"] = ProviderInfo("p1", "1.1.1.1", 8080, 1.0, "7B")
        return mgr, "p1"

    def test_report_success_increases_reputation(self) -> None:
        mgr, nid = self._make_manager_with_provider()
        mgr.providers[nid].reputation = 90.0
        mgr.report_success(nid)
        assert mgr.providers[nid].reputation == 92.0

    def test_report_success_caps_at_100(self) -> None:
        mgr, nid = self._make_manager_with_provider()
        mgr.providers[nid].reputation = 99.5
        mgr.report_success(nid)
        assert mgr.providers[nid].reputation == 100.0

    def test_report_failure_decreases_reputation(self) -> None:
        mgr, nid = self._make_manager_with_provider()
        mgr.report_failure(nid)
        assert mgr.providers[nid].reputation == 80.0

    def test_report_failure_floors_at_zero(self) -> None:
        mgr, nid = self._make_manager_with_provider()
        mgr.providers[nid].reputation = 10.0
        mgr.report_failure(nid)
        assert mgr.providers[nid].reputation == 0.0

    def test_report_on_unknown_node_is_noop(self) -> None:
        mgr = P2PManager(node_id="c", role="consumer", api_port=8080)
        mgr.report_success("unknown")  # Should not raise
        mgr.report_failure("unknown")  # Should not raise


class TestCleanup:
    @pytest.mark.asyncio
    async def test_stale_providers_removed(self) -> None:
        mgr = P2PManager(
            node_id="consumer-1", role="consumer", api_port=8080,
        )
        mgr._running = True
        mgr.providers["stale"] = ProviderInfo("stale", "1.1.1.1", 8080, 1.0, "7B")
        mgr.providers["stale"].last_seen = time.time() - 200  # stale
        mgr.providers["fresh"] = ProviderInfo("fresh", "2.2.2.2", 8080, 1.0, "7B")

        # Run one cleanup iteration
        cleanup_task = asyncio.create_task(mgr._cleanup_loop())
        await asyncio.sleep(0.1)
        mgr._running = False
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass

        assert "stale" not in mgr.providers
        assert "fresh" in mgr.providers
