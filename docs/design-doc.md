# Multi-Tenant Clinic Management Portal — Design Document

**Date:** 2026-03-30
**Proposal:** docs/proposals/01-clinic-portal-mvp.md
**Status:** Draft

---

## 1. Current Context

### Existing System
Greenfield project. No existing code beyond project configuration (CLAUDE.md, SPEC.md, pyproject.toml stub). The repository has a `.venv` directory and `uv.lock` but no Django project scaffold, no models, no API endpoints.

### Gap Being Addressed
Small/medium medical clinics lack affordable tools for internal business process management. Current solutions (spreadsheets, Trello, Notion) offer no data isolation between organizations, no audit trails, and no domain-specific AI assistance. This project provides each clinic with an isolated workspace featuring workflow automation, document management, and AI-powered summarization.

### Scope
- **Users:** Clinic administrators (create/manage workflows), clinic staff (execute tasks), platform superadmins (monitor tenants)
- **Scale:** MVP targets 10-50 tenants, designed to scale to 100+ without architecture changes
- **Deployment:** Local development via Docker Compose (PostgreSQL + Redis + Django)
- **Tenancy:** Full schema-per-tenant isolation via django-tenants

---

## 2. Requirements

### Functional Requirements
1. Clinic signup creates an isolated tenant with its own PostgreSQL schema and subdomain
2. Session-based authentication with Redis backend; users are global, tenant access is granted
3. Role-based access: admin (full CRUD) and staff (read + task operations)
4. Workflow CRUD with task management via enforced state machine (created → assigned → in_progress → completed/cancelled)
5. AuditLog tracks every state mutation with entity type, action, performer, and timestamp
6. Document upload/download via S3 presigned URLs with tenant-namespaced keys
7. AI document summarization and task generation via Lambda invocation
8. Dashboard with aggregate statistics per tenant
9. Staff management: add by email, remove (admin only)
10. Superadmin can list all tenants

### Non-Functional Requirements
- **Performance:** API responses < 500ms for CRUD operations; Lambda summarization < 30s
- **Scalability:** Schema-per-tenant supports 100+ tenants; stateless Django for horizontal scaling
- **Observability:** Structured logging at 10 required points (see Section 7); AuditLog for all mutations
- **Security:** CSRF on all mutating endpoints; session cookie hardening; tenant isolation at DB/S3/Redis layers; presigned URLs expire in 15 minutes
- **Reliability:** Graceful degradation when AWS credentials missing; Lambda timeout handling; transaction-wrapped mutations

---

## 3. Design Decisions

### Decision 1: Schema-per-Tenant via django-tenants
Will implement PostgreSQL schema-per-tenant isolation because:
- Complete data isolation at the database level — a queryset bug cannot leak data across tenants
- Tenant deletion is `DROP SCHEMA CASCADE` — clean and atomic
- No application-level filtering required (no `WHERE tenant_id = X` everywhere)
- Trade-off: Migration complexity grows linearly with tenant count; must run `migrate_schemas` across all schemas
- Alternative considered: Row-level tenancy with `tenant_id` FK on every model — simpler migrations but weaker isolation; a single missing filter leaks all tenant data

### Decision 2: Django Ninja for API Layer
Will implement Django Ninja with Pydantic Schema classes because:
- Native Python type hints for request/response validation — catches input errors automatically
- Faster request handling than DRF; less boilerplate
- Mandatory per SPEC.md and CLAUDE.md Rule 1
- Trade-off: Smaller ecosystem and community than DRF; fewer third-party packages
- Alternative considered: Django REST Framework — more mature, larger ecosystem, but explicitly prohibited by project rules

### Decision 3: Session Auth via Redis (not JWT)
Will implement session-based authentication with Redis backend because:
- Django's built-in session framework handles creation, rotation, and expiry
- Redis provides sub-millisecond session lookups
- Session cookies are `HttpOnly` — not accessible to JavaScript (XSS-resistant)
- Tenant-aware session keys via `django_tenants.cache.make_key` prevent cross-tenant session collision
- Trade-off: Requires Redis as a runtime dependency; sessions are server-stateful
- Alternative considered: JWT tokens — stateless, no Redis needed, but token revocation is complex, tokens are accessible to JS, and JWTs require custom tenant-scoping

