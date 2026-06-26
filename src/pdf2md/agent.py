from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from pdf2md.config import settings
from pdf2md.tools.file_tools import read_file_lines, write_file_lines
from pdf2md.tools.image_analyzer import describe_image
from pdf2md.tools.pdf_to_image import pdf_to_images

logger = logging.getLogger(__name__)


class PageProcessingError(Exception):
    """单页处理失败（重试 3 次后仍失败）异常，携带页码信息用于断点续传。"""

    def __init__(self, page_num: int, total_pages: int, cause: Exception):
        self.page_num = page_num
        self.total_pages = total_pages
        self.cause = cause
        super().__init__(
            f"第 {page_num}/{total_pages} 页处理失败（已重试 3 次）: {cause}"
        )


_PAGE_SYSTEM_PROMPT = """\
你是专业的 PDF 页面转 Markdown 助手，每次只处理一张 PDF 页面图像，完成后立即结束。

## 处理步骤

**步骤 1 — 读取上下文**（第 1 页跳过）
调用 `read_file_lines(output_path, start_line=-15)` 读取已有 Markdown 末尾 15 行。

**步骤 2 — 识别当前页**
根据上下文构建 prompt，调用 `describe_image(page_path, prompt)`。
prompt 应包含：
- 文档类型/语言背景、当前页码信息
- 上文末尾（用于衔接，告知模型上页结尾内容）
- 提取要求：
  - 文字/段落：保留标题层级（# ## ###）、加粗、列表
  - 表格：标准 Markdown 表格 `| 列 | 列 |\\n|---|---|`
  - 数学公式：$$...$$ LaTeX 格式
  - 代码截图：代码块并标注语言
  - 普通图片："> 📷 " 开头描述
  - 流程图 → ```mermaid flowchart TD```
  - 时序图 → ```mermaid sequenceDiagram```
  - 数据图表 → 趋势描述 + 关键数据表格
  - 架构图/复杂示意图 → "> 📷 " 描述
- 布局：双栏时先完整读左栏，再读右栏

**步骤 3 — 检查拼接**
- 若上文末尾最后一句未完结 → 当前页内容直接续写，不重复已有内容
- 若拼接不自然，可调用 `describe_image(prev_page_path, ...)` 查看上一页（仅限 prev_page_path 存在时）

**步骤 4 — 自检**（发现问题则重新调用 describe_image，最多 1 次）
- $$ 公式是否闭合
- 表格列数是否一致
- 内容是否明显截断
- Mermaid 代码块是否为空

**步骤 5 — 写入文件**
调用 `write_file_lines(output_path, content, mode='append')` 追加到文件。
衔接规则：
- 跨页段落/列表：直接续写，不插多余空行
- 跨页表格：合并重复表头
- 章节切换：保留一个空行

完成后输出"第 X 页处理完成"，不要再调用任何工具。
"""


def _build_llm() -> ChatOpenAI:
    kwargs: dict = {
        "model": settings.llm_model,
        "temperature": 0,
        "streaming": False,
        "max_retries": settings.max_retries,
    }
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


def _build_page_agent():
    """为单页处理创建全新的 LangGraph React Agent（无共享状态，无上下文溢出风险）。"""
    return create_react_agent(
        _build_llm(),
        tools=[describe_image, read_file_lines, write_file_lines],
        prompt=_PAGE_SYSTEM_PROMPT,
    )


def _format_langgraph_event(event: dict) -> dict | None:
    """将 LangGraph streaming 事件转换为前端日志条目，无关事件返回 None。"""
    now = datetime.now().isoformat()
    kind = event.get("event", "")

    if kind == "on_chat_model_stream":
        chunk = event["data"].get("chunk")
        content = getattr(chunk, "content", "") if chunk else ""
        if content:
            return {"type": "agent_thinking", "content": content, "timestamp": now}

    elif kind == "on_tool_start":
        return {
            "type": "tool_start",
            "tool": event.get("name", ""),
            "input": event["data"].get("input", {}),
            "timestamp": now,
        }

    elif kind == "on_tool_end":
        output_str = str(event["data"].get("output", ""))
        if len(output_str) > 800:
            output_str = output_str[:800] + " …[截断]"
        return {
            "type": "tool_end",
            "tool": event.get("name", ""),
            "output": output_str,
            "timestamp": now,
        }

    return None


def _now() -> str:
    return datetime.now().isoformat()


