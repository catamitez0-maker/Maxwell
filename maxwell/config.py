"""
maxwell.config — Centralized configuration management.

Loads settings from TOML files, environment variables, or defaults.
Priority: CLI args > TOML file > Environment variables > Defaults.

Usage:
    # Load from TOML
    cfg = MaxwellConfig.from_toml("maxwell.toml")

    # Override programmatically
    cfg.port = 9090

    # Load from environment
    cfg = MaxwellConfig.from_env()
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

__all__ = ["MaxwellConfig"]

logger = logging.getLogger("maxwell.config")


@dataclass
class MaxwellConfig:
    """Central configuration with sensible defaults."""

    # ── Server ────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080
    mode: str = "server"
    role: str = "standalone"

    # ── Funnel ────────────────────────────────────────────────────
    entropy_low: float = 1.0
    entropy_high: float = 4.5
    workers: int = 2
    rules_path: str = "rules.json"

    # ── Model ─────────────────────────────────────────────────────
    model_name: str = "llama-7b"
    max_seq_length: int = 8192

    # ── Backend ───────────────────────────────────────────────────
    backend_url: str = ""
    backend_type: str = "ollama"

    # ── P2P ───────────────────────────────────────────────────────
    bootstrap_node: str = ""
    public_ip: str = "127.0.0.1"
    price: float = 1.0

    # ── Auth ──────────────────────────────────────────────────────
    api_keys_path: str = ""

    # ── Logging ───────────────────────────────────────────────────
    log_path: str = "logs/maxwell_access.jsonl"
    verbose: bool = False

    # ── Simulation ────────────────────────────────────────────────
    sim_rate: float = 0.01

    @classmethod
    def from_toml(cls, path: str) -> "MaxwellConfig":
        """
        Load configuration from a TOML file.

        Supports flat keys and nested sections that map to field names:
            [server]
            host = "0.0.0.0"
            port = 8080

        Section keys are flattened: server.host → host, funnel.workers → workers.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(p, "rb") as f:
            raw = tomllib.load(f)

        # Flatten nested sections
        flat: dict[str, Any] = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    flat[sub_key] = sub_value
            else:
                flat[key] = value

        # Map TOML key aliases
        _aliases = {"name": "model_name"}
        resolved: dict[str, Any] = {}
        valid_fields = {f.name for f in fields(cls)}
        for k, v in flat.items():
            mapped = _aliases.get(k, k)
            if mapped in valid_fields:
                resolved[mapped] = v
            else:
                logger.warning("Unknown config key: %s", k)

        logger.info("Loaded config from %s (%d keys)", path, len(resolved))
        return cls(**resolved)

    @classmethod
    def from_env(cls) -> "MaxwellConfig":
        """
        Load configuration from environment variables.

        Environment variable format: MAXWELL_{FIELD_NAME_UPPER}
        Example: MAXWELL_PORT=9090, MAXWELL_MODEL_NAME=llama-13b
        """
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            env_key = f"MAXWELL_{f.name.upper()}"
            env_val = os.environ.get(env_key)
            if env_val is not None:
                # Type coercion
                if f.type == "int":
                    kwargs[f.name] = int(env_val)
                elif f.type == "float":
                    kwargs[f.name] = float(env_val)
                elif f.type == "bool":
                    kwargs[f.name] = env_val.lower() in ("true", "1", "yes")
                else:
                    kwargs[f.name] = env_val

        if kwargs:
            logger.info("Loaded %d config value(s) from environment", len(kwargs))
        return cls(**kwargs)

    def merge_cli_args(self, **kwargs: Any) -> None:
        """
        Override config values with CLI arguments.

        Only overrides if the CLI value differs from the field default
        (to preserve TOML/env values when CLI arg isn't explicitly set).
        """
        defaults = MaxwellConfig()
        for key, value in kwargs.items():
            if value is None:
                continue
            if hasattr(self, key):
                default_val = getattr(defaults, key)
                # Only override if explicitly set (different from default)
                if value != default_val:
                    setattr(self, key, value)

    def to_dict(self) -> dict[str, Any]:
        """Export config as a flat dict."""
        return {f.name: getattr(self, f.name) for f in fields(self)}
