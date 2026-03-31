# Implementation Plan: Multi-Tenant Clinic Management Portal

**Design Doc:** docs/design-doc.md
**Branch:** `feature/clinic-portal-mvp`
**Start Date:** 2026-03-30

---

## Summary
Build a multi-tenant SaaS portal with schema-per-tenant isolation (django-tenants), session-based auth (django-tenant-users + Redis), workflow/task management with enforced state machine, S3 document storage via presigned URLs, and AI-powered summarization via AWS Lambda. Implementation follows strict dependency ordering across 7 phases.

## Agent Assignment (per CLAUDE.md Agent Selection Matrix)

| Phase | Domain | Agent | context7 Libraries |
|-------|--------|-------|-------------------|
| 1 | domain-tenants | @django-tenants-agent | django-tenants, django-tenant-users |
| 2 | domain-auth | @django-ninja-agent | django-ninja, django-tenant-users |
| 3 | domain-workflows | @django-ninja-agent | django-ninja |
| 4 | domain-documents | @s3-lambda-agent + @django-ninja-agent | boto3, django-ninja |
| 5 | domain-aws | @s3-lambda-agent | boto3 |
| 6 | domain-frontend | /sc:implement (frontend) | — |
| 7 | domain-tenants | @django-tenants-agent | django-tenants |

---

## Tasks

### Phase 1: Foundation (Infrastructure + Tenancy) — P0
**Agent:** @django-tenants-agent
**Done criteria:** Docker services running, Django scaffold complete, Tenant/Domain/User models migrated, public tenant created.

