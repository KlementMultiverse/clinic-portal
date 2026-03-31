# Multi-Tenant Clinic Management Portal — Complete Build Spec

## Overview

Build a multi-tenant SaaS management portal where medical clinics sign up and get their own private workspace. Each clinic's data is fully isolated via PostgreSQL schema-per-tenant. The portal provides **internal business process automation** (workflows + tasks), **document management** via S3, **AI-powered summarization** via Claude Haiku, and a **clinical research QA system** that searches real medical databases (ClinicalTrials.gov + PubMed) with RAG-based AI summarization and conversational chat.

## Tech Stack (mandatory — use ALL)

| Technology | Purpose | Version |
|---|---|---|
| Python | Runtime | 3.12 |
| Django | Web framework | 5.x+ |
| Django Ninja | REST API layer (NOT DRF) | 1.0+ |
| django-tenants | Multi-tenancy via PostgreSQL schemas | 3.6+ |
| django-tenant-users | Global auth + per-tenant permissions | 1.0+ |
| PostgreSQL | Database (required by django-tenants) | 15+ |
| Redis | Session backend + tenant-aware caching | 7+ |
| AWS S3 | Document/file storage via presigned URLs | — |
| Claude Haiku (Anthropic API) | LLM for summarization, task generation, clinical QA | claude-haiku-4-5 |
| httpx | Async HTTP client for external API calls | 0.28+ |
| Docker + Docker Compose | Containerized local dev (PG + Redis + Django) | — |
| uv | Python package manager (NEVER pip) | latest |
| Pico CSS | Frontend styling (CDN, no build step) | 2.x |

## Architecture

### Project Structure

```
clinic-portal/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── manage.py
├── .env.example
├── .gitignore
├── config/
│   ├── settings.py
│   ├── urls.py              # Tenant-specific URL routing
│   ├── urls_public.py       # Public schema URLs (landing, signup)
│   ├── views.py             # Template view functions
│   └── wsgi.py
├── apps/
│   ├── tenants/             # SHARED — Tenant + Domain models, signup
│   │   ├── models.py
│   │   ├── api.py
│   │   └── management/commands/
│   │       ├── create_public_tenant.py
│   │       └── seed_demo.py
│   ├── users/               # SHARED — Global user model, auth, staff
│   │   ├── models.py
│   │   ├── api.py           # NinjaAPI instance + auth/staff/tenant routers
│   │   ├── services.py      # track_action() session helper
│   │   └── middleware.py    # PasswordResetMiddleware
│   ├── dashboard/           # TENANT — Main dashboard
│   │   └── api.py
│   ├── workflows/           # TENANT — Business process automation
│   │   ├── models.py        # Workflow, Task, AuditLog
│   │   └── api.py
│   ├── documents/           # TENANT — S3 file management
│   │   ├── models.py
│   │   ├── api.py
│   │   └── services.py      # S3 + LLM service functions
│   └── search/              # TENANT — Clinical QA search + chat
│       ├── models.py        # SearchHistory, ChatThread, ChatMessage
│       ├── api.py
│       ├── services.py      # ClinicalTrials.gov + PubMed + RAG
│       └── chat.py          # Conversational chat service
├── lambdas/
│   └── summarize/
│       ├── handler.py
│       └── requirements.txt
├── templates/
│   ├── base.html
│   ├── landing.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── workflows.html
│   ├── documents.html
│   ├── staff.html
│   ├── search.html
│   └── chat.html
├── static/
│   ├── styles.css
│   └── app.js
└── tests/
    └── e2e/
        ├── test_frontend_flows.py
        └── test_clinical_search.py
```

### SHARED_APPS vs TENANT_APPS

```python
SHARED_APPS = [
    "django_tenants",           # Must be first
    "apps.tenants",             # Tenant + Domain models
    "apps.users",               # Global user model (UserProfile)
    "tenant_users.permissions",  # In BOTH shared and tenant
    "tenant_users.tenants",     # Shared only
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.admin",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

TENANT_APPS = [
    "django.contrib.contenttypes",  # In BOTH
    "django.contrib.auth",          # In BOTH
    "tenant_users.permissions",     # In BOTH
    "apps.dashboard",
    "apps.workflows",
    "apps.documents",
    "apps.search",
]

INSTALLED_APPS = list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]
```

### Middleware (order matters — TenantMainMiddleware MUST be position 0)

