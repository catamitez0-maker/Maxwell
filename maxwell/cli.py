"""
Maxwell CLI — entry point using Typer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from .api import MaxwellServer
from .auth import APIKeyStore
from .config import MaxwellConfig
from .crypto import TEESimulator
from .dashboard import create_dashboard
from .models import FunnelStats, Task
from .oracle import MODELS
from .p2p import P2PManager
from .proxy import PruningProxy
from .settlement import SettlementHandler, Web3Relayer

app = typer.Typer(
    name="maxwell",
    help="⚡ Maxwell Protocol — Heuristic pruning gateway for AI compute.",
    add_completion=False,
)
console = Console()
logger = logging.getLogger("maxwell.cli")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@app.command()
def serve(
    mode: str = typer.Option("server", help="Run mode: 'server' or 'simulate'"),
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8080, help="Bind port"),
    rules: str = typer.Option("rules.json", help="Rules config path"),
    log: str = typer.Option("logs/maxwell_access.jsonl", help="Structured log path"),
    rate: float = typer.Option(0.01, help="Simulation request interval (seconds)"),
    entropy_low: float = typer.Option(1.0, help="Low entropy threshold"),
    entropy_high: float = typer.Option(4.5, help="High entropy threshold"),
    workers: int = typer.Option(2, help="Number of funnel workers"),
    model_name: str = typer.Option(
        "llama-7b", help="Model name (e.g. llama-7b, mixtral-8x7b) for FLOPs estimation"
    ),
    max_seq: int = typer.Option(8192, help="Max sequence length for FLOPs budget"),
    role: str = typer.Option("standalone", help="Node role: 'consumer', 'provider', 'settlement', or 'standalone'"),
    price: float = typer.Option(1.0, help="Provider price per PetaFLOP"),
    backend_url: str = typer.Option("", help="Actual LLM backend URL (e.g. http://localhost:11434/api/generate)"),
    backend_type: str = typer.Option("ollama", help="Backend type: ollama, openai, vllm"),
    bootstrap_node: str = typer.Option("", help="Kademlia bootstrap node (IP:PORT)"),
    public_ip: str = typer.Option("127.0.0.1", help="Public IP to broadcast in DHT"),
    api_keys: str = typer.Option("", help="Path to API keys JSON file (auth disabled if empty)"),
    config: str = typer.Option("", help="Path to maxwell.toml config file"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Start Maxwell proxy in server or simulation mode."""
    # ── Load config: TOML → env → defaults ────────────────────────
    if config and os.path.exists(config):
        cfg = MaxwellConfig.from_toml(config)
        console.print(f"[dim]📄 Loaded config from {config}[/dim]")
    else:
        cfg = MaxwellConfig.from_env()

    # CLI args override TOML/env (only when explicitly set)
    cfg.merge_cli_args(
        host=host, port=port, mode=mode, role=role,
        entropy_low=entropy_low, entropy_high=entropy_high,
        workers=workers, model_name=model_name, max_seq_length=max_seq,
        backend_url=backend_url, backend_type=backend_type,
        bootstrap_node=bootstrap_node, public_ip=public_ip,
        price=price, api_keys_path=api_keys,
        log_path=log, verbose=verbose, rules_path=rules,
        sim_rate=rate,
    )

    _setup_logging(cfg.verbose)
    node_id = f"node-{os.getpid()}-{random.randint(1000, 9999)}"
    asyncio.run(_run(cfg, node_id))


