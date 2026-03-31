import json

from django.db import connection
from django.test import override_settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from apps.tenants.models import Tenant
from apps.users.models import User
from apps.workflows.models import AuditLog, Task, Workflow


def _ensure_public_tenant():
    """Create the public tenant if it does not already exist."""
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@wf-test.local",
    )


def _create_owner_user():
    """Create a user in the public schema to act as tenant owner."""
    _ensure_public_tenant()
    connection.set_schema_to_public()
    try:
        return User.objects.get(email="owner@wf-test.com")
    except User.DoesNotExist:
        return User.objects.create_user(
            email="owner@wf-test.com",
            password="ownerpass123",
        )


CACHE_OVERRIDE = override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)


@CACHE_OVERRIDE
class WorkflowEndpointTest(TenantTestCase):
    """Tests for workflow CRUD endpoints (tenant app)."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Test Clinic"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        # Users must be created in public schema per tenant-users
        connection.set_schema_to_public()
        self.admin = User.objects.create_user(
            email="wf-admin@test.com",
            password="adminpass123",
        )
        self.admin.role = "admin"
        self.admin.name = "Admin User"
        self.admin.save()
        self.staff = User.objects.create_user(
            email="wf-staff@test.com",
            password="staffpass123",
        )
        self.staff.role = "staff"
        self.staff.name = "Staff User"
        self.staff.save()
        # Add users to tenant so TenantAccessMiddleware allows access
        self.tenant.add_user(self.admin)
        self.tenant.add_user(self.staff)
        connection.set_tenant(self.tenant)

    def _post_json(self, url, data):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def _put_json(self, url, data):
        return self.client.put(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    # -- Workflow tests --

    def test_create_workflow_admin_201(self):
        """POST /api/workflows/ as admin creates workflow + AuditLog."""
        self.client.force_login(self.admin)
        resp = self._post_json(
            "/api/workflows/",
            {"name": "Onboarding", "description": "New hire flow"},
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "Onboarding")
        self.assertEqual(data["created_by_id"], self.admin.id)
        log = AuditLog.objects.filter(entity_type="workflow", action="created").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.entity_id, data["id"])
        self.assertEqual(log.performed_by, self.admin)

    def test_create_workflow_staff_403(self):
        """POST /api/workflows/ as staff returns 403."""
        self.client.force_login(self.staff)
        resp = self._post_json("/api/workflows/", {"name": "Nope"})
        self.assertEqual(resp.status_code, 403)

    def test_list_workflows(self):
        """GET /api/workflows/ returns all workflows in tenant."""
        self.client.force_login(self.admin)
        self._post_json("/api/workflows/", {"name": "WF1"})
        self._post_json("/api/workflows/", {"name": "WF2"})
        self.client.force_login(self.staff)
        resp = self.client.get("/api/workflows/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 2)

    def test_get_workflow_detail_includes_tasks(self):
        """GET /api/workflows/{id} includes tasks list."""
        self.client.force_login(self.admin)
        resp = self._post_json("/api/workflows/", {"name": "Detail WF"})
        wf_id = resp.json()["id"]
        self._post_json(
            "/api/tasks/",
            {"workflow_id": wf_id, "title": "Task 1"},
        )
        self.client.force_login(self.staff)
        resp = self.client.get(f"/api/workflows/{wf_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["name"], "Detail WF")
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["title"], "Task 1")

    def test_update_workflow_admin_200(self):
        """PUT /api/workflows/{id} updates and creates AuditLog."""
        self.client.force_login(self.admin)
        resp = self._post_json("/api/workflows/", {"name": "Original"})
        wf_id = resp.json()["id"]
        resp = self._put_json(
            f"/api/workflows/{wf_id}",
            {"name": "Updated", "description": "New desc"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["name"], "Updated")
        log = AuditLog.objects.filter(entity_type="workflow", action="updated").first()
        self.assertIsNotNone(log)

    def test_delete_workflow_admin_200(self):
        """DELETE /api/workflows/{id} deletes and creates AuditLog."""
        self.client.force_login(self.admin)
        resp = self._post_json("/api/workflows/", {"name": "To Delete"})
        wf_id = resp.json()["id"]
        resp = self.client.delete(f"/api/workflows/{wf_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Workflow.objects.filter(pk=wf_id).exists())
        log = AuditLog.objects.filter(entity_type="workflow", action="deleted").first()
        self.assertIsNotNone(log)


@CACHE_OVERRIDE
class TaskEndpointTest(TenantTestCase):
    """Tests for task CRUD and transition endpoints (tenant app)."""

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Test Clinic Tasks"

    @classmethod
    def get_test_schema_name(cls):
        return "test_tasks"

    @classmethod
    def get_test_tenant_domain(cls):
        return "tasks.tenant.test.com"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        # Users must be created in public schema per tenant-users
        connection.set_schema_to_public()
        self.admin = User.objects.create_user(
            email="task-admin@test.com",
            password="adminpass123",
        )
        self.admin.role = "admin"
        self.admin.name = "Admin"
        self.admin.save()
        self.staff = User.objects.create_user(
            email="task-staff@test.com",
            password="staffpass123",
        )
        self.staff.role = "staff"
        self.staff.name = "Staff"
        self.staff.save()
        # Add users to tenant so TenantAccessMiddleware allows access
        self.tenant.add_user(self.admin)
        self.tenant.add_user(self.staff)
        connection.set_tenant(self.tenant)
        # Create a workflow for tasks
        self.client.force_login(self.admin)
        resp = self.client.post(
            "/api/workflows/",
            data=json.dumps({"name": "Test WF"}),
            content_type="application/json",
        )
        self.workflow_id = resp.json()["id"]

    def _post_json(self, url, data):
        return self.client.post(
            url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_create_task_201(self):
        """POST /api/tasks/ creates task and AuditLog entry."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "New Task",
                "description": "Do something",
            },
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["title"], "New Task")
        self.assertEqual(data["status"], "created")
        log = AuditLog.objects.filter(entity_type="task", action="created").first()
        self.assertIsNotNone(log)
        self.assertEqual(log.entity_id, data["id"])

    def test_valid_transition_created_to_assigned(self):
        """Transition created->assigned succeeds with AuditLog."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Transition Task",
            },
        )
        task_id = resp.json()["id"]
        resp = self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "assigned"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "assigned")
        log = AuditLog.objects.filter(
            entity_type="task",
            entity_id=task_id,
            action__startswith="status_change:",
        ).first()
        self.assertIsNotNone(log)
        self.assertIn("created", log.action)
        self.assertIn("assigned", log.action)

    def test_invalid_transition_created_to_completed_400(self):
        """Transition created->completed returns 400."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Bad Transition",
            },
        )
        task_id = resp.json()["id"]
        resp = self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "completed"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Cannot transition", resp.json()["message"])
        task = Task.objects.get(pk=task_id)
        self.assertEqual(task.status, "created")

    def test_transition_from_terminal_state_400(self):
        """Transition from completed state returns 400."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Terminal Task",
            },
        )
        task_id = resp.json()["id"]
        self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "assigned"},
        )
        self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "in_progress"},
        )
        self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "completed"},
        )
        resp = self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "cancelled"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Cannot transition", resp.json()["message"])

    def test_assign_task_200(self):
        """Assign task sets assigned_to and creates AuditLog."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Assign Me",
            },
        )
        task_id = resp.json()["id"]
        resp = self._post_json(
            f"/api/tasks/{task_id}/assign",
            {"user_id": self.staff.id},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["assigned_to_id"], self.staff.id)
        log = AuditLog.objects.filter(
            entity_type="task",
            entity_id=task_id,
            action__startswith="assigned_to:",
        ).first()
        self.assertIsNotNone(log)

    def test_filter_tasks_by_status(self):
        """Filter tasks by status query param."""
        self._post_json(
            "/api/tasks/",
            {"workflow_id": self.workflow_id, "title": "Task A"},
        )
        resp = self._post_json(
            "/api/tasks/",
            {"workflow_id": self.workflow_id, "title": "Task B"},
        )
        task_b_id = resp.json()["id"]
        self._post_json(
            f"/api/tasks/{task_b_id}/transition",
            {"new_status": "assigned"},
        )
        resp = self.client.get("/api/tasks/?status=created")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Task A")
        resp = self.client.get("/api/tasks/?status=assigned")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["title"], "Task B")

    def test_create_task_staff_403(self):
        """POST /api/tasks/ as staff returns 403."""
        self.client.force_login(self.staff)
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Nope",
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_transition_task(self):
        """Staff users can transition tasks."""
        resp = self._post_json(
            "/api/tasks/",
            {
                "workflow_id": self.workflow_id,
                "title": "Staff Transition",
            },
        )
        task_id = resp.json()["id"]
        self.client.force_login(self.staff)
        resp = self._post_json(
            f"/api/tasks/{task_id}/transition",
            {"new_status": "assigned"},
        )
        self.assertEqual(resp.status_code, 200)
