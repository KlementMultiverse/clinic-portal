import logging
import os
import re

from django.http import HttpRequest
from ninja import Router
from ninja.security import django_auth
from tenant_users.tenants.models import ExistsError
from tenant_users.tenants.tasks import provision_tenant

from apps.tenants.models import Domain, Tenant
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
    subdomain = data.subdomain.lower().strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$", subdomain):
        return 400, {
            "message": "Subdomain must be 3-32 characters, "
            "lowercase letters, numbers, and hyphens only."
        }
    if subdomain in RESERVED_SUBDOMAINS:
        return 400, {
            "message": f'"{subdomain}" is reserved. ' "Try a different subdomain."
        }

    # Check for existing clinic with same name
    from apps.tenants.models import Domain

    if (
        Tenant.objects.filter(name__iexact=data.name)
        .exclude(schema_name="public")
        .exists()
    ):
        return 409, {
            "message": f'A clinic named "{data.name}" already exists. '
            "Please choose a different name."
        }

    # Check for existing subdomain
    domain_name = f"{subdomain}.localhost"
    if Domain.objects.filter(domain=domain_name).exists():
        return 409, {
            "message": f'The subdomain "{subdomain}" is already taken. '
            "Please choose a different one."
        }

    try:
        tenant, domain = provision_tenant(
            tenant_name=data.name,
            tenant_slug=subdomain,
            owner=request.user,
        )
    except ExistsError:
        return 409, {
            "message": f'The subdomain "{subdomain}" is already taken. '
            "Please choose a different one."
        }
    except Exception as e:
        logger.error("Tenant creation failed: %s", e, exc_info=True)
        return 400, {"message": f"Workspace creation failed: {e}"}

    logger.info(
        "Tenant created: name=%s, subdomain=%s by user=%s",
        data.name,
        data.subdomain,
        request.user.email,
    )

    # Add live domain if configured (e.g., klementgunndu.space)
    live_domain = os.environ.get("TENANT_BASE_DOMAIN", "")
    if live_domain:
        live_fqdn = f"{subdomain}.{live_domain}"
        if not Domain.objects.filter(domain=live_fqdn).exists():
            Domain.objects.create(domain=live_fqdn, tenant=tenant, is_primary=False)

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


@tenant_router.get(
    "/my-clinics",
    response={200: list[TenantOut]},
    auth=django_auth,
)
def my_clinics(request: HttpRequest):
    """List clinics the current user belongs to."""
    tenants = request.user.tenants.exclude(schema_name="public")
    return 200, [
        {
            "id": t.id,
            "name": t.name,
            "schema_name": t.schema_name,
            "created_at": t.created_at,
        }
        for t in tenants
    ]


@tenant_router.post(
    "/switch/{tenant_id}",
    response={200: MessageOut, 404: MessageOut},
    auth=django_auth,
)
def switch_tenant(request: HttpRequest, tenant_id: int):
    """Switch active tenant (session-based, for single-domain deployments)."""
    tenant = request.user.tenants.filter(pk=tenant_id).exclude(
        schema_name="public"
    ).first()
    if not tenant:
        return 404, {"message": "Clinic not found."}
    request.session["active_tenant_id"] = tenant.id
    logger.info(
        "Tenant switch: user=%s → %s", request.user.email, tenant.name
    )
    return 200, {"message": f"Switched to {tenant.name}"}
