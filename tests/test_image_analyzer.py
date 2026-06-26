"""
tests/test_image_analyzer.py — 测试 describe_image 工具
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
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

    def test_retries_timeout_and_returns_timeout_message(self, tmp_path, mocker):
        """网络超时应重试多次，最终返回包含重试信息的错误文本。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = httpx.TimeoutException("request timed out")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke({"image_path": str(image_path), "prompt": "描述图像"})

        assert result.startswith("⚠️ 错误：LLM 调用超时")
        assert "已重试" in result
        assert mock_llm.invoke.call_count == 4

    def test_retries_rate_limit_and_returns_rate_limit_message(self, tmp_path, mocker):
        """Rate limit 应重试后返回明确错误。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("429 Too Many Requests: rate limit exceeded")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)
        mocker.patch("pdf2md.tools.image_analyzer.time.sleep")

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke({"image_path": str(image_path), "prompt": "描述图像"})

        assert result.startswith("⚠️ 错误：触发 Rate Limit")
        assert "已重试" in result
        assert mock_llm.invoke.call_count == 4


class TestRepetitionDetection:
    def test_detect_single_char_repetition(self):
        """单字符连续重复 20+ 次应被检测为异常。"""
        from pdf2md.tools.image_analyzer import _detect_repetition

        normal = "# 标题\n\n这是正常的内容，没有重复。"
        assert _detect_repetition(normal) is None

        emoji_repeat = "📷" * 30
        assert _detect_repetition(emoji_repeat) is not None

        bullet_repeat = "▪" * 25 + " 一些文字"
        assert _detect_repetition(bullet_repeat) is not None

    def test_detect_line_repetition(self):
        """相同行重复 5+ 次应被检测为异常。"""
        from pdf2md.tools.image_analyzer import _detect_repetition

        repeated = ("这是一行内容\n" * 6) + "其他内容"
        result = _detect_repetition(repeated)
        assert result is not None
        assert "重复" in result

    def test_truncate_at_repetition(self):
        """截断函数应保留重复前的内容并附加警告。"""
        from pdf2md.tools.image_analyzer import _truncate_at_repetition

        text = "# 正常标题\n\n正常段落内容。\n\n" + "📷" * 50
        result = _truncate_at_repetition(text)

        assert "正常段落内容" in result
        assert "⚠️" in result
        assert "📷" * 50 not in result

    def test_describe_image_retries_on_repetition(self, tmp_path, mocker):
        """检测到重复输出时应使用修正 prompt 重试一次。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        repeat_response = "📷" * 50
        normal_response = "# 正常内容\n\n这是重试后的正常输出。"

        call_count = [0]

        def side_effect(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(content=repeat_response)
            return MagicMock(content=normal_response)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = side_effect
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke({"image_path": str(image_path), "prompt": "描述图像"})

        assert result == normal_response
        assert mock_llm.invoke.call_count == 2
        # 第二次 prompt 中应包含修正说明
        second_call_msg = mock_llm.invoke.call_args_list[1][0][0]
        assert "重要" in str(second_call_msg) or "重复" in str(second_call_msg)

    def test_describe_image_truncates_when_retry_still_repeats(self, tmp_path, mocker):
        """重试后仍有重复输出时应截断并附加警告，而非崩溃。"""
        image_path = tmp_path / "page_001.jpg"
        _create_dummy_jpeg(image_path)

        repeat_response = "# 正常开头\n\n" + "📷" * 50

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content=repeat_response)
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.image_analyzer import describe_image

        result = describe_image.invoke({"image_path": str(image_path), "prompt": "描述图像"})

        assert "⚠️" in result
        assert "正常开头" in result
        assert "📷" * 50 not in result
