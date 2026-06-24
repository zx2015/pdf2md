"""
tests/test_agent.py — 测试 LangGraph React Agent 工具链协作（mock LLM）
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestToolChain:
    def test_pdf_to_images_then_describe_then_write(self, tmp_path, mocker):
        """验证 pdf_to_images → describe_image → write_file_lines 工具链协作正确。"""
        import fitz

        # 准备真实 2 页 PDF
        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        for i in range(2):
            p = doc.new_page()
            p.insert_text((72, 72), f"Page {i + 1} content")
        doc.save(str(pdf_path))
        doc.close()

        output_path = tmp_path / "output.md"
        images_dir = tmp_path / "images"

        # Mock describe_image 的 LLM：每次调用返回不同页面内容
        page_responses = [
            "# 第一章\n\n第一页内容。",
            "## 第二节\n\n第二页内容。",
        ]
        call_count = 0

        def mock_llm_invoke(messages):
            nonlocal call_count
            resp = page_responses[call_count % len(page_responses)]
            call_count += 1
            return MagicMock(content=resp)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = mock_llm_invoke
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        # 工具链：Step 1 — PDF → images
        from pdf2md.tools.pdf_to_image import pdf_to_images

        images_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(images_dir), "dpi": 72}
        )
        images = json.loads(images_json)
        assert len(images) == 2

        # 工具链：Step 2 — describe_image + write_file_lines（模拟 Agent 逐页处理）
        from pdf2md.tools.file_tools import read_file_lines, write_file_lines
        from pdf2md.tools.image_analyzer import describe_image

        for i, img_path in enumerate(images):
            page_content = describe_image.invoke(
                {
                    "image_path": img_path,
                    "prompt": f"提取第 {i + 1} 页内容为 Markdown",
                }
            )
            assert not page_content.startswith("⚠️"), f"第 {i+1} 页描述失败: {page_content}"

            # 读取末尾上下文（滑动窗口）
            if i > 0:
                context = read_file_lines.invoke({"path": str(output_path), "start_line": -15})
                assert isinstance(context, str)

            separator = "\n\n---\n\n" if i > 0 else ""
            write_file_lines.invoke(
                {"path": str(output_path), "content": separator + page_content, "mode": "append"}
            )

        # 验证输出
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "第一章" in content
        assert "第二节" in content
        assert "---" in content

    def test_describe_image_error_handling(self, tmp_path, mocker):
        """describe_image 失败时返回错误字符串，工具链可继续处理。"""
        import fitz

        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Test")
        doc.save(str(pdf_path))
        doc.close()

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = ConnectionError("Network error")
        mocker.patch("pdf2md.tools.image_analyzer._build_llm", return_value=mock_llm)

        from pdf2md.tools.file_tools import write_file_lines
        from pdf2md.tools.image_analyzer import describe_image
        from pdf2md.tools.pdf_to_image import pdf_to_images

        images_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(tmp_path / "images"), "dpi": 72}
        )
        images = json.loads(images_json)

        result = describe_image.invoke({"image_path": images[0], "prompt": "描述图像"})
        assert result.startswith("⚠️ 错误：")

        # Agent 应写入占位符继续执行
        placeholder = f"> ⚠️ 第 1 页解析失败：{result}\n"
        output_path = tmp_path / "output.md"
        write_file_lines.invoke({"path": str(output_path), "content": placeholder, "mode": "append"})
        assert "⚠️" in output_path.read_text(encoding="utf-8")

    @pytest.mark.e2e
    def test_full_conversion_with_real_api(self, tmp_path):
        """端到端测试：需要真实 OPENAI_API_KEY。用 pytest -m e2e 运行。"""
        import fitz

        pdf_path = tmp_path / "e2e_sample.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "This is an end-to-end test document.")
        page.insert_text((72, 120), "It contains simple text for LLM extraction.")
        doc.save(str(pdf_path))
        doc.close()

        output_path = tmp_path / "e2e_output.md"

        from pdf2md.agent import convert_pdf_to_markdown

        result = convert_pdf_to_markdown(str(pdf_path), str(output_path))
        assert Path(output_path).exists()
        content = Path(output_path).read_text(encoding="utf-8")
        assert len(content.strip()) > 0
