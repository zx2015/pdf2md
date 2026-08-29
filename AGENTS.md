# AGENTS.md

## 项目概述

pdf2md 是一个基于 **LangGraph React Agent** 的 PDF 转 Markdown 工具，提供 Web UI 和命令行两种使用方式。
采用 **per-page Agent 架构**：Python 外层循环串行驱动，每页独立创建一个 LangGraph Agent 实例，彻底避免长文档的上下文溢出问题，并支持断点续传。

## 项目结构

```
src/pdf2md/
├── agent.py              # 核心：per-page Agent 循环、PageProcessingError、astream_conversion()
├── config.py             # 环境变量配置（Settings，双 Provider）
├── llm.py                # build_vision_llm() / build_orchestrator_llm() 双 Provider 构建函数
├── model_registry.py     # get_model_limits()：视觉模型 context window/最大输出 token 分层探测
├── task_manager.py       # 任务 CRUD + SQLite + 目录管理 + resume_from_page + model
├── streaming.py          # asyncio pub/sub，为 SSE 提供实时事件总线
├── cli.py                # 命令行：convert（单文件）| serve（启动 Web）
├── tools/
│   ├── pdf_to_image.py   # Tool: pdf_to_images — PDF 每页 → JPEG
│   ├── image_analyzer.py # Tool: describe_image — 图像 → Markdown 文本（含 tenacity 重试、
│                         #      重复输出检测、任务级视觉模型切换）
│   └── file_tools.py     # Tool: read_file_lines / write_file_lines
└── web/
    ├── app.py            # FastAPI 应用工厂 + 所有 API 路由（含 /resume 端点）
    └── static/
        ├── index.html    # 单页面 Web 应用（SPA）
        └── vendor/       # 本地静态资源：bootstrap-icons, marked.js, KaTeX
tests/
├── conftest.py
├── fixtures/
├── test_pdf_to_image.py
├── test_image_analyzer.py
├── test_assembler.py
└── test_agent.py
docs/
├── requirements.md
└── design.md
```

## 开发环境

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填写 SILICONFLOW_API_KEY / OPENAI_API_KEY（详见下方"环境变量"）
```

## 运行命令

```bash
# 启动 Web 服务（推荐）
python -m pdf2md serve
# 开发模式（热重载）
python -m pdf2md serve --reload --port 8000

# 命令行单文件转换
python -m pdf2md convert input.pdf -o output.md
```

## 测试命令

```bash
pytest                          # 全部测试
pytest -m "not e2e"            # 跳过需要 API Key 的测试
pytest tests/test_agent.py     # 单个文件
PYTHONPATH=src pytest -m "not e2e"   # 未安装包时使用
```

## 架构关键点

### 请求 → 响应流程

```
浏览器上传 PDF
      │ POST /api/tasks
      ▼
FastAPI 创建任务目录 + 写入 SQLite
      │ BackgroundTask → _run_task(task_id)
      ▼
astream_conversion(pdf, output, images_dir, start_page)
      │
      ├─ Step 1: pdf_to_images() → [page_001.jpg … page_N.jpg]
      │
      └─ Step 2: for page in pages[start_page:]:
            │
            └─ _process_one_page()  ← 全新 LangGraph Agent 实例
                  │  工具：describe_image, read_file_lines, write_file_lines
                  │  失败重试 3 次 → PageProcessingError → 记录断点
                  ▼
              SSE 事件流 → task_manager.append_log() → logs.jsonl
                                   │
                              streaming.publish() → asyncio.Queue
                                                         │
                                              GET /api/tasks/{id}/stream
                                           ◄───────────────────────────┘
浏览器 EventSource
```

### Per-page Agent 工作流

每页 Agent 接收用户消息（当前页码、图片路径、上一页路径、输出文件路径），执行：

1. `read_file_lines(output.md, start_line=-15)` — 读取上文末尾 15 行（第 1 页跳过）
2. `describe_image(page_N.jpg, prompt)` — 带上下文 prompt 识别当前页
3. 按需 `describe_image(page_N-1.jpg, ...)` — 拼接检查时查看上一页（可选）
4. `write_file_lines(output.md, content, "append")` — 追加到输出文件

每页 Agent recursion_limit=20，不共享任何状态。

### 断点续传机制

- `task_manager.tasks` 表含 `resume_from_page INTEGER` 列
- 某页重试 3 次后仍失败 → 抛出 `PageProcessingError(page_num, total_pages, cause)`
- `_run_task` 捕获后调用 `set_resume_page(task_id, page_num)` + 写入 `task_error` 事件（含 `resume_from_page`）
- `POST /api/tasks/{id}/resume` 端点：检查 `resume_from_page`，重置状态，重新启动 `_run_task`
- `_run_task` 读取 `task.resume_from_page`，传给 `astream_conversion(start_page=N)`
- 图像已存在时跳过 PDF 转换步骤

### SSE 断线重连机制

1. 每个事件实时写入 `tasks/{id}/logs.jsonl`（持久化）
2. SSE 端点先回放 `logs.jsonl` 全部历史，再订阅 live queue
3. 浏览器刷新或重连后自动获取完整日志，无数据丢失

### 任务目录结构

```
tasks/
└── {uuid}/
    ├── input.pdf    # 原始上传文件
    ├── images/      # page_001.jpg, page_002.jpg, ...
    ├── output.md    # 转换结果（逐页追加）
    └── logs.jsonl   # 每行一条日志 JSON（SSE 回放用）
