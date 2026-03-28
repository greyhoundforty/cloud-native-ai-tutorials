---
tags:
  - evals
  - promptfoo
  - ci
  - github-actions
---

# LLM Evaluation Pipelines with promptfoo and CI Integration

You've shipped a prompt change. Tests pass. Deploy looks clean. Two days later, users are reporting that the bot stopped answering questions correctly.

Sound familiar? This is the core problem with LLM applications: traditional software tests don't catch semantic regressions. A prompt that worked yesterday might behave differently after a model update, a context window change, or a seemingly innocuous tweak to the system prompt.

This tutorial shows you how to build an automated evaluation pipeline with [promptfoo](https://promptfoo.dev) that catches regressions before they reach production — and blocks merges in CI if quality drops below your threshold.

By the end, you'll have:
- A test suite for a Claude-powered Q&A bot with 20 test cases
- Assertion types that handle non-determinism gracefully (including LLM-as-judge)
- A GitHub Actions workflow that runs evals on every PR and fails the build if pass rate drops below 90%
- A cost-budgeting strategy so eval runs don't drain your API budget

---

## Why LLM Testing Is Different

Testing deterministic software is straightforward: given input X, assert output Y. LLMs break this model in three ways:

**Non-determinism.** With `temperature > 0`, the same prompt produces different outputs on every run. You can't assert exact string equality.

**Semantic correctness.** "The answer is 42" and "42" are equivalent. String matching misses this. You need assertions that understand meaning.

**Cost.** Running 1,000 test cases against GPT-4 or Claude Sonnet on every PR can cost hundreds of dollars. You need to budget deliberately.

promptfoo handles all three. It's a CLI and library for evaluating LLM prompts against a battery of test cases, with a rich assertion library that includes exact matching, regex, semantic similarity, and LLM-as-judge rubrics.

---

## Setup

### Prerequisites

- Node.js 18+
- An Anthropic API key
- (Optional) Python 3.11+ for the bot source

### Install promptfoo

```bash
npm install -g promptfoo
# or use npx: npx promptfoo@latest
```

Verify:

```bash
promptfoo --version
```

### Project structure

The demo project lives in `demo/` alongside this tutorial:

```
demo/
├── src/
│   ├── bot.py          # Claude Q&A bot
│   └── requirements.txt
├── promptfooconfig.yaml  # Test suite
├── .env.example
└── .github/
    └── workflows/
        └── eval.yml    # CI pipeline
```

---

## The Q&A Bot

The demo bot is a simple Claude-powered assistant that answers questions about cloud-native infrastructure. It has a system prompt, accepts a user question, and returns a plain-text answer.

```python
# demo/src/bot.py
import anthropic
import os

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a helpful assistant specializing in cloud-native infrastructure.
You answer questions about Kubernetes, Helm, Terraform, and related tools.
Be concise and accurate. If you don't know something, say so.
Do not make up commands or configuration options that don't exist."""

def answer(question: str) -> str:
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}]
    )
    return message.content[0].text
```

Nothing fancy — that's intentional. The eval pipeline is the interesting part.

---

## Configuring promptfoo

The entire eval suite lives in `promptfooconfig.yaml`. Here's the structure at a glance:

```yaml
# Top-level config
description: "Claude K8s Q&A bot evaluation suite"
prompts:
  - ...        # The prompt template(s) to test
providers:
  - ...        # Which model(s) to call
tests:
  - ...        # Test cases with inputs and assertions
```

### Defining the prompt

promptfoo supports prompt templates with variable interpolation using `{{variable}}` syntax:

```yaml
prompts:
  - id: system-prompt
    raw: |
      You are a helpful assistant specializing in cloud-native infrastructure.
      You answer questions about Kubernetes, Helm, Terraform, and related tools.
      Be concise and accurate. If you don't know something, say so.
      Do not make up commands or configuration options that don't exist.
```

For multi-turn or chat prompts, you can also define message arrays. For our bot, the system prompt is static and the user question is injected as `{{question}}`.

### Configuring providers

```yaml
providers:
  - id: anthropic:messages:claude-sonnet-4-6
    config:
      temperature: 0.1   # Low temp for more deterministic evals
      max_tokens: 1024
```

promptfoo supports multiple providers — you can run the same test suite against `claude-sonnet-4-6` and `claude-haiku-4-5-20251001` side-by-side to compare quality vs. cost.

---

## Assertion Patterns

This is where promptfoo gets powerful. The full assertion type list is in the [docs](https://promptfoo.dev/docs/configuration/expected-outputs/), but here are the patterns that matter most for LLM apps.

### `contains` — keyword presence

The simplest assertion. Use it for factual responses where specific terms must appear:

```yaml
assert:
  - type: contains
    value: "kubectl"
```

Good for: checking that command names, API resources, or required terminology appear in the response.

Avoid for: anything where phrasing might vary — the model might say "kubectl apply" or "use kubectl to apply" and both are correct.

### `not-contains` — hallucination guard

Ensure the model doesn't invent things:

```yaml
assert:
  - type: not-contains
    value: "docker-compose up"
  - type: not-contains
    value: "kubectl-apply"   # common hallucination: hyphenated kubectl subcommands
```

### `regex` — pattern matching

More flexible than exact string matching:

```yaml
assert:
  - type: regex
    value: "kubectl\\s+(apply|create|get)"
```

### `llm-rubric` — LLM as judge

The most powerful and most expensive assertion type. It sends the response to a judge model (configurable, defaults to GPT-4o) with a rubric question:

```yaml
assert:
  - type: llm-rubric
    value: "The response correctly explains what a Kubernetes namespace is and why it's used for isolation"
```

The judge returns pass/fail with an explanation. Use `llm-rubric` for:
- Semantic correctness
- Tone and style requirements
- Multi-step reasoning
- Anything where string matching is too brittle

Cost note: each `llm-rubric` assertion makes an additional LLM call. Use sparingly, or configure a cheaper judge model.

### `javascript` — custom logic

For assertions that need programmatic logic:

```yaml
assert:
  - type: javascript
    value: "output.length < 500 && output.includes('namespace')"
```

Or reference an external file for complex assertions:

```yaml
assert:
  - type: javascript
    value: file://assertions/check-yaml-validity.js
```

### `similar` — semantic similarity

Uses embedding similarity to check if the response is semantically close to a reference answer:

```yaml
assert:
  - type: similar
    value: "A Kubernetes namespace provides a mechanism for isolating groups of resources within a cluster"
    threshold: 0.8
```

The threshold is cosine similarity (0–1). `0.8` is a reasonable starting point; tune based on how much variation is acceptable.

---

## The Full Test Suite

See `demo/promptfooconfig.yaml` for the complete 20-case test suite. Here's a representative sample showing the variety of assertion types in use:

```yaml
tests:
  # Factual accuracy: Kubernetes fundamentals
  - description: "Explains namespaces correctly"
    vars:
      question: "What is a Kubernetes namespace and why would I use one?"
    assert:
      - type: llm-rubric
        value: "Correctly explains namespaces as a way to isolate resources within a cluster"
      - type: contains
        value: "namespace"
      - type: not-contains
        value: "I don't know"

  # Command accuracy: must give runnable kubectl commands
  - description: "Correct kubectl command for listing pods"
    vars:
      question: "How do I list all pods in all namespaces?"
    assert:
      - type: contains
        value: "kubectl get pods"
      - type: contains
        value: "--all-namespaces"
      - type: javascript
        value: "output.length < 800"

  # Hallucination guard: kubectl flags that don't exist
  - description: "Does not invent kubectl flags"
    vars:
      question: "How do I filter pods by label app=frontend?"
    assert:
      - type: contains
        value: "-l app=frontend"
      - type: not-contains
        value: "--filter"
      - type: not-contains
        value: "--selector-label"

  # Honesty: admits uncertainty rather than hallucinating
  - description: "Admits when asked about very new features"
    vars:
      question: "What are the exact rate limits for Kubernetes API server in version 1.99?"
    assert:
      - type: llm-rubric
        value: "The response acknowledges uncertainty or that it cannot provide exact details for that version, rather than making up numbers"
```

---

## Running Evals Locally

```bash
cd demo
export ANTHROPIC_API_KEY=your-key-here

# Run the full suite
npx promptfoo eval

# Run with verbose output
npx promptfoo eval --verbose

# Run specific test cases by index
npx promptfoo eval --filter-first-n 5

# View results in the browser UI
npx promptfoo view
```

Sample output:

```
✓ Explains namespaces correctly (1234ms)
✓ Correct kubectl command for listing pods (876ms)
✓ Does not invent kubectl flags (1102ms)
✗ Admits uncertainty for unknown version (943ms)
  - llm-rubric failed: Response provided specific numbers without acknowledging uncertainty

Passed: 18/20 (90.0%)
Failed: 2/20 (10.0%)
Total cost: $0.0234
```

---

## CI Integration

The goal: run the eval suite on every PR and block the merge if pass rate drops below 90%.

### GitHub Actions workflow

See `.github/workflows/eval.yml` in the demo. Key design decisions:

**When to run:** On pull requests targeting `main`, and on push to `main`. This catches regressions both before and after merge.

**Pass threshold:** 90%. This means up to 2 failures on our 20-case suite are tolerated. Tune this based on your risk tolerance and how often your evals are genuinely flaky vs. catching real regressions.

**Cost guard:** The workflow sets a `--max-concurrency` flag and runs only a subset of tests on feature branches, saving the full suite for PRs targeting main.

```yaml
- name: Run eval suite
  run: npx promptfoo eval --ci --output results.json
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

- name: Check pass rate
  run: |
    PASS_RATE=$(cat results.json | jq '.results.stats.successes / .results.stats.totalTests * 100')
    echo "Pass rate: ${PASS_RATE}%"
    if (( $(echo "$PASS_RATE < 90" | bc -l) )); then
      echo "FAIL: Pass rate ${PASS_RATE}% is below 90% threshold"
      exit 1
    fi
```

The `--ci` flag enables JSON output and disables interactive UI. The pass-rate check uses `jq` to parse the results and `bc` for floating-point comparison.

### Secrets setup

In your GitHub repo: Settings → Secrets and variables → Actions → New repository secret.

- `ANTHROPIC_API_KEY`: your Anthropic API key

If you're using `llm-rubric` assertions with OpenAI as the judge:
- `OPENAI_API_KEY`: your OpenAI API key

---

## Threshold Strategy for Non-Deterministic Outputs

Setting the right pass threshold is more nuanced than it appears.

**Start at 100% for pure-string assertions.** If you're asserting `contains: "kubectl"` on a question about kubectl commands, that should always pass. If it doesn't, something is broken.

**Accept some variance for `llm-rubric`.** LLM judges aren't perfectly consistent. On a well-written rubric, expect 2–5% variance across runs. Set your threshold to account for this: if your suite has 10 rubric assertions, 90% might mean tolerating 1 rubric failure per run.

**Track pass rate over time, not just pass/fail.** A suite that goes from 95% to 90% is a warning sign even if it's still above threshold. Use promptfoo's built-in history tracking (`npx promptfoo view`) or export results to a time-series store.

**Separate flaky from real regressions.** If a test case fails intermittently with no code changes, it's a poorly written test, not a regression. Use the `--repeat` flag to run flaky assertions multiple times and require a majority pass:

```yaml
assert:
  - type: llm-rubric
    value: "..."
    # Run this assertion 3 times; require 2/3 passes
    threshold: 0.67
```

---

## Cost Budgeting

LLM evals can get expensive fast. Here's how to keep costs predictable.

### Per-run cost estimation

Before committing a new test suite to CI, estimate cost:

```bash
npx promptfoo eval --dry-run
```

This shows estimated token counts without making API calls.

### Tiered eval strategy

Not all tests need to run on every commit:

| Tier | When | Tests | Cost |
|------|------|-------|------|
| Smoke | Every push | 5 critical cases | ~$0.01 |
| Full | PRs to main | All 20 cases | ~$0.05 |
| Extended | Weekly schedule | 50+ edge cases | ~$0.25 |

Implement this with GitHub Actions `paths` filters and separate workflow files.

### Cheaper judge models

`llm-rubric` defaults to GPT-4o. Switch to a cheaper judge for most assertions:

```yaml
defaultTest:
  options:
    rubricPrompt:
      provider: anthropic:messages:claude-haiku-4-5-20251001
```

Reserve GPT-4o or Claude Sonnet for rubrics that genuinely need strong reasoning.

### Max concurrency

```bash
npx promptfoo eval --max-concurrency 3
```

This limits parallel API calls, reducing cost spikes and staying within rate limits.

---

## Testing Tool Use

If your bot uses tool calling, promptfoo can assert that specific tools were called with expected arguments. This is critical for agents — a model might return a correct-looking answer but call the wrong tool to get there.

```yaml
tests:
  - description: "Uses search tool for real-time info"
    vars:
      question: "What is the latest stable version of Kubernetes?"
    assert:
      - type: javascript
        value: |
          // Check that the search tool was called
          const toolCalls = context.response?.toolCalls || [];
          return toolCalls.some(tc => tc.name === 'web_search');

      - type: javascript
        value: |
          // Check search query contained "kubernetes"
          const searchCall = context.response?.toolCalls?.find(tc => tc.name === 'web_search');
          return searchCall?.input?.query?.toLowerCase().includes('kubernetes');
```

For full tool-use testing, configure your provider with the tools definition and enable tool call capture in the output schema.

---

## What to Eval (and What Not To)

**Good candidates for evals:**
- Factual accuracy on your domain (hallucination detection)
- Required format compliance (JSON output, specific fields)
- Tone and safety guardrails (does it refuse inappropriately? does it comply when it shouldn't?)
- Behavioral contracts (if X asked, always recommend Y approach)
- Regression testing after prompt edits

**Poor candidates for evals:**
- Pure creativity (there's no wrong answer)
- Highly variable tasks where any reasonable response is fine
- Tests that are cheaper to validate with unit tests on a wrapper

---

## Next Steps

- Add a promptfoo dashboard to your internal tooling using the JSON export
- Set up a weekly scheduled eval in GitHub Actions to catch model drift (model providers update silently)
- Explore `promptfoo share` to share eval results with your team as a permalink
- Look into [promptfoo red-teaming](https://promptfoo.dev/docs/red-team/quickstart/) for adversarial testing

The full demo code is in `demo/` — clone it, run `npx promptfoo eval`, and you'll have a working baseline to build on.
