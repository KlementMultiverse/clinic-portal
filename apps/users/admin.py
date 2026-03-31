from django.contrib import admin

from apps.users.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "email",
        "name",
        "role",
        "is_active",
        "must_reset_password",
        "tenant_list",
    ]
    list_filter = ["role", "is_active", "must_reset_password"]
    search_fields = ["email", "name"]
    ordering = ["email"]
    list_editable = ["role", "is_active"]
    readonly_fields = ["last_login", "tenant_list"]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal Info", {"fields": ("name",)}),
        ("Roles & Access", {"fields": ("role", "is_active", "must_reset_password")}),
        ("Info", {"fields": ("last_login", "tenant_list")}),
    )

    def tenant_list(self, obj):
        """Show which clinics this user belongs to."""
        tenants = obj.tenants.exclude(schema_name="public")
        return ", ".join(t.name for t in tenants) if tenants else "None"

    tenant_list.short_description = "Clinics"

    def save_model(self, request, obj, form, change):
        """Hash password if it was changed via admin."""
        if "password" in form.changed_data:
            obj.set_password(form.cleaned_data["password"])
        super().save_model(request, obj, form, change)
