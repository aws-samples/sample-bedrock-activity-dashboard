#!/bin/bash
set -e

echo "=== Running Alembic migrations ==="
python -m alembic upgrade head
echo "=== Migrations complete ==="

echo "=== Starting uvicorn ==="
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 2 \
  --timeout-keep-alive 300
