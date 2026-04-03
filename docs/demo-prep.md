# Clinic Portal -- Interview Demo Preparation

Position: Senior Frontend Dev (AI-Native Engineer)

---

## Part 1: Architecture Deep Dive

### 1.1 Multi-Tenancy: Schema-Per-Tenant

**How it works:**

The application uses `django-tenants` with PostgreSQL's native schema support to provide complete data isolation between clinics. When a clinic signs up (e.g., "Sunrise Clinic"), the system:

1. Creates a new PostgreSQL schema (e.g., `sunrise_clinic`)
2. Runs tenant-specific migrations inside that schema
3. Creates a domain record mapping `clinic1.localhost` to that tenant

Every incoming request hits `TenantMainMiddleware` at position 0 in the middleware stack. This middleware inspects the `Host` header (the subdomain), looks up the corresponding `Domain` record, and sets `request.tenant`. From that point forward, the ORM automatically scopes all queries to that tenant's schema -- `Workflow.objects.all()` in tenant A only returns tenant A's workflows. No manual filtering needed.

**Shared vs Tenant apps:**

- SHARED_APPS (stored in `public` schema): `tenants` (Tenant + Domain models), `users` (global User model), `tenant_users.permissions`
- TENANT_APPS (duplicated per schema): `workflows` (Workflow, Task, AuditLog), `documents`, `dashboard`, `search`

The `INSTALLED_APPS` list is computed dynamically: `list(SHARED_APPS) + [app for app in TENANT_APPS if app not in SHARED_APPS]`

**Why NOT row-level isolation:**

- Schema-per-tenant gives you database-level guarantees -- even a bug in your ORM query cannot leak data across tenants because PostgreSQL's `search_path` is set per-connection
- With row-level isolation you need `tenant_id` foreign keys on every model and must remember to filter every query -- one missed `.filter(tenant=...)` is a data leak
- Schema-per-tenant allows per-tenant migrations, per-tenant backups, and per-tenant performance tuning
- Compliance: for a medical clinic portal, you can point to the database schema as proof of isolation, rather than relying on application-layer filtering

**Key middleware stack (order matters):**

```
Position 0: TenantMainMiddleware      -- resolves tenant from subdomain
Position 6: PasswordResetMiddleware   -- enforces password reset for invited staff
Position 7: TenantAccessMiddleware    -- blocks users who don't belong to this tenant
```

### 1.2 State Machine: VALID_TRANSITIONS

The Task model enforces a strict finite state machine at the model level:

```
created -> assigned -> in_progress -> completed
  \          \            \
   cancelled  cancelled    cancelled
```

The `VALID_TRANSITIONS` dict lives directly on the `Task` model class:

```python
VALID_TRANSITIONS = {
    "created": ["assigned", "cancelled"],
    "assigned": ["in_progress", "cancelled"],
    "in_progress": ["completed", "cancelled"],
    "completed": [],    # Terminal state
    "cancelled": [],    # Terminal state
}
```

The `transition_to()` method:
1. Checks if `new_status` is in `VALID_TRANSITIONS[self.status]`
2. If invalid, raises `ValueError` (logged at WARNING level with task ID and attempted transition)
3. If valid, saves the new status and creates an immutable `AuditLog` entry with `status_change:old->new`

**Why enforce at model level, not API level:**

- Defense in depth -- even if someone calls `task.status = 'completed'` and `task.save()` directly, the transition logic still fires through `transition_to()`
- The API layer is a second check, but the model is the source of truth
- The frontend also mirrors `VALID_TRANSITIONS` in JavaScript for immediate UI feedback (rendering only valid transition buttons), but the backend is authoritative

### 1.3 Audit Logging: Immutable Records

`AuditLog` is a tenant-scoped model that tracks every state mutation in the system. It is engineered to be truly immutable:

```python
def save(self, *args, **kwargs):
    if self.pk is not None:
        raise ValueError("AuditLog entries are immutable and cannot be updated.")
    super().save(*args, **kwargs)

def delete(self, *args, **kwargs):
    raise ValueError("AuditLog entries are immutable and cannot be deleted.")
```

