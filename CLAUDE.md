# CLAUDE.md — Clinic Portal

Multi-tenant SaaS management portal where medical clinics sign up and get their own private workspace. Each clinic's data is fully isolated via PostgreSQL schema-per-tenant. Internal business process automation with AI-powered summarization.

## SDLC Flow (PM Agent: follow this flow for ALL work)

<system-reminder>
This is the orchestration flow. The PM agent (/sc:pm) MUST follow these stages in order.
NEVER write application code directly — ALWAYS delegate to specialist agents via /run-with-checkpoint.
EVERY stage boundary requires /gate (CodeRabbit must have 0 suggestions before proceeding).
</system-reminder>

```
STAGE 1: SPECIFY
  /specify SPEC.md → proposal + GitHub Issues
  /checkpoint specify | "feature proposal"
  /gate stage-1

STAGE 2: ARCHITECT
  /design-doc proposal → 10-section design doc ("Will implement X because")
  /plan-tasks design-doc → GitHub Issues with phase labels
  /checkpoint each
  /gate stage-2

STAGE 3: IMPLEMENT (per GitHub Issue, in phase order)
  For each issue:
    1. @context-loader-agent → fetch library docs via context7
    2. Select agent by domain label (see matrix below)
    3. Agent writes code (TDD: test first → implement → verify)
    4. /checkpoint after each agent
    5. Post-impl: black + ruff + migrate + test
    6. If fail → reflexion (max 3) via @root-cause-analyst
    7. Green → commit → close issue
  /gate after each phase

STAGE 4: VALIDATE
  /audit-patterns full → must be >90%
  /sc:test --coverage
  /gate stage-4

STAGE 5: REVIEW
  /retro (BEFORE PR) → retrospective + update CLAUDE.md
  /gate stage-5 (final PR → CodeRabbit → merge)

STAGE 6: ITERATE
  Feedback → new issues → loop to Stage 1 or 3
```

### Agent Selection Matrix
| Domain Label | Agent | context7 Libraries |
|---|---|---|
| domain-tenants | @django-tenants-agent | django-tenants, django-tenant-users |
| domain-auth | @django-ninja-agent | django-ninja, django-tenant-users |
| domain-workflows | @django-ninja-agent | django-ninja |
| domain-documents | @s3-lambda-agent + @django-ninja-agent | boto3, django-ninja |
| domain-aws | @aws-setup-agent | aws-cli |
| domain-frontend | /sc:implement (frontend persona) | — |

### PM Agent Rules
1. NEVER write application code — only delegate and evaluate
2. Every agent run goes through /run-with-checkpoint
3. Every stage boundary goes through /gate
4. Max 2 agents in parallel, only for independent tasks
5. Sequential for dependent tasks (models before endpoints, shared before tenant)
6. If credentials needed (AWS, OpenAI) → STOP and ask the user
7. If agent output fails checkpoint → fix agent prompt, re-run
8. Track progress via GitHub Issues (gh issue edit labels)

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

## Architecture Rules

<system-reminder>
These rules apply to EVERY agent, EVERY command, EVERY file change in this project.
If a rule conflicts with your instinct — the rule wins.
Re-read this section at the start of every task.
</system-reminder>

1. Django Ninja for ALL API routes — NEVER import `rest_framework`
2. All models follow SHARED vs TENANT separation — check SPEC.md
3. `TenantMainMiddleware` MUST be position 0 in MIDDLEWARE — no exceptions
4. Database engine MUST be `django_tenants.postgresql_backend` — NEVER `django.db.backends.postgresql`
5. Migrations: `migrate_schemas --shared` for shared apps, `migrate_schemas --tenant` for tenant apps — NEVER bare `migrate`
6. S3 keys MUST be namespaced by tenant: `{tenant_schema_name}/{uuid}/{filename}`
7. Presigned URLs expire after 15 minutes — NEVER serve files directly
8. Lambda invocation via `boto3.client("lambda").invoke()` — NEVER call OpenAI directly from Django
9. All credentials from `os.environ` or `.env` — NEVER hardcoded
10. `uv` for all package management — NEVER `pip install`
11. Redis cache keys via `django_tenants.cache.make_key` — NEVER raw keys
12. AuditLog tracks EVERY state mutation — no silent changes
13. Task state transitions enforced by `VALID_TRANSITIONS` dict — NEVER skip validation
14. Run tests after EVERY code change — `uv run python manage.py test`
15. Frontend: Django templates + vanilla JS — NEVER React/Vue/Angular

## Post-Implementation Rule

<system-reminder>
After ANY code generation — run these before moving to the next task:
1. black . && ruff check . --fix
2. uv run python manage.py test
Do NOT defer testing to a separate step.
</system-reminder>

## Testing Rules

- **Shared app tests** (tenants, users): use `django.test.TestCase`
- **Tenant app tests** (workflows, documents, dashboard): MUST use `django_tenants.test.cases.TenantTestCase`
- Run specific app: `uv run python manage.py test apps.<app_name>`
- Run all: `uv run python manage.py test`

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
