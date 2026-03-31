# Feature Proposal: Intelligent Backend Patterns

**Proposal Number:** 02
**Status:** Draft
**Author:** Claude Code + user
**Created:** 2026-03-30
**Target Branch:** `feature/intelligent-backend-patterns`

---

## Problem Statement

The clinic portal MVP is functional but lacks production-grade backend patterns: no Redis caching (despite Redis running), no structured LLM prompts (basic one-liners without k-shot, CoT, or reflexion), no session memory, incomplete error handling on S3 calls, and minimal observability logging. These patterns are defined in SPEC.md "Intelligent Backend Patterns" and are REQUIRED.

## User Stories

- As a **clinic staff member**, I want dashboard and workflow pages to load faster so that I don't wait for repeated database queries (caching)
- As a **clinic admin**, I want AI-generated tasks to be consistently formatted and relevant so that I can trust the output (prompt engineering + k-shot + validation)
- As a **clinic staff member**, I want the dashboard to show my recent actions so that I can quickly resume where I left off (session memory)
- As a **platform operator**, I want structured logs at all observability points so that I can debug issues and monitor system health (logging)
- As a **clinic staff member**, I want AI summaries that reason through the document before summarizing so that summaries are more accurate (Chain-of-Thought)

## Proposed Solution

### High-Level Approach

Add 6 backend patterns across all apps: Redis caching with tenant-aware keys, enhanced LLM prompts (system-reminder tags, k-shot examples, Chain-of-Thought, reflexion), pre-work context injection from AuditLog, session-scoped action memory, comprehensive error handling on all S3 calls, and structured logging at all 10 observability points.

### Technical Approach

1. **Redis Caching**: `cache.get()`/`cache.set()` with `django.core.cache` (already tenant-aware via `make_key` in settings). Cache invalidation via explicit `cache.delete()` on mutations.
2. **LLM Prompts**: Update `_summarize_via_llm` and `_generate_tasks_via_llm` with SPEC-mandated prompt templates, temperature params, k-shot examples, CoT reasoning blocks, and reflexion retry.
3. **Pre-work Context**: Query `AuditLog.objects.filter(performed_by=user).order_by("-timestamp")[:3]` before LLM calls.
4. **Session Memory**: Append to `request.session["recent_actions"]` on every mutation, capped at 5 entries.
5. **Error Handling**: Wrap all 3 S3 service functions in try/except matching Lambda error handling pattern.
6. **Logging**: Add `logger = logging.getLogger(__name__)` + structured log calls at all 10 points.

---

## Implementation Plan

### Phase 1: Error Handling + Logging Foundation
- [ ] [P] S3 error handling on generate_upload_url, generate_download_url, delete_s3_object
- [ ] [P] Add loggers to all api.py + services.py modules
- [ ] Structured logging at all 10 observability points across all apps

### Phase 2: Redis Caching
- [ ] Dashboard stats caching (60s TTL)
- [ ] Workflow list caching (30s TTL)
- [ ] S3 download URL caching (14min TTL)
- [ ] LLM summary caching (24hr TTL)
- [ ] LLM tasks caching (1hr TTL)
- [ ] Cache invalidation on all mutations

### Phase 3: LLM Prompt Engineering
- [ ] Temperature params (0.2 summarize, 0.5 tasks)
- [ ] system-reminder tags in both prompts
- [ ] 2 k-shot examples in task generation
- [ ] Chain-of-Thought in summarization (strip reasoning block)
- [ ] Output validation (JSON, length, empty checks)
- [ ] Reflexion retry pattern (max 1 retry)

### Phase 4: Context + Memory
- [ ] Pre-work context calls (last 3 AuditLog entries injected into LLM prompts)
- [ ] Session memory (recent_actions in request.session, capped at 5)
- [ ] Dashboard shows recent actions
- [ ] Clear recent_actions on logout

### Phase 5: Testing
- [ ] Tests for caching (hit/miss/invalidation)
- [ ] Tests for LLM validation (bad JSON, long output, empty output, reflexion)
- [ ] Tests for session memory
- [ ] Tests for error handling on S3 failures

**Dependencies:** Redis (already running), existing Claude Haiku API key

---

## Acceptance Criteria

Given the dashboard stats endpoint is called twice within 60 seconds
When no mutations occur between calls
Then the second call returns cached data without hitting the database

Given a document is summarized via the AI endpoint
When the LLM returns output with a `<reasoning>` block
Then only the "Summary:" portion is stored in the database, reasoning is stripped

Given a task generation LLM call returns invalid JSON
When reflexion retries with error context
Then the retry prompt includes "Your previous response was invalid because [reason]"

Given a user performs 6 actions (create workflow, create task, transition task, upload doc, summarize doc, create workflow)
When the dashboard is loaded
Then request.session["recent_actions"] contains only the last 5 actions

Given an S3 generate_upload_url call fails with ClientError
When the error is caught
Then a structured JSON error is returned and the error is logged at WARNING level

Given the LLM returns a summary longer than 500 words
When the summary is processed
Then it is truncated with "... [summary truncated]" appended

Given a staff member logs out
When the session is destroyed
Then request.session["recent_actions"] is cleared

---

## Risks

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Cache key collisions across tenants | Low | High | Enforced by django_tenants.cache.make_key (already configured) |
| LLM reflexion retry doubles API cost | Low | Low | Max 1 retry, only on validation failure |
| Session memory bloat | Low | Low | Capped at 5 entries |
| Logging verbosity impacts performance | Low | Medium | INFO level only, no debug logging in production |
| Cache serving stale data | Medium | Low | Short TTLs + explicit invalidation on mutations |

## Security & Privacy

- Cache keys are tenant-isolated via `django_tenants.cache.make_key` — no cross-tenant cache access
- LLM output continues to be sanitized with `strip_tags()` — CoT reasoning block is stripped before storage
- Logging NEVER includes passwords, API keys, session tokens — email only in auth logs
- Session memory stores action summaries, not sensitive data (entity type + ID + action type)

---

**Retrospective:** `docs/retrospectives/02-intelligent-backend-patterns.md` (link after implementation)
