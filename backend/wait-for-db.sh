#!/bin/sh

echo "Waiting for Postgres..."

while ! nc -z db 5432; do
  sleep 1
done

echo "Postgres started"

alembic upgrade head

# --reload-exclude keeps the watcher off paths that generate a lot of churn
# but never contain code it needs to reload on: var/uploads is a mounted
# volume (docker-compose.yml) that fills with uploaded documents, and
# __pycache__ writes on every import. Watching them added enough file
# descriptors to the rust-notify watcher to reproduce a live
# "Cannot allocate memory (os error 12)" crash under Docker Desktop's
# default WSL2 memory allocation.
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
  --reload-exclude "var/uploads/*" \
  --reload-exclude "**/__pycache__/*"