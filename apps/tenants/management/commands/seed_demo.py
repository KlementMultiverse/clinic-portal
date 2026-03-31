from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = (
        "Seed demo data: public tenant, superadmin, two clinics"
        " with staff, groups, workflows, tasks, and documents. Idempotent."
    )

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType

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
        admin_user.is_staff = True
        admin_user.save()

        # ------------------------------------------------------------------
        # 3. Demo clinic 1 — "Sunrise Clinic"
        # ------------------------------------------------------------------
        self.stdout.write("Creating Sunrise Clinic... ", ending="")
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

        admin_user.role = "admin"
        admin_user.save()

        # ------------------------------------------------------------------
        # 4. Demo clinic 2 — "Downtown Medical" (for isolation demo)
        # ------------------------------------------------------------------
        self.stdout.write("Creating Downtown Medical... ", ending="")
        clinic2_domain_name = "clinic2.localhost"
        if Domain.objects.filter(domain=clinic2_domain_name).exists():
            clinic2_domain = Domain.objects.get(domain=clinic2_domain_name)
            clinic2_tenant = clinic2_domain.tenant
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        else:
            from tenant_users.tenants.tasks import provision_tenant

            clinic2_tenant, clinic2_domain = provision_tenant(
                "Downtown Medical",
                "clinic2",
                admin_user,
            )
            self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 5. Staff users for Sunrise Clinic
        # ------------------------------------------------------------------
        self.stdout.write("Creating staff users... ", ending="")
        staff_data = [
            {
                "email": "staff1@clinic1.localhost",
                "password": "staff123",
                "name": "Alice Johnson",
                "role": "staff",
            },
            {
                "email": "staff2@clinic1.localhost",
                "password": "staff123",
                "name": "Bob Smith",
                "role": "staff",
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
                user.role = sd["role"]
                user.save()
            try:
                demo_tenant.add_user(user)
            except Exception:
                pass
            staff_users.append(user)
        staff1, staff2 = staff_users
        self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 6. Clinic Manager for Downtown Medical (different user)
        # ------------------------------------------------------------------
        self.stdout.write("Creating Downtown Medical manager... ", ending="")
        try:
            clinic2_manager = User.objects.get(email="manager@clinic2.localhost")
        except User.DoesNotExist:
            clinic2_manager = User.objects.create_user(
                email="manager@clinic2.localhost",
                password="manager123",
            )
            clinic2_manager.name = "Dr. Sarah Chen"
            clinic2_manager.role = "admin"
            clinic2_manager.save()
        try:
            clinic2_tenant.add_user(clinic2_manager)
        except Exception:
            pass
        self.stdout.write(self.style.SUCCESS("OK"))

        # Staff for Downtown Medical
        self.stdout.write("Creating Downtown Medical staff... ", ending="")
        try:
            clinic2_staff = User.objects.get(email="nurse@clinic2.localhost")
        except User.DoesNotExist:
            clinic2_staff = User.objects.create_user(
                email="nurse@clinic2.localhost",
                password="nurse123",
            )
            clinic2_staff.name = "Mike Rivera"
            clinic2_staff.role = "staff"
            clinic2_staff.save()
        try:
            clinic2_tenant.add_user(clinic2_staff)
        except Exception:
            pass
        self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 7. Groups & Permissions (shared — visible in admin)
        # ------------------------------------------------------------------
        self.stdout.write("Creating groups & permissions... ", ending="")

        # Clinic Managers group — can manage workflows + staff
        managers_group, _ = Group.objects.get_or_create(name="Clinic Managers")
        # Clinical Staff group — can view & update tasks
        clinical_staff_group, _ = Group.objects.get_or_create(name="Clinical Staff")
        # Viewers group — read-only access
        viewers_group, _ = Group.objects.get_or_create(name="Viewers")

        # Assign relevant permissions to groups
        try:
            workflow_ct = ContentType.objects.get(
                app_label="workflows", model="workflow"
            )
            task_ct = ContentType.objects.get(app_label="workflows", model="task")
            document_ct = ContentType.objects.get(
                app_label="documents", model="document"
            )

            # Managers: full CRUD on workflows, tasks, documents
            manager_perms = Permission.objects.filter(
                content_type__in=[workflow_ct, task_ct, document_ct]
            )
            managers_group.permissions.set(manager_perms)

            # Clinical Staff: view + change tasks and documents, view workflows
            staff_perms = Permission.objects.filter(
                content_type__in=[task_ct, document_ct],
                codename__in=[
                    "view_task",
                    "change_task",
                    "view_document",
                    "add_document",
                ],
            )
            staff_perms |= Permission.objects.filter(
                content_type=workflow_ct, codename="view_workflow"
            )
            clinical_staff_group.permissions.set(staff_perms)

            # Viewers: view-only on everything
            view_perms = Permission.objects.filter(
                content_type__in=[workflow_ct, task_ct, document_ct],
                codename__startswith="view_",
            )
            viewers_group.permissions.set(view_perms)
        except ContentType.DoesNotExist:
            self.stdout.write(
                self.style.WARNING("(content types not found — run migrations first)")
            )

        self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 8-10. Sunrise Clinic tenant-scoped data
        # ------------------------------------------------------------------
        with schema_context(demo_tenant.schema_name):
            # 8. Workflows
            self.stdout.write("Creating Sunrise Clinic workflows... ", ending="")
            workflow1, _ = Workflow.objects.get_or_create(
                name="Patient Intake",
                defaults={
                    "description": "Standard patient intake process for new patients",
                    "created_by": admin_user,
                },
            )
            workflow2, _ = Workflow.objects.get_or_create(
                name="Lab Results Review",
                defaults={
                    "description": "Process for reviewing and distributing lab results",
                    "created_by": admin_user,
                },
            )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 9. Tasks for Patient Intake
            self.stdout.write("Creating Sunrise Clinic tasks... ", ending="")
            tasks_data = [
                {
                    "title": "Verify insurance information",
                    "status": "completed",
                    "assigned_to": staff1,
                    "workflow": workflow1,
                },
                {
                    "title": "Collect patient history form",
                    "status": "in_progress",
                    "assigned_to": staff2,
                    "workflow": workflow1,
                },
                {
                    "title": "Schedule initial consultation",
                    "status": "assigned",
                    "assigned_to": staff1,
                    "workflow": workflow1,
                },
                {
                    "title": "Send welcome packet",
                    "status": "created",
                    "assigned_to": None,
                    "workflow": workflow1,
                },
                # Tasks for Lab Results Review
                {
                    "title": "Collect samples from lab",
                    "status": "in_progress",
                    "assigned_to": staff2,
                    "workflow": workflow2,
                },
                {
                    "title": "Review abnormal results",
                    "status": "created",
                    "assigned_to": None,
                    "workflow": workflow2,
                },
                {
                    "title": "Notify patients of results",
                    "status": "created",
                    "assigned_to": None,
                    "workflow": workflow2,
                },
            ]
            for td in tasks_data:
                task, created = Task.objects.get_or_create(
                    workflow=td["workflow"],
                    title=td["title"],
                    defaults={
                        "status": td["status"],
                        "assigned_to": td["assigned_to"],
                        "created_by": admin_user,
                    },
                )
                if created:
                    AuditLog.objects.create(
                        entity_type="task",
                        entity_id=task.id,
                        action="created",
                        details={"seeded": True, "initial_status": td["status"]},
                        performed_by=admin_user,
                    )
                    if td["status"] != "created":
                        AuditLog.objects.create(
                            entity_type="task",
                            entity_id=task.id,
                            action=f"status_change:created\u2192{td['status']}",
                            details={"seeded": True},
                            performed_by=admin_user,
                        )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 10. Documents
            self.stdout.write("Creating Sunrise Clinic documents... ", ending="")
            docs_data = [
                {
                    "name": "Intake Form Template.pdf",
                    "s3_key": f"{demo_tenant.schema_name}/sample-uuid/intake-form.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 45000,
                    "workflow": workflow1,
                },
                {
                    "name": "Lab Request Form.pdf",
                    "s3_key": f"{demo_tenant.schema_name}/sample-uuid2/lab-request.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 32000,
                    "workflow": workflow2,
                },
                {
                    "name": "Insurance Verification Checklist.docx",
                    "s3_key": (
                        f"{demo_tenant.schema_name}"
                        "/sample-uuid3/insurance-checklist.docx"
                    ),
                    "content_type": (
                        "application/vnd.openxmlformats"
                        "-officedocument.wordprocessingml.document"
                    ),
                    "size_bytes": 18500,
                    "workflow": workflow1,
                },
            ]
            for dd in docs_data:
                doc, created = Document.objects.get_or_create(
                    name=dd["name"],
                    defaults={
                        "s3_key": dd["s3_key"],
                        "content_type": dd["content_type"],
                        "size_bytes": dd["size_bytes"],
                        "uploaded_by": admin_user,
                        "workflow": dd["workflow"],
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

        # ------------------------------------------------------------------
        # 11-12. Downtown Medical tenant-scoped data
        # ------------------------------------------------------------------
        with schema_context(clinic2_tenant.schema_name):
            self.stdout.write("Creating Downtown Medical data... ", ending="")
            wf, _ = Workflow.objects.get_or_create(
                name="Referral Processing",
                defaults={
                    "description": "Handle incoming specialist referrals",
                    "created_by": clinic2_manager,
                },
            )
            tasks_c2 = [
                {
                    "title": "Review referral letter",
                    "status": "completed",
                    "assigned_to": clinic2_staff,
                },
                {
                    "title": "Check insurance coverage",
                    "status": "in_progress",
                    "assigned_to": clinic2_staff,
                },
                {
                    "title": "Schedule specialist appointment",
                    "status": "created",
                    "assigned_to": None,
                },
            ]
            for td in tasks_c2:
                task, created = Task.objects.get_or_create(
                    workflow=wf,
                    title=td["title"],
                    defaults={
                        "status": td["status"],
                        "assigned_to": td["assigned_to"],
                        "created_by": clinic2_manager,
                    },
                )
                if created:
                    AuditLog.objects.create(
                        entity_type="task",
                        entity_id=task.id,
                        action="created",
                        details={"seeded": True},
                        performed_by=clinic2_manager,
                    )
            doc, created = Document.objects.get_or_create(
                name="Referral Template.pdf",
                defaults={
                    "s3_key": (
                        f"{clinic2_tenant.schema_name}"
                        "/ref-uuid/referral-template.pdf"
                    ),
                    "content_type": "application/pdf",
                    "size_bytes": 28000,
                    "uploaded_by": clinic2_manager,
                    "workflow": wf,
                },
            )
            self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # Summary
        # ------------------------------------------------------------------
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully!"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write("")
        self.stdout.write("CLINICS:")
        self.stdout.write("  Sunrise Clinic  → http://clinic1.localhost:8000/")
        self.stdout.write("  Downtown Medical → http://clinic2.localhost:8000/")
        self.stdout.write("")
        self.stdout.write("ACCOUNTS:")
        self.stdout.write("  Superadmin:  admin@portal.localhost / admin123")
        self.stdout.write("  Manager:     manager@clinic2.localhost / manager123")
        self.stdout.write("  Staff:       staff1@clinic1.localhost / staff123")
        self.stdout.write("  Staff:       staff2@clinic1.localhost / staff123")
        self.stdout.write("  Nurse:       nurse@clinic2.localhost / nurse123")
        self.stdout.write("")
        self.stdout.write("GROUPS:")
        self.stdout.write("  Clinic Managers  → full CRUD on workflows, tasks, docs")
        self.stdout.write("  Clinical Staff   → view workflows, edit tasks & docs")
        self.stdout.write("  Viewers          → read-only access")
        self.stdout.write("")
        self.stdout.write("ADMIN PORTAL: http://portal.localhost:8000/admin/")
        self.stdout.write("  Login: admin@portal.localhost / admin123")
