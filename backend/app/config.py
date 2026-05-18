"""Application configuration.

Single source of truth for env vars. Loaded from .env at import time via
pydantic-settings. After load, we also bridge LangSmith vars into
os.environ so that LangChain libraries (which inspect env directly) pick
them up automatically.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ─── LLM provider ────────────────────────────────────────────────
    llm_provider: str = "bedrock"
    anthropic_api_key: str | None = None

    # ─── AWS Bedrock ─────────────────────────────────────────────────
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "us-east-1"
    bedrock_model_id_synthesis: str = "anthropic.claude-sonnet-4-6"
    bedrock_model_id_fast: str = "anthropic.claude-haiku-4-5-20251001-v1:0"

    # ─── LangSmith tracing ───────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "equity-research-copilot"

    # ─── CORS ────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()


# ─── Bridge LangSmith config into os.environ ─────────────────────────
# LangChain libraries (langchain-core, langchain-aws, etc.) read these
# variables from `os.environ` directly at module-import time. pydantic-settings
# loads `.env` into the Settings object but does NOT push values back into
# `os.environ`. So we copy them across here, once, at startup.
if settings.langchain_tracing_v2:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

if settings.langsmith_api_key:
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key

if settings.langsmith_project:
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
