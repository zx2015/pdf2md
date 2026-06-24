from __future__ import annotations

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

_SYSTEM_PROMPT = """\
你是专业的 PDF 转 Markdown 助手，按以下步骤将 PDF 转换为 Markdown 文件。

## 工作流程

**第一步**：调用 `pdf_to_images` 将 PDF 所有页面转换为 JPEG 图像列表。

**第二步**：逐页处理——对每张图像依次执行：
1. 根据文档类型和当前上下文构建合适的 prompt
2. 调用 `describe_image(image_path, prompt)` 获取该页的 Markdown 内容
3. 若不是第 1 页，调用 `read_file_lines(output_path, start_line=-15)` 读取已写内容末尾 15 行，了解衔接上下文
4. 根据上下文决定如何衔接，调用 `write_file_lines(output_path, content, mode="append")` 追加到输出文件
5. **逐页处理，不要在内存中积累所有页面内容**

## 如何构建 prompt

传给 `describe_image` 的 prompt 应包含以下要素：

### 基础提取要求
```
分析这张文档图像，提取所有内容为 Markdown 格式：
- 文字/段落：保留标题层级（# ## ###），保留加粗、列表等格式
- 表格：输出为标准 Markdown 表格（| 列 | 列 |\\n|---|---|）
- 数学公式：使用 $$...$$ LaTeX 格式
- 代码截图：使用代码块并标注编程语言
- 普通图片：以 "> 📷 " 开头描述图片内容
```

### 图表类型处理（遇到图形时选择对应格式）
- 流程图 → ```mermaid\\nflowchart TD\\n...```
- 时序图 → ```mermaid\\nsequenceDiagram\\n...```
- 甘特图 → Markdown 表格（列：任务 | 负责人 | 开始时间 | 结束时间 | 进度）
- 思维导图 → 嵌套 Markdown 列表（用缩进表示层级）
- 数据图表（柱/折/饼图）→ 趋势描述 + 关键数据点表格
- 架构图/复杂示意图 → "> 📷 " 开头，描述各组件及其关系

### 布局处理
- 检测是否为双栏排版：若是，**先完整读完左栏（从上到下），再完整读右栏（从上到下）**，严禁横向交替读取
- 输出时合并为单栏连续文本

### 附加上下文（可选，适用于第 2 页起）
处理后续页面时，可在 prompt 中附加已知背景，例如：
"已知这是一份中文技术手册，当前处理第 3 页，请延续上文风格。"

## 组装规则

读取末尾 15 行后，根据上下文决定衔接方式：
- **跨页段落**：若上页末尾是未完结的句子，直接续写，不插入多余空行
- **跨页表格**：识别并合并重复表头，将表格连为一体
- **跨页列表**：直接续写列表项，不重复列表标记
- **正常章节切换**：保留一个空行或 `---` 分隔
- **失败页面**：若 `describe_image` 返回 "⚠️ 错误：" 开头，写入 `> ⚠️ 第 N 页解析失败：{错误信息}\\n`
"""


def _build_llm() -> ChatOpenAI:
    kwargs: dict = {
        "model": settings.llm_model,
        "temperature": 0,
        "max_retries": settings.max_retries,
    }
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return ChatOpenAI(**kwargs)


def build_agent():
    """构建并返回 PDF 转 Markdown 的 LangGraph React Agent。"""
    llm = _build_llm()
    tools = [
        pdf_to_images,
        describe_image,
        read_file_lines,
        write_file_lines,
    ]
    return create_react_agent(llm, tools=tools, prompt=_SYSTEM_PROMPT)


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
        tool_input = event["data"].get("input", {})
        return {
            "type": "tool_start",
            "tool": event.get("name", ""),
            "input": tool_input,
            "timestamp": now,
        }

    elif kind == "on_tool_end":
        output = event["data"].get("output", "")
        output_str = str(output)
        if len(output_str) > 800:
            output_str = output_str[:800] + " …[截断]"
        return {
            "type": "tool_end",
            "tool": event.get("name", ""),
            "output": output_str,
            "timestamp": now,
        }

    return None


async def astream_conversion(
    pdf_path: str,
    output_path: str,
    images_dir: str,
    task_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """异步流式运行 Agent，将每个有意义的事件作为日志条目 yield 出来。

    Args:
        pdf_path: 输入 PDF 文件路径。
        output_path: 输出 Markdown 文件路径。
        images_dir: JPEG 图像临时目录。
        task_id: 任务 ID（用于 SSE 日志关联，可选）。
    """
    pdf_path = str(Path(pdf_path).resolve())
    agent = build_agent()

    user_message = (
        f"请将以下 PDF 文件转换为 Markdown：\n"
        f"- PDF 路径：{pdf_path}\n"
        f"- 临时图像目录：{images_dir}\n"
        f"- 输出 Markdown 路径：{output_path}\n"
        f"- PDF 渲染 DPI：{settings.pdf_dpi}"
    )

    logger.info("Agent 开始流式处理，PDF: %s", pdf_path)
    async for event in agent.astream_events(
        {"messages": [("user", user_message)]},
        config={"recursion_limit": 500},
        version="v2",
    ):
        log_entry = _format_langgraph_event(event)
        if log_entry:
            yield log_entry

    logger.info("Agent 流式处理完成，PDF: %s", pdf_path)


def convert_pdf_to_markdown(pdf_path: str, output_path: str, temp_dir: str | None = None) -> str:
    """将 PDF 文件转换为 Markdown（命令行同步版）。"""
    pdf_path = str(Path(pdf_path).resolve())
    images_dir = temp_dir or settings.temp_dir

    agent = build_agent()

    user_message = (
        f"请将以下 PDF 文件转换为 Markdown：\n"
        f"- PDF 路径：{pdf_path}\n"
        f"- 临时图像目录：{images_dir}\n"
        f"- 输出 Markdown 路径：{output_path}\n"
        f"- PDF 渲染 DPI：{settings.pdf_dpi}"
    )

    logger.info("启动 Agent，PDF: %s → MD: %s", pdf_path, output_path)
    result = agent.invoke(
        {"messages": [("user", user_message)]},
        config={"recursion_limit": 500},
    )

    final_message = result["messages"][-1].content
    logger.info("Agent 完成：%s", final_message)
    return final_message
