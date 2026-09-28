FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install dependencies first so the layer is cached between code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# The application runs straight from the source tree; no package build step.
COPY alembic.ini entrypoint.sh ./
COPY alembic ./alembic
COPY mockbank ./mockbank
COPY docs ./docs
RUN chmod +x /app/entrypoint.sh \
    && printf '#!/bin/sh\nexec python -m mockbank.cli "$@"\n' > /usr/local/bin/mockbank \
    && chmod +x /usr/local/bin/mockbank

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "mockbank.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
