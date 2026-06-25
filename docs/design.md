# 设计文档：PDF to Markdown 转换工具

## 1. 系统架构

### 1.1 总体架构

```
浏览器上传 PDF
      │ POST /api/tasks
      ▼
FastAPI (web/app.py)
  - 创建任务目录结构
  - 写入 SQLite (task_manager.py)
  - 启动后台协程 _run_task()
      │
      ▼
astream_conversion() [agent.py]
      │
      ├─ Step 1: pdf_to_images()
      │     PyMuPDF 渲染每页为 JPEG
      │
      └─ Step 2: for page_N in pages (Python 串行循环)
              │
              └─ _process_one_page()
                    全新 LangGraph React Agent（每页独立实例）
                    工具：describe_image, read_file_lines, write_file_lines
                    recursion_limit=20
                    失败重试最多 3 次
                    3 次后 → raise PageProcessingError(page_num, ...)
      │
      ▼
event dict → task_manager.append_log() → logs.jsonl
                    │
              streaming.publish() → asyncio.Queue
                                          │
                              SSE /api/tasks/{id}/stream
                           ◄──────────────────────────┘
浏览器 EventSource
```

### 1.2 Per-page Agent 设计原理

**问题**：单个 Agent 处理整本 PDF 时，随页数增加，消息历史（thinking + tool calls + results）会超出 LLM 的 context window，导致 Agent 提前宣布完成或推理错误。

**解决方案**：Python 外层 `for` 循环串行驱动，每页创建全新 Agent 实例，每个 Agent 的 context 仅包含：
- 系统提示词（处理规则）
- 当前页的用户消息（图片路径、上页路径、输出文件路径）
- 当页的工具调用历史（最多 ~8 步）

这样无论 PDF 有多少页，每页的 context 大小固定可控。

## 2. 模块结构

```
src/pdf2md/
├── agent.py           # 核心：PageProcessingError, _build_page_agent(),
│                      #       _process_one_page(), astream_conversion()
├── config.py          # Settings dataclass（pydantic-settings）
├── task_manager.py    # 任务 CRUD + SQLite + resume_from_page
├── streaming.py       # asyncio pub/sub 事件总线
├── cli.py             # 命令行入口
├── assembler.py       # Markdown 组装工具（assemble_markdown，遗留）
└── tools/
    ├── pdf_to_image.py   # pdf_to_images：PyMuPDF 渲染 PDF
    ├── image_analyzer.py # describe_image：LLM 视觉识别（含 tenacity 重试）
    ├── file_tools.py     # read_file_lines / write_file_lines
    └── page_analyzer.py  # analyze_all_pages（遗留，未使用）
```

## 3. 数据流

### 3.1 正常处理流程

```
input.pdf
  │ [pdf_to_images]
  ▼
images/page_001.jpg, page_002.jpg, ...
  │
  │ for each page_N:
  │   [read_file_lines(output.md, -15)]  ← 读末尾上下文
  │   [describe_image(page_N.jpg, prompt)]  ← LLM 识别
  │   [write_file_lines(output.md, content, "append")]
  ▼
output.md（逐页追加）
```

### 3.2 断点续传流程

```
任务失败 (page_N 重试 3 次)
  │
  ▼
PageProcessingError(page_num=N)
  │
_run_task 捕获 → set_resume_page(task_id, N)
  │              update_status("failed", error=...)
  │              emit task_error {resume_from_page: N}
  ▼
前端显示"从第 N 页继续"按钮

  用户点击继续
  │ POST /api/tasks/{id}/resume
  ▼
update_status("pending")
_run_task(task_id)
  │ task.resume_from_page = N
  │ images 已存在 → 跳过 pdf_to_images
  ▼
astream_conversion(start_page=N)
  │ 跳过 page_1 .. page_{N-1}
  ▼
从 page_N 继续处理
```

## 4. 核心模块详解

### 4.1 agent.py

关键函数：

```python
class PageProcessingError(Exception):
    page_num: int       # 失败页码（1-indexed）
    total_pages: int    # 总页数
    cause: Exception    # 原始异常

def _build_page_agent() -> CompiledGraph:
    """每次调用创建全新 Agent 实例，工具：[describe_image, read_file_lines, write_file_lines]"""

async def _process_one_page(
    page_path, prev_page_path, output_path, page_num, total_pages
) -> AsyncGenerator[dict, None]:
    """单页处理，yield LangGraph 事件，失败时 raise"""

async def astream_conversion(
    pdf_path, output_path, images_dir,
    task_id=None, start_page=1
) -> AsyncGenerator[dict, None]:
    """外层循环：PDF转图像 + 逐页串行 + 3次重试 + PageProcessingError"""
```

