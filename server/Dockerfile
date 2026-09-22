FROM python:3.12-slim

RUN adduser --uid 1000 --disabled-password --gecos "" appuser

WORKDIR /app
COPY app.py test_health.py ./

USER appuser

CMD ["python", "-u", "app.py"]
