# pdf2md

> 基于 **LangGraph React Agent** 和 LLM 视觉能力，将 PDF 文件智能转换为 Markdown 的工具。

提供 **Web 界面**和**命令行**两种使用方式。Web 界面实时展示 Agent 的思考过程和工具调用日志，支持任务历史管理。

---

## 功能特性

- 📄 **PDF 渲染** — 将每页 PDF 渲染为高清 JPEG 图像
- 🤖 **智能识别** — 使用支持视觉的 LLM（默认 GPT-4o）理解页面内容
- 📝 **文字提取** — 保留标题层级、段落结构
- 📊 **表格转换** — 自动识别表格，输出标准 Markdown 表格
- 📐 **图表处理** — 流程图、甘特图、思维导图、时序图等转为 Mermaid 代码块
- 🔢 **公式支持** — 数学公式转为 LaTeX（`$$...$$`）
- 🗂️ **双栏布局** — 自动识别左右双栏排版，按正确顺序组装
- 🌐 **实时日志** — Web 界面通过 SSE 实时推送 Agent 思考、工具调用全过程
- 📋 **任务历史** — 支持查看、重访、删除历史转换任务
- 🔗 **断线续看** — 刷新页面后自动回放历史日志，不丢失处理记录

---

## 界面预览

单页面三栏布局：

| 左栏 | 中栏 | 右侧边栏 |
|------|------|---------|
| PDF 页面缩略图 | 处理日志 / Markdown 预览 | 上传入口 + 任务历史 |

转换完成后可在中栏切换「日志」/「预览」标签，一键下载生成的 Markdown 文件。

---

## 快速开始

### 1. 安装依赖

```bash
git clone <repo-url>
cd pdf2md
pip install -e ".[dev]"
```

### 2. 配置 API Key

```bash
cp .env.example .env
```

编辑 `.env`，填入必要的配置：

```dotenv
OPENAI_API_KEY=sk-your-api-key-here
LLM_MODEL=gpt-4o
```

### 3. 启动 Web 服务

```bash
python -m pdf2md serve
```

浏览器打开 **http://localhost:8000**，拖拽或点击上传 PDF 文件即可开始转换。

### 4. 命令行转换（可选）

```bash
python -m pdf2md convert your_document.pdf -o output.md
```

---

## 安装要求

- Python **3.11+**
- 支持**视觉输入（Vision）** 的 LLM API，例如：
  - OpenAI GPT-4o / GPT-4o-mini
  - Azure OpenAI
  - 阿里云 Qwen-VL
  - SiliconFlow / 其他兼容 OpenAI 接口的服务

---

## 配置说明

所有配置项通过项目根目录的 `.env` 文件设置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | API Key（**必填**） | — |
| `LLM_MODEL` | 视觉模型名称 | `gpt-4o` |
| `OPENAI_BASE_URL` | 自定义 API 端点（第三方服务时填写） | OpenAI 官方 |
| `PDF_DPI` | PDF 渲染分辨率，越高越清晰但处理更慢 | `150` |
| `HOST` | Web 服务监听地址 | `0.0.0.0` |
| `PORT` | Web 服务监听端口 | `8000` |
| `TASKS_DIR` | 任务文件存储目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发转换任务数 | `3` |

### 使用第三方 LLM 服务示例

```dotenv
# 阿里云 Qwen-VL
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_API_KEY=sk-xxx
LLM_MODEL=qwen-vl-plus

# SiliconFlow
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_API_KEY=sk-xxx
LLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct

# 本地 Ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
LLM_MODEL=llava
```

---

## 技术架构

```
浏览器上传 PDF
    │ POST /api/tasks
    ▼
FastAPI — 创建任务目录，写入 SQLite
    │ 后台协程
    ▼
LangGraph React Agent
    │
    ├─ pdf_to_images      将 PDF 每页渲染为 JPEG
    ├─ describe_image     调用 LLM 视觉理解单页图像（Agent 自行构造 prompt）
    ├─ read_file_lines    读取已写入的 Markdown 末尾若干行（滑动窗口）
    └─ write_file_lines   追加/覆盖写入 Markdown 文件
    │
    ▼
SSE 实时推送事件 → 浏览器 EventSource
每条事件同步写入 logs.jsonl（断线重连时回放）
```

Agent 采用**滑动窗口**组装策略：每写入一页前先读取文件末尾 15 行，保持上下文连贯，同时避免超出模型 context window。

---

## 任务目录结构

每次转换任务对应一个独立目录：

```
tasks/
└── {uuid}/
    ├── input.pdf       # 上传的原始 PDF
    ├── images/         # page_001.jpg, page_002.jpg, ...
    ├── output.md       # 转换生成的 Markdown
    └── logs.jsonl      # 处理事件日志（每行一条 JSON，用于 SSE 回放）
```

---

## 开发指南

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行全部测试
pytest

# 跳过需要真实 API Key 的端到端测试
pytest -m "not e2e"

# 运行单个测试文件
pytest tests/test_image_analyzer.py

# 开发模式启动（热重载）
python -m pdf2md serve --reload --port 8000
```

### 项目结构

```
src/pdf2md/
├── agent.py            # LangGraph React Agent（核心入口）
├── config.py           # 环境变量配置
├── task_manager.py     # 任务 CRUD + SQLite + 目录管理
├── streaming.py        # asyncio pub/sub（SSE 事件总线）
├── cli.py              # 命令行入口
├── tools/
│   ├── pdf_to_image.py     # Tool: PDF → JPEG
│   ├── image_analyzer.py   # Tool: describe_image（LLM 视觉理解）
│   └── file_tools.py       # Tool: read_file_lines / write_file_lines
└── web/
    ├── app.py              # FastAPI 路由
    └── static/
        └── index.html      # 单页面 Web 应用
tests/
docs/
├── requirements.md     # 详细需求文档
└── design.md           # 架构设计文档
```

---

## License

MIT

