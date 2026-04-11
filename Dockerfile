FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Copy application code
COPY src/ src/
COPY main.py ./

# Install production dependencies + project into the venv (no dev deps, no re-sync at runtime)
RUN uv sync --frozen --no-dev

# Create data directory for SQLite
RUN mkdir -p /data

ENV DATABASE_URL=sqlite:////data/tracker.db
ENV ENVIRONMENT=production

EXPOSE 8000

# Use the venv binary directly — avoids uv re-syncing (and re-downloading) on every container start
CMD [".venv/bin/uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
