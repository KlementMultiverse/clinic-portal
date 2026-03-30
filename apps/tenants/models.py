from django.db import models
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase


class Tenant(TenantBase):
    """
    Each clinic gets its own PostgreSQL schema.
    Extends TenantBase (django-tenant-users) which provides schema_name,
    tenant ownership, and user management methods (add_user, remove_user).
    """

    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    auto_create_schema = True

    def __str__(self):
        return f"{self.name} ({self.schema_name})"


class Domain(DomainMixin):
    """Maps a hostname to a Tenant. No extra fields needed."""

    pass
