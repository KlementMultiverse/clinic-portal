# Feature Proposal: Multi-Tenant Clinic Management Portal MVP

**Proposal Number:** 01
**Status:** Draft
**Author:** Claude Code + user
**Created:** 2026-03-30
**Target Branch:** `feature/clinic-portal-mvp`

---

## Problem Statement

Small and medium medical clinics lack affordable, purpose-built tools for internal business process management. Clinic administrators currently manage workflows, tasks, and documents using spreadsheets, shared drives, email chains, and general-purpose tools like Trello or Notion — none of which provide tenant data isolation, audit trails, or domain-specific AI assistance.

This project builds a multi-tenant SaaS portal where each clinic gets an isolated workspace (PostgreSQL schema-per-tenant) with workflow/task management, document storage via S3, and AI-powered document summarization and task generation via AWS Lambda + OpenAI.

[NEEDS CLARIFICATION]: How is a superadmin distinguished from a tenant admin? Assumed: Django `is_superuser=True` flag.
[NEEDS CLARIFICATION]: Does clinic signup create both Tenant and first admin User atomically? Assumed: yes, single-step.
[NEEDS CLARIFICATION]: What does "invite staff" mean? SPEC.md says "Add a user to this tenant (by email)" but also excludes email/SMS notifications. Assumed: "by email" means the admin identifies the user by email address and creates their account directly (no invitation email sent). This aligns with the no-email constraint.

## User Stories

- As a **clinic owner**, I want to sign up and get a private workspace so that my clinic's data is fully isolated from other clinics
- As a **clinic admin**, I want to create workflows and have AI generate task suggestions so that I can automate business process setup
- As a **clinic admin**, I want to invite and remove staff members so that I control who has access to my workspace
- As a **staff member**, I want to view and transition tasks assigned to me so that workflow progress is tracked accurately
- As a **staff member**, I want to upload documents and request AI summaries so that I can quickly understand file contents
- As a **staff member**, I want every action to be audit-logged so that there is a clear trail of who did what
- As a **superadmin**, I want to list all tenants so that I can monitor active clinics on the platform
- As a **clinic admin**, I want a dashboard with aggregate stats so that I have a quick overview of clinic activity

## Proposed Solution

### High-Level Approach

Clinics visit a public landing page, sign up to create their workspace, and are routed to their private subdomain (e.g., `clinic1.localhost`). Within their workspace, admins create workflows, manage tasks through a state machine (created → assigned → in_progress → completed/cancelled), upload documents to S3 via presigned URLs, and leverage AI for document summarization and automatic task generation. Every mutation is audit-logged. Staff members collaborate on assigned tasks. A dashboard provides at-a-glance statistics.

### Technical Approach

- **Multi-tenancy**: django-tenants with PostgreSQL schema-per-tenant; django-tenant-users for global auth + per-tenant permissions
- **API layer**: Django Ninja (NOT DRF) — all endpoints as Schema classes
- **Database**: PostgreSQL 15+ with `django_tenants.postgresql_backend` engine; `TenantMainMiddleware` at position 0
- **Cache/Sessions**: Redis 7 with tenant-aware keys via `django_tenants.cache.make_key`
- **Storage**: AWS S3 with presigned URLs (15-min expiry); keys namespaced by tenant schema name
- **AI**: AWS Lambda invoked via `boto3.client("lambda").invoke()` — never call OpenAI directly from Django
- **Frontend**: Django templates + vanilla JS + Pico CSS — no build step
- **Infrastructure**: Docker Compose (PG + Redis + Django); uv for package management
- **SHARED_APPS**: django_tenants, apps.tenants, apps.users, django contrib apps
- **TENANT_APPS**: apps.dashboard, apps.workflows, apps.documents

### Alternatives Considered