- [ ] [P] Task 1.1: Docker Compose setup → `docker-compose.yml`, `Dockerfile` (#27)
- [ ] [P] Task 1.2: pyproject.toml + uv dependencies → `pyproject.toml` (#28)
- [ ] Task 1.3: Django project scaffold + settings.py → `config/settings.py`, `config/urls.py`, `config/urls_public.py`, `config/wsgi.py`, `manage.py` (#29)
- [ ] Task 1.4: Tenant + Domain + User models + migrations → `apps/tenants/models.py`, `apps/users/models.py`, `apps/tenants/apps.py`, `apps/users/apps.py` (#30)
- [ ] Task 1.5: create_public_tenant management command → `scripts/create_public_tenant.py` (#31)

**Dependencies:** 1.1 + 1.2 are parallel. 1.3 depends on 1.2. 1.4 depends on 1.3. 1.5 depends on 1.4.

### Phase 2: Authentication + Staff Management — P0
**Agent:** @django-ninja-agent
**Done criteria:** Users can register, login, logout, view profile. Admins can manage staff. TenantAccessMiddleware blocks cross-tenant access.

- [ ] Task 2.1: Auth endpoints (login, register, logout, me) → `apps/users/api.py`, `config/urls.py` (#32)
- [ ] Task 2.2: Tenant signup flow (create tenant + domain + admin) → `apps/tenants/api.py`, `apps/tenants/services.py` (#33)
- [ ] Task 2.3: Staff management endpoints (list, invite, remove) → `apps/users/api.py`, `apps/users/services.py` (#34)

**Dependencies:** 2.1 depends on Phase 1. 2.2 depends on 2.1. 2.3 depends on 2.1.

### Phase 3: Workflows + Tasks + AuditLog — P1
**Agent:** @django-ninja-agent
**Done criteria:** Workflow/Task CRUD works, state machine enforces valid transitions, every mutation creates an AuditLog entry.

- [ ] Task 3.1: AuditLog model → `apps/workflows/models.py` (#37)
- [ ] Task 3.2: Workflow model + CRUD endpoints → `apps/workflows/models.py`, `apps/workflows/api.py` (#35)
- [ ] Task 3.3: Task model + state machine + endpoints → `apps/workflows/models.py`, `apps/workflows/api.py`, `apps/workflows/services.py` (#36)

**Dependencies:** 3.1 must come first (audit needed by 3.2 and 3.3). 3.2 before 3.3 (Task FK to Workflow).

### Phase 4: Documents + S3 Integration — P1
**Agent:** @s3-lambda-agent + @django-ninja-agent
**Done criteria:** Documents can be uploaded via presigned URL, registered, downloaded, and deleted. S3 keys are tenant-namespaced. AuditLog integration.

- [ ] Task 4.1: Document model + S3 presigned upload/download + CRUD → `apps/documents/models.py`, `apps/documents/api.py`, `apps/documents/services.py` (#38)

**Dependencies:** Depends on Phase 1 (tenant schema) and Phase 3 (AuditLog, Workflow/Task models for FKs).

### Phase 5: AI Integration (Lambda) — P2
**Agent:** @s3-lambda-agent
**Done criteria:** Lambda handler deployed, summarize endpoint invokes Lambda and saves summary, generate-tasks endpoint creates Task records from Lambda response.

- [ ] Task 5.1: Lambda function handler → `lambdas/summarize/handler.py`, `lambdas/summarize/requirements.txt` (#39)
- [ ] Task 5.2: Django endpoints for Lambda invocation → `apps/documents/api.py`, `apps/workflows/api.py` (#40)

**Dependencies:** 5.1 is independent (Lambda code). 5.2 depends on Phase 3 (workflows) + Phase 4 (documents).
**STOP:** AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, LAMBDA_SUMMARIZE_ARN) and OPENAI_API_KEY needed. Ask user before proceeding.

### Phase 6: Dashboard + Frontend Templates — P2
**Agent:** /sc:implement (frontend persona)
**Done criteria:** All pages render with Pico CSS, CRUD operations work end-to-end from browser, no build step.

- [ ] [P] Task 6.1: Dashboard stats endpoint → `apps/dashboard/api.py` (#41)
- [ ] [P] Task 6.2: Base + Landing + Login + Register templates → `templates/base.html`, `templates/landing.html`, `templates/login.html`, `templates/register.html`, `static/styles.css`, `static/app.js` (#42)
- [ ] Task 6.3: Workflows + Documents + Staff templates → `templates/dashboard.html`, `templates/workflows.html`, `templates/documents.html` (#43)

**Dependencies:** 6.1 and 6.2 are parallel. 6.3 depends on 6.2 (base template) and all APIs existing.

### Phase 7: Seed Data + Polish — P2
**Agent:** @django-tenants-agent
**Done criteria:** seed_demo.py creates public tenant, demo clinic, sample workflows/tasks/documents.

- [ ] Task 7.1: Seed demo data script → `scripts/seed_demo.py` (#44)

**Dependencies:** Depends on all models and management commands existing.

[P] = can run in parallel

---

## Done Criteria

Given all tasks are complete
When full pattern audit runs (`/audit-patterns full`)
Then >90% pass rate, zero critical failures

Given the feature is implemented
When all 10 acceptance criteria from proposal 01 are checked
Then all criteria pass

Given tests are run
When `uv run python manage.py test` executes
Then all tests pass with zero failures

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| django-tenants + django-tenant-users version incompatibility | High | Pin versions, test in Phase 1 before proceeding |
| Django Ninja CSRF disabled by default with session auth | High | Set `NinjaAPI(csrf=True)` explicitly in Phase 2 |
| Lambda cold starts slow user experience | Medium | Accept for MVP; async invocation is a future enhancement |
| S3/Lambda operations fail without AWS credentials | Medium | Graceful degradation with clear error messages |
| Schema migrations slow with many tenants | Medium | Accept for MVP (<100 tenants); parallel migration available |

---

## Progress Log

| Date | Phase | Status | Notes |
|------|-------|--------|-------|
| 2026-03-30 | Stage 1 (Specify) | Complete | Proposal 01 written, 18 issues created |
| 2026-03-30 | Stage 2 (Architect) | In Progress | Design doc + plan-tasks |
