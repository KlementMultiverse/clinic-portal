
from django.db import connection
from django.test import override_settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient

from apps.documents.models import Document
from apps.tenants.models import Tenant
from apps.users.models import User
from apps.workflows.models import Task, Workflow


def _ensure_public_tenant():
    """Create the public tenant if it does not already exist."""
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@dash-test.local",
    )


def _create_owner_user():
    """Create a user in the public schema to act as tenant owner."""
    _ensure_public_tenant()
    connection.set_schema_to_public()
    try:
        return User.objects.get(email="owner@dash-test.com")
    except User.DoesNotExist:
        return User.objects.create_user(
            email="owner@dash-test.com",
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
class DashboardStatsTest(TenantTestCase):
    """Tests for GET /api/dashboard/stats endpoint.

    Uses TenantTestCase per CLAUDE.md testing rules (dashboard is a tenant app).
    """

    @classmethod
    def setup_tenant(cls, tenant):
        owner = _create_owner_user()
        tenant.owner = owner
        tenant.name = "Test Clinic Dashboard"

    @classmethod
    def get_test_schema_name(cls):
        return "test_dashboard"

    @classmethod
    def get_test_tenant_domain(cls):
        return "dashboard.tenant.test.com"

    def setUp(self):
        super().setUp()
        self.client = TenantClient(self.tenant)
        # Users must be created in public schema per tenant-users
        connection.set_schema_to_public()
        self.admin = User.objects.create_user(
            email="dash-admin@test.com",
            password="adminpass123",
        )
        self.admin.role = "admin"
        self.admin.name = "Admin User"
        self.admin.save()
        self.staff = User.objects.create_user(
            email="dash-staff@test.com",
            password="staffpass123",
        )
        self.staff.role = "staff"
        self.staff.name = "Staff User"
        self.staff.save()
        # Add users to tenant so TenantAccessMiddleware allows access
        self.tenant.add_user(self.admin)
        self.tenant.add_user(self.staff)
        connection.set_tenant(self.tenant)

    def test_stats_returns_correct_counts(self):
        """GET /api/dashboard/stats returns accurate aggregate counts."""
        self.client.force_login(self.admin)

        # Create workflows
        wf1 = Workflow.objects.create(name="WF1", created_by=self.admin)
        wf2 = Workflow.objects.create(name="WF2", created_by=self.admin)

        # Create tasks with various statuses
        Task.objects.create(
            workflow=wf1, title="Task A", status="created", created_by=self.admin
        )
        Task.objects.create(
            workflow=wf1, title="Task B", status="assigned", created_by=self.admin
        )
        Task.objects.create(
            workflow=wf2, title="Task C", status="assigned", created_by=self.admin
        )
        Task.objects.create(
            workflow=wf2, title="Task D", status="in_progress", created_by=self.admin
        )
        Task.objects.create(
            workflow=wf2, title="Task E", status="completed", created_by=self.admin
        )

        # Create documents
        Document.objects.create(
            name="Doc1",
            s3_key="test/doc1.pdf",
            content_type="application/pdf",
            size_bytes=1000,
            uploaded_by=self.admin,
        )
        Document.objects.create(
            name="Doc2",
            s3_key="test/doc2.pdf",
            content_type="application/pdf",
            size_bytes=2000,
            uploaded_by=self.admin,
        )

        resp = self.client.get("/api/dashboard/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["total_workflows"], 2)
        self.assertEqual(data["total_documents"], 2)
        # Staff count = tenant users (admin + staff + owner = 3)
        self.assertGreaterEqual(data["total_staff"], 2)
        self.assertEqual(data["tasks_by_status"]["created"], 1)
        self.assertEqual(data["tasks_by_status"]["assigned"], 2)
        self.assertEqual(data["tasks_by_status"]["in_progress"], 1)
        self.assertEqual(data["tasks_by_status"]["completed"], 1)

    def test_stats_empty_tenant(self):
        """GET /api/dashboard/stats on empty tenant returns zeroes."""
        self.client.force_login(self.staff)
        resp = self.client.get("/api/dashboard/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_workflows"], 0)
        self.assertEqual(data["total_documents"], 0)
        self.assertEqual(data["tasks_by_status"], {})

    def test_stats_requires_auth(self):
        """GET /api/dashboard/stats without auth returns 401."""
        resp = self.client.get("/api/dashboard/stats")
        self.assertEqual(resp.status_code, 401)