1. **Django REST Framework instead of Django Ninja**: DRF is more mature with larger ecosystem, but SPEC mandates Django Ninja. Ninja offers better type safety via Pydantic schemas and faster development for this scope.
2. **Row-level tenancy instead of schema-per-tenant**: Simpler migrations but weaker isolation. Schema-per-tenant provides stronger guarantees appropriate for healthcare-adjacent data — mandated by SPEC.
3. **Celery for async tasks instead of Lambda**: Would add infrastructure complexity. Lambda is serverless, always-free-tier eligible, and already required by SPEC for LLM integration.

---

## Implementation Plan

### Phase 1: Foundation (Infrastructure + Tenancy)
- [ ] [P] Docker Compose setup (PostgreSQL 15 + Redis 7 + Django)
- [ ] [P] pyproject.toml + uv setup with all dependencies
- [ ] Django project scaffold (config/, apps/, manage.py)
- [ ] settings.py: database engine, middleware, SHARED_APPS/TENANT_APPS, Redis cache/sessions
- [ ] Tenant model (TenantBase) + Domain model (DomainMixin)
- [ ] User model (extends UserProfile from django-tenant-users)
- [ ] Management command: create_public_tenant (public schema + superadmin)
- [ ] Migrations: migrate_schemas --shared

### Phase 2: Authentication + Staff Management
- [ ] Django Ninja auth router: login, register, logout, me endpoints
- [ ] Session-based auth with Redis backend
- [ ] Tenant signup flow (creates Tenant + Domain + admin User atomically)
- [ ] Staff management endpoints: list, add-by-email (create/find user + link to tenant), remove (admin only)
- [ ] TenantAccessMiddleware integration test

### Phase 3: Workflows + Tasks + AuditLog
- [ ] Workflow model + CRUD Django Ninja endpoints
- [ ] Task model with state machine (VALID_TRANSITIONS dict)
- [ ] Task endpoints: CRUD + transition + assign
- [ ] AuditLog model — tracks every mutation on Workflow and Task
- [ ] Migrations: migrate_schemas --tenant

### Phase 4: Documents + S3 Integration
- [ ] Document model (s3_key, content_type, size_bytes, summary, FKs)
- [ ] S3 presigned upload URL endpoint (tenant-namespaced keys)
- [ ] Document registration endpoint (after S3 upload)
- [ ] S3 presigned download URL endpoint (15-min expiry)
- [ ] Document deletion (S3 object + DB record)
- [ ] AuditLog integration for document operations

### Phase 5: AI Integration (Lambda)
- [ ] Lambda function: summarize_document handler (OpenAI)
- [ ] Lambda function: generate_tasks handler (OpenAI)
- [ ] Django endpoint: POST /api/documents/{id}/summarize (invokes Lambda)
- [ ] Django endpoint: POST /api/workflows/{id}/generate-tasks (invokes Lambda)
- [ ] Graceful degradation when AWS credentials missing

### Phase 6: Dashboard + Frontend Templates
- [ ] [P] Dashboard stats API endpoint
- [ ] [P] Base template (nav, tenant context, Pico CSS)
- [ ] Landing page (public tenant — signup/login)
- [ ] Login + Register pages
- [ ] Dashboard page (stats cards)
- [ ] Workflows list + detail pages (CRUD + state transitions + AI generate)
- [ ] Documents page (upload, list, download, summarize)
- [ ] Staff management page (admin only)

### Phase 7: Seed Data + Polish
- [ ] seed_demo.py script (public tenant, demo clinic, sample data)
- [ ] Superadmin tenant listing endpoint
- [ ] End-to-end manual testing
- [ ] black + ruff formatting pass

**Dependencies:** PostgreSQL 15+, Redis 7, AWS account (S3 + Lambda + IAM), OpenAI API key

[P] = can run in parallel
[NEEDS CLARIFICATION]: AWS credentials and OpenAI API key needed before Phase 5 — will STOP and ask user.

---

## Acceptance Criteria

Given a new user visits portal.localhost
When they fill in the signup form with clinic name, email, and password
Then a new tenant is created with its own PostgreSQL schema, a subdomain domain record is created, and the user is redirected to their clinic's dashboard

