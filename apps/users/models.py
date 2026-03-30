from django.conf import settings
from django.db import models
from tenant_users.tenants.models import UserProfile


class User(UserProfile):
    """
    Global user model. Extends UserProfile from django-tenant-users.
    Email is the username field (inherited from UserProfile).
    Role is per-tenant via TenantMembership, NOT on this model.
    """

    name = models.CharField(max_length=150)

    def __str__(self):
        return f"{self.name} ({self.email})"


class TenantMembership(models.Model):
    """
    Per-tenant role assignment. A user can be admin in one clinic and staff in another.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=[("admin", "Admin"), ("staff", "Staff")],
        default="staff",
    )

    class Meta:
        unique_together = ("user", "tenant")

    def __str__(self):
        return f"{self.user.name} → {self.tenant.name} ({self.role})"