```python
MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",    # Position 0 — resolves tenant from subdomain
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.users.middleware.PasswordResetMiddleware",          # Custom — enforces password reset for invited staff
    "tenant_users.tenants.middleware.TenantAccessMiddleware",  # Blocks unauthorized tenant access
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

### Database Config

```python
DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",  # NOT django.db.backends.postgresql
        "NAME": os.environ.get("DB_NAME", "clinic_portal"),
        "USER": os.environ.get("DB_USER", "postgres"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
    }
}
DATABASE_ROUTERS = ["django_tenants.routers.TenantSyncRouter"]
TENANT_MODEL = "tenants.Tenant"
TENANT_DOMAIN_MODEL = "tenants.Domain"
AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = ["tenant_users.permissions.backend.UserBackend"]
```

### Redis Config

```python
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        "KEY_FUNCTION": "django_tenants.cache.make_key",      # Tenant-aware keys
        "REVERSE_KEY_FUNCTION": "django_tenants.cache.reverse_key",
    }
}
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG  # True in production
```

### Security Settings

```python
SECRET_KEY = os.environ.get("SECRET_KEY", "")
# Must raise ValueError if not set in production (DEBUG=False)

DEBUG = os.environ.get("DEBUG", "False").lower() == "true"  # Default False
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,portal.localhost,clinic1.localhost").split(",")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
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
    auto_create_schema = True

class Domain(DomainMixin):
    pass
```

### User (shared schema)

```python
from tenant_users.tenants.models import UserProfile

class User(UserProfile):
    name = models.CharField(max_length=150, blank=True, default="")
    role = models.CharField(max_length=20, choices=[("admin", "Admin"), ("staff", "Staff")], default="staff")
    must_reset_password = models.BooleanField(default=False)
```

### AuditLog (tenant schema — IMMUTABLE)

```python
class AuditLog(models.Model):
    entity_type = models.CharField(max_length=50)   # "task", "workflow", "document", "search"
    entity_id = models.IntegerField()
    action = models.CharField(max_length=200)        # "created", "status_change:created→assigned"
    details = models.JSONField(default=dict, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["-timestamp"]),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog entries cannot be deleted.")
```

### Workflow + Task (tenant schema)

```python
class Workflow(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

class Task(models.Model):
    STATUS_CHOICES = [
        ("created", "Created"), ("assigned", "Assigned"),
        ("in_progress", "In Progress"), ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]
    VALID_TRANSITIONS = {
        "created": ["assigned", "cancelled"],
        "assigned": ["in_progress", "cancelled"],
        "in_progress": ["completed", "cancelled"],
        "completed": [],    # Terminal
        "cancelled": [],    # Terminal
    }
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="created", db_index=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tasks")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="created_tasks")
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
            entity_type="task", entity_id=self.id,
            action=f"status_change:{old_status}→{new_status}",
            performed_by=user,
        )
```

### Document (tenant schema)

```python
class Document(models.Model):
    name = models.CharField(max_length=300)
    s3_key = models.CharField(max_length=500)        # {tenant_schema}/{uuid}/{filename}
    content_type = models.CharField(max_length=100)
    size_bytes = models.IntegerField()
    summary = models.TextField(blank=True)            # LLM-generated, strip_tags before storage
    workflow = models.ForeignKey(Workflow, null=True, blank=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.SET_NULL)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```

### SearchHistory (tenant schema)

```python
class SearchHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    query = models.TextField()
    summary = models.TextField(blank=True)
    trials_data = models.JSONField(default=list)     # [{nct_id, title, status, summary}]
    papers_data = models.JSONField(default=list)     # [{pmid, title, authors, journal, pub_date}]
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-created_at"]
```

### ChatThread + ChatMessage (tenant schema)

```python
class ChatThread(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=200, blank=True)
    trials_context = models.JSONField(default=list)   # Cached trials for conversation
    papers_context = models.JSONField(default=list)   # Cached papers for conversation
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ["-updated_at"]

