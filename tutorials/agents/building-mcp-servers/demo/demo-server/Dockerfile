FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests first so Docker can cache this layer
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ ./src/

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Default environment — all overridable at runtime
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    ROOT_DIR=/data \
    MAX_FILE_BYTES=524288

EXPOSE 8000

# Use uv run so the venv is activated correctly
CMD ["uv", "run", "python", "src/file_browser_mcp/server.py"]
