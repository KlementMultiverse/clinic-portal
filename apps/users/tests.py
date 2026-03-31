from django.test import TestCase

from apps.users.models import User


class UserModelTest(TestCase):
    """Tests for the User model (shared app — uses standard TestCase)."""

    def test_user_has_name_field(self):
        """User model should have a name field."""
        field = User._meta.get_field("name")
        self.assertEqual(field.max_length, 150)
        self.assertTrue(field.blank)
        self.assertEqual(field.default, "")

    def test_user_has_role_field(self):
        """User model should have a role field with admin/staff choices."""
        field = User._meta.get_field("role")
        choices = dict(field.choices)
        self.assertIn("admin", choices)
        self.assertIn("staff", choices)
        self.assertEqual(choices["admin"], "Admin")
        self.assertEqual(choices["staff"], "Staff")

    def test_user_default_role_is_staff(self):
        """User default role should be 'staff'."""
        field = User._meta.get_field("role")
        self.assertEqual(field.default, "staff")

    def test_user_has_must_reset_password_field(self):
        """User model should have a must_reset_password boolean field."""
        field = User._meta.get_field("must_reset_password")
        self.assertFalse(field.default)

    def test_user_role_max_length(self):
        """User role field should have max_length of 20."""
        field = User._meta.get_field("role")
        self.assertEqual(field.max_length, 20)
