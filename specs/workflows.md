# Workflows — CRUD + AI Task Generation

<system-reminder>
1. Workflows is a TENANT app — it lives in `TENANT_APPS`, its tables are created per-schema, and all ORM queries are auto-scoped by `TenantMainMiddleware`.
2. Only Admin role can create, update, delete workflows and trigger AI task generation. Any authenticated tenant member can read.
3. The `generate-tasks` endpoint calls AWS Lambda via `boto3.client("lambda").invoke()` — NEVER call OpenAI directly from Django.
4. Lambda payload format: `{"text": "<workflow description>", "task_type": "generate_tasks"}`. Lambda response format: `{"tasks": [{"title": "...", "description": "..."}, ...]}`.
5. Created tasks from AI generation must go through the normal Task creation path (set `status = "created"`, `created_by = request.user`, logged in AuditLog).
</system-reminder>

## Goal

Tenant admins can create, read, update, and delete business process workflows (e.g., "Patient Intake", "Referral Processing"). Any authenticated tenant member can list and view workflows with their associated tasks. Admins can trigger AI-powered task generation that sends the workflow description to an AWS Lambda function and auto-creates Task objects from the response.

## Definitions

- **Workflow**: A business process template. Fields: `id`, `name` (CharField, max 200), `description` (TextField, blank allowed), `created_by` (FK to User), `created_at` (auto_now_add), `modified_at` (auto_now).
- **TENANT app**: An app whose tables are created inside each tenant's PostgreSQL schema. Queries are automatically scoped by the middleware-set `search_path`.
- **generate-tasks**: An endpoint that sends the workflow's description to AWS Lambda with `task_type: "generate_tasks"` and creates Task objects from the returned list.
- **Lambda invocation**: Synchronous (`InvocationType="RequestResponse"`) call to the Lambda ARN stored in `LAMBDA_SUMMARIZE_ARN` env var.

## Plan

1. Create `apps/workflows/__init__.py` (empty) and `apps/workflows/models.py` with `Workflow`, `Task`, and `AuditLog` models (all three in this file since they are tightly coupled).
2. Verify `apps.workflows` is in `TENANT_APPS` in `config/settings.py`.
3. Create `apps/workflows/schemas.py` with Django Ninja schemas: `WorkflowIn` (name, description), `WorkflowOut` (id, name, description, task_count, created_by_name, created_at, modified_at), `WorkflowDetailOut` (same as WorkflowOut plus `tasks` list), `GenerateTasksOut` (tasks list).
4. Create `apps/workflows/services.py` with:
   a. `invoke_generate_tasks(workflow_description: str) -> list[dict]` — calls Lambda, parses response, returns list of `{"title": ..., "description": ...}` dicts.
   b. `create_tasks_from_ai(workflow, task_dicts, user)` — bulk-creates Task objects and AuditLog entries.
5. Create `apps/workflows/api.py` with a Django Ninja Router containing 6 endpoints: list, create, get, update, delete, generate-tasks.
6. Implement role checks: create/update/delete/generate-tasks require `request.user.role == "admin"`; list/get require only authentication.
7. Wire the router into `config/urls.py` (tenant URL config) under `/api/workflows/`.
8. Write tests in `apps/workflows/tests.py`.
9. Run `uv run python manage.py migrate_schemas --tenant`.
10. Run `black . && ruff check . --fix`, then `uv run python manage.py test`.

## Source Files

- `apps/workflows/__init__.py` — empty init
- `apps/workflows/models.py` — Workflow, Task, AuditLog models
- `apps/workflows/schemas.py` — Ninja request/response schemas
- `apps/workflows/services.py` — Lambda invocation, AI task creation logic
- `apps/workflows/api.py` — Workflow CRUD + generate-tasks endpoints
- `apps/workflows/tests.py` — Test cases
- `apps/workflows/admin.py` — Register models in Django admin
- `config/settings.py` — Verify `apps.workflows` in `TENANT_APPS`
- `config/urls.py` — Include workflow router at `/api/workflows/`

## Test Cases

```
Given an admin user in a tenant
When POST /api/workflows/ with {"name": "Patient Intake", "description": "New patient onboarding process"}
Then 201 with {"id": <int>, "name": "Patient Intake", "description": "New patient onboarding process", "task_count": 0}
And an AuditLog entry is created with entity_type="workflow", action="created"

Given a staff (non-admin) user in a tenant
When POST /api/workflows/ with {"name": "Test", "description": "..."}
Then 403 with {"detail": "Admin access required"}

Given 3 workflows exist in the tenant
When GET /api/workflows/
Then 200 with a list of 3 workflows, each including task_count

Given a workflow with id 1 exists and has 2 tasks
When GET /api/workflows/1
Then 200 with workflow detail including tasks array of length 2

Given an admin user and a workflow with id 1
When PUT /api/workflows/1 with {"name": "Updated Name", "description": "Updated desc"}
Then 200 with updated workflow data

Given an admin user and a workflow with id 1
When DELETE /api/workflows/1
Then 200 with {"success": true} and the workflow and its tasks are deleted

Given a workflow with id 1 does not exist
When GET /api/workflows/1
Then 404 with {"detail": "Workflow not found"}

Given an admin user and a workflow with id 1 that has description "Patient intake process for new clinic visitors"
When POST /api/workflows/1/generate-tasks (Lambda returns 3 tasks)
Then 200 with {"tasks": [{"title": "...", "description": "..."}, ...]} (3 items)
And 3 Task objects are created in the DB with status "created" and created_by = requesting user
And 3 AuditLog entries are created with entity_type="task", action="created"

Given a staff user and a workflow with id 1
When POST /api/workflows/1/generate-tasks
Then 403 with {"detail": "Admin access required"}

Given an admin user and a workflow with id 1, but Lambda invocation fails
When POST /api/workflows/1/generate-tasks
Then 502 with {"detail": "AI service unavailable"}
```

## Edge Cases

- Workflow name must not be empty. Description can be empty but generate-tasks requires a non-empty description (return 400 if empty).
- Deleting a workflow cascades to its tasks (via `on_delete=models.CASCADE`). AuditLog entries for those tasks are NOT deleted (they reference `entity_id` as an integer, not a FK).
- Lambda timeout: set a 30-second timeout on the boto3 invocation. If it times out, return 502.
- Lambda returns malformed JSON or missing `tasks` key: return 502 with a generic error, log the raw response for debugging.
- If Lambda returns an empty tasks list, return 200 with an empty list (not an error).
- `task_count` on the list endpoint uses annotation (`Count("tasks")`) for efficiency, not `len(workflow.tasks.all())`.
- Concurrent requests to generate-tasks for the same workflow should be safe (tasks are appended, not replaced).

## Out of Scope

- Workflow templates or cloning workflows
- Workflow versioning or history
- Workflow status/lifecycle (active, archived)
- Task ordering within a workflow
- Permissions beyond admin/staff (no per-workflow ACL)
- Pagination on the list endpoint (add in Extensions)

## Extensions

- Add pagination with cursor-based pagination for large workflow lists
- Add workflow archival (`is_archived` boolean, filter from default list)
- Add workflow cloning (duplicate workflow + its task templates)
- Cache workflow list in Redis with tenant-aware keys, invalidate on mutation
