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
from pdf2md.llm import build_vision_llm

logger = logging.getLogger(__name__)


# ── 重复输出检测 ──────────────────────────────────────────────────────────────

# 规则1：1~6 字符的序列连续重复 50 次以上（覆盖单字符、emoji、短词）
_REPEAT_RE = re.compile(r"(.{1,6})\1{49,}", re.DOTALL)

# 规则2：7~60 字符的中长序列连续重复 50 次以上（覆盖 LaTeX 片段如 \times 3 \times 0）
_LONG_REPEAT_RE = re.compile(r"(.{7,60})\1{49,}", re.DOTALL)

# 合法的排版分隔符字符集：目录引导点、横线、箱形线等
# 这些字符大量重复是正常排版（如 "第一章 .............. 17"），不是 LLM 暴走
# 注意：▪ • 等列表标记不在此列，25 个连续列表标记属于异常
_SEPARATOR_ONLY_RE = re.compile(r"^[\s.\-_=~*·─━═┄┈…]+$")

# Markdown 表格分隔行：如 |---|---|、| :--- | ---: |、|----|----|
_TABLE_SEP_LINE_RE = re.compile(r"^[\s|:\-]+$")


def _is_separator_pattern(seq: str) -> bool:
    """判断重复序列是否为合法排版分隔符（如目录引导点 ....）。"""
    return bool(_SEPARATOR_ONLY_RE.match(seq))


