"""
Maxwell CLI — entry point using Typer.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from .api import MaxwellServer
from .crypto import TEESimulator
from .dashboard import create_dashboard
from .models import FunnelStats, Task
from .oracle import MODELS
from .p2p import P2PManager
from .proxy import PruningProxy

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
    config: str = typer.Option("rules.json", help="Rules config path"),
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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
) -> None:
    """Start Maxwell proxy in server or simulation mode."""
    _setup_logging(verbose)
    node_id = f"node-{os.getpid()}-{random.randint(1000, 9999)}"
    asyncio.run(_run(
        mode, host, port, config, log, rate,
        entropy_low, entropy_high, workers,
        model_name, max_seq, role, price, node_id,
        backend_url, backend_type, bootstrap_node, public_ip,
    ))


async def _run(
    mode: str, host: str, port: int, config: str, log: str, rate: float,
    entropy_low: float, entropy_high: float, workers: int,
    model_name: str, max_seq: int, role: str, price: float, node_id: str,
    backend_url: str, backend_type: str, bootstrap_node: str, public_ip: str,
) -> None:
    os.makedirs(os.path.dirname(log) or ".", exist_ok=True)

    if role == "settlement":
        import uvicorn
        console.print(Panel(
            f"[bold cyan]⚡ Maxwell Settlement Node[/bold cyan] — Listening on {host}:{port}",
            subtitle="Role: SETTLEMENT",
        ))
        # Run FastAPI app
        uvicorn.run("maxwell.settlement:app", host=host, port=port, log_level="info")
        return

    tee = None
    if role in ("provider", "standalone"):
        tee = TEESimulator()
        
    p2p_manager = None
    if role in ("provider", "consumer"):
        p2p_manager = P2PManager(node_id, role, port, price, model_name, bootstrap_node, public_ip)
        await p2p_manager.start()
        
    model = MODELS.get(model_name)
    if not model:
        logger.error(f"Unknown model {model_name}. Valid options: {list(MODELS.keys())}")
        model = MODELS["llama-7b"]

    stats = FunnelStats()
    proxy = PruningProxy(
        stats,
        worker_count=workers,
        model=model,
        max_seq_length=max_seq,
        role=role,
        p2p_manager=p2p_manager,
        tee=tee,
        backend_url=backend_url,
        backend_type=backend_type,
    )
    proxy.entropy_low = entropy_low
    proxy.entropy_high = entropy_high

    await proxy.reload_rules(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, proxy.shutdown)

    tasks: list[asyncio.Task[None]] = [
        *proxy.create_funnel_tasks(),
        asyncio.create_task(proxy.config_watcher(config), name="config-watcher"),
        asyncio.create_task(proxy.log_worker(log), name="log-worker"),
    ]

    if mode == "simulate":
        tasks.append(
            asyncio.create_task(_simulate_producer(proxy, rate), name="simulator")
        )
        console.print(Panel(
            "[bold green]⚡ Maxwell Protocol[/bold green] — Simulation Mode",
            subtitle=f"Phase 7 · {workers} workers · {model.name}",
        ))
    else:
        server = MaxwellServer(proxy, host=host, port=port)
        await server.start()
        console.print(Panel(
            f"[bold cyan]⚡ Maxwell Protocol[/bold cyan] — Listening on {host}:{port}",
            subtitle=f"Role: {role.upper()} · Mode: {mode} · Model: {model.name}",
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
        proxy.stats.total_requests += 1
        task_id += 1
        
        asyncio.create_task(_run_stream(task))
        
        await asyncio.sleep(rate)

if __name__ == "__main__":
    app()
