# resume-tailor: FastAPI + UI + Tectonic for Fly.io (and local Docker).
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    RESUME_TAILOR_TECTONIC=/usr/local/bin/tectonic \
    XDG_CACHE_HOME=/data/cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Tectonic 0.17.0 (linux x86_64 musl) — self-contained LaTeX engine.
RUN curl -fsSL \
      "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz" \
      -o /tmp/tectonic.tar.gz \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic \
    && chmod +x /usr/local/bin/tectonic \
    && rm /tmp/tectonic.tar.gz \
    && tectonic --version

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY projects.yaml ./projects.yaml
COPY supabase ./supabase

RUN uv sync --frozen --no-dev \
    && mkdir -p /data/cache

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

# Fly mounts a volume at /data for the Tectonic package cache.
CMD ["uvicorn", "resume_tailor.web:create_app_for_server", "--factory", "--host", "0.0.0.0", "--port", "8080"]