async def _process_one_page(
    page_path: str,
    prev_page_path: str | None,
    output_path: str,
    page_num: int,
    total_pages: int,
) -> AsyncGenerator[dict, None]:
    """创建全新 Agent 处理单页，yield 日志事件流，失败时 raise。"""
    agent = _build_page_agent()

    prev_info = (
        f"上一页图片路径：`{prev_page_path}`\n（可选：需要检查跨页拼接时，可调用 describe_image 分析此图）"
        if prev_page_path
        else "（这是第一页，无上一页）"
    )
    user_message = (
        f"请处理第 {page_num} 页（共 {total_pages} 页）。\n\n"
        f"以下是本次任务的路径参数，请原样复制到工具调用中，不要修改：\n"
        f"- 当前页图片路径：`{page_path}`\n"
        f"- {prev_info}\n"
        f"- Markdown 输出文件路径：`{output_path}`\n"
    )

    async for event in agent.astream_events(
        {"messages": [("user", user_message)]},
        config={"recursion_limit": 20},
        version="v2",
    ):
        log_entry = _format_langgraph_event(event)
        if log_entry:
            yield log_entry


async def astream_conversion(
    pdf_path: str,
    output_path: str,
    images_dir: str,
    task_id: str | None = None,
    start_page: int = 1,
) -> AsyncGenerator[dict, None]:
    """按页串行处理，每页独立 Agent，失败重试 3 次，超限抛出 PageProcessingError。

    Args:
        pdf_path: 输入 PDF 路径。
        output_path: 输出 Markdown 路径。
        images_dir: JPEG 图像目录。
        task_id: 任务 ID（日志关联，可选）。
        start_page: 从第几页开始（1-indexed），默认 1，断点续传时传入断点页码。
    """
    pdf_path = str(Path(pdf_path).resolve())
    images_dir_path = Path(images_dir)

    # ── Step 1: PDF → images（断点续传时跳过，图像已存在）────────────────
    existing = sorted(images_dir_path.glob("page_*.jpg"))
    if existing:
        image_paths = [str(p) for p in existing]
        logger.info("已有 %d 张图像，跳过 PDF 转换（start_page=%d）", len(image_paths), start_page)
        yield {
            "type": "agent_thinking",
            "content": f"检测到已有 {len(image_paths)} 张图像，从第 {start_page} 页继续处理",
            "timestamp": _now(),
        }
    else:
        yield {
            "type": "tool_start",
            "tool": "pdf_to_images",
            "input": {"pdf_path": pdf_path, "output_dir": images_dir, "dpi": settings.pdf_dpi},
            "timestamp": _now(),
        }
        result_json = pdf_to_images.invoke(
            {"pdf_path": pdf_path, "output_dir": images_dir, "dpi": settings.pdf_dpi}
        )
        image_paths = json.loads(result_json)
        yield {
            "type": "tool_end",
            "tool": "pdf_to_images",
            "output": f"已生成 {len(image_paths)} 张图像",
            "timestamp": _now(),
        }

    total_pages = len(image_paths)

    # ── Step 2: 逐页串行处理 ──────────────────────────────────────────────
    for idx, page_path in enumerate(image_paths):
        page_num = idx + 1
        if page_num < start_page:
            continue  # 断点续传：跳过已完成页面

        prev_page_path = image_paths[idx - 1] if idx > 0 else None

        yield {
            "type": "page_start",
            "page": page_num,
            "total": total_pages,
            "message": f"开始处理第 {page_num}/{total_pages} 页",
            "timestamp": _now(),
        }

        last_error: Exception | None = None
        for attempt in range(1, 4):  # 最多尝试 3 次
            try:
                async for event in _process_one_page(
                    page_path, prev_page_path, output_path, page_num, total_pages
                ):
                    yield event
                last_error = None
                break  # 成功，退出重试循环
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "第 %d 页第 %d 次处理失败: %s: %s",
                    page_num, attempt, type(exc).__name__, exc,
                )
                if attempt < 3:
                    wait = 2 ** attempt  # 2s → 4s
                    yield {
                        "type": "page_retry",
                        "page": page_num,
                        "attempt": attempt,
                        "error": str(exc),
                        "message": f"第 {page_num} 页第 {attempt} 次失败，{wait}s 后重试: {exc}",
                        "timestamp": _now(),
                    }
                    await asyncio.sleep(wait)

        if last_error is not None:
            raise PageProcessingError(page_num, total_pages, last_error)

        yield {
            "type": "page_complete",
            "page": page_num,
            "total": total_pages,
            "message": f"第 {page_num}/{total_pages} 页处理完成",
            "timestamp": _now(),
        }

    logger.info("所有 %d 页处理完成，task_id=%s", total_pages, task_id)


def convert_pdf_to_markdown(pdf_path: str, output_path: str, temp_dir: str | None = None) -> str:
    """将 PDF 文件转换为 Markdown（命令行同步版）。"""
    pdf_path = str(Path(pdf_path).resolve())
    images_dir = temp_dir or settings.temp_dir

    async def _run():
        async for _ in astream_conversion(pdf_path, output_path, images_dir):
            pass

    asyncio.run(_run())
    return f"转换完成：{output_path}"
