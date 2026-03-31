# Interview Video Script — 30 Minutes
## Senior Backend Engineer: Clinical QA System Demo

> **Goal**: Show you designed and understand every layer.
> Don't just demo — explain WHY you made each decision.
> Every section maps to an interview question they'll ask.

---

## [0:00 – 2:00] OPEN — Problem Statement

**SHOW**: Landing page at `http://portal.localhost:8000/`

**SAY**:
> "The challenge: build a clinical QA system where medical clinics
> search real medical databases and get AI-summarized answers.
> Multi-tenant — every clinic's data fully isolated.
>
> I built this as a production-grade system, not a demo.
> Let me walk you through the architecture, then show it live."

---

## [2:00 – 6:00] MULTI-TENANCY — "How do you isolate tenant data?"

**SHOW**: `config/settings.py` — middleware + apps

**SAY**:
> "Three approaches to multi-tenancy: database-per-tenant,
> schema-per-tenant, and row-level with a tenant_id column.
>
> I chose schema-per-tenant. Why? Database-per-tenant has too
> much operational overhead — N databases to backup and migrate.
> Row-level tenancy is one missing WHERE clause away from a
> data leak. Schema-per-tenant gives me hard isolation at the
> PostgreSQL level — the search_path prevents queries from
> reaching another tenant's tables — while sharing one database
> and one connection pool.
>
> TenantMainMiddleware at position zero resolves the tenant from
> the subdomain. Before authentication, before CSRF, before
> anything — the database context is set. Every ORM query from
> that point is automatically scoped.
>
> SHARED_APPS — tenants and users live in the public schema
> because users are global, one person can belong to multiple
> clinics. TENANT_APPS — workflows, documents, search — each
> clinic has their own tables in their own schema."

**SHOW**: Terminal proof

```bash
curl -s http://clinic1.localhost:8000/api/workflows/ -b c1.txt
# → Patient Intake

curl -s http://clinic2.localhost:8000/api/workflows/ -b c2.txt
# → Lab Processing (DIFFERENT DATA)

curl -s http://clinic2.localhost:8000/api/workflows/ -b c1.txt
# → {"detail": "Unauthorized"} (CROSS-TENANT BLOCKED)
```

**SAY**:
> "Two clinics, same server, completely different data. When I
> use Clinic 1's session on Clinic 2's subdomain — blocked.
> TenantAccessMiddleware verifies user membership."

---

## [6:00 – 9:00] STATE MACHINE + AUDIT — "How do you handle workflow state?"

**SHOW**: `apps/workflows/models.py` — Task model

**SAY**:
> "I get asked: why not just a status field with if/elif?
> Because ad-hoc status updates are unmaintainable and error-prone.
>
> VALID_TRANSITIONS is a dictionary — single source of truth.
> Created goes to Assigned or Cancelled. Completed is terminal.
> The transition_to method validates, updates, and creates an
> AuditLog entry in the same transaction. If the audit write
> fails, the transition rolls back.
>
> AuditLog is immutable — I override save() and delete() to
> raise ValueError. You can't update or delete audit records.
> In healthcare-adjacent systems, this is non-negotiable.
> Regulatory bodies audit these trails."

**SHOW**: The `VALID_TRANSITIONS` dict + `transition_to()` method

---

## [9:00 – 11:00] CACHING — "What's your caching strategy?"

**SHOW**: `apps/dashboard/api.py` — cache.get/cache.set

**SAY**:
> "Cache-aside pattern with Redis. On read, check cache first.
> On miss, query database, populate cache, return. On write,
> invalidate the key.
>
> Critical for multi-tenancy: every cache key is namespaced by
> tenant schema via django_tenants.cache.make_key. Clinic A
> and Clinic B can both cache 'dashboard:stats' — they'll never
> collide.
>
> Tiered TTLs: dashboard 60 seconds, workflow list 30 seconds,
> S3 download URLs 14 minutes — shorter than the 15-minute
> presigned URL expiry so we never serve an expired link.
> LLM summaries 24 hours — same document, same summary,
> expensive to regenerate.
>
> Cache stampede prevention: TTLs are short enough that
> stale data expires quickly even if invalidation fails."

