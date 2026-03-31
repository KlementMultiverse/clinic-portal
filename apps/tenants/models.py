from django.db import models
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase


class Tenant(TenantBase):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    auto_create_schema = True

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    def __str__(self):
        return self.domain
