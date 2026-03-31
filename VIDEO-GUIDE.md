# 30-Minute Video Recording Guide
## Clinical QA System — Live Demo + Architecture Explanation

### Pre-Recording Checklist

```bash
# Terminal 1 — keep running
docker compose up -d db redis
uv run python manage.py runserver

# Terminal 2 — ready for commands
# Keep this open for live terminal demos

# Browser tabs (pre-open, don't show yet):
# Tab 1: http://portal.localhost:8000/
# Tab 2: http://clinic1.localhost:8000/login/
# Tab 3: VS Code with project open

# Verify everything works:
curl -s http://portal.localhost:8000/ | head -1   # should return HTML
curl -s http://clinic1.localhost:8000/login/ | head -1
```

---

## THE FLOW (30 minutes)

---

### [0:00 – 2:00] OPEN — What I Built and Why

**SHOW**: Browser — `http://portal.localhost:8000/` (landing page)

**SAY**:
> "I built a multi-tenant clinical QA system. Clinics sign up, get
> their own isolated workspace, and their staff can search real
> medical databases — ClinicalTrials.gov and PubMed — and get
> AI-summarized answers with proper citations.
>
> The stack: Django 5, Django Ninja for the API, PostgreSQL with
> schema-per-tenant isolation via django-tenants, Redis for
> caching and sessions, Claude Haiku for AI, and vanilla JS
> with Pico CSS on the frontend. No React, no build step."

**SHOW**: Scroll the landing page briefly

---

### [2:00 – 5:00] ARCHITECTURE — Show You Understand the System

**SHOW**: VS Code — open `config/settings.py`

**SAY** (pointing at code):
> "Let me show you the architecture decisions.
>
> **Middleware** — line 69. TenantMainMiddleware is position zero.
> Every request, it reads the subdomain from the Host header,
> looks up which tenant that is, and sets the PostgreSQL
> search_path. From that point, every Django ORM query is
> automatically scoped to that tenant's schema. This is not
> application-level filtering — it's database-level isolation.
> A bug in my code literally cannot leak data across tenants.
>
> **Apps** — I have 6 apps. Tenants and Users are SHARED — they
> live in the public schema because users are global (one user
> can belong to multiple clinics). Workflows, Documents, Dashboard,
> and Search are TENANT apps — each clinic has their own tables
> in their own PostgreSQL schema.
>
> **Cache** — line 104. Redis with `django_tenants.cache.make_key`.
> Every cache key is automatically prefixed with the tenant
> schema name. Clinic A and Clinic B can both cache
> `dashboard:stats` — they'll never collide.
>
> **Sessions** — Redis-backed, HttpOnly, SameSite=Lax, Secure
> in production. Session cookies are per-subdomain."

---

### [5:00 – 8:00] MODELS — State Machine + Audit Trail

**SHOW**: VS Code — open `apps/workflows/models.py`

**SAY**:
> "The Task model has a state machine. Line 58 — VALID_TRANSITIONS.
> Created can go to Assigned or Cancelled. Assigned can go to
> In Progress or Cancelled. Completed and Cancelled are terminal.
> You can't skip states.
>
> The `transition_to` method — line 91 — validates the transition,
> saves the new status, and creates an AuditLog entry in the
> same operation. Every state change is recorded with who did it
> and when.
>
> AuditLog is immutable — line 26. I override `save()` and
> `delete()` to raise ValueError if you try to update or delete
> a record. This is a write-once audit trail. In healthcare-adjacent
> systems, this is non-negotiable."

---

### [8:00 – 10:00] LIVE — Login + Dashboard

**SHOW**: Browser — go to `http://clinic1.localhost:8000/login/`

**DO**: Type `admin@portal.localhost` / `admin123` → Login

**SAY**:
> "I'm logging into Sunrise Clinic — clinic1.localhost.
> The subdomain tells Django which tenant schema to use."

**SHOW**: Dashboard loads — stats cards appear

**SAY**:
> "Dashboard fetches stats from `/api/dashboard/stats`. It shows
> workflow count, task breakdown by status, document count, staff
> count, and search count. This is cached for 60 seconds in Redis
> — so repeated page loads don't hit the database."

---

### [10:00 – 13:00] LIVE — Workflows + Tasks

**SHOW**: Click "Workflows" in nav

**SAY**:
> "Here are the clinic's workflows. Patient Intake is our seeded
> workflow with 4 tasks in different states."

**DO**: Click on "Patient Intake" to see task list

**SAY**:
> "Each task has a status badge — Created, Assigned, In Progress,
> Completed. The colors are CSS classes, no JavaScript framework.
>
> The state machine is enforced server-side. If I try to transition
> a Completed task back to In Progress, the API returns 400 with
> the valid transitions for that state."

