from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str | None = None
    llm_model: str = "gpt-4o"
    pdf_dpi: int = 150
    temp_dir: str = "./tmp"
    max_retries: int = 2
    page_timeout: int = 60

    # Web server
    host: str = "0.0.0.0"
    port: int = 8000
    tasks_dir: str = "./tasks"
    max_concurrent_tasks: int = 3



settings = Settings()
