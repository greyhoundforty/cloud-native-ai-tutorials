# LLM Observability with OpenTelemetry and Grafana

Production LLM applications fail in ways that standard APM tools miss entirely. A request might succeed (HTTP 200) while silently burning $0.50 in tokens, taking 12 seconds to first token, or retrying three times before getting a coherent response. This tutorial builds a real observability stack for a FastAPI + Claude app — traces in Tempo, metrics in Prometheus, dashboards in Grafana, and alerting on what actually matters.

## What makes LLM observability different

Standard app observability tracks latency, error rate, and throughput. That's necessary but not sufficient for LLM apps. The signals you care about are:

| Signal | Why it matters |
|---|---|
| **Time to first token (TTFT)** | Streaming UX quality; distinct from total latency |
| **Total generation time** | End-to-end user wait |
| **Input token count** | Direct cost driver; scales with prompt engineering choices |
| **Output token count** | Variable cost; correlates with response quality |
| **Model used** | Cost/capability tradeoff visibility |
| **Error type** | Rate limits vs context length vs API errors behave differently |
| **Prompt template version** | Did a prompt change break quality? |

You can't answer "why did costs spike on Tuesday?" with just HTTP metrics. You need per-request token counts tied to traces.

## The Stack

```
FastAPI app
    │
    ├── OpenTelemetry SDK (traces + metrics)
    │         │
    │         └── OTLP gRPC → OTel Collector
    │                              │
    │                  ┌──────────┴──────────┐
    │                  ▼                     ▼
    │             Grafana Tempo         Prometheus
    │            (trace store)       (metrics store)
    │                  │                     │
    └──────────────────┴─────────────────────┘
                              │
                          Grafana
                    (dashboards + alerts)
```

**Versions used:** Python 3.12, FastAPI 0.115, opentelemetry-sdk 1.29, Grafana 11, Tempo 2.6, Prometheus 2.55, OTel Collector Contrib 0.115.

## Project structure

```
llm-observability-otel/
├── app/
│   ├── main.py              # FastAPI app with OTel instrumentation
│   ├── telemetry.py         # OTel setup (traces + metrics)
│   ├── llm.py               # Claude API wrapper with span instrumentation
│   └── requirements.txt
├── otel-collector/
│   └── config.yaml          # OTel Collector pipeline config
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   └── datasources.yaml
│   │   └── dashboards/
│   │       ├── dashboard.yaml
│   │       └── llm-dashboard.json
│   └── alerting/
│       └── rules.yaml
├── prometheus/
│   └── prometheus.yml
├── tempo/
│   └── tempo.yaml
└── docker-compose.yml
```

## Step 1: The FastAPI application

### `app/requirements.txt`

```
fastapi==0.115.5
uvicorn[standard]==0.32.1
anthropic==0.40.0
opentelemetry-api==1.29.0
opentelemetry-sdk==1.29.0
opentelemetry-exporter-otlp-proto-grpc==1.29.0
opentelemetry-instrumentation-fastapi==0.50b0
opentelemetry-instrumentation-httpx==0.50b0
```

### `app/telemetry.py`

This module wires up both trace and metric providers, exporting via OTLP gRPC to the collector.

```python
import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.semconv.resource import ResourceAttributes

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "llm-app")


def setup_telemetry() -> tuple[trace.Tracer, metrics.Meter]:
    resource = Resource.create({
        ResourceAttributes.SERVICE_NAME: SERVICE_NAME,
        ResourceAttributes.SERVICE_VERSION: "1.0.0",
        ResourceAttributes.DEPLOYMENT_ENVIRONMENT: os.getenv("ENV", "development"),
    })

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    # Metrics (export every 15 seconds)
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True),
        export_interval_millis=15_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    tracer = trace.get_tracer(SERVICE_NAME)
    meter = metrics.get_meter(SERVICE_NAME)
    return tracer, meter
```

### `app/llm.py`

The core instrumentation lives here. Every call to the Claude API gets a span with token counts, model name, and timing recorded as span attributes and metric data points.

