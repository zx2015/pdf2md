from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _render_page(page_data: dict) -> str:
    """将单页的 content_blocks 渲染为 Markdown 字符串。"""
    page_number = page_data.get("page_number", "?")
    error = page_data.get("error")

    if error:
        return f"> ⚠️ 第 {page_number} 页解析失败：{error}\n"

    blocks = page_data.get("content_blocks", [])
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "text")
        content = block.get("content", "").strip()
        if not content:
            continue
        if block_type in ("text", "table", "image_description"):
            parts.append(content)
        else:
            parts.append(content)

    return "\n\n".join(parts)


@tool
def assemble_markdown(pages_json: str, output_path: str) -> str:
    """将各页分析结果组装为最终 Markdown 文件。

    Args:
        pages_json: JSON 字符串，内容为 PageAnalysis 对象列表。
                    例如: '[{"page_number":1,"content_blocks":[...]}, ...]'
        output_path: 输出 Markdown 文件路径。

    Returns:
        输出文件的绝对路径字符串。
    """
    pages: list[dict] = json.loads(pages_json)
    pages.sort(key=lambda p: p.get("page_number", 0))

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    page_sections = [_render_page(page) for page in pages]
    markdown_content = "\n\n---\n\n".join(section for section in page_sections if section.strip())

    output_file.write_text(markdown_content, encoding="utf-8")
    logger.info("Markdown 文件已写入: %s", output_file.resolve())
    return str(output_file.resolve())
