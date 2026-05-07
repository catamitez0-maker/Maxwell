import sys
from unittest.mock import MagicMock

# Mock required dependencies
# We don't mock mmh3, numpy since we installed them. We mock kademlia, pynvml.
sys.modules['kademlia'] = MagicMock()
sys.modules['kademlia.network'] = MagicMock()

class MockPynvml:
    class NVMLError(Exception):
        pass
    def nvmlInit(self):
        pass
    def nvmlDeviceGetCount(self):
        return 0
    def nvmlDeviceGetHandleByIndex(self, i):
        return MagicMock()
    def nvmlDeviceGetUtilizationRates(self, handle):
        mock = MagicMock()
        mock.gpu = 0
        return mock
    def nvmlDeviceGetMemoryInfo(self, handle):
        mock = MagicMock()
        mock.used = 0
        mock.total = 100
        return mock

sys.modules['pynvml'] = MockPynvml()
