"""
tests/test_image_analyzer.py — 测试 describe_image 工具
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _create_dummy_jpeg(path: Path) -> None:
    """创建最小合法 JPEG 文件（100x100 白色像素）。"""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color=(255, 255, 255))
    img.save(str(path), "JPEG")


class TestDescribeImage:
    def test_returns_string_response(self, tmp_path, mocker):
        """正常调用应返回 LLM 的原始文本响应。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="# 标题\n\n这是正文内容。")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke(
            {"image_path": str(image_path), "prompt": "提取此页内容为 Markdown"}
        )

        assert result == "# 标题\n\n这是正文内容。"
        mock_llm.invoke.assert_called_once()

    def test_prompt_is_passed_to_llm(self, tmp_path, mocker):
        """传入的 prompt 应完整传递给 LLM。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="some response")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        custom_prompt = "这是第 5 页，已知是技术手册，请提取表格为 Markdown。"
        describe_image.invoke({"image_path": str(image_path), "prompt": custom_prompt})

        call_args = mock_llm.invoke.call_args[0][0]
        message_content = call_args[0].content
        text_block = next(b for b in message_content if b["type"] == "text")
        assert custom_prompt in text_block["text"]

    def test_handles_nonexistent_image(self, tmp_path):
        """不存在的图像路径应返回 '⚠️ 错误：' 开头的字符串，而不抛出异常。"""
        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke(
            {"image_path": str(tmp_path / "ghost.jpg"), "prompt": "描述图像"}
        )

        assert result.startswith("⚠️ 错误：")
        assert "不存在" in result

    def test_handles_llm_exception(self, tmp_path, mocker):
        """LLM 调用抛出异常时，应返回 '⚠️ 错误：' 开头的字符串，不向上传播。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = TimeoutError("API timeout")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke(
            {"image_path": str(image_path), "prompt": "描述图像"}
        )

        assert result.startswith("⚠️ 错误：")
        assert "timeout" in result.lower() or "LLM" in result

    def test_returns_raw_text_not_json(self, tmp_path, mocker):
        """describe_image 应返回原始文本，不做 JSON 解析或包装。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mermaid_response = "```mermaid\nflowchart TD\n  A-->B\n```"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=mermaid_response)
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke(
            {"image_path": str(image_path), "prompt": "识别流程图并转为 Mermaid"}
        )

        assert result == mermaid_response