**SHOW**: Terminal — cache speedup proof

```bash
# First call: 0.67s (cache miss — hits PubMed + ClinicalTrials)
# Second call: 0.0009s (cache hit — 750x faster)
```

---

## [11:00 – 16:00] RAG PIPELINE — "Explain your RAG architecture"

**SHOW**: `apps/search/services.py`

**SAY**:
> "This is the core feature — Retrieval-Augmented Generation.
> Not chunking and vector stores — that's for document corpus
> search. This is live retrieval from real medical databases.
>
> Step 1: Query rewriting. Claude Haiku expands abbreviations —
> T2DM becomes type 2 diabetes mellitus. This improves search
> recall dramatically.
>
> Step 2: Parallel retrieval. asyncio.gather runs two httpx
> calls simultaneously — ClinicalTrials.gov and PubMed NCBI.
> Two API calls, one round-trip time. 10-second timeout each.
>
> Step 3: Context formatting. Each trial gets an [NCT...] tag,
> each paper gets a [PMID:...] tag. These become the citations.
>
> Step 4: Augmented generation. The prompt tells Claude:
> answer ONLY from the provided context, cite every claim,
> never make unsupported statements. Temperature 0.2 for
> factual output. If no results, say so — don't hallucinate.
>
> Step 5: Output validation. strip_tags on all LLM output
> before storage. Length check — truncate at 500 words.
> Empty check — return 'Summary unavailable', never store
> empty string."

**SHOW**: Browser — live search

**DO**: Go to `http://clinic1.localhost:8000/search/`
**TYPE**: `SGLT2 inhibitors heart failure`
**WAIT**: Results appear

**SAY**:
> "10 trials from ClinicalTrials.gov, 10 papers from PubMed.
> AI summary with citations — every [NCT...] links to the real
> trial page, every [PMID:...] links to the real PubMed paper."

**DO**: Click an [NCT...] link → opens ClinicalTrials.gov
**DO**: Click a [PMID:...] link → opens PubMed

> "Real data, real citations, real links. Not hallucinated."

---

## [16:00 – 19:00] CHAT — "How do you handle multi-turn conversations?"

**SHOW**: Browser — `http://clinic1.localhost:8000/chat/`

**DO**: Type `What are the latest CAR-T therapy trials?`
**WAIT**: Response with citations

**SAY**:
> "First message triggers a search. The thread now stores
> 10 trials and 10 papers as context.
>
> The memory architecture: temporary memory is the thread's
> trials_context and papers_context — JSON fields that persist
> across requests but reset when a new topic is detected.
> Permanent memory is every ChatMessage row — full conversation
> history in the database."

**DO**: Type `Which ones are recruiting?`

**SAY**:
> "Follow-up — no new search. The AI detects this is a
> refinement, not a new topic. It filters from the existing
> context. Faster response, no API calls."

**DO**: Type `Search for GLP-1 instead`

**SAY**:
> "Trigger words detected — 'search for'. New search, context
> updated. The AI now has GLP-1 data, not CAR-T."

---

## [19:00 – 22:00] SECURITY — "How do you prevent XSS and ensure isolation?"

**SHOW**: `apps/documents/services.py` — strip_tags on LLM output

**SAY**:
> "Every layer enforces isolation independently. Database:
> schema search_path. Cache: tenant-prefixed keys. S3:
> tenant-prefixed object keys, validated server-side on both
> upload and download. API: user-tenant membership check.
>
> LLM output is treated as untrusted input. strip_tags before
> every database write. The same defense we use against user XSS.
>
> Session cookies: HttpOnly prevents JavaScript access.
> SameSite=Lax prevents cross-site request forgery. Secure=True
> in production ensures HTTPS only.
>
> The AuditLog is immutable. You can't cover your tracks."

---

## [22:00 – 25:00] TESTING — "What's your testing strategy?"

**SHOW**: Terminal — run tests live

