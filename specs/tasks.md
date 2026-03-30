# Tasks — CRUD, State Transitions, Assignment

<system-reminder>
1. Task status transitions MUST be validated against `VALID_TRANSITIONS` — NEVER allow a direct status update that bypasses the state machine. The only way to change status is through the `/transition` endpoint.
2. VALID_TRANSITIONS map (memorize this):
   - "created" -> ["assigned", "cancelled"]
   - "assigned" -> ["in_progress", "cancelled"]
   - "in_progress" -> ["completed", "cancelled"]
   - "completed" -> [] (terminal)
   - "cancelled" -> [] (terminal)
3. Every state transition MUST create an AuditLog entry with `entity_type="task"`, `entity_id=task.id`, `action="status_change:{old}->{new}"`, `performed_by=request.user`.
4. The `/assign` endpoint changes `assigned_to` but does NOT automatically transition status. The admin must explicitly transition from "created" to "assigned" separately.
5. Tasks is a TENANT app — all queries are auto-scoped by middleware. Never manually filter by tenant.
</system-reminder>

## Goal

Tenant members can list and view tasks (with filtering by status and assignee). Admins can create tasks within a workflow, update task metadata (title, description, due_date), assign tasks to staff members, and transition task status through a strictly enforced state machine. Every state change is recorded in the AuditLog for health-tech audit compliance.

## Definitions

- **Task**: A unit of work within a workflow. Fields: `id`, `workflow` (FK to Workflow), `title` (CharField, max 300), `description` (TextField, blank), `status` (CharField, max 20, choices: created/assigned/in_progress/completed/cancelled, default "created"), `assigned_to` (FK to User, nullable), `created_by` (FK to User), `due_date` (DateTimeField, nullable), `created_at` (auto_now_add), `modified_at` (auto_now).
- **State machine**: The `VALID_TRANSITIONS` dict that defines which status changes are legal. Terminal states (completed, cancelled) have no outgoing transitions.
- **AuditLog**: Tracks every mutation. Fields: `entity_type` (CharField, max 50), `entity_id` (IntegerField), `action` (CharField, max 200), `details` (JSONField, default dict), `performed_by` (FK to User, nullable), `timestamp` (auto_now_add).
- **Transition**: A POST to `/api/tasks/{id}/transition` with `{"status": "new_status"}`. Validated against `VALID_TRANSITIONS`, logged in AuditLog.
- **Assignment**: A POST to `/api/tasks/{id}/assign` with `{"user_id": <int>}`. Sets `assigned_to` without changing status.

## Plan

1. Models are defined in `apps/workflows/models.py` (Task and AuditLog live alongside Workflow since they are in the same tenant app). If models already exist from the workflows spec, this spec adds no new models — only endpoints and services.
2. Create `apps/workflows/task_schemas.py` (or add to existing `schemas.py`) with: `TaskIn` (workflow_id, title, description, due_date), `TaskOut` (id, title, description, status, assigned_to_id, assigned_to_name, workflow_id, due_date, created_at, modified_at), `TaskDetailOut` (same plus workflow_name, created_by_name, audit_log entries), `TransitionIn` (status), `TransitionOut` (id, status, previous_status), `AssignIn` (user_id), `AssignOut` (id, assigned_to_id, assigned_to_name), `TaskFilterQuery` (status optional, assigned_to optional).
3. Create `apps/workflows/task_services.py` with:
   a. `transition_task(task, new_status, user)` — validates against `VALID_TRANSITIONS`, saves, creates AuditLog.
   b. `assign_task(task, user_to_assign, requesting_user)` — sets `assigned_to`, creates AuditLog with action `"assigned:{user_id}"`.
4. Create `apps/workflows/task_api.py` (or add to existing `api.py`) with a Django Ninja Router containing 6 endpoints: list (with query filters), create, get, update, transition, assign.
5. Implement filtering on the list endpoint: optional `status` and `assigned_to` query parameters.
6. Implement role checks: create/update/assign require admin; list/get/transition require any authenticated member.
7. Wire the router into `config/urls.py` under `/api/tasks/`.
8. Write tests in `apps/workflows/tests_tasks.py` (separate from workflow tests for clarity).
9. Run `uv run python manage.py migrate_schemas --tenant` (if model changes).
10. Run `black . && ruff check . --fix`, then `uv run python manage.py test`.

## Source Files

