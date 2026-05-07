import pytest
from maxwell.crypto import TEESimulator

def test_tee_simulator_signature():
    simulator = TEESimulator()
    assert simulator.public_address.startswith("0x")
    
    task_id = 12345
    flops_actual = 999999999
    
    signature = simulator.sign_execution(task_id, flops_actual)
    
    assert signature.public_key == simulator.public_address
    assert signature.flops_actual == flops_actual
    assert signature.signature_hex.startswith("0x")
    assert len(signature.signature_hex) == 132  # 0x + 130 hex chars (65 bytes)
