import os
import pytest
from maxwell.settlement import Web3Relayer

def test_web3_relayer_production_mode_requires_pk(monkeypatch):
    # Set WEB3_RPC_URL to trigger production mode
    monkeypatch.setenv("WEB3_RPC_URL", "http://127.0.0.1:8545")
    # Ensure RELAYER_PRIVATE_KEY is NOT set
    monkeypatch.delenv("RELAYER_PRIVATE_KEY", raising=False)

    with pytest.raises(ValueError, match="RELAYER_PRIVATE_KEY environment variable is required in production mode."):
        Web3Relayer()

def test_web3_relayer_mock_mode_fallback(monkeypatch):
    # Ensure WEB3_RPC_URL is NOT set to trigger mock mode
    monkeypatch.delenv("WEB3_RPC_URL", raising=False)
    monkeypatch.delenv("RELAYER_PRIVATE_KEY", raising=False)

    relayer = Web3Relayer()
    assert relayer.mock_mode is True
    assert relayer.account is not None
