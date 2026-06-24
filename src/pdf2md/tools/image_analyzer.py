from __future__ import annotations

import base64
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from pdf2md.config import settings

logger = logging.getLogger(__name__)


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

    try:
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
        response = llm.invoke([message])
        result = response.content
        logger.info("图像描述完成，响应长度: %d 字符", len(result))
        return result

    except Exception as exc:
        error_msg = f"⚠️ 错误：LLM 调用失败: {exc}"
        logger.error("图像描述失败 %s: %s", image_path, exc)
        return error_msg
