# Multi-Tenant Clinic Management Portal -- Design Document

---

## 1. Current Context

### Existing System

No existing system. This is a greenfield build for an interview demonstration targeting a Senior Frontend Dev / AI-Native Engineer role at a healthcare SaaS company. The company's stack is Django 5, Django Ninja, django-tenants, PostgreSQL, Redis, AWS S3, and AWS Lambda. This project mirrors their production architecture to demonstrate competency.

### Gap Being Addressed

No open-source solution exists that combines Django multi-tenant isolation (schema-per-tenant via django-tenants) with AI-powered workflow automation (Lambda + OpenAI) and presigned-URL document management (S3). Existing Django multi-tenant examples stop at tenant provisioning and do not include business logic, state machines, audit logging, or LLM integration.

### Scope

- Local development demo, single developer, multi-tenant capable
- Not production-deployed: no CI/CD, no HIPAA compliance, no real patient data
- Demonstrates the full vertical: tenant isolation, session auth, CRUD, state machine, S3 presigned URLs, Lambda invocation, audit trail, server-rendered frontend
- Target: fully functional in Docker Compose on localhost with subdomain-based routing (`portal.localhost`, `clinic1.localhost`)

---

## 2. Requirements

### Functional Requirements

**Auth (4 endpoints)**
1. Users can register with email + password (shared schema, public tenant)
2. Users can log in and receive a Django session cookie backed by Redis
3. Users can log out to destroy the session
4. Users can call `/api/auth/me` to retrieve identity, role, and current tenant

**Tenants (2 endpoints)**
5. Authenticated users can create a new clinic tenant, which provisions a PostgreSQL schema, creates a Domain mapping, and makes the creator the admin
6. Superadmins can list all tenants

**Staff Management (3 endpoints)**
7. Tenant admins can list staff members in their tenant
8. Tenant admins can invite existing users to their tenant by email
9. Tenant admins can remove staff from their tenant

**Workflows (6 endpoints)**
10. Any authenticated tenant member can list workflows with task counts
11. Any authenticated tenant member can view a workflow with its tasks
12. Admins can create, update, and delete workflows
13. Admins can trigger AI task generation via Lambda, which appends tasks to the workflow (never replaces existing tasks)
14. Deleting a workflow with existing tasks returns 400 (blocked, not cascaded)

**Tasks (6 endpoints)**
15. Any authenticated tenant member can list tasks with filtering by status and assignee
16. Any authenticated tenant member can view task detail with audit log entries
17. Admins can create tasks within a workflow and update task metadata
18. Admins can assign tasks to staff members
19. Any staff member can transition ANY task in their tenant (not just tasks assigned to them) through a strictly enforced state machine
20. Every state transition is recorded in the AuditLog

**Documents (6 endpoints)**
21. Both admin and staff can upload documents via S3 presigned POST URLs
22. Users can register uploaded documents in the database after S3 upload completes
23. Users can list and download documents via presigned GET URLs
24. Users can request LLM-powered summaries (plain text only for MVP) via Lambda
25. Only admin or the original uploader can delete a document
26. S3 deletion is synchronous; if S3 delete fails, rollback the DB deletion

**Dashboard (1 endpoint)**
27. Any authenticated tenant member can retrieve aggregate stats: workflow count, tasks by all 5 statuses, document count, staff count

**Seed Data**
28. Management command creates public tenant with `portal.localhost` domain
29. Management command seeds demo clinic "Sunrise Clinic" with 2 staff, sample workflow with 4 tasks, and a sample document

**Frontend (server-rendered)**
30. Landing page, login, register, dashboard, workflows, workflow detail, documents, staff management pages
31. Django templates + vanilla JS + Pico CSS (no build step)

### Non-Functional Requirements

1. **Tenant Isolation**: PostgreSQL schema-per-tenant via django-tenants. TenantAccessMiddleware blocks unauthorized cross-tenant access. S3 keys namespaced by tenant schema name.
2. **Audit Logging**: ALL mutations logged -- task transitions, workflow CRUD, document upload/delete, staff invite/remove. AuditLog is append-only (no update/delete endpoints).
3. **Session Auth**: Django sessions backed by Redis with tenant-aware cache keys via `django_tenants.cache.make_key`. CSRF on all mutating endpoints.
4. **Presigned URL Security**: All S3 URLs expire after 15 minutes. POST-based presigned URLs with FormData (`generate_presigned_post`). Max 50MB upload enforced by presigned conditions.
5. **State Machine Enforcement**: VALID_TRANSITIONS dict enforced on every transition. Terminal states (completed, cancelled) have no outgoing transitions.
6. **Secrets Management**: All credentials from environment variables or `.env` file. Never hardcoded.
7. **SSL-Ready**: `SECURE_SSL_REDIRECT` and `SESSION_COOKIE_SECURE` configurable for production.
8. **S3 Encryption**: Server-side encryption (SSE-S3, AES-256) on all objects.
9. **Response Times**: API endpoints under 500ms. Lambda invocations under 30 seconds (with loading state in UI).
10. **Observability**: Structured logging at INFO level for all API calls. WARNING/ERROR for failures. AuditLog queryable per entity.
11. **Containerized**: Docker Compose for PostgreSQL 15 + Redis 7 + Django. Single `docker compose up` to start.
12. **Package Management**: uv exclusively. Never pip.
13. **Code Style**: black (line-length 100), ruff (rules E, F, I, UP, B; ignores E501, B008).

---

## 3. Design Decisions

### 1. Multi-Tenancy: Schema-Per-Tenant via django-tenants

