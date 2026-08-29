# pdf2md

> 基于 **LangGraph React Agent** 和 LLM 视觉能力，将 PDF 文件智能转换为 Markdown 的工具。

提供 **Web 界面**和**命令行**两种使用方式。Web 界面实时展示 Agent 的思考过程和工具调用日志，支持任务历史管理。

---

## 功能特性

- 📄 **PDF 渲染** — 将每页 PDF 渲染为高清 JPEG 图像
- 🤖 **智能识别** — 使用支持视觉的 LLM 理解页面内容
- 📝 **文字提取** — 保留标题层级、段落结构
- 📊 **表格转换** — 自动识别表格，输出标准 Markdown 表格
- 📐 **图表处理** — 流程图、甘特图、思维导图、时序图等转为 Mermaid 代码块
- 🔢 **公式支持** — 数学公式转为 LaTeX（`$$...$$`），Web 界面使用 KaTeX 实时渲染
- 🗂️ **双栏布局** — 自动识别左右双栏排版，按正确顺序组装
- 🌐 **实时日志** — Web 界面通过 SSE 实时推送 Agent 思考、工具调用全过程
- 📋 **任务历史** — 支持查看、重访、删除历史转换任务
- 🔗 **断线续看** — 刷新页面后自动回放历史日志，不丢失处理记录
- ⚡ **断点续传** — 单页连续失败 3 次后记录断点，可从中断页一键继续处理
- 🔄 **自动重试** — 网络超时、连接中断、Rate Limit 自动退避重试

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

编辑 `.env`，填入两个 Provider 的必要配置（详见下方[双 Provider 架构](#双-provider-架构)）：

```dotenv
# Provider 1：视觉 LLM（固定为 SiliconFlow）
SILICONFLOW_API_KEY=sk-your-siliconflow-api-key-here
VISION_CHAT_MODEL=Qwen/Qwen3.5-4B

# Provider 2：编排 Agent（可指向任意 OpenAI 兼容服务）
OPENAI_API_KEY=sk-your-orchestrator-api-key-here
ORCHESTRATOR_MODEL=gpt-4o-mini
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
- **视觉 Provider**：固定为 [SiliconFlow](https://cloud.siliconflow.cn)（OpenAI 兼容接口），需一个 SiliconFlow API Key，且模型需支持图像输入（image_url）
- **编排 Agent Provider**：任意支持 Tool Calling 的 OpenAI 兼容服务（OpenAI 官方 / Azure / SiliconFlow / 自建 LiteLLM、Ollama 网关等）

---

## 双 Provider 架构

pdf2md 使用**两个相互独立的 LLM Provider**：

| Provider | 用途 | 配置方式 | 端点 |
|---|---|---|---|
| **视觉**（SiliconFlow） | `describe_image` 实际识别页面图像内容 | 由 `.env` 的 `VISION_CHAT_MODEL` 固定配置 | 固定为 `https://api.siliconflow.cn/v1` |
| **编排 Agent** | 驱动 LangGraph ReAct Agent 决定调用哪个工具（`read_file_lines`/`describe_image`/`write_file_lines`）及顺序，需具备 Tool Calling 能力 | 由 `.env` 的 `ORCHESTRATOR_MODEL` 固定配置 | 任意 OpenAI 兼容端点（`OPENAI_BASE_URL`，留空则为 OpenAI 官方） |

两者拆分的原因：并非所有支持视觉理解的模型都同时支持 Tool Calling（决定 ReAct Agent 是否能正常调用工具），因此编排 Agent 固定使用一个支持 Tool Calling 的独立模型，与实际负责图像识别的视觉模型分开配置、互不影响。

---

## 视觉模型容量自动探测

不同视觉模型的 context window / 最大输出 token 数差异很大，`VISION_MAX_TOKENS` 若配置不当（过大会被 API 拒绝、过小则浪费模型能力，配置缺失则有陷入重复输出循环无限生成的风险）。`pdf2md/model_registry.py` 提供分层兜底的自动探测：

1. **`.env` 显式配置**（`VISION_MAX_TOKENS`）—— 优先级最高，设置后固定生效，不再自动探测。
2. **项目内置精确匹配表** —— 已在本项目中实测验证过的模型（如 `Qwen/Qwen3.5-4B`）。
3. **可选依赖 `litellm` 的社区维护注册表** —— `pip install pdf2md[model-registry]` 安装后自动启用，覆盖数百个主流模型；未安装时自动跳过此层。
4. **项目内置前缀模糊匹配** —— 覆盖同系列但未逐一登记的型号（如 `Qwen/Qwen3-VL-*` 系列）。
5. **保守安全默认值** —— 均未命中时使用，并记录 WARNING 日志提示手动配置。

---

## 配置说明

所有配置项通过项目根目录的 `.env` 文件设置：

### Provider 1：视觉 LLM（SiliconFlow，必填）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SILICONFLOW_API_KEY` | SiliconFlow API Key（**必填**） | — |
| `SILICONFLOW_BASE_URL` | SiliconFlow API 端点，一般无需修改 | `https://api.siliconflow.cn/v1` |
| `VISION_CHAT_MODEL` | `describe_image` 固定使用的视觉模型 | `Qwen/Qwen3.5-4B` |

> 该模型固定通过 `.env` 配置，不支持在 Web 界面或命令行按任务切换；修改后需重启服务生效。

### Provider 2：编排 Agent（可自定义，必填）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | 编排 Agent Provider 的 API Key（**必填**） | — |
| `OPENAI_BASE_URL` | 编排 Agent Provider 的 API 端点 | OpenAI 官方 |
| `ORCHESTRATOR_MODEL` | 编排 Agent 使用的模型，需支持 Tool Calling | `gpt-4o-mini` |

### 其他配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PDF_DPI` | PDF 渲染分辨率，越高越清晰但处理更慢 | `150` |
| `HOST` | Web 服务监听地址 | `0.0.0.0` |
| `PORT` | Web 服务监听端口 | `8000` |
| `TASKS_DIR` | 任务文件存储目录 | `./tasks` |
| `MAX_CONCURRENT_TASKS` | 最大并发转换任务数 | `3` |
| `PAGE_TIMEOUT` | 单次视觉 LLM 调用超时（秒，为空闲超时非总耗时） | `120` |
| `VISION_MAX_TOKENS` | 视觉模型单次输出最大 token 数（硬性上限）；留空则按模型自动探测 | 自动探测 |
| `RETRY_ATTEMPTS` | 视觉 LLM 调用重试总次数（含首次） | `4` |
| `RETRY_WAIT_MIN` | 首次重试最小等待秒数 | `2` |
| `RETRY_WAIT_MAX` | 最大退避等待秒数 | `60` |
| `RATE_LIMIT_WAIT` | 触发 Rate Limit（429）时额外等待秒数 | `15` |

### 编排 Agent Provider 自定义示例

```dotenv
# 使用 OpenAI 官方
OPENAI_API_KEY=sk-xxx
ORCHESTRATOR_MODEL=gpt-4o-mini

# 使用自建 LiteLLM 网关
OPENAI_BASE_URL=http://localhost:4000/v1
OPENAI_API_KEY=sk-xxx
ORCHESTRATOR_MODEL=qwen-plus

# 也指向 SiliconFlow（与视觉 Provider 使用不同的 Key/模型均可）
OPENAI_BASE_URL=https://api.siliconflow.cn/v1
OPENAI_API_KEY=sk-xxx
ORCHESTRATOR_MODEL=Qwen/Qwen2.5-72B-Instruct
```

---

## 技术架构

```
浏览器上传 PDF
    │ POST /api/tasks
    ▼
FastAPI — 创建任务目录，写入 SQLite
    │ 后台协程 _run_task()
    ▼
astream_conversion(pdf, output, images_dir, start_page)
    │
    ├─ Step 1: pdf_to_images — 将 PDF 所有页渲染为 JPEG
    │
    └─ Step 2: for each page（串行）
          │
          └─ 全新 LangGraph Agent（独立上下文，无溢出风险）
                ├─ read_file_lines    读取已写 Markdown 末尾 15 行（了解上下文）
                ├─ describe_image     调用视觉 LLM 识别当前页（含 tenacity 重试）
                └─ write_file_lines   追加到输出文件
          │
          失败重试 3 次 → PageProcessingError → 记录断点 → 可继续
    │
    ▼
SSE 实时推送事件 → 浏览器 EventSource
每条事件同步写入 logs.jsonl（断线重连时回放）
```

每页创建**独立 Agent 实例**，无跨页共享状态，彻底解决长文档上下文溢出问题。

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
│   ├── image_analyzer.py   # Tool: describe_image（视觉 LLM）
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

