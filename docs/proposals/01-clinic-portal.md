# Feature Proposal: Multi-Tenant Clinic Management Portal

**Proposal Number:** 01
**Status:** Draft
**Author:** Claude Code + Klement
**Created:** 2026-03-30
**Target Branch:** `feature/clinic-portal-mvp`

---

## Problem Statement

Medical clinics need private, isolated workspaces to manage internal business processes — workflows, tasks, documents — with AI assistance. No open-source solution exists that combines multi-tenant isolation (schema-per-tenant) with AI-powered workflow automation in a Django stack.

This is being built as an interview demonstration for a Senior Frontend Dev / AI-Native Engineer role at a healthcare SaaS company using their exact tech stack (Django 5, Django Ninja, Django Tenants, PostgreSQL, Redis, AWS S3/Lambda).

[NEEDS CLARIFICATION]: Task transition permissions — can staff transition ANY task or only tasks assigned to them?
[NEEDS CLARIFICATION]: Document summarization — only plain text files, or should Lambda handle PDF extraction?
[NEEDS CLARIFICATION]: Registration flow — new user exists in shared schema only until they create/join a tenant?
[NEEDS CLARIFICATION]: Generate-tasks behavior — append to existing tasks or replace?
[NEEDS CLARIFICATION]: Testing strategy for multi-tenancy — django-tenants requires PostgreSQL, not SQLite test DBs.

## User Stories

- As a **clinic owner**, I want to sign up and create a private workspace so that my team's data is completely isolated from other clinics
- As a **clinic admin**, I want to invite staff by email so they can access our workspace without seeing other clinics
- As a **clinic admin**, I want to create workflows (e.g., "Patient Intake") so I can standardize business processes
- As a **clinic admin**, I want AI to auto-generate task checklists from a workflow description so I don't manually define every step
- As a **clinic admin**, I want to assign tasks to staff so everyone knows their responsibilities
- As a **staff member**, I want to transition tasks through statuses (assigned → in_progress → completed) so progress is tracked
- As a **staff member**, I want to upload documents and get AI summaries so I can quickly understand file contents
- As a **clinic admin**, I want a dashboard showing counts of workflows, tasks by status, documents, and staff for an at-a-glance view
- As a **clinic admin**, I want every state change logged in an audit trail for accountability
- As a **staff member**, I want secure, time-limited download URLs so files are never publicly exposed
- As a **superadmin**, I want to list all tenants to monitor platform usage

## Proposed Solution

### High-Level Approach

Clinics visit the portal landing page → sign up → create their clinic workspace (which provisions a PostgreSQL schema automatically). They get a subdomain (clinic1.localhost). Inside, they create workflows, add tasks, upload documents, and use AI to summarize documents and auto-generate task checklists. Every action is audit-logged.

### Technical Approach

- **Multi-tenancy:** django-tenants with schema-per-tenant + django-tenant-users for global auth (per CLAUDE.md rules 1-5)
- **API layer:** Django Ninja with Schema classes (per CLAUDE.md rule 1 — NEVER DRF)
- **Storage:** AWS S3 with presigned URLs, tenant-namespaced keys (per CLAUDE.md rules 6-7)
- **LLM:** AWS Lambda invoking OpenAI for summarization + task generation (per CLAUDE.md rule 8)
- **Frontend:** Django templates + vanilla JS + Pico CSS (per CLAUDE.md rule 15)
- **Auth:** Django session auth via Redis with CSRF protection
- **Observability:** AuditLog on every mutation (per CLAUDE.md rule 12)

### Alternatives Considered

1. **Django REST Framework instead of Django Ninja**: More ecosystem support and third-party packages. But the target company uses Django Ninja — matching their stack is non-negotiable. DRF adds serializer boilerplate that Ninja's Pydantic schemas avoid.

2. **Celery instead of Lambda for LLM calls**: More Pythonic, easier to debug locally. But the company's job description lists Lambda — demonstrating Lambda integration shows we can work with their infra. Lambda also means no background worker process to manage.

3. **Single-database multi-tenancy (shared schema + tenant_id)**: Simpler setup, no middleware complexity. But schema-per-tenant via django-tenants is what the company uses (django-tenants in their stack). Also provides stronger data isolation — critical for healthcare.

4. **React frontend**: More impressive UI interactions. But adds Node toolchain, build step, and time. Django templates with vanilla JS are faster to build, match the "backend is the priority" guidance from the recruiter, and demonstrate you don't over-engineer.

---

## Implementation Plan

### Phase 1: Infrastructure [P]
- [ ] [P] Docker Compose (PostgreSQL 15 + Redis 7)
- [ ] [P] AWS setup (S3 bucket + Lambda function + IAM user) → aws-setup-agent
- [ ] Django project scaffold (config/, manage.py, pyproject.toml)
- [ ] Settings (SHARED_APPS, TENANT_APPS, middleware, database, cache, URLs)
- [ ] /etc/hosts setup for subdomain routing

