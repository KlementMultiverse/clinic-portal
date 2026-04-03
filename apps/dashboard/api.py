import logging

from django.core.cache import cache
from django.db.models import Count
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.security import django_auth

from apps.documents.models import Document
from apps.search.models import SearchHistory
from apps.users.models import User
from apps.workflows.models import Task, Workflow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas -- Per CLAUDE.md Rule #1, using Django Ninja Schema (Pydantic)
# ---------------------------------------------------------------------------


class DashboardStatsOut(Schema):
    total_workflows: int
    total_documents: int
    total_staff: int
    total_searches: int = 0
    tasks_by_status: dict  # {"created": 1, "assigned": 2, ...}
    recent_actions: list = []


class MessageOut(Schema):
    message: str


# ---------------------------------------------------------------------------
# Dashboard Router -- /api/dashboard/
# Per CLAUDE.md Rule #1: Django Ninja for ALL API routes.
# ---------------------------------------------------------------------------
dashboard_router = Router(auth=django_auth, tags=["dashboard"])


@dashboard_router.get("/stats", response={200: DashboardStatsOut})
def get_stats(request: HttpRequest):
    """Return aggregate dashboard stats for the current tenant.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    logger.info("Dashboard stats requested by user=%s", request.user.email)
    cached = cache.get("dashboard:stats")
    if cached:
        return cached

    total_workflows = Workflow.objects.count()
    total_documents = Document.objects.count()

    # Count staff: users associated with the current tenant
    if hasattr(request, "tenant") and request.tenant.schema_name != "public":
        total_staff = request.tenant.user_set.count()
    else:
        total_staff = User.objects.count()

    # Aggregate task counts by status
    status_counts = (
        Task.objects.values("status").annotate(count=Count("id")).order_by("status")
    )
    tasks_by_status = {item["status"]: item["count"] for item in status_counts}

    total_searches = SearchHistory.objects.count()

    recent_actions = request.session.get("recent_actions", [])

    result = {
        "total_workflows": total_workflows,
        "total_documents": total_documents,
        "total_staff": total_staff,
        "total_searches": total_searches,
        "tasks_by_status": tasks_by_status,
        "recent_actions": recent_actions,
    }
    cache.set("dashboard:stats", result, 60)
    return 200, result
