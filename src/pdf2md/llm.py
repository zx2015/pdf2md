"""
llm.py — 公共 ChatOpenAI 构建逻辑，供 agent.py 与 tools/image_analyzer.py 共用。

两个独立 Provider：
- 视觉 Provider（build_vision_llm）：固定为 SiliconFlow，模型通过 settings.vision_chat_model 固定配置，
  仅用于 describe_image 的实际图像识别调用。
- 编排 Agent Provider（build_orchestrator_llm）：独立配置，可指向任意 OpenAI 兼容服务，
  负责驱动 ReAct Agent 的 Tool Calling（决定调用哪个工具、以何种顺序）。

两者互不共享 api_key / base_url / model，避免视觉模型选择意外影响编排 Agent 的稳定性。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from pdf2md.config import settings

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


def build_chat_llm(
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    streaming: bool | None = None,
) -> "ChatOpenAI":
    """构建 ChatOpenAI 实例的底层公共逻辑，统一注入 max_retries 等参数。

    Args:
        model: 模型名称。
        api_key: 该 Provider 的 API Key，为空时不传（由 SDK 走默认环境变量解析）。
        base_url: 该 Provider 的 API 端点，为 None 时使用 ChatOpenAI 默认值（OpenAI 官方）。
        timeout: 单次调用超时时间（秒），为 None 时使用 ChatOpenAI 默认值。注意这是
                 httpx 的"空闲超时"（多久没收到新数据算超时），不是总耗时上限——
                 模型持续吐字符（哪怕是重复的垃圾内容）时不会触发此超时，需配合
                 max_tokens 兜底。
        max_tokens: 单次输出的最大 token 数，为 None 时使用 ChatOpenAI 默认值（通常
                    等于模型上限）。强烈建议为视觉调用设置，避免模型陷入重复
                    输出循环时无限生成、长时间占用连接。
        streaming: 是否启用流式输出，为 None 时使用 ChatOpenAI 默认值。
    """
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "model": model,
        "temperature": 0,
        "max_retries": settings.max_retries,
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if streaming is not None:
        kwargs["streaming"] = streaming
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def build_vision_llm(model: str | None = None, timeout: float | None = None) -> "ChatOpenAI":
    """构建视觉 Provider（固定为 SiliconFlow）的 ChatOpenAI 实例。

    Args:
        model: 模型名称（如 "Qwen/Qwen3.5-4B"），为 None 时使用
               settings.vision_chat_model。
        timeout: 单次调用超时时间（秒）。
    """
    return build_chat_llm(
        model=model or settings.vision_chat_model,
        api_key=settings.siliconflow_api_key,
        base_url=settings.siliconflow_base_url,
        timeout=timeout,
        max_tokens=settings.effective_vision_max_tokens,
    )


def build_orchestrator_llm(streaming: bool | None = None) -> "ChatOpenAI":
    """构建编排 Agent Provider 的 ChatOpenAI 实例（可指向任意 OpenAI 兼容服务）。

    使用 settings.orchestrator_model + settings.openai_api_key/openai_base_url，
    与视觉 Provider（SiliconFlow）完全独立配置。
    """
    return build_chat_llm(
        model=settings.orchestrator_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        streaming=streaming,
    )