```python
import time
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import anthropic

# Semantic conventions for LLM spans (following OpenTelemetry GenAI spec)
LLM_SYSTEM = "gen_ai.system"
LLM_REQUEST_MODEL = "gen_ai.request.model"
LLM_RESPONSE_MODEL = "gen_ai.response.model"
LLM_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
LLM_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
LLM_OPERATION_NAME = "gen_ai.operation.name"


class InstrumentedLLMClient:
    def __init__(self, tracer, meter):
        self.client = anthropic.Anthropic()
        self.tracer = tracer

        # Histograms give you p50/p95/p99 in Grafana out of the box
        self.latency_histogram = meter.create_histogram(
            name="llm.request.duration",
            description="Total LLM request duration in seconds",
            unit="s",
        )
        self.ttft_histogram = meter.create_histogram(
            name="llm.time_to_first_token",
            description="Time to first token in seconds",
            unit="s",
        )
        self.input_tokens_histogram = meter.create_histogram(
            name="llm.usage.input_tokens",
            description="Input token count per request",
            unit="{token}",
        )
        self.output_tokens_histogram = meter.create_histogram(
            name="llm.usage.output_tokens",
            description="Output token count per request",
            unit="{token}",
        )
        self.error_counter = meter.create_counter(
            name="llm.errors",
            description="Number of LLM request errors",
        )

    def chat(
        self,
        prompt: str,
        model: str = "claude-3-5-haiku-20241022",
        max_tokens: int = 1024,
        system: str | None = None,
    ) -> dict:
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system

        attrs = {
            LLM_SYSTEM: "anthropic",
            LLM_REQUEST_MODEL: model,
            LLM_OPERATION_NAME: "chat",
        }

        with self.tracer.start_as_current_span("llm.chat", attributes=attrs) as span:
            start = time.perf_counter()
            try:
                response = self.client.messages.create(**kwargs)
                duration = time.perf_counter() - start

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                response_model = response.model

                # Record span attributes
                span.set_attribute(LLM_RESPONSE_MODEL, response_model)
                span.set_attribute(LLM_USAGE_INPUT_TOKENS, input_tokens)
                span.set_attribute(LLM_USAGE_OUTPUT_TOKENS, output_tokens)
                span.set_status(Status(StatusCode.OK))

                # Record metrics with model label for per-model breakdown
                metric_attrs = {"model": response_model, "status": "success"}
                self.latency_histogram.record(duration, metric_attrs)
                self.input_tokens_histogram.record(input_tokens, metric_attrs)
                self.output_tokens_histogram.record(output_tokens, metric_attrs)

                return {
                    "text": response.content[0].text,
                    "model": response_model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_s": round(duration, 3),
                }

            except anthropic.RateLimitError as e:
                self._handle_error(span, e, model, "rate_limit")
                raise
            except anthropic.BadRequestError as e:
                self._handle_error(span, e, model, "bad_request")
                raise
            except Exception as e:
                self._handle_error(span, e, model, "unknown")
                raise

    def _handle_error(self, span, exc, model: str, error_type: str):
        span.set_status(Status(StatusCode.ERROR, str(exc)))
        span.record_exception(exc)
        self.error_counter.add(1, {"model": model, "error_type": error_type})
```

### `app/main.py`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from .telemetry import setup_telemetry
from .llm import InstrumentedLLMClient

tracer = None
llm_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tracer, llm_client
    t, meter = setup_telemetry()
    tracer = t
    llm_client = InstrumentedLLMClient(tracer=t, meter=meter)
    yield


