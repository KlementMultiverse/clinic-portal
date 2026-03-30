# Agent Handoff Protocol

Every agent in this project returns a structured handoff when it completes its task. The PM agent (`/sc:pm`) reads handoffs and routes to the next agent. No agent starts work without receiving a handoff from the previous agent.

## Standard Handoff Format

Every agent MUST return this structure at the end of its work:

```markdown
## Handoff: [source-agent] → [next-agent or next-command]

### Task Completed
[One sentence: what was done]

### Files Changed
- `path/to/file.py` — [what changed]
- `path/to/new.py` — [created: purpose]

### Test Results
[PASS: N tests | FAIL: N tests | NOT RUN: no tests applicable]

### GitHub Issues Updated
- #[N]: [status change — e.g., "closed", "in-progress", "comment added"]

### Context for Next Agent
[What the next agent needs to know to start its work.
Include: relevant file paths, decisions made, constraints discovered.]

### Blockers
[Anything that prevents the next step. "None" if clear to proceed.]
```

## Handoff Chain (Full SDLC)

```
/specify           → Handoff → /design-doc
/design-doc        → Handoff → /plan-tasks
/plan-tasks        → Handoff → /sc:implement (per task)
/sc:implement      → Handoff → /sc:test --fix
/sc:test --fix     → Handoff → next task OR /audit-patterns
/audit-patterns    → Handoff → fix agent OR /gate
/gate              → Handoff → next stage OR /retro
/retro             → Handoff → /gate (final PR)
```

## PM Agent Role in Handoffs

The PM agent (`/sc:pm`):
1. Receives every handoff
2. Reads the "Context for Next Agent" and "Blockers" sections
3. If blockers exist → routes to the agent that can unblock
4. If clear → routes to the next agent/command in the chain
5. Updates GitHub Issue status via `gh issue edit`
6. Tracks overall progress across all stages
