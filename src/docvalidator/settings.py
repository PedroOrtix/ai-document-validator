"""Runtime configuration for optional service integrations."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """Configuration for the optional OpenRouter-compatible LLM extractor."""

    model_config = SettingsConfigDict(extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    validator_llm_model: str = "z-ai/glm-5.3-flash"
    validator_llm_reasoning_effort: str = "low"
    validator_llm_timeout_seconds: float = 30.0
    validator_vlm_model: str = "z-ai/glm-5.3-flash"
    validator_vlm_reasoning_effort: str = "low"
    validator_vlm_timeout_seconds: float = 60.0
