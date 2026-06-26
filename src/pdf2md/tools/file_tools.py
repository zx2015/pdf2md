"""
file_tools.py — 文件读写工具集，供 Agent 在组装阶段使用。

设计原则：
- 支持按行读写，避免一次性加载大文件
- read_file_lines 支持负数索引（如 start_line=-15 表示倒数第15行），
  实现「滑动窗口」组装时只需读取输出文件末尾 N 行
- write_file_lines 支持追加和覆盖两种模式
"""
from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def read_file_lines(path: str, start_line: int = 1, end_line: int = -1) -> str:
    """读取文件指定行范围的内容。

    支持负数行号：start_line=-15 表示从倒数第 15 行开始，
    用于「滑动窗口」组装时读取输出文件末尾内容。

    Args:
        path: 文件路径。
        start_line: 起始行号（从 1 开始）。负数表示从末尾倒数。
        end_line: 结束行号（含）。-1 表示文件最后一行。

    Returns:
        指定行范围的文本内容。文件不存在时返回空字符串。
    """
    file_path = Path(path)
    if not file_path.exists():
        return ""

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        logger.warning("read_file_lines 收到二进制文件路径（疑似图片），跳过: %s", path)
        return f"⚠️ 该路径是二进制文件，不是文本文件，请检查路径是否正确: {path}"
    total = len(lines)
    if total == 0:
        return ""

    # 处理负数索引
    if start_line < 0:
        start_line = max(1, total + start_line + 1)
    if end_line < 0 or end_line > total:
        end_line = total

    start_line = max(1, min(start_line, total))
    end_line = max(start_line, end_line)

    selected = lines[start_line - 1 : end_line]
    return "".join(selected)


@tool
def write_file_lines(path: str, content: str, mode: str = "append") -> str:
    """向文件写入文本内容。

    Args:
        path: 输出文件路径。目录不存在时自动创建。
        content: 要写入的文本。
        mode: 写入模式：
              'append'    — 追加到文件末尾（默认，组装时逐页追加）
              'overwrite' — 覆盖整个文件

    Returns:
        写入结果描述（行数、字符数）。
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    write_mode = "w" if mode == "overwrite" else "a"
    with file_path.open(write_mode, encoding="utf-8") as f:
        f.write(content)

    line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    logger.debug("写入文件 %s：%d 行，%d 字符（%s）", path, line_count, len(content), mode)
    return f"✓ 已写入 {line_count} 行，{len(content)} 字符（模式: {mode}）→ {path}"