Given an authenticated admin on clinic1.localhost
When they create a new workflow with name and description
Then the workflow is saved in the tenant's schema and an AuditLog entry is created

Given a task with status "created"
When a user attempts to transition it to "completed"
Then the transition is rejected with a 400 error because "created" can only transition to "assigned" or "cancelled"

Given a task with status "assigned"
When a user transitions it to "in_progress"
Then the status updates, an AuditLog entry records the transition, and the task detail reflects the new status

Given an authenticated user on a tenant subdomain
When they request a presigned upload URL with filename "report.pdf"
Then the returned URL contains the S3 key `{tenant_schema}/{uuid}/report.pdf` and expires in 15 minutes

Given a document has been uploaded and registered
When a user clicks "Summarize"
Then the system invokes Lambda with the document text and saves the returned summary to the Document record

Given a user authenticated in tenant A
When they attempt to access tenant B's subdomain
Then TenantAccessMiddleware blocks the request and returns a 403

Given an admin on a tenant
When they add a staff member by providing their email address (and name/password for new users)
Then the user is created (or existing user found by email) with role=staff and associated with the current tenant (note: SPEC.md excludes email/SMS notifications — "by email" means identifying users by email address, not sending invitation emails)

Given the dashboard endpoint is called
When there are 3 workflows, 10 tasks (4 completed, 3 in_progress, 2 assigned, 1 created), and 5 documents
Then the response contains accurate counts for each category

Given a document exists in S3 and the database
When an admin deletes the document
Then both the S3 object and the database record are removed, and an AuditLog entry is created

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Schema-per-tenant migration complexity at scale (100+ tenants) | Medium | High | Accept for MVP; revisit if scaling beyond 100 tenants |
| OpenAI API dependency — pricing changes or rate limits | Medium | Medium | Abstract Lambda payload interface; provider-swappable later |
| Clinic staff upload documents with patient PII despite no-HIPAA scope | High | High | Add clear disclaimers in UI; consider future PII detection |
| AWS credential misconfiguration blocks S3/Lambda features | Medium | Medium | Graceful degradation; clear error messages; .env.example template |
| django-tenants + django-tenant-users version compatibility | Low | High | Pin versions; test early in Phase 1 |
| CSRF handling with Django Ninja endpoints called from templates | Medium | Low | Use Django's CSRF middleware; pass tokens in JS fetch calls |
| Subdomain routing not working in local dev (hosts file) | Low | Medium | Document /etc/hosts setup; provide setup script |

## Open Questions

- [ ] How should superadmin be identified? (Assumed: `is_superuser=True`)
- [ ] Should the signup flow be single-step (create tenant + user) or two-step? (Assumed: single-step)
- [ ] What does "invite staff" mean without email? (Assumed: admin creates account with credentials)
- [ ] Should documents be linkable to workflows/tasks at upload time or later? (Assumed: optional at upload, can link later)
- [ ] Is tenant deactivation/deletion in scope? (Assumed: out of scope for MVP)
- [ ] What is the Lambda request/response JSON schema for summarization and task generation?

## Security & Privacy

- **Tenant isolation**: PostgreSQL schema-per-tenant ensures complete data separation at the database level
- **Auth**: Session-based authentication stored in Redis; CSRF protection on all mutating endpoints
- **Access control**: TenantAccessMiddleware blocks cross-tenant access; role-based (admin/staff) controls on management endpoints
- **S3 security**: All objects namespaced by tenant schema name; presigned URLs expire after 15 minutes; bucket blocks all public access; server-side encryption (AES-256)
- **Secrets**: All credentials loaded from environment variables; `.env` in `.gitignore`
- **Audit trail**: AuditLog model tracks every state mutation — entity type, entity ID, action, performer, timestamp
- **Input validation**: Django Ninja Pydantic schemas validate all API inputs
- **PII risk**: Clinics may upload sensitive documents — UI disclaimers needed; future PII detection recommended

---

**Retrospective:** `docs/retrospectives/01-clinic-portal-mvp.md` (link after implementation)
