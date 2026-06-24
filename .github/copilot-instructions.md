# Copilot Instructions — pdf2md

## 架构概述

两层架构：**Web 服务层**（FastAPI + SSE）+ **Agent 处理层**（LangGraph React Agent，单阶段逐页处理）。

```
浏览器上传 PDF
    │ POST /api/tasks
    ▼
web/app.py → task_manager.py（SQLite + tasks/{uuid}/目录）
    │ BackgroundTask
    ▼
agent.astream_conversion(pdf, output, images_dir, task_id)
    │
    ├─ pdf_to_images → JPEG 列表
    │
    └─ 逐页「滑动窗口」处理：
        describe_image(page_N.jpg, prompt) → Markdown 文本
        read_file_lines(output.md, -15)   → 末尾上下文
        write_file_lines(output.md, content, append)
        Agent 自行构建 prompt，上下文窗口固定大小
```

**关键文件**：
- `web/app.py` — 所有 API 路由（上传/列表/详情/删除/SSE/下载）
- `task_manager.py` — 任务 CRUD（SQLite），`images_dir` 等路径属性
- `streaming.py` — asyncio pub/sub（`subscribe/publish/close`）
- `agent.py` — `astream_conversion()`，4 工具，Agent 自建 prompt
- `tools/image_analyzer.py` — `describe_image(image_path, prompt)` 工具
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
pytest tests/test_assembler.py::TestAssembleMarkdown::test_writes_markdown_file  # 单个测试
```

## Agent 工具集

| 工具 | 输入 | 输出 |
|------|------|------|
| `pdf_to_images` | pdf_path, output_dir, dpi | JSON 路径列表 |
| `describe_image` | image_path, prompt | 原始文本（LLM 直接响应） |
| `read_file_lines` | path, start_line, end_line | 文本内容（支持负数索引） |
| `write_file_lines` | path, content, mode | 写入结果描述 |

## describe_image prompt 构建规则

Agent 自行构建 prompt，应包含：
- **基础提取**：文字→Markdown、表格→Markdown 表格、公式→`$$..$$`、代码→代码块
- **图表类型**：流程图→Mermaid flowchart、时序图→Mermaid sequenceDiagram、甘特图→Markdown 表格、思维导图→嵌套列表、数据图表→趋势描述+数据表格、架构图→`> 📷 ` 描述
- **布局处理**：双栏时先读完左栏再读右栏
- **上下文（可选）**：从第 2 页起可在 prompt 中带入文档背景

## SSE 日志事件格式

```json
{"type": "task_start",     "message": "...",                    "timestamp": "..."}
{"type": "agent_thinking", "content": "...",                    "timestamp": "..."}
{"type": "tool_start",     "tool": "describe_image", "input": {...}, "timestamp": "..."}
{"type": "tool_end",       "tool": "describe_image", "output": "...", "timestamp": "..."}
{"type": "task_complete",  "output_path": "...", "page_count": 10,   "timestamp": "..."}
{"type": "task_error",     "error": "...",                       "timestamp": "..."}
```

## 关键约定

- 所有工具用 `@tool` 装饰，docstring 作为 LLM 工具描述（不要省略）
- 工具内部用 `logging`，禁止 `print`
- `describe_image` 失败时返回 `"⚠️ 错误：..."` 字符串，不抛异常
- Agent 遇到 `"⚠️ 错误："` 开头的返回时，写入 `> ⚠️ 第 N 页解析失败：...` 占位符
- mock LLM 时 patch `pdf2md.tools.image_analyzer._build_llm`

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 必填 | — |
| `LLM_MODEL` | 视觉模型（需支持 image_url） | `gpt-4o` |
| `OPENAI_BASE_URL` | 自定义 API 端点 | OpenAI 官方 |
| `PDF_DPI` | PDF 渲染分辨率 | `150` |
| `TASKS_DIR` | 任务存储根目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发 Web 任务数 | `3` |
| `HOST` / `PORT` | Web 服务监听 | `0.0.0.0:8000` |