### Decision 4: S3 Presigned URLs (never proxy uploads)
Will implement presigned URL upload/download pattern because:
- Django never handles file bytes — keeps the server stateless and lightweight
- S3 handles storage, encryption (AES-256), and bandwidth
- Keys namespaced by `{tenant_schema_name}/{uuid}/{filename}` enforce tenant isolation at the storage layer
- 15-minute expiry limits the window for URL leakage
- Trade-off: Two-step upload flow (get URL, then upload) adds client complexity
- Alternative considered: Server-proxied uploads via Django — simpler client flow but creates a bandwidth bottleneck, requires large request body limits, and Django becomes stateful for file content

### Decision 5: Lambda for LLM Integration (not direct OpenAI)
Will implement AWS Lambda as the sole LLM integration point because:
- Django never holds OpenAI API keys — isolates cost, rate limiting, and credential rotation
- Lambda scales independently; cold starts acceptable for user-triggered summarization
- Synchronous invocation (RequestResponse) is appropriate since users explicitly trigger and expect to wait
- Trade-off: Added AWS dependency; Lambda cold starts add 1-3s latency on first call
- Alternative considered: Direct OpenAI SDK calls from Django — simpler architecture but puts API keys in the web server, creates tight coupling, and makes provider switching harder

### Decision 6: Django Templates + Vanilla JS (no SPA)
Will implement server-rendered templates with Pico CSS because:
- No build step, no Node dependency, simpler deployment
- Staff-facing internal tool — does not need SPA-level interactivity
- Progressive enhancement with vanilla JS `fetch()` for interactive operations (upload, state transitions)
- Trade-off: Less responsive UI for complex interactions; full page reloads for navigation
- Alternative considered: React SPA — richer interactivity but adds build toolchain, Node dependency, and doubles the codebase; explicitly prohibited by SPEC

### Decision 7: CSRF Enabled on Django Ninja
Will implement `NinjaAPI(csrf=True)` because:
- Django Ninja disables CSRF by default, but we use session-based auth from templates
- Without CSRF, any cross-site form submission can execute actions as the authenticated user
- Frontend JS must include CSRF token in `X-CSRFToken` header for all mutating requests
- Trade-off: Slightly more complex frontend JS (must read and send CSRF token)
- Alternative considered: Leave CSRF disabled and rely on SameSite cookies only — insufficient protection for older browsers; violates defense-in-depth principle

### Decision 8: AuditLog in Tenant Schema with INSERT-Only
Will implement AuditLog as a tenant-scoped model with write-only access because:
- Every tenant's audit trail is isolated within their schema
- `INSERT`-only DB permissions prevent admin users from tampering with audit records
- JSON `details` field stores old/new values for field-level change tracking
- Trade-off: Audit table grows linearly with mutations; needs eventual partitioning
- Alternative considered: Centralized audit in public schema — simpler querying across tenants but breaks isolation principle; a tenant admin could never see their own audit data without cross-schema access

### Decision 9: Tenant User Provisioning via django-tenant-users
Will implement `provision_tenant()` and `tenant.add_user()` from django-tenant-users because:
- Users are global (public schema); tenant access is a separate relationship
- A single user can belong to multiple tenants (supports consultants, multi-clinic staff)
- `TenantAccessMiddleware` automatically blocks unauthorized tenant access
- Trade-off: Global user table means user email uniqueness is system-wide, not per-tenant
- Alternative considered: Per-tenant user tables — allows duplicate emails across tenants but breaks the global identity model needed for multi-tenant membership

### Decision 10: Temporary Password with Server-Side Forced Reset
Will implement temporary password flow with `must_reset_password` middleware enforcement because:
- SPEC excludes email/SMS — cannot send invite links via email
- Admin provides email + name; system generates temporary password shown once
- Middleware intercepts all requests from users with `must_reset_password=True` and redirects to reset endpoint
- Trade-off: Admin must securely communicate temporary password to staff (out-of-band)
- Alternative considered: Email-based invite links — better security but explicitly excluded by SPEC constraints; one-time signed URLs could work but still need a delivery channel

---

## 4. Technical Design

### Core Components

```
config/
├── settings.py          # Django settings: DB, middleware, apps, cache, S3, Lambda config
├── urls.py              # Tenant-specific URL routing (mounts NinjaAPI)
├── urls_public.py       # Public schema URLs (landing, signup)
└── wsgi.py

apps/
├── tenants/             # SHARED — Tenant + Domain models, provisioning
│   ├── models.py        # Tenant(TenantBase), Domain(DomainMixin)
│   ├── api.py           # POST /api/tenants/ (signup), GET /api/tenants/ (superadmin)
│   └── services.py      # provision_tenant() wrapper
│
├── users/               # SHARED — Global user model, auth
│   ├── models.py        # User(UserProfile) with name, role, must_reset_password
��   ├── api.py           # /api/auth/ (login, register, logout, me), /api/staff/
│   └── services.py      # Staff invite logic, password generation
│
├── workflows/           # TENANT — Business process automation
│   ├── models.py        # Workflow, Task (state machine), AuditLog
│   ├── api.py           # /api/workflows/, /api/tasks/
│   └── services.py      # State machine validation, audit logging
│
├── documents/           # TENANT — S3 file management
│   ���── models.py        # Document (s3_key, summary, FKs)
│   ├── api.py           # /api/documents/ (upload-url, register, download-url, summarize)
│   ���── services.py      # S3 presigned URL generation, Lambda invocation
│
└── dashboard/           # TENANT — Statistics
    ├── api.py           # GET /api/dashboard/stats
    └── templates/       # Dashboard template
```

### Data Models

```python
# apps/tenants/models.py (SHARED)
class Tenant(TenantBase):
    name: CharField(max_length=100)
    created_at: DateTimeField(auto_now_add=True)
    auto_create_schema = True

class Domain(DomainMixin):
    pass  # Maps hostname → tenant, supports is_primary

# apps/users/models.py (SHARED)
class User(UserProfile):
    name: CharField(max_length=150)
    role: CharField(choices=["admin", "staff"], default="staff")
    must_reset_password: BooleanField(default=False)

# apps/workflows/models.py (TENANT)
class Workflow(Model):
    name: CharField(max_length=200)
    description: TextField(blank=True)
    created_by: FK(User, on_delete=CASCADE)
    created_at: DateTimeField(auto_now_add=True)
    modified_at: DateTimeField(auto_now=True)

class Task(Model):
    VALID_TRANSITIONS = {
        "created": ["assigned", "cancelled"],
        "assigned": ["in_progress", "cancelled"],
        "in_progress": ["completed", "cancelled"],
        "completed": [],
        "cancelled": [],
    }
    workflow: FK(Workflow, related_name="tasks", on_delete=CASCADE)
    title: CharField(max_length=300)
    description: TextField(blank=True)
    status: CharField(choices=[...], default="created")
    assigned_to: FK(User, null=True, blank=True, on_delete=SET_NULL)
    created_by: FK(User, on_delete=CASCADE)
    due_date: DateTimeField(null=True, blank=True)
    created_at: DateTimeField(auto_now_add=True)
    modified_at: DateTimeField(auto_now=True)

class AuditLog(Model):
    entity_type: CharField(max_length=50)
    entity_id: IntegerField()
    action: CharField(max_length=200)
    details: JSONField(default=dict, blank=True)
    performed_by: FK(User, null=True, on_delete=SET_NULL)
    timestamp: DateTimeField(auto_now_add=True)

# apps/documents/models.py (TENANT)
class Document(Model):
    name: CharField(max_length=300)
    s3_key: CharField(max_length=500)
    content_type: CharField(max_length=100)
    size_bytes: IntegerField()
    summary: TextField(blank=True)
    workflow: FK(Workflow, null=True, blank=True, on_delete=SET_NULL)
    task: FK(Task, null=True, blank=True, on_delete=SET_NULL)
    uploaded_by: FK(User, on_delete=CASCADE)
    created_at: DateTimeField(auto_now_add=True)
```

### Integration Points

| Component A | Component B | Mechanism |
|---|---|---|
| Browser | Django | HTTP (subdomain routing via TenantMainMiddleware) |
| Browser | S3 | HTTP (presigned PUT/GET, direct upload/download) |
| Django | PostgreSQL | `django_tenants.postgresql_backend` (search_path per request) |
| Django | Redis | `django.core.cache.backends.redis.RedisCache` with `make_key` |
| Django | S3 | boto3 (presigned URL generation only — never file content) |
| Django | Lambda | `boto3.client("lambda").invoke()` (sync, RequestResponse) |
| Lambda | S3 | boto3 (reads document for summarization) |
| Lambda | OpenAI | openai SDK (internal to Lambda, key in Lambda env vars) |

### File Changes

**New files to create:**
- `Dockerfile` — Python 3.12 + uv base image
- `docker-compose.yml` — PG 15, Redis 7, Django web service
- `pyproject.toml` — All dependencies (django, django-ninja, django-tenants, django-tenant-users, boto3, redis, python-dotenv)
- `manage.py` — Django management script
- `config/__init__.py`, `config/settings.py`, `config/urls.py`, `config/urls_public.py`, `config/wsgi.py`
- `apps/__init__.py`
- `apps/tenants/__init__.py`, `apps/tenants/models.py`, `apps/tenants/api.py`, `apps/tenants/services.py`, `apps/tenants/apps.py`
- `apps/users/__init__.py`, `apps/users/models.py`, `apps/users/api.py`, `apps/users/services.py`, `apps/users/apps.py`
- `apps/workflows/__init__.py`, `apps/workflows/models.py`, `apps/workflows/api.py`, `apps/workflows/services.py`, `apps/workflows/apps.py`
- `apps/documents/__init__.py`, `apps/documents/models.py`, `apps/documents/api.py`, `apps/documents/services.py`, `apps/documents/apps.py`
- `apps/dashboard/__init__.py`, `apps/dashboard/api.py`, `apps/dashboard/apps.py`
- `templates/base.html`, `templates/landing.html`, `templates/login.html`, `templates/register.html`, `templates/dashboard.html`, `templates/workflows.html`, `templates/documents.html`
- `static/styles.css`, `static/app.js`
- `scripts/create_public_tenant.py` — Management command
- `scripts/seed_demo.py` — Demo data seeder
- `lambdas/summarize/handler.py`, `lambdas/summarize/requirements.txt`
- `.env.example` — Template for required environment variables
- `.gitignore` — Exclude .env, __pycache__, .venv, etc.

---

## 5. Implementation Plan

| Step | What | Why This Order | Files |
|------|------|---------------|-------|
| 1 | Docker Compose + pyproject.toml + uv setup | Infrastructure must exist before any Django code | `docker-compose.yml`, `Dockerfile`, `pyproject.toml` |
| 2 | Django project scaffold + settings.py | Framework config needed before any app | `config/settings.py`, `config/urls.py`, `config/urls_public.py`, `config/wsgi.py`, `manage.py` |
| 3 | Tenant + Domain models + migrations | Schema-per-tenant is the foundation everything builds on | `apps/tenants/models.py`, `apps/tenants/apps.py` |
| 4 | User model (UserProfile) + migrations | Auth system needed before any API endpoint | `apps/users/models.py`, `apps/users/apps.py` |
| 5 | create_public_tenant management command | Public tenant required for any subdomain routing to work | `scripts/create_public_tenant.py` |
| 6 | Auth API (login, register, logout, me) | Authentication required before any protected endpoint | `apps/users/api.py`, `config/urls.py` |
| 7 | Tenant signup API | New tenants can be created after auth exists | `apps/tenants/api.py`, `apps/tenants/services.py` |
| 8 | Staff management API | Depends on auth + tenant models | `apps/users/api.py`, `apps/users/services.py` |
| 9 | AuditLog model | Must exist before workflows/tasks to log their mutations | `apps/workflows/models.py` (AuditLog class) |
| 10 | Workflow model + CRUD API | Core business logic; depends on tenant schema + audit | `apps/workflows/models.py`, `apps/workflows/api.py` |
| 11 | Task model + state machine + API | Depends on Workflow model; includes transition validation | `apps/workflows/models.py`, `apps/workflows/api.py`, `apps/workflows/services.py` |
| 12 | Document model + S3 presigned URLs | Depends on tenant schema; S3 integration | `apps/documents/models.py`, `apps/documents/api.py`, `apps/documents/services.py` |
| 13 | Lambda function (summarize + generate-tasks) | Must be deployed before Django can invoke it | `lambdas/summarize/handler.py` |
| 14 | Lambda invocation endpoints | Depends on Document model + Lambda deployment | `apps/documents/api.py`, `apps/workflows/api.py` |
| 15 | Dashboard stats API | Depends on all models existing to aggregate | `apps/dashboard/api.py` |
| 16 | Base template + Pico CSS | Foundation for all frontend pages | `templates/base.html`, `static/styles.css` |
| 17 | Landing + Login + Register pages | Public-facing pages; depend on auth API | `templates/landing.html`, `templates/login.html`, `templates/register.html` |
| 18 | Dashboard + Workflows + Documents pages | Tenant pages; depend on all APIs | `templates/dashboard.html`, `templates/workflows.html`, `templates/documents.html` |
| 19 | Seed demo data script | Depends on all models and commands existing | `scripts/seed_demo.py` |

---

## 6. Testing Strategy

| # | Scenario | Input | Expected | Pass Condition |
|---|----------|-------|----------|----------------|
| 1 | Create tenant via signup | `{name: "Sunrise Clinic", subdomain: "sunrise", email, password}` | 201, tenant created with own schema | Tenant exists in DB, schema created, domain record exists |
| 2 | Login with valid credentials | `{email, password}` | 200, session cookie set | Session exists in Redis, `request.user.is_authenticated` |
| 3 | Login with wrong password | `{email, wrong_password}` | 401, no session | No session created, error message returned |
| 4 | Access tenant without membership | Authenticated user on wrong subdomain | 403 | TenantAccessMiddleware blocks request |
| 5 | Create workflow (admin) | `{name: "Patient Intake", description: "..."}` | 201, workflow created | Workflow in tenant schema, AuditLog entry exists |
| 6 | Create workflow (staff — forbidden) | Same as above but staff role | 403 | No workflow created |
| 7 | Valid task transition (created → assigned) | `{new_status: "assigned"}` on created task | 200, status updated | Task.status == "assigned", AuditLog records transition |
| 8 | Invalid task transition (created → completed) | `{new_status: "completed"}` on created task | 400, transition rejected | Task.status unchanged, error message explains valid transitions |
| 9 | Transition from terminal state (completed → anything) | `{new_status: "assigned"}` on completed task | 400, transition rejected | Task.status unchanged |
| 10 | Generate presigned upload URL | `{filename: "report.pdf", content_type: "application/pdf"}` | 200, presigned URL with tenant-namespaced key | URL contains `{schema_name}/{uuid}/report.pdf`, expires in 900s |
| 11 | Register document after upload | `{s3_key, name, content_type, size_bytes}` | 201, document created | Document in tenant schema, AuditLog entry |
| 12 | Generate presigned download URL | GET on existing document | 200, presigned GET URL | URL expires in 900s, key matches document.s3_key |
| 13 | Delete document | DELETE on existing document | 200, document removed | DB record deleted, S3 object deleted, AuditLog entry |
| 14 | Summarize document (Lambda invocation) | POST on document with uploaded status | 200, summary saved | Document.summary populated, AuditLog entry |
| 15 | Lambda invocation failure (graceful degradation) | POST summarize with invalid Lambda ARN | 500 with clear error | Error message returned, document unchanged, no crash |
| 16 | Dashboard stats accuracy | Tenant with 3 workflows, 10 tasks (mixed statuses), 5 docs | 200, accurate counts | Each count matches actual DB state |
| 17 | Staff invite (admin) | `{email, name}` | 201, user created with temp password | User exists, linked to tenant, must_reset_password=True |
| 18 | Staff invite (staff — forbidden) | Same as above but staff role | 403 | No user created |
| 19 | Cross-tenant data isolation | Query tasks from tenant A while on tenant B | Empty result | Tenant B sees zero tasks from tenant A |
| 20 | CSRF protection on mutating endpoint | POST without CSRF token | 403 | Request rejected |
| 21 | Empty input validation | `{name: ""}` on workflow create | 422, validation error | Pydantic schema rejects empty required field |
| 22 | Assign task to user | `{assigned_to: user_id}` on created task | 200, task assigned | Task.assigned_to set, status transitions to assigned |
| 23 | Superadmin list tenants | GET /api/tenants/ as superadmin | 200, list of all tenants | Returns all tenants; non-superadmin gets 403 |
| 24 | Generate tasks via Lambda | POST /api/workflows/{id}/generate-tasks | 200, tasks created | Task records created from Lambda response |
| 25 | Delete workflow with tasks | DELETE on workflow with child tasks | 200, cascade delete | Workflow + tasks deleted, AuditLog entries for each |

---

## 7. Observability

### 10 Required Logging Points

| # | Category | What to Log | Level |
|---|----------|------------|-------|
| 1 | Function entry/exit | API endpoint invocations with method, path, tenant | INFO |
| 2 | Errors | Exception type, message, traceback, tenant context | ERROR |
| 3 | External API calls | S3 presigned URL generation, Lambda invocations (function ARN, duration) | INFO |
| 4 | State mutations | Task transitions (old → new), document uploads, workflow CRUD | INFO |
| 5 | Security events | Login success/failure, logout, tenant access denied, CSRF rejection | WARNING |
| 6 | Business milestones | Tenant created, workflow completed (all tasks done), first document uploaded | INFO |
| 7 | Performance anomalies | Lambda response > 10s, DB query > 1s | WARNING |
| 8 | Configuration changes | Settings loaded, tenant provisioned, user role changed | INFO |
| 9 | Validation failures | Invalid state transitions, Pydantic validation errors, auth failures | WARNING |
| 10 | Resource limits | S3 upload size exceeded, Lambda payload too large | ERROR |

### Never Log
- Passwords (plain or hashed)
- Session tokens or cookies
- AWS access keys or secret keys
- OpenAI API keys
- Full document content (only metadata)

---

## 8. Future Considerations

### Potential Enhancements
- Async Lambda invocation with polling endpoint for long-running summarizations
- Workflow templates marketplace (pre-built templates shared across tenants)
- PII detection on document upload (flag documents containing patient data)
- Multi-model AI support (swap OpenAI for Anthropic/local models)
- Tenant deactivation/archival workflow
- Export audit logs to CloudWatch or S3 for tamper-proof retention

### Known Limitations
- Synchronous Lambda invocation may be slow for large documents (accepted for MVP)
- Schema-per-tenant migration time grows linearly with tenant count (acceptable up to ~100 tenants)
- No email delivery — staff must receive temporary passwords out-of-band
- Single-region deployment (us-east-1) — no multi-region failover
- No rate limiting on API endpoints (should be added before production)

---

## 9. Dependencies

### Development Dependencies
- Python 3.12 (via uv)
- Django 5.x
- django-ninja >= 1.0
- django-tenants >= 3.6
- django-tenant-users >= 1.0
- boto3 >= 1.34
- redis >= 5.0
- python-dotenv >= 1.0
- black (formatting)
- ruff (linting)

### External Services
- PostgreSQL 15+ (Docker: `postgres:15`)
- Redis 7 (Docker: `redis:7-alpine`)
- AWS S3 (bucket with SSE-S3, block public access)
- AWS Lambda (Python 3.12 runtime, 256MB, 30s timeout)
- AWS IAM (app user with S3 + Lambda permissions)

---

## 10. Security Considerations

- **Authentication:** Session-based auth via Redis. Login rotates session ID (Django default). Logout invalidates session. `SESSION_COOKIE_SECURE=True`, `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`.

- **Authorization:** Role-based (admin/staff) enforced per endpoint via Django Ninja dependency. Superadmin identified by `is_superuser=True`, restricted to public schema context. `TenantAccessMiddleware` verifies user-tenant membership on every request.

- **CSRF Protection:** `NinjaAPI(csrf=True)` explicitly enabled. Frontend JS reads CSRF token from cookie and sends in `X-CSRFToken` header. `CSRF_TRUSTED_ORIGINS` includes all active tenant subdomains.

- **Tenant Isolation:**
  - Database: PostgreSQL `SET search_path` per request via TenantMainMiddleware — no cross-schema queries possible
  - Storage: S3 keys prefixed with `{tenant_schema_name}/` — constructed server-side only, never from client input
  - Cache: Redis keys prefixed with tenant schema via `django_tenants.cache.make_key`
  - Auth: `TenantAccessMiddleware` blocks users from accessing tenants they don't belong to

- **Data Protection:** Presigned URLs expire after 15 minutes. S3 bucket blocks all public access. Server-side encryption (AES-256). `Content-Disposition: attachment` on download URLs. No file content ever passes through Django.

- **Audit Trail:** AuditLog model in tenant schema tracks every mutation: entity_type, entity_id, action, details (JSON with old/new values), performed_by, timestamp. No silent changes. DB role should be INSERT-only for the audit table.

- **Secrets Management:** All credentials in `.env` file. `.env` in `.gitignore`. `.env.example` template provided. `ALLOWED_HOSTS` synced with active tenant domains. Lambda env vars for OpenAI key.

- **Input Validation:** All API inputs validated via Django Ninja Pydantic schemas. Write schemas explicitly list allowed fields — privilege fields (role, is_superuser) excluded. Lambda responses sanitized before storage (strip HTML tags).

- **Password Security:** Staff invited with temporary passwords. `must_reset_password` flag enforced by server-side middleware (not client-side redirect). Temporary passwords should expire after 24 hours.

- **Security Headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. CSP configured to restrict script sources.
