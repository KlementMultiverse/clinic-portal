import json

from django.test import TestCase, override_settings

from apps.tenants.models import Tenant
from apps.users.models import User


class UserModelTest(TestCase):
    """Tests for the User model (shared app -- uses standard TestCase)."""

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


def _ensure_public_tenant():
    """Create the public tenant if it does not already exist.

    Uses tenant_users create_public_tenant utility which handles the
    chicken-and-egg problem of needing an owner user before tenants exist.
    """
    if Tenant.objects.filter(schema_name="public").exists():
        return
    from tenant_users.tenants.utils import create_public_tenant

    create_public_tenant(
        domain_url="testserver",
        owner_email="system@clinic-portal.local",
    )


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class AuthEndpointTest(TestCase):
    """Tests for auth API endpoints (shared app -- uses standard TestCase)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_public_tenant()

    def _register_user(
        self,
        email="test@example.com",
        password="testpass123",
        name="Test User",
    ):
        return self.client.post(
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

    def _login_user(self, email="test@example.com", password="testpass123"):
        return self.client.post(
            "/api/auth/login",
            data=json.dumps({"email": email, "password": password}),
            content_type="application/json",
        )

    def test_register_creates_user(self):
        """POST /api/auth/register creates a new user and returns 201."""
        resp = self._register_user()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["email"], "test@example.com")
        self.assertEqual(data["name"], "Test User")
        self.assertTrue(User.objects.filter(email="test@example.com").exists())

    def test_register_duplicate_email_returns_409(self):
        """POST /api/auth/register with existing email returns 409."""
        self._register_user()
        resp = self._register_user()
        self.assertEqual(resp.status_code, 409)
        self.assertIn("already exists", resp.json()["message"])

    def test_login_valid_credentials_returns_200(self):
        """POST /api/auth/login with valid credentials returns 200."""
        self._register_user()
        resp = self._login_user()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], "test@example.com")
        self.assertEqual(data["name"], "Test User")

    def test_login_bad_password_returns_401(self):
        """POST /api/auth/login with wrong password returns 401."""
        self._register_user()
        resp = self._login_user(password="wrongpassword")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Invalid", resp.json()["message"])

    def test_logout_invalidates_session(self):
        """POST /api/auth/logout invalidates the session."""
        self._register_user()
        self._login_user()
        # Verify logged in via /me
        me_resp = self.client.get("/api/auth/me")
        self.assertEqual(me_resp.status_code, 200)
        # Logout
        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200)
        # Verify no longer logged in
        me_resp = self.client.get("/api/auth/me")
        self.assertEqual(me_resp.status_code, 401)

    def test_me_returns_user_info_when_authenticated(self):
        """GET /api/auth/me returns user info when authenticated."""
        self._register_user()
        self._login_user()
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["email"], "test@example.com")
        self.assertEqual(data["name"], "Test User")
        self.assertEqual(data["role"], "staff")

    def test_me_returns_401_when_not_authenticated(self):
        """GET /api/auth/me returns 401 when not authenticated."""
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_password_reset(self):
        """POST /api/auth/reset-password updates the password."""
        self._register_user()
        self._login_user()
        resp = self.client.post(
            "/api/auth/reset-password",
            data=json.dumps({"new_password": "newpass12345"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # Verify old password no longer works
        self.client.post("/api/auth/logout")
        resp = self._login_user(password="testpass123")
        self.assertEqual(resp.status_code, 401)
        # Verify new password works
        resp = self._login_user(password="newpass12345")
        self.assertEqual(resp.status_code, 200)

    def test_password_reset_too_short(self):
        """POST /api/auth/reset-password with short password returns 400."""
        self._register_user()
        self._login_user()
        resp = self.client.post(
            "/api/auth/reset-password",
            data=json.dumps({"new_password": "short"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class PasswordResetMiddlewareTest(TestCase):
    """Tests for the PasswordResetMiddleware."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _ensure_public_tenant()

    def test_must_reset_password_blocks_other_endpoints(self):
        """User with must_reset_password=True gets 403 on non-exempt paths."""
        self.client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "reset@example.com",
                    "password": "testpass123",
                    "name": "Reset User",
                }
            ),
            content_type="application/json",
        )
        self.client.post(
            "/api/auth/login",
            data=json.dumps(
                {
                    "email": "reset@example.com",
                    "password": "testpass123",
                }
            ),
            content_type="application/json",
        )
        # Set must_reset_password
        user = User.objects.get(email="reset@example.com")
        user.must_reset_password = True
        user.save(update_fields=["must_reset_password"])
        # Accessing /api/auth/me should be blocked
        resp = self.client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Password reset required", resp.json()["message"])

    def test_must_reset_password_allows_reset_endpoint(self):
        """User with must_reset_password=True can access reset-password."""
        self.client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "reset2@example.com",
                    "password": "testpass123",
                    "name": "Reset User 2",
                }
            ),
            content_type="application/json",
        )
        self.client.post(
            "/api/auth/login",
            data=json.dumps(
                {
                    "email": "reset2@example.com",
                    "password": "testpass123",
                }
            ),
            content_type="application/json",
        )
        user = User.objects.get(email="reset2@example.com")
        user.must_reset_password = True
        user.save(update_fields=["must_reset_password"])
        # Reset password endpoint should work
        resp = self.client.post(
            "/api/auth/reset-password",
            data=json.dumps({"new_password": "newpass12345"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # After reset, must_reset_password should be False
        user.refresh_from_db()
        self.assertFalse(user.must_reset_password)