Will implement schema-per-tenant using django-tenants because:
- The target company uses django-tenants in production -- matching their stack is non-negotiable for the interview demo
- Schema-per-tenant provides the strongest data isolation available in PostgreSQL: each tenant gets its own schema with its own tables, and the `search_path` is set per request by `TenantMainMiddleware`
- Healthcare data demands strong isolation -- even without HIPAA compliance, demonstrating awareness of isolation patterns signals maturity
- ORM queries are automatically scoped by the middleware-set `search_path` -- no manual `tenant_id` filtering, which eliminates an entire class of data leak bugs
- Trade-off: schema-per-tenant has higher operational overhead than shared-schema (each tenant = separate set of tables, migrations run per-schema via `migrate_schemas`). At scale, thousands of schemas become expensive. For a demo with 2-3 tenants, this is not a concern.
- Alternative considered: **Shared-schema with tenant_id column** -- simpler setup, single migration path, no middleware complexity. Rejected because: (a) every query must manually filter by tenant_id (error-prone), (b) a single missed filter leaks data across tenants, (c) does not match the target company's architecture.
- Alternative considered: **Database-per-tenant** -- strongest isolation (separate PostgreSQL databases). Rejected because: django-tenants does not support it natively, connection pooling becomes complex, and it is overkill for a demo.

### 2. API Framework: Django Ninja over DRF

Will implement all API routes using Django Ninja because:
- The target company's job description lists Django Ninja -- using DRF would demonstrate inability to read requirements
- Django Ninja uses Pydantic v2 schemas (type-safe, auto-validated) instead of DRF serializers (imperative, more boilerplate)
- Automatic OpenAPI/Swagger docs generated from type hints -- zero manual schema writing
- Native `async` support (not needed for MVP but demonstrates forward compatibility)
- Trade-off: smaller ecosystem than DRF (fewer third-party packages, less Stack Overflow coverage). For this project's scope, no DRF package is needed.
- Alternative considered: **Django REST Framework** -- larger community, more battle-tested, richer ecosystem (filters, pagination, permissions out of the box). Rejected because: (a) spec explicitly says "NOT DRF", (b) DRF serializer boilerplate is heavier, (c) DRF does not use Pydantic natively.

### 3. Auth: Django Session Auth via Redis (Not JWT)

Will implement authentication using Django's built-in session framework backed by Redis because:
- Sessions are the Django-native approach -- `authenticate()`, `login()`, `logout()` just work with zero custom code
- Redis session backend (`SESSION_ENGINE = "django.contrib.sessions.backends.cache"`) provides fast reads and automatic expiry
- Tenant-aware cache keys via `django_tenants.cache.make_key` prevent session collision across tenants
- Server-side session storage means the server controls session lifetime, can force logout, and never exposes sensitive data to the client
- Trade-off: sessions require server-side state (Redis). If Redis goes down, all sessions are lost. For a local demo, this is acceptable. Production would use Redis Sentinel or ElastiCache.
- Alternative considered: **JWT tokens** -- stateless, no server-side storage, works natively with SPAs and mobile apps. Rejected because: (a) JWT revocation is hard (requires a blocklist, which reintroduces server-side state), (b) JWTs in localStorage are vulnerable to XSS, (c) Django's session middleware integrates with CSRF protection out of the box, (d) this is a server-rendered app, not a SPA.
- Alternative considered: **Token auth (DRF-style)** -- simpler than JWT, stored in DB. Rejected because: (a) requires DRF, (b) database lookup on every request is slower than Redis.

### 4. Storage: S3 Presigned URLs (Not Server-Proxied Uploads)

Will implement file uploads and downloads using S3 presigned URLs (POST-based via `generate_presigned_post` with FormData) because:
- Browser uploads directly to S3 -- zero file data flows through Django, no memory pressure, no request timeout risk on large files
- `generate_presigned_post` returns a URL + form fields; the frontend builds a `FormData` and POSTs to S3. This is the AWS-recommended pattern for browser uploads.
- Presigned URLs expire after 15 minutes -- files are never publicly accessible
- S3 keys are namespaced by tenant schema name (`{schema}/{uuid}/{filename}`) -- even if a user guesses another tenant's key, presigned URLs are scoped to the generating tenant's IAM context
- Trade-off: two-step upload flow (get presigned URL, then upload to S3, then register in DB) is more complex for the frontend. Worth it for the security and performance benefits.
- Alternative considered: **Server-proxied uploads** -- Django receives the file via multipart form, streams to S3. Simpler frontend (single POST) but: (a) Django process holds the file in memory/disk, (b) large files can timeout or OOM, (c) doubles bandwidth (client -> Django -> S3). Rejected.
- Clarification applied: presigned URLs are POST-based (`generate_presigned_post` with FormData), not PUT-based (`generate_presigned_url("put_object")`).

### 5. LLM Integration: Lambda + OpenAI (Not Direct from Django)

Will implement LLM calls via AWS Lambda invoking OpenAI, invoked from Django via `boto3.client("lambda").invoke()` because:
- Lambda isolates the OpenAI dependency outside the Django process -- OpenAI SDK, API key, and network calls do not touch the Django runtime
- Lambda cold starts (5-15s) are acceptable for an AI feature that users expect to take a few seconds
- Lambda's 15-minute max timeout provides a natural ceiling for LLM calls without blocking Django workers
- The target company uses Lambda -- demonstrating boto3 Lambda invocation is a direct skill match
- Two task types: `"summarize_document"` (returns `{"summary": "..."}`) and `"generate_tasks"` (returns `{"tasks": [...]}`)
- Trade-off: Lambda adds deployment complexity (separate function, layer for OpenAI SDK, IAM permissions). For a demo, this is manageable via AWS Console.
- Alternative considered: **Direct OpenAI from Django** -- simpler (one `openai.chat.completions.create()` call), no Lambda deployment. Rejected because: (a) CLAUDE.md rule 8 forbids it, (b) OpenAI SDK as a Django dependency increases attack surface, (c) does not demonstrate Lambda skills.
- Alternative considered: **Celery background tasks** -- Pythonic, easier local debugging, retry logic built in. Rejected because: (a) adds a worker process + message broker, (b) the company uses Lambda not Celery, (c) spec says "No Celery."