class ChatMessage(models.Model):
    ROLE_CHOICES = [("user", "User"), ("assistant", "Assistant")]
    thread = models.ForeignKey(ChatThread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    sources_used = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["created_at"]
```

---

## API Endpoints (Django Ninja)

### Auth (`/api/auth/`)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register` | None | Register with email + password + name. Returns 201. |
| POST | `/api/auth/login` | None | Login with email + password. Sets session. Returns user + tenant name. |
| POST | `/api/auth/logout` | Session | End session. Clears recent_actions from session. |
| GET | `/api/auth/me` | Session | Current user info + tenant name. |
| POST | `/api/auth/reset-password` | Session | Reset password (min 8 chars). Clears must_reset_password flag. |

### Tenants (`/api/tenants/`)

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| POST | `/api/tenants/` | Session | Any | Create clinic tenant. Validates subdomain format + reserved names. Uses provision_tenant(). |
| GET | `/api/tenants/` | Session | Superadmin | List all tenants (excludes public). |

### Staff (`/api/staff/`)

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/staff/` | Session | Admin | List staff in current tenant. |
| POST | `/api/staff/invite` | Session | Admin | Add user by email. Creates account if new (must_reset_password=True). |
| DELETE | `/api/staff/{id}` | Session | Admin | Remove staff from tenant (not global delete). Cannot remove owner. |

### Workflows (`/api/workflows/`)

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/workflows/` | Session | Any | List workflows (30s cache). |
| POST | `/api/workflows/` | Session | Admin | Create workflow. AuditLog. Cache invalidation. |
| GET | `/api/workflows/{id}` | Session | Any | Get workflow with nested tasks. |
| PUT | `/api/workflows/{id}` | Session | Admin | Update workflow. AuditLog. |
| DELETE | `/api/workflows/{id}` | Session | Admin | Delete workflow. AuditLog. |
| POST | `/api/workflows/{id}/generate-tasks` | Session | Admin | AI generates tasks via Claude Haiku. K-shot + reflexion. 1hr cache. |

### Tasks (`/api/tasks/`)

| Method | Path | Auth | Role | Description |
|---|---|---|---|---|
| GET | `/api/tasks/` | Session | Any | List tasks. Filterable by status, assigned_to. |
| POST | `/api/tasks/` | Session | Admin | Create task. AuditLog. |
| GET | `/api/tasks/{id}` | Session | Any | Get task detail. |
| PUT | `/api/tasks/{id}` | Session | Admin | Update task. AuditLog. |
| POST | `/api/tasks/{id}/transition` | Session | Any | Change status. VALID_TRANSITIONS enforced. AuditLog. |
| POST | `/api/tasks/{id}/assign` | Session | Admin | Assign to user. Validates user belongs to tenant. AuditLog. |

### Documents (`/api/documents/`)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/documents/` | Session | List documents. Filterable by workflow_id, task_id. |
| POST | `/api/documents/upload-url` | Session | Get S3 presigned PUT URL (900s expiry). Validates content_type. |
| POST | `/api/documents/` | Session | Register document after S3 upload. Validates S3 key tenant prefix. |
| GET | `/api/documents/{id}/download-url` | Session | Get S3 presigned GET URL (900s). Validates tenant prefix. 14min cache. |
| POST | `/api/documents/{id}/summarize` | Session | AI summarize via Claude Haiku. Chain-of-Thought. 24hr cache. |
| DELETE | `/api/documents/{id}` | Session (Admin) | Delete S3 object + DB record. AuditLog. |

### Dashboard (`/api/dashboard/`)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/dashboard/stats` | Session | Counts: workflows, documents, staff, searches, tasks_by_status, recent_actions. 60s cache. |

### Search (`/api/search/`)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/search/` | Session | Search ClinicalTrials.gov + PubMed, AI summarize (RAG). Saves to SearchHistory. |
| GET | `/api/search/history` | Session | User's past searches (max 20). |
| POST | `/api/search/chat` | Session | Send message in clinical QA chat. Auto-searches when needed. |
| GET | `/api/search/chat/threads` | Session | User's chat threads (max 20). |
| GET | `/api/search/chat/{id}` | Session | Get all messages in a thread. |

---

## Clinical QA Search (RAG Pipeline)

### How It Works

```
User types: "Phase 3 trials for metformin in Type 2 diabetes"
  ↓
Query rewriting: expand abbreviations (T2DM→type 2 diabetes mellitus), add MeSH synonyms
  ↓
Parallel fetch (asyncio.gather with httpx):
  - ClinicalTrials.gov API v2: GET /api/v2/studies?query.term={query}&pageSize=10
  - PubMed E-utilities: esearch (get PMIDs) → esummary (get details)
  ↓
Format context with [NCT...] and [PMID:...] citations
  ↓
Claude Haiku RAG summarization (temperature 0.2):
  - OVERVIEW: 1-2 sentences
  - TRIALS: grouped by status (Recruiting/Active/Completed)
  - RESEARCH: 3-5 key findings from papers
  - BOTTOM LINE: 1 sentence takeaway
  ↓
Save to SearchHistory (per user, per tenant)
```

### External APIs (free, no keys needed)

**ClinicalTrials.gov v2:**
```
GET https://clinicaltrials.gov/api/v2/studies?query.term={query}&pageSize=10
Response: { "studies": [{ "protocolSection": { "identificationModule": { "nctId", "briefTitle" }, "statusModule": { "overallStatus" }, "descriptionModule": { "briefSummary" } } }] }
```

**PubMed NCBI E-utilities:**
```
Step 1: GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmax=10&retmode=json
Step 2: GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={comma_ids}&retmode=json
```

### Conversational Chat

Multi-turn conversations with clinical search context:
- Thread stores trials_context + papers_context as JSON
- AI detects when new search is needed (triggers: "search for", "find", "what about", "compare with")
- Follow-up questions use existing context (no new search)
- Conversation history (last 10 messages) sent to Claude
- Citations auto-linked in frontend: [NCT...] → clinicaltrials.gov, [PMID:...] → pubmed.ncbi.nlm.nih.gov

---

## Redis Caching (tenant-aware via make_key)

| What | TTL | Key Pattern | Invalidated On |
|---|---|---|---|
| Dashboard stats | 60s | `dashboard:stats` | Workflow/task/document CRUD |
| Workflow list | 30s | `workflows:list` | Workflow create/update/delete |
| S3 download URLs | 14 min (840s) | `s3:download:{doc_id}` | Document delete |
| LLM summaries | 24 hr | `llm:summary:{doc_id}` | Re-summarize |
| LLM generated tasks | 1 hr | `llm:tasks:{workflow_id}:{hash}` | — |
| Clinical trials | 6 hr | `search:trials:{query_hash}` | fresh=True |
| PubMed papers | 7 days | `search:papers:{query_hash}` | fresh=True |
| Query rewrites | 24 hr | `search:rewrite:{query_hash}` | — |

---

## LLM Prompt Engineering

### Summarization Prompt (temperature 0.2)

Includes `<system-reminder>` tags, Chain-of-Thought (Step 1-3 with `<reasoning>` block stripped before storage), 500-word max with truncation, empty check returns "Summary unavailable".

### Task Generation Prompt (temperature 0.5)

Includes `<system-reminder>` tags, 2 k-shot examples (patient check-in, lab results), JSON validation, reflexion retry on invalid JSON (max 1 retry), 3-8 tasks required.

### Clinical QA Prompt (temperature 0.2)

RAG prompt: ONLY use provided context, cite every claim with [NCT...] or [PMID:...], structured as OVERVIEW/TRIALS/RESEARCH/BOTTOM LINE, plain text only (no markdown), max 250 words.

### Output Validation (ALL LLM output)

- `strip_tags()` before storage (treat LLM output as untrusted)
- JSON validation for task generation (parse, validate structure)
- Length check (500 words max, truncate with "... [summary truncated]")
- Empty check (return "Summary unavailable", never store empty string)
- Reflexion: retry once with error context if validation fails

---

## Session Memory

**Temporary (session-scoped):**
- `request.session["recent_actions"]`: last 5 user actions, each with {action, entity_type, entity_name, entity_id, timestamp}
- Tracked on: workflow/task/document CRUD, staff invite/remove, search, chat
- Shown on dashboard via `/api/dashboard/stats` response
- Cleared on logout

**Permanent (DB-scoped):**
- AuditLog: tracks ALL mutations (immutable, no update/delete)
- SearchHistory: all clinical searches per user
- ChatThread + ChatMessage: full conversation history

---

## Observability (10 Logging Points)

Every app logs at INFO level with `logger = logging.getLogger(__name__)`:

1. Function entry with params
2. Function exit with result summary
3. Errors with `exc_info=True`
4. External API calls (S3 operations, LLM invocations, httpx calls)
5. State mutations (task transitions with old→new status)
6. Security events (login success/failure, logout)
7. Business milestones (tenant created, workflow completed)
8. Performance warnings (query > 500ms)
9. Validation failures (invalid transitions, bad content types)
10. Resource limits (cache misses)

**NEVER log:** passwords, API keys, session tokens, PII (email OK in auth logs only).

---

## Error Handling

Every external call (S3, Lambda, httpx, Claude API) MUST have:
```python
try:
    result = external_call(...)
except botocore.exceptions.ClientError as e:
    logger.warning("AWS error: %s", e)
    return graceful_fallback
except (ReadTimeoutError, ConnectTimeoutError):
    logger.warning("Timeout")
    return graceful_fallback
except NoCredentialsError:
    logger.error("Credentials not configured")
    return graceful_fallback
```
Never let exceptions propagate to the user. Always return structured JSON errors.

---

## Docker Compose

```yaml
services:
  db:
    image: postgres:15
    ports: ["5433:5432"]
    environment:
      POSTGRES_DB: clinic_portal
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  web:
    build: .
    command: >
      sh -c "uv run python manage.py migrate_schemas --shared &&
             uv run python manage.py migrate_schemas --tenant &&
             uv run python manage.py runserver 0.0.0.0:8000"
    env_file: .env
    ports: ["8000:8000"]
    depends_on: [db, redis]
    volumes: [".:/app"]

volumes:
  pgdata:
```

## Environment Variables (.env)

```
DB_NAME=clinic_portal
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5433
REDIS_URL=redis://localhost:6379/0
SECRET_KEY=<generate-a-real-key>
DEBUG=True
ALLOWED_HOSTS=localhost,portal.localhost,clinic1.localhost,clinic2.localhost
TENANT_USERS_DOMAIN=localhost
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_BUCKET=clinic-portal-docs
AWS_REGION=us-east-1
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
LAMBDA_SUMMARIZE_ARN=              # Empty = direct Claude API calls (no Lambda needed)
```

## Local Development Setup

```bash
# 1. Add to /etc/hosts
echo "127.0.0.1 portal.localhost clinic1.localhost clinic2.localhost" | sudo tee -a /etc/hosts

# 2. Start services
docker compose up -d db redis

# 3. Install deps
uv sync

# 4. Run migrations
uv run python manage.py migrate_schemas --shared
uv run python manage.py migrate_schemas --tenant

# 5. Create public tenant + seed demo data
uv run python manage.py create_public_tenant
uv run python manage.py seed_demo

# 6. Run dev server
uv run python manage.py runserver

# 7. Open browser
# Portal:  http://portal.localhost:8000/
# Clinic:  http://clinic1.localhost:8000/login/
# Login:   admin@portal.localhost / admin123
```

## Seed Data

The `seed_demo.py` script creates (idempotent):
1. Public tenant with domain `portal.localhost`
2. Superadmin: `admin@portal.localhost` / `admin123`
3. Demo clinic "Sunrise Clinic" with domain `clinic1.localhost`
4. Staff: `staff1@clinic1.localhost` / `staff123` (Alice Johnson)
5. Staff: `staff2@clinic1.localhost` / `staff123` (Bob Smith)
6. Workflow "Patient Intake" with 4 tasks (created, assigned, in_progress, completed)
7. Sample document "Intake Form Template.pdf"
8. AuditLog entries for all mutations

## Frontend (Django templates + vanilla JS + Pico CSS)

| Page | URL | Description |
|---|---|---|
| Landing | `portal.localhost/` | Public signup + login links |
| Login | `*/login/` | Email + password form, fetch() to API |
| Register | `*/register/` | Name + email + password form |
| Dashboard | `clinic.*/` | Stats cards, task breakdown, recent actions |
| Workflows | `clinic.*/workflows/` | CRUD, task list with status badges, AI generate |
| Documents | `clinic.*/documents/` | Upload via presigned URL, download, summarize |
| Staff | `clinic.*/staff/` | Invite by email, remove (admin only) |
| Search | `clinic.*/search/` | Clinical QA: query → trials + papers + AI summary |
| Chat | `clinic.*/chat/` | Conversational clinical QA with thread sidebar |

Navigation bar shows: Dashboard, Workflows, Documents, Search, Chat, Staff (admin only).
All templates extend `base.html` with Pico CSS CDN + `static/app.js` (CSRF helper, apiFetch wrapper).

## Testing

- **Shared app tests** (tenants, users): use `django.test.TestCase`
- **Tenant app tests** (workflows, documents, dashboard, search): use `django_tenants.test.cases.TenantTestCase`
- **E2E tests**: Playwright with Chromium headless
- Run specific app: `uv run python manage.py test apps.<app_name>`
- Run all Django: `uv run python manage.py test --noinput`
- Run E2E: `uv run pytest tests/e2e/ -v`
- Target: 200+ tests (Django unit + Playwright E2E)

## What NOT to Build

- No patient registry or medical records
- No appointment scheduling
- No HIPAA/FHIR compliance
- No payment/billing
- No email/SMS notifications
- No Celery (Lambda handles async, or direct Claude API)
- No React/Vue/Angular (Django templates are fine)
- No CI/CD pipeline
- No complex role hierarchy beyond admin/staff
