"""
page_analyzer.py — analyze_all_pages 工具

并发分析所有页面图像，内部使用全局 Semaphore 控制并发 LLM 调用数量，
结果按页码写入磁盘，支持断点续传。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_core.tools.base import InjectedToolArg

logger = logging.getLogger(__name__)

# 全局 Semaphore：跨所有并发任务共享，真正限制 LLM 视觉 API 的并发数
_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        from pdf2md.config import settings
        _llm_semaphore = asyncio.Semaphore(settings.max_concurrent_llm_calls)
    return _llm_semaphore


@tool
async def analyze_all_pages(
    images_json: str,
    results_dir: str,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
) -> str:
    """并发分析所有页面图像，提取结构化内容，结果写入磁盘。

    内部使用全局 Semaphore 限制并发 LLM 调用数（由 MAX_CONCURRENT_LLM_CALLS 配置）。
    支持断点续传：已存在 page_NNN.json 的页面会自动跳过。

    Args:
        images_json: JSON 字符串，包含按页码排序的 JPEG 路径列表。
                     例如 '["tasks/abc/images/page_001.jpg", ...]'
        results_dir: 结果输出目录，每页写入 page_NNN.json。

    Returns:
        JSON 字符串：{"processed": N, "failed": M, "results_dir": "...绝对路径"}
    """
    from pdf2md.tools.image_analyzer import _analyze_image_async
    from pdf2md import streaming, task_manager

    images: list[str] = json.loads(images_json)
    results_path = Path(results_dir)
    results_path.mkdir(parents=True, exist_ok=True)

    total = len(images)
    task_id: str | None = None
    if config:
        task_id = config.get("configurable", {}).get("task_id")

    async def emit_progress(page_num: int, status: str, error: str | None = None) -> None:
        if not task_id:
            return
        entry: dict = {
            "type": "page_analyzed",
            "page": page_num,
            "total": total,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }
        if error:
            entry["error"] = error
        task_manager.append_log(task_id, entry)
        await streaming.publish(task_id, entry)

    async def process_one(image_path: str, page_num: int) -> dict:
        result_path = results_path / f"page_{page_num:03d}.json"

        # 断点续传：已处理的页跳过
        if result_path.exists():
            logger.debug("跳过已处理页 %d", page_num)
            return {"page_number": page_num, "status": "skipped"}

        async with _get_llm_semaphore():
            logger.info("分析第 %d/%d 页: %s", page_num, total, image_path)
            try:
                result = await _analyze_image_async(image_path, page_num)
                result_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                await emit_progress(page_num, "success")
                return {"page_number": page_num, "status": "success"}
            except Exception as exc:
                error_msg = str(exc)
                logger.error("第 %d 页分析失败: %s", page_num, error_msg)
                error_result = {
                    "page_number": page_num,
                    "layout": "unknown",
                    "content_blocks": [],
                    "error": error_msg,
                }
                result_path.write_text(
                    json.dumps(error_result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                await emit_progress(page_num, "failed", error_msg)
                return {"page_number": page_num, "status": "failed", "error": error_msg}

    tasks = [process_one(path, i + 1) for i, path in enumerate(images)]
    results = await asyncio.gather(*tasks)

    processed = sum(1 for r in results if r["status"] in ("success", "skipped"))
    failed = sum(1 for r in results if r["status"] == "failed")
    logger.info("全部页面处理完成：%d 成功，%d 失败", processed, failed)

    return json.dumps(
        {
            "processed": processed,
            "failed": failed,
            "results_dir": str(results_path.resolve()),
        },
        ensure_ascii=False,
    )