### 6. Frontend: Django Templates + Vanilla JS (Not React)

Will implement the frontend using Django templates with vanilla JavaScript and Pico CSS because:
- The interview feedback explicitly stated "backend is the priority" -- spending time on a React build pipeline signals misallocated effort
- Django templates render server-side with zero build step -- no Node.js, no webpack, no npm
- Pico CSS provides clean, semantic styling from a single CDN link -- no Tailwind build, no PostCSS
- Vanilla JS with `fetch()` handles API calls, form submissions, and DOM updates for the limited interactivity needed (status transitions, upload flow, AI buttons)
- Trade-off: no component reuse, no client-side routing, limited interactivity compared to React. For 7 pages with simple CRUD forms and status buttons, this is sufficient.
- Alternative considered: **React (or Vue)** -- richer UI, component model, client-side routing, better UX for dynamic interactions. Rejected because: (a) adds Node toolchain and build complexity, (b) recruiter said backend matters more, (c) time better spent on multi-tenancy and audit logging, (d) CLAUDE.md rule 15 forbids it.

### 7. State Machine: Python Dict-Based VALID_TRANSITIONS (Not django-fsm)

Will implement task status transitions using a plain Python dict (`VALID_TRANSITIONS`) on the Task model because:
- Five states, five transitions -- the state machine is small enough that a dict + validation method is clearer than a library
- `transition_to(new_status, user)` method: check dict, update status, create AuditLog entry -- three lines of business logic, fully readable
- No magic: the dict is visible in the model file, every developer can read the allowed transitions without learning a library API
- Trade-off: no built-in transition hooks, no automatic graph visualization, no protection against direct `.status = "completed"` saves bypassing the method. Mitigated by: (a) the `/transition` endpoint is the only way to change status, (b) the PUT endpoint ignores the `status` field.
- Alternative considered: **django-fsm library** -- provides `@transition` decorators, automatic validation, transition conditions, graph export. Rejected because: (a) adds a dependency for a 5-state machine, (b) decorator-based transitions obscure the logic behind magic, (c) the team (one developer) gains nothing from the abstraction.

### 8. User Model: django-tenant-users UserProfile (Not Custom AbstractUser)

Will implement the User model by extending `tenant_users.tenants.models.UserProfile` because:
- django-tenant-users provides the global-user-per-tenant-membership pattern: users live in the shared (public) schema, tenant membership is tracked separately
- `UserProfile` uses email as the username field -- no need for a separate username, which matches the healthcare SaaS pattern (email-based accounts)
- `add_user()` / `remove_user()` methods on the Tenant model handle membership management with no custom join table
- `TenantAccessMiddleware` from django-tenant-users blocks users from accessing tenants they do not belong to -- authorization is handled at the middleware layer
- Trade-off: UserProfile's API is less documented than Django's AbstractUser. Requires reading django-tenant-users source for edge cases.
- Alternative considered: **Custom AbstractUser** -- full control over fields and methods, well-documented Django pattern. Rejected because: (a) does not integrate with django-tenant-users' membership system, (b) requires building the tenant-user association from scratch, (c) does not get `TenantAccessMiddleware` for free.
- Clarification applied: new users exist in the shared schema until they create or join a tenant. Registration does not require a tenant context.

### 9. Audit Logging: Explicit AuditLog Model on Every Mutation (Not Django Signals)

Will implement audit logging via explicit `AuditLog.objects.create()` calls at every mutation point because:
- Every mutation is visible in the code path -- `create()` calls are co-located with the business logic they audit
- The AuditLog tracks: `entity_type` (task, workflow, document, staff), `entity_id`, `action` (created, status_change:old->new, deleted, assigned, invited, removed), `details` (JSONField for extra context), `performed_by` (FK to User), `timestamp`
- Health-tech companies expect comprehensive audit trails -- logging only task transitions is insufficient. ALL mutations are logged: task transitions, workflow CRUD, document upload/delete, staff invite/remove
- Trade-off: audit calls are manual and can be forgotten. Mitigated by: (a) test cases verify AuditLog entries exist after every mutation, (b) code review checklist includes audit check.
- Alternative considered: **Django signals (post_save, post_delete)** -- automatic, no manual calls, impossible to forget. Rejected because: (a) signals fire on every save including internal/admin changes, creating noise, (b) signals cannot access `request.user` without thread-local hacks, (c) signals make the audit flow invisible -- you cannot see the logging by reading the endpoint code, (d) debugging signal-based audit issues is notoriously difficult.
- Alternative considered: **Django middleware logging** -- intercept all requests, log method/path/user. Rejected because: (a) too coarse -- logs reads alongside writes, (b) does not capture business context (which status transition, which entity), (c) does not replace the need for entity-level audit.

### 10. Testing: TenantTestCase with PostgreSQL (Not SQLite)

Will implement tests using `django_tenants.test.cases.TenantTestCase` with a real PostgreSQL database because:
- django-tenants requires PostgreSQL for schema creation -- SQLite does not support `CREATE SCHEMA` or `SET search_path`
- `TenantTestCase` automatically creates a test tenant with its own schema, runs the test within that schema context, and tears it down after
- This tests the actual tenant isolation mechanism, not a mock of it
- Trade-off: tests are slower than SQLite-based tests (PostgreSQL schema creation takes ~100ms per test class). Mitigated by: (a) using `TenantTestCase` only for integration tests that need tenant context, (b) running PostgreSQL in Docker for consistency.
- Alternative considered: **SQLite with mocked tenant context** -- faster tests, no Docker dependency. Rejected because: (a) SQLite cannot test the actual schema isolation that is the core feature, (b) django-tenants middleware and routing do not function with SQLite, (c) false confidence in test results.

---

## 4. Technical Design

### 4.1 Core Components

