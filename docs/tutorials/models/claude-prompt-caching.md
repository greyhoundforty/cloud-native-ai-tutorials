---
tags:
  - claude-api
  - cost-optimization
  - caching
---

# Prompt Caching and Cost Optimization with the Claude API

If you're calling Claude in a loop — answering questions about a large document, running evaluations, or powering a multi-turn assistant — you're probably sending the same tokens over and over. Prompt caching lets you pay for those tokens once and reuse the cached version on subsequent requests. Combined with the Message Batches API for async workloads, you can cut input-token costs by 80% or more.

This guide covers:
- How prompt caching works under the hood
- When and what to cache
- Measuring real savings with before/after comparisons
- Batching requests when latency isn't critical
- Combining caching + batching for maximum efficiency
- Production-ready Python patterns

All examples use the [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python). A complete benchmark script is included at the end.

---

## How Prompt Caching Works

When you mark a content block with `"cache_control": {"type": "ephemeral"}`, Anthropic stores that prefix in a server-side cache keyed to your API account. On subsequent requests that send the exact same prefix, the cached version is retrieved instead of re-processing the tokens.

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-5",
    max_tokens=1024,
    system=[
        {
            "type": "text",
            "text": "You are a helpful assistant...",
        },
        {
            "type": "text",
            "text": "<reference_document>\n" + large_document + "\n</reference_document>",
            "cache_control": {"type": "ephemeral"},  # cache this block
        },
    ],
    messages=[{"role": "user", "content": "Summarize the key findings."}],
)
```

### Cache TTL

The cache has a **5-minute TTL** that resets on each cache hit. In practice:
- A steady stream of requests keeps the cache warm indefinitely
- Bursts with gaps >5 minutes between them will incur a cache miss on restart
- Cache warming (a no-op call before a batch) is a useful pattern for predictable workloads

### Cache Hit Metrics

The API response includes token usage broken down by cache status:

```python
usage = response.usage
print(f"Input tokens:          {usage.input_tokens}")
print(f"Cache creation tokens: {usage.cache_creation_input_tokens}")
print(f"Cache read tokens:     {usage.cache_read_input_tokens}")
print(f"Output tokens:         {usage.output_tokens}")
```

`cache_creation_input_tokens` appears on the first call (you pay full price to create the cache entry). `cache_read_input_tokens` appears on subsequent hits — these cost 90% less than regular input tokens.

### Pricing Summary (claude-opus-4-5)

| Token type           | Price per million tokens |
|----------------------|--------------------------|
| Input (standard)     | $15.00                   |
| Cache write          | $18.75 (1.25× standard)  |
| Cache read           | $1.50  (0.10× standard)  |
| Output               | $75.00                   |

Cache writes cost slightly more than standard input — you break even after about 1.1 cache hits, so caching is profitable from the second request onwards.

---

## What to Cache

### Large System Prompts

If your system prompt is substantial (persona, rules, response format instructions), mark the entire thing cacheable:

```python
system = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,  # 2,000+ tokens
        "cache_control": {"type": "ephemeral"},
    }
]
```

### Reference Documents

RAG-style workflows that inject a document into every call are the highest-value caching target. A 50K-token document cached for 100 queries saves ~4.5M input tokens.

```python
def build_system_with_doc(document: str) -> list[dict]:
    return [
        {"type": "text", "text": "You are an expert analyst."},
        {
            "type": "text",
            "text": f"<document>\n{document}\n</document>",
            "cache_control": {"type": "ephemeral"},
        },
    ]
```

### Few-Shot Examples

Long few-shot example blocks are stable across requests and cache well:

```python
system = [
    {"type": "text", "text": TASK_INSTRUCTIONS},
    {
        "type": "text",
        "text": FEW_SHOT_EXAMPLES,  # 5,000 tokens of examples
        "cache_control": {"type": "ephemeral"},
    },
]
```

### What Not to Cache

- Short prompts (<1,000 tokens): overhead isn't worth it
- Highly dynamic content that changes every request
- One-off calls where there's no second request to benefit from the cache

---

## Measuring Savings

Track `cache_read_input_tokens` across a session to calculate real savings:

```python
from dataclasses import dataclass, field

# Pricing for claude-opus-4-5 (per million tokens)
PRICE_INPUT_PER_M      = 15.00
PRICE_CACHE_WRITE_PER_M = 18.75
PRICE_CACHE_READ_PER_M  = 1.50
PRICE_OUTPUT_PER_M     = 75.00


