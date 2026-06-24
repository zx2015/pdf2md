# AGENTS.md

## 项目概述

pdf2md 是一个基于 **LangGraph React Agent** 的 PDF 转 Markdown 工具，提供 Web UI 和命令行两种使用方式。Agent 编排三个工具完成转换，处理过程通过 SSE 实时推送到浏览器。

## 项目结构

```
src/pdf2md/
├── agent.py              # LangGraph React Agent（同步 + 异步流式两个入口）
├── config.py             # 环境变量配置（Settings dataclass）
├── assembler.py          # Markdown 组装（Tool 3）
├── task_manager.py       # 任务 CRUD + SQLite + 目录管理
├── streaming.py          # asyncio pub/sub，为 SSE 提供实时事件总线
├── cli.py                # 命令行：convert（单文件）| serve（启动 Web）
├── tools/
│   ├── pdf_to_image.py    # Tool 1: PDF 每页 → JPEG
│   └── image_analyzer.py  # Tool 2: JPEG → JSON 结构化内容块
└── web/
    ├── app.py             # FastAPI 应用工厂 + 所有 API 路由
    └── static/
        ├── index.html     # 首页：上传 + 任务历史
        └── task.html      # 任务详情：实时日志流 + Markdown 预览
tests/
├── fixtures/              # 测试用 PDF 和参考 Markdown
├── conftest.py            # 公共 fixtures
├── test_pdf_to_image.py
├── test_image_analyzer.py
├── test_assembler.py
└── test_agent.py
docs/
├── requirements.md        # 完整需求文档
└── design.md              # 架构与设计文档
```

## 开发环境

```bash
pip install -e ".[dev]"
cp .env.example .env   # 填写 OPENAI_API_KEY
```

## 运行命令

```bash
# 启动 Web 服务（推荐）
python -m pdf2md serve
# 或 开发模式（热重载）
python -m pdf2md serve --reload --port 8000

# 命令行单文件转换
python -m pdf2md convert input.pdf -o output.md
```

## 测试命令

```bash
pytest                                                      # 全部测试
pytest -m "not e2e"                                        # 跳过需要 API Key 的测试
pytest tests/test_assembler.py                             # 单个文件
pytest tests/test_assembler.py::TestAssembleMarkdown::test_writes_markdown_file  # 单个用例
```

## 架构关键点

### 请求 → 响应流程

```
浏览器上传 PDF
      │ POST /api/tasks
      ▼
FastAPI 创建任务目录 + 写入 SQLite
      │ BackgroundTask
      ▼
_run_task(task_id) [asyncio协程]
      │ astream_conversion()
      ▼
LangGraph astream_events()  ──► _format_langgraph_event()
      │                               │
      │                               ▼ dict
      │                    task_manager.append_log()  → logs.jsonl
      │                    streaming.publish()        → asyncio.Queue
      │                                                     │
      ▼                                              SSE /api/tasks/{id}/stream
浏览器 EventSource                              ◄──────────────────────────┘
```

### SSE 断线重连机制

1. 每个事件实时写入 `tasks/{id}/logs.jsonl`（持久化）  
2. SSE 端点先回放 `logs.jsonl` 全部历史，再订阅 live queue  
3. 浏览器刷新或重连后自动获取完整日志，无数据丢失

### 任务目录结构

```
tasks/
└── {uuid}/
    ├── input.pdf       # 原始上传文件
    ├── images/         # page_001.jpg, page_002.jpg, ...
    ├── output.md       # 转换结果
    └── logs.jsonl      # 每行一条日志 JSON（用于 SSE 回放）
```

### LangGraph 工具约定

工具函数用 `@tool` 装饰，返回值必须是字符串（JSON 或路径）：

| 工具 | 输入 | 输出 |
|------|------|------|
| `pdf_to_images` | pdf_path, output_dir, dpi | JSON 路径列表 |
| `analyze_image_page` | image_path, page_number | JSON PageAnalysis |
| `assemble_markdown` | pages_json, output_path | 输出文件路径字符串 |

### 错误处理约定

- 单页分析失败：`PageAnalysis.error` 非空，内容以 `> ⚠️` 占位，继续处理其余页
- 工具内部用 `logging`，禁止 `print`
- `_run_task` 捕获所有异常，写入 `task_error` 事件后关闭 SSE 流

## 关键依赖

| 包 | 用途 |
|----|------|
| `langgraph` | React Agent 框架 |
| `langchain-openai` | LLM 调用（GPT-4o vision） |
| `pymupdf` | PDF 渲染为图像 |
| `fastapi` + `uvicorn` | Web 服务 |
| `pydantic-settings` | 环境变量配置 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 必填 | — |
| `OPENAI_BASE_URL` | 自定义 API 端点 | OpenAI 官方 |
| `LLM_MODEL` | 视觉模型名称 | `gpt-4o` |
| `PDF_DPI` | PDF 渲染分辨率 | `150` |
| `TASKS_DIR` | 任务存储目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发 Agent 数 | `3` |
| `HOST` / `PORT` | Web 服务监听地址 | `0.0.0.0:8000` |


## 项目概述

pdf2md 是一个基于 **LangGraph React Agent** 的 PDF 转 Markdown 工具。Agent 编排三个工具完成转换：PDF→JPEG、LLM图像分析、Markdown组装。

## 项目结构

```
src/pdf2md/
├── agent.py          # LangGraph React Agent 入口（核心）
├── config.py         # 环境变量配置（Settings dataclass）
├── assembler.py      # Markdown 组装逻辑（独立于 Agent）
├── cli.py            # 命令行入口
└── tools/
    ├── pdf_to_image.py    # Tool 1: PDF 每页 → JPEG
    └── image_analyzer.py  # Tool 2: JPEG → JSON 结构化内容块
tests/
├── fixtures/         # 测试用 PDF 和参考 Markdown
├── test_pdf_to_image.py
├── test_image_analyzer.py
└── test_agent.py
docs/
├── requirements.md   # 完整需求文档
└── design.md         # 架构与设计文档
```

## 开发环境

```bash
# 安装依赖
pip install -e ".[dev]"

# 配置环境变量（复制并填写 API Key）
cp .env.example .env
```

## 运行与测试命令

```bash
# 运行全部测试
pytest

# 运行单个测试文件
pytest tests/test_pdf_to_image.py

# 运行单个测试函数
pytest tests/test_pdf_to_image.py::test_converts_pdf_to_images

# 跳过需要真实 API Key 的端到端测试
pytest -m "not e2e"

# 命令行转换
python -m pdf2md convert input.pdf -o output.md
```

## 架构关键点

### LangGraph React Agent 模式

- `agent.py` 使用 `langgraph.prebuilt.create_react_agent` 构建 Agent
- Agent 以 `pdf_path` 和 `output_path` 为输入，自主决定工具调用顺序
- 工具函数用 `@tool` 装饰器定义，返回值均为字符串（JSON 或路径）

### 数据模型

内容块类型（`ContentBlock.type`）：
- `"text"` — 文字段落，保留标题层级
- `"table"` — Markdown 表格字符串
- `"image_description"` — 以 `> 📷` 开头的图片描述

### 工具调用约定

| 工具 | 输入 | 输出 |
|------|------|------|
| `pdf_to_images` | pdf_path, output_dir, dpi | JSON 字符串（路径列表） |
| `analyze_image_page` | image_path, page_number | JSON 字符串（PageAnalysis） |
| `assemble_markdown` | pages_json, output_path | 输出文件路径字符串 |

### 错误处理约定

- 单页分析失败时，`PageAnalysis.error` 字段非空，`content_blocks` 为空
- `assemble_markdown` 对 `error` 非空的页面输出 `> ⚠️ 第 N 页解析失败：{error}` 占位符
- 工具函数内部使用 `logging` 模块，不直接 `print`

## 关键依赖

| 包 | 用途 |
|----|------|
| `langgraph` | React Agent 框架 |
| `langchain-openai` | LLM 调用（GPT-4o vision） |
| `pymupdf` | PDF 渲染为图像 |
| `pydantic-settings` | 环境变量配置 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API Key（必填） | — |
| `OPENAI_BASE_URL` | 自定义 API 端点 | OpenAI 官方 |
| `LLM_MODEL` | 视觉模型名称 | `gpt-4o` |
| `PDF_DPI` | PDF 渲染分辨率 | `150` |
| `TEMP_DIR` | 临时图像目录 | `./tmp` |