```
Request Flow:
  Browser (clinic1.localhost:8000)
    -> TenantMainMiddleware (resolves domain -> tenant, sets search_path to "clinic1")
    -> SessionMiddleware (reads sessionid cookie, loads session from Redis)
    -> AuthenticationMiddleware (attaches request.user from session)
    -> TenantAccessMiddleware (verifies user is member of resolved tenant)
    -> Django Ninja Router (dispatches to endpoint)
    -> Endpoint handler (ORM queries auto-scoped to "clinic1" schema)
    -> Response

Public Flow (portal.localhost:8000):
  Browser (portal.localhost:8000)
    -> TenantMainMiddleware (resolves domain -> public tenant, search_path = "public")
    -> PUBLIC_SCHEMA_URLCONF routes (auth + tenant creation endpoints)
    -> Response
```

**Middleware Stack (order is critical):**

```python
MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",       # Position 0 — MUST be first
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tenant_users.tenants.middleware.TenantAccessMiddleware",    # Blocks unauthorized tenant access
    "django.contrib.messages.middleware.MessageMiddleware",
]
```

**SHARED_APPS vs TENANT_APPS:**

```python
SHARED_APPS = [
    "django_tenants",           # Must be first
    "apps.tenants",             # Tenant + Domain models
    "apps.users",               # Global user model (UserProfile)
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
]

TENANT_APPS = [
    "apps.dashboard",
    "apps.workflows",
    "apps.documents",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]
```

### 4.2 Data Models

**Tenant (shared schema)**

```python
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase

class Tenant(TenantBase):
    name = models.CharField(max_length=100)              # "Sunrise Clinic"
    created_at = models.DateTimeField(auto_now_add=True)
    auto_create_schema = True                            # CREATE SCHEMA on save()

class Domain(DomainMixin):
    pass  # domain (CharField), tenant (FK), is_primary (BooleanField)
```

**User (shared schema)**

```python
from tenant_users.tenants.models import UserProfile

class User(UserProfile):
    name = models.CharField(max_length=150)
    role = models.CharField(
        max_length=20,
        choices=[("admin", "Admin"), ("staff", "Staff")],
        default="staff",
    )
    # Inherits from UserProfile: email (username field), is_active, tenant membership
```

**Workflow (tenant schema)**

```python
class Workflow(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
```

**Task (tenant schema)**

```python
class Task(models.Model):
    STATUS_CHOICES = [
        ("created", "Created"),
        ("assigned", "Assigned"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]
    VALID_TRANSITIONS = {
        "created": ["assigned", "cancelled"],
        "assigned": ["in_progress", "cancelled"],
        "in_progress": ["completed", "cancelled"],
        "completed": [],       # Terminal
        "cancelled": [],       # Terminal
    }

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="created")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_tasks",
    )
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    def transition_to(self, new_status, user):
        if new_status not in self.VALID_TRANSITIONS.get(self.status, []):
            raise ValueError(f"Cannot transition from {self.status} to {new_status}")
        old_status = self.status
        self.status = new_status
        self.save()
        AuditLog.objects.create(
            entity_type="task",
            entity_id=self.id,
            action=f"status_change:{old_status}->{new_status}",
            performed_by=user,
        )
```

**AuditLog (tenant schema)**

```python
class AuditLog(models.Model):
    entity_type = models.CharField(max_length=50)   # "task", "workflow", "document", "staff"
    entity_id = models.IntegerField()                # ID of the entity (not a FK -- survives deletion)
    action = models.CharField(max_length=200)        # "created", "status_change:created->assigned", "deleted"
    details = models.JSONField(default=dict, blank=True)  # Extra context
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
```

**Document (tenant schema)**

```python
class Document(models.Model):
    name = models.CharField(max_length=300)               # Original filename
    s3_key = models.CharField(max_length=500, unique=True) # {schema}/{uuid}/{filename}
    content_type = models.CharField(max_length=100)        # MIME type
    size_bytes = models.IntegerField()
    summary = models.TextField(blank=True)                 # LLM-generated
    workflow = models.ForeignKey(Workflow, null=True, blank=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```

### 4.3 Integration Points

**Auth Flow:**

```
1. POST /api/auth/register  -> User.objects.create_user() in shared schema
2. POST /api/auth/login     -> authenticate() + login() -> session stored in Redis
3. All subsequent requests   -> sessionid cookie -> Redis lookup -> request.user
4. POST /api/auth/logout    -> logout() -> session destroyed in Redis
```

**Tenant Provisioning Flow:**

```
1. POST /api/tenants/ with {name, subdomain}
2. services.provision_tenant():
   a. Create Tenant(schema_name=subdomain, name=name) -> auto_create_schema runs CREATE SCHEMA
   b. Create Domain(domain="{subdomain}.localhost", tenant=tenant, is_primary=True)
   c. tenant.add_user(user) -> adds user to tenant membership
   d. user.role = "admin" for this tenant context
3. Return tenant details
```

**S3 Upload Flow (presigned POST):**

```
1. Frontend: POST /api/documents/upload-url {filename, content_type}
2. Backend: generate s3_key = "{schema}/{uuid4}/{sanitized_filename}"
3. Backend: s3_client.generate_presigned_post(
       Bucket=bucket,
       Key=s3_key,
       Fields={"Content-Type": content_type},
       Conditions=[
           {"Content-Type": content_type},
           ["content-length-range", 1, 52428800],  # 1 byte to 50MB
       ],
       ExpiresIn=900,  # 15 minutes
   )
4. Backend returns: {url, fields, s3_key}
5. Frontend: builds FormData from fields + file, POSTs to url
6. Frontend: POST /api/documents/ {name, s3_key, content_type, size_bytes}
7. Backend: validates s3_key starts with tenant schema_name, creates Document row
```

**S3 Download Flow:**

```
1. Frontend: GET /api/documents/{id}/download-url
2. Backend: s3_client.generate_presigned_url("get_object", Bucket=bucket, Key=doc.s3_key, ExpiresIn=900)
3. Backend returns: {url}
4. Frontend: redirects browser to presigned URL
```