**SHOW**: Point out the "Generate Tasks with AI" button

**SAY**:
> "This button calls Claude Haiku to generate tasks from the
> workflow description. The prompt uses k-shot examples —
> two example workflows with their expected outputs — so the
> LLM returns consistent JSON format. If the JSON is invalid,
> there's a reflexion retry: it sends the error back to the LLM
> and asks it to try again. Max one retry."

---

### [13:00 – 15:00] LIVE — Documents + S3

**SHOW**: Click "Documents" in nav

**SAY**:
> "Documents are stored in S3 via presigned URLs. Django never
> touches the file bytes. When you upload, the flow is:
>
> 1. Frontend asks Django for a presigned PUT URL
> 2. Django generates the URL with a tenant-namespaced S3 key —
>    `clinic1_schema/uuid/filename.pdf`
> 3. Frontend uploads directly to S3
> 4. Frontend tells Django 'upload complete' with the S3 key
> 5. Django validates the key starts with the current tenant's
>    schema name — this prevents cross-tenant file access
>
> Download URLs are also presigned, expire in 15 minutes,
> and cached for 14 minutes in Redis."

**SHOW**: Point at the "Summarize" button

**SAY**:
> "Summarize calls Claude Haiku with a Chain-of-Thought prompt.
> The LLM reasons through the document in a `<reasoning>` block,
> then writes the summary. I strip the reasoning block before
> storing — the user only sees the clean summary. And
> `strip_tags()` is applied to all LLM output before database
> storage. LLM output is untrusted input."

---

### [15:00 – 20:00] LIVE — Clinical Search (THE MAIN FEATURE)

**SHOW**: Click "Search" in nav

**SAY**:
> "This is the clinical QA system. Real medical database search
> with AI summarization. Let me show you."

**DO**: Type `SGLT2 inhibitors heart failure` → Submit

**WAIT**: Results load (2-3 seconds)

**SAY** (while results appear):
> "Here's what just happened:
>
> 1. Query rewriting — Claude expanded 'SGLT2' to
>    'sodium-glucose cotransporter 2 inhibitors' and added
>    medical synonyms. This dramatically improves search quality.
>
> 2. Two API calls ran in parallel — asyncio.gather with httpx.
>    ClinicalTrials.gov returned 10 trials, PubMed returned
>    10 papers. Total time: under 1 second.
>
> 3. RAG summarization — the trials and papers are formatted
>    as context, injected into a Claude prompt that says
>    'answer ONLY from this context, cite every claim.'
>    Temperature 0.2 for factual output.
>
> Look at the summary — every claim has a citation.
> [NCT...] links to clinicaltrials.gov. [PMID:...] links to PubMed."

**DO**: Click one [NCT...] link → shows real trial page
**DO**: Click one [PMID:...] link → shows real PubMed paper

**SAY**:
> "These are real links to real data. Not hallucinated."

**DO**: Search again — `CAR-T cell therapy lymphoma`

**SAY**:
> "Different query, completely different results. The cache
> stores each query's results separately — 6 hours for trials,
> 7 days for papers. Second time you search the same thing,
> it's 750x faster from cache."

---

### [20:00 – 23:00] LIVE — Conversational Chat

**SHOW**: Click "Chat" in nav

**SAY**:
> "Search is one-shot. Chat is conversational. The AI remembers
> what you asked."

**DO**: Type `What are the latest GLP-1 trials for weight loss?`
**WAIT**: Response appears with citations

**SAY**:
> "It searched ClinicalTrials.gov and PubMed, loaded 10 trials
> and 10 papers into the thread context."

**DO**: Type `Which ones are currently recruiting?`
**WAIT**: Response appears (faster — no new search)

**SAY**:
> "Follow-up — no new search needed. It used the existing
> context. The AI detected this is a follow-up question,
> not a new topic.
>
> If I say 'search for metformin instead' — it detects the
> trigger words and runs a fresh search with new context."

**DO**: Type `Search for metformin type 2 diabetes instead`
**WAIT**: Response with new data

**SAY**:
> "New search triggered. Context updated. The thread stores
> both the trials/papers context and the full conversation
> history. This is the memory architecture —
> temporary memory in the thread context, permanent memory
> in the database."

---

### [23:00 – 26:00] LIVE — Tenant Isolation Proof

**SHOW**: Open Terminal

**DO**: Run these commands live:

