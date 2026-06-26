from __future__ import annotations

import base64
import logging
import re
import time
from collections import Counter
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


# ── 重复输出检测 ──────────────────────────────────────────────────────────────

# 匹配 1~6 字符的序列连续重复 20 次以上（覆盖单字符、emoji、短词）
_REPEAT_RE = re.compile(r"(.{1,6})\1{19,}", re.DOTALL)


def _detect_repetition(text: str) -> str | None:
    """检测模型输出是否存在异常重复，返回描述字符串；正常则返回 None。"""
    if not text or len(text) < 10:
        return None

    # 1. 连续重复序列（如 📷📷📷... 或 ▪▪▪...）
    m = _REPEAT_RE.search(text)
    if m:
        seq = m.group(1)
        count = len(re.findall(re.escape(seq), text))
        return f"序列 {repr(seq)} 重复 {count} 次"

    # 2. 相同行大量出现（如同一段话重复5次以上）
    lines = [ln.strip() for ln in text.split("\n") if len(ln.strip()) >= 4]
    if lines:
        top_line, cnt = Counter(lines).most_common(1)[0]
        if cnt >= 5:
            return f"行重复 {cnt} 次: {top_line[:50]!r}"

    return None


def _truncate_at_repetition(text: str) -> str:
    """截断重复部分，保留开头正常内容，附加警告。"""
    m = _REPEAT_RE.search(text)
    if m and m.start() > 0:
        prefix = text[: m.start()].rstrip()
        return prefix + "\n\n> ⚠️ 模型输出异常（重复内容），此部分已截断。"
    # 没有合适截断点，直接截取前 1000 字符
    return text[:1000] + "\n\n> ⚠️ 模型输出异常（重复内容），已截断。"



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

        # ── 重复输出检测 ──────────────────────────────────────────────────
        repeat_reason = _detect_repetition(result)
        if repeat_reason:
            logger.warning(
                "检测到重复输出（%s），使用修正 prompt 重试: %s", repeat_reason, image_path
            )
            corrected_prompt = (
                f"【重要】上次分析该图像时输出出现异常重复（{repeat_reason}），"
                f"请重新仔细分析，不要重复输出相同字符或短语。\n\n"
                f"原始要求：\n{prompt}"
            )
            corrected_message = HumanMessage(
                content=[
                    {"type": "text", "text": corrected_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                            "detail": "high",
                        },
                    },
                ]
            )
            result = _invoke_llm_with_retry(llm, corrected_message)
            logger.info("修正重试完成，响应长度: %d 字符", len(result))

            # 重试后仍有重复 → 截断并附加警告
            retry_reason = _detect_repetition(result)
            if retry_reason:
                logger.warning(
                    "重试后仍有重复输出（%s），截断处理: %s", retry_reason, image_path
                )
                result = _truncate_at_repetition(result)

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
