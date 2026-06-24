"""
tests/conftest.py — 公共 fixtures
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_page_analysis_json() -> str:
    """返回单页分析结果的 JSON 字符串（用于测试 assemble_markdown）。"""
    data = {
        "page_number": 1,
        "content_blocks": [
            {"type": "text", "content": "# 标题一\n\n这是第一段文字。"},
            {
                "type": "table",
                "content": "| 姓名 | 年龄 |\n|------|------|\n| 张三 | 25 |",
            },
            {"type": "image_description", "content": "> 📷 一张柱状图，展示了各月销售额。"},
        ],
        "error": None,
    }
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def sample_pages_json(sample_page_analysis_json) -> str:
    """返回两页分析结果的 JSON 数组字符串。"""
    page1 = json.loads(sample_page_analysis_json)
    page2 = {
        "page_number": 2,
        "content_blocks": [
            {"type": "text", "content": "## 第二章\n\n第二页的文字内容。"},
        ],
        "error": None,
    }
    return json.dumps([page1, page2], ensure_ascii=False)


@pytest.fixture
def error_page_json() -> str:
    """返回一个解析失败的页面 JSON。"""
    data = {
        "page_number": 3,
        "content_blocks": [],
        "error": "LLM API timeout",
    }
    return json.dumps(data, ensure_ascii=False)
