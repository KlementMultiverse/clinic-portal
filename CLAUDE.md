# CLAUDE.md — Clinic Portal

Multi-tenant SaaS management portal where medical clinics sign up and get their own private workspace. Each clinic's data is fully isolated via PostgreSQL schema-per-tenant. Internal business process automation with AI-powered summarization.

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Runtime | Python 3.12 | via uv |
| Framework | Django 5.x | NOT Flask, NOT FastAPI |
| API | Django Ninja | NOT DRF — Schema classes, not serializers |
| Multi-tenancy | django-tenants + django-tenant-users | PostgreSQL schema-per-tenant |
| Database | PostgreSQL 15+ | REQUIRED by django-tenants |
| Cache | Redis 7 | Tenant-aware keys via django_tenants.cache |
| Storage | AWS S3 | Presigned URLs, never proxy uploads |
| LLM | AWS Lambda + OpenAI | Lambda handles summarization + task generation |
| Container | Docker + Docker Compose | PG + Redis + Django |
| Packages | uv | NEVER pip |
| Frontend | Django templates + vanilla JS | NOT React/Vue — no build step |
| CSS | Pico CSS or similar | No Tailwind, no build step |

## Project Structure

```
clinic-portal/
├── config/
│   ├── settings.py             # SHARED_APPS + TENANT_APPS, middleware, DB config
│   ├── urls.py                 # Tenant-specific URL routing
│   ├── urls_public.py          # Public schema URLs (landing, signup)
│   └── wsgi.py
├── apps/
│   ├── tenants/                # SHARED — Tenant + Domain models, signup
│   ├── users/                  # SHARED — Global user model (UserProfile)
│   ├── dashboard/              # TENANT — Dashboard stats
│   ├── workflows/              # TENANT — Workflows + Tasks + AuditLog
│   └── documents/              # TENANT — S3 file management
├── lambdas/summarize/          # AWS Lambda function
├── templates/                  # Django templates
├── static/                     # CSS + JS
├── scripts/                    # Management commands (create_public_tenant, seed_demo)
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── SPEC.md                     # Full specification
```

## Setup & Commands

```bash
# Prerequisites
echo "127.0.0.1 portal.localhost clinic1.localhost clinic2.localhost" | sudo tee -a /etc/hosts

# Start services
docker compose up -d db redis

# Install dependencies
uv sync

# Run migrations (django-tenants requires separate shared + tenant migrations)
uv run python manage.py migrate_schemas --shared
uv run python manage.py migrate_schemas --tenant

# Create public tenant + superadmin
uv run python manage.py create_public_tenant

# Seed demo data
uv run python manage.py seed_demo

# Run dev server
uv run python manage.py runserver

# Run tests
uv run python manage.py test

# Format + lint
black . && ruff check . --fix
```

## Code Style

- **black** line-length 100
- **ruff** rules: E, F, I, UP, B (ignores E501, B008)
- Target: Python 3.10-3.12

## Architecture Rules

<system-reminder>
These rules apply to EVERY agent, EVERY command, EVERY file change in this project.
If a rule conflicts with your instinct — the rule wins.
Re-read this section at the start of every task.
</system-reminder>

1. Django Ninja for ALL API routes — NEVER import `rest_framework`
2. All models follow SHARED vs TENANT separation — check SPEC.md Section "SHARED_APPS vs TENANT_APPS"
3. `TenantMainMiddleware` MUST be position 0 in MIDDLEWARE — no exceptions
4. Database engine MUST be `django_tenants.postgresql_backend` — NEVER `django.db.backends.postgresql`
5. Migrations: `migrate_schemas --shared` for shared apps, `migrate_schemas --tenant` for tenant apps — NEVER bare `migrate`
6. S3 keys MUST be namespaced by tenant: `{tenant_schema_name}/{uuid}/{filename}`
7. Presigned URLs expire after 15 minutes — NEVER serve files directly
8. Lambda invocation via `boto3.client("lambda").invoke()` — NEVER call OpenAI directly from Django
9. All credentials from `os.environ` or `.env` — NEVER hardcoded
10. `uv` for all package management — NEVER `pip install`
11. Redis cache keys via `django_tenants.cache.make_key` — NEVER raw keys (would collide across tenants)
12. AuditLog tracks EVERY state mutation — no silent changes
13. Task state transitions enforced by `VALID_TRANSITIONS` dict — NEVER skip validation
14. Run tests after EVERY code change — `uv run python manage.py test`
15. Frontend: Django templates + vanilla JS — NEVER React/Vue/Angular

## Post-Implementation Rule

<system-reminder>
After ANY code generation or modification — whether from /sc:implement, a subagent, or manual coding — you MUST run these commands before moving to the next task:
1. black . && ruff check . --fix
2. uv run python manage.py test
Do NOT defer testing to a separate /sc:test invocation. Test immediately.
</system-reminder>

## Testing Rules

<system-reminder>
django-tenants requires PostgreSQL for tests — SQLite will NOT work.
Use the correct test case base class for each app type.
</system-reminder>

