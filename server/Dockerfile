FROM python:3.12-slim

RUN adduser --uid 1000 --disabled-password --gecos "" appuser

WORKDIR /app
COPY app.py .

USER appuser

HEALTHCHECK --interval=60s --timeout=3s --start-period=30s --retries=2 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',8080),2); s.close()" || exit 1

CMD ["python", "-u", "app.py"]