- `save()` with an existing PK raises ValueError -- no updates
- `delete()` always raises ValueError -- no deletions
- The admin class (`AuditLogAdmin`) blocks add/change/delete in Django Admin as well
- Indexed on `(entity_type, entity_id)` for fast entity-specific lookups and `(-timestamp)` for recent-first queries

Every mutation in the system (workflow CRUD, task transitions, task assignments, document uploads/deletes/summaries, search queries) creates an AuditLog entry with: entity_type, entity_id, action string, JSON details, performing user, and timestamp.

### 1.4 S3 Integration: Presigned URLs + AI Summarization

**Upload flow (3-step presigned URL pattern):**

1. Frontend calls `POST /api/documents/upload-url` with filename and content_type
2. Backend generates a presigned PUT URL with 900s (15 min) expiry, S3 key namespaced as `{tenant_schema}/{uuid}/{filename}`
3. Frontend uploads directly to S3 using `fetch(presignedUrl, { method: 'PUT', body: file })` -- Django never touches the file bytes
4. Frontend calls `POST /api/documents/` to register the document in the database

**Security enforcement:**

- S3 key MUST start with the tenant's schema name -- validated on both upload-url generation and document registration
- `create_document()` checks `data.s3_key.startswith(f"{tenant_schema}/")` and returns 403 if mismatched
- Download URLs are also presigned (15 min expiry) and cached in Redis for 14 minutes

**AI Summarization:**

- The `_summarize_with_claude()` function sends the actual document to Claude Haiku 4.5
- PDFs and images are sent as native content blocks (base64-encoded) -- Claude reads them directly
- Text files are sent as text content blocks (first 10,000 chars)
- The prompt enforces plain text output (no markdown), 200 word limit
- All LLM output is sanitized with `strip_tags()` before storage (treating LLM output as untrusted input)
- Results cached in Redis for 24 hours with key `llm:summary:{doc_id}`
- If Claude is unavailable, graceful fallback: "Could not summarize this document."

**Task Generation:**

- Admin clicks "Generate Tasks with AI" on a workflow
- Backend sends workflow name + description to Claude Haiku with k-shot examples (patient check-in, lab results)
- Output must be valid JSON: `{"tasks": [{"title": "...", "description": "..."}]}`
- Reflexion pattern: if JSON validation fails, retries once with the error context appended to the conversation
- Generated tasks are cached for 1 hour by workflow ID + description hash

### 1.5 Clinical QA Search: ClinicalTrials.gov + PubMed Integration

**RAG Pipeline:**

```
User query -> Query rewriting (Claude Haiku) -> Parallel fetch -> Context formatting -> RAG summarization
```

1. **Query rewriting**: Claude Haiku optimizes the raw query -- expands abbreviations (T2DM -> type 2 diabetes mellitus, NSCLC -> non-small cell lung cancer), fixes spelling, adds medical synonyms. Cached 24 hours.

2. **Parallel fetch** using `asyncio.gather` with `httpx.AsyncClient`:
   - ClinicalTrials.gov v2 API: `GET /api/v2/studies?query.term={query}&pageSize=30`
   - PubMed E-utilities: `esearch` (get PMIDs) then `esummary` (get paper details)
   - Both APIs are free, no keys needed
   - Results cached: trials 6 hours, papers 7 days

3. **RAG summarization**: Formats all trials and papers as context with citation markers ([NCT...], [PMID:...]), sends to Claude Haiku with a structured prompt (OVERVIEW / TRIALS / RESEARCH / BOTTOM LINE), temperature 0.2 for factual output.

4. **Fallback**: If LLM is unavailable, `_fallback_summary()` generates a basic count summary without AI.

**Conversational Chat (multi-turn):**

The chat module implements a 3-call pattern:
- Call 1: Intent classification (search / follow_up / general) -- 10 max tokens, temperature 0.0
- Call 2: Query rewriting if search intent
- Call 3: Response generation with full context (thread's cached trials + papers, last 10 messages, user's recent search history)

