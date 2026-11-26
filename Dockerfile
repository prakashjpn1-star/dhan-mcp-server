FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY dhan_mcp_server ./dhan_mcp_server
RUN pip install --no-cache-dir . && useradd --system --uid 10001 app
USER 10001
ENV MCP_BIND_HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "-m", "dhan_mcp_server.deploy"]
