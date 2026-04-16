FROM python:3.12-slim

RUN adduser --uid 1000 --disabled-password --gecos "" appuser

WORKDIR /app
COPY app.py .

USER appuser

CMD ["python", "-u", "app.py"]