Thread stores `trials_context` and `papers_context` as JSONFields, so follow-up questions use cached data without re-searching. Citations in the frontend are auto-linked: `[NCT12345]` becomes a hyperlink to clinicaltrials.gov, `[PMID: 12345]` links to PubMed.

### 1.6 Auth: Session-Based, Tenant-Scoped

**Authentication stack:**

- `django-tenant-users` provides `UserProfile` base class -- User model is shared (in `public` schema)
- `UserBackend` from `tenant_users.permissions.backend` handles authentication
- Session stored in Redis via `django.contrib.sessions.backends.cache`, with tenant-aware cache keys (`django_tenants.cache.make_key`)
- CSRF token delivered via meta tag AND cookie, with `SESSION_COOKIE_HTTPONLY = True` and `SESSION_COOKIE_SAMESITE = "Lax"`

**PasswordResetMiddleware:**

When an admin invites a staff member via `POST /api/staff/invite`:
1. If the user does not exist, a new user is created with a random temporary password (`secrets.token_urlsafe(16)`) and `must_reset_password = True`
2. `PasswordResetMiddleware` intercepts ALL requests from users with `must_reset_password = True`
3. Only `/api/auth/reset-password` and `/api/auth/logout` are exempt
4. Everything else returns 403 with "Password reset required"
5. After password reset, the flag is cleared and the user is re-authenticated to keep the session valid

**Tenant access control:**

- `TenantAccessMiddleware` (from django-tenant-users) blocks users who are not associated with the current tenant
- Staff are added to tenants via `tenant.add_user(user)` and removed via `tenant.remove_user(user)`
- Cannot remove the tenant owner (raises `DeleteError`)

---

## Part 2: Expected Interview Questions & Answers

### Q1: Why Django instead of FastAPI/Node?

"Django was the right choice here for several reasons. First, the multi-tenancy requirement -- `django-tenants` is a mature, battle-tested library that gives you schema-per-tenant isolation out of the box with PostgreSQL. There is no equivalent in FastAPI or Express that handles schema switching, tenant-aware migrations, and ORM query scoping this cleanly.

Second, Django's ORM, admin panel, and migrations system save a lot of time for a CRUD-heavy application with complex model relationships -- workflows with nested tasks, documents linked to workflows, audit logs for every mutation. The admin panel alone gives you a built-in back-office tool for debugging tenant data.

Third, the template system. For this project, server-rendered Django templates with vanilla JS gave us faster development velocity than a React SPA -- no build step, no client-side routing, no state management library. The pages are mostly CRUD forms and data tables, which templates handle well.

For the AI integration, we use async where it matters (httpx for parallel API calls to ClinicalTrials.gov and PubMed) without needing the entire framework to be async. Django 5.x supports async views natively, but we use sync views with a ThreadPoolExecutor for the search pipeline, which keeps the code simple."

### Q2: Why schema-per-tenant vs row-level isolation?

"Schema-per-tenant gives you database-level data isolation, which is a stronger guarantee than application-level filtering. With row-level isolation, every query needs a `WHERE tenant_id = X` clause -- if a developer forgets one filter, you have a cross-tenant data leak. With schema isolation, PostgreSQL's `search_path` is set per-connection, so even a raw SQL query without any tenant filter will only see the current tenant's data.

The tradeoffs are real -- schema-per-tenant has higher overhead per tenant (each schema has its own set of tables and indexes), so it does not scale to thousands of tenants. But for a B2B SaaS portal serving medical clinics, you are looking at hundreds of tenants, not millions. Each clinic has meaningful data volume, and the isolation guarantee matters more than scale efficiency.

Schema-per-tenant also gives you independent migration paths per tenant, per-tenant database backups, and the ability to move a high-value tenant to a dedicated database if needed."

### Q3: How do you prevent cross-tenant data leaks?

"There are four layers of defense:

1. **Database layer**: `django-tenants` sets PostgreSQL's `search_path` to the tenant's schema on every connection. All ORM queries automatically scope to that schema.

