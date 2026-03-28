#!/usr/bin/env python3
"""
Prompt Caching + Batching Benchmark
====================================
Processes a synthetic ~50K-token reference document against 100 queries in
three modes:
  1. no-cache  — standard API calls, no cache_control
  2. cache     — prompt caching enabled, synchronous calls
  3. batch     — prompt caching + Message Batches API

Prints a cost/token comparison table at the end.

Usage:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-...
    python benchmark.py [--mode no-cache|cache|batch|all] [--queries 20]

Note: Running all three modes against 100 queries will cost real money
(~$1–5 depending on model). Use --queries 10 for a cheap smoke test.
"""

import argparse
import os
import random
import string
import time
from dataclasses import dataclass, field
from typing import Optional

import anthropic

# ---------------------------------------------------------------------------
# Pricing (claude-opus-4-5, per million tokens, as of early 2026)
# ---------------------------------------------------------------------------
PRICE_INPUT_PER_M       = 15.00
PRICE_CACHE_WRITE_PER_M = 18.75
PRICE_CACHE_READ_PER_M  =  1.50
PRICE_OUTPUT_PER_M      = 75.00

MODEL = "claude-opus-4-5"
MAX_OUTPUT_TOKENS = 256

# ---------------------------------------------------------------------------
# Synthetic document generator (~50K tokens ≈ ~200K characters of prose)
# ---------------------------------------------------------------------------

SECTION_TOPICS = [
    "executive summary", "market analysis", "competitive landscape",
    "technical architecture", "security considerations", "cost projections",
    "implementation roadmap", "risk assessment", "compliance requirements",
    "stakeholder analysis", "go-to-market strategy", "product roadmap",
    "engineering resources", "data governance", "SLA definitions",
    "incident response", "capacity planning", "vendor evaluation",
    "success metrics", "post-launch review",
]

FILLER_SENTENCES = [
    "The data clearly supports this conclusion given the current market dynamics.",
    "Stakeholders have expressed strong interest in accelerating this initiative.",
    "Further analysis is required to validate the assumptions in this section.",
    "Historical trends suggest a 12-18 month window for optimal deployment.",
    "Cross-functional alignment is critical before proceeding to the next phase.",
    "The proposed architecture reduces operational overhead by an estimated 35%.",
    "Regulatory compliance must be verified against the latest NIST guidelines.",
    "Budget allocations should be revisited on a quarterly basis going forward.",
    "Integration with existing systems will require dedicated migration sprints.",
    "User acceptance testing is scheduled for the end of the next fiscal quarter.",
]


def generate_document(target_chars: int = 200_000) -> str:
    """Generate a synthetic multi-section report of approximately target_chars characters."""
    rng = random.Random(42)  # deterministic
    sections = []
    chars_per_section = target_chars // len(SECTION_TOPICS)

    for topic in SECTION_TOPICS:
        header = f"\n## Section: {topic.title()}\n\n"
        body_parts = []
        while sum(len(p) for p in body_parts) < chars_per_section:
            sentence = rng.choice(FILLER_SENTENCES)
            # add light variation so the text isn't obviously repetitive
            word = "".join(rng.choices(string.ascii_lowercase, k=6))
            body_parts.append(f"{sentence} (ref-{word})")
        sections.append(header + " ".join(body_parts))

    return (
        "# Enterprise Reference Document\n\n"
        "This document contains detailed analysis across multiple domains.\n"
        + "".join(sections)
    )


# ---------------------------------------------------------------------------
# Query bank
# ---------------------------------------------------------------------------

QUERY_TEMPLATES = [
    "What are the key risks identified in the {section} section?",
    "Summarize the main recommendations from the {section} section.",
    "What budget considerations are mentioned in the {section} section?",
    "List the compliance requirements discussed in the {section} section.",
    "What timeline is proposed in the {section} section?",
    "Identify stakeholders mentioned in the {section} section.",
    "What metrics are used to evaluate success in the {section} section?",
    "Describe the technical approach outlined in the {section} section.",
    "What dependencies are noted in the {section} section?",
    "What open questions remain in the {section} section?",
]