app = FastAPI(title="LLM Observability Demo", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


class ChatRequest(BaseModel):
    prompt: str
    model: str = "claude-3-5-haiku-20241022"
    max_tokens: int = 1024
    system: str | None = None


class ChatResponse(BaseModel):
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_s: float


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        result = llm_client.chat(
            prompt=req.prompt,
            model=req.model,
            max_tokens=req.max_tokens,
            system=req.system,
        )
        return ChatResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
```

## Step 2: The infrastructure stack

### `docker-compose.yml`

```yaml
services:
  app:
    build: ./app
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
      - OTEL_SERVICE_NAME=llm-app
      - ENV=development
    depends_on:
      - otel-collector

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.115.0
    volumes:
      - ./otel-collector/config.yaml:/etc/otelcol-contrib/config.yaml
    ports:
      - "4317:4317"   # OTLP gRPC
      - "4318:4318"   # OTLP HTTP
      - "8889:8889"   # Prometheus metrics scrape endpoint
    depends_on:
      - tempo
      - prometheus

  tempo:
    image: grafana/tempo:2.6.1
    command: ["-config.file=/etc/tempo.yaml"]
    volumes:
      - ./tempo/tempo.yaml:/etc/tempo.yaml
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"   # Tempo HTTP API
      - "9095:9095"   # Tempo gRPC

  prometheus:
    image: prom/prometheus:v2.55.1
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=15d"

  grafana:
    image: grafana/grafana:11.3.2
    ports:
      - "3000:3000"
    environment:
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
      - GF_FEATURE_TOGGLES_ENABLE=traceqlEditor
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning
      - grafana-data:/var/lib/grafana

volumes:
  tempo-data:
  prometheus-data:
  grafana-data:
```

### `otel-collector/config.yaml`

The collector is the routing layer. It receives OTLP from the app, fans traces out to Tempo, and exposes a Prometheus scrape endpoint for metrics.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 512
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
    spike_limit_mib: 128

exporters:
  otlp/tempo:
    endpoint: tempo:9095
    tls:
      insecure: true

  prometheus:
    endpoint: "0.0.0.0:8889"
    namespace: llm
    send_timestamps: true
    metric_expiration: 3m

  debug:
    verbosity: basic

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo, debug]

    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus, debug]
```

### `tempo/tempo.yaml`

```yaml
server:
  http_listen_port: 3200
  grpc_listen_port: 9095

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:9095

ingester:
  trace_idle_period: 10s
  max_block_bytes: 1_000_000
  max_block_duration: 5m

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal

compactor:
  compaction:
    compaction_window: 1h

query_frontend:
  search:
    duration_slo: 5s
    throughput_bytes_slo: 1.073741824e+09
```

### `prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "otel-collector"
    static_configs:
      - targets: ["otel-collector:8889"]
    metric_relabel_configs:
      # Drop internal collector metrics we don't care about
      - source_labels: [__name__]
        regex: "otelcol_.*"
        action: drop
```

### Grafana data source provisioning

**`grafana/provisioning/datasources/datasources.yaml`**

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus
    url: http://prometheus:9090
    isDefault: true
    jsonData:
      httpMethod: POST

  - name: Tempo
    type: tempo
    uid: tempo
    url: http://tempo:3200
    jsonData:
      tracesToLogsV2:
        datasourceUid: ""
      serviceMap:
        datasourceUid: prometheus
      nodeGraph:
        enabled: true
      traceQuery:
        timeShiftEnabled: true
        spanStartTimeShift: "-5m"
        spanEndTimeShift: "5m"
```

## Step 3: The Grafana dashboard

The dashboard JSON below provisions automatically. It covers four panels:

1. **Request rate** — requests/sec by model
2. **p50 / p95 latency** — `llm_llm_request_duration_seconds` histogram
3. **Token burn rate** — input + output tokens per minute
4. **Error rate** — errors/sec by type

**`grafana/provisioning/dashboards/dashboard.yaml`**

```yaml
apiVersion: 1

providers:
  - name: LLM Dashboards
    orgId: 1
    folder: LLM
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards
```

**`grafana/provisioning/dashboards/llm-dashboard.json`**

```json
{
  "title": "LLM Observability",
  "uid": "llm-observability",
  "schemaVersion": 39,
  "refresh": "30s",
  "time": { "from": "now-1h", "to": "now" },
  "templating": {
    "list": [
      {
        "name": "model",
        "type": "query",
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "query": "label_values(llm_llm_request_duration_seconds_count, model)",
        "includeAll": true,
        "allValue": ".*",
        "multi": true
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Request Rate (req/s)",
      "type": "timeseries",
      "gridPos": { "x": 0, "y": 0, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        {
          "expr": "sum by(model) (rate(llm_llm_request_duration_seconds_count{model=~\"$model\"}[2m]))",
          "legendFormat": "{{model}}"
        }
      ]
    },
    {
      "id": 2,
      "title": "Latency — p50 / p95 (seconds)",
      "type": "timeseries",
      "gridPos": { "x": 12, "y": 0, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        {
          "expr": "histogram_quantile(0.50, sum by(le, model) (rate(llm_llm_request_duration_seconds_bucket{model=~\"$model\"}[5m])))",
          "legendFormat": "p50 {{model}}"
        },
        {
          "expr": "histogram_quantile(0.95, sum by(le, model) (rate(llm_llm_request_duration_seconds_bucket{model=~\"$model\"}[5m])))",
          "legendFormat": "p95 {{model}}"
        }
      ]
    },
    {
      "id": 3,
      "title": "Token Burn Rate (tokens/min)",
      "type": "timeseries",
      "gridPos": { "x": 0, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        {
          "expr": "sum by(model) (rate(llm_llm_usage_input_tokens_sum{model=~\"$model\"}[2m])) * 60",
          "legendFormat": "input {{model}}"
        },
        {
          "expr": "sum by(model) (rate(llm_llm_usage_output_tokens_sum{model=~\"$model\"}[2m])) * 60",
          "legendFormat": "output {{model}}"
        }
      ]
    },
    {
      "id": 4,
      "title": "Error Rate (errors/s)",
      "type": "timeseries",
      "gridPos": { "x": 12, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "targets": [
        {
          "expr": "sum by(error_type) (rate(llm_llm_errors_total[2m]))",
          "legendFormat": "{{error_type}}"
        }
      ]
    }
  ]
}
```

## Step 4: Alerting rules

Grafana can evaluate Prometheus queries and fire alerts. The two rules below cover the most common production incidents: latency spikes and runaway token costs.

**`grafana/alerting/rules.yaml`**

```yaml
apiVersion: 1

groups:
  - orgId: 1
    name: llm-alerts
    folder: LLM Alerts
    interval: 1m
    rules:
      - uid: llm-high-latency
        title: LLM p95 Latency > 10s
        condition: C
        data:
          - refId: A
            datasourceUid: prometheus
            model:
              expr: >
                histogram_quantile(0.95,
                  sum by(le) (rate(llm_llm_request_duration_seconds_bucket[5m]))
                )
              intervalMs: 1000
              maxDataPoints: 43200
          - refId: C
            datasourceUid: "__expr__"
            model:
              type: threshold
              conditions:
                - evaluator:
                    params: [10]
                    type: gt
                  operator:
                    type: and
                  query:
                    params: [A]
        noDataState: NoData
        execErrState: Alerting
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "LLM p95 latency exceeded 10s"
          description: "p95 latency is {{ humanizeDuration $value }} over the last 5 minutes"

      - uid: llm-token-burn-spike
        title: Token Burn Rate > 10k tokens/min
        condition: C
        data:
          - refId: A
            datasourceUid: prometheus
            model:
              expr: >
                sum(rate(llm_llm_usage_input_tokens_sum[2m]) +
                    rate(llm_llm_usage_output_tokens_sum[2m])) * 60
              intervalMs: 1000
              maxDataPoints: 43200
          - refId: C
            datasourceUid: "__expr__"
            model:
              type: threshold
              conditions:
                - evaluator:
                    params: [10000]
                    type: gt
                  operator:
                    type: and
                  query:
                    params: [A]
        noDataState: NoData
        execErrState: Alerting
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Token burn rate spike detected"
          description: "{{ humanize $value }} tokens/min over the last 5 minutes — check for runaway loops or prompt injection"
```

## Running it

```bash
# Export your API key
export ANTHROPIC_API_KEY=sk-ant-...

# Start the stack
docker compose up -d

# Wait for health checks (Grafana takes ~10 seconds)
docker compose ps

# Send a test request
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Summarize what OpenTelemetry is in two sentences"}' | jq .
```

Open Grafana at `http://localhost:3000` — the **LLM Observability** dashboard is pre-provisioned under the **LLM** folder. Run a few requests and you'll see latency histograms and token burn graphs populate within 30 seconds.

To explore a trace, click any data point in the **Request Rate** panel → **View in Traces** → Tempo will show the full span tree with token counts as attributes.

## Deploying to Kubernetes

For production, the docker-compose services become Helm chart deployments. The only change in the application layer is the collector endpoint:

```yaml
# values.yaml excerpt
env:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector.monitoring.svc.cluster.local:4317"
  - name: OTEL_SERVICE_NAME
    value: "llm-app"
```

The Grafana, Tempo, and Prometheus components map naturally to the [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack) Helm chart (includes Prometheus + Grafana with alertmanager) and the [grafana/tempo](https://github.com/grafana/helm-charts/tree/main/charts/tempo) chart. The OTel Collector runs as a `DaemonSet` via the [opentelemetry-collector](https://github.com/open-telemetry/opentelemetry-helm-charts) chart so every node can receive OTLP without cross-node traffic.

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts

helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f monitoring-values.yaml

helm install tempo grafana/tempo \
  -n monitoring \
  -f tempo-values.yaml

helm install otel-collector open-telemetry/opentelemetry-collector \
  -n monitoring \
  --set mode=daemonset \
  -f otel-collector-values.yaml
```

Copy the same provisioned datasource and dashboard JSON into your Grafana deployment's ConfigMap — Grafana's provisioning system handles it the same way in both docker-compose and Kubernetes.

## What to add next

- **Prompt version tracking:** Add `gen_ai.prompt.template_id` as a span attribute. Query Tempo for traces by prompt version to correlate template changes with latency or token count shifts.
- **Streaming TTFT:** For streaming responses, start a timer at request time and record `llm.time_to_first_token` when the first chunk arrives. The `anthropic` SDK's `stream()` context manager makes this straightforward.
- **Cost estimation:** Compute cost client-side using the token counts you already record (`input_tokens * price_per_mtok / 1e6`). Emit it as a histogram so you can alert on daily cost projections.
- **Exemplars:** Set `PROMETHEUS_EXEMPLARS=true` on the collector and enable exemplar storage in Prometheus. This links Grafana metric panels directly to the exact trace for any data point — no manual correlation.