2. **Middleware layer**: `TenantMainMiddleware` at position 0 resolves the tenant from the subdomain. `TenantAccessMiddleware` blocks users who are not associated with the current tenant.

3. **Application layer**: S3 keys are namespaced by tenant schema name (`{tenant_schema}/{uuid}/{filename}`), and the API validates this prefix on both upload and download. The `create_document` endpoint checks `data.s3_key.startswith(f'{tenant_schema}/')` and returns 403 if it does not match.

4. **Cache layer**: Redis cache keys use `django_tenants.cache.make_key`, which automatically prefixes keys with the tenant schema name. So `cache.get('workflows:list')` in tenant A and tenant B hit different Redis keys."

### Q4: Why Django Ninja over DRF?

"Django Ninja gives us Pydantic-based schema validation with type hints, which is closer to FastAPI's developer experience but runs inside Django. The key advantages over DRF for this project:

- Schema classes are plain Pydantic models, not DRF serializers -- less boilerplate, better IDE support with type checking
- Auto-generated OpenAPI docs at `/api/docs` without any additional configuration
- Native async view support (we use this for httpx calls in the search pipeline)
- CSRF handling is per-auth-class (`SessionAuth.csrf=True`), which is more explicit than DRF's blanket CSRF settings

One lesson learned: we do not pass `csrf=True` to the `NinjaAPI()` constructor -- CSRF is handled by the `django_auth` authenticator. We verify API parameters exist in the installed version before using them."

### Q5: How does the AI integration work without calling OpenAI directly?

"The architecture has two modes. In production, LLM calls go through AWS Lambda (`boto3.client('lambda').invoke()`) -- Django sends a payload with the text and task type, Lambda handles the Claude API call, and returns the result. This keeps API keys out of the Django container and lets Lambda handle retries, timeouts, and scaling independently.

In local development, when `LAMBDA_SUMMARIZE_ARN` is empty, the code falls back to calling the Anthropic API directly via `urllib.request`. This dual-path design means developers can work locally without AWS infrastructure.

For document summarization, Claude Haiku 4.5 reads PDFs and images natively -- we send the raw file as a base64 content block, so there is no text extraction step for PDFs. All LLM output is treated as untrusted input: sanitized with `strip_tags()`, length-checked (500 word max with truncation), and empty-checked before storage.

Task generation uses k-shot prompting with two examples and a reflexion retry -- if the first response fails JSON validation, we send a second request with the error context appended, giving the model one chance to self-correct."

### Q6: How do you handle file uploads securely?

"Django never touches the file bytes. The upload flow is three steps:

1. The frontend requests a presigned PUT URL from the API, which generates an S3 key with the pattern `{tenant_schema}/{uuid}/{filename}` and signs it for 15 minutes
2. The frontend uploads directly to S3 using `fetch()` with the presigned URL -- the file goes straight from the browser to S3
3. The frontend registers the document in our database with the S3 key, and we validate that the key starts with the current tenant's schema name

This means the Django server never proxies file content, which keeps it lightweight and avoids memory issues with large files. Download URLs are also presigned and cached in Redis for 14 minutes (just under the 15-minute expiry).

Content type validation happens at the API level -- we only allow PDFs, images, text files, and Word documents. The allowed types map directly to what Claude Haiku can read natively for summarization."

### Q7: What is your testing strategy for multi-tenant apps?

"The testing strategy splits along the shared/tenant boundary:

- **Shared app tests** (tenants, users): use standard `django.test.TestCase`. These test user registration, authentication, tenant creation, and staff invitation -- all of which operate on the public schema.

- **Tenant app tests** (workflows, documents, dashboard, search): use `django_tenants.test.cases.TenantTestCase`, which automatically creates a test tenant, sets up the schema, runs the test, and tears down. This ensures every test runs in a proper tenant context with schema isolation.

Each test class focuses on one domain: workflow CRUD and transitions, document upload/download flow, dashboard stats aggregation, search pipeline with mocked external APIs. The state machine tests specifically verify that invalid transitions raise `ValueError` and valid transitions create audit log entries.

