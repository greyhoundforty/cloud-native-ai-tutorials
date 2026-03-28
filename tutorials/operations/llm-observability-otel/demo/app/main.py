from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel

from telemetry import setup_telemetry
from llm import InstrumentedLLMClient

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
