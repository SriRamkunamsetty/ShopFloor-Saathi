# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY saathi ./saathi
RUN pip install --prefix=/install ".[gcp]"

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SAATHI_ENV=production SAATHI_AUTH_MODE=firebase SAATHI_REPO_BACKEND=firestore \
    SAATHI_WEB_DIR=/app/web
RUN useradd --system --uid 10001 --no-create-home saathi
WORKDIR /app
COPY --from=build /install /usr/local
COPY web ./web
USER 10001
EXPOSE 8080
# Cloud Run sets PORT; default to 8080 locally. Shell form so the variable expands.
CMD exec uvicorn saathi.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'
