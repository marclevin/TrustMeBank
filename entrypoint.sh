#!/bin/sh
# Wait for PostgreSQL, apply migrations, seed if requested, then run the given command.
set -e

echo "Waiting for the database..."
python - <<'PY'
import os, sys, time
from sqlalchemy import create_engine, text
url = os.environ.get("DATABASE_URL", "postgresql+psycopg://mockbank:mockbank@db:5432/mockbank")
engine = create_engine(url, pool_pre_ping=True)
for attempt in range(60):
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        time.sleep(1)
print("Database did not become ready in time", file=sys.stderr)
sys.exit(1)
PY

echo "Applying migrations..."
alembic upgrade head

if [ "${SEED_ON_STARTUP:-true}" = "true" ]; then
  echo "Seeding (idempotent)..."
  mockbank seed
fi

exec "$@"
