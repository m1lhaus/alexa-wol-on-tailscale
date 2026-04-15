FROM python:3.12-slim

WORKDIR /workspace

# git is required for VS Code source control integration
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# debugpy for VS Code debugger, plus any dev convenience tools
RUN pip install --no-cache-dir debugpy

# Run as root in dev so debugpy can bind freely and we can install packages
