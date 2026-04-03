from django.contrib import admin
from django.utils.html import format_html
from django_tenants.admin import TenantAdminMixin

from apps.tenants.models import Domain, Tenant


@admin.register(Tenant)
class TenantAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ["name", "schema_name", "domain_list", "user_count", "created_at"]
    search_fields = ["name", "schema_name"]
    readonly_fields = ["schema_name", "created_at", "domain_list", "user_count"]
    list_filter = ["created_at"]

    def domain_list(self, obj):
        domains = obj.domains.all()
        return ", ".join(d.domain for d in domains) if domains else "None"

    domain_list.short_description = "Domains"

    def user_count(self, obj):
        from apps.users.models import User

        count = User.objects.filter(tenants=obj, is_active=True).count()
        return format_html(
            '<span style="font-weight:bold;">{}</span>',
            count,
        )

    user_count.short_description = "Users"


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain"]
