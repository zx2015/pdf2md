"""
tests/test_agent.py — 测试 per-page Agent 架构及工具链协作（mock LLM）
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestToolChain:
    def test_pdf_to_images_then_describe_then_write(self, tmp_path, mocker):
        """验证 pdf_to_images → describe_image → write_file_lines 工具链协作正确。"""
        import fitz

        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        for i in range(2):
            p = doc.new_page()
            p.insert_text((72, 72), f"Page {i + 1} content")
        doc.save(str(pdf_path))
        doc.close()

        output_path = tmp_path / "output.md"
        images_dir = tmp_path / "images"

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

        from pdf2md.tools.pdf_to_image import pdf_to_images

        images_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(images_dir), "dpi": 72}
        )
        images = json.loads(images_json)
        assert len(images) == 2

        from pdf2md.tools.file_tools import read_file_lines, write_file_lines
        from pdf2md.tools.image_analyzer import describe_image

        for i, img_path in enumerate(images):
            page_content = describe_image.invoke(
                {"image_path": img_path, "prompt": f"提取第 {i + 1} 页内容为 Markdown"}
            )
            assert not page_content.startswith("⚠️"), f"第 {i+1} 页描述失败: {page_content}"

            if i > 0:
                context = read_file_lines.invoke({"path": str(output_path), "start_line": -15})
                assert isinstance(context, str)

            separator = "\n\n---\n\n" if i > 0 else ""
            write_file_lines.invoke(
                {"path": str(output_path), "content": separator + page_content, "mode": "append"}
            )

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

        placeholder = f"> ⚠️ 第 1 页解析失败：{result}\n"
        output_path = tmp_path / "output.md"
        write_file_lines.invoke({"path": str(output_path), "content": placeholder, "mode": "append"})
        assert "⚠️" in output_path.read_text(encoding="utf-8")


class TestAstreamConversion:
    def test_yields_page_events(self, tmp_path, mocker):
        """astream_conversion 应按顺序 yield page_start / page_complete 事件。"""
        import fitz

        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        for i in range(3):
            p = doc.new_page()
            p.insert_text((72, 72), f"Page {i + 1}")
        doc.save(str(pdf_path))
        doc.close()

        output_path = tmp_path / "output.md"
        images_dir = tmp_path / "images"

        # Mock _build_page_agent 返回的 agent
        page_responses = [f"# 第{i+1}页\n内容。" for i in range(3)]

        def make_mock_agent(idx=[0]):
            mock_agent = MagicMock()

            async def fake_astream_events(inputs, config, version):
                # 模拟 tool_start → tool_end → AIMessage 事件
                # 直接写文件（模拟 Agent 行为）
                content = page_responses[idx[0] % len(page_responses)]
                idx[0] += 1
                msg = inputs["messages"][0][1]
                # 从 user_message 提取 output_path（反引号包裹的路径）
                for line in msg.split("\n"):
                    if "Markdown 输出文件路径" in line or "Markdown 输出文件" in line:
                        op = line.split("：")[-1].strip().strip("`")
                        Path(op).open("a", encoding="utf-8").write(content + "\n")
                        break
                yield {}  # 至少 yield 一次让 async for 能迭代

            mock_agent.astream_events = fake_astream_events
            return mock_agent

        mocker.patch("pdf2md.agent._build_page_agent", side_effect=make_mock_agent)

        from pdf2md.agent import astream_conversion

        async def run():
            events = []
            async for ev in astream_conversion(
                str(pdf_path), str(output_path), str(images_dir)
            ):
                events.append(ev)
            return events

        events = asyncio.run(run())
        types = [e["type"] for e in events]

        assert "page_start" in types
        assert "page_complete" in types
        # 3页，各有 page_start 和 page_complete
        assert types.count("page_start") == 3
        assert types.count("page_complete") == 3
        # 顺序检查：每页 page_start 在 page_complete 之前
        starts = [i for i, t in enumerate(types) if t == "page_start"]
        completes = [i for i, t in enumerate(types) if t == "page_complete"]
        for s, c in zip(starts, completes):
            assert s < c

    def test_page_processing_error_on_repeated_failure(self, tmp_path, mocker):
        """单页 Agent 连续失败 3 次应抛出 PageProcessingError 并携带页码。"""
        import fitz
        from pdf2md.agent import PageProcessingError, astream_conversion

        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Page 1")
        doc.save(str(pdf_path))
        doc.close()

        output_path = tmp_path / "output.md"
        images_dir = tmp_path / "images"

        def make_failing_agent():
            mock_agent = MagicMock()

            async def always_fail(inputs, config, version):
                raise ConnectionError("模拟网络错误")
                yield  # 使其成为 async generator

            mock_agent.astream_events = always_fail
            return mock_agent

        mocker.patch("pdf2md.agent._build_page_agent", side_effect=make_failing_agent)

        async def run():
            events = []
            async for ev in astream_conversion(
                str(pdf_path), str(output_path), str(images_dir)
            ):
                events.append(ev)

        with pytest.raises(PageProcessingError) as exc_info:
            asyncio.run(run())

        err = exc_info.value
        assert err.page_num == 1
        assert err.total_pages == 1

    def test_start_page_skips_processed_pages(self, tmp_path, mocker):
        """start_page=2 时跳过第1页，只处理第2页起。"""
        import fitz

        pdf_path = tmp_path / "sample.pdf"
        doc = fitz.open()
        for i in range(2):
            p = doc.new_page()
            p.insert_text((72, 72), f"Page {i + 1}")
        doc.save(str(pdf_path))
        doc.close()

        images_dir = tmp_path / "images"
        output_path = tmp_path / "output.md"

        # 先生成图像
        from pdf2md.tools.pdf_to_image import pdf_to_images
        pdf_to_images.invoke({"pdf_path": str(pdf_path), "output_dir": str(images_dir), "dpi": 72})

        processed_pages = []

        def make_mock_agent():
            mock_agent = MagicMock()

            async def capture_page(inputs, config, version):
                msg = inputs["messages"][0][1]
                for line in msg.split("\n"):
                    if "请处理第" in line:
                        import re
                        m = re.search(r"第 (\d+) 页", line)
                        if m:
                            processed_pages.append(int(m.group(1)))
                            break
                yield {}

            mock_agent.astream_events = capture_page
            return mock_agent

        mocker.patch("pdf2md.agent._build_page_agent", side_effect=make_mock_agent)

        from pdf2md.agent import astream_conversion

        async def run():
            async for _ in astream_conversion(
                str(pdf_path), str(output_path), str(images_dir), start_page=2
            ):
                pass

        asyncio.run(run())
        assert processed_pages == [2], f"应只处理第2页，实际处理: {processed_pages}"

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
