#!/bin/bash
set -e

echo "Running shared schema migrations..."
uv run python manage.py migrate_schemas --shared

echo "Running tenant schema migrations..."
uv run python manage.py migrate_schemas --tenant

echo "Seeding demo data..."
uv run python manage.py seed_demo

echo "Collecting static files..."
uv run python manage.py collectstatic --noinput

echo "Starting gunicorn..."
exec uv run gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
