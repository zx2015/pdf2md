from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── 视觉 Provider（固定为 SiliconFlow）──────────────────────────────────
    # describe_image 固定使用 vision_chat_model，在 .env 中配置，不提供用户在
    # 界面按任务切换的选项。
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    vision_chat_model: str = "Qwen/Qwen3.5-4B"

    # ── 编排 Agent Provider（独立配置，可指向任意 OpenAI 兼容服务）──────────
    # 负责决定调用 read_file_lines / describe_image / write_file_lines 的 ReAct
    # Agent，需具备 Tool Calling 能力；与视觉 Provider 完全独立配置（不同的
    # api_key/base_url/model），可以是 OpenAI 官方、Azure、SiliconFlow 或自建的
    # LiteLLM/Ollama 等兼容网关。
    openai_api_key: str = ""
    openai_base_url: str | None = None  # 为 None 时使用 OpenAI 官方端点
    orchestrator_model: str = "gpt-4o-mini"

    pdf_dpi: int = 150
    temp_dir: str = "./tmp"

    # LLM 调用超时与重试
    page_timeout: int = 120          # 单次 LLM 调用超时（秒）——注意：httpx/openai 的
                                      # timeout 是"空闲超时"（多久没收到新数据算超时），
                                      # 不是"总耗时"超时。若模型陷入重复输出循环但仍在
                                      # 持续吐字符，此超时不会触发，必须靠 vision_max_tokens
                                      # 兜底限制最坏情况下的生成时长。
    vision_max_tokens: int = 4000    # 视觉模型单次输出的最大 token 数（硬性上限）。
                                      # 实测：未设置该值时，模型一旦陷入重复输出循环会
                                      # 持续生成直至自行停止，曾观察到单次调用耗时超过
                                      # 90 分钟、响应体膨胀至数十 MB 的极端情况。
    max_retries: int = 2             # httpx 层连接重试次数
    max_concurrent_llm_calls: int = 3  # LLM 视觉 API 并发数限制（仅 page_analyzer.py 遗留工具使用）
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