```

### SSE 日志事件格式

| type | 说明 | 关键字段 |
|------|------|------|
| `task_start` | 任务开始 | `message` |
| `agent_thinking` | Agent 推理文本流 | `content` |
| `tool_start` | 工具调用开始 | `tool`, `input` |
| `tool_end` | 工具调用结束 | `tool`, `output` |
| `page_start` | 开始处理某页 | `page`, `total` |
| `page_complete` | 某页处理完成 | `page`, `total` |
| `page_retry` | 某页重试 | `page`, `attempt`, `error` |
| `task_complete` | 全部完成 | `output_path`, `page_count` |
| `task_error` | 任务失败 | `error`, `resume_from_page`（可选）|

### 工具约定

工具函数用 `@tool` 装饰，docstring 作为 LLM 工具描述（不要省略）。

| 工具 | 输入 | 输出 |
|------|------|------|
| `pdf_to_images` | pdf_path, output_dir, dpi | JSON 字符串（路径列表）|
| `describe_image` | image_path, prompt | 原始 Markdown 文本（视觉 LLM 直接响应）|
| `read_file_lines` | path, start_line, end_line | 文本内容（支持负数索引）|
| `write_file_lines` | path, content, mode | 写入结果描述 |

### 错误处理约定

- `describe_image` 内部有 tenacity 重试（网络错误/超时/rate limit），失败后返回 `"⚠️ 错误：..."` 字符串
- 检测到重复输出时用自然语言批评性 prompt 重试一次，仍重复则直接截断并附加警告
- 工具内部用 `logging`，禁止 `print`
- 单页 Agent 失败时由外层循环重试最多 3 次；3 次后抛 `PageProcessingError`
- mock LLM 时 patch `pdf2md.tools.image_analyzer._build_llm`

## 关键依赖

| 包 | 用途 |
|----|------|
| `langgraph` | React Agent 框架 |
| `langchain-openai` | LLM 调用（SiliconFlow 视觉 + 编排 Agent，均为 OpenAI-compatible）|
| `pymupdf` | PDF 渲染为图像 |
| `fastapi` + `uvicorn` | Web 服务 |
| `pydantic-settings` | 环境变量配置 |
| `tenacity` | describe_image LLM 调用重试 |

## 双 Provider 架构

- **视觉 Provider**（固定为 SiliconFlow）：`tools/image_analyzer.py` 的 `describe_image` 实际调用，模型固定使用 `settings.vision_chat_model`，不支持按任务切换。
- **编排 Agent Provider**（独立配置，任意 OpenAI-compatible 服务）：`agent.py` 的 `_build_page_agent()` 使用，需支持 Tool Calling，与视觉 Provider 完全独立（并非所有支持视觉理解的模型都同时支持 Tool Calling）。
- `llm.py` 提供 `build_vision_llm()` / `build_orchestrator_llm()` 两个构建函数；`build_vision_llm()` 通过 `settings.effective_vision_max_tokens` 获取 `max_tokens`，该属性在 `VISION_MAX_TOKENS` 未显式配置时会调用 `model_registry.get_model_limits()` 做分层自动探测（精确表 → 可选的 litellm 注册表 → 前缀模糊匹配 → 安全默认值）。

## 环境变量

**Provider 1：视觉 LLM（固定为 SiliconFlow）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SILICONFLOW_API_KEY` | 必填 | — |
| `SILICONFLOW_BASE_URL` | SiliconFlow API 端点 | `https://api.siliconflow.cn/v1` |
| `VISION_CHAT_MODEL` | `describe_image` 固定使用的视觉模型 | `Qwen/Qwen3.5-4B` |

**Provider 2：编排 Agent（独立配置）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 必填 | — |
| `OPENAI_BASE_URL` | 自定义 API 端点 | OpenAI 官方 |
| `ORCHESTRATOR_MODEL` | 编排 Agent 模型，需支持 Tool Calling | `gpt-4o-mini` |

**其他**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PDF_DPI` | PDF 渲染分辨率 | `150` |
| `TASKS_DIR` | 任务存储根目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发 Web 任务数 | `3` |
| `HOST` / `PORT` | Web 服务监听 | `0.0.0.0:8000` |
| `PAGE_TIMEOUT` | 单次视觉 LLM 调用超时（秒，为空闲超时非总耗时）| `120` |
| `VISION_MAX_TOKENS` | 视觉模型单次输出最大 token 数（硬性上限，防止重复输出循环无限生成）；留空则按模型自动探测（见 `model_registry.py`）| 自动探测 |
| `RETRY_ATTEMPTS` | tenacity 重试总次数（含首次）| `4` |
| `RETRY_WAIT_MIN` | 首次重试等待秒数 | `2` |
| `RETRY_WAIT_MAX` | 最大重试等待秒数 | `60` |
| `RATE_LIMIT_WAIT` | 429 时额外等待秒数 | `15` |
