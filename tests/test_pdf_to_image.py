"""
tests/test_pdf_to_image.py — 测试 pdf_to_images 工具
"""
from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

import pytest


def _create_minimal_pdf(path: Path) -> None:
    """创建一个最小化的合法单页 PDF 文件（不依赖外部库）。"""
    # 构造一个包含单页空白内容的 PDF
    content = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj
xref
0 4
0000000000 65535 f\r
0000000009 00000 n\r
0000000058 00000 n\r
0000000115 00000 n\r
trailer<</Size 4/Root 1 0 R>>
startxref
217
%%EOF
"""
    path.write_bytes(content)


class TestPdfToImages:
    def test_converts_pdf_to_images(self, tmp_path):
        """正常 PDF 应成功转换为 JPEG 列表。"""
        import fitz

        # 用 PyMuPDF 创建真实 PDF
        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "Hello, pdf2md!")
        doc.save(str(pdf_path))
        doc.close()

        output_dir = tmp_path / "images"
        from pdf2md.tools.pdf_to_image import pdf_to_images

        result_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(output_dir), "dpi": 72}
        )
        paths = json.loads(result_json)

        assert len(paths) == 1
        assert Path(paths[0]).exists()
        assert paths[0].endswith(".jpg")
        assert "page_001" in paths[0]

    def test_creates_output_dir_if_not_exists(self, tmp_path):
        """输出目录不存在时应自动创建。"""
        import fitz

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        nonexistent_dir = tmp_path / "new" / "deep" / "dir"
        assert not nonexistent_dir.exists()

        from pdf2md.tools.pdf_to_image import pdf_to_images

        pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(nonexistent_dir), "dpi": 72}
        )
        assert nonexistent_dir.exists()

    def test_multi_page_pdf(self, tmp_path):
        """多页 PDF 应输出多个 JPEG，按页码命名。"""
        import fitz

        pdf_path = tmp_path / "multi.pdf"
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1}")
        doc.save(str(pdf_path))
        doc.close()

        output_dir = tmp_path / "images"
        from pdf2md.tools.pdf_to_image import pdf_to_images

        result_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(output_dir), "dpi": 72}
        )
        paths = json.loads(result_json)

        assert len(paths) == 3
        filenames = [Path(p).name for p in paths]
        assert filenames == ["page_001.jpg", "page_002.jpg", "page_003.jpg"]

    def test_raises_for_nonexistent_pdf(self, tmp_path):
        """不存在的 PDF 路径应抛出 FileNotFoundError。"""
        from pdf2md.tools.pdf_to_image import pdf_to_images

        with pytest.raises(FileNotFoundError, match="PDF 文件不存在"):
            pdf_to_images.invoke(
                {
                    "pdf_path": str(tmp_path / "ghost.pdf"),
                    "output_dir": str(tmp_path / "images"),
                }
            )

    def test_image_naming_format(self, tmp_path):
        """图像文件名应使用三位补零格式，如 page_001.jpg。"""
        import fitz

        pdf_path = tmp_path / "test.pdf"
        doc = fitz.open()
        doc.new_page()
        doc.save(str(pdf_path))
        doc.close()

        output_dir = tmp_path / "images"
        from pdf2md.tools.pdf_to_image import pdf_to_images

        result_json = pdf_to_images.invoke(
            {"pdf_path": str(pdf_path), "output_dir": str(output_dir), "dpi": 72}
        )
        paths = json.loads(result_json)
        assert Path(paths[0]).name == "page_001.jpg"
