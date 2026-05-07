
import pytest
from maxwell.p2p import P2PManager, ProviderInfo

@pytest.fixture
def p2p_manager():
    return P2PManager(node_id="test_node", role="consumer", api_port=8080)

def test_report_failure(p2p_manager):
    node_id = "provider_1"
    provider = ProviderInfo(node_id=node_id, host="127.0.0.1", port=9000, price=1.0, model="7B")
    p2p_manager.providers[node_id] = provider

    initial_reputation = provider.reputation
    p2p_manager.report_failure(node_id)

    assert provider.reputation == initial_reputation - 20.0
    assert provider.reputation == 80.0

def test_report_failure_min_bound(p2p_manager):
    node_id = "provider_1"
    provider = ProviderInfo(node_id=node_id, host="127.0.0.1", port=9000, price=1.0, model="7B")
    provider.reputation = 10.0
    p2p_manager.providers[node_id] = provider

    p2p_manager.report_failure(node_id)

    assert provider.reputation == 0.0

def test_report_success(p2p_manager):
    node_id = "provider_1"
    provider = ProviderInfo(node_id=node_id, host="127.0.0.1", port=9000, price=1.0, model="7B")
    p2p_manager.providers[node_id] = provider

    initial_reputation = provider.reputation
    p2p_manager.report_success(node_id)

    # reputation starts at 100.0, and min(100.0, 100.0 + 2.0) is 100.0
    assert provider.reputation == 100.0

    provider.reputation = 50.0
    p2p_manager.report_success(node_id)
    assert provider.reputation == 52.0

def test_report_success_max_bound(p2p_manager):
    node_id = "provider_1"
    provider = ProviderInfo(node_id=node_id, host="127.0.0.1", port=9000, price=1.0, model="7B")
    provider.reputation = 99.0
    p2p_manager.providers[node_id] = provider

    p2p_manager.report_success(node_id)

    assert provider.reputation == 100.0

def test_report_methods_nonexistent_node(p2p_manager):
    # Should not raise exception
    p2p_manager.report_failure("nonexistent")
    p2p_manager.report_success("nonexistent")

def test_get_best_provider_reputation_impact(p2p_manager):
    p1 = ProviderInfo(node_id="p1", host="127.0.0.1", port=9000, price=1.0, model="7B")
    p2 = ProviderInfo(node_id="p2", host="127.0.0.1", port=9001, price=1.0, model="7B")

    p2p_manager.providers["p1"] = p1
    p2p_manager.providers["p2"] = p2

    # Both have 100.0 reputation.
    # Slash p1.
    p2p_manager.report_failure("p1") # p1: 80.0, p2: 100.0

    best = p2p_manager.get_best_provider()
    assert best.node_id == "p2"

    # Slash p2 twice.
    p2p_manager.report_failure("p2") # p2: 80.0
    p2p_manager.report_failure("p2") # p2: 60.0

    best = p2p_manager.get_best_provider()
    assert best.node_id == "p1"

def test_get_best_provider_zero_reputation(p2p_manager):
    p1 = ProviderInfo(node_id="p1", host="127.0.0.1", port=9000, price=1.0, model="7B")
    p1.reputation = 0.0
    p2p_manager.providers["p1"] = p1

    assert p2p_manager.get_best_provider() is None

def test_get_best_provider_empty(p2p_manager):
    assert p2p_manager.get_best_provider() is None
