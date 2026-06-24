from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main():
    parser = argparse.ArgumentParser(
        prog="pdf2md",
        description="PDF 转 Markdown 工具（LangGraph React Agent）",
    )
    subparsers = parser.add_subparsers(dest="command")

    # convert 子命令（命令行直接转换）
    convert_parser = subparsers.add_parser("convert", help="命令行转换单个 PDF 文件")
    convert_parser.add_argument("pdf_path", help="输入 PDF 文件路径")
    convert_parser.add_argument("-o", "--output", required=True, help="输出 Markdown 文件路径")
    convert_parser.add_argument("--temp-dir", default="./tmp", help="临时图像目录（默认 ./tmp）")

    # serve 子命令（启动 Web 服务）
    serve_parser = subparsers.add_parser("serve", help="启动 Web 服务（上传、查看、历史）")
    serve_parser.add_argument("--host", default=None, help="监听地址（默认读取 HOST 环境变量，或 0.0.0.0）")
    serve_parser.add_argument("--port", type=int, default=None, help="监听端口（默认读取 PORT 环境变量，或 8000）")
    serve_parser.add_argument("--reload", action="store_true", help="开发模式热重载")

    args = parser.parse_args()

    if args.command == "convert":
        from pdf2md.agent import convert_pdf_to_markdown
        try:
            output = convert_pdf_to_markdown(args.pdf_path, args.output, args.temp_dir)
            print(f"✅ 转换成功：{output}")
        except FileNotFoundError as e:
            print(f"❌ 文件不存在：{e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ 转换失败：{e}", file=sys.stderr)
            sys.exit(1)

    elif args.command == "serve":
        import uvicorn
        from pdf2md.config import settings
        from pdf2md.web.app import app

        host = args.host or settings.host
        port = args.port or settings.port
        print(f"🚀 pdf2md Web 服务启动：http://{host}:{port}")
        uvicorn.run(app, host=host, port=port, reload=args.reload)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
