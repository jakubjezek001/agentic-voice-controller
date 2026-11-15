# Voice Coach worker: analyzes recordings that appear in /data/inbox.

# --- build stage: compilers are needed where no prebuilt wheel exists
#     (praat-parselmouth has none for Linux ARM64, e.g. Docker on Apple Silicon)
FROM python:3.12-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# --- runtime stage
FROM python:3.12-slim-bookworm

COPY --from=build /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    VOICECOACH_DATA=/data \
    HF_HOME=/data/cache/hf

WORKDIR /data
VOLUME ["/data"]
CMD ["voicecoach", "watch"]
