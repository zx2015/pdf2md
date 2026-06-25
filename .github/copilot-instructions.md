# Copilot Instructions — pdf2md

## 架构概述

两层架构：**Web 服务层**（FastAPI + SSE）+ **Agent 处理层**（per-page LangGraph React Agent，Python 外层串行循环驱动）。

```
浏览器上传 PDF
    │ POST /api/tasks
    ▼
web/app.py → task_manager.py（SQLite + tasks/{uuid}/目录）
    │ BackgroundTask → _run_task(task_id)
    ▼
agent.astream_conversion(pdf, output, images_dir, task_id, start_page=1)
    │
    ├─ Step 1: pdf_to_images → JPEG 列表（已存在则跳过，断点续传用）
    │
    └─ Step 2: for page_N in pages[start_page:]:
          └─ _process_one_page()  ← 每页独立 LangGraph Agent 实例
                describe_image(page_N.jpg, prompt)  → Markdown 文本
                read_file_lines(output.md, -15)     → 末尾上下文
                write_file_lines(output.md, content, append)
                失败 → 外层重试最多 3 次 → PageProcessingError → 记录断点
```

**关键文件**：
- `web/app.py` — 所有 API 路由（上传/列表/详情/删除/resume/SSE/下载）
- `task_manager.py` — 任务 CRUD（SQLite），含 `resume_from_page` 字段和 `set_resume_page()`
- `streaming.py` — asyncio pub/sub（`subscribe/publish/close`）
- `agent.py` — `PageProcessingError`, `_build_page_agent()`, `_process_one_page()`, `astream_conversion()`
- `tools/image_analyzer.py` — `describe_image(image_path, prompt)`，含 tenacity 重试
- `tools/file_tools.py` — `read_file_lines` / `write_file_lines`
- `tools/pdf_to_image.py` — `pdf_to_images`

## 任务目录结构

```
tasks/{uuid}/
├── input.pdf      # 上传的原始文件
├── images/        # page_001.jpg ... (pdf_to_images 输出)
├── output.md      # 最终 Markdown（write_file_lines 逐页追加）
└── logs.jsonl     # 所有事件（SSE 断线重连时回放）
```

## 开发命令

```bash
pip install -e ".[dev]"          # 安装依赖
python -m pdf2md serve --reload  # 启动 Web 服务（开发模式）
python -m pdf2md convert input.pdf -o output.md  # 命令行转换

pytest                           # 全部测试
pytest -m "not e2e"              # 跳过需要 API Key 的测试
PYTHONPATH=src pytest -m "not e2e"   # 未安装包时使用
```

## 断点续传机制

- `tasks` 表含 `resume_from_page INTEGER` 列
- 页面重试 3 次后 → `PageProcessingError(page_num, total_pages, cause)` → `set_resume_page(task_id, N)`
- `POST /api/tasks/{id}/resume` → 重置状态 → 重新启动 `_run_task` → 读 `task.resume_from_page` → `start_page=N`
- 图像已存在时跳过 PDF 转换，直接从第 N 页继续

## Agent 工具集

| 工具 | 输入 | 输出 |
|------|------|------|
| `pdf_to_images` | pdf_path, output_dir, dpi | JSON 路径列表 |
| `describe_image` | image_path, prompt | 原始文本（LLM 直接响应）|
| `read_file_lines` | path, start_line, end_line | 文本内容（支持负数索引）|
| `write_file_lines` | path, content, mode | 写入结果描述 |

## SSE 日志事件格式

```json
{"type": "task_start",     "message": "...",                          "timestamp": "..."}
{"type": "agent_thinking", "content": "...",                          "timestamp": "..."}
{"type": "tool_start",     "tool": "describe_image", "input": {...},  "timestamp": "..."}
{"type": "tool_end",       "tool": "describe_image", "output": "...", "timestamp": "..."}
{"type": "page_start",     "page": 1, "total": 10,                    "timestamp": "..."}
{"type": "page_complete",  "page": 1, "total": 10,                    "timestamp": "..."}
{"type": "page_retry",     "page": 2, "attempt": 1, "error": "...",   "timestamp": "..."}
{"type": "task_complete",  "output_path": "...", "page_count": 10,    "timestamp": "..."}
{"type": "task_error",     "error": "...", "resume_from_page": 5,     "timestamp": "..."}
```

## 关键约定

- 所有工具用 `@tool` 装饰，docstring 作为 LLM 工具描述（不要省略）
- 工具内部用 `logging`，禁止 `print`
- `describe_image` 失败时返回 `"⚠️ 错误：..."` 字符串，不抛异常
- mock LLM 时 patch `pdf2md.tools.image_analyzer._build_llm`
- mock per-page agent 时 patch `pdf2md.agent._build_page_agent`

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 必填 | — |
| `LLM_MODEL` | 视觉模型（需支持 image_url）| `gpt-4o` |
| `OPENAI_BASE_URL` | 自定义 API 端点 | OpenAI 官方 |
| `PDF_DPI` | PDF 渲染分辨率 | `150` |
| `TASKS_DIR` | 任务存储根目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发 Web 任务数 | `3` |
| `HOST` / `PORT` | Web 服务监听 | `0.0.0.0:8000` |
| `PAGE_TIMEOUT` | 单次 LLM 调用超时（秒）| `120` |
| `RETRY_ATTEMPTS` | tenacity 重试总次数 | `4` |
| `RETRY_WAIT_MIN` / `RETRY_WAIT_MAX` | 退避等待范围（秒）| `2` / `60` |
| `RATE_LIMIT_WAIT` | 429 时额外等待（秒）| `15` |