```bash
uv run python manage.py test --noinput -v1
# → 139 passed

uv run pytest tests/e2e/ -q
# → 71 passed
```

**SAY**:
> "210 total tests. Three layers.
>
> Unit tests: model logic, state machine transitions, LLM
> output validation. Shared apps use TestCase, tenant apps use
> TenantTestCase — which creates a real PostgreSQL schema for
> each test. This is critical — you can't test schema isolation
> with SQLite.
>
> Integration tests: API endpoints with mocked external services.
> Every boto3 call, every httpx call is mocked. We test the
> happy path AND the failure path — Lambda timeout, S3 error,
> malformed JSON from the LLM.
>
> E2E tests: Playwright with Chromium headless. Login flows,
> clinical search, chat conversations, cross-tenant isolation,
> mobile viewport. Even SQL injection and XSS edge cases."

---

## [25:00 – 28:00] DOCUMENTS + S3 — "How do you handle file uploads?"

**SHOW**: Browser — Documents page

**SAY**:
> "Django never touches file bytes. The flow:
>
> 1. Frontend asks for a presigned PUT URL
> 2. Django generates the URL with a tenant-namespaced S3 key
> 3. Frontend uploads directly to S3
> 4. Frontend tells Django 'upload done' with the S3 key
> 5. Django validates the key starts with the tenant's schema name
>
> Presigned URLs expire in 15 minutes. Download URLs are cached
> for 14 minutes — shorter than expiry so we never serve a
> dead link."

---

## [28:00 – 30:00] CLOSE — What Makes This Production-Grade

**SAY**:
> "Let me summarize what makes this a production system,
> not a demo:
>
> **Isolation**: Schema-per-tenant at the database. Tenant-prefixed
> S3 keys. Tenant-aware Redis keys. Defense in depth — a bug
> at one layer can't cause a breach because other layers still
> enforce isolation.
>
> **Intelligence**: RAG with real medical databases. Query rewriting.
> Conversational memory. Chain-of-thought reasoning. Reflexion
> retry on validation failures.
>
> **Reliability**: 210 tests. Every external call has error
> handling. Graceful degradation when services are unavailable.
> Immutable audit trail.
>
> **Performance**: Redis caching at 8 points — 750x speedup on
> cache hits. Async parallel API calls. Indexed queries.
>
> The stack: Django + Django Ninja + PostgreSQL + Redis + Claude.
> 704 lines of spec. 6,500 lines of code. 210 tests.
>
> The constraint isn't the AI model. It's the context you give it."

---

## QUICK REFERENCE — If They Ask Questions

| Question | Your Answer (30 seconds) |
|----------|-------------------------|
| "Why schema-per-tenant?" | "Hard isolation — search_path prevents cross-tenant queries. One missing WHERE clause can't leak data like in row-level tenancy." |
| "Why Django Ninja over DRF?" | "Pydantic schemas, native type hints, faster — less boilerplate for the same result." |
| "Why not React?" | "Internal staff tool — doesn't need SPA interactivity. Django templates + vanilla JS, zero build step, ships faster." |
| "How do you prevent hallucinations?" | "RAG prompt says 'ONLY use provided context'. Temperature 0.2. Every claim must have a [NCT] or [PMID] citation. If no data, it says 'insufficient data'." |
| "What about scaling to 1000 tenants?" | "Schema-per-tenant works to ~5000 schemas. Beyond that, shard into multiple databases. Connection pooling via PgBouncer in transaction mode." |
| "How do you handle LLM failures?" | "Every LLM call has try/except. On failure: graceful fallback summary with counts ('Found 10 trials, 5 papers. AI summary unavailable'). Never show raw errors." |
| "What would you improve next?" | "Per-tenant roles instead of global role field. Rate limiting per tenant. Vector-based RAG for uploaded documents. WebSocket for real-time chat streaming." |
| "How do you test tenant isolation?" | "Integration tests: create 2 tenants, authenticate as Tenant A, try to access Tenant B's data. Assert 403. Plus Playwright E2E tests doing the same in a real browser." |
