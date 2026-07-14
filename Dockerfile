# WorkBuddy Copilot — 中心服务端容器镜像
# 仅包含 Linux 服务端；不含 macOS/Windows 学员客户端
FROM python:3.12-slim

LABEL org.opencontainers.image.title="WorkBuddy Copilot Server"
LABEL org.opencontainers.image.description="Central analysis service for WorkBuddy Copilot"
LABEL org.opencontainers.image.source="https://github.com/minc-nice-100/workbuddy-copilot"

# 防止 .pyc 文件写入和 stdout/stderr 缓冲
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 安装系统依赖（仅保留运行时必需）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 仅安装服务端依赖（不含 macOS 客户端依赖）
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# 复制应用代码
COPY copilot/ copilot/

# 复制配置模板（运行时通过环境变量或挂载覆盖）
COPY config.example.json ./config.example.json

# 容器入口脚本
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 创建非 root 用户运行服务
RUN useradd --create-home --shell /bin/bash copilot \
    && mkdir -p /data \
    && chown -R copilot:copilot /app /data

USER copilot

# 服务端口
EXPOSE 8765

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]