# Dashboard — Stats Endpoint

<system-reminder>
1. Dashboard is a TENANT app — all queries are auto-scoped by `TenantMainMiddleware`. Never manually filter by tenant.
2. This spec has exactly ONE endpoint: `GET /api/dashboard/stats`. Do not add CRUD or mutation endpoints here.
3. Use Django ORM aggregation (`Count`, `Q` objects) for all stats — do not load all rows into Python and count manually.
4. The `tasks_by_status` field must include ALL 5 status values (created, assigned, in_progress, completed, cancelled) even if the count is 0.
</system-reminder>

## Goal

Any authenticated tenant member can call a single endpoint to retrieve aggregate statistics for their clinic's workspace: total workflow count, task counts broken down by status, total document count, and total staff count. This powers the dashboard cards on the frontend.

## Definitions

- **Dashboard stats**: A JSON object with four top-level keys: `workflows` (int), `tasks_by_status` (dict mapping each status to its count), `documents` (int), `staff_count` (int).
- **tasks_by_status**: A dictionary with keys `"created"`, `"assigned"`, `"in_progress"`, `"completed"`, `"cancelled"`, each mapping to an integer count. All 5 keys are always present.
- **staff_count**: The number of users who are members of the current tenant. Obtained via `django-tenant-users` tenant membership query.
- **TENANT app**: Queries for Workflow, Task, Document are auto-scoped. Staff count requires querying the shared User model filtered by tenant membership.

## Plan

1. Create `apps/dashboard/__init__.py` (empty) and `apps/dashboard/api.py` with a single endpoint.
2. Verify `apps.dashboard` is in `TENANT_APPS` in `config/settings.py`.
3. Create `apps/dashboard/schemas.py` with `DashboardStatsOut` schema: `workflows` (int), `tasks_by_status` (dict[str, int]), `documents` (int), `staff_count` (int).
4. Implement the stats endpoint in `apps/dashboard/api.py`:
   a. `workflows`: `Workflow.objects.count()`
   b. `tasks_by_status`: `Task.objects.aggregate()` with conditional `Count` for each status using `Q` objects, or `Task.objects.values("status").annotate(count=Count("id"))` then fill missing statuses with 0.
   c. `documents`: `Document.objects.count()`
   d. `staff_count`: query tenant membership count via `request.tenant.get_users().count()` or equivalent `django-tenant-users` method.
5. Wire the router into `config/urls.py` (tenant URL config) under `/api/dashboard/`.
6. Write tests in `apps/dashboard/tests.py`.
7. Run `uv run python manage.py migrate_schemas --tenant` (dashboard has no models, but confirm app is registered).
8. Run `black . && ruff check . --fix`, then `uv run python manage.py test`.

## Source Files

- `apps/dashboard/__init__.py` — empty init
- `apps/dashboard/api.py` — Stats endpoint
- `apps/dashboard/schemas.py` — DashboardStatsOut schema
- `apps/dashboard/tests.py` — Test cases
- `config/settings.py` — Verify `apps.dashboard` in `TENANT_APPS`
- `config/urls.py` — Include dashboard router at `/api/dashboard/`

## Test Cases

```
Given a tenant with 2 workflows, 5 tasks (1 created, 2 assigned, 1 in_progress, 1 completed, 0 cancelled), 3 documents, and 4 staff members
When GET /api/dashboard/stats
Then 200 with {
  "workflows": 2,
  "tasks_by_status": {"created": 1, "assigned": 2, "in_progress": 1, "completed": 1, "cancelled": 0},
  "documents": 3,
  "staff_count": 4
}

Given an empty tenant (no workflows, tasks, documents; 1 staff member -- the admin)
When GET /api/dashboard/stats
Then 200 with {
  "workflows": 0,
  "tasks_by_status": {"created": 0, "assigned": 0, "in_progress": 0, "completed": 0, "cancelled": 0},
  "documents": 0,
  "staff_count": 1
}

Given an unauthenticated request
When GET /api/dashboard/stats
Then 401 with {"detail": "Authentication required"}

Given a user who is not a member of this tenant
When GET /api/dashboard/stats (on the tenant's subdomain)
Then 403 (blocked by TenantAccessMiddleware before reaching the endpoint)

Given a tenant with 100 workflows, 500 tasks, 200 documents
When GET /api/dashboard/stats
Then 200 with correct counts (verifies aggregation is efficient, not N+1)
```

## Edge Cases

- The `tasks_by_status` dict must always have all 5 keys even when some counts are 0. Never return a partial dict.
- Staff count includes the admin(s) who created the tenant, not just invited staff.
- If the Workflow, Task, or Document models have not been migrated yet (empty tenant schema), the counts should return 0 without errors.
- This endpoint performs 4 queries (workflows count, tasks aggregate, documents count, staff count). Ensure these are simple aggregations, not full table scans. For large datasets, consider caching with a 60-second TTL using tenant-aware Redis keys.
- The dashboard app has NO models of its own. It only reads from models in `apps.workflows` and `apps.documents`. This is acceptable for a TENANT app -- it still needs to be in `TENANT_APPS` so its URLs are available in tenant context.

## Out of Scope

- Historical stats or trends over time
- Per-user task statistics
- Real-time updates via WebSocket
- Dashboard customization or widget configuration
- Export stats to CSV/PDF
- Caching (add in Extensions)

## Extensions

- Cache stats in Redis with a 60-second TTL using `django_tenants.cache.make_key`; invalidate on any Workflow/Task/Document mutation
- Add `recent_activity` field: last 10 AuditLog entries for the tenant
- Add `tasks_overdue` count: tasks with `due_date < now()` and status not in (completed, cancelled)
- Add `tasks_by_assignee` breakdown: count per assigned user