- **Shared app tests** (tenants, users): use `django.test.TestCase`
- **Tenant app tests** (workflows, documents, dashboard): MUST use `django_tenants.test.cases.TenantTestCase`
- **TenantTestCase** auto-creates a test tenant + schema. Do NOT manually create schemas in tests.
- Run specific app: `uv run python manage.py test apps.<app_name>`
- Run all: `uv run python manage.py test`
- Coverage: `uv run coverage run manage.py test && uv run coverage report`

## Tenant Provisioning Flow

```text
1. User calls POST /api/tenants/ with {name, subdomain}
2. services.provision_tenant():
   a. Validate subdomain (not reserved: public, pg_*, information_schema)
   b. Create Tenant(schema_name=subdomain, name=name) → auto_create_schema runs CREATE SCHEMA
   c. Create Domain(domain="{subdomain}.localhost", tenant=tenant, is_primary=True)
   d. tenant.add_user(request.user) → adds creator to tenant membership
   e. TenantMembership.objects.create(user=request.user, tenant=tenant, role="admin")
3. Return tenant details
4. User accesses {subdomain}.localhost → TenantMainMiddleware resolves → schema set
```

## Access Patterns

### Database (tenant-aware)
```python
# Django-tenants auto-sets search_path based on request hostname
# All ORM queries are automatically scoped to the current tenant
# No manual tenant filtering needed in queries

# For shared models (Tenant, User): always accessible
# For tenant models (Workflow, Task, Document): auto-scoped by middleware
```

### S3 (presigned URLs)
```python
# Upload flow:
# 1. Frontend calls POST /api/documents/upload-url
# 2. Backend generates presigned POST via boto3
# 3. Frontend uploads directly to S3
# 4. Frontend calls POST /api/documents/ to register in DB

# Download flow:
# 1. Frontend calls GET /api/documents/{id}/download-url
# 2. Backend generates presigned GET URL
# 3. Frontend redirects to presigned URL
```

### Lambda (LLM calls)
```python
# All LLM calls go through Lambda — never direct from Django
# Two task types:
#   "summarize_document" → returns {"summary": "..."}
#   "generate_tasks"     → returns {"tasks": [{"title": "...", "description": "..."}]}
```

### Redis (tenant-aware caching)
```python
# Cache keys auto-prefixed with tenant schema name
# django_tenants.cache.make_key handles this
# Never construct cache keys manually
```

## API Contracts

### Auth (`/api/auth/`)
| Method | Path | Auth | Response |
|---|---|---|---|
| POST | `/api/auth/login` | None | `{user_id, name, role, tenant}` |
| POST | `/api/auth/register` | None | `{user_id, name}` |
| POST | `/api/auth/logout` | Session | `{success}` |
| GET | `/api/auth/me` | Session | `{user_id, name, role, tenant}` |

### Tenants (`/api/tenants/`) — public schema
| Method | Path | Auth | Response |
|---|---|---|---|
| POST | `/api/tenants/` | Session | `{id, name, domain, created_at}` |
| GET | `/api/tenants/` | Superadmin | `[{id, name, domain}]` |

### Workflows (`/api/workflows/`) — tenant schema
| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/api/workflows/` | Session | `[{id, name, description, task_count}]` |
| POST | `/api/workflows/` | Admin | `{id, name, description}` |
| GET | `/api/workflows/{id}` | Session | `{id, name, description, tasks: [...]}` |
| PUT | `/api/workflows/{id}` | Admin | `{id, name, description}` |
| DELETE | `/api/workflows/{id}` | Admin | `{success}` |
| POST | `/api/workflows/{id}/generate-tasks` | Admin | `{tasks: [{title, description}]}` |

### Tasks (`/api/tasks/`) — tenant schema
| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/api/tasks/` | Session | `[{id, title, status, assigned_to, due_date}]` |
| POST | `/api/tasks/` | Admin | `{id, title, status}` |
| GET | `/api/tasks/{id}` | Session | `{id, title, description, status, assigned_to, workflow}` |
| PUT | `/api/tasks/{id}` | Admin | `{id, title, description}` |
| POST | `/api/tasks/{id}/transition` | Session | `{id, status, previous_status}` |
| POST | `/api/tasks/{id}/assign` | Admin | `{id, assigned_to}` |

### Documents (`/api/documents/`) — tenant schema
| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/api/documents/` | Session | `[{id, name, size_bytes, summary, created_at}]` |
| POST | `/api/documents/upload-url` | Session | `{url, fields, s3_key}` |
| POST | `/api/documents/` | Session | `{id, name, s3_key}` |
| GET | `/api/documents/{id}/download-url` | Session | `{url}` |
| POST | `/api/documents/{id}/summarize` | Session | `{summary}` |
| DELETE | `/api/documents/{id}` | Session | `{success}` |

### Dashboard (`/api/dashboard/`)
| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/api/dashboard/stats` | Session | `{workflows, tasks_by_status, documents, staff_count}` |

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
