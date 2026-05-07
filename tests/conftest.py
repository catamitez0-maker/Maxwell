import sys
from unittest.mock import MagicMock

class MockPynvmlError(Exception):
    pass

pynvml_mock = MagicMock()
pynvml_mock.NVMLError = MockPynvmlError
pynvml_mock.nvmlDeviceGetCount.return_value = 0

sys.modules['pynvml'] = pynvml_mock

for mod in ['mmh3', 'numpy', 'kademlia', 'kademlia.network', 'aiohttp', 'eth_account', 'eth_account.messages', 'web3', 'bitarray']:
    sys.modules[mod] = MagicMock()
