#!/bin/bash
set -e

echo "Running shared schema migrations..."
uv run python manage.py migrate_schemas --shared

echo "Running tenant schema migrations..."
uv run python manage.py migrate_schemas --tenant

echo "Seeding demo data..."
uv run python manage.py seed_demo

echo "Registering Railway domain..."
uv run python manage.py shell -c "
from apps.tenants.models import Tenant, Domain
if '$RAILWAY_PUBLIC_DOMAIN':
    pub = Tenant.objects.filter(schema_name='public').first()
    if pub:
        Domain.objects.get_or_create(
            domain='$RAILWAY_PUBLIC_DOMAIN',
            defaults={'tenant': pub, 'is_primary': False}
        )
        print(f'Railway domain registered: $RAILWAY_PUBLIC_DOMAIN')
"

echo "Collecting static files..."
uv run python manage.py collectstatic --noinput

echo "Starting gunicorn..."
exec uv run gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120
