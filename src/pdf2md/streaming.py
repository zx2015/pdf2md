"""
streaming.py — 基于 asyncio.Queue 的任务事件发布/订阅，用于 SSE 实时推送。

生命周期：
- 任务开始处理时，调用方可订阅该任务的事件队列。
- 后台处理协程通过 publish() 推送事件到所有订阅者。
- 任务完成/失败后调用 close() 发送哨兵 None，通知订阅者关闭流。

⚠️ 部署限制：
- `_subscribers` 是进程内的全局内存字典，仅在单个 Python 进程内有效。
- 若以多进程方式部署（如 `uvicorn --workers N`，N > 1），上传任务的请求与
  SSE 订阅请求可能被路由到不同的 worker 进程，导致订阅者收不到事件推送。
- 因此本服务必须以单 worker 方式运行（`uvicorn --workers 1`，即默认值）。
  如需水平扩展/多进程部署，需要把发布/订阅改造为基于 Redis Pub/Sub、
  Postgres LISTEN/NOTIFY 等跨进程消息队列的实现。
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

# task_id -> list of subscriber queues
# 注意：仅进程内有效，见上方模块 docstring 的部署限制说明。
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)


def subscribe(task_id: str) -> asyncio.Queue:
    """为指定任务创建并注册一个订阅队列，返回该队列。"""
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[task_id].append(q)
    return q


def unsubscribe(task_id: str, q: asyncio.Queue) -> None:
    """注销一个订阅队列。"""
    try:
        _subscribers[task_id].remove(q)
    except ValueError:
        pass
    if not _subscribers[task_id]:
        _subscribers.pop(task_id, None)


async def publish(task_id: str, event: dict) -> None:
    """将事件推送到该任务的所有订阅者队列。"""
    for q in list(_subscribers.get(task_id, [])):
        await q.put(event)


async def close(task_id: str) -> None:
    """发送哨兵 None 关闭所有订阅流，并清理注册表。"""
    for q in list(_subscribers.get(task_id, [])):
        await q.put(None)
    _subscribers.pop(task_id, None)
