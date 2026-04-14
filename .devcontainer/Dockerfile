FROM python:3.12-slim

WORKDIR /workspace

# debugpy for VS Code debugger, plus any dev convenience tools
RUN pip install --no-cache-dir debugpy

# Run as root in dev so debugpy can bind freely and we can install packages
