import hashlib
import logging
from datetime import datetime
from typing import Optional

from django.core.cache import cache
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from apps.documents.services import get_user_context, invoke_generate_tasks_lambda
from apps.users.models import User
from apps.users.services import track_action
from apps.workflows.models import AuditLog, Task, Workflow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schemas — Per CLAUDE.md Rule #1, using Django Ninja Schema (Pydantic)
# ---------------------------------------------------------------------------


class WorkflowIn(Schema):
    name: str
    description: str = ""


class WorkflowOut(Schema):
    id: int
    name: str
    description: str
    created_by_id: int
    created_at: datetime
    modified_at: datetime


class TaskOut(Schema):
    id: int
    workflow_id: int
    title: str
    description: str
    status: str
    assigned_to_id: Optional[int] = None
    created_by_id: int
    due_date: Optional[datetime] = None
    created_at: datetime


class WorkflowDetailOut(Schema):
    id: int
    name: str
    description: str
    created_by_id: int
    created_at: datetime
    modified_at: datetime
    tasks: list[TaskOut]


class TaskIn(Schema):
    workflow_id: int
    title: str
    description: str = ""
    due_date: Optional[datetime] = None


class TaskTransitionIn(Schema):
    new_status: str


class TaskAssignIn(Schema):
    user_id: int


class AuditLogOut(Schema):
    id: int
    entity_type: str
    entity_id: int
    action: str
    details: dict
    performed_by_id: Optional[int] = None
    timestamp: datetime


class MessageOut(Schema):
    message: str


class GenerateTasksOut(Schema):
    tasks: list[TaskOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_admin(request: HttpRequest) -> None:
    """Raise 403 if user is not an admin."""
    if request.user.role != "admin":
        raise HttpError(403, "Only admins can perform this action.")


# ---------------------------------------------------------------------------
# Workflow Router — /api/workflows/
# ---------------------------------------------------------------------------
workflow_router = Router(auth=django_auth, tags=["workflows"])


@workflow_router.get("/", response={200: list[WorkflowOut]})
def list_workflows(request: HttpRequest):
    """List all workflows in current tenant. Any authenticated user.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    cached = cache.get("workflows:list")
    if cached:
        return cached
    workflows = Workflow.objects.all()
    result = list(workflows)
    cache.set("workflows:list", result, 30)
    return 200, result


@workflow_router.post("/", response={201: WorkflowOut, 403: MessageOut})
def create_workflow(request: HttpRequest, data: WorkflowIn):
    """Create a new workflow. Admin only. AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    _require_admin(request)
    workflow = Workflow.objects.create(
        name=data.name,
        description=data.description,
        created_by=request.user,
    )
    AuditLog.objects.create(
        entity_type="workflow",
        entity_id=workflow.id,
        action="created",
        performed_by=request.user,
    )
    logger.info(
        "Workflow created: id=%d, name=%s by user=%s",
        workflow.id,
        workflow.name,
        request.user.email,
    )
    cache.delete("workflows:list")
    cache.delete("dashboard:stats")
    track_action(request, "created", "workflow", workflow.name, workflow.id)
    return 201, workflow


@workflow_router.get(
    "/{workflow_id}", response={200: WorkflowDetailOut, 404: MessageOut}
)
def get_workflow(request: HttpRequest, workflow_id: int):
    """Get workflow detail with tasks. Any authenticated user.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 404 Not Found: workflow does not exist
    """
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}
    return 200, {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "created_by_id": workflow.created_by_id,
        "created_at": workflow.created_at,
        "modified_at": workflow.modified_at,
        "tasks": list(workflow.tasks.all()),
    }


@workflow_router.put(
    "/{workflow_id}", response={200: WorkflowOut, 403: MessageOut, 404: MessageOut}
)
def update_workflow(request: HttpRequest, workflow_id: int, data: WorkflowIn):
    """Update a workflow. Admin only. AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: workflow does not exist
    """
    _require_admin(request)
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}
    workflow.name = data.name
    workflow.description = data.description
    workflow.save()
    AuditLog.objects.create(
        entity_type="workflow",
        entity_id=workflow.id,
        action="updated",
        performed_by=request.user,
    )
    cache.delete("workflows:list")
    cache.delete("dashboard:stats")
    track_action(request, "updated", "workflow", workflow.name, workflow.id)
    return 200, workflow