def _is_table_separator_line(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行，如 |---|---| 或 | :--- | ---: |。"""
    return bool(_TABLE_SEP_LINE_RE.match(line)) and "|" in line and "-" in line


def _find_token_repetition(text: str) -> tuple[str, int, int] | None:
    """检测 token 级别的重复序列（按空白分词）。

    返回 (重复序列字符串, 重复次数, 在原文中的起始字节偏移)，未检测到则返回 None。
    覆盖如 ``\\times 3 \\times 0 \\times 3 \\times 0`` 这类交替模式。
    """
    tokens = text.split()
    n = len(tokens)
    if n < 16:
        return None

    for window in range(2, 9):  # 尝试 2~8 个 token 组成的窗口
        min_tokens_needed = window * 8
        if n < min_tokens_needed:
            continue
        # 只在前 200 个 token 范围内寻找起点（性能保护）
        for start in range(min(n - min_tokens_needed + 1, 200)):
            seq = tuple(tokens[start : start + window])
            count = 0
            pos = start
            while pos + window <= n and tuple(tokens[pos : pos + window]) == seq:
                count += 1
                pos += window
            if count >= 50:
                seq_str = " ".join(seq)
                # 找到该序列在原文中的近似起始位置
                char_offset = text.find(seq_str)
                return seq_str, count, max(char_offset, 0)

    return None


def _detect_repetition(text: str) -> str | None:
    """检测模型输出是否存在异常重复，返回描述字符串；正常则返回 None。

    覆盖四类模式：
    1. 短字符重复（1~6 字符，如 ``📷📷📷``）
    2. 中长字符重复（7~60 字符，如 ``\\times 3 \\times 0``）
    3. 同一行重复出现 50 次以上（跳过 Markdown 表格分隔行）
    4. Token 序列重复（2~8 词组，如 ``\\times 3 \\times 0 \\times 3 \\times 0``）
    """
    if not text or len(text) < 10:
        return None

    # 1. 短序列重复（1~6 字符）：跳过合法排版分隔符
    m = _REPEAT_RE.search(text)
    if m:
        seq = m.group(1)
        if not _is_separator_pattern(seq):
            count = len(re.findall(re.escape(seq), text))
            return f"序列 {repr(seq)} 重复 {count} 次"

    # 2. 中长序列重复（7~60 字符）：同样跳过合法排版分隔符
    m = _LONG_REPEAT_RE.search(text)
    if m:
        seq = m.group(1)
        if not _is_separator_pattern(seq):
            count = text.count(seq)
            return f"序列 {repr(seq[:40])} 重复 {count} 次"

    # 3. 相同行大量出现（同一段话重复 30 次以上，且不是 Markdown 表格分隔行）
    lines = [ln.strip() for ln in text.split("\n") if len(ln.strip()) >= 4]
    if lines:
        top_line, cnt = Counter(lines).most_common(1)[0]
        if cnt >= 50 and not _is_table_separator_line(top_line):
            return f"行重复 {cnt} 次: {top_line[:50]!r}"

    # 4. Token 级别的多词序列重复（如 \times 3 \times 0 \times 3 ...）
    token_result = _find_token_repetition(text)
    if token_result:
        seq_str, count, _ = token_result
        return f"token序列 {repr(seq_str[:50])} 连续重复 {count} 次"

    return None


def _truncate_at_repetition(text: str) -> str:
    """截断重复部分，保留开头正常内容，附加警告。"""
    # 尝试各规则找截断点，取最早出现的位置
    cut_pos: int | None = None

    m = _REPEAT_RE.search(text)
    if m:
        cut_pos = m.start()

    m2 = _LONG_REPEAT_RE.search(text)
    if m2 and (cut_pos is None or m2.start() < cut_pos):
        cut_pos = m2.start()

    token_result = _find_token_repetition(text)
    if token_result:
        _, _, offset = token_result
        if cut_pos is None or offset < cut_pos:
            cut_pos = offset

    if cut_pos is not None and cut_pos > 0:
        prefix = text[:cut_pos].rstrip()
        return prefix + "\n\n> ⚠️ 模型输出异常（重复内容），此部分已截断。"

    # 没有合适截断点，直接截取前 1000 字符
    return text[:1000] + "\n\n> ⚠️ 模型输出异常（重复内容），已截断。"



def _is_rate_limit(exc: BaseException) -> bool:
    """判断是否为 rate limit 错误（HTTP 429）。

    优先读取异常自带的 HTTP 状态码（如 openai.RateLimitError.status_code、
    httpx.HTTPStatusError.response.status_code）；状态码不可用时（如网络库包装
    后的通用异常）退化为字符串匹配，保持对多种异常类型的兼容覆盖。
    """
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return status_code == 429

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


def _build_llm(model: str):
    """构建全新的 ChatOpenAI 实例（未缓存）。仅供 `_get_llm()` 内部调用及测试 mock 使用。

    使用视觉 Provider（固定为 SiliconFlow），与编排 Agent Provider 相互独立。
    """
    return build_vision_llm(model=model, timeout=settings.page_timeout)


# 按模型名缓存 LLM 实例：不同任务可能并发选用不同模型，因此不能用单一全局实例。
_llm_cache: dict[str, object] = {}


def _get_llm(model: str):
    """返回指定模型对应的缓存 ChatOpenAI 实例，避免每次调用都新建底层 httpx 客户端。

    测试中可调用 `_reset_llm_cache()` 清空缓存，以配合对 `_build_llm` 的 mock 生效。
    """
    if model not in _llm_cache:
        _llm_cache[model] = _build_llm(model=model)
    return _llm_cache[model]


def _reset_llm_cache() -> None:
    """清空缓存的 LLM 实例（供测试隔离 mock 使用）。"""
    _llm_cache.clear()


def _build_message(image_b64: str, text: str) -> HumanMessage:
    # 注意：image_url 放在 text 之前。实测发现部分模型在 text-before-image 顺序下
    # 更容易输出无意义的重复内容，换成 image-before-text 后更稳定，故统一采用。
    return HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"},
            },
            {"type": "text", "text": text},
        ]
    )


def _call_vision_model(model: str, image_path: str, prompt: str) -> str:
    """调用指定视觉模型分析图像，处理重复输出检测与重试，返回文本结果。

    检测到重复输出时，用自然语言批评性 prompt 重新提示模型"上次输出异常
    重复，请重新分析"；仍重复则截断并附加警告。
    """
    llm = _get_llm(model)
    image_b64 = _encode_image_to_base64(image_path)

    result = _invoke_llm_with_retry(llm, _build_message(image_b64, prompt))
    logger.info("视觉模型调用完成，响应长度: %d 字符", len(result))

    repeat_reason = _detect_repetition(result)
    if not repeat_reason:
        return result

    logger.warning("检测到重复输出（%s），使用修正 prompt 重试: %s", repeat_reason, image_path)
    corrected_prompt = (
        f"【重要】上次分析该图像时输出出现异常重复（{repeat_reason}），"
        f"请重新仔细分析，不要重复输出相同字符或短语。\n\n"
        f"原始要求：\n{prompt}"
    )
    result = _invoke_llm_with_retry(llm, _build_message(image_b64, corrected_prompt))
    logger.info("重试完成，响应长度: %d 字符", len(result))

    retry_reason = _detect_repetition(result)
    if retry_reason:
        logger.warning("重试后仍有重复输出（%s），截断处理: %s", retry_reason, image_path)
        result = _truncate_at_repetition(result)

    return result


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

    model = settings.vision_chat_model
    logger.info("描述图像: %s（模型: %s）", image_path, model)

    try:
        return _call_vision_model(model, image_path, prompt)

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
