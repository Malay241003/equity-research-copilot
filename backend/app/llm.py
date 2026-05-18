"""LLM provider abstraction.

Two factory functions expose the two model tiers from ARCHITECTURE.md:

- get_synthesis_model() — high-quality model for final synthesis (Sonnet)
- get_fast_model() — cheap/fast model for extraction & simple Q&A (Haiku)

Both dispatch on settings.llm_provider. Today only bedrock is wired up;
adding ollama or anthropic later is a matter of one more elif per
factory function.
"""

from langchain_aws import ChatBedrockConverse
from langchain_core.language_models import BaseChatModel

from app.config import settings


class LLMProviderError(Exception):
    """Raised when LLM_PROVIDER is unset or unsupported."""


def _bedrock_chat(model_id: str, *, temperature: float, max_tokens: int) -> ChatBedrockConverse:
    """Construct a ChatBedrockConverse for the given Bedrock model ID.

    Credentials are passed explicitly when set in `.env`, otherwise boto3 falls
    back to its default credential chain (env vars, ~/.aws/credentials, IAM
    role, etc.).
    """
    kwargs: dict = {
        "model": model_id,
        "region_name": settings.aws_region,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    return ChatBedrockConverse(**kwargs)


def get_synthesis_model() -> BaseChatModel:
    """Return the high-quality model used for the final synthesis step."""
    if settings.llm_provider == "bedrock":
        return _bedrock_chat(
            settings.bedrock_model_id_synthesis,
            temperature=0.3,
            max_tokens=2048,
        )
    raise LLMProviderError(
        f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}. "
        "Set LLM_PROVIDER=bedrock in backend/.env."
    )


def get_fast_model() -> BaseChatModel:
    """Return the cheap/fast model used for extraction & retrieval reranking."""
    if settings.llm_provider == "bedrock":
        return _bedrock_chat(
            settings.bedrock_model_id_fast,
            temperature=0.0,
            max_tokens=512,
        )
    raise LLMProviderError(
        f"Unsupported LLM_PROVIDER: {settings.llm_provider!r}. "
        "Set LLM_PROVIDER=bedrock in backend/.env."
    )