def generate_queries(n: int) -> list[str]:
    rng = random.Random(99)
    queries = []
    for i in range(n):
        template = rng.choice(QUERY_TEMPLATES)
        section = rng.choice(SECTION_TOPICS)
        queries.append(template.format(section=section))
    return queries


# ---------------------------------------------------------------------------
# Cost accounting
# ---------------------------------------------------------------------------

@dataclass
class RunStats:
    mode: str
    n_queries: int
    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0.0

    def add_usage(self, usage) -> None:
        self.input_tokens       += getattr(usage, "input_tokens", 0)
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0)
        self.cache_read_tokens  += getattr(usage, "cache_read_input_tokens", 0)
        self.output_tokens      += getattr(usage, "output_tokens", 0)

    @property
    def actual_cost(self) -> float:
        return (
            self.input_tokens       / 1_000_000 * PRICE_INPUT_PER_M
            + self.cache_write_tokens / 1_000_000 * PRICE_CACHE_WRITE_PER_M
            + self.cache_read_tokens  / 1_000_000 * PRICE_CACHE_READ_PER_M
            + self.output_tokens      / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    @property
    def nocache_cost(self) -> float:
        """Hypothetical cost if every token were billed as standard input."""
        total_input = self.input_tokens + self.cache_write_tokens + self.cache_read_tokens
        return (
            total_input      / 1_000_000 * PRICE_INPUT_PER_M
            + self.output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    @property
    def savings_pct(self) -> float:
        if self.nocache_cost == 0:
            return 0.0
        return (self.nocache_cost - self.actual_cost) / self.nocache_cost * 100


# ---------------------------------------------------------------------------
# Benchmark modes
# ---------------------------------------------------------------------------

def build_system(document: str, use_cache: bool) -> list[dict]:
    doc_block: dict = {
        "type": "text",
        "text": f"<document>\n{document}\n</document>",
    }
    if use_cache:
        doc_block["cache_control"] = {"type": "ephemeral"}
    return [
        {"type": "text", "text": "You are an expert analyst. Answer questions about the document concisely in 2-3 sentences."},
        doc_block,
    ]


def run_no_cache(client: anthropic.Anthropic, document: str, queries: list[str]) -> RunStats:
    stats = RunStats(mode="no-cache", n_queries=len(queries))
    system = build_system(document, use_cache=False)

    t0 = time.monotonic()
    for i, query in enumerate(queries):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        stats.add_usage(response.usage)
        if (i + 1) % 10 == 0:
            print(f"  no-cache: {i + 1}/{len(queries)} done")

    stats.elapsed_seconds = time.monotonic() - t0
    return stats


def run_with_cache(client: anthropic.Anthropic, document: str, queries: list[str]) -> RunStats:
    stats = RunStats(mode="cache", n_queries=len(queries))
    system = build_system(document, use_cache=True)

    t0 = time.monotonic()
    for i, query in enumerate(queries):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": query}],
        )
        stats.add_usage(response.usage)
        if (i + 1) % 10 == 0:
            cache_reads = getattr(response.usage, "cache_read_input_tokens", 0)
            print(f"  cache: {i + 1}/{len(queries)} done (cache_read={cache_reads:,})")

    stats.elapsed_seconds = time.monotonic() - t0
    return stats


def run_cache_plus_batch(
    client: anthropic.Anthropic,
    document: str,
    queries: list[str],
    poll_interval: int = 20,
) -> RunStats:
    stats = RunStats(mode="cache+batch", n_queries=len(queries))
    system = build_system(document, use_cache=True)

    # Step 1: warm the cache
    print("  Warming cache...")
    warmup = client.messages.create(
        model=MODEL,
        max_tokens=8,
        system=system,
        messages=[{"role": "user", "content": "Ready."}],
    )
    cache_written = getattr(warmup.usage, "cache_creation_input_tokens", 0)
    print(f"  Cache write: {cache_written:,} tokens")
    # Don't count warmup in stats (it's overhead, not part of the query set)

    # Step 2: submit batch
    t0 = time.monotonic()
    requests = [
        anthropic.types.message_create_params.Request(
            custom_id=f"q-{i}",
            params=anthropic.types.MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": q}],
            ),
        )
        for i, q in enumerate(queries)
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"  Submitted batch {batch.id}")

    # Step 3: poll
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        counts = batch.request_counts
        if batch.processing_status == "ended":
            break
        print(
            f"  Polling... processing={counts.processing} "
            f"succeeded={counts.succeeded} errored={counts.errored}"
        )
        time.sleep(poll_interval)

    # Step 4: collect results
    for result in client.messages.batches.results(batch.id):
        if result.result.type == "succeeded":
            stats.add_usage(result.result.message.usage)
        else:
            print(f"  Warning: request {result.custom_id} failed: {result.result.type}")

    stats.elapsed_seconds = time.monotonic() - t0
    return stats


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_comparison(results: list[RunStats]) -> None:
    separator = "-" * 80
    print(f"\n{separator}")
    print("BENCHMARK RESULTS")
    print(separator)
    header = f"{'Mode':<16} {'Queries':>7} {'Input':>10} {'CacheWr':>10} {'CacheRd':>10} {'Output':>8} {'Cost':>8} {'Savings':>8}"
    print(header)
    print(separator)

    baseline_cost: Optional[float] = None
    for r in results:
        if baseline_cost is None:
            baseline_cost = r.actual_cost

        savings_vs_baseline = ""
        if baseline_cost and r.actual_cost < baseline_cost:
            pct = (baseline_cost - r.actual_cost) / baseline_cost * 100
            savings_vs_baseline = f"-{pct:.0f}%"

        print(
            f"{r.mode:<16} {r.n_queries:>7} "
            f"{r.input_tokens:>10,} {r.cache_write_tokens:>10,} {r.cache_read_tokens:>10,} "
            f"{r.output_tokens:>8,} "
            f"${r.actual_cost:>7.4f} {savings_vs_baseline:>8}"
        )

    print(separator)

    if len(results) >= 2:
        baseline = results[0]
        best = min(results, key=lambda r: r.actual_cost)
        abs_savings = baseline.actual_cost - best.actual_cost
        pct_savings = abs_savings / baseline.actual_cost * 100 if baseline.actual_cost else 0
        print(
            f"\nBest mode: {best.mode}  "
            f"Total savings vs no-cache: ${abs_savings:.4f} ({pct_savings:.1f}%)"
        )

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode",
        choices=["no-cache", "cache", "batch", "all"],
        default="all",
        help="Which benchmark modes to run (default: all)",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=100,
        help="Number of queries to run (default: 100; use 10 for a cheap smoke test)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    print("Generating synthetic document (~50K tokens)...")
    document = generate_document(target_chars=200_000)
    print(f"  Document length: {len(document):,} characters")

    print(f"\nGenerating {args.queries} queries...")
    queries = generate_queries(args.queries)

    results: list[RunStats] = []

    if args.mode in ("no-cache", "all"):
        print(f"\n[1/3] Running no-cache mode ({args.queries} queries)...")
        r = run_no_cache(client, document, queries)
        results.append(r)
        print(f"  Done in {r.elapsed_seconds:.1f}s  cost=${r.actual_cost:.4f}")

    if args.mode in ("cache", "all"):
        print(f"\n[2/3] Running cache mode ({args.queries} queries)...")
        r = run_with_cache(client, document, queries)
        results.append(r)
        print(f"  Done in {r.elapsed_seconds:.1f}s  cost=${r.actual_cost:.4f}")

    if args.mode in ("batch", "all"):
        print(f"\n[3/3] Running cache+batch mode ({args.queries} queries)...")
        r = run_cache_plus_batch(client, document, queries)
        results.append(r)
        print(f"  Done in {r.elapsed_seconds:.1f}s  cost=${r.actual_cost:.4f}")

    print_comparison(results)


if __name__ == "__main__":
    main()
