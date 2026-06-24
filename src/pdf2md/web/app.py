from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pdf2md import task_manager, streaming
from pdf2md.agent import astream_conversion
from pdf2md.config import settings
from pdf2md.task_manager import init_db

logger = logging.getLogger(__name__)

# 并发限制：最多同时运行 N 个 Agent
_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
    return _semaphore


async def _run_task(task_id: str) -> None:
    """后台协程：运行 Agent，写日志，发布事件，更新任务状态。"""
    task = task_manager.get_task(task_id)
    if task is None:
        return

    async def emit(entry: dict) -> None:
        task_manager.append_log(task_id, entry)
        await streaming.publish(task_id, entry)

    async with _get_semaphore():
        try:
            task_manager.update_status(task_id, "processing")
            await emit({
                "type": "task_start",
                "message": f"开始处理：{task.filename}",
                "timestamp": datetime.now().isoformat(),
            })

            async for log_entry in astream_conversion(
                str(task.input_pdf),
                str(task.output_md),
                str(task.images_dir),
                task_id=task_id,
            ):
                await emit(log_entry)

            page_count = len(list(task.images_dir.glob("page_*.jpg")))
            task_manager.update_status(task_id, "completed", page_count=page_count)
            await emit({
                "type": "task_complete",
                "output_path": str(task.output_md),
                "page_count": page_count,
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as exc:
            error_msg = str(exc)
            logger.exception("任务 %s 处理失败", task_id)
            task_manager.update_status(task_id, "failed", error=error_msg)
            await emit({
                "type": "task_error",
                "error": error_msg,
                "timestamp": datetime.now().isoformat(),
            })
        finally:
            await streaming.close(task_id)


def create_app() -> FastAPI:
    app = FastAPI(title="pdf2md", version="0.1.0")

    @app.on_event("startup")
    async def startup():
        init_db()

    # ── Task API ──────────────────────────────────────────────────────────

    @app.post("/api/tasks", status_code=201)
    async def upload_pdf(file: UploadFile, background_tasks: BackgroundTasks):
        """上传 PDF，创建任务，立即开始后台处理。"""
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="只接受 .pdf 文件")

        task = task_manager.create_task(file.filename)
        pdf_bytes = await file.read()
        task.input_pdf.write_bytes(pdf_bytes)

        background_tasks.add_task(_run_task, task.id)
        return task.to_dict()

    @app.get("/api/tasks")
    async def list_tasks():
        """返回所有任务列表（按创建时间倒序）。"""
        return [t.to_dict() for t in task_manager.list_tasks()]

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        """返回单个任务详情。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task.to_dict()

    @app.delete("/api/tasks/{task_id}", status_code=204)
    async def delete_task(task_id: str):
        """删除任务及其所有文件（包括 PDF、图像、Markdown）。"""
        if not task_manager.delete_task(task_id):
            raise HTTPException(status_code=404, detail="任务不存在")

    @app.get("/api/tasks/{task_id}/stream")
    async def stream_task_logs(task_id: str):
        """SSE 端点：先回放已存储日志，再实时推送新事件。支持断线重连。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        async def event_generator():
            # 1. 回放已持久化的日志（断线重连时可获取完整历史）
            for entry in task_manager.read_logs(task_id):
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"

            # 2. 如果任务已结束，直接关流
            current = task_manager.get_task(task_id)
            if current and current.status in ("completed", "failed"):
                return

            # 3. 订阅实时事件
            q = streaming.subscribe(task_id)
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(q.get(), timeout=25.0)
                        if event is None:  # 哨兵：任务结束
                            break
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                streaming.unsubscribe(task_id, q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/tasks/{task_id}/output")
    async def get_task_output(task_id: str):
        """返回转换后的 Markdown 文本内容。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not task.output_md.exists():
            raise HTTPException(status_code=404, detail="Markdown 文件尚未生成")
        return {"content": task.output_md.read_text(encoding="utf-8")}

    @app.get("/api/tasks/{task_id}/download")
    async def download_task_output(task_id: str):
        """下载转换后的 Markdown 文件。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not task.output_md.exists():
            raise HTTPException(status_code=404, detail="Markdown 文件尚未生成")
        download_name = Path(task.filename).stem + ".md"
        return FileResponse(
            path=str(task.output_md),
            media_type="text/markdown",
            filename=download_name,
        )

    @app.get("/api/tasks/{task_id}/images")
    async def list_task_images(task_id: str):
        """返回任务所有页面图像文件名列表。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not task.images_dir.exists():
            return []
        return [f.name for f in sorted(task.images_dir.glob("page_*.jpg"))]

    @app.get("/api/tasks/{task_id}/images/{filename}")
    async def serve_task_image(task_id: str, filename: str):
        """返回任务页面图像文件。"""
        task = task_manager.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        img_path = task.images_dir / filename
        if not img_path.exists() or not img_path.is_file():
            raise HTTPException(status_code=404, detail="图像不存在")
        return FileResponse(str(img_path), media_type="image/jpeg")

    # ── 静态文件（SPA 前端，最后挂载，避免覆盖 API） ─────────────────────
    static_dir = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
