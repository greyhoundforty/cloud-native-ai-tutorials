---
title: "Cut Claude API Costs 80%+ with Prompt Caching and Message Batches"
published: false
description: "Prompt caching lets you pay for large token prefixes once and reuse them. Message Batches cut all token costs by 50%. Combined, you can slash input costs by 85–95% on document Q&A workloads."
tags: ["ai", "python", "claude", "productivity"]
canonical_url: "https://greyhoundforty-tutorials.netlify.app/tutorials/models/claude-prompt-caching/"
---

If you're calling Claude in a loop — answering questions about a large document, running evaluations, or powering a multi-turn assistant — you're probably sending the same tokens over and over. That's expensive and slow. Two Claude API features eliminate this waste:

- **Prompt caching**: pay full price once, then 90% less on every subsequent read
- **Message Batches API**: 50% off all tokens for async workloads, up to 10,000 requests per batch

## How prompt caching works

Mark any content block with `"cache_control": {"type": "ephemeral"}` and the API caches that prefix server-side, keyed to your account:

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-5",
    max_tokens=1024,
    system=[
        {"type": "text", "text": "You are a helpful assistant."},
        {
            "type": "text",
            "text": "<reference_document>\n" + large_document + "\n</reference_document>",
            "cache_control": {"type": "ephemeral"},  # cache this block
        },
    ],
    messages=[{"role": "user", "content": "Summarize the key findings."}],
)
```

The cache has a **5-minute TTL** that resets on each cache hit. A steady stream of requests keeps it warm indefinitely.

### Pricing breakdown (claude-opus-4-5)

| Token type | Price per million |
|-----------|-------------------|
| Standard input | $15.00 |
| Cache write | $18.75 (1.25×) |
| Cache read | $1.50 (0.10×) |
| Output | $75.00 |

Cache writes cost slightly more — but you break even after just 1.1 hits. From the second request onwards, caching pays.

### Measuring actual savings

The API response includes cache usage:

```python
usage = response.usage
print(f"Input tokens:          {usage.input_tokens}")
print(f"Cache creation tokens: {usage.cache_creation_input_tokens}")
print(f"Cache read tokens:     {usage.cache_read_input_tokens}")
```

Track accumulated savings across a session:

```python
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
            self.input_tokens          / 1_000_000 * 15.00
            + self.cache_creation_tokens / 1_000_000 * 18.75
            + self.cache_read_tokens     / 1_000_000 * 1.50
            + self.output_tokens         / 1_000_000 * 75.00
        )
```

## What to cache

- **Large system prompts** (2,000+ token personas, rules, format instructions)
- **Reference documents** — a 50K-token doc cached for 100 queries saves ~4.5M input tokens
- **Few-shot example blocks** — stable across requests, expensive to resend

What **not** to cache: short prompts (<1,000 tokens), highly dynamic content, one-off calls.

## Message Batches API for async workloads

For nightly jobs, bulk analysis, and eval runs — workloads where latency doesn't matter — the Batches API cuts all token costs by 50%:

```python
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
    return batch.id
```

Poll for results:

```python
def poll_batch(batch_id: str, poll_interval: int = 30) -> list[dict]:
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        time.sleep(poll_interval)

    return [
        {"id": r.custom_id, "content": r.result.message.content[0].text}
        for r in client.messages.batches.results(batch_id)
        if r.result.type == "succeeded"
    ]
```

| Use case | Sync API | Batches API |
|---------|----------|-------------|
| Interactive chat | ✅ | ❌ |
| Nightly report generation | ❌ | ✅ |
| Bulk document analysis | ❌ | ✅ |
| Eval runs | ❌ | ✅ |

## The power combo: cache + batch

Warm the cache with one sync call, then submit the full batch. Requests within the batch share the cached prefix — you pay cache-write price once and cache-read price for the rest:

```python
def run_cached_batch(document: str, queries: list[str]) -> list[dict]:
    system = [
        {"type": "text", "text": "You are an expert analyst."},
        {
            "type": "text",
            "text": f"<document>\n{document}\n</document>",
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # Warm the cache
    client.messages.create(
        model="claude-opus-4-5", max_tokens=16,
        system=system,
        messages=[{"role": "user", "content": "Ready."}],
    )

    batch_id = submit_batch(queries, system)
    return poll_batch(batch_id)
```

## Summary

| Technique | Cost reduction | Best for |
|-----------|---------------|---------|
| Prompt caching | Up to 90% on inputs | Repeated large prefixes |
| Message Batches | 50% on all tokens | Offline / bulk workloads |
| Cache + Batches | 85–95% on inputs | Large-doc Q&A at scale |

The full tutorial includes a `benchmark.py` script that runs a concrete 50K-token / 100-query benchmark comparing all three approaches against your account.

---

→ **Full tutorial + demo code:** [greyhoundforty-tutorials.netlify.app](https://greyhoundforty-tutorials.netlify.app/tutorials/models/claude-prompt-caching/)
