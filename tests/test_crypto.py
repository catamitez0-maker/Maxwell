import pytest
from maxwell.crypto import TEESimulator

def test_tee_simulator_signature():
    simulator = TEESimulator()
    assert simulator.public_address.startswith("0x")
    
    task_id = 12345
    flops_actual = 999999999
    
    quote = simulator.sign_execution(task_id, flops_actual)
    
    assert quote.public_key == simulator.public_address
    assert quote.flops_actual == flops_actual
    assert quote.signature_hex.startswith("0x")
    assert len(quote.signature_hex) == 132  # 0x + 130 hex chars (65 bytes)
    assert len(quote.certificate_chain) == 3
    
    # Test valid verification
    assert TEESimulator.verify_execution(quote, task_id) == True
    
    # Test MRENCLAVE mismatch
    tampered_quote = quote._replace(mrenclave="bad_hash")
    assert TEESimulator.verify_execution(tampered_quote, task_id) == False

    # Test invalid certificate chain length
    tampered_quote = quote._replace(certificate_chain=quote.certificate_chain[:2])
    assert TEESimulator.verify_execution(tampered_quote, task_id) == False

    # Test missing ROOT_CA_MOCK in certificate chain
    tampered_chain = list(quote.certificate_chain)
    tampered_chain[0] = "-----BEGIN CERTIFICATE-----\nINVALID_CA_MOCK\n-----END CERTIFICATE-----"
    tampered_quote = quote._replace(certificate_chain=tampered_chain)
    assert TEESimulator.verify_execution(tampered_quote, task_id) == False

    # Test tampered flops_actual
    tampered_quote = quote._replace(flops_actual=1)
    assert TEESimulator.verify_execution(tampered_quote, task_id) == False

    # Test mismatched task_id
    assert TEESimulator.verify_execution(quote, task_id + 1) == False

    # Test invalid signature format (triggers exception)
    tampered_quote = quote._replace(signature_hex="0xinvalid")
    assert TEESimulator.verify_execution(tampered_quote, task_id) == False
