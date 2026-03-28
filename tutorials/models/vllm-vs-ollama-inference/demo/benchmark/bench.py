#!/usr/bin/env python3
"""
bench.py — vLLM vs Ollama inference benchmark

Sends concurrent chat-completion requests to both backends and reports:
  - Requests/sec
  - Time-to-first-token (TTFT) p50 / p95
  - Total tokens/sec
  - Error rate

Usage:
    python bench.py \
        --ollama-url http://localhost:11434 \
        --vllm-url   http://localhost:8000 \
        --model      llama3.2:7b \
        --concurrency 1 4 8 16 \
        --requests   50

Prerequisites:
    pip install httpx rich

Both servers must be running and the model must already be loaded.
For vLLM, pass --vllm-model if the model ID differs from the Ollama name.
"""

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from rich.console import Console
from rich.table import Table

PROMPT = (
    "Explain the difference between PagedAttention and traditional KV-cache "
    "management in large language model inference servers. Be concise."
)

console = Console()


@dataclass
class RequestResult:
    ttft: float  # seconds to first token
    total_time: float  # seconds for full response
    tokens: int  # rough token count from response text
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class BenchResult:
    backend: str
    concurrency: int
    results: list[RequestResult] = field(default_factory=list)

    @property
    def ok_results(self):
        return [r for r in self.results if r.ok]

    @property
    def error_rate(self) -> float:
        if not self.results:
            return 0.0
        return len([r for r in self.results if not r.ok]) / len(self.results)

    @property
    def rps(self) -> float:
        """Requests per second (successful only)."""
        if not self.ok_results:
            return 0.0
        total_time = sum(r.total_time for r in self.ok_results)
        return len(self.ok_results) / total_time * self.concurrency

    @property
    def ttft_p50(self) -> float:
        ttfts = [r.ttft for r in self.ok_results]
        return statistics.median(ttfts) if ttfts else 0.0

    @property
    def ttft_p95(self) -> float:
        ttfts = sorted(r.ttft for r in self.ok_results)
        if not ttfts:
            return 0.0
        idx = max(0, int(len(ttfts) * 0.95) - 1)
        return ttfts[idx]

    @property
    def tokens_per_sec(self) -> float:
        if not self.ok_results:
            return 0.0
        total_tokens = sum(r.tokens for r in self.ok_results)
        total_time = sum(r.total_time for r in self.ok_results)
        return total_tokens / total_time * self.concurrency if total_time else 0.0


async def call_vllm(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
    api_key: str,
) -> RequestResult:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": 256,
    }
    ttft = None
    tokens = 0
    t0 = time.perf_counter()
    try:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                if ttft is None:
                    ttft = time.perf_counter() - t0
                # Rough token count: each non-empty chunk ≈ 1 token
                tokens += 1
        total_time = time.perf_counter() - t0
        return RequestResult(ttft=ttft or total_time, total_time=total_time, tokens=tokens)
    except Exception as exc:
        return RequestResult(ttft=0, total_time=time.perf_counter() - t0, tokens=0, error=str(exc))


async def call_ollama(
    client: httpx.AsyncClient,
    base_url: str,
    model: str,
) -> RequestResult:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
    }
    ttft = None
    tokens = 0
    t0 = time.perf_counter()
    try:
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                if ttft is None:
                    ttft = time.perf_counter() - t0
                tokens += 1
        total_time = time.perf_counter() - t0
        return RequestResult(ttft=ttft or total_time, total_time=total_time, tokens=tokens)
    except Exception as exc:
        return RequestResult(ttft=0, total_time=time.perf_counter() - t0, tokens=0, error=str(exc))


