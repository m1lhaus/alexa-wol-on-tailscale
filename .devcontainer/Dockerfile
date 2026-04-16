FROM python:3.12-slim

# Create non-root user with uid 1000 to match host user, avoiding root-owned files on bind mounts
RUN adduser --uid 1000 --disabled-password --gecos "" devuser

WORKDIR /workspace

# git is required for VS Code source control integration
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# debugpy for VS Code debugger, plus any dev convenience tools
RUN pip install --no-cache-dir debugpy

USER devuser
