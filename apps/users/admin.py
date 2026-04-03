from django.contrib import admin
from django.utils.html import format_html

from apps.users.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "name",
        "role_badge",
        "is_active",
        "must_reset_password",
        "tenant_list",
    ]
    list_filter = ["role", "is_active", "must_reset_password"]
    search_fields = ["email", "name"]
    ordering = ["email"]
    list_editable = ["is_active"]
    readonly_fields = ["last_login", "tenant_list"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal Info", {"fields": ("name",)}),
        ("Roles & Access", {"fields": ("role", "is_active", "must_reset_password")}),
        ("Info", {"fields": ("last_login", "tenant_list")}),
    )

    def role_badge(self, obj):
        colors = {"admin": "#28a745", "staff": "#007bff"}
        color = colors.get(obj.role, "#6c757d")
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; '
            'border-radius:3px; font-size:11px;">{}</span>',
            color,
            obj.role.upper(),
        )

    role_badge.short_description = "Role"

    def tenant_list(self, obj):
        """Show which clinics this user belongs to."""
        tenants = obj.tenants.exclude(schema_name="public")
        if not tenants:
            return "—"
        return ", ".join(t.name for t in tenants)

    tenant_list.short_description = "Clinics"

    def save_model(self, request, obj, form, change):
        """Hash password if it was changed via admin."""
        if "password" in form.changed_data:
            obj.set_password(form.cleaned_data["password"])
        super().save_model(request, obj, form, change)
