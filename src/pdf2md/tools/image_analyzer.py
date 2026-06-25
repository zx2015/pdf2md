from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from pdf2md.config import settings

logger = logging.getLogger(__name__)


# ── 可重试的异常判断 ───────────────────────────────────────────────────────

def _is_rate_limit(exc: BaseException) -> bool:
    """判断是否为 rate limit 错误（HTTP 429）。"""
    msg = str(exc).lower()
    return (
        "rate limit" in msg
        or "429" in msg
        or "too many requests" in msg
        or "ratelimit" in msg
    )


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, (
        httpx.RemoteProtocolError,   # incomplete chunked read 等
        httpx.TimeoutException,      # 请求超时
        httpx.ConnectError,          # 连接失败
        httpx.ReadError,             # 读取中断
        httpx.NetworkError,          # 其他网络错误
    )):
        return True
    return _is_rate_limit(exc)


def _before_sleep(retry_state: RetryCallState) -> None:
    """重试前记录日志，rate limit 时额外等待。"""
    exc = retry_state.outcome.exception()
    attempt = retry_state.attempt_number
    wait_secs = getattr(retry_state.next_action, "sleep", 0)

    if _is_rate_limit(exc):
        extra = settings.rate_limit_wait
        logger.warning(
            "LLM 调用触发 Rate Limit，第 %d 次重试，等待 %.0f + %d 秒: %s",
            attempt, wait_secs, extra, exc,
        )
        time.sleep(extra)
    elif isinstance(exc, httpx.TimeoutException):
        logger.warning("LLM 调用超时，第 %d 次重试，等待 %.0f 秒", attempt, wait_secs)
    elif isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.NetworkError)):
        logger.warning(
            "LLM 连接中断（%s），第 %d 次重试，等待 %.0f 秒",
            type(exc).__name__, attempt, wait_secs,
        )
    else:
        logger.warning("LLM 调用失败，第 %d 次重试: %s", attempt, exc)


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(
        multiplier=2,
        min=settings.retry_wait_min,
        max=settings.retry_wait_max,
    ),
    stop=stop_after_attempt(settings.retry_attempts),
    before_sleep=_before_sleep,
    reraise=True,
)
def _invoke_llm_with_retry(llm, message: HumanMessage) -> str:
    """带重试的 LLM 调用，失败时抛出异常由调用方处理。"""
    response = llm.invoke([message])
    return response.content


# ── 工具构建 ──────────────────────────────────────────────────────────────

def _encode_image_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_llm():
    from langchain_openai import ChatOpenAI

    kwargs: dict = {
        "model": settings.llm_model,
        "temperature": 0,
        "timeout": settings.page_timeout,
        "max_retries": settings.max_retries,
    }
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


@tool
def describe_image(image_path: str, prompt: str) -> str:
    """用指定的 prompt 调用 LLM 视觉能力分析图像，返回原始分析结果文本。

    Agent 应根据当前文档上下文自行构建 prompt，例如：
    - 指定提取方式（提取文字为 Markdown、表格转 Markdown 表格、图表转 Mermaid 等）
    - 指定布局处理方式（双栏时先读左栏再读右栏）
    - 附加已知的文档背景信息（文档类型、语言、所处章节等）

    Args:
        image_path: JPEG 图像文件路径。
        prompt: 分析指令，由 Agent 根据当前上下文自行构建。

    Returns:
        LLM 对图像的分析结果文本。若图像不存在或 LLM 调用失败，
        返回以 "⚠️ 错误：" 开头的错误描述。
    """
    if not Path(image_path).exists():
        error_msg = f"⚠️ 错误：图像文件不存在: {image_path}"
        logger.error(error_msg)
        return error_msg

    logger.info("描述图像: %s", image_path)
    llm = _build_llm()
    image_b64 = _encode_image_to_base64(image_path)
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"},
            },
        ]
    )

    try:
        result = _invoke_llm_with_retry(llm, message)
        logger.info("图像描述完成，响应长度: %d 字符", len(result))
        return result

    except httpx.TimeoutException as exc:
        msg = f"⚠️ 错误：LLM 调用超时（超过 {settings.page_timeout}s），已重试 {settings.retry_attempts} 次: {exc}"
        logger.error("图像描述超时 %s: %s", image_path, exc)
        return msg

    except Exception as exc:
        if _is_rate_limit(exc):
            msg = f"⚠️ 错误：触发 Rate Limit，已重试 {settings.retry_attempts} 次仍失败: {exc}"
        elif isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.NetworkError)):
            msg = f"⚠️ 错误：网络连接中断（{type(exc).__name__}），已重试 {settings.retry_attempts} 次: {exc}"
        else:
            msg = f"⚠️ 错误：LLM 调用失败: {exc}"
        logger.error("图像描述失败 %s: %s", image_path, exc)
        return msg