- `apps/workflows/models.py` — Task model with `VALID_TRANSITIONS` and `transition_to()` method, AuditLog model (may already exist from workflows spec)
- `apps/workflows/task_schemas.py` — Ninja schemas for task endpoints
- `apps/workflows/task_services.py` — State machine enforcement, assignment logic
- `apps/workflows/task_api.py` — Task CRUD + transition + assign endpoints
- `apps/workflows/tests_tasks.py` — Test cases for task endpoints
- `config/urls.py` — Include task router at `/api/tasks/`

## Test Cases

```
Given an admin user and a workflow with id 1
When POST /api/tasks/ with {"workflow_id": 1, "title": "Verify insurance", "description": "Check patient insurance coverage", "due_date": "2026-04-15T10:00:00Z"}
Then 201 with {"id": <int>, "title": "Verify insurance", "status": "created", "assigned_to_id": null}
And an AuditLog entry with entity_type="task", action="created"

Given a staff user
When POST /api/tasks/ with valid payload
Then 403 with {"detail": "Admin access required"}

Given 5 tasks exist (2 created, 2 in_progress, 1 completed)
When GET /api/tasks/?status=in_progress
Then 200 with a list of 2 tasks, all with status "in_progress"

Given 5 tasks exist, 2 assigned to user 3
When GET /api/tasks/?assigned_to=3
Then 200 with a list of 2 tasks

Given a task with id 1 in status "created"
When POST /api/tasks/1/transition with {"status": "assigned"}
Then 200 with {"id": 1, "status": "assigned", "previous_status": "created"}
And an AuditLog entry with action="status_change:created->assigned"

Given a task with id 1 in status "created"
When POST /api/tasks/1/transition with {"status": "completed"}
Then 400 with {"detail": "Invalid transition from 'created' to 'completed'"}
And no AuditLog entry is created

Given a task with id 1 in status "completed"
When POST /api/tasks/1/transition with {"status": "in_progress"}
Then 400 with {"detail": "Invalid transition from 'completed' to 'in_progress'"}

Given a task with id 1 in status "cancelled"
When POST /api/tasks/1/transition with {"status": "created"}
Then 400 with {"detail": "Invalid transition from 'cancelled' to 'created'"}

Given an admin user, a task with id 1, and a staff user with id 5 in this tenant
When POST /api/tasks/1/assign with {"user_id": 5}
Then 200 with {"id": 1, "assigned_to_id": 5, "assigned_to_name": "Staff Name"}
And an AuditLog entry with action="assigned:5"

Given an admin user and a task with id 1
When POST /api/tasks/1/assign with {"user_id": 999} (user not in tenant)
Then 404 with {"detail": "User not found in this tenant"}

Given an admin user and a task with id 1
When PUT /api/tasks/1 with {"title": "Updated title", "description": "New desc"}
Then 200 with updated task data
And the status field is NOT changed (update endpoint cannot change status)

Given a task with id 999 does not exist
When GET /api/tasks/999
Then 404 with {"detail": "Task not found"}

Given a task with id 1 exists
When GET /api/tasks/1
Then 200 with full task detail including audit_log entries for this task
```

## Edge Cases

- The PUT (update) endpoint MUST NOT accept a `status` field. Status can only change via `/transition`. If `status` is included in the update payload, ignore it silently.
- Assigning a task to a user who is not a member of the current tenant must return 404. Validate membership via `django-tenant-users` methods.
- Assigning a task that is already assigned to the same user returns 200 (idempotent), but still creates an AuditLog entry.
- The `due_date` field accepts ISO 8601 format. Null means no due date. Passing an empty string should set it to null.
- When listing tasks, if both `status` and `assigned_to` filters are provided, apply them with AND logic.
- Task title must not be empty. Return 422 if empty.
- A task cannot be created in a nonexistent workflow. Return 404 if `workflow_id` does not exist.
- The transition endpoint is available to all authenticated users (staff can transition their own tasks), not just admins.

## Out of Scope

- Task comments or attachments (documents are linked separately)
- Task priority levels
- Task dependencies (task A blocks task B)
- Recurring tasks
- Bulk status transitions
- Task notifications or reminders
- Undo/revert transitions

## Extensions

- Add task priority field (low, medium, high, urgent) with filtering support
- Add task comments as a sub-resource (`/api/tasks/{id}/comments`)
- Add bulk transition endpoint for admin to complete/cancel multiple tasks
- Add overdue task detection (tasks past `due_date` with status != completed/cancelled)
