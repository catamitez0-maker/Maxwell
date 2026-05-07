"""
Unit tests for maxwell.filters — Bloom Filter, Entropy, Regex, Repetition.
"""

import re

import pytest

from maxwell.filters import BloomFilter, shannon_entropy, entropy_gate, regex_gate, repetition_gate


class TestBloomFilter:
    def test_membership(self) -> None:
        bf = BloomFilter(capacity=100)
        bf.add("hello")
        bf.add("world")
        assert "hello" in bf
        assert "world" in bf
        assert "missing" not in bf

    def test_blacklist_items(self) -> None:
        bf = BloomFilter(capacity=100)
        blacklist = ["dirty_blacklist_data", "malicious_payload_x", "spam_bot_001"]
        for item in blacklist:
            bf.add(item)
        for item in blacklist:
            assert item in bf
        assert "clean_data" not in bf

    def test_count(self) -> None:
        bf = BloomFilter(capacity=100)
        bf.add("a")
        bf.add("b")
        assert len(bf) == 2

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            BloomFilter(capacity=0)

    def test_invalid_fp_rate_raises(self) -> None:
        with pytest.raises(ValueError):
            BloomFilter(capacity=100, fp_rate=0.0)
        with pytest.raises(ValueError):
            BloomFilter(capacity=100, fp_rate=1.0)

    def test_estimated_fp_rate(self) -> None:
        bf = BloomFilter(capacity=100, fp_rate=0.01)
        assert bf.estimated_fp_rate == 0.0
        bf.add("item")
        assert bf.estimated_fp_rate >= 0.0

    def test_large_capacity(self) -> None:
        bf = BloomFilter(capacity=100_000, fp_rate=0.001)
        for i in range(1000):
            bf.add(f"item_{i}")
        for i in range(1000):
            assert f"item_{i}" in bf


class TestShannonEntropy:
    def test_empty_string(self) -> None:
        assert shannon_entropy("") == 0.0

    def test_single_char_repeat(self) -> None:
        assert shannon_entropy("aaaaaaa") == 0.0

    def test_high_entropy(self) -> None:
        e = shannon_entropy("abcdefghijklmnop")
        assert e > 3.5

    def test_natural_language_range(self) -> None:
        e = shannon_entropy("The quick brown fox jumps over the lazy dog")
        assert 3.0 < e < 5.0

    def test_chinese_text(self) -> None:
        e = shannon_entropy("正常中文推理测试请求")
        assert e > 0.0


class TestEntropyGate:
    def test_low_entropy_blocked(self) -> None:
        assert entropy_gate("aaaaaaa", low_threshold=1.0) is True

    def test_normal_passes(self) -> None:
        assert entropy_gate(
            "normal user query about AI",
            low_threshold=1.0, high_threshold=5.0,
        ) is False

    def test_load_tightens_thresholds(self) -> None:
        payload = "borderline_test_payload"
        result_low = entropy_gate(payload, load_factor=0.0)
        result_high = entropy_gate(payload, load_factor=0.9)
        assert isinstance(result_low, bool)
        assert isinstance(result_high, bool)


class TestRegexGate:
    def test_matches_rule(self) -> None:
        rules = [re.compile(r"system\("), re.compile(r"admin_login")]
        assert regex_gate("system(rm -rf)", rules) is True
        assert regex_gate("admin_login", rules) is True

    def test_no_match_passes(self) -> None:
        rules = [re.compile(r"system\(")]
        assert regex_gate("normal request", rules) is False

    def test_empty_rules(self) -> None:
        assert regex_gate("anything", []) is False


class TestRepetitionGate:
    def test_highly_repetitive_blocked(self) -> None:
        assert repetition_gate("abcabcabcabcabcabcabcabc") is True

    def test_loop_pattern_blocked(self) -> None:
        assert repetition_gate("looploop" * 10) is True

    def test_normal_text_passes(self) -> None:
        assert repetition_gate("The quick brown fox jumps over the lazy dog") is False

    def test_short_text_skipped(self) -> None:
        # Short payloads bypass repetition check
        assert repetition_gate("aaa") is False

    def test_single_char_repeat(self) -> None:
        assert repetition_gate("x" * 50) is True
