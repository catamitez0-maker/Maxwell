"""
Tests for maxwell.config — TOML configuration system.
"""

import os
import pytest
from maxwell.config import MaxwellConfig


class TestMaxwellConfigDefaults:
    def test_default_values(self) -> None:
        cfg = MaxwellConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 8080
        assert cfg.mode == "server"
        assert cfg.role == "standalone"
        assert cfg.entropy_low == 1.0
        assert cfg.entropy_high == 4.5
        assert cfg.workers == 2
        assert cfg.model_name == "llama-7b"
        assert cfg.max_seq_length == 8192
        assert cfg.backend_url == ""
        assert cfg.backend_type == "ollama"
        assert cfg.verbose is False

    def test_to_dict(self) -> None:
        cfg = MaxwellConfig()
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert d["port"] == 8080
        assert d["model_name"] == "llama-7b"


class TestMaxwellConfigTOML:
    def test_from_toml(self, tmp_path: pytest.TempPathFactory) -> None:
        toml_content = """
[server]
host = "192.168.1.1"
port = 9090
role = "provider"

[funnel]
entropy_low = 0.5
workers = 4

[model]
name = "mixtral-8x7b"
max_seq_length = 16384
"""
        toml_file = tmp_path / "test.toml"
        toml_file.write_text(toml_content)

        cfg = MaxwellConfig.from_toml(str(toml_file))
        assert cfg.host == "192.168.1.1"
        assert cfg.port == 9090
        assert cfg.role == "provider"
        assert cfg.entropy_low == 0.5
        assert cfg.workers == 4
        assert cfg.model_name == "mixtral-8x7b"
        assert cfg.max_seq_length == 16384
        # Unset values should be defaults
        assert cfg.entropy_high == 4.5
        assert cfg.verbose is False

    def test_from_toml_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            MaxwellConfig.from_toml("/nonexistent/path.toml")

    def test_from_toml_flat_keys(self, tmp_path: pytest.TempPathFactory) -> None:
        toml_content = """
port = 7777
verbose = true
"""
        toml_file = tmp_path / "flat.toml"
        toml_file.write_text(toml_content)
        cfg = MaxwellConfig.from_toml(str(toml_file))
        assert cfg.port == 7777
        assert cfg.verbose is True


class TestMaxwellConfigEnv:
    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAXWELL_PORT", "9999")
        monkeypatch.setenv("MAXWELL_MODEL_NAME", "llama-13b")
        monkeypatch.setenv("MAXWELL_VERBOSE", "true")

        cfg = MaxwellConfig.from_env()
        assert cfg.port == 9999
        assert cfg.model_name == "llama-13b"
        assert cfg.verbose is True

    def test_from_env_no_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Remove all MAXWELL_ vars
        for key in list(os.environ.keys()):
            if key.startswith("MAXWELL_"):
                monkeypatch.delenv(key, raising=False)
        cfg = MaxwellConfig.from_env()
        assert cfg.port == 8080  # default


class TestMergeCLIArgs:
    def test_override_non_default(self) -> None:
        cfg = MaxwellConfig()
        cfg.merge_cli_args(port=9090, host="127.0.0.1")
        assert cfg.port == 9090
        assert cfg.host == "127.0.0.1"

    def test_default_values_not_overridden(self) -> None:
        """CLI defaults shouldn't override TOML values."""
        cfg = MaxwellConfig(port=9090)  # Set by TOML
        cfg.merge_cli_args(port=8080)   # CLI default = 8080
        assert cfg.port == 9090  # TOML value preserved

    def test_explicit_cli_overrides_toml(self) -> None:
        cfg = MaxwellConfig(port=9090)
        cfg.merge_cli_args(port=7777)  # Non-default CLI value
        assert cfg.port == 7777
"""
Tests for maxwell.backends — Backend protocol and implementations.
"""

from maxwell.backends import (
    Backend,
    SimulatedBackend,
    OllamaBackend,
    get_backend,
)


class TestBackendProtocol:
    def test_simulated_is_backend(self) -> None:
        backend = SimulatedBackend()
        assert isinstance(backend, Backend)

    def test_ollama_is_backend(self) -> None:
        backend = OllamaBackend(url="http://localhost:11434")
        assert isinstance(backend, Backend)


class TestGetBackend:
    def test_ollama_with_url(self) -> None:
        backend = get_backend("ollama", "http://localhost:11434/api/generate")
        assert isinstance(backend, OllamaBackend)

    def test_no_url_returns_simulated(self) -> None:
        backend = get_backend("ollama", "")
        assert isinstance(backend, SimulatedBackend)

    def test_simulated_type(self) -> None:
        backend = get_backend("simulated")
        assert isinstance(backend, SimulatedBackend)


class TestSimulatedBackend:
    @pytest.mark.asyncio
    async def test_stream_yields_tokens(self) -> None:
        backend = SimulatedBackend(delay=0.0)
        tokens = []
        async for token in backend.stream("hello", "test-model"):
            tokens.append(token)
            if len(tokens) > 5:
                break
        assert len(tokens) > 5
        assert tokens[0] == "Here is the response from the simulated Compute Engine:\n"
