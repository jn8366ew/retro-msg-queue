FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# pycurl (celery[sqs], 2단계) 빌드 의존성. 지금 넣어 2단계 재빌드를 줄인다 (R15).
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcurl4-openssl-dev libssl-dev gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pyproject.toml .
COPY app/ ./app/
COPY experiments/ ./experiments/
COPY tests/ ./tests/
