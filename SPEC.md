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
