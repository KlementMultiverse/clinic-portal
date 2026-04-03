# Clinic Portal — Recording Script (30 Minutes)

## BEFORE YOU START — Environment Setup

### 1. Start the services
```bash
cd ~/projects/clinic-portal
docker compose up -d    # PostgreSQL + Redis
uv run python manage.py runserver 0.0.0.0:8000
```

### 2. Browser setup
- Chrome, no bookmarks bar (Ctrl+Shift+B to toggle off)
- Zoom to 125% (Ctrl + +)
- Close all other tabs
- Open DevTools Network tab (optional, for presigned URL demo)
- Window size: 1920x1080

### 3. Pre-load test data
Make sure you have:
- At least 1 clinic already created (e.g. "Sunrise Clinic")
- 1-2 workflows with tasks in various states
- 1-2 uploaded documents (use a real medical PDF)
- A few search history entries
- A chat thread with messages
- 1 staff member invited
- A second clinic with different data (for isolation demo)

### 4. Recording tool
- OBS Studio or any screen recorder
- Record at 1080p, 30fps
- Use a decent mic — speak clearly, normal pace
- Record each clip separately so you can re-do any clip

---

## CLIP 1 — INTRO (no screen needed, or show landing page)

**Say exactly:**

> Hi, I'm Klement.
>
> I built a multi-tenant SaaS portal for medical
> clinics — a clinical QA system with AI.
>
> Each clinic gets its own isolated PostgreSQL schema —
> its own users, workflows, and documents.
> Database-level isolation, not just application-level
> filtering.
>
> It integrates with ClinicalTrials.gov and PubMed for
> real-time search, and uses Claude AI for summarization
> and Q&A — scoped strictly to medical queries.
>
> The stack: Django 5, Django Ninja APIs, PostgreSQL
> with schema-per-tenant, Redis, AWS S3 with presigned
> URLs, and vanilla JavaScript.
>
> Let me show you.

**On screen:** Landing page at `http://portal.localhost:8000/` or nothing (just you talking)

**Duration:** ~30 seconds

---

## CLIP 2 — REGISTRATION & CLINIC CREATION

**Say exactly:**

> When a new clinic signs up, the system provisions a
> dedicated PostgreSQL schema — completely isolated from
> every other clinic.
>
> I'll register a new user and create a clinic called
> Apollo Medical.
>
> The system just created a new PostgreSQL schema called
> apollo_medical, ran all tenant-specific migrations
> inside it, and mapped the subdomain. This clinic now
> has its own tables for workflows, tasks, documents,
> and search history.

**On screen — do this:**
1. Go to `http://portal.localhost:8000/register/`
2. Fill in: Name → "Dr. Raj Patel", Email → "raj@apollo.com", Password → something
3. Click Register
4. On the clinic creation form: Name → "Apollo Medical", Subdomain → "apollo-medical"
5. Click Create
6. Show the redirect to dashboard

**Duration:** ~40 seconds

---

## CLIP 3 — DASHBOARD

**Say exactly:**

> This is the clinic dashboard. It shows real-time
> stats — workflow count, document count, staff count,
> and a breakdown of tasks by status.
>
> Everything here is scoped to this clinic's schema.
> Another clinic logging in would see completely
> different numbers.

**On screen — do this:**
1. You're already on the dashboard at `/`
2. Point cursor at each stat card slowly
3. Point at the tasks-by-status section
4. Pause for 2 seconds so viewer can read

**Duration:** ~20 seconds

---

## CLIP 4 — WORKFLOWS + AI TASK GENERATION

**Say exactly:**

> Workflows organize clinical processes. Let me create
> one for patient onboarding.
>
> I'll describe what needs to happen, and Claude AI
> will generate actionable tasks from the description.
>
> The AI returned structured tasks — each with a title
> and description. The backend validates the JSON output
> and retries with a reflexion pattern if validation
> fails.
>
> Each task follows a strict state machine — created,
> assigned, in progress, completed. Only valid
> transitions are allowed, and every change is
> audit-logged.

