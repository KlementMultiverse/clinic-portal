import secrets
from datetime import datetime
from typing import Optional

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest
from ninja import NinjaAPI, Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from apps.tenants.models import Tenant
from apps.users.models import User

# ---------------------------------------------------------------------------
# NinjaAPI instance — Per CLAUDE.md Rule #1, using Django Ninja for all API
# routes. CSRF enabled for session-based authentication.
# ---------------------------------------------------------------------------
api = NinjaAPI(urls_namespace="api")  # CSRF enforced via django_auth (SessionAuth.csrf=True)


# ---------------------------------------------------------------------------
# Schemas — Pydantic models for request/response validation.
# Per spec: EXPLICIT fields only, never expose sensitive fields.
# ---------------------------------------------------------------------------
class RegisterIn(Schema):
    email: str
    password: str
    name: str


class LoginIn(Schema):
    email: str
    password: str


class UserOut(Schema):
    id: int
    email: str
    name: str
    role: str


class UserWithTenantOut(Schema):
    id: int
    email: str
    name: str
    role: str
    tenant: Optional[str] = None


class TenantCreateIn(Schema):
    name: str
    subdomain: str


class TenantOut(Schema):
    id: int
    name: str
    schema_name: str
    created_at: Optional[datetime] = None


class StaffInviteIn(Schema):
    email: str
    name: str


class StaffInviteOut(Schema):
    id: int
    email: str
    name: str
    role: str
    is_new: bool


class PasswordResetIn(Schema):
    new_password: str


class MessageOut(Schema):
    message: str


# ---------------------------------------------------------------------------
# Auth Router — /api/auth/
# ---------------------------------------------------------------------------
auth_router = Router(tags=["auth"])


@auth_router.post("/register", response={201: UserOut, 409: MessageOut}, auth=None)
def register(request: HttpRequest, data: RegisterIn):
    """Register a new user. No auth required.

    Error responses:
    - 409 Conflict: email already registered
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    from tenant_users.tenants.models import ExistsError

    try:
        user = User.objects.create_user(
            email=data.email,
            password=data.password,
            name=data.name,
        )
    except ExistsError:
        return 409, {"message": "A user with this email already exists."}
    except ValueError as e:
        raise HttpError(400, str(e))
    return 201, user


@auth_router.post(
    "/login", response={200: UserWithTenantOut, 401: MessageOut}, auth=None
)
def login_view(request: HttpRequest, data: LoginIn):
    """Log in with email and password. Returns user info + tenant context.

    Error responses:
    - 401 Unauthorized: bad credentials
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    user = authenticate(request, email=data.email, password=data.password)
    if user is None:
        return 401, {"message": "Invalid email or password."}
    # Django rotates session key on login by default
    login(request, user)
    tenant_name = None
    if hasattr(request, "tenant") and request.tenant.schema_name != "public":
        tenant_name = request.tenant.name
    return 200, {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenant": tenant_name,
    }


@auth_router.post("/logout", response={200: MessageOut}, auth=django_auth)
def logout_view(request: HttpRequest):
    """Log out. Auth required.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    logout(request)
    return 200, {"message": "Logged out successfully."}


@auth_router.get("/me", response={200: UserWithTenantOut}, auth=django_auth)
def me(request: HttpRequest):
    """Return current user info + current tenant name if on tenant subdomain.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    user = request.user
    tenant_name = None
    if hasattr(request, "tenant") and request.tenant.schema_name != "public":
        tenant_name = request.tenant.name
    return 200, {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenant": tenant_name,
    }


@auth_router.post(
    "/reset-password", response={200: MessageOut, 400: MessageOut}, auth=django_auth
)
def reset_password(request: HttpRequest, data: PasswordResetIn):
    """Reset password for the authenticated user.

    Error responses:
    - 400 Bad Request: password too short
    - 401 Unauthorized: not authenticated
    """
    if len(data.new_password) < 8:
        return 400, {"message": "Password must be at least 8 characters."}
    user = request.user
    user.set_password(data.new_password)
    user.must_reset_password = False
    user.save(update_fields=["password", "must_reset_password"])
    # Re-authenticate so session stays valid after password change
    login(request, user)
    return 200, {"message": "Password reset successfully."}


