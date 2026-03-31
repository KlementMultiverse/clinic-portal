from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = (
        "Seed demo data: public tenant, superadmin, demo clinic"
        " with staff, workflows, tasks, and a document. Idempotent."
    )

    def handle(self, *args, **options):
        from apps.documents.models import Document
        from apps.tenants.models import Domain, Tenant
        from apps.users.models import User
        from apps.workflows.models import AuditLog, Task, Workflow

        # ------------------------------------------------------------------
        # 1. Public tenant + superadmin user
        # ------------------------------------------------------------------
        admin_email = "admin@portal.localhost"

        self.stdout.write("Creating public tenant... ", ending="")
        if Tenant.objects.filter(schema_name="public").exists():
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        else:
            from tenant_users.tenants.utils import create_public_tenant

            create_public_tenant(
                domain_url="portal.localhost",
                owner_email=admin_email,
                is_superuser=True,
                is_staff=True,
                password="admin123",
            )
            self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 2. Superadmin user
        # ------------------------------------------------------------------
        self.stdout.write("Creating superadmin... ", ending="")
        try:
            admin_user = User.objects.get(email=admin_email)
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        except User.DoesNotExist:
            admin_user = User.objects.create_superuser(
                email=admin_email,
                password="admin123",
            )
            self.stdout.write(self.style.SUCCESS("OK"))
        # Ensure superadmin has correct name and role
        admin_user.name = "Portal Admin"
        admin_user.role = "admin"
        admin_user.save()

        # ------------------------------------------------------------------
        # 3. Demo clinic tenant — "Sunrise Clinic"
        # ------------------------------------------------------------------
        self.stdout.write("Creating demo clinic... ", ending="")
        demo_domain_name = "clinic1.localhost"
        if Domain.objects.filter(domain=demo_domain_name).exists():
            demo_domain = Domain.objects.get(domain=demo_domain_name)
            demo_tenant = demo_domain.tenant
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        else:
            from tenant_users.tenants.tasks import provision_tenant

            demo_tenant, demo_domain = provision_tenant(
                "Sunrise Clinic",
                "clinic1",
                admin_user,
            )
            self.stdout.write(self.style.SUCCESS("OK"))

        # Ensure owner has admin role within the tenant
        admin_user.role = "admin"
        admin_user.save()

        # ------------------------------------------------------------------
        # 4. Staff users
        # ------------------------------------------------------------------
        self.stdout.write("Creating staff users... ", ending="")
        staff_data = [
            {
                "email": "staff1@clinic1.localhost",
                "password": "staff123",
                "name": "Alice Johnson",
            },
            {
                "email": "staff2@clinic1.localhost",
                "password": "staff123",
                "name": "Bob Smith",
            },
        ]
        staff_users = []
        for sd in staff_data:
            try:
                user = User.objects.get(email=sd["email"])
            except User.DoesNotExist:
                user = User.objects.create_user(
                    email=sd["email"],
                    password=sd["password"],
                )
                user.name = sd["name"]
                user.role = "staff"
                user.save()
            # Add user to tenant (idempotent — add_user raises if already added)
            try:
                demo_tenant.add_user(user)
            except Exception:
                pass  # already added
            staff_users.append(user)
        staff1, staff2 = staff_users
        self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 5–7. Tenant-scoped data: workflow, tasks, document
        # ------------------------------------------------------------------
        # Switch to the demo tenant schema for creating tenant-scoped objects
        with schema_context(demo_tenant.schema_name):
            # 5. Sample workflow
            self.stdout.write("Creating sample workflow... ", ending="")
            workflow, _ = Workflow.objects.get_or_create(
                name="Patient Intake",
                defaults={
                    "description": "Standard patient intake process for new patients",
                    "created_by": admin_user,
                },
            )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 6. Sample tasks
            self.stdout.write("Creating sample tasks... ", ending="")
            tasks_data = [
                {
                    "title": "Verify insurance information",
                    "status": "completed",
                    "assigned_to": staff1,
                },
                {
                    "title": "Collect patient history form",
                    "status": "in_progress",
                    "assigned_to": staff2,
                },
                {
                    "title": "Schedule initial consultation",
                    "status": "assigned",
                    "assigned_to": staff1,
                },
                {
                    "title": "Send welcome packet",
                    "status": "created",
                    "assigned_to": None,
                },
            ]
            for td in tasks_data:
                task, created = Task.objects.get_or_create(
                    workflow=workflow,
                    title=td["title"],
                    defaults={
                        "status": td["status"],
                        "assigned_to": td["assigned_to"],
                        "created_by": admin_user,
                    },
                )
                if created:
                    # Create AuditLog entry for seed data (Rule #12)
                    AuditLog.objects.create(
                        entity_type="task",
                        entity_id=task.id,
                        action="created",
                        details={"seeded": True, "initial_status": td["status"]},
                        performed_by=admin_user,
                    )
                    # If status is not "created", log the transition too
                    if td["status"] != "created":
                        AuditLog.objects.create(
                            entity_type="task",
                            entity_id=task.id,
                            action=f"status_change:created\u2192{td['status']}",
                            details={"seeded": True},
                            performed_by=admin_user,
                        )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 7. Sample document
            self.stdout.write("Creating sample document... ", ending="")
            doc, created = Document.objects.get_or_create(
                name="Intake Form Template.pdf",
                defaults={
                    "s3_key": f"{demo_tenant.schema_name}/sample-uuid/intake-form.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 45000,
                    "uploaded_by": admin_user,
                },
            )
            if created:
                AuditLog.objects.create(
                    entity_type="document",
                    entity_id=doc.id,
                    action="created",
                    details={"seeded": True},
                    performed_by=admin_user,
                )
            self.stdout.write(self.style.SUCCESS("OK"))

        self.stdout.write(self.style.SUCCESS("\nDemo data seeded successfully!"))
