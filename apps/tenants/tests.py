from django.test import TestCase

from apps.tenants.models import Domain, Tenant


class TenantModelTest(TestCase):
    """Tests for the Tenant model (shared app — uses standard TestCase)."""

    def test_tenant_has_required_fields(self):
        """Tenant model should have name, created_at, and schema_name fields."""
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
    """Tests for the Domain model (shared app — uses standard TestCase)."""

    def test_domain_model_exists(self):
        """Domain model should be importable and have expected fields."""
        field_names = [f.name for f in Domain._meta.get_fields()]
        self.assertIn("domain", field_names)
        self.assertIn("is_primary", field_names)
        self.assertIn("tenant", field_names)
