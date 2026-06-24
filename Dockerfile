FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制配置文件并安装依赖
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 安装当前项目及其依赖
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple .

# 任务输出目录
RUN mkdir -p /app/tasks /app/tmp

# 默认端口
EXPOSE 8000

# 启动 Web 服务
CMD ["pdf2md", "serve", "--host", "0.0.0.0", "--port", "8000"]
