FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project

COPY lg_tv.py ./

RUN uv run pyinstaller \
    --onefile \
    --name lg-tv-control \
    lg_tv.py
