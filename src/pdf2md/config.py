from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str | None = None
    llm_model: str = "gpt-4o"
    pdf_dpi: int = 150
    temp_dir: str = "./tmp"

    # LLM 调用超时与重试
    page_timeout: int = 120          # 单次 LLM 调用超时（秒）
    max_retries: int = 2             # httpx 层连接重试次数
    retry_attempts: int = 4          # tenacity 业务层重试总次数（含首次）
    retry_wait_min: int = 2          # 首次重试等待秒数
    retry_wait_max: int = 60         # 最大等待秒数
    rate_limit_wait: int = 15        # 遇到 429 时额外等待秒数

    # Web server
    host: str = "0.0.0.0"
    port: int = 8000
    tasks_dir: str = "./tasks"
    max_concurrent_tasks: int = 3


settings = Settings()
