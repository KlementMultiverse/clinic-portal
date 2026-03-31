import logging
import re

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError
from ninja.security import django_auth
from tenant_users.tenants.models import ExistsError
from tenant_users.tenants.tasks import provision_tenant

from apps.tenants.models import Tenant
from apps.users.api import MessageOut, TenantCreateIn, TenantOut

RESERVED_SUBDOMAINS = {"admin", "api", "www", "public", "portal", "mail", "ftp"}

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tenant Router — /api/tenants/
# Per CLAUDE.md Rule #1: Django Ninja for all API routes.
# ---------------------------------------------------------------------------
tenant_router = Router(tags=["tenants"])


@tenant_router.post(
    "/",
    response={201: TenantOut, 400: MessageOut, 409: MessageOut},
    auth=django_auth,
)
def create_tenant(request: HttpRequest, data: TenantCreateIn):
    """Create a new tenant. Auth required.

    Uses provision_tenant from tenant_users to create the tenant, schema,
    domain, and set the requesting user as owner.

    Error responses:
    - 400 Bad Request: invalid input
    - 401 Unauthorized: not authenticated
    - 409 Conflict: subdomain already taken
    """
    subdomain = data.subdomain
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", subdomain):
        return 400, {"message": "Invalid subdomain format."}
    if subdomain in RESERVED_SUBDOMAINS:
        return 400, {"message": "This subdomain is reserved."}

    try:
        tenant, domain = provision_tenant(
            tenant_name=data.name,
            tenant_slug=data.subdomain,
            owner=request.user,
        )
    except ExistsError:
        return 409, {"message": "A tenant with this subdomain already exists."}
    except Exception as e:
        raise HttpError(400, str(e))

    logger.info(
        "Tenant created: name=%s, subdomain=%s by user=%s",
        data.name,
        data.subdomain,
        request.user.email,
    )

    # Set the user's role to admin for this tenant
    request.user.role = "admin"
    request.user.save(update_fields=["role"])

    return 201, {
        "id": tenant.id,
        "name": tenant.name,
        "schema_name": tenant.schema_name,
        "created_at": tenant.created_at,
    }


@tenant_router.get(
    "/",
    response={200: list[TenantOut], 403: MessageOut},
    auth=django_auth,
)
def list_tenants(request: HttpRequest):
    """List all tenants. Superadmin only (is_superuser).

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not a superuser
    """
    if not request.user.is_superuser:
        return 403, {"message": "Only superadmins can list all tenants."}

    tenants = Tenant.objects.exclude(schema_name="public").order_by("-created_at")
    return 200, [
        {
            "id": t.id,
            "name": t.name,
            "schema_name": t.schema_name,
            "created_at": t.created_at,
        }
        for t in tenants
    ]
