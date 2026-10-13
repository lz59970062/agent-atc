# Build from the repository root:
#   docker build -f examples/docker/Dockerfile -t atc:0.7.0 .
# BASE_IMAGE overrides must provide Python 3.9+ and Debian/Ubuntu apt tooling.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends screen ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/atc-source
COPY pyproject.toml ./
COPY src/ ./src/
RUN python -m pip install --no-cache-dir . \
    && rm -rf ./src ./build ./pyproject.toml

RUN useradd --create-home --uid 1000 --shell /bin/bash atc \
    && mkdir -p /state/atc /state/codex /state/claude /workspace \
    && chown -R atc:atc /state /workspace

ENV PYTHONUNBUFFERED=1 \
    ATC_HOME=/state/atc \
    CODEX_HOME=/state/codex \
    CLAUDE_CONFIG_DIR=/state/claude \
    ATC_EXECUTION_BACKEND=screen \
    ATC_DESKTOP_APP_SERVER=0

# Install the selected agent CLI and workload dependencies in a derived image.
# Agent sessions and authentication belong in runtime mounts, never image layers.
USER atc
WORKDIR /workspace

# Run with docker --init. A detached setup command must not be the container CMD.
CMD ["atc", "daemon"]
