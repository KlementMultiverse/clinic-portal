import json

from django.test import TestCase, override_settings

from apps.tenants.models import Domain, Tenant
from apps.users.tests import _ensure_public_tenant


class TenantModelTest(TestCase):
    """Tests for the Tenant model (shared app -- uses standard TestCase)."""

    def test_tenant_has_required_fields(self):
        """Tenant model should have name, created_at, and schema_name."""
        field_names = [f.name for f in Tenant._meta.get_fields()]
        self.assertIn("name", field_names)
        self.assertIn("created_at", field_names)
        self.assertIn("schema_name", field_names)

    def test_tenant_auto_create_schema_is_true(self):
        """Tenant.auto_create_schema should be True by default."""
        self.assertTrue(Tenant.auto_create_schema)

    def test_tenant_name_max_length(self):
        """Tenant name field should have max_length of 100."""
        name_field = Tenant._meta.get_field("name")
        self.assertEqual(name_field.max_length, 100)


class DomainModelTest(TestCase):
    """Tests for the Domain model (shared app -- uses standard TestCase)."""

    def test_domain_model_exists(self):
        """Domain model should be importable and have expected fields."""
        field_names = [f.name for f in Domain._meta.get_fields()]
        self.assertIn("domain", field_names)
        self.assertIn("is_primary", field_names)
        self.assertIn("tenant", field_names)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class TenantEndpointTest(TestCase):
    """Tests for tenant API endpoints (shared app -- uses standard TestCase)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_public_tenant()

    def _register_and_login(
        self,
        email="owner@example.com",
        password="testpass123",
        name="Owner",
    ):
        self.client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": email,
                    "password": password,
                    "name": name,
                }
            ),
            content_type="application/json",
        )
        self.client.post(
            "/api/auth/login",
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )

    def test_create_tenant_works_for_authenticated_user(self):
        """POST /api/tenants/ creates a tenant for authenticated user."""
        self._register_and_login()
        resp = self.client.post(
            "/api/tenants/",
            data=json.dumps({"name": "Test Clinic", "subdomain": "testclinic"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["name"], "Test Clinic")
        self.assertIn("testclinic", data["schema_name"])

    def test_create_tenant_requires_auth(self):
        """POST /api/tenants/ returns 401 when not authenticated."""
        resp = self.client.post(
            "/api/tenants/",
            data=json.dumps({"name": "Test Clinic", "subdomain": "nope"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_create_duplicate_subdomain_returns_409(self):
        """POST /api/tenants/ with existing subdomain returns 409."""
        self._register_and_login(email="owner2@example.com")
        self.client.post(
            "/api/tenants/",
            data=json.dumps({"name": "Clinic A", "subdomain": "dupclinic"}),
            content_type="application/json",
        )
        resp = self.client.post(
            "/api/tenants/",
            data=json.dumps({"name": "Clinic B", "subdomain": "dupclinic"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 409)

    def test_list_tenants_requires_superuser(self):
        """GET /api/tenants/ returns 403 for non-superuser."""
        self._register_and_login(email="regular@example.com")
        resp = self.client.get("/api/tenants/")
        self.assertEqual(resp.status_code, 403)