For E2E: Playwright with Chromium headless tests the full frontend flow -- login, navigate to workflows, create a workflow, generate tasks, transition task status."

### Q8: How did you use AI (Claude Code) in development?

"This project was built entirely with AI-native development using Claude Code as the primary development tool. The CLAUDE.md file at the project root defines a complete SDLC flow that Claude Code follows:

1. **PM Agent orchestration**: Claude Code acts as a project manager, reading the spec, breaking work into phases, and delegating to specialist agents (django-tenants-agent, django-ninja-agent, s3-lambda-agent)
2. **TDD workflow**: Every feature starts with writing tests first, then implementing, then verifying
3. **Architecture rules**: 19 hard rules in CLAUDE.md that every code change must follow -- things like 'never import rest_framework', 'TenantMainMiddleware must be position 0', 'S3 keys must be namespaced by tenant'
4. **Lessons learned section**: After each retrospective, anti-patterns are captured in CLAUDE.md so Claude Code never makes the same mistake twice. For example, lesson 16: 'django-ninja CSRF is handled per-auth-class, not via NinjaAPI(csrf=True)'
5. **Post-implementation rule**: After any code generation, Claude Code runs `black . && ruff check . --fix` and `uv run python manage.py test` before moving on

The entire project -- 10 templates, 6 Django apps, ~4,000 lines of Python, full test suite -- was built through this AI-orchestrated workflow. The key insight is that AI-native development is not about generating code faster; it is about encoding architectural decisions and lessons learned so the AI maintains consistency across a complex codebase."

### Q9: Why vanilla JS instead of React?

"Three reasons:

1. **No build step**: The frontend is Django templates with Pico CSS from CDN and a single `app.js` file. There is no webpack, no npm, no node_modules. This means deployment is simpler and the development loop is faster -- edit a template, refresh the browser.

2. **The pages are CRUD forms and data tables**: The workflows page is a list of cards, a dialog for creating, and a dialog for detail view. The documents page is an upload form and a list. These are not complex interactive UIs -- they are forms that call API endpoints and render the results.

3. **Progressive enhancement**: The JS in each page template is self-contained (inline `<script>` blocks in `{% block extra_js %}`), uses the shared `apiFetch()` wrapper for CSRF handling, and uses `escapeHtml()` for XSS prevention. The chat page is the most complex -- it has a thread sidebar, message rendering with citation linking, and search results panel -- but it is still about 200 lines of vanilla JS.

The only place where React might add value is the chat interface, which has real-time-feeling interactions. But since we are not doing WebSockets (the chat is request-response via REST), vanilla JS handles it fine."

### Q10: How would you scale this?

"Scaling has three dimensions:

1. **Tenant count**: Schema-per-tenant works well up to a few hundred tenants. Beyond that, you would shard -- route tenants to different database servers based on tenant ID. django-tenants supports this with custom database routing.

2. **Request throughput**: Add gunicorn workers behind a load balancer, use Redis clustering for session/cache, and put CloudFront in front of S3 for document downloads. The presigned URL pattern means S3 handles file serving load, not Django.

3. **AI pipeline**: Move from synchronous Claude API calls to async via Lambda or a task queue. Right now, document summarization and search summarization block the request. You could return a 202 Accepted with a job ID and poll for results, or use WebSockets for real-time updates.

4. **Database**: Connection pooling via PgBouncer, read replicas for dashboard stats and search history, and archiving old audit logs to cold storage (S3/Glacier).

The key architectural advantage is that the presigned URL pattern and schema isolation already handle the two biggest scaling concerns (file storage and data isolation) at the infrastructure level rather than the application level."

### Q11: Security considerations?

"The security model has several layers:

