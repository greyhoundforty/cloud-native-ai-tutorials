import time
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
import anthropic

# Semantic conventions aligned with OpenTelemetry GenAI spec
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

        # Histograms give p50/p95/p99 in Grafana out of the box
        self.latency_histogram = meter.create_histogram(
            name="llm.request.duration",
            description="Total LLM request duration in seconds",
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

        span_attrs = {
            LLM_SYSTEM: "anthropic",
            LLM_REQUEST_MODEL: model,
            LLM_OPERATION_NAME: "chat",
        }

        with self.tracer.start_as_current_span("llm.chat", attributes=span_attrs) as span:
            start = time.perf_counter()
            try:
                response = self.client.messages.create(**kwargs)
                duration = time.perf_counter() - start

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                response_model = response.model

                span.set_attribute(LLM_RESPONSE_MODEL, response_model)
                span.set_attribute(LLM_USAGE_INPUT_TOKENS, input_tokens)
                span.set_attribute(LLM_USAGE_OUTPUT_TOKENS, output_tokens)
                span.set_status(Status(StatusCode.OK))

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