**S3 Deletion Flow (synchronous with rollback):**

```
1. DELETE /api/documents/{id}
2. Validate: user is admin OR user is the original uploader (uploaded_by)
3. Attempt s3_client.delete_object(Bucket=bucket, Key=doc.s3_key)
4. If S3 delete succeeds: delete Document row from DB, return 200
5. If S3 delete fails: do NOT delete DB row, return 502 with error
6. Create AuditLog entry (on success)
```

**Lambda Invocation Flow:**

```
Summarize Document:
1. POST /api/documents/{id}/summarize
2. Frontend sends document text in request body
3. Backend: lambda_client.invoke(
       FunctionName=LAMBDA_SUMMARIZE_ARN,
       InvocationType="RequestResponse",
       Payload=json.dumps({"text": text, "task_type": "summarize_document"}),
   )
4. Lambda: calls OpenAI, returns {"summary": "..."}
5. Backend: stores summary on Document record, returns {summary}

Generate Tasks:
1. POST /api/workflows/{id}/generate-tasks
2. Backend: sends workflow.description to Lambda with task_type="generate_tasks"
3. Lambda: calls OpenAI, returns {"tasks": [{"title": "...", "description": "..."}, ...]}
4. Backend: creates Task objects (APPENDED to existing tasks, never replaced)
5. Backend: creates AuditLog entry per task
```

### 4.4 File Changes

Every file that will be created or modified in this project:

```
clinic-portal/
├── config/
│   ├── __init__.py                          # NEW — empty
│   ├── settings.py                          # NEW — full Django settings
│   ├── urls.py                              # NEW — tenant URL config
│   ├── urls_public.py                       # NEW — public schema URL config
│   └── wsgi.py                              # NEW — WSGI entry point
├── apps/
│   ├── __init__.py                          # NEW — empty
│   ├── tenants/
│   │   ├── __init__.py                      # NEW — empty
│   │   ├── models.py                        # NEW — Tenant, Domain
│   │   ├── schemas.py                       # NEW — TenantCreateIn, TenantOut, StaffOut, InviteIn
│   │   ├── services.py                      # NEW — provision_tenant()
│   │   ├── api.py                           # NEW — tenant + staff endpoints
│   │   ├── admin.py                         # NEW — register Tenant, Domain
│   │   └── tests.py                         # NEW — tenant + staff tests
│   ├── users/
│   │   ├── __init__.py                      # NEW — empty
│   │   ├── models.py                        # NEW — User(UserProfile)
│   │   ├── schemas.py                       # NEW — LoginIn, RegisterIn, UserOut
│   │   ├── api.py                           # NEW — auth endpoints
│   │   ├── admin.py                         # NEW — register User
│   │   └── tests.py                         # NEW — auth tests
│   ├── dashboard/
│   │   ├── __init__.py                      # NEW — empty
│   │   ├── api.py                           # NEW — stats endpoint
│   │   ├── schemas.py                       # NEW — DashboardStatsOut
│   │   └── tests.py                         # NEW — dashboard tests
│   ├── workflows/
│   │   ├── __init__.py                      # NEW — empty
│   │   ├── models.py                        # NEW — Workflow, Task, AuditLog
│   │   ├── schemas.py                       # NEW — WorkflowIn, WorkflowOut, WorkflowDetailOut
│   │   ├── task_schemas.py                  # NEW — TaskIn, TaskOut, TransitionIn, AssignIn
│   │   ├── services.py                      # NEW — invoke_generate_tasks(), create_tasks_from_ai()
│   │   ├── task_services.py                 # NEW — transition_task(), assign_task()
│   │   ├── api.py                           # NEW — workflow CRUD + generate-tasks
│   │   ├── task_api.py                      # NEW — task CRUD + transition + assign
│   │   ├── admin.py                         # NEW — register Workflow, Task, AuditLog
│   │   ├── tests.py                         # NEW — workflow tests
│   │   └── tests_tasks.py                   # NEW — task tests
│   └── documents/
│       ├── __init__.py                      # NEW — empty
│       ├── models.py                        # NEW — Document
│       ├── schemas.py                       # NEW — UploadUrlIn, DocumentIn, DocumentOut, etc.
│       ├── services.py                      # NEW — S3 presigned URLs, Lambda invocation, S3 deletion
│       ├── api.py                           # NEW — document endpoints
│       ├── admin.py                         # NEW — register Document
│       └── tests.py                         # NEW — document tests (mocked boto3)
├── lambdas/
│   └── summarize/
│       ├── handler.py                       # NEW — Lambda function (OpenAI summarize + generate tasks)
│       └── requirements.txt                 # NEW — openai
├── templates/
│   ├── base.html                            # NEW — base template with nav, tenant context
│   ├── landing.html                         # NEW — public landing page
│   ├── login.html                           # NEW — login form
│   ├── register.html                        # NEW — registration form
│   ├── dashboard.html                       # NEW — stats cards
│   ├── workflows.html                       # NEW — workflow list + create
│   ├── workflow_detail.html                 # NEW — tasks + transitions + AI generate
│   ├── documents.html                       # NEW — upload + list + summarize
│   └── staff.html                           # NEW — staff list + invite + remove (admin only)
├── static/
│   ├── styles.css                           # NEW — custom styles (Pico CSS via CDN)
│   └── app.js                               # NEW — vanilla JS (fetch, DOM updates, upload flow)
├── scripts/
│   ├── create_public_tenant.py              # NEW — management command
│   └── seed_demo.py                         # NEW — seed demo clinic
├── docker-compose.yml                       # NEW — PG + Redis + Django
├── Dockerfile                               # NEW — Django container
├── pyproject.toml                           # NEW — uv dependencies
├── manage.py                                # NEW — Django manage
├── .env.example                             # MODIFIED — add all required vars
└── .gitignore                               # MODIFIED — add .env, __pycache__, *.pyc, data/
```