### Phase 2: Shared Models & Auth
- [ ] Tenant + Domain models (TenantBase, DomainMixin)
- [ ] User model (UserProfile extension)
- [ ] Auth endpoints (login, register, logout, me)
- [ ] create_public_tenant management command
- [ ] Tenant creation endpoint + provisioning service
- [ ] Staff management endpoints (list, invite, remove)

### Phase 3: Tenant Models & Business Logic
- [ ] [P] Workflow model + CRUD endpoints
- [ ] [P] AuditLog model
- [ ] Task model with state machine (VALID_TRANSITIONS)
- [ ] Task CRUD + transition + assign endpoints
- [ ] [P] Document model
- [ ] Document endpoints (presigned upload/download, register, delete)

### Phase 4: LLM Integration
- [ ] Lambda function (handler.py — OpenAI summarize + generate tasks)
- [ ] Document summarize endpoint (invokes Lambda)
- [ ] Workflow generate-tasks endpoint (invokes Lambda)

### Phase 5: Frontend
- [ ] [P] Base template + landing + login + register
- [ ] [P] Dashboard with stats cards
- [ ] Workflows page + workflow detail (task list + state transitions)
- [ ] Documents page (upload + summarize)
- [ ] Staff management page (admin only)

### Phase 6: Seed & Polish
- [ ] seed_demo.py (public tenant, superadmin, Sunrise Clinic, 2 staff, sample workflow + tasks + document)
- [ ] End-to-end manual test
- [ ] Run /audit-patterns full
- [ ] Write retrospective

**Dependencies:** django-tenants, django-tenant-users, django-ninja, boto3, openai (Lambda-side), httpx, pico.css

[P] = can run in parallel
[NEEDS CLARIFICATION]: Testing strategy — need PostgreSQL-based test fixtures, not SQLite

---

## Acceptance Criteria

```
Given a new user visits portal.localhost
When they register with email + password and create a clinic "Valley Health"
Then a PostgreSQL schema is created, a subdomain is provisioned, and they are the admin
```

```
Given an admin is logged into their clinic subdomain
When they create a workflow "Patient Intake" and click "Generate Tasks with AI"
Then Lambda is invoked and returns a list of tasks that are auto-created in the workflow
```

```
Given a staff member is assigned a task with status "assigned"
When they transition it to "in_progress"
Then the status changes AND an AuditLog entry is created with the old and new status
```

```
Given a staff member tries to transition a "completed" task to "in_progress"
When the transition endpoint is called
Then a 400 error is returned with "Cannot transition from completed to in_progress"
```

```
Given a user uploads a document via presigned URL
When they click "Summarize"
Then Lambda is invoked, OpenAI generates a summary, and it is stored on the document record
```

```
Given a user in Clinic A
When they try to access Clinic B's subdomain
Then TenantAccessMiddleware returns 403 Forbidden
```

```
Given an admin views the dashboard
When the stats endpoint is called
Then it returns counts of workflows, tasks by all 5 statuses, documents, and staff
```

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| django-tenants migration complexity | High | High | Test migrate_schemas early. Use context7 for latest docs. |
| Lambda cold start delays (5-15s) | Medium | Medium | Set Lambda memory to 256MB+ for faster cold starts. Show loading state in UI. |
| Over-engineering signal to interviewer | Medium | High | Narrate every decision — explain WHY each tech choice, not just WHAT. Keep frontend simple. |
| OpenAI API key cost during demo | Low | Low | Use gpt-3.5-turbo for demo (cheaper). Cache Lambda responses for repeated calls. |
| S3 presigned URL CORS issues | Medium | Medium | Configure CORS on S3 bucket during setup. Test upload flow early. |
| Multi-tenant test complexity | High | Medium | Use django-tenants test utilities. Test with PostgreSQL, not SQLite. |

## Open Questions

- [ ] Can staff members transition ANY task or only tasks assigned to them?
- [ ] Does document summarization handle only plain text, or should Lambda extract text from PDFs?
- [ ] When "Generate Tasks" is clicked on a workflow with existing tasks — append or replace?
- [ ] Is S3 object deletion synchronous (block response) or async (best-effort)?
- [ ] Should the audit log cover ALL mutations (workflow CRUD, doc upload/delete, staff changes) or only task transitions?

## Security & Privacy

- **Tenant isolation:** PostgreSQL schema-per-tenant via django-tenants. TenantAccessMiddleware blocks unauthorized cross-tenant access.
- **S3 isolation:** Keys namespaced by tenant schema name ({tenant_schema}/{uuid}/{filename}). No public access. Presigned URLs expire in 15 minutes. SSE-S3 encryption (AES-256).
- **Auth:** Session-based via Redis. CSRF on all mutating endpoints. SSL-ready config.
- **Secrets:** All credentials in .env (AWS keys, OpenAI key, DB passwords). .gitignore excludes .env. .env.example provided.
- **Audit trail:** AuditLog tracks every state mutation with entity, action, user, and timestamp. Immutable — no update/delete endpoints.
- **HIPAA note:** This demo does NOT handle patient data or PHI. No HIPAA compliance required. However, the architecture (audit logging, encryption, tenant isolation) demonstrates awareness of healthcare security requirements.

---

**Retrospective:** `docs/retrospectives/01-clinic-portal.md` (link after implementation)