@workflow_router.delete(
    "/{workflow_id}", response={200: MessageOut, 403: MessageOut, 404: MessageOut}
)
def delete_workflow(request: HttpRequest, workflow_id: int):
    """Delete a workflow. Admin only. AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: workflow does not exist
    """
    _require_admin(request)
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}
    workflow_id_val = workflow.id
    workflow_name = workflow.name
    workflow.delete()
    AuditLog.objects.create(
        entity_type="workflow",
        entity_id=workflow_id_val,
        action="deleted",
        performed_by=request.user,
    )
    cache.delete("workflows:list")
    cache.delete("dashboard:stats")
    track_action(request, "deleted", "workflow", workflow_name, workflow_id_val)
    return 200, {"message": "Workflow deleted."}


@workflow_router.post(
    "/{workflow_id}/generate-tasks",
    response={200: GenerateTasksOut, 403: MessageOut, 404: MessageOut, 503: MessageOut},
)
def generate_tasks(request: HttpRequest, workflow_id: int):
    """Generate tasks for a workflow using AI (Lambda). Admin only.

    Per CLAUDE.md Rule #8: Lambda invocation via boto3 -- NEVER call OpenAI directly.
    Per CLAUDE.md Rule #12: AuditLog entry created for each generated task.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: workflow does not exist
    - 503 Service Unavailable: Lambda invocation failure
    """
    _require_admin(request)
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}

    logger.info("Generate tasks for workflow=%d", workflow_id)
    desc_hash = hashlib.md5(workflow.description.encode()).hexdigest()[:8]
    cache_key = f"llm:tasks:{workflow_id}:{desc_hash}"
    cached = cache.get(cache_key)

    if cached:
        generated = cached
    else:
        try:
            user_context = get_user_context(request.user)
            generated = invoke_generate_tasks_lambda(
                f"Workflow: {workflow.name}\nDescription: {workflow.description}",
                user_context=user_context,
            )
        except Exception as exc:
            return 503, {"message": f"AI service unavailable: {exc}"}
        cache.set(cache_key, generated, 3600)

    created_tasks = []
    for task_data in generated:
        title = task_data.get("title", "")
        description = task_data.get("description", "")
        if not title:
            continue
        task = Task.objects.create(
            workflow=workflow,
            title=title,
            description=description,
            status="created",
            created_by=request.user,
        )
        AuditLog.objects.create(
            entity_type="task",
            entity_id=task.id,
            action="created",
            details={"source": "ai_generated", "workflow_id": workflow.id},
            performed_by=request.user,
        )
        created_tasks.append(task)

    cache.delete("dashboard:stats")
    track_action(request, "generated_tasks", "workflow", workflow.name, workflow.id)
    return 200, {"tasks": created_tasks}


# ---------------------------------------------------------------------------
# Task Router — /api/tasks/
# ---------------------------------------------------------------------------
task_router = Router(auth=django_auth, tags=["tasks"])


@task_router.get("/", response={200: list[TaskOut]})
def list_tasks(
    request: HttpRequest,
    status: Optional[str] = None,
    assigned_to: Optional[int] = None,
):
    """List tasks, filterable by status and assigned_to. Any authenticated user.

    Error responses:
    - 401 Unauthorized: not authenticated
    """
    qs = Task.objects.all()
    if status:
        qs = qs.filter(status=status)
    if assigned_to:
        qs = qs.filter(assigned_to_id=assigned_to)
    return 200, list(qs)


@task_router.post("/", response={201: TaskOut, 403: MessageOut, 404: MessageOut})
def create_task(request: HttpRequest, data: TaskIn):
    """Create a task in a workflow. Admin only. AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: workflow does not exist
    - 422 Unprocessable Entity: schema validation failure (automatic)
    """
    _require_admin(request)
    try:
        workflow = Workflow.objects.get(pk=data.workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}
    task = Task.objects.create(
        workflow=workflow,
        title=data.title,
        description=data.description,
        due_date=data.due_date,
        created_by=request.user,
    )
    AuditLog.objects.create(
        entity_type="task",
        entity_id=task.id,
        action="created",
        performed_by=request.user,
    )
    cache.delete("dashboard:stats")
    track_action(request, "created", "task", task.title, task.id)
    return 201, task