```bash
# Show clinic1 data
curl -s http://clinic1.localhost:8000/api/auth/me \
  -b cookies1.txt | python3 -m json.tool
# Shows: Sunrise Clinic

# Show clinic2 data
curl -s http://clinic2.localhost:8000/api/auth/me \
  -b cookies2.txt | python3 -m json.tool
# Shows: Valley Health Center

# Show different workflows
curl -s http://clinic1.localhost:8000/api/workflows/ \
  -b cookies1.txt | python3 -c "
import json,sys;[print(w['name']) for w in json.load(sys.stdin)]"
# Patient Intake

curl -s http://clinic2.localhost:8000/api/workflows/ \
  -b cookies2.txt | python3 -c "
import json,sys;[print(w['name']) for w in json.load(sys.stdin)]"
# Lab Processing

# Try cross-tenant access
curl -s http://clinic2.localhost:8000/api/workflows/ \
  -b cookies1.txt
# Returns: {"detail": "Unauthorized"}
```

**SAY**:
> "Two clinics, same server, completely different data.
> Clinic 1 has Patient Intake, Clinic 2 has Lab Processing.
> When I use Clinic 1's session cookie on Clinic 2's subdomain,
> I get Unauthorized. The session is per-subdomain, and
> TenantAccessMiddleware verifies the user belongs to the tenant."

---

### [26:00 – 28:00] TESTING — Show Engineering Discipline

**SHOW**: Terminal

**DO**: Run tests live:

```bash
# Django tests
uv run python manage.py test --noinput -v1 2>&1 | tail -5
# 139 tests, 0 failures

# Playwright E2E tests
uv run pytest tests/e2e/ --tb=line -q 2>&1 | tail -5
# 71 passed
```

**SAY**:
> "210 total tests. 139 Django unit and integration tests —
> shared apps use TestCase, tenant apps use TenantTestCase which
> automatically creates a test tenant schema. 71 Playwright
> browser tests covering every page, every flow, SQL injection,
> XSS, cross-tenant isolation, even mobile viewport.
>
> Every API endpoint has tests. Every edge case is covered.
> The clinical search tests mock httpx and the LLM —
> no real API calls in tests."

---

### [28:00 – 30:00] CLOSE — What Makes This Production-Grade

**SHOW**: VS Code — quick scroll through project tree

**SAY**:
> "Let me summarize what makes this production-grade:
>
> **Isolation** — schema-per-tenant at the database level.
> Tenant-prefixed S3 keys. Tenant-aware Redis cache keys.
> A bug cannot leak data.
>
> **Security** — immutable audit trail, CSRF protection,
> session cookies with HttpOnly/SameSite/Secure, input
> validation on every endpoint, LLM output sanitized with
> strip_tags before storage.
>
> **Intelligence** — RAG pipeline with real medical databases,
> query rewriting that expands abbreviations, conversational
> chat with context memory, Chain-of-Thought reasoning,
> reflexion retry on validation failures.
>
> **Performance** — Redis caching at 8 points with proper
> invalidation, async parallel API calls, 750x cache speedup.
>
> **Quality** — 210 tests, 704-line spec, design doc with
> 10 architecture decisions, every decision documented with
> rationale and trade-offs.
>
> The constraint is not the AI model. It's the context you
> give it. CLAUDE.md, SPEC.md, and the SDLC flow — that's
> what makes the difference between a demo and a system."

---

## TIMING CHEAT SHEET

| Time | What | Duration |
|------|------|----------|
| 0:00 | Intro — what I built | 2 min |
| 2:00 | Architecture — settings.py | 3 min |
| 5:00 | Models — state machine + audit | 3 min |
| 8:00 | Live — login + dashboard | 2 min |
| 10:00 | Live — workflows + tasks | 3 min |
| 13:00 | Live — documents + S3 | 2 min |
| 15:00 | Live — clinical search (MAIN) | 5 min |
| 20:00 | Live — chat conversation | 3 min |
| 23:00 | Live — tenant isolation proof | 3 min |
| 26:00 | Tests — 210 passing | 2 min |
| 28:00 | Close — what makes it prod-grade | 2 min |

## THINGS TO AVOID

- Don't write code on camera — everything is built, just show and explain
- Don't read code line by line — point at key lines, explain the WHY
- Don't say "Claude did this" — say "I designed this, here's why"
- Don't rush the search demo — it's the main feature, let it breathe
- Don't apologize for anything — own every decision

## POWER PHRASES TO USE

- "Schema-per-tenant isolation — a bug cannot leak data"
- "Every LLM output is treated as untrusted input"
- "The state machine enforces transitions — you can't skip states"
- "Async parallel fetch — two API calls, one round-trip time"
- "RAG with citations — every claim links to a real source"
- "The constraint is not the model, it's the context"
- "Write-once audit trail — immutable by design"
- "750x cache speedup with proper invalidation"
