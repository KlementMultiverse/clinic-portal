from django.contrib import admin
from django_tenants.admin import TenantAdminMixin

from apps.tenants.models import Domain, Tenant


@admin.register(Tenant)
class TenantAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ["name", "schema_name", "created_at"]
    search_fields = ["name", "schema_name"]
    readonly_fields = ["schema_name", "created_at"]
    list_filter = ["created_at"]


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain"]