# ---------------------------------------------------------------------------
# Staff Router — /api/staff/
# ---------------------------------------------------------------------------
staff_router = Router(tags=["staff"])


def _get_current_tenant(request: HttpRequest) -> Tenant:
    """Return the current tenant from the request, raising 400 if on public."""
    if not hasattr(request, "tenant") or request.tenant.schema_name == "public":
        raise HttpError(400, "Staff management requires a tenant context.")
    return request.tenant


def _require_admin(request: HttpRequest) -> None:
    """Raise 403 if user is not an admin."""
    if request.user.role != "admin":
        raise HttpError(403, "Only admins can manage staff.")


@staff_router.get("/", response={200: list[UserOut]}, auth=django_auth)
def list_staff(request: HttpRequest):
    """List all users associated with the current tenant.

    Error responses:
    - 400 Bad Request: not in tenant context
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    """
    tenant = _get_current_tenant(request)
    _require_admin(request)
    users = tenant.user_set.all()
    return 200, [
        {"id": u.id, "email": u.email, "name": u.name, "role": u.role} for u in users
    ]


@staff_router.post(
    "/invite",
    response={200: StaffInviteOut, 409: MessageOut},
    auth=django_auth,
)
def invite_staff(request: HttpRequest, data: StaffInviteIn):
    """Invite a user to the current tenant. Creates the user if they don't exist.

    Error responses:
    - 400 Bad Request: not in tenant context
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 409 Conflict: user already in tenant
    """
    from tenant_users.tenants.models import ExistsError

    tenant = _get_current_tenant(request)
    _require_admin(request)

    is_new = False
    try:
        user = User.objects.get(email=data.email)
    except User.DoesNotExist:
        # Create new user with random temporary password
        temp_password = secrets.token_urlsafe(16)
        user = User.objects.create_user(
            email=data.email,
            password=temp_password,
            name=data.name,
        )
        user.must_reset_password = True
        user.save(update_fields=["must_reset_password"])
        is_new = True

    try:
        tenant.add_user(user)
    except ExistsError:
        return 409, {"message": "User is already a member of this tenant."}

    return 200, {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "is_new": is_new,
    }


@staff_router.delete(
    "/{user_id}",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut},
    auth=django_auth,
)
def remove_staff(request: HttpRequest, user_id: int):
    """Remove a user from the current tenant. Does NOT delete the user globally.

    Error responses:
    - 400 Bad Request: not in tenant context, or cannot remove owner
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: user not found
    """
    from tenant_users.tenants.models import DeleteError

    tenant = _get_current_tenant(request)
    _require_admin(request)

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return 404, {"message": "User not found."}

    try:
        tenant.remove_user(user)
    except DeleteError as e:
        return 400, {"message": str(e)}
    except User.DoesNotExist:
        return 404, {"message": "User is not a member of this tenant."}

    return 200, {"message": "User removed from tenant."}


# ---------------------------------------------------------------------------
# Mount routers on the API instance
# ---------------------------------------------------------------------------
api.add_router("/auth/", auth_router)
api.add_router("/staff/", staff_router)

# Import and mount tenant router
from apps.tenants.api import tenant_router  # noqa: E402

api.add_router("/tenants/", tenant_router)

# Import and mount workflow/task routers (Phase 3)
from apps.workflows.api import task_router, workflow_router  # noqa: E402

api.add_router("/workflows/", workflow_router, tags=["workflows"])
api.add_router("/tasks/", task_router, tags=["tasks"])

# Import and mount document router (Phase 4)
from apps.documents.api import document_router  # noqa: E402

api.add_router("/documents/", document_router, tags=["documents"])

# Import and mount dashboard router (Phase 6)
from apps.dashboard.api import dashboard_router  # noqa: E402

api.add_router("/dashboard/", dashboard_router, tags=["dashboard"])
