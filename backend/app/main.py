"""FastAPI application entry point.

Routes are defined inline here while the surface area is small.
"""

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import settings
from app.llm import LLMProviderError, get_fast_model, get_synthesis_model

app = FastAPI(title="Equity Research Copilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health / debugging endpoints ────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/ping")
async def ping(name: str = "world") -> dict:
    """Trivial echo for connectivity smoke tests."""
    return {"message": f"pong, {name}"}


@app.get("/info")
async def info() -> dict:
    """Non-sensitive snapshot of the current configuration."""
    return {
        "llm_provider": settings.llm_provider,
        "aws_region": settings.aws_region,
        "has_aws_creds": bool(settings.aws_access_key_id and settings.aws_secret_access_key),
        "has_langsmith_key": bool(settings.langsmith_api_key),
        "langsmith_tracing": settings.langchain_tracing_v2,
        "langsmith_project": settings.langsmith_project,
        "cors_origins": settings.cors_origins,
    }


# ─── LLM endpoint ────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    """Single-shot LLM call request body."""

    prompt: str = Field(..., min_length=1, description="The user prompt.")
    model: Literal["synthesis", "fast"] = Field(
        default="fast",
        description="Which model tier to use. 'fast' = Haiku, 'synthesis' = Sonnet.",
    )


class GenerateResponse(BaseModel):
    """Single-shot LLM call response body."""

    content: str
    model_used: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    """Single-shot LLM call. Hello-world style — confirms Bedrock + LangSmith wiring."""
    try:
        model = get_synthesis_model() if req.model == "synthesis" else get_fast_model()
        result = await model.ainvoke(req.prompt)
    except LLMProviderError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface the underlying error verbatim
        raise HTTPException(
            status_code=502, detail=f"LLM call failed: {type(exc).__name__}: {exc}"
        ) from exc

    # `result.content` is usually a string; for some Bedrock responses it's a list
    # of content blocks. Normalise to string for the response.
    if isinstance(result.content, str):
        content = result.content
    else:
        # Each block has a `text` field for text blocks; concat all text blocks.
        content = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in result.content
        )

    usage = getattr(result, "usage_metadata", None) or {}
    return GenerateResponse(
        content=content,
        model_used=req.model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )
