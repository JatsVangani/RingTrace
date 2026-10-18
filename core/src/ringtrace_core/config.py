from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    falkordb_url: str = Field(default="redis://localhost:6379", validation_alias="FALKORDB_URL")
    graph_name: str = Field(default="ringtrace", validation_alias="GRAPH_NAME")
    dataset: str = Field(default="HI-Small", validation_alias="DATASET")

    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    agent_model: str = Field(default="claude-sonnet-5-5", validation_alias="AGENT_MODEL")
    agent_max_steps: int = Field(default=12, validation_alias="AGENT_MAX_STEPS")
    candidates_per_run: int = Field(default=50, validation_alias="CANDIDATES_PER_RUN")
    llm_base_url: str = Field(default="http://localhost:8080/v1", validation_alias="LLM_BASE_URL")
    llm_model: str = Field(default="local", validation_alias="LLM_MODEL")
    llm_api_key: str | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_timeout_s: float = Field(default=300, validation_alias="LLM_TIMEOUT_S")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