async def _run(cfg: MaxwellConfig, node_id: str) -> None:
    os.makedirs(os.path.dirname(cfg.log_path) or ".", exist_ok=True)

    # ── API Key Authentication ─────────────────────────────────────
    key_store = APIKeyStore()
    key_store.load_from_env()
    if cfg.api_keys_path:
        key_store.load_from_file(cfg.api_keys_path)
    if key_store.enabled:
        console.print(f"[green]🔒 API auth enabled ({len(key_store)} key(s))[/green]")
    else:
        console.print("[yellow]⚠️  API auth disabled (no keys configured)[/yellow]")

    # ── Settlement handler (for settlement role) ───────────────────
    settlement_handler = None
    if cfg.role == "settlement":
        settlement_handler = SettlementHandler(Web3Relayer())

    tee = None
    if cfg.role in ("provider", "standalone"):
        tee = TEESimulator()

    p2p_manager = None
    if cfg.role in ("provider", "consumer"):
        p2p_manager = P2PManager(
            node_id, cfg.role, cfg.port, cfg.price,
            cfg.model_name, cfg.bootstrap_node, cfg.public_ip,
        )
        await p2p_manager.start()

    model = MODELS.get(cfg.model_name)
    if not model:
        logger.error("Unknown model %s. Valid options: %s", cfg.model_name, list(MODELS.keys()))
        model = MODELS["llama-7b"]

    stats = FunnelStats()
    proxy = PruningProxy(
        stats,
        worker_count=cfg.workers,
        model=model,
        max_seq_length=cfg.max_seq_length,
        role=cfg.role,
        p2p_manager=p2p_manager,
        tee=tee,
        backend_url=cfg.backend_url,
        backend_type=cfg.backend_type,
    )
    proxy.entropy_low = cfg.entropy_low
    proxy.entropy_high = cfg.entropy_high

    await proxy.reload_rules(cfg.rules_path)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, proxy.shutdown)

    tasks: list[asyncio.Task[None]] = [
        *proxy.create_funnel_tasks(),
        asyncio.create_task(proxy.config_watcher(cfg.rules_path), name="config-watcher"),
        asyncio.create_task(proxy.log_worker(cfg.log_path), name="log-worker"),
    ]

    if cfg.mode == "simulate":
        tasks.append(
            asyncio.create_task(_simulate_producer(proxy, cfg.sim_rate), name="simulator")
        )
        console.print(Panel(
            "[bold green]⚡ Maxwell Protocol[/bold green] — Simulation Mode",
            subtitle=f"Phase 7 · {cfg.workers} workers · {model.name}",
        ))
    else:
        server = MaxwellServer(
            proxy, host=cfg.host, port=cfg.port,
            api_key_store=key_store,
            settlement_handler=settlement_handler,
        )
        await server.start()
        console.print(Panel(
            f"[bold cyan]⚡ Maxwell Protocol[/bold cyan] — Listening on {cfg.host}:{cfg.port}",
            subtitle=f"Role: {cfg.role.upper()} · Mode: {cfg.mode} · Model: {model.name}",
        ))

    with Live(
        create_dashboard(stats),
        refresh_per_second=4,
        console=console,
    ) as live:
        while proxy.is_running:
            live.update(create_dashboard(stats))
            await asyncio.sleep(0.25)

    console.print("\n[yellow]Shutting down…[/yellow]")
    if p2p_manager:
        await p2p_manager.stop()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    console.print("[green]✅ Maxwell stopped.[/green]")


async def _simulate_producer(proxy: PruningProxy, rate: float) -> None:
    """Generate synthetic traffic for testing the full funnel."""
    samples = [
        "valid_ai_inference_request",
        "normal_user_query_about_transformers",
        "aaaaaa",
        "123",
        "dirty_blacklist_data",
        "!!@@##$$%%^^&&**",
        "exec(rm -rf)",
        "<script>alert(1)</script>",
        "admin_login",
        "\x01\x02\x03_junk",
        "正常中文推理测试请求",
        "The quick brown fox jumps over the lazy dog",
        # L5: repetitive idle-loop payload
        "abcabcabcabcabcabcabcabcabcabcabcabc",
        "looploop" * 10,
    ]
    async def _run_stream(task: Task) -> None:
        try:
            async for _ in proxy.process_stream(task):
                pass
        except Exception:
            pass

    task_id = 0
    while proxy.is_running:
        payload = random.choice(samples)
        task = Task(id=task_id, payload=payload)
        # total_requests is tracked inside process_stream(); no duplicate increment
        task_id += 1

        asyncio.create_task(_run_stream(task))

        await asyncio.sleep(rate)

if __name__ == "__main__":
    app()


@app.command()
def init(
    directory: str = typer.Argument(".", help="Directory to initialize"),
    with_key: bool = typer.Option(True, help="Generate an API key pair"),
) -> None:
    """Initialize a Maxwell project with config files and API keys."""
    from .auth import generate_api_key

    target = os.path.abspath(directory)
    os.makedirs(target, exist_ok=True)

    # ── maxwell.toml ─────────────────────────────────────────────
    toml_path = os.path.join(target, "maxwell.toml")
    if not os.path.exists(toml_path):
        toml_content = (
            "# Maxwell Protocol Configuration\n"
            "# Docs: https://github.com/maxwell-protocol/maxwell\n\n"
            "[server]\n"
            'host = "0.0.0.0"\n'
            "port = 8080\n"
            'mode = "server"\n'
            'role = "standalone"\n\n'
            "[funnel]\n"
            "entropy_low = 1.0\n"
            "entropy_high = 4.5\n"
            "workers = 2\n"
            'rules_path = "rules.json"\n\n'
            "[model]\n"
            'name = "llama-7b"\n'
            "max_seq_length = 8192\n\n"
            "[backend]\n"
            'backend_url = ""\n'
            'backend_type = "ollama"\n\n'
            "[auth]\n"
            'api_keys_path = "api_keys.json"\n\n'
            "[logging]\n"
            'log_path = "logs/maxwell_access.jsonl"\n'
            "verbose = false\n"
        )
        with open(toml_path, "w") as f:
            f.write(toml_content)
        console.print(f"  [green]\u2713[/green] Created {toml_path}")
    else:
        console.print(f"  [dim]\u23ed {toml_path} already exists[/dim]")

    # ── rules.json ───────────────────────────────────────────────
    rules_path = os.path.join(target, "rules.json")
    if not os.path.exists(rules_path):
        rules = {
            "blacklist": [
                "exec(rm",
                "DROP TABLE",
                "<script>",
                "admin_login",
            ],
            "patterns": [
                "(?i)(password|secret|token)\\s*[:=]",
                "(?i)ignore\\s+previous\\s+instructions",
            ],
        }
        with open(rules_path, "w") as f:
            json.dump(rules, f, indent=2)
        console.print(f"  [green]\u2713[/green] Created {rules_path}")
    else:
        console.print(f"  [dim]\u23ed {rules_path} already exists[/dim]")

    # ── API Key ──────────────────────────────────────────────────
    keys_path = os.path.join(target, "api_keys.json")
    if with_key:
        key_id, secret = generate_api_key()
        keys_data: dict = {"keys": {}}
        if os.path.exists(keys_path):
            with open(keys_path, "r") as f:
                keys_data = json.load(f)
        keys_data["keys"][key_id] = secret
        with open(keys_path, "w") as f:
            json.dump(keys_data, f, indent=2)
        console.print(f"  [green]\u2713[/green] API key generated:")
        console.print(f"    Key ID:  [bold]{key_id}[/bold]")
        console.print(f"    Secret:  [bold]{secret}[/bold]")
        console.print(f"    Saved to {keys_path}")

    # ── logs dir ─────────────────────────────────────────────────
    logs_dir = os.path.join(target, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    console.print(Panel(
        "[bold green]\u26a1 Maxwell project initialized![/bold green]\n\n"
        f"  Config:  {toml_path}\n"
        f"  Rules:   {rules_path}\n\n"
        "  Start with: [bold]maxwell serve --config maxwell.toml[/bold]",
        title="Maxwell Init",
    ))


@app.command()
def keygen() -> None:
    """Generate a new API key pair."""
    from .auth import generate_api_key

    key_id, secret = generate_api_key()
    console.print(f"[bold]Key ID:[/bold]  {key_id}")
    console.print(f"[bold]Secret:[/bold]  {secret}")
    console.print()
    console.print("[dim]Add to env:  export MAXWELL_API_KEYS=" + key_id + ":" + secret + "[/dim]")
    console.print("[dim]Or to file:  maxwell init --with-key[/dim]")
