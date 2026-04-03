import os

from django.core.management.base import BaseCommand

from apps.tenants.models import Domain, Tenant


class Command(BaseCommand):
    help = "Create the public tenant and superadmin user (idempotent)"

    def handle(self, *args, **options):
        # Check if public tenant already exists
        if Tenant.objects.filter(schema_name="public").exists():
            self.stdout.write(
                self.style.WARNING("Public tenant already exists. Skipping.")
            )
            return

        # Try using tenant_users utility first
        try:
            from tenant_users.tenants.utils import create_public_tenant

            domain = os.environ.get("PUBLIC_DOMAIN", "portal.localhost")
            admin_email = os.environ.get("ADMIN_EMAIL", "admin@clinic-portal.local")

            create_public_tenant(domain_url=domain, owner_email=admin_email)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Public tenant created via tenant_users utility. "
                    f"Domain: {domain}, Admin: {admin_email}"
                )
            )
        except (ImportError, Exception) as e:
            self.stdout.write(
                self.style.WARNING(
                    f"tenant_users utility unavailable ({e}), creating manually..."
                )
            )
            self._create_manually()

    def _create_manually(self):
        """Fallback: manually create public tenant, domain, and superadmin."""
        from apps.users.models import User

        # Create the public tenant
        tenant = Tenant(
            schema_name="public",
            name="Public",
        )
        tenant.save()

        # Create the primary domain
        domain_url = os.environ.get("PUBLIC_DOMAIN", "portal.localhost")
        Domain.objects.create(
            domain=domain_url,
            tenant=tenant,
            is_primary=True,
        )

        # Create superadmin user
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@clinic-portal.local")
        if not User.objects.filter(email=admin_email).exists():
            User.objects.create_superuser(
                email=admin_email,
                password=os.environ.get("ADMIN_PASSWORD", "changeme123"),
                name="Admin",
                role="admin",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Public tenant created manually. "
                    f"Domain: {domain_url}, Admin: {admin_email}"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Public tenant created. Admin user already exists.")
            )
