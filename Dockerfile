FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached between code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md alembic.ini entrypoint.sh ./
COPY alembic ./alembic
COPY mockbank ./mockbank
COPY docs ./docs
RUN pip install --no-deps . && chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "mockbank.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