@task_router.get("/{task_id}", response={200: TaskOut, 404: MessageOut})
def get_task(request: HttpRequest, task_id: int):
    """Get task detail. Any authenticated user.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 404 Not Found: task does not exist
    """
    try:
        task = Task.objects.get(pk=task_id)
    except Task.DoesNotExist:
        return 404, {"message": "Task not found."}
    return 200, task


@task_router.put(
    "/{task_id}", response={200: TaskOut, 403: MessageOut, 404: MessageOut}
)
def update_task(request: HttpRequest, task_id: int, data: TaskIn):
    """Update a task. Admin only. AuditLog entry created.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: task does not exist
    """
    _require_admin(request)
    try:
        task = Task.objects.get(pk=task_id)
    except Task.DoesNotExist:
        return 404, {"message": "Task not found."}
    try:
        workflow = Workflow.objects.get(pk=data.workflow_id)
    except Workflow.DoesNotExist:
        return 404, {"message": "Workflow not found."}
    task.workflow = workflow
    task.title = data.title
    task.description = data.description
    task.due_date = data.due_date
    task.save()
    AuditLog.objects.create(
        entity_type="task",
        entity_id=task.id,
        action="updated",
        performed_by=request.user,
    )
    return 200, task


@task_router.post(
    "/{task_id}/transition", response={200: TaskOut, 400: MessageOut, 404: MessageOut}
)
def transition_task(request: HttpRequest, task_id: int, data: TaskTransitionIn):
    """Change task status using VALID_TRANSITIONS. AuditLog entry.

    Per CLAUDE.md Rule #13: Task state transitions enforced by VALID_TRANSITIONS.

    Error responses:
    - 400 Bad Request: invalid transition
    - 401 Unauthorized: not authenticated
    - 404 Not Found: task does not exist
    """
    _require_admin(request)
    try:
        task = Task.objects.get(pk=task_id)
    except Task.DoesNotExist:
        return 404, {"message": "Task not found."}
    old_status = task.status
    try:
        task.transition_to(data.new_status, request.user)
    except ValueError as e:
        logger.warning(
            "Invalid transition attempted: task=%d, %s → %s",
            task_id,
            old_status,
            data.new_status,
        )
        return 400, {"message": str(e)}
    logger.info(
        "Task %d transitioned: %s → %s by %s",
        task_id,
        old_status,
        data.new_status,
        request.user.email,
    )
    cache.delete("dashboard:stats")
    track_action(request, "transitioned", "task", task.title, task.id)
    return 200, task


@task_router.post(
    "/{task_id}/assign", response={200: TaskOut, 403: MessageOut, 404: MessageOut}
)
def assign_task(request: HttpRequest, task_id: int, data: TaskAssignIn):
    """Assign task to a staff member. Admin only. AuditLog entry.

    Error responses:
    - 401 Unauthorized: not authenticated
    - 403 Forbidden: not an admin
    - 404 Not Found: task or user does not exist
    """
    _require_admin(request)
    try:
        task = Task.objects.get(pk=task_id)
    except Task.DoesNotExist:
        return 404, {"message": "Task not found."}
    try:
        user = User.objects.get(pk=data.user_id)
    except User.DoesNotExist:
        return 404, {"message": "User not found."}
    if not request.tenant.user_set.filter(pk=data.user_id).exists():
        return 404, {"message": "User not found in this tenant."}
    task.assigned_to = user
    task.save()
    logger.info("Task %d assigned to user=%s", task_id, user.email)
    AuditLog.objects.create(
        entity_type="task",
        entity_id=task.id,
        action=f"assigned_to:{user.email}",
        performed_by=request.user,
    )
    cache.delete("dashboard:stats")
    track_action(request, "assigned", "task", task.title, task.id)
    return 200, task
