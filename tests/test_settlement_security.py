import pytest
from pydantic import ValidationError
from maxwell.settlement import SettleRequest

def test_settle_request_valid():
    req = SettleRequest(
        task_id=1,
        provider_address="0x1234567890123456789012345678901234567890",
        consumer_address="0x1234567890123456789012345678901234567890",
        flops_actual=1000,
        signature_hex="0x" + "a" * 130,
        mrenclave="b" * 64,
        certificate_chain=["cert1"],
        price_per_petaflop=1.5,
        is_state_channel=True
    )
    assert req.task_id == 1

def test_settle_request_invalid_addresses():
    with pytest.raises(ValidationError):
        SettleRequest(
            task_id=1,
            provider_address="0x123",
            consumer_address="0x1234567890123456789012345678901234567890",
            flops_actual=1000,
            signature_hex="0x" + "a" * 130,
            mrenclave="b" * 64,
            certificate_chain=["cert1"],
            price_per_petaflop=1.5,
            is_state_channel=True
        )
    with pytest.raises(ValidationError):
        SettleRequest(
            task_id=1,
            provider_address="0x1234567890123456789012345678901234567890",
            consumer_address="1234567890123456789012345678901234567890",
            flops_actual=1000,
            signature_hex="0x" + "a" * 130,
            mrenclave="b" * 64,
            certificate_chain=["cert1"],
            price_per_petaflop=1.5,
            is_state_channel=True
        )

def test_settle_request_invalid_signature():
    with pytest.raises(ValidationError):
        SettleRequest(
            task_id=1,
            provider_address="0x1234567890123456789012345678901234567890",
            consumer_address="0x1234567890123456789012345678901234567890",
            flops_actual=1000,
            signature_hex="0x" + "a" * 129, # too short
            mrenclave="b" * 64,
            certificate_chain=["cert1"],
            price_per_petaflop=1.5,
            is_state_channel=True
        )

def test_settle_request_invalid_mrenclave():
    with pytest.raises(ValidationError):
        SettleRequest(
            task_id=1,
            provider_address="0x1234567890123456789012345678901234567890",
            consumer_address="0x1234567890123456789012345678901234567890",
            flops_actual=1000,
            signature_hex="0x" + "a" * 130,
            mrenclave="b" * 65, # too long
            certificate_chain=["cert1"],
            price_per_petaflop=1.5,
            is_state_channel=True
        )