---

## 5. Implementation Plan

| Step | What | Why This Order | Files |
|------|------|----------------|-------|
| 1 | Docker Compose + project scaffold | Foundation -- nothing runs without PostgreSQL, Redis, and a Django project. Must verify database connectivity before any model work. | `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `manage.py`, `config/__init__.py`, `config/settings.py`, `config/wsgi.py`, `.env.example`, `.gitignore` |
| 2 | Settings + middleware + database config | django-tenants requires specific settings (ENGINE, MIDDLEWARE order, SHARED_APPS, TENANT_APPS, DATABASE_ROUTERS) before any migration can run. Get this right first. | `config/settings.py` (complete), `config/urls.py`, `config/urls_public.py` |
| 3 | Tenant + Domain models | Tenant model must exist before `migrate_schemas --shared` can run. Domain model must exist for subdomain routing. This is the foundation of multi-tenancy. | `apps/tenants/__init__.py`, `apps/tenants/models.py`, `apps/tenants/admin.py` |
| 4 | User model | User model must exist before auth endpoints. `AUTH_USER_MODEL` must be set before first migration. | `apps/users/__init__.py`, `apps/users/models.py`, `apps/users/admin.py` |
| 5 | Run shared migrations + create public tenant | Shared schema tables must exist before any auth or tenant endpoint works. Public tenant is the entry point for all users. | `scripts/create_public_tenant.py`, run `migrate_schemas --shared` |
| 6 | Auth endpoints + tests | Auth must work before any tenant or business logic. Login/register are the gateway to everything else. | `apps/users/schemas.py`, `apps/users/api.py`, `apps/users/tests.py`, `config/urls_public.py` (wire auth router) |
| 7 | Tenant provisioning + staff management + tests | Tenant creation must work before tenant-scoped models can be tested. Staff invite/remove enables multi-user testing. | `apps/tenants/schemas.py`, `apps/tenants/services.py`, `apps/tenants/api.py`, `apps/tenants/tests.py` |
| 8 | Workflow + AuditLog models | Workflow and AuditLog are the core tenant-scoped models. Must exist before tasks or documents can reference them. | `apps/workflows/__init__.py`, `apps/workflows/models.py`, `apps/workflows/admin.py` |
| 9 | Workflow CRUD endpoints + tests | Workflows must be creatable before tasks can be added to them. Tests verify CRUD + role checks. | `apps/workflows/schemas.py`, `apps/workflows/api.py`, `apps/workflows/tests.py`, `config/urls.py` (wire workflow router) |
| 10 | Task CRUD + state machine + assignment + tests | Tasks depend on workflows. State machine is the most complex business logic -- test it thoroughly. | `apps/workflows/task_schemas.py`, `apps/workflows/task_services.py`, `apps/workflows/task_api.py`, `apps/workflows/tests_tasks.py`, `config/urls.py` (wire task router) |
| 11 | Document model + S3 services | Document model depends on Workflow and Task (optional FKs). S3 services are standalone (testable with mocked boto3). | `apps/documents/__init__.py`, `apps/documents/models.py`, `apps/documents/services.py`, `apps/documents/admin.py` |
| 12 | Document endpoints + tests | Upload, register, download, delete, summarize. Tests mock boto3 and Lambda. | `apps/documents/schemas.py`, `apps/documents/api.py`, `apps/documents/tests.py`, `config/urls.py` (wire document router) |
| 13 | Lambda function | Lambda handler is standalone Python. Deploy to AWS after Django endpoints are working. | `lambdas/summarize/handler.py`, `lambdas/summarize/requirements.txt` |
| 14 | Generate-tasks endpoint + Lambda integration | Depends on working Lambda function + existing workflow/task endpoints. | `apps/workflows/services.py` (invoke_generate_tasks, create_tasks_from_ai) |
| 15 | Dashboard stats endpoint + tests | Dashboard reads from all models -- must be built after all models exist and have data. | `apps/dashboard/__init__.py`, `apps/dashboard/schemas.py`, `apps/dashboard/api.py`, `apps/dashboard/tests.py`, `config/urls.py` (wire dashboard router) |
| 16 | Frontend templates | All API endpoints must work before the frontend can consume them. Templates are the last layer. | `templates/*.html`, `static/styles.css`, `static/app.js` |
| 17 | Seed data script | Seeds a demo clinic with realistic data. Must run after all models and migrations are in place. | `scripts/seed_demo.py` |
| 18 | End-to-end manual test + polish | Verify the full flow from registration to AI summarization in a browser. Fix edge cases. | No new files -- fixes across existing files |

---

## 6. Testing Strategy

### Unit Tests (mocked dependencies)

Tests for services with external dependencies (S3, Lambda) use `unittest.mock.patch` to mock boto3 clients.

### Integration Tests (TenantTestCase + PostgreSQL)

All endpoint tests use `django_tenants.test.cases.TenantTestCase`, which:
- Creates a test tenant with a real PostgreSQL schema
- Runs the test within that schema context
- Tears down the schema after the test class completes

### Test Scenarios (minimum 15)

| # | Scenario | Type | Input | Expected | Status Code |
|---|----------|------|-------|----------|-------------|
| 1 | Register new user | Happy path | `{email, password, name}` | User created in shared schema | 201 |
| 2 | Login with valid credentials | Happy path | `{email, password}` | Session cookie set, user info returned | 200 |
| 3 | Login with wrong password | Auth failure | `{email, wrong_password}` | Error message, no session | 401 |
| 4 | Access /me without session | Auth failure | No cookie | "Authentication required" | 401 |
| 5 | Create tenant | Happy path | `{name, subdomain}` | PG schema created, user is admin | 201 |
| 6 | User from Tenant A accesses Tenant B | Tenant isolation | Request to `clinic2.localhost` as clinic1 member | Blocked by TenantAccessMiddleware | 403 |
| 7 | Create workflow (admin) | Happy path | `{name, description}` | Workflow created, AuditLog entry exists | 201 |
| 8 | Create workflow (staff) | Role check | `{name, description}` | "Admin access required" | 403 |
| 9 | Transition task: created -> assigned | Happy path | `{status: "assigned"}` | Status changed, AuditLog with "status_change:created->assigned" | 200 |
| 10 | Transition task: created -> completed (invalid) | State machine violation | `{status: "completed"}` | "Invalid transition from 'created' to 'completed'" | 400 |
| 11 | Transition task: completed -> in_progress (terminal) | State machine violation | `{status: "in_progress"}` | "Invalid transition from 'completed' to 'in_progress'" | 400 |
| 12 | Delete workflow with existing tasks | Business rule | DELETE workflow that has tasks | "Cannot delete workflow with existing tasks" | 400 |
| 13 | Generate presigned upload URL | Happy path | `{filename, content_type}` | URL + fields + s3_key returned, key starts with tenant schema | 200 |
| 14 | Delete document (original uploader) | Happy path | DELETE by uploader | S3 object deleted, DB row deleted | 200 |
| 15 | Delete document (staff, not uploader, not admin) | Auth check | DELETE by non-uploader staff | "Permission denied" | 403 |
| 16 | Lambda failure on summarize | Graceful degradation | Lambda raises ClientError | "AI service unavailable" | 502 |
| 17 | Lambda failure on generate-tasks | Graceful degradation | Lambda timeout | "AI service unavailable" | 502 |
| 18 | Register document with empty filename | Validation | `{name: "", s3_key: "..."}` | Validation error | 422 |
| 19 | Assign task to non-member user | Validation | `{user_id: 999}` (not in tenant) | "User not found in this tenant" | 404 |
| 20 | Dashboard stats: empty tenant | Edge case | No data in tenant | All counts = 0, all 5 status keys present | 200 |

### Mock Strategy

- **boto3 S3 client**: mocked via `unittest.mock.patch("apps.documents.services.s3_client")`. Tests verify the correct bucket, key, and expiry are passed.
- **boto3 Lambda client**: mocked via `unittest.mock.patch("apps.workflows.services.lambda_client")`. Tests return canned responses for both task types.
- **PostgreSQL**: NOT mocked. Real database via Docker. `TenantTestCase` handles schema creation/teardown.

---

## 7. Observability

### Logging Points

Applied to this project's specific mutation and integration points:

| # | What to Log | Level | Where | Example Message |
|---|------------|-------|-------|-----------------|
| 1 | Every API request | INFO | Middleware or Django Ninja exception handler | `INFO request: POST /api/workflows/ user=5 tenant=sunrise_clinic` |
| 2 | Auth events (login, logout, register) | INFO | `apps/users/api.py` | `INFO auth.login: user=5 email=admin@clinic.com` |
| 3 | Auth failures (bad password, no session) | WARNING | `apps/users/api.py` | `WARNING auth.login_failed: email=admin@clinic.com reason=invalid_password` |
| 4 | Tenant provisioning | INFO | `apps/tenants/services.py` | `INFO tenant.created: schema=sunrise_clinic name=Sunrise Clinic owner=5` |
| 5 | State machine transitions | INFO | `apps/workflows/task_services.py` | `INFO task.transition: task=12 created->assigned user=5 tenant=sunrise_clinic` |
| 6 | Invalid transition attempts | WARNING | `apps/workflows/task_services.py` | `WARNING task.invalid_transition: task=12 completed->in_progress user=5` |
| 7 | S3 presigned URL generation | INFO | `apps/documents/services.py` | `INFO s3.presigned_post: key=sunrise_clinic/uuid/report.pdf expires=900s` |
| 8 | S3 deletion success/failure | INFO/ERROR | `apps/documents/services.py` | `ERROR s3.delete_failed: key=sunrise_clinic/uuid/report.pdf error=AccessDenied` |
| 9 | Lambda invocation (request + response) | INFO | `apps/workflows/services.py`, `apps/documents/services.py` | `INFO lambda.invoke: function=summarize task_type=generate_tasks` |
| 10 | Lambda failure (timeout, error) | ERROR | Same as above | `ERROR lambda.failed: function=summarize error=ReadTimeoutError elapsed=30s` |

### Log Format

```python
LOGGING = {
    "version": 1,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
```

### Metrics (via AuditLog queries)

- Tasks completed per day per tenant (query AuditLog where `action` starts with `status_change:` and ends with `->completed`)
- Workflows created per week
- Documents uploaded per day
- Lambda invocation count and failure rate (logged, queryable from logs)
- Active tenants (tenants with any AuditLog entry in last 7 days)

---

## 8. Future Considerations

### Potential Enhancements

1. **HIPAA Compliance Path**: Add encryption at rest (PostgreSQL TDE or column-level), access logging to S3 (S3 server access logs), BAA with AWS, PHI-aware audit log with retention policies, user consent tracking.

2. **Celery for Background Processing**: Replace synchronous Lambda invocation with Celery tasks for long-running operations. Add a Redis-backed Celery worker process. Enables retry logic, task chaining, and progress tracking via WebSocket.

3. **PDF Text Extraction**: Add a Lambda preprocessing step that extracts text from PDF documents before summarization. Use `pdfplumber` or `PyPDF2` in a Lambda layer. Enables summarization of non-text documents.

4. **Real-Time Notifications**: Add Django Channels with WebSocket support for live task status updates, new document notifications, and AI completion alerts. Requires Redis as the channel layer backend (already present).

5. **Role Hierarchy**: Extend beyond admin/staff to include viewer (read-only), manager (can assign but not create), and owner (can delete tenant). Requires per-tenant role storage rather than a single `role` field on User.

### Known Limitations

1. **No Pagination**: List endpoints return all results. Will become slow at scale. Add cursor-based pagination in a follow-up.
2. **Single Lambda Function**: Both summarization and task generation share one Lambda. Should be split for independent scaling and error isolation.
3. **No Rate Limiting**: Login and Lambda endpoints are unprotected against abuse. Add Django ratelimit middleware.
4. **No Email**: Staff invitation does not send email -- the invited user must already have an account. Add email via SES in a follow-up.
5. **Schema Migration at Scale**: `migrate_schemas --tenant` runs migrations sequentially across all schemas. At 100+ tenants, this is slow. Solution: parallel migration with `--executor=parallel` flag (django-tenants supports this).

---

## 9. Dependencies

### Python Packages (via uv)

| Package | Version | Purpose |
|---------|---------|---------|
| django | >=5.0 | Web framework |
| django-ninja | >=1.0 | REST API layer (Pydantic schemas) |
| django-tenants | >=3.6 | Multi-tenancy (schema-per-tenant) |
| django-tenant-users | >=1.0 | Global auth + per-tenant membership |
| psycopg2-binary | >=2.9 | PostgreSQL adapter |
| redis | >=5.0 | Redis client for sessions + cache |
| boto3 | >=1.34 | AWS SDK (S3 + Lambda) |
| python-dotenv | >=1.0 | Load .env variables |

### Development Dependencies

| Package | Purpose |
|---------|---------|
| black | Code formatter (line-length 100) |
| ruff | Linter (rules E, F, I, UP, B) |
| pytest | Test runner (via Django's test command) |
| pytest-django | Django test integration |

### Docker Images

| Image | Purpose |
|-------|---------|
| postgres:15 | Database (required by django-tenants) |
| redis:7-alpine | Session backend + cache |

### AWS Services

| Service | Resource | Purpose |
|---------|----------|---------|
| S3 | `clinic-portal-docs` bucket | Document storage (SSE-S3, block public access) |
| Lambda | `clinic-portal-summarize` function | LLM endpoint (Python 3.12, 256MB, 30s timeout) |
| IAM | `clinic-portal-app` user | Credentials for S3 + Lambda access |

### Lambda Dependencies (separate from Django)

| Package | Purpose |
|---------|---------|
| openai | OpenAI API client for summarization + task generation |

---

## 10. Security Considerations

### Authentication

- Django session auth backed by Redis. Session cookie is `httponly`, `samesite=Lax`, and `secure=True` in production.
- `authenticate()` + `login()` from Django contrib -- no custom password handling.
- Password validation: minimum 8 characters via Django's built-in validators.
- Email is case-insensitive: normalized to lowercase before storage.
- Logout destroys the Redis session immediately (no lingering tokens).

### Authorization

- **Role-based**: Admin can create/update/delete workflows, create/update tasks, assign tasks, invite/remove staff. Staff can read all resources and transition any task status.
- **TenantAccessMiddleware**: Blocks any user from accessing a tenant they are not a member of. Returns 403 before the request reaches any endpoint.
- **Document deletion**: Only admin OR original uploader can delete. Enforced at the endpoint level.
- **Superadmin**: Only superadmin can list all tenants. Checked via `request.user.is_superuser`.

### Tenant Isolation

- **Database**: Schema-per-tenant via django-tenants. `TenantMainMiddleware` sets `search_path` per request. ORM queries cannot cross schema boundaries.
- **S3**: Object keys namespaced by tenant schema name (`{schema}/{uuid}/{filename}`). Presigned URLs are generated per-request with the current tenant's context. A user cannot generate a presigned URL for another tenant's file.
- **Redis**: Cache keys prefixed with tenant schema name via `django_tenants.cache.make_key`. Sessions are not tenant-scoped (a user's session works across tenants they belong to), but `TenantAccessMiddleware` blocks unauthorized access.
- **URL Routing**: Subdomains map to tenants via Domain model. `PUBLIC_SCHEMA_URLCONF` routes public endpoints. `ROOT_URLCONF` routes tenant endpoints. There is no URL path that leaks across tenants.

### S3 Security

- Bucket policy: block all public access. No public reads, no public writes.
- Presigned URLs expire after 15 minutes (900 seconds).
- Presigned POST conditions enforce content type and max size (50MB).
- Server-side encryption: SSE-S3 (AES-256) on all objects.
- S3 key validation on document registration: the `s3_key` in the POST body must start with the current tenant's `schema_name`. Prevents cross-tenant document registration.

### Secrets Management

- All secrets stored in `.env` file (excluded from git via `.gitignore`).
- `.env.example` provided with placeholder values.
- Secrets loaded via `os.environ` or `python-dotenv`.
- Never hardcoded in source: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`, `LAMBDA_SUMMARIZE_ARN`, `OPENAI_API_KEY`, `DB_PASSWORD`, `DJANGO_SECRET_KEY`.

### CSRF Protection

- Django's CSRF middleware is enabled for all mutating endpoints.
- Django Ninja handles CSRF token validation when session auth is used.
- Templates include `{% csrf_token %}` in all forms.
- JavaScript `fetch()` calls include the CSRF token from the cookie.

### Audit Trail

- AuditLog records every mutation: who, what, when, which entity, what changed.
- AuditLog is append-only: no update or delete endpoints exist.
- `entity_id` is an IntegerField (not a FK) so audit entries survive entity deletion.
- `performed_by` uses `SET_NULL` on delete so audit entries survive user deletion.
- Logged actions: `created`, `updated`, `deleted`, `status_change:{old}->{new}`, `assigned:{user_id}`, `invited:{email}`, `removed:{user_id}`, `uploaded`, `summarized`.

### CORS

- Not configured (server-rendered app, no cross-origin requests needed).
- S3 bucket CORS must be configured to allow uploads from the app's domain (`portal.localhost`, `*.localhost`).

---

*Document complete. All 10 sections written. Ready for implementation.*