@dataclass
class CostAccumulator:
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage) -> None:
        self.input_tokens          += usage.input_tokens
        self.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0)
        self.cache_read_tokens     += getattr(usage, "cache_read_input_tokens", 0)
        self.output_tokens         += usage.output_tokens

    @property
    def actual_cost(self) -> float:
        return (
            self.input_tokens          / 1_000_000 * PRICE_INPUT_PER_M
            + self.cache_creation_tokens / 1_000_000 * PRICE_CACHE_WRITE_PER_M
            + self.cache_read_tokens     / 1_000_000 * PRICE_CACHE_READ_PER_M
            + self.output_tokens         / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    @property
    def nocache_cost(self) -> float:
        """What this would have cost without caching."""
        total_input = self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens
        return (
            total_input      / 1_000_000 * PRICE_INPUT_PER_M
            + self.output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    def summary(self) -> str:
        savings = self.nocache_cost - self.actual_cost
        pct = savings / self.nocache_cost * 100 if self.nocache_cost > 0 else 0
        return (
            f"Actual cost:    ${self.actual_cost:.4f}\n"
            f"No-cache cost:  ${self.nocache_cost:.4f}\n"
            f"Savings:        ${savings:.4f} ({pct:.1f}%)\n"
            f"Cache reads:    {self.cache_read_tokens:,} tokens\n"
            f"Cache writes:   {self.cache_creation_tokens:,} tokens"
        )
```

---

## Batching with the Message Batches API

For async workloads — nightly report generation, bulk evaluation, offline enrichment — the Message Batches API lets you submit up to 10,000 requests in one call and retrieve results when they're ready (within 24 hours, usually within an hour). Batched requests cost **50% less** than synchronous calls.

```python
import anthropic, time

client = anthropic.Anthropic()


def submit_batch(queries: list[str], system: list[dict]) -> str:
    requests = [
        anthropic.types.message_create_params.Request(
            custom_id=f"query-{i}",
            params=anthropic.types.MessageCreateParamsNonStreaming(
                model="claude-opus-4-5",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": q}],
            ),
        )
        for i, q in enumerate(queries)
    ]

    batch = client.messages.batches.create(requests=requests)
    print(f"Submitted batch {batch.id} with {len(requests)} requests")
    return batch.id


def poll_batch(batch_id: str, poll_interval: int = 30) -> list[dict]:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        print(f"  {batch.request_counts.processing} processing, "
              f"{batch.request_counts.succeeded} done...")
        time.sleep(poll_interval)

    results = []
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            results.append({
                "id": result.custom_id,
                "content": result.result.message.content[0].text,
                "usage": result.result.message.usage,
            })
    return results
```

### When Batching Makes Sense

| Use case                    | Sync API | Batches API |
|-----------------------------|----------|-------------|
| Interactive chat             | ✅       | ❌          |
| Real-time classification     | ✅       | ❌          |
| Nightly report generation    | ❌       | ✅          |
| Bulk document analysis       | ❌       | ✅          |
| Evaluation runs / evals      | ❌       | ✅          |
| CI/CD LLM-as-judge           | ❌       | ✅          |

---

## Combining Caching + Batching

Prompt caching and batching are complementary. Within a batch, requests that share the same cached prefix hit the cache — so you pay cache-write price once and cache-read price for the rest.

The strategy: warm the cache with a single sync call, then submit the full batch.

```python
def run_cached_batch(document: str, queries: list[str]) -> list[dict]:
    system = [
        {"type": "text", "text": "You are an expert analyst. Answer concisely."},
        {
            "type": "text",
            "text": f"<document>\n{document}\n</document>",
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # 1. Warm the cache with a cheap sentinel query
    print("Warming cache...")
    warmup = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=16,
        system=system,
        messages=[{"role": "user", "content": "Ready."}],
    )
    print(f"  Cache write: {warmup.usage.cache_creation_input_tokens:,} tokens")

    # 2. Submit the full batch
    batch_id = submit_batch(queries, system)

    # 3. Collect results
    return poll_batch(batch_id)
```

---

## Cache-Aware Client Wrapper

For production use, wrap the client to track cache metrics automatically:

```python
from anthropic import Anthropic
from dataclasses import dataclass, field


@dataclass
class CacheStats:
    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0

    @property
    def hit_rate(self) -> float:
        return self.cache_hits / self.requests if self.requests else 0.0


class CachedClient:
    """Anthropic client wrapper that tracks prompt cache performance."""

    def __init__(self, **kwargs):
        self._client = Anthropic(**kwargs)
        self.stats = CacheStats()

    def create(self, **kwargs) -> anthropic.types.Message:
        response = self._client.messages.create(**kwargs)
        usage = response.usage

        self.stats.requests += 1
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_write = getattr(usage, "cache_creation_input_tokens", 0)

        if cache_read > 0:
            self.stats.cache_hits += 1
            self.stats.total_cache_read_tokens += cache_read
        elif cache_write > 0:
            self.stats.cache_misses += 1
            self.stats.total_cache_write_tokens += cache_write

        return response

    def report(self) -> str:
        s = self.stats
        return (
            f"Requests: {s.requests} | "
            f"Hit rate: {s.hit_rate:.1%} | "
            f"Cache reads: {s.total_cache_read_tokens:,} tokens | "
            f"Cache writes: {s.total_cache_write_tokens:,} tokens"
        )
```

---

## Summary

| Technique         | Cost reduction         | Latency impact      | Best for                          |
|-------------------|------------------------|---------------------|-----------------------------------|
| Prompt caching    | Up to 90% on inputs    | Faster (cache hit)  | Repeated large prefixes           |
| Message Batches   | 50% on all tokens      | Higher (async only) | Offline / bulk workloads          |
| Cache + Batches   | 85–95% on inputs       | Async               | Large-doc Q&A at scale            |

The demo script (`benchmark.py`) in this directory runs a concrete benchmark: a 50K-token reference document queried 100 times, comparing no-cache vs. cache-only vs. cache+batch. Run it to see real numbers against your account.

---

## Next Steps

- [Prompt caching guide](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching) — official reference
- [Message Batches API](https://docs.anthropic.com/en/docs/build-with-claude/message-batches) — official reference
- [Token counting](https://docs.anthropic.com/en/docs/build-with-claude/token-counting) — pre-flight token estimation
