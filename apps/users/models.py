from django.db import models
from tenant_users.tenants.models import UserProfile


class User(UserProfile):
    name = models.CharField(max_length=150, blank=True, default="")
    role = models.CharField(
        max_length=20,
        choices=[("admin", "Admin"), ("staff", "Staff")],
        default="staff",
    )
    must_reset_password = models.BooleanField(default=False)

    def __str__(self):
        return self.email
