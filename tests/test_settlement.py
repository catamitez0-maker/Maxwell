import pytest
from fastapi.testclient import TestClient

from maxwell.settlement import app, relayer
from maxwell.crypto import TEESimulator

@pytest.fixture
def client():
    # Ensure mock mode and clear state
    relayer.mock_mode = True
    relayer.mock_settled_tasks.clear()
    relayer.mock_balances.clear()

    with TestClient(app) as c:
        yield c

def test_get_balance(client):
    response = client.get("/balances/0x123")
    assert response.status_code == 200
    assert response.json() == {"address": "0x123", "balance": 1000.0}

    # modify mock balance
    relayer.mock_balances["0xabc"] = 500.0
    response = client.get("/balances/0xabc")
    assert response.status_code == 200
    assert response.json() == {"address": "0xabc", "balance": 500.0}

def test_get_ledger(client):
    response = client.get("/ledger")
    assert response.status_code == 200
    assert response.json() == {"balances": {}}

    # modify mock balance
    relayer.mock_balances["0xabc"] = 500.0
    response = client.get("/ledger")
    assert response.status_code == 200
    assert response.json() == {"balances": {"0xabc": 500.0}}

def test_settle_state_channel_success(client):
    payload = {
        "task_id": 1,
        "provider_address": "0xprovider",
        "consumer_address": "0xconsumer",
        "flops_actual": 1000,
        "signature_hex": "0x1234",
        "mrenclave": "mock_enclave",
        "certificate_chain": ["mock_cert"],
        "price_per_petaflop": 1.5,
        "is_state_channel": True
    }

    response = client.post("/settle", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["task_id"] == 1
    assert data["tx_hash"] == "0xmock"

    # State should be updated
    assert relayer.mock_settled_tasks[1] == 1000

def test_settle_state_channel_monotonicity_failure(client):
    relayer.mock_settled_tasks[1] = 1000

    payload = {
        "task_id": 1,
        "provider_address": "0xprovider",
        "consumer_address": "0xconsumer",
        "flops_actual": 1000, # Not strictly increasing
        "signature_hex": "0x1234",
        "mrenclave": "mock_enclave",
        "certificate_chain": ["mock_cert"],
        "price_per_petaflop": 1.5,
        "is_state_channel": True
    }

    response = client.post("/settle", json=payload)
    assert response.status_code == 400
    assert "monotonically increasing" in response.json()["detail"]

def test_settle_tee_execution_success(client):
    sim = TEESimulator()
    task_id = 2
    flops_actual = 50000

    quote = sim.sign_execution(task_id, flops_actual)

    payload = {
        "task_id": task_id,
        "provider_address": quote.public_key,
        "consumer_address": "0xconsumer",
        "flops_actual": quote.flops_actual,
        "signature_hex": quote.signature_hex,
        "mrenclave": quote.mrenclave,
        "certificate_chain": quote.certificate_chain,
        "price_per_petaflop": 1.5,
        "is_state_channel": False
    }

    response = client.post("/settle", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["task_id"] == task_id

def test_settle_tee_execution_invalid_signature(client):
    sim = TEESimulator()
    task_id = 3
    flops_actual = 60000

    quote = sim.sign_execution(task_id, flops_actual)

    # Tamper with the task_id
    payload = {
        "task_id": task_id + 1,  # different task id invalidates signature
        "provider_address": quote.public_key,
        "consumer_address": "0xconsumer",
        "flops_actual": quote.flops_actual,
        "signature_hex": quote.signature_hex,
        "mrenclave": quote.mrenclave,
        "certificate_chain": quote.certificate_chain,
        "price_per_petaflop": 1.5,
        "is_state_channel": False
    }

    response = client.post("/settle", json=payload)
    assert response.status_code == 400
    assert "Invalid TEE signature" in response.json()["detail"]
