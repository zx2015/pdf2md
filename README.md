# pdf2md

将 PDF 文件转换为 Markdown 的命令行工具，基于 **LangGraph React Agent** 和 LLM 视觉能力实现。

## 功能

- 📄 PDF 每页渲染为 JPEG 图像
- 📝 LLM 提取文字，保留标题层级结构
- 📊 自动识别表格并转换为 Markdown 表格
- 🖼️ 图片/图表/公式转为文字描述，插入上下文合适位置
- 🤖 LangGraph React Agent 自主编排转换流程

## 快速开始

```bash
# 安装
pip install -e ".[dev]"

# 配置
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY

# 转换
python -m pdf2md convert your_document.pdf -o output.md
```

## 环境要求

- Python 3.11+
- 支持视觉输入的 LLM API（默认使用 GPT-4o）

## 文档

- [需求文档](docs/requirements.md)
- [设计文档](docs/design.md)

## 开发

```bash
pip install -e ".[dev]"
pytest                        # 运行全部测试
pytest -m "not e2e"           # 跳过需要 API Key 的测试
pytest tests/test_pdf_to_image.py::test_converts_pdf_to_images  # 单个测试
```
