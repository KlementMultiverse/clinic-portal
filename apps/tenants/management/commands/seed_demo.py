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
        from apps.tenants.models import Tenant
        from apps.users.models import User
        from apps.workflows.models import AuditLog, Task, Workflow

        # ------------------------------------------------------------------
        # 1. Public tenant + superadmin user
        # ------------------------------------------------------------------
        admin_email = "admin@clinic-portal.com"

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
        # Check by tenant name OR domain (handles different TENANT_USERS_DOMAIN)
        demo_tenant = Tenant.objects.filter(name="Sunrise Clinic").first()
        if demo_tenant:
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        else:
            from tenant_users.tenants.tasks import provision_tenant

            try:
                demo_tenant, _ = provision_tenant(
                    "Sunrise Clinic",
                    "clinic1",
                    admin_user,
                )
                self.stdout.write(self.style.SUCCESS("OK"))
            except Exception as e:
                # Tenant might exist under different domain
                demo_tenant = Tenant.objects.filter(
                    schema_name__startswith="clinic1"
                ).first()
                if demo_tenant:
                    self.stdout.write(self.style.SUCCESS("found existing, OK"))
                else:
                    self.stdout.write(self.style.ERROR(f"FAILED: {e}"))
                    return

        admin_user.role = "admin"
        admin_user.save()

        # ------------------------------------------------------------------
        # 4. Demo clinic 2 — "Downtown Medical" (for isolation demo)
        # ------------------------------------------------------------------
        self.stdout.write("Creating Downtown Medical... ", ending="")
        clinic2_tenant = (
            Tenant.objects.filter(name="Downtown Medical").first()
            or Tenant.objects.filter(name="Valley Health Center").first()
        )
        if clinic2_tenant:
            self.stdout.write(self.style.SUCCESS("already exists, OK"))
        else:
            from tenant_users.tenants.tasks import provision_tenant

            try:
                clinic2_tenant, _ = provision_tenant(
                    "Downtown Medical",
                    "clinic2",
                    admin_user,
                )
                self.stdout.write(self.style.SUCCESS("OK"))
            except Exception as e:
                clinic2_tenant = Tenant.objects.filter(
                    schema_name__startswith="clinic2"
                ).first()
                if clinic2_tenant:
                    self.stdout.write(self.style.SUCCESS("found existing, OK"))
                else:
                    self.stdout.write(self.style.ERROR(f"FAILED: {e}"))
                    return

        # ------------------------------------------------------------------
        # 5. Staff users for Sunrise Clinic
        # ------------------------------------------------------------------
        self.stdout.write("Creating staff users... ", ending="")
        staff_data = [
            {
                "email": "alice@sunriseclinic.com",
                "password": "staff123",
                "name": "Alice Johnson",
                "role": "staff",
            },
            {
                "email": "bob@sunriseclinic.com",
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
            clinic2_manager = User.objects.get(email="sarah@downtownmed.com")
        except User.DoesNotExist:
            clinic2_manager = User.objects.create_user(
                email="sarah@downtownmed.com",
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
            clinic2_staff = User.objects.get(email="mike@downtownmed.com")
        except User.DoesNotExist:
            clinic2_staff = User.objects.create_user(
                email="mike@downtownmed.com",
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
            wf_intake, _ = Workflow.objects.get_or_create(
                name="Patient Intake",
                defaults={
                    "description": "Standard patient intake process for new patients including insurance verification, history collection, and initial consultation scheduling.",
                    "created_by": admin_user,
                },
            )
            wf_lab, _ = Workflow.objects.get_or_create(
                name="Lab Results Review",
                defaults={
                    "description": "Process for collecting, reviewing, and distributing lab results to patients and referring physicians.",
                    "created_by": admin_user,
                },
            )
            wf_discharge, _ = Workflow.objects.get_or_create(
                name="Discharge Planning",
                defaults={
                    "description": "Coordinate patient discharge including medication reconciliation, follow-up scheduling, and care instructions.",
                    "created_by": admin_user,
                },
            )
            wf_quality, _ = Workflow.objects.get_or_create(
                name="Quality Assurance Audit",
                defaults={
                    "description": "Monthly clinical quality audit covering documentation standards, compliance checks, and process improvements.",
                    "created_by": admin_user,
                },
            )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 9. Tasks — varied statuses across all workflows
            self.stdout.write("Creating Sunrise Clinic tasks... ", ending="")
            tasks_data = [
                # Patient Intake — mix of all statuses
                {"title": "Verify insurance information", "status": "completed", "assigned_to": staff1, "workflow": wf_intake},
                {"title": "Collect patient history form", "status": "completed", "assigned_to": staff2, "workflow": wf_intake},
                {"title": "Record current medications", "status": "in_progress", "assigned_to": staff1, "workflow": wf_intake},
                {"title": "Schedule initial consultation", "status": "in_progress", "assigned_to": staff2, "workflow": wf_intake},
                {"title": "Obtain signed consent forms", "status": "assigned", "assigned_to": staff1, "workflow": wf_intake},
                {"title": "Send welcome packet", "status": "created", "assigned_to": None, "workflow": wf_intake},
                {"title": "Set up patient portal access", "status": "created", "assigned_to": None, "workflow": wf_intake},
                # Lab Results Review
                {"title": "Collect blood samples from lab", "status": "completed", "assigned_to": staff2, "workflow": wf_lab},
                {"title": "Run CBC panel analysis", "status": "completed", "assigned_to": staff1, "workflow": wf_lab},
                {"title": "Review abnormal lipid results", "status": "in_progress", "assigned_to": staff2, "workflow": wf_lab},
                {"title": "Flag critical values for physician", "status": "assigned", "assigned_to": staff1, "workflow": wf_lab},
                {"title": "Notify patients of normal results", "status": "created", "assigned_to": None, "workflow": wf_lab},
                {"title": "File results in patient records", "status": "created", "assigned_to": None, "workflow": wf_lab},
                # Discharge Planning
                {"title": "Complete medication reconciliation", "status": "completed", "assigned_to": staff1, "workflow": wf_discharge},
                {"title": "Prepare discharge summary", "status": "in_progress", "assigned_to": staff2, "workflow": wf_discharge},
                {"title": "Schedule 2-week follow-up visit", "status": "assigned", "assigned_to": staff1, "workflow": wf_discharge},
                {"title": "Print care instructions for patient", "status": "created", "assigned_to": None, "workflow": wf_discharge},
                {"title": "Coordinate home health referral", "status": "created", "assigned_to": None, "workflow": wf_discharge},
                # Quality Assurance Audit
                {"title": "Review documentation completeness", "status": "in_progress", "assigned_to": staff1, "workflow": wf_quality},
                {"title": "Audit consent form compliance", "status": "assigned", "assigned_to": staff2, "workflow": wf_quality},
                {"title": "Check medication error reports", "status": "created", "assigned_to": None, "workflow": wf_quality},
                {"title": "Compile monthly quality metrics", "status": "created", "assigned_to": None, "workflow": wf_quality},
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
                        details={"title": td["title"], "workflow": td["workflow"].name},
                        performed_by=admin_user,
                    )
                    # Add realistic state transition audit trail
                    transitions = {
                        "assigned": [("created", "assigned")],
                        "in_progress": [("created", "assigned"), ("assigned", "in_progress")],
                        "completed": [("created", "assigned"), ("assigned", "in_progress"), ("in_progress", "completed")],
                    }
                    for from_s, to_s in transitions.get(td["status"], []):
                        AuditLog.objects.create(
                            entity_type="task",
                            entity_id=task.id,
                            action=f"status_change:{from_s}\u2192{to_s}",
                            details={"performed_by_name": (td["assigned_to"].name if td["assigned_to"] else "System")},
                            performed_by=td["assigned_to"] or admin_user,
                        )
            self.stdout.write(self.style.SUCCESS("OK"))

            # 10. Documents
            self.stdout.write("Creating Sunrise Clinic documents... ", ending="")
            schema = demo_tenant.schema_name
            docs_data = [
                {"name": "Patient Intake Form.pdf", "s3_key": f"{schema}/d1/intake-form.pdf", "content_type": "application/pdf", "size_bytes": 45200, "workflow": wf_intake, "summary": "Standard patient intake form covering demographics, insurance, emergency contacts, and medical history questionnaire."},
                {"name": "Insurance Verification Checklist.docx", "s3_key": f"{schema}/d2/insurance-checklist.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": 18500, "workflow": wf_intake, "summary": "Checklist for verifying patient insurance coverage including policy number validation, copay confirmation, and prior authorization requirements."},
                {"name": "Consent for Treatment.pdf", "s3_key": f"{schema}/d3/consent-treatment.pdf", "content_type": "application/pdf", "size_bytes": 32100, "workflow": wf_intake, "summary": "General consent form for medical examination and treatment. Includes HIPAA acknowledgment and privacy practices notice."},
                {"name": "Lab Request Form — CBC Panel.pdf", "s3_key": f"{schema}/d4/lab-request-cbc.pdf", "content_type": "application/pdf", "size_bytes": 28700, "workflow": wf_lab, "summary": "Complete blood count panel request form with physician orders, patient demographics, and specimen collection instructions."},
                {"name": "Lipid Panel Results — March 2026.pdf", "s3_key": f"{schema}/d5/lipid-results-mar.pdf", "content_type": "application/pdf", "size_bytes": 15800, "workflow": wf_lab, "summary": "Lipid panel results showing total cholesterol, LDL, HDL, and triglyceride levels. Two values flagged as outside normal range."},
                {"name": "Discharge Summary Template.docx", "s3_key": f"{schema}/d6/discharge-summary.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": 22400, "workflow": wf_discharge, "summary": "Standardized discharge summary template including diagnosis, treatment provided, medications prescribed, and follow-up instructions."},
                {"name": "Medication Reconciliation Form.pdf", "s3_key": f"{schema}/d7/med-reconciliation.pdf", "content_type": "application/pdf", "size_bytes": 19300, "workflow": wf_discharge, "summary": "Form for reconciling pre-admission medications with discharge medications. Highlights changes, additions, and discontinued medications."},
                {"name": "Quality Metrics Report — Q1 2026.pdf", "s3_key": f"{schema}/d8/quality-q1-2026.pdf", "content_type": "application/pdf", "size_bytes": 67500, "workflow": wf_quality, "summary": "Quarterly quality metrics report covering patient satisfaction scores, readmission rates, documentation compliance, and clinical outcome measures."},
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
                        "summary": dd.get("summary", ""),
                    },
                )
                if created:
                    AuditLog.objects.create(
                        entity_type="document",
                        entity_id=doc.id,
                        action="uploaded",
                        details={"filename": dd["name"], "size": dd["size_bytes"]},
                        performed_by=admin_user,
                    )
            self.stdout.write(self.style.SUCCESS("OK"))

        # ------------------------------------------------------------------
        # 11-12. Downtown Medical tenant-scoped data
        # ------------------------------------------------------------------
        with schema_context(clinic2_tenant.schema_name):
            self.stdout.write("Creating Downtown Medical workflows... ", ending="")
            wf_referral, _ = Workflow.objects.get_or_create(
                name="Referral Processing",
                defaults={
                    "description": "Handle incoming specialist referrals including insurance verification, scheduling, and follow-up coordination.",
                    "created_by": clinic2_manager,
                },
            )
            wf_screening, _ = Workflow.objects.get_or_create(
                name="Annual Health Screening",
                defaults={
                    "description": "Comprehensive annual health screening workflow covering physical exam, lab work, and preventive care recommendations.",
                    "created_by": clinic2_manager,
                },
            )
            wf_compliance, _ = Workflow.objects.get_or_create(
                name="Regulatory Compliance Review",
                defaults={
                    "description": "Periodic review of clinic operations for regulatory compliance including OSHA, infection control, and staff certifications.",
                    "created_by": clinic2_manager,
                },
            )
            self.stdout.write(self.style.SUCCESS("OK"))

            self.stdout.write("Creating Downtown Medical tasks... ", ending="")
            tasks_c2 = [
                # Referral Processing
                {"title": "Review referral letter from Dr. Patel", "status": "completed", "assigned_to": clinic2_staff, "workflow": wf_referral},
                {"title": "Verify specialist network coverage", "status": "completed", "assigned_to": clinic2_staff, "workflow": wf_referral},
                {"title": "Obtain prior authorization", "status": "in_progress", "assigned_to": clinic2_staff, "workflow": wf_referral},
                {"title": "Schedule cardiology consultation", "status": "assigned", "assigned_to": clinic2_staff, "workflow": wf_referral},
                {"title": "Send records to specialist office", "status": "created", "assigned_to": None, "workflow": wf_referral},
                # Annual Health Screening
                {"title": "Complete physical exam checklist", "status": "completed", "assigned_to": clinic2_staff, "workflow": wf_screening},
                {"title": "Order standard lab panel", "status": "in_progress", "assigned_to": clinic2_staff, "workflow": wf_screening},
                {"title": "Review immunization records", "status": "assigned", "assigned_to": clinic2_staff, "workflow": wf_screening},
                {"title": "Schedule mammogram/colonoscopy if due", "status": "created", "assigned_to": None, "workflow": wf_screening},
                {"title": "Generate preventive care summary", "status": "created", "assigned_to": None, "workflow": wf_screening},
                # Regulatory Compliance
                {"title": "Verify staff CPR certifications", "status": "in_progress", "assigned_to": clinic2_staff, "workflow": wf_compliance},
                {"title": "Inspect fire extinguisher logs", "status": "assigned", "assigned_to": clinic2_staff, "workflow": wf_compliance},
                {"title": "Audit sharps disposal procedures", "status": "created", "assigned_to": None, "workflow": wf_compliance},
                {"title": "Update infection control manual", "status": "created", "assigned_to": None, "workflow": wf_compliance},
            ]
            for td in tasks_c2:
                task, created = Task.objects.get_or_create(
                    workflow=td["workflow"],
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
                        details={"title": td["title"], "workflow": td["workflow"].name},
                        performed_by=clinic2_manager,
                    )
                    transitions = {
                        "assigned": [("created", "assigned")],
                        "in_progress": [("created", "assigned"), ("assigned", "in_progress")],
                        "completed": [("created", "assigned"), ("assigned", "in_progress"), ("in_progress", "completed")],
                    }
                    for from_s, to_s in transitions.get(td["status"], []):
                        AuditLog.objects.create(
                            entity_type="task",
                            entity_id=task.id,
                            action=f"status_change:{from_s}\u2192{to_s}",
                            details={"performed_by_name": (td["assigned_to"].name if td["assigned_to"] else "System")},
                            performed_by=td["assigned_to"] or clinic2_manager,
                        )
            self.stdout.write(self.style.SUCCESS("OK"))

            self.stdout.write("Creating Downtown Medical documents... ", ending="")
            schema2 = clinic2_tenant.schema_name
            docs_c2 = [
                {"name": "Referral Letter — Cardiology.pdf", "s3_key": f"{schema2}/r1/referral-cardiology.pdf", "content_type": "application/pdf", "size_bytes": 24300, "workflow": wf_referral, "summary": "Referral letter from Dr. Patel requesting cardiology evaluation for suspected atrial fibrillation. Includes relevant ECG findings and medication history."},
                {"name": "Prior Authorization Form.pdf", "s3_key": f"{schema2}/r2/prior-auth.pdf", "content_type": "application/pdf", "size_bytes": 18900, "workflow": wf_referral, "summary": "Insurance prior authorization request for specialist consultation. Contains diagnosis codes, clinical justification, and supporting documentation."},
                {"name": "Annual Screening Checklist.docx", "s3_key": f"{schema2}/r3/screening-checklist.docx", "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "size_bytes": 15200, "workflow": wf_screening, "summary": "Comprehensive annual health screening checklist covering vital signs, lab orders, cancer screenings, immunizations, and lifestyle counseling."},
                {"name": "OSHA Compliance Checklist 2026.pdf", "s3_key": f"{schema2}/r4/osha-compliance.pdf", "content_type": "application/pdf", "size_bytes": 41600, "workflow": wf_compliance, "summary": "OSHA workplace safety compliance checklist for medical facilities. Covers bloodborne pathogen exposure plan, PPE inventory, and hazard communication standards."},
                {"name": "Staff Certification Tracker.xlsx", "s3_key": f"{schema2}/r5/cert-tracker.xlsx", "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "size_bytes": 8900, "workflow": wf_compliance, "summary": "Spreadsheet tracking staff certifications including CPR, BLS, ACLS expiration dates and renewal status for all clinical personnel."},
            ]
            for dd in docs_c2:
                doc, created = Document.objects.get_or_create(
                    name=dd["name"],
                    defaults={
                        "s3_key": dd["s3_key"],
                        "content_type": dd["content_type"],
                        "size_bytes": dd["size_bytes"],
                        "uploaded_by": clinic2_manager,
                        "workflow": dd["workflow"],
                        "summary": dd.get("summary", ""),
                    },
                )
                if created:
                    AuditLog.objects.create(
                        entity_type="document",
                        entity_id=doc.id,
                        action="uploaded",
                        details={"filename": dd["name"], "size": dd["size_bytes"]},
                        performed_by=clinic2_manager,
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
        self.stdout.write("  Sunrise Clinic   → https://clinic1.klementgunndu.space/")
        self.stdout.write("  Downtown Medical → https://clinic2.klementgunndu.space/")
        self.stdout.write("")
        self.stdout.write("ACCOUNTS:")
        self.stdout.write("  Superadmin:  admin@clinic-portal.com / admin123")
        self.stdout.write("  Manager:     sarah@downtownmed.com / manager123")
        self.stdout.write("  Staff:       alice@sunriseclinic.com / staff123")
        self.stdout.write("  Staff:       bob@sunriseclinic.com / staff123")
        self.stdout.write("  Nurse:       mike@downtownmed.com / nurse123")
        self.stdout.write("")
        self.stdout.write("GROUPS:")
        self.stdout.write("  Clinic Managers  → full CRUD on workflows, tasks, docs")
        self.stdout.write("  Clinical Staff   → view workflows, edit tasks & docs")
        self.stdout.write("  Viewers          → read-only access")
        self.stdout.write("")
        self.stdout.write("ADMIN PORTAL: https://portal.klementgunndu.space/admin/")
        self.stdout.write("  Login: admin@clinic-portal.com / admin123")