**On screen — do this:**
1. Click "Workflows" in the nav
2. Click "New Workflow" or the create button
3. Name → "Patient Onboarding Protocol"
4. Description → "New patient intake including medical history review, insurance verification, initial assessment, and lab work scheduling"
5. Click Create
6. Open the workflow detail
7. Click "Generate Tasks with AI"
8. Wait for tasks to appear (3-8 tasks)
9. Show the task list with status badges
10. Click one task → transition it from "created" to "assigned"
11. Show the status badge change

**Duration:** ~60 seconds

---

## CLIP 5 — DOCUMENT UPLOAD + AI SUMMARIZATION

**Say exactly:**

> Documents upload directly to S3 using presigned URLs.
> The file never touches the Django server — it goes
> straight from the browser to S3.
>
> The S3 key is namespaced by tenant schema, so one
> clinic can never access another clinic's files.
>
> Once uploaded, Claude AI can summarize the document.
> It reads PDFs and images natively — no text
> extraction step needed.
>
> The summary is sanitized before storage because we
> treat all LLM output as untrusted input.

**On screen — do this:**
1. Click "Documents" in the nav
2. Click "Upload" or the upload button
3. Select a medical PDF (have one ready on desktop)
4. Show it uploading and appearing in the list
5. Click "Summarize" on the document
6. Wait for the AI summary to appear
7. Show the summary text
8. (Optional) Click "Download" to show presigned URL works

**Duration:** ~50 seconds

---

## CLIP 6 — CLINICAL SEARCH

**Say exactly:**

> The search engine queries ClinicalTrials.gov and
> PubMed in real time. Claude rewrites your query
> first — fixing typos and expanding medical
> abbreviations.
>
> I'll search for T2DM metformin — that's an
> abbreviation for Type 2 Diabetes Mellitus.
>
> The AI expanded the abbreviation, fetched trials and
> papers in parallel, and generated a summary with
> citations. Each citation links to the original source
> — NCT numbers go to ClinicalTrials.gov, PMIDs go to
> PubMed.
>
> Results are cached — trials for 6 hours, papers for
> 7 days — so repeat searches are instant.

**On screen — do this:**
1. Click "Search" in the nav
2. Type "T2DM metformin efficacy" in the search box
3. Click Search
4. Wait for results to load
5. Point at the "Searched as:" text showing the expanded query
6. Scroll through the AI summary — point at a citation
7. Click a citation link to show it opens the real source
8. Show the trials column and papers column below
9. Scroll down to show search history table

**Duration:** ~60 seconds

---

## CLIP 7 — INTELLIGENT CHAT

**Say exactly:**

> The chat interface is intent-aware. It classifies
> each message — is the user searching for something
> clinical, following up on a previous answer, or going
> off-topic?
>
> If it's a clinical question, it auto-searches trials
> and papers, then answers with sources. Follow-up
> questions use cached context without re-searching.
>
> Let me try an off-topic question — the AI redirects
> back to medical queries. It only answers within its
> clinical scope.

**On screen — do this:**
1. Click "Chat" in the nav
2. Type: "What are the latest trials for SGLT2 inhibitors in heart failure?"
3. Send — wait for response with citations
4. Point at the citations in the response
5. Type: "What about the side effects?"
6. Send — show it uses context from the previous answer
7. Type: "What's the weather today?"
8. Send — show the off-topic redirect response
9. Point at the thread sidebar showing conversation history

**Duration:** ~90 seconds

---

## CLIP 8 — STAFF MANAGEMENT

**Say exactly:**

> Admins can invite staff members. The system creates
> them with a temporary password and forces a password
> reset on first login.
>
> Staff are scoped to this clinic — they can only see
> this clinic's data. They can be assigned to tasks but
> can't create workflows or manage other staff.

