# Retrospective: Clinic Portal MVP

**Branch:** `stage-1-specify`
**Date:** 2026-03-30
**Duration:** Single session (~3 hours from first implementation commit to final audit fixes)

---

## Summary

Delivered a complete multi-tenant SaaS clinic management portal from SPEC.md through all 7 implementation phases. 20 GitHub Issues created and closed, 61 tests passing, 85% pattern audit pass rate (100/118 applicable checks). The SDLC flow (Specify → Architect → Implement → Validate → Review) was followed end-to-end. All 15 CLAUDE.md architecture rules pass compliance.

## What Went Well

- **SDLC Flow execution was smooth**: The staged approach (specify → design-doc → plan-tasks → implement per phase → audit → retro) kept work organized and prevented scope drift. Each phase had clear inputs/outputs.
- **Agent delegation worked effectively**: Specialist agents (django-tenants-agent, django-ninja-agent, s3-lambda-agent) produced working code with tests on first pass for most phases. The agent selection matrix in CLAUDE.md proved valuable.
- **context7 library docs prevented misconfigurations**: Fetching django-tenants and django-tenant-users docs before implementation caught critical details (TenantBase vs TenantMixin, tenant_users.permissions in both app lists, provision_tenant import path).
- **Test-per-phase approach caught regressions**: Running all tests after each phase (9 → 24 → 38 → 52 → 58 → 61) ensured no breakage between phases.
- **CodeRabbit caught real issues**: The staff invite wording inconsistency, existing-user password reset behavior, and tenant key naming were all valid catches that improved the spec.

## What Could Improve

- **CSRF implementation confusion**:
  - What happened: The design doc (Decision 7) specified `NinjaAPI(csrf=True)` but this parameter doesn't exist in the installed django-ninja version. The pattern audit flagged it as a critical failure, and the fix attempt caused a TypeError.
  - Root cause: context7 docs didn't clarify that django-ninja handles CSRF per-auth-class (`SessionAuth.csrf=True`), not as a global NinjaAPI parameter. The design doc was written based on incomplete library knowledge.
  - Improvement: When writing design doc decisions about specific API parameters, verify the exact parameter exists in the installed version via `inspect.signature()` before committing to it.

- **Inconsistent Lambda error handling**:
  - What happened: `invoke_summarize_lambda()` was implemented without error handling while `invoke_generate_tasks_lambda()` had comprehensive try/except. The pattern audit caught this.
  - Root cause: The two functions were written by different agent invocations (Phase 4 vs Phase 5). The Phase 4 agent wrote the simpler version first; the Phase 5 agent improved the pattern but didn't update the earlier function.
  - Improvement: When an agent writes a service function that follows a pattern, check if earlier similar functions in the same file need updating. Add a CLAUDE.md rule: "When improving a pattern in a service module, apply the improvement to all existing functions in that module."

- **Pattern audit at 85% (below 90% threshold)**:
  - What happened: Several checks failed that were fixable with small changes (SESSION_COOKIE_SECURE, AuditLog immutability, LLM output sanitization, Domain.__str__).
  - Root cause: These were design doc requirements that weren't explicitly listed in the agent prompts, so the implementing agents didn't include them.
  - Improvement: Extract all security requirements from the design doc Section 10 into a checklist that gets included in every implementation agent prompt.

- **Branch naming doesn't match conventions**:
  - What happened: Work was done on `stage-1-specify` branch which is a stage name, not `feature/clinic-portal-mvp` as planned in the proposal.
  - Root cause: The gate created the branch for Stage 1 and subsequent work continued on it.
  - Improvement: Create the feature branch at the start of Stage 3 (Implement) per the proposal's target branch.

## Lessons Learned

1. **django-ninja CSRF is auth-class-level, not API-level**: `SessionAuth` (used as `django_auth`) has `csrf=True` by default. No global parameter needed. This is a version-specific detail that must be verified before design decisions.

2. **Agent consistency requires explicit cross-referencing**: When multiple agents write code in the same file across phases, each agent should read and match the patterns established by previous agents. Include "read existing functions in this file and follow the same patterns" in agent prompts.

3. **Security checklist should be a first-class artifact**: Design doc security requirements (Section 10) should be extracted into a machine-checkable list that runs automatically, not just prose that agents may miss.

4. **context7 docs are critical for multi-library projects**: Fetching docs for django-tenants, django-tenant-users, django-ninja, and boto3 prevented multiple misconfigurations. Always run context-loader before any implementation phase.

## Changes Made

### Files Created (72 new files)
- `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `manage.py` — Infrastructure
- `config/settings.py`, `config/urls.py`, `config/urls_public.py`, `config/wsgi.py`, `config/views.py` — Django config
- `apps/tenants/models.py`, `apps/users/models.py`, `apps/workflows/models.py`, `apps/documents/models.py` — Data models
- `apps/users/api.py`, `apps/tenants/api.py`, `apps/workflows/api.py`, `apps/documents/api.py`, `apps/dashboard/api.py` — API endpoints
- `apps/documents/services.py` — S3 + Lambda service layer
- `apps/users/middleware.py` — Password reset enforcement
- `apps/tenants/management/commands/create_public_tenant.py`, `seed_demo.py` — Management commands
- `templates/*.html` (8 templates), `static/app.js`, `static/styles.css` — Frontend
- `lambdas/summarize/handler.py` — AWS Lambda function
- `docs/proposals/01-clinic-portal-mvp.md`, `docs/design-doc.md`, `docs/implementation-plan.md` — Architecture docs
- `docs/checkpoints/01-03*.md`, `docs/checkpoints/INDEX.md` — Checkpoint records
- `apps/*/tests.py` (5 test files) — Test suites
- `.env.example`, `.gitignore` — Environment

### Files Modified
- `CLAUDE.md` — Updated SDLC flow formatting

## Metrics

- GitHub Issues created: 21 (#27-#47)
- GitHub Issues completed: 21/21 (100%)
- Older duplicate issues closed: 26 (#1-#26)
- Pattern audit pass rate: 85% (100/118 applicable)
- Architecture rules compliance: 100% (15/15)
- Security checks: 83% (10/12)
- Total tests: 61 passing, 0 failing
- Total lines added: ~6,235
- CodeRabbit suggestions addressed: 5 (all resolved)

## Action Items

- [ ] Run `/audit-patterns full` after these fixes to verify >90% — Owner: PM Agent
- [ ] Consider rate limiting on auth endpoints before production — Owner: future sprint
- [ ] Set up AWS infrastructure (S3 + Lambda) for integration testing — Owner: user
- [ ] Add /etc/hosts entries for local subdomain testing — Owner: user
