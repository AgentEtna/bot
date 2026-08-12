# syntax=docker/dockerfile:1.9

# --- web frontend build stage (svelte 5 + adapter-static via bun) ---
FROM oven/bun:1-slim AS web-builder
WORKDIR /web
COPY web/package.json web/bun.lockb ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

# --- lexidraw-mcp build stage (stdio node MCP server phi draws with) ---
FROM node:22-slim AS lexidraw-builder
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone https://tangled.org/zzstoatzz.io/lexidraw-mcp /lexidraw \
    && git -C /lexidraw checkout c4774e97c6364f7c3718174a108e026b65e98138
WORKDIR /lexidraw
RUN npm ci && npm run build && npm prune --omit=dev

# --- python deps stage ---
FROM python:3.12-slim AS builder

# Install uv and git (needed for git+ dependencies)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Configure uv to use /app as the project environment
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12 \
    UV_PROJECT_ENVIRONMENT=/app

# hatch-vcs needs git — pretend a version instead
ENV SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0

WORKDIR /src

# Copy source for installation
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install dependencies and the application (non-editable)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# --- runtime stage ---
FROM python:3.12-slim

# node runtime for the lexidraw stdio MCP server; the copied binary needs
# libstdc++ which python-slim doesn't guarantee
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=node:22-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=lexidraw-builder /lexidraw/dist /opt/lexidraw/dist
COPY --from=lexidraw-builder /lexidraw/node_modules /opt/lexidraw/node_modules
COPY --from=lexidraw-builder /lexidraw/package.json /opt/lexidraw/package.json

COPY --from=builder /app /app

# Copy runtime data
COPY personalities/ /app/personalities/
COPY skills/ /app/skills/

# Copy built frontend
COPY --from=web-builder /web/build /app/web

ENV PATH="/app/bin:$PATH"
ENV PYTHONUNBUFFERED=1
WORKDIR /app

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "bot.main:app", "--host", "0.0.0.0", "--port", "8080"]