- **Authentication**: Session-based with HttpOnly cookies, SameSite=Lax. CSRF token in both meta tag and cookie. Passwords hashed by Django's default (PBKDF2).
- **Authorization**: Admin/staff roles enforced at the API level via `_require_admin()` helper. Tenant access enforced by `TenantAccessMiddleware`.
- **Data isolation**: Schema-per-tenant at database level, tenant prefix validation on S3 keys, tenant-scoped Redis cache keys.
- **Input sanitization**: All LLM output sanitized with `strip_tags()` before storage. Frontend uses `escapeHtml()` for all user-generated content. Query inputs sanitized to remove injection characters (`< > { } | \ ^ ~ [ ]`).
- **Password management**: Invited staff get random temporary passwords (`secrets.token_urlsafe(16)`) and must reset before accessing any functionality.
- **Secrets management**: All credentials from `os.environ` via `.env` file. SECRET_KEY validation in production (raises ValueError if empty when DEBUG=False).
- **Headers**: `X_FRAME_OPTIONS = 'DENY'`, `SECURE_CONTENT_TYPE_NOSNIFF = True`, `SESSION_COOKIE_SECURE = not DEBUG`.

What I would add next: rate limiting on login attempts, Content-Security-Policy headers, and request signing for the Lambda invocations."

### Q12: State management in workflows?

"State management happens at three levels:

1. **Model level**: The `VALID_TRANSITIONS` dict on the Task model is the single source of truth. The `transition_to()` method validates, saves, and creates an audit log atomically.

2. **API level**: The `POST /api/tasks/{id}/transition` endpoint catches `ValueError` from invalid transitions and returns 400 with the error message. It also invalidates the dashboard stats cache on every transition.

3. **Frontend level**: The workflows template mirrors `VALID_TRANSITIONS` in JavaScript and only renders transition buttons for valid next states. This gives immediate visual feedback -- a 'completed' task shows no buttons because it is a terminal state.

Session memory (`request.session['recent_actions']`) tracks the last 5 user actions for the dashboard's 'recent activity' display. This is temporary -- it clears on logout. Permanent history lives in AuditLog (immutable, per-tenant) and SearchHistory (per-user, per-tenant)."

---

## Part 3: Demo Script Talking Points

### Landing Page (portal.localhost)

- "This is the public portal where clinics sign up. When you register and create a clinic, the system provisions a new PostgreSQL schema, runs tenant-specific migrations, and creates a subdomain mapping -- all through django-tenants' `provision_tenant()` function."
- "Notice the URL routing -- portal.localhost uses `PUBLIC_SCHEMA_URLCONF` for signup flows, while clinic subdomains use `ROOT_URLCONF` for the tenant-specific dashboard. The middleware resolves which URL configuration to use based on the incoming subdomain."

### Login Page

- "Authentication uses django-tenant-users' `UserBackend`, which handles the global user model across schemas. When you log in on a clinic's subdomain, the session is stored in Redis with tenant-aware cache keys, so session data is isolated per tenant."
- "If this user was invited by an admin, they would see a forced password reset screen -- our `PasswordResetMiddleware` intercepts all requests for users with `must_reset_password=True` and only allows the reset-password and logout endpoints."

### Dashboard

- "The dashboard aggregates stats across the current tenant -- workflow count, document count, staff count, and a task breakdown by status. This data is cached in Redis for 60 seconds with automatic invalidation when any CRUD operation happens."
- "The 'recent actions' section comes from session memory -- the last 5 actions tracked by our `track_action()` helper, which stores them in `request.session['recent_actions']`. This is ephemeral -- it clears on logout -- while the permanent audit trail lives in the AuditLog model."
- "Notice the tasks-by-status breakdown -- this maps directly to the state machine. You can see how many tasks are in each state, which gives clinic managers visibility into workflow bottlenecks."

### Workflows Page

- "Each workflow has a name, description, and a list of tasks. The workflow detail dialog shows tasks with status badges and transition buttons. Watch the buttons -- they only show valid next states based on the `VALID_TRANSITIONS` dictionary, which is mirrored in the frontend JavaScript."
- "Let me click 'Generate Tasks with AI' -- this sends the workflow description to Claude Haiku with k-shot examples and returns structured JSON. The backend validates the JSON, and if validation fails, uses a reflexion retry where it sends the error back to Claude for self-correction. Generated tasks are cached for 1 hour by workflow ID and description hash."
- "When I transition a task from 'created' to 'assigned', three things happen atomically: the status updates, an AuditLog entry is created recording the state change, and the dashboard stats cache is invalidated."

