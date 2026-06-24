from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pdf2md.config import settings

DB_FILE = "tasks.db"


def _tasks_root() -> Path:
    root = Path(settings.tasks_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _db_path() -> Path:
    return _tasks_root() / DB_FILE


@contextlib.contextmanager
def _db():
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                status      TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                page_count  INTEGER,
                error       TEXT
            )
        """)


@dataclass
class Task:
    id: str
    filename: str
    status: str  # pending | processing | completed | failed
    created_at: str
    updated_at: str
    page_count: int | None = None
    error: str | None = None

    @property
    def task_dir(self) -> Path:
        return _tasks_root() / self.id

    @property
    def input_pdf(self) -> Path:
        return self.task_dir / "input.pdf"

    @property
    def output_md(self) -> Path:
        return self.task_dir / "output.md"

    @property
    def images_dir(self) -> Path:
        return self.task_dir / "images"

    @property
    def results_dir(self) -> Path:
        """每页分析 JSON 存放目录（page_001.json, page_002.json, ...）。"""
        return self.task_dir / "results"

    @property
    def logs_file(self) -> Path:
        return self.task_dir / "logs.jsonl"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "page_count": self.page_count,
            "error": self.error,
        }


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        filename=row["filename"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        page_count=row["page_count"],
        error=row["error"],
    )


def create_task(filename: str) -> Task:
    """创建新任务，建立目录结构，写入数据库。"""
    task_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    task = Task(id=task_id, filename=filename, status="pending", created_at=now, updated_at=now)

    task.task_dir.mkdir(parents=True, exist_ok=True)
    task.images_dir.mkdir(exist_ok=True)

    with _db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, filename, status, created_at, updated_at) VALUES (?,?,?,?,?)",
            (task.id, task.filename, task.status, task.created_at, task.updated_at),
        )
    return task


def get_task(task_id: str) -> Task | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_tasks() -> list[Task]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    return [_row_to_task(r) for r in rows]


def update_status(
    task_id: str,
    status: str,
    page_count: int | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now().isoformat()
    with _db() as conn:
        conn.execute(
            "UPDATE tasks SET status=?, updated_at=?, page_count=COALESCE(?,page_count), error=? WHERE id=?",
            (status, now, page_count, error, task_id),
        )


def delete_task(task_id: str) -> bool:
    """删除任务记录及其目录，返回是否成功。"""
    task = get_task(task_id)
    if task is None:
        return False
    if task.task_dir.exists():
        shutil.rmtree(task.task_dir)
    with _db() as conn:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return True


def append_log(task_id: str, entry: dict) -> None:
    """将日志条目追加到任务的 logs.jsonl 文件。"""
    task = get_task(task_id)
    if task is None:
        return
    with task.logs_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_logs(task_id: str) -> list[dict]:
    """读取任务已记录的全部日志条目。"""
    task = get_task(task_id)
    if task is None or not task.logs_file.exists():
        return []
    entries = []
    with task.logs_file.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries
