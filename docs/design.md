# 设计文档：PDF to Markdown 转换工具

## 1. 系统架构

```
用户输入 (PDF路径)
        │
        ▼
┌──────────────────────────────────────────┐
│          LangGraph React Agent           │
│                                          │
│  ┌─────────┐  ┌──────────────────────┐  │
│  │  State  │  │   Tool Executor      │  │
│  │  Graph  │◄─┤  - pdf_to_images     │  │
│  └─────────┘  │  - analyze_image_page│  │
│               │  - assemble_markdown │  │
│               └──────────────────────┘  │
└──────────────────────────────────────────┘
        │
        ▼
   Markdown 文件输出
```

## 2. 模块结构

```
src/pdf2md/
├── __init__.py
├── agent.py              # LangGraph React Agent 入口
├── assembler.py          # Markdown 组装逻辑
├── cli.py                # 命令行入口
├── config.py             # 环境变量配置
└── tools/
    ├── __init__.py
    ├── pdf_to_image.py   # Tool 1: PDF → JPEG
    └── image_analyzer.py # Tool 2: JPEG → 结构化内容
```

## 3. 数据流

### 3.1 整体数据流

```
PDF文件
  │
  │ [Tool: pdf_to_images]
  ▼
[page_001.jpg, page_002.jpg, ...]
  │
  │ [Tool: analyze_image_page] × N页
  ▼
[PageAnalysis(page=1, blocks=[...]),
 PageAnalysis(page=2, blocks=[...]), ...]
  │
  │ [Tool: assemble_markdown]
  ▼
output.md
```

### 3.2 数据模型

```python
class ContentBlock(BaseModel):
    type: Literal["text", "table", "image_description"]
    content: str

class PageAnalysis(BaseModel):
    page_number: int
    content_blocks: list[ContentBlock]
    error: str | None = None
```

## 4. LangGraph Agent 设计

### 4.1 使用 `create_react_agent`

采用 LangGraph prebuilt 的 `create_react_agent`，它实现标准的 ReAct (Reasoning + Acting) 循环：

```
[用户消息] → [LLM推理] → [工具调用] → [观察结果] → [LLM推理] → ... → [最终答案]
```

### 4.2 Agent 系统提示词

```
你是一个 PDF 转 Markdown 的专业助手。
给定一个 PDF 文件路径，你需要：
1. 调用 pdf_to_images 将 PDF 转为 JPEG 图像列表
2. 对每个图像依次调用 analyze_image_page 提取结构化内容
3. 收集所有页面的分析结果后，调用 assemble_markdown 生成最终文件
4. 返回输出 Markdown 文件的路径
```

### 4.3 Agent 状态图

```
START
  │
  ▼
[agent node]  ←─────────────────┐
  │                              │
  ├─ 有工具调用? ──Yes──► [tools node]
  │                              │
  └─ No ──────────────► END ─────┘
```

## 5. 工具实现细节

### 5.1 Tool 1: `pdf_to_images`

- 使用 **PyMuPDF** (`fitz`) 渲染 PDF
- 渲染矩阵：`fitz.Matrix(dpi/72, dpi/72)`
- 颜色空间：RGB
- 输出格式：JPEG，质量 85

```python
@tool
def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> str:
    """将 PDF 每页转换为 JPEG 文件，返回图像路径列表的 JSON 字符串"""
```

### 5.2 Tool 2: `analyze_image_page`

LLM 提示词结构：
```
你是专业的文档分析助手。分析这张图片，按阅读顺序提取所有内容：
- 文字段落：保留标题层级（使用 #/##/### 等）
- 表格：转换为 Markdown 表格（|列1|列2|...|）
- 图片/图表/公式：输出一段描述，以 > 📷 开头

返回 JSON 格式：
{
  "page_number": <页码>,
  "content_blocks": [
    {"type": "text"|"table"|"image_description", "content": "..."},
    ...
  ]
}
```

- 图片通过 base64 编码后作为 `image_url` 传入 LLM
- 使用 `langchain_core.messages.HumanMessage` 的多模态格式

### 5.3 Tool 3: `assemble_markdown`

- 按 `page_number` 升序排列页面
- 各页内容块按顺序拼接，块间加空行
- 相邻页之间插入：`\n\n---\n\n`
- 写入文件，返回文件路径

## 6. 错误处理策略

| 场景 | 处理方式 |
|------|----------|
| PDF 文件不存在 | 直接抛出 `FileNotFoundError` |
| PDF 页面渲染失败 | 跳过该页，记录警告日志 |
| LLM 调用超时 | 重试最多 2 次，仍失败则输出占位符 |
| LLM 返回非法 JSON | 将原始文本作为 `text` 块兜底处理 |
| 输出目录不存在 | 自动创建 |

## 7. 配置管理

通过 `config.py` 统一读取环境变量：

```python
class Settings(BaseSettings):
    llm_model: str = "gpt-4o"
    openai_api_key: str
    openai_base_url: str | None = None
    pdf_dpi: int = 150
    temp_dir: str = "./tmp"
    max_retries: int = 2
    page_timeout: int = 60
```

## 8. 测试策略

| 测试类型 | 覆盖范围 | 工具 |
|----------|----------|------|
| 单元测试 | 每个 Tool 函数 | pytest + pytest-mock |
| 集成测试 | Agent 完整流程（mock LLM） | pytest + langgraph |
| 端到端测试 | 真实 PDF 转换 | pytest（需 API Key）|

测试 PDF 样本放置在 `tests/fixtures/` 目录。