### Documents Page

- "File uploads use a 3-step presigned URL pattern. Watch the network tab -- the frontend first calls our API to get a presigned PUT URL, then uploads directly to S3 (Django never touches the file bytes), then registers the document in our database. The S3 key is namespaced by tenant schema: `{tenant_schema}/{uuid}/{filename}`."
- "The 'Summarize with AI' button sends the document to Claude Haiku 4.5, which reads PDFs and images natively -- no text extraction needed. The summary is sanitized with `strip_tags()` before storage because we treat all LLM output as untrusted input. Summaries are cached in Redis for 24 hours."
- "Notice the download also uses presigned URLs -- the download URL is cached for 14 minutes (just under the 15-minute S3 presigned URL expiry). Django never proxies file content."

### Staff Management Page

- "Only admins see this page -- the nav link is conditionally rendered with `{% if user.role == 'admin' %}`. The API endpoints enforce the same check server-side via `_require_admin()`."
- "When inviting a new staff member, if the email does not exist, we create a user with `secrets.token_urlsafe(16)` as a temporary password and set `must_reset_password=True`. The `PasswordResetMiddleware` ensures they cannot access anything until they change their password."
- "Staff are added to the tenant via `tenant.add_user(user)` from django-tenant-users. Removing staff calls `tenant.remove_user(user)`, which only removes the tenant association -- it does not delete the user globally. You cannot remove the tenant owner."

### Clinical Search Page

- "This is the RAG pipeline in action. When you search for something like 'SGLT2 inhibitors heart failure', the query goes through three stages: AI query rewriting (Claude optimizes the search terms, expanding abbreviations like 'HF' to 'heart failure'), parallel fetching from ClinicalTrials.gov and PubMed using httpx async, and then RAG summarization where all the trial and paper data is formatted as context and sent to Claude for a structured summary."
- "Notice the 'Searched as' text below the summary -- that shows the rewritten query. The filters on the trials column let you filter by recruitment status. Both data sources are cached -- trials for 6 hours, papers for 7 days -- so repeat searches are instant."
- "Every search creates both a `SearchHistory` record (permanent, per-user, per-tenant) and an `AuditLog` entry. The search history table at the bottom is clickable -- clicking a past search re-runs it."

### Clinical QA Chat Page

- "This is a multi-turn conversational interface built on the same search infrastructure. The AI uses a 3-call pattern: first it classifies intent (is the user searching, following up, or just chatting), then rewrites the query if needed, then generates a response with the full conversation context."
- "Notice the thread sidebar -- conversations are persisted in `ChatThread` and `ChatMessage` models. The thread stores the search context (trials + papers) as JSON, so follow-up questions use cached data without re-searching. The last 10 messages are sent to Claude as conversation history."
- "Citations in the chat bubbles are auto-linked -- `[NCT12345]` becomes a clickable link to clinicaltrials.gov, and `[PMID: 12345]` links to PubMed. This happens with a regex replace in the `renderMessage()` function after HTML escaping."

### Architecture Walkthrough (if asked to show code)

- "The `CLAUDE.md` file at the root is the project's architectural constitution -- 19 rules that every code change must follow, plus lessons learned from retrospectives. This is how we maintain consistency in an AI-native development workflow."
- "Look at `apps/documents/services.py` -- every external call (S3, Lambda, Claude API) has structured error handling for ClientError, timeout, and credentials errors. We never let exceptions propagate to the user; we always return structured JSON errors with graceful fallbacks."
- "The Redis caching strategy is documented in the spec with specific TTLs per cache type and explicit invalidation triggers. For example, workflow list cache (30s) is invalidated on workflow create/update/delete, and dashboard stats cache (60s) is invalidated on any CRUD operation. All keys are tenant-scoped via `django_tenants.cache.make_key`."
