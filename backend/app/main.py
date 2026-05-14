from fastapi import FastAPI

from app.config import settings

app = FastAPI()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ping")
async def ping(name: str = "world") -> dict:
    return {"message": f"pong, {name}"}


@app.get("/info")
async def info() -> dict:
    return {
        "llm_provider": settings.llm_provider,
        "has_anthropic_key": bool(settings.anthropic_api_key),
        "cors_origins": settings.cors_origins,
    }