### 4.2 tools/image_analyzer.py

`describe_image` 工具包含 tenacity 重试装饰器，处理：
- `httpx.RemoteProtocolError` — 连接中断（如 `incomplete chunked read`）
- `httpx.TimeoutException` — 请求超时
- `httpx.ConnectError` / `httpx.ReadError` — 网络错误
- Rate Limit（HTTP 429）— 额外等待 `rate_limit_wait` 秒

重试策略：指数退避（`retry_wait_min` → `retry_wait_max`），最多 `retry_attempts` 次。

工具失败时**不抛出异常**，返回 `"⚠️ 错误：..."` 字符串，由 Agent 写入占位符。

### 4.3 task_manager.py

SQLite 表 `tasks`：

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | TEXT PK | UUID |
| `filename` | TEXT | 原始文件名 |
| `status` | TEXT | pending / processing / completed / failed |
| `created_at` | TEXT | ISO 时间戳 |
| `updated_at` | TEXT | ISO 时间戳 |
| `page_count` | INTEGER | 总页数（完成后写入）|
| `error` | TEXT | 错误信息 |
| `resume_from_page` | INTEGER | 断点页码（1-indexed，可 NULL）|

### 4.4 web/app.py — API 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 上传 PDF，创建任务并开始处理 |
| GET | `/api/tasks` | 列出所有任务 |
| GET | `/api/tasks/{id}` | 获取单个任务详情 |
| DELETE | `/api/tasks/{id}` | 删除任务及文件 |
| POST | `/api/tasks/{id}/resume` | 从断点继续处理 |
| GET | `/api/tasks/{id}/stream` | SSE 实时日志流（含历史回放）|
| GET | `/api/tasks/{id}/output` | 获取 Markdown 内容 |
| GET | `/api/tasks/{id}/download` | 下载 Markdown 文件 |
| GET | `/api/tasks/{id}/images` | 列出页面图像文件名 |
| GET | `/api/tasks/{id}/images/{filename}` | 获取单张页面图像 |

## 5. 错误处理策略

| 场景 | 处理方式 |
|------|----------|
| PDF 文件不存在 | `FileNotFoundError` → `task_error` 事件 |
| LLM 调用超时/网络中断 | tenacity 重试（最多 `retry_attempts` 次）|
| Rate Limit (429) | 额外等待 `rate_limit_wait` 秒后重试 |
| 单页 Agent 全部重试失败 | `PageProcessingError` → 记录断点 → status=failed |
| 输出目录不存在 | 自动创建 |
| SSE 客户端断线 | `logs.jsonl` 回放保证数据不丢失 |

## 6. 配置管理

通过 `config.py` 统一读取环境变量（`pydantic-settings`）：

```python
class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_base_url: str | None = None
    llm_model: str = "gpt-4o"
    pdf_dpi: int = 150
    temp_dir: str = "./tmp"          # 命令行模式临时目录
    page_timeout: int = 120          # 单次 LLM 调用超时（秒）
    max_retries: int = 2             # httpx 连接重试次数
    retry_attempts: int = 4          # tenacity 业务层重试总次数
    retry_wait_min: int = 2          # 首次重试等待秒数
    retry_wait_max: int = 60         # 最大等待秒数
    rate_limit_wait: int = 15        # 429 时额外等待秒数
    host: str = "0.0.0.0"
    port: int = 8000
    tasks_dir: str = "./tasks"
    max_concurrent_tasks: int = 3
```

## 7. 前端设计

单页面应用（SPA），三栏布局：

- **左栏**：PDF 页面缩略图，点击可打开 Lightbox 全屏查看
- **中栏**：日志视图 / Markdown 预览（Tab 切换）
- **右侧边栏**：上传入口 + 任务历史列表

技术依赖（本地 vendor，无 CDN）：
- **Bootstrap Icons** — 图标
- **marked.js** — Markdown → HTML 渲染
- **KaTeX** — 数学公式渲染（`$$...$$` 块级 + `$...$` 行内）

## 8. 测试策略

| 测试类型 | 覆盖范围 | 标记 |
|----------|----------|------|
| 单元测试 | 每个 Tool 函数 | 默认运行 |
| 集成测试 | per-page Agent 事件流、断点续传、start_page | 默认运行 |
| 端到端测试 | 真实 PDF 转换（需 API Key）| `@pytest.mark.e2e` |
