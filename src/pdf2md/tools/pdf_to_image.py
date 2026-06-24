from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import fitz  # PyMuPDF
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> str:
    """将 PDF 每页转换为 JPEG 文件。

    Args:
        pdf_path: PDF 文件的路径。
        output_dir: 输出 JPEG 文件的目录。
        dpi: 渲染分辨率，默认 150。

    Returns:
        JSON 字符串，包含按页码排序的 JPEG 文件路径列表。
        例如: '["./tmp/page_001.jpg", "./tmp/page_002.jpg"]'
    """
    pdf_path = str(Path(pdf_path).resolve())
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    image_paths: list[str] = []
    matrix = fitz.Matrix(dpi / 72, dpi / 72)

    doc = fitz.open(pdf_path)
    try:
        logger.info("开始转换 PDF，共 %d 页：%s", len(doc), pdf_path)
        for page_index in range(len(doc)):
            page = doc[page_index]
            pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
            image_filename = f"page_{page_index + 1:03d}.jpg"
            image_path = str(output_dir_path / image_filename)
            pixmap.save(image_path, output="jpeg", jpg_quality=85)
            image_paths.append(image_path)
            logger.debug("已转换第 %d 页 → %s", page_index + 1, image_path)
    finally:
        doc.close()

    logger.info("PDF 转换完成，共 %d 张图像", len(image_paths))
    return json.dumps(image_paths, ensure_ascii=False)