**On screen — do this:**
1. Click "Staff" in the nav
2. Click "Invite Staff"
3. Fill in: Name → "Nurse Priya", Email → "priya@apollo.com"
4. Click Invite
5. Show them appear in the staff list
6. Point at the role badge (staff vs admin)

**Duration:** ~30 seconds

---

## CLIP 9 — MULTI-TENANCY PROOF

**Say exactly:**

> Let me prove the isolation works. I'll switch to a
> completely different clinic.
>
> Different dashboard, different data, different schema.
> Zero data bleed between tenants.
>
> Each clinic's PostgreSQL schema is a separate
> namespace — even a raw SQL query without any tenant
> filter would only see this clinic's data. That's the
> power of schema-per-tenant isolation.

**On screen — do this:**
1. Show current clinic's dashboard with data (note the numbers)
2. Switch to a different clinic (either via subdomain or tenant switcher)
3. Show the dashboard with different data (or empty if new)
4. (Bonus) Open a terminal and run:
   ```bash
   docker exec -it clinic-portal-db-1 psql -U postgres -d clinic_portal -c "\dn"
   ```
   Show the list of schemas — public, sunrise_clinic, apollo_medical, etc.

**Duration:** ~40 seconds

---

## CLIP 10 — ARCHITECTURE (optional, for technical audience)

**Say exactly:**

> A quick look under the hood. The project has six
> Django apps — tenants and users are shared across all
> schemas, while workflows, documents, search, and
> dashboard are duplicated per tenant.
>
> The CLAUDE.md file at the root defines 19
> architecture rules that every code change must follow.
> The entire project was built using AI-native
> development with Claude Code as the primary tool.

**On screen — do this:**
1. Open VS Code or terminal
2. Show the project structure: `ls apps/`
3. Open `CLAUDE.md` briefly — scroll through the rules
4. (Optional) Open `apps/workflows/models.py` — show VALID_TRANSITIONS dict
5. (Optional) Open `apps/search/chat.py` — show the 3-call pattern

**Duration:** ~40 seconds

---

## CLIP 11 — CLOSING

**Say exactly:**

> That's the full system. Multi-tenant with
> database-level isolation. AI-powered search,
> summarization, and chat — scoped strictly to
> medicine.
>
> Built with Django 5, Django Ninja, PostgreSQL, Redis,
> S3, and Claude AI.
>
> The entire project — six Django apps, ten templates,
> four thousand lines of Python — was built using
> AI-native development with Claude Code.
>
> Thanks for watching.

**On screen:** Dashboard or landing page

**Duration:** ~25 seconds

---

## TOTAL TIME: ~28-32 minutes

### Timing Breakdown
| Clip | Topic | Duration |
|------|-------|----------|
| 1 | Intro | ~30s |
| 2 | Registration & Clinic | ~40s |
| 3 | Dashboard | ~20s |
| 4 | Workflows + AI Tasks | ~60s |
| 5 | Documents + AI Summary | ~50s |
| 6 | Clinical Search | ~60s |
| 7 | Intelligent Chat | ~90s |
| 8 | Staff Management | ~30s |
| 9 | Multi-Tenancy Proof | ~40s |
| 10 | Architecture | ~40s |
| 11 | Closing | ~25s |
| **Total** | | **~8 min narration** |

**Note:** 8 minutes of narration + pauses + loading times + natural flow = ~15-20 minutes on screen. If you need 30 minutes, slow down, show more details, open code files, explain architecture deeper between clips, or add a terminal walkthrough of the database schemas.

### Tips to Fill 30 Minutes
- After each clip, pause and explain what just happened technically
- Show the Network tab during document upload (presigned URL flow)
- Open code files and walk through key functions
- Show the database schemas in psql
- Show Redis cache keys with `redis-cli KEYS "*"`
- Run the test suite on camera: `uv run python manage.py test`
- Show the OpenAPI docs at `/api/docs`