async def run_batch(
    backend: str,
    call_fn,
    concurrency: int,
    total_requests: int,
) -> BenchResult:
    result = BenchResult(backend=backend, concurrency=concurrency)
    sem = asyncio.Semaphore(concurrency)

    async def bounded_call():
        async with sem:
            return await call_fn()

    async with httpx.AsyncClient() as client:
        # Rebind call_fn to pass client
        tasks = [bounded_call() for _ in range(total_requests)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        result.results = list(results)

    return result


async def bench_backend(
    backend: str,
    base_url: str,
    model: str,
    concurrency_levels: list[int],
    total_requests: int,
    api_key: str = "",
) -> list[BenchResult]:
    bench_results = []

    for c in concurrency_levels:
        console.print(f"  [cyan]{backend}[/] concurrency={c} ({total_requests} requests)...")

        if backend == "vllm":
            async def call_fn(c=c):
                async with httpx.AsyncClient() as client:
                    return await call_vllm(client, base_url, model, api_key)
        else:
            async def call_fn(c=c):
                async with httpx.AsyncClient() as client:
                    return await call_ollama(client, base_url, model)

        sem = asyncio.Semaphore(c)

        async def bounded(fn=call_fn):
            async with sem:
                return await fn()

        tasks = [bounded() for _ in range(total_requests)]
        results = await asyncio.gather(*tasks)
        br = BenchResult(backend=backend, concurrency=c, results=list(results))
        bench_results.append(br)

        # Progress summary
        console.print(
            f"    rps={br.rps:.1f} ttft_p50={br.ttft_p50*1000:.0f}ms "
            f"ttft_p95={br.ttft_p95*1000:.0f}ms tok/s={br.tokens_per_sec:.0f} "
            f"errors={br.error_rate:.0%}"
        )

    return bench_results


def render_table(all_results: list[BenchResult]) -> None:
    table = Table(title="Benchmark Results: vLLM vs Ollama", show_lines=True)
    table.add_column("Backend", style="bold cyan")
    table.add_column("Concurrency", justify="right")
    table.add_column("Req/s", justify="right")
    table.add_column("TTFT p50 (ms)", justify="right")
    table.add_column("TTFT p95 (ms)", justify="right")
    table.add_column("Tok/s", justify="right")
    table.add_column("Errors", justify="right")

    for br in all_results:
        table.add_row(
            br.backend,
            str(br.concurrency),
            f"{br.rps:.1f}",
            f"{br.ttft_p50 * 1000:.0f}",
            f"{br.ttft_p95 * 1000:.0f}",
            f"{br.tokens_per_sec:.0f}",
            f"{br.error_rate:.0%}",
        )

    console.print(table)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark vLLM vs Ollama")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--vllm-url", default="http://localhost:8000")
    parser.add_argument("--model", default="llama3.2:7b",
                        help="Model name for Ollama; use --vllm-model to override for vLLM.")
    parser.add_argument("--vllm-model", default=None,
                        help="Override model ID sent to vLLM (defaults to --model value).")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 4, 8, 16],
                        help="List of concurrency levels to test.")
    parser.add_argument("--requests", type=int, default=50,
                        help="Total requests per concurrency level per backend.")
    parser.add_argument("--vllm-api-key", default="", help="Optional API key for vLLM.")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-vllm", action="store_true")
    args = parser.parse_args()

    vllm_model = args.vllm_model or args.model
    all_results: list[BenchResult] = []

    if not args.skip_ollama:
        console.rule("[bold green]Ollama")
        ollama_results = await bench_backend(
            backend="ollama",
            base_url=args.ollama_url,
            model=args.model,
            concurrency_levels=args.concurrency,
            total_requests=args.requests,
        )
        all_results.extend(ollama_results)

    if not args.skip_vllm:
        console.rule("[bold blue]vLLM")
        vllm_results = await bench_backend(
            backend="vllm",
            base_url=args.vllm_url,
            model=vllm_model,
            concurrency_levels=args.concurrency,
            total_requests=args.requests,
            api_key=args.vllm_api_key,
        )
        all_results.extend(vllm_results)

    console.print()
    render_table(all_results)


if __name__ == "__main__":
    asyncio.run(main())
