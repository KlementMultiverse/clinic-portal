"""Custom tenant middleware that supports both subdomains and session-based routing.

On localhost: subdomains work normally (clinic1.localhost → Sunrise Clinic)
On Railway/ngrok: no subdomains → tenant stored in session after user picks clinic
"""

from typing import Callable

from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django_tenants.middleware.main import TenantMainMiddleware
from django_tenants.utils import get_public_schema_name, get_tenant_model


class SafeTenantAccessMiddleware:
    """Replaces tenant_users TenantAccessMiddleware with login-aware version.

    - Anonymous users on tenant subdomains → redirect to /login/
    - Authenticated users not belonging to tenant → deny access
    - Login, auth API, and static paths are always allowed
    """

    EXEMPT_PATHS = ("/login/", "/api/auth/", "/static/", "/admin/")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Only check on tenant subdomains, not public schema
        if (
            not hasattr(request, "tenant")
            or request.tenant.schema_name == get_public_schema_name()
        ):
            return self.get_response(request)

        # Always allow exempt paths
        if any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
            return self.get_response(request)

        # Anonymous → redirect to login
        if not request.user.is_authenticated:
            return redirect("/login/")

        # Authenticated but not a member of this tenant → logout and redirect to login
        if not request.user.tenants.filter(pk=request.tenant.pk).exists():
            from django.contrib.auth import logout
            logout(request)
            return redirect("/login/")

        return self.get_response(request)


class FlexibleTenantMiddleware(TenantMainMiddleware):
    """Extends TenantMainMiddleware to fall back to session-based tenant routing.

    Flow:
    1. Try normal subdomain-based routing (TenantMainMiddleware default)
    2. If that resolves to public schema AND user has a tenant in session → use that
    3. Otherwise stay on public schema
    """

    def process_request(self, request: HttpRequest) -> None:
        # Let the parent try subdomain-based routing first
        super().process_request(request)

        # If we landed on public schema, check if session has a tenant override
        if (
            hasattr(request, "tenant")
            and request.tenant.schema_name == get_public_schema_name()
        ):
            tenant_id = request.session.get("active_tenant_id")
            if tenant_id:
                TenantModel = get_tenant_model()
                try:
                    tenant = TenantModel.objects.get(pk=tenant_id)
                    if tenant.schema_name != get_public_schema_name():
                        request.tenant = tenant
                        connection.set_tenant(tenant)
                except TenantModel.DoesNotExist:
                    # Invalid tenant in session — clear it
                    request.session.pop("active_tenant_id", None)
