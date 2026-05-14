from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False
    )


    # LLM
    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = None

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

settings = Settings()