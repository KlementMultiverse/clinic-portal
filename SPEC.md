# Multi-Tenant Clinic Management Portal — Build Spec

## Overview

Build a multi-tenant SaaS management portal where medical clinics sign up and get their own private workspace. Each clinic's data is fully isolated via PostgreSQL schema-per-tenant. The portal demonstrates **internal business process automation** for health-tech: clinics manage tasks/workflows with AI-powered summarization.

## Tech Stack (mandatory — use ALL)

| Technology | Purpose |
|---|---|
| Python 3.12 | Runtime |
| Django 5.x | Web framework |
| Django Ninja | REST API layer (not DRF) |
| django-tenants | Multi-tenancy via PostgreSQL schemas |
| django-tenant-users | Global auth + per-tenant permissions |
| PostgreSQL 15+ | Database (required by django-tenants) |
| Redis | Session backend + tenant-aware caching |
| AWS S3 | Document/file storage via presigned URLs |
| AWS Lambda | LLM-powered summarization endpoint |
| Docker + Docker Compose | Containerized local dev (PG + Redis + Django) |
| uv | Python package manager |
| LLM module | OpenAI API (called from Lambda) for text summarization |

## Architecture

### Project Structure

```
clinic-portal/
├── docker-compose.yml          # PG + Redis + Django
├── Dockerfile
├── pyproject.toml              # uv
├── uv.lock
├── manage.py
├── config/
│   ├── settings.py             # Django settings (shared/tenant apps, middleware, etc.)
│   ├── urls.py                 # Tenant-specific URL routing
│   ├── urls_public.py          # Public schema URLs (landing, signup)
│   └── wsgi.py
├── apps/
│   ├── tenants/                # SHARED — Tenant + Domain models, signup logic
│   │   ├── models.py
│   │   ├── api.py              # Django Ninja routes: create tenant, list tenants (superadmin)
│   │   └── services.py         # Tenant provisioning logic
│   ├── users/                  # SHARED — Global user model (extends UserProfile)
│   │   ├── models.py
│   │   ├── api.py              # Login, register, invite staff, me endpoint
│   │   └── services.py
│   ├── dashboard/              # TENANT — Main dashboard view
│   │   ├── api.py              # Dashboard stats endpoint
│   │   └── templates/
│   ├── workflows/              # TENANT — Business process automation
│   │   ├── models.py           # Workflow, Task, AuditLog models
│   │   ├── api.py              # CRUD + state transitions + assign
│   │   ├── services.py         # State machine logic, validation
│   │   └── templates/
│   └── documents/              # TENANT — S3 file management
│       ├── models.py           # Document model (S3 key, metadata)
│       ├── api.py              # Upload (presigned URL), download, list, summarize
│       └── services.py         # S3 presigned URL generation, Lambda invocation
├── lambdas/
│   └── summarize/
│       ├── handler.py          # Lambda function: receives text, calls OpenAI, returns summary
│       └── requirements.txt
├── templates/
│   ├── base.html               # Base template with nav, tenant context
│   ├── landing.html            # Public landing page (signup/login)
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── workflows.html
│   └── documents.html
├── static/
│   ├── styles.css
│   └── app.js
└── scripts/
    ├── create_public_tenant.py # Management command: creates public tenant + superadmin
    └── seed_demo.py            # Seeds a demo clinic tenant with sample data
```

### SHARED_APPS vs TENANT_APPS

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

### Middleware (order matters)

```python
MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",    # MUST be position 0
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tenant_users.tenants.middleware.TenantAccessMiddleware",  # Blocks unauthorized tenant access
    "django.contrib.messages.middleware.MessageMiddleware",
]
```

### Database Config

```python
DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",  # NOT django.db.backends.postgresql
        "NAME": env("DB_NAME", "clinic_portal"),
        "USER": env("DB_USER", "postgres"),
        "PASSWORD": env("DB_PASSWORD", "postgres"),
        "HOST": env("DB_HOST", "localhost"),
        "PORT": env("DB_PORT", "5432"),
    }
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]
```

### Redis Config

```python
# Tenant-aware caching — keys won't collide across tenants
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", "redis://localhost:6379/0"),
        "KEY_FUNCTION": "django_tenants.cache.make_key",
        "REVERSE_KEY_FUNCTION": "django_tenants.cache.reverse_key",
    }
}

# Redis session backend
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

### URL Routing

```python
ROOT_URLCONF = "config.urls"                 # Tenant-specific routes
PUBLIC_SCHEMA_URLCONF = "config.urls_public"  # Public landing/signup routes
```

---

## Models

### Tenant (shared schema)

```python
from django_tenants.models import DomainMixin
from tenant_users.tenants.models import TenantBase

class Tenant(TenantBase):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    auto_create_schema = True  # Auto-creates PG schema on save()

class Domain(DomainMixin):
    pass  # Maps hostname → tenant, supports is_primary flag
```

### User (shared schema)

```python
from tenant_users.tenants.models import UserProfile

class User(UserProfile):
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=[("admin", "Admin"), ("staff", "Staff")], default="staff")
```

### Workflow + Task (tenant schema — this is the business process automation)

```python
class Workflow(models.Model):
    """A business process template. E.g., 'Patient Intake', 'Referral Processing'."""
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

class Task(models.Model):
    """A task within a workflow. Has a state machine for tracking progress."""
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
        "completed": [],
        "cancelled": [],
    }

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="created")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_tasks")
    due_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    def transition_to(self, new_status, user):
        """Enforces valid state transitions and logs the change."""
        if new_status not in self.VALID_TRANSITIONS.get(self.status, []):
            raise ValueError(f"Cannot transition from {self.status} to {new_status}")
        old_status = self.status
        self.status = new_status
        self.save()
        AuditLog.objects.create(
            entity_type="task",
            entity_id=self.id,
            action=f"status_change:{old_status}→{new_status}",
            performed_by=user,
        )
```

### AuditLog (tenant schema)

```python
class AuditLog(models.Model):
    """Tracks every mutation. Health-tech companies expect this."""
    entity_type = models.CharField(max_length=50)  # "task", "workflow", "document"
    entity_id = models.IntegerField()
    action = models.CharField(max_length=200)       # "created", "status_change:created→assigned"
    details = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
```

### Document (tenant schema)

```python
class Document(models.Model):
    """Files stored in S3, linked to workflows/tasks."""
    name = models.CharField(max_length=300)
    s3_key = models.CharField(max_length=500)       # S3 object key
    content_type = models.CharField(max_length=100)
    size_bytes = models.IntegerField()
    summary = models.TextField(blank=True)           # LLM-generated summary
    workflow = models.ForeignKey(Workflow, null=True, blank=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```

---

## API Endpoints (Django Ninja)

### Auth (`/api/auth/`)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | None | Login with email + password, returns session |
| POST | `/api/auth/register` | None | Register new user (public tenant only) |
| POST | `/api/auth/logout` | Session | End session |
| GET | `/api/auth/me` | Session | Current user info + tenant + role |

### Tenants (`/api/tenants/`) — public schema only

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/tenants/` | Session (authenticated user) | Create new clinic tenant (user becomes admin) |
| GET | `/api/tenants/` | Superadmin | List all tenants |

### Staff (`/api/staff/`) — tenant schema

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/staff/` | Session | Admin | List staff in this tenant |
| POST | `/api/staff/invite` | Session | Admin | Add a user to this tenant (by email) |
| DELETE | `/api/staff/{id}` | Session | Admin | Remove staff from tenant |

### Workflows (`/api/workflows/`) — tenant schema

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/workflows/` | Session | Any | List all workflows |
| POST | `/api/workflows/` | Session | Admin | Create workflow |
| GET | `/api/workflows/{id}` | Session | Any | Get workflow with tasks |
| PUT | `/api/workflows/{id}` | Session | Admin | Update workflow |
| DELETE | `/api/workflows/{id}` | Session | Admin | Delete workflow |
| POST | `/api/workflows/{id}/generate-tasks` | Session | Admin | **LLM endpoint**: send workflow description to Lambda, auto-generate task checklist |

### Tasks (`/api/tasks/`) — tenant schema

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/tasks/` | Session | Any | List tasks (filterable by status, assigned_to) |
| POST | `/api/tasks/` | Session | Admin | Create task in a workflow |
| GET | `/api/tasks/{id}` | Session | Any | Get task detail |
| PUT | `/api/tasks/{id}` | Session | Admin | Update task |
| POST | `/api/tasks/{id}/transition` | Session | Any | Change task status (state machine enforced) |
| POST | `/api/tasks/{id}/assign` | Session | Admin | Assign task to staff member |

### Documents (`/api/documents/`) — tenant schema

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/documents/` | Session | List documents |
| POST | `/api/documents/upload-url` | Session | Get S3 presigned upload URL |
| POST | `/api/documents/` | Session | Register uploaded document (after S3 upload completes) |
| GET | `/api/documents/{id}/download-url` | Session | Get S3 presigned download URL |
| POST | `/api/documents/{id}/summarize` | Session | **LLM endpoint**: invoke Lambda to summarize document text |
| DELETE | `/api/documents/{id}` | Session | Delete document (S3 + DB) |

### Dashboard (`/api/dashboard/`) — tenant schema

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/dashboard/stats` | Session | Counts: total workflows, tasks by status, documents, staff |

---

## AWS Integration

### AWS Free Tier Setup (do this BEFORE building)

Create a free AWS account at https://aws.amazon.com/free/. All services used are within the free tier:

| Service | Free Tier | Our Usage |
|---|---|---|
| S3 | 5 GB storage, 20K GET, 2K PUT/month (12 months) | A few demo files |
| Lambda | 1M requests, 400K GB-seconds/month (always free) | A few LLM calls |
| IAM | Always free | 1 user for credentials |

**Step-by-step AWS setup:**

1. **Create S3 bucket:**
   - Go to S3 console → Create bucket
   - Name: `clinic-portal-docs` (or any unique name)
   - Region: `us-east-1`
   - Enable "Server-side encryption" (SSE-S3, AES-256)
   - Block all public access: ON (we use presigned URLs, not public files)

2. **Create Lambda function:**
   - Go to Lambda console → Create function
   - Name: `clinic-portal-summarize`
   - Runtime: Python 3.12
   - Paste code from `lambdas/summarize/handler.py`
   - Add environment variable: `OPENAI_API_KEY=sk-...`
   - Add a Lambda Layer for the `openai` package (or zip it with deps)
   - Memory: 256 MB, Timeout: 30 seconds
   - Copy the function ARN (e.g. `arn:aws:lambda:us-east-1:123456:function:clinic-portal-summarize`)

3. **Create IAM user for app credentials:**
   - Go to IAM console → Users → Create user
   - Name: `clinic-portal-app`
   - Attach policies: `AmazonS3FullAccess`, `AWSLambda_FullAccess`
   - Create access key → copy `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`

4. **Put credentials in `.env` file** (never commit this):
   ```env
   AWS_ACCESS_KEY_ID=AKIA...
   AWS_SECRET_ACCESS_KEY=...
   AWS_S3_BUCKET=clinic-portal-docs
   AWS_REGION=us-east-1
   LAMBDA_SUMMARIZE_ARN=arn:aws:lambda:us-east-1:123456:function:clinic-portal-summarize
   OPENAI_API_KEY=sk-...
   ```

### S3 — Document Storage

Use **presigned URLs** (not server-proxied uploads):

1. Frontend calls `POST /api/documents/upload-url` with filename + content type
2. Backend generates presigned POST via `boto3.client('s3').generate_presigned_post()`
3. Frontend uploads directly to S3 using the presigned URL
4. Frontend calls `POST /api/documents/` with the S3 key to register the document in DB

Config:
```python
AWS_STORAGE_BUCKET_NAME = env("AWS_S3_BUCKET", "clinic-portal-docs")
AWS_S3_REGION_NAME = env("AWS_REGION", "us-east-1")
AWS_S3_ENCRYPTION = "AES256"  # Server-side encryption
```

S3 bucket structure:
```
clinic-portal-docs/
  {tenant_schema_name}/
    {uuid}/{original_filename}
```

### Lambda — LLM Summarization

**Lambda function** (`lambdas/summarize/handler.py`):
- Input: `{"text": "...", "task_type": "summarize_document" | "generate_tasks"}`
- Calls OpenAI API (or AWS Bedrock)
- Output: `{"summary": "..."}` or `{"tasks": [{"title": "...", "description": "..."}, ...]}`

**Invocation from Django** (via boto3):
```python
import boto3, json

lambda_client = boto3.client("lambda")

def invoke_summarize(text: str) -> str:
    response = lambda_client.invoke(
        FunctionName=env("LAMBDA_SUMMARIZE_ARN"),
        InvocationType="RequestResponse",
        Payload=json.dumps({"text": text, "task_type": "summarize_document"}),
    )
    result = json.loads(response["Payload"].read())
    return result["summary"]

def invoke_generate_tasks(workflow_description: str) -> list[dict]:
    response = lambda_client.invoke(
        FunctionName=env("LAMBDA_SUMMARIZE_ARN"),
        InvocationType="RequestResponse",
        Payload=json.dumps({"text": workflow_description, "task_type": "generate_tasks"}),
    )
    result = json.loads(response["Payload"].read())
    return result["tasks"]
```

### S3 Client Setup

```python
s3_client = boto3.client(
    "s3",
    region_name=env("AWS_REGION", "us-east-1"),
    aws_access_key_id=env("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=env("AWS_SECRET_ACCESS_KEY"),
)
```

---

## Docker Compose

```yaml
services:
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: clinic_portal
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  web:
    build: .
    command: >
      sh -c "python manage.py migrate_schemas --shared &&
             python manage.py migrate_schemas --tenant &&
             python manage.py runserver 0.0.0.0:8000"
    env_file: .env
    environment:
      DATABASE_URL: postgres://postgres:postgres@db:5432/clinic_portal
      REDIS_URL: redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
    volumes:
      - .:/app

volumes:
  pgdata:
```

**`.env` file** (never commit this):
```env
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=clinic-portal-docs
AWS_REGION=us-east-1
LAMBDA_SUMMARIZE_ARN=arn:aws:lambda:us-east-1:123456:function:clinic-portal-summarize
OPENAI_API_KEY=sk-...
```

## Local Development Setup

```bash
# 1. Add to /etc/hosts (required for subdomain-based multi-tenancy)
echo "127.0.0.1 portal.localhost clinic1.localhost clinic2.localhost" | sudo tee -a /etc/hosts

# 2. Start services
docker compose up -d db redis

# 3. Install deps
uv sync

# 4. Run migrations
python manage.py migrate_schemas --shared
python manage.py migrate_schemas --tenant

# 5. Create public tenant + superadmin
python manage.py create_public_tenant

# 6. Run dev server
python manage.py runserver
```

## Security

- CSRF protection enabled on all mutating endpoints
- Session auth via Redis (not cookies with secrets)
- `TenantAccessMiddleware` blocks users from accessing tenants they don't belong to
- S3 objects namespaced by tenant schema name — no cross-tenant file access
- Presigned URLs expire after 15 minutes
- All models have `created_by` / `modified_at` for audit
- AuditLog tracks every state change
- Environment variables for all secrets (no hardcoded keys)
- SSL-ready: set `SECURE_SSL_REDIRECT = True` and `SESSION_COOKIE_SECURE = True` for production

## Frontend (minimal — backend is the priority)

Server-rendered Django templates with vanilla JS:

1. **Landing page** (public tenant): Logo, "Create your clinic workspace" signup form, login link
2. **Login page**: Email + password form
3. **Dashboard**: Stats cards (workflows, tasks, documents, staff count)
4. **Workflows page**: List workflows, create new, click into workflow to see tasks
5. **Workflow detail**: Task list with status badges, assign button, transition buttons (state machine), "Generate Tasks with AI" button
6. **Documents page**: Upload button, file list, "Summarize" button per document
7. **Staff page** (admin only): List staff, invite by email, remove

Use a simple CSS framework (Pico CSS or similar) for clean styling without a build step.

## Seed Data

The `seed_demo.py` script should:
1. Create a public tenant with domain `portal.localhost`
2. Create a superadmin user
3. Create a demo clinic tenant "Sunrise Clinic" with domain `clinic1.localhost`
4. Add 2 staff users to the clinic
5. Create a sample workflow "Patient Intake" with 4 tasks in various states
6. Create a sample document

## Intelligent Backend Patterns (MUST implement all)

### Pre-Work Context Calls (from Claude Code internals — "Peeking Under the Hood" article)
Before any LLM call (summarization or task generation), run 2 preparatory steps:
1. **Context summary call**: Summarize what the user has been doing in this session (last 3 actions from AuditLog) — inject as context into the LLM prompt so the summary is contextually relevant
2. **Query classification call**: Classify the request type ("summarize_document" vs "generate_tasks" vs "unknown") — route to the correct prompt template. Do NOT use the same prompt for both.

### Redis Caching (MUST use — Redis is already running)
Cache these with tenant-aware keys (`django_tenants.cache.make_key`):

| What to Cache | TTL | Key Pattern | Why |
|---|---|---|---|
| Dashboard stats | 60 seconds | `dashboard:stats` | Avoids 4 DB queries on every page load |
| Workflow list | 30 seconds | `workflows:list` | Frequently accessed, rarely changes |
| S3 presigned download URLs | 14 minutes | `s3:download:{doc_id}` | Presigned URLs expire in 15 min, cache for 14 |
| LLM summarization results | 24 hours | `llm:summary:{doc_id}` | Same document = same summary, expensive to re-generate |
| LLM generated tasks | 1 hour | `llm:tasks:{workflow_id}:{hash}` | Same description = same tasks |
| User session data | 30 minutes | handled by SESSION_ENGINE | Already configured via Redis session backend |

Cache invalidation: invalidate on mutations (create/update/delete) using Django signals or explicit `cache.delete()` in service functions.

### Memory Patterns

**Temporary memory (session-scoped):**
- Store user's last 5 actions in the session: `request.session["recent_actions"] = [...]`
- Use this to personalize dashboard: "You last worked on Patient Intake workflow"
- Cleared on logout

**Permanent memory (DB-scoped):**
- AuditLog already tracks all mutations — use it for "activity feed" on dashboard
- Store user preferences per tenant in a `UserPreference` model (optional — future enhancement)

### LLM Prompt Engineering (for Lambda handler)

**Temperature:** 0.2 for summarization (deterministic, factual), 0.5 for task generation (slightly creative)

**Summarization prompt MUST include:**
```
<system-reminder>
You are summarizing a document for a medical clinic staff member.
- Summarize ONLY what is in the document — do NOT add information from your training data
- Keep the summary under 200 words
- Use plain language — avoid medical jargon unless it's in the document
- Structure: 1-2 sentence overview, then key points as bullets
- If the document is too short to summarize meaningfully, say so
</system-reminder>
```

**Task generation prompt MUST include:**
```
<system-reminder>
You are generating a task checklist for a clinic workflow.
- Generate 3-8 tasks (not more, not fewer)
- Each task must be a concrete, actionable step (not vague like "do the thing")
- Tasks should be in logical order (dependencies first)
- Each task needs a title (under 100 chars) and a description (1-2 sentences)
- Output ONLY valid JSON: {"tasks": [{"title": "...", "description": "..."}, ...]}
- Do NOT include tasks outside the workflow's scope
</system-reminder>
```

**K-shot examples in prompts (Week 1 pattern):**
Include 2 examples in each prompt to stabilize output format:
```
Example 1:
Input: "Patient check-in process at front desk"
Output: {"tasks": [{"title": "Greet patient and verify appointment", "description": "Confirm patient name, appointment time, and provider."}, ...]}

Example 2:
Input: "Lab result review workflow"
Output: {"tasks": [{"title": "Retrieve lab results from portal", "description": "Log into lab portal and download latest results for the patient."}, ...]}
```

### LLM Output Validation (MUST implement)
- **Sanitize**: `strip_tags()` on ALL LLM output before storing in DB — treat as untrusted input
- **Validate JSON**: For task generation, parse the JSON response. If invalid JSON, retry once. If still invalid, return error "AI could not generate tasks — try rephrasing the workflow description"
- **Length check**: If summary > 500 words, truncate with "... [summary truncated]"
- **Empty check**: If LLM returns empty or just whitespace, return "Summary unavailable" — never store empty string

### Reflexion Pattern for LLM (Week 1)
If LLM output fails validation:
1. First attempt: standard prompt
2. If fails validation → add the error to the prompt: "Your previous response was invalid because [reason]. Try again."
3. If fails again → return graceful error to user
Max 2 retries per LLM call.

### Chain-of-Thought for Summarization (Week 1)
The summarization prompt should instruct the LLM to reason before summarizing:
```
Step 1: Identify the document type (report, form, notes, letter)
Step 2: Extract the 3-5 most important facts
Step 3: Write a concise summary based on those facts

<reasoning>
[Your analysis here — this will be stripped before storing]
</reasoning>

Summary: [Your final summary here — this is what gets stored]
```
Parse out the `<reasoning>` block — store only the Summary portion.

### Error Handling on ALL External Calls
Every call to S3 or Lambda MUST have:
```python
try:
    result = boto3_call(...)
except botocore.exceptions.ClientError as e:
    logger.warning(f"AWS error: {e.response['Error']['Code']}: {e.response['Error']['Message']}")
    return graceful_fallback
except (botocore.exceptions.ReadTimeoutError, botocore.exceptions.ConnectTimeoutError):
    logger.warning(f"AWS timeout on {operation}")
    return graceful_fallback
except botocore.exceptions.NoCredentialsError:
    logger.error("AWS credentials not configured")
    return graceful_fallback
```
Never let boto3 exceptions propagate to the user. Always return a structured error response.

### Observability (10 Logging Points — Steve's SDLC)
Every app MUST log at INFO level:
1. **Function entry**: `logger.info(f"{func_name} called: {params}")`
2. **Function exit**: `logger.info(f"{func_name} returned: {result_summary}")`
3. **Errors**: `logger.error(f"{func_name} failed: {error}", exc_info=True)`
4. **External API calls**: `logger.info(f"S3 {operation}: {key}")` / `logger.info(f"Lambda invoke: {task_type}")`
5. **State mutations**: `logger.info(f"Task {id} transitioned: {old} → {new} by {user}")`
6. **Security events**: `logger.info(f"Login: {email}")` / `logger.warning(f"Failed login: {email}")`
7. **Business milestones**: `logger.info(f"Tenant created: {name}")` / `logger.info(f"Workflow completed: {id}")`
8. **Performance**: `logger.warning(f"Slow query: {ms}ms")` if any DB query > 500ms
9. **Validation failures**: `logger.warning(f"Invalid transition: {old} → {new}")`
10. **Resource limits**: `logger.warning(f"Cache miss rate high: {rate}%")`

Never log: passwords, API keys, session tokens, PII (email is OK for auth logs but not in other contexts).

### AWS Bedrock Integration (replaces OpenAI)
The Lambda handler uses **AWS Bedrock** with Claude 3.5 Haiku — NOT OpenAI:
```python
import boto3
import json

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

def invoke_claude(prompt: str, max_tokens: int = 1024, temperature: float = 0.2) -> str:
    response = bedrock.invoke_model(
        modelId="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}]
        })
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]
```

Environment variable needed: `AWS_BEARER_TOKEN_BEDROCK` (Bedrock API key, expires April 4, 2026).
Model: `us.anthropic.claude-3-5-haiku-20241022-v1:0` (inference profile ID).

## What NOT to Build

- No patient registry or medical records
- No appointment scheduling
- No HIPAA/FHIR compliance
- No payment/billing
- No email/SMS notifications
- No Celery (Lambda handles async)
- No React (Django templates are fine)
- No CI/CD pipeline
- No complex role hierarchy beyond admin/staff
