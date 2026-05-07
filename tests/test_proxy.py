"""
Integration tests for maxwell.proxy — async funnel engine.
"""

import asyncio

import pytest

from maxwell.models import FunnelStats, Task
from maxwell.proxy import PruningProxy


@pytest.fixture
def proxy() -> PruningProxy:
    stats = FunnelStats()
    p = PruningProxy(stats, worker_count=1)
    return p


class TestPruningProxy:
    @pytest.mark.asyncio
    async def test_bloom_blocks_blacklisted(self, proxy: PruningProxy) -> None:
        """L1: items in bloom filter should be blocked."""
        proxy.bloom.add("malicious_payload")

        task = Task(id=1, payload="malicious_payload")
        await proxy.input_queue.put(task)
        proxy.stats.total_requests += 1

        # Run funnel for one cycle
        worker = asyncio.create_task(proxy.funnel_worker(0))
        await asyncio.sleep(0.2)
        proxy.shutdown()
        await worker

        assert proxy.stats.bloom_blocked == 1
        assert proxy.output_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_regex_blocks_pattern(self, proxy: PruningProxy) -> None:
        """L2: payloads matching regex rules should be blocked."""
        import re
        proxy.rules = [re.compile(r"exec\(")]

        task = Task(id=2, payload="exec(rm -rf)")
        await proxy.input_queue.put(task)
        proxy.stats.total_requests += 1

        worker = asyncio.create_task(proxy.funnel_worker(0))
        await asyncio.sleep(0.2)
        proxy.shutdown()
        await worker

        assert proxy.stats.regex_blocked == 1
        assert proxy.output_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_entropy_blocks_repetitive(self, proxy: PruningProxy) -> None:
        """L3: low entropy payloads should be blocked."""
        task = Task(id=3, payload="aaaaaaaaaaaa")
        await proxy.input_queue.put(task)
        proxy.stats.total_requests += 1

        worker = asyncio.create_task(proxy.funnel_worker(0))
        await asyncio.sleep(0.2)
        proxy.shutdown()
        await worker

        assert proxy.stats.entropy_blocked == 1
        assert proxy.output_queue.qsize() == 0

    @pytest.mark.asyncio
    async def test_valid_payload_passes(self, proxy: PruningProxy) -> None:
        """Valid payloads should pass all layers and reach output queue."""
        task = Task(id=4, payload="normal AI inference query about transformers")
        await proxy.input_queue.put(task)
        proxy.stats.total_requests += 1

        worker = asyncio.create_task(proxy.funnel_worker(0))
        await asyncio.sleep(0.2)
        proxy.shutdown()
        await worker

        assert proxy.stats.passed_to_engine == 1
        assert proxy.output_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_multi_worker_throughput(self) -> None:
        """Multiple funnel workers should process tasks concurrently."""
        stats = FunnelStats()
        proxy = PruningProxy(stats, worker_count=4)

        # Submit 20 tasks
        for i in range(20):
            task = Task(id=i, payload=f"valid_payload_{i}_with_enough_entropy")
            await proxy.input_queue.put(task)
            proxy.stats.total_requests += 1

        workers = proxy.create_funnel_tasks()
        await asyncio.sleep(0.5)
        proxy.shutdown()
        await asyncio.gather(*workers, return_exceptions=True)

        # All should be processed
        processed = stats.passed_to_engine + stats.total_blocked
        assert processed == 20

    @pytest.mark.asyncio
    async def test_config_reload(self, proxy: PruningProxy, tmp_path: str) -> None:
        """Config reload should update bloom filter and regex rules."""
        import json
        import tempfile
        import os

        config = {
            "blacklist": ["test_block_item"],
            "regex_rules": ["^forbidden"]
        }
        config_file = os.path.join(tempfile.mkdtemp(), "rules.json")
        with open(config_file, "w") as f:
            json.dump(config, f)

        result = await proxy.reload_rules(config_file)
        assert result is True
        assert "test_block_item" in proxy.bloom
        assert len(proxy.rules) == 1

    @pytest.mark.asyncio
    async def test_shutdown_event(self, proxy: PruningProxy) -> None:
        """Shutdown event should stop all workers gracefully."""
        assert proxy.is_running is True
        proxy.shutdown()
        assert proxy.is_running is False
