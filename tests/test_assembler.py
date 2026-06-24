"""
tests/test_assembler.py — 测试 assemble_markdown 工具
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestAssembleMarkdown:
    def test_writes_markdown_file(self, tmp_path, sample_pages_json):
        """正常输入应写入 Markdown 文件并返回绝对路径。"""
        output_path = tmp_path / "output.md"

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": sample_pages_json, "output_path": str(output_path)}
        )

        assert Path(result).exists()
        assert Path(result).is_absolute()
        content = Path(result).read_text(encoding="utf-8")
        assert "# 标题一" in content
        assert "| 姓名 | 年龄 |" in content
        assert "> 📷" in content
        assert "## 第二章" in content

    def test_pages_separated_by_divider(self, tmp_path, sample_pages_json):
        """多页之间应用 '---' 分隔。"""
        output_path = tmp_path / "output.md"

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": sample_pages_json, "output_path": str(output_path)}
        )

        content = Path(result).read_text(encoding="utf-8")
        assert "---" in content

    def test_pages_sorted_by_page_number(self, tmp_path):
        """页面应按 page_number 升序排列，而非输入顺序。"""
        pages = [
            {"page_number": 3, "content_blocks": [{"type": "text", "content": "第三页"}], "error": None},
            {"page_number": 1, "content_blocks": [{"type": "text", "content": "第一页"}], "error": None},
            {"page_number": 2, "content_blocks": [{"type": "text", "content": "第二页"}], "error": None},
        ]
        output_path = tmp_path / "output.md"

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": json.dumps(pages), "output_path": str(output_path)}
        )

        content = Path(result).read_text(encoding="utf-8")
        pos1 = content.index("第一页")
        pos2 = content.index("第二页")
        pos3 = content.index("第三页")
        assert pos1 < pos2 < pos3

    def test_error_page_renders_placeholder(self, tmp_path, error_page_json):
        """解析失败的页面应渲染为警告占位符。"""
        output_path = tmp_path / "output.md"

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": f"[{error_page_json}]", "output_path": str(output_path)}
        )

        content = Path(result).read_text(encoding="utf-8")
        assert "⚠️" in content
        assert "第 3 页解析失败" in content
        assert "LLM API timeout" in content

    def test_creates_parent_directories(self, tmp_path):
        """输出目录不存在时应自动创建。"""
        deep_output = tmp_path / "a" / "b" / "c" / "output.md"
        pages = [{"page_number": 1, "content_blocks": [{"type": "text", "content": "文字"}], "error": None}]

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": json.dumps(pages), "output_path": str(deep_output)}
        )

        assert Path(result).exists()

    def test_empty_content_blocks_skipped(self, tmp_path):
        """空内容块不应出现在输出中。"""
        pages = [
            {
                "page_number": 1,
                "content_blocks": [
                    {"type": "text", "content": ""},
                    {"type": "text", "content": "有效内容"},
                    {"type": "text", "content": "   "},
                ],
                "error": None,
            }
        ]
        output_path = tmp_path / "output.md"

        from pdf2md.assembler import assemble_markdown

        result = assemble_markdown.invoke(
            {"pages_json": json.dumps(pages), "output_path": str(output_path)}
        )

        content = Path(result).read_text(encoding="utf-8")
        assert content.strip() == "有效内容"
