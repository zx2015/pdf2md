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
    llm_seed: int | None = 42        # 视觉/编排 LLM 调用固定的随机数种子，配合已固定的
                                      # temperature=0 尽量提升多次调用结果的一致性。
                                      # 注意：这只是"best effort"优化，不是硬保证——实测
                                      # 发现即使 temperature=0 + 固定 seed，SiliconFlow 等
                                      # 共享云推理服务仍可能因服务端批处理（batching）导致
                                      # 的浮点舍入误差差异而产生不同输出（"batch invariance"
                                      # 问题，非本项目代码可控）。设为 None 可禁用该参数。
    vision_max_tokens: int | None = None  # 视觉模型单次输出的最大 token 数（硬性上限）。
                                      # 显式设置时（.env 的 VISION_MAX_TOKENS）优先级最高，
                                      # 固定生效；留空（None）时按 vision_chat_model 自动
                                      # 探测（见 model_registry.py 的分层兜底策略），取该
                                      # 模型的 max_output_tokens。未设置任何上限的风险：
                                      # 模型一旦陷入重复输出循环会持续生成直至自行停止，
                                      # 曾观察到单次调用耗时超过 90 分钟、响应体膨胀至
                                      # 数十 MB 的极端情况，因此本字段务必有值（自动或手动）。
    max_retries: int = 2             # httpx 层连接重试次数
    max_concurrent_llm_calls: int = 3  # LLM 视觉 API 并发数限制；同时用于
                                        # page_analyzer.py 遗留工具及 image_analyzer.py
                                        # 硬性超时线程池的 max_workers
    retry_attempts: int = 4          # tenacity 业务层重试总次数（含首次）
    retry_wait_min: int = 2          # 首次重试等待秒数
    retry_wait_max: int = 60         # 最大等待秒数
    rate_limit_wait: int = 15        # 遇到 429 时额外等待秒数

    # Web server
    host: str = "0.0.0.0"
    port: int = 8000
    tasks_dir: str = "./tasks"
    max_concurrent_tasks: int = 3

    @property
    def effective_vision_max_tokens(self) -> int:
        """返回实际生效的视觉模型最大输出 token 数。

        .env 显式配置的 vision_max_tokens 优先级最高，固定生效；未配置
        （None）时按 vision_chat_model 自动探测（见 model_registry.py）。
        """
        if self.vision_max_tokens is not None:
            return self.vision_max_tokens
        from pdf2md.model_registry import get_model_limits

        return get_model_limits(self.vision_chat_model).max_output_tokens


settings = Settings()
