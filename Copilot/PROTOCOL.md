# Copilot Execution Protocol

Date: 2026-04-14
Scope: market discovery + hybrid paper strategy implementation

## Core Rules

1. Do not drift from agreed strategy intent:
- Discover temperature markets.
- Enter only after deterministic bucket decision.
- Exit by +100% policy or confidence-gated hold to resolve.

2. Keep hybrid behavior intact unless explicitly requested.

3. Every implementation batch must be documented in Copilot/WORKLOG.md.

4. Every handoff must update Copilot/HANDOFF.md.

## Required Worklog Entry Format

Each new entry must include:
- Timestamp (UTC)
- Change scope (files/functions touched)
- Reason for change
- Validation evidence (tests/commands)
- Result status (pass/fail and notes)

## Required Handoff Content

Copilot/HANDOFF.md must always include:
- Current baseline status
- What changed in this session
- Open risks or assumptions
- Exact next implementation steps

## Completion Gate for Any Batch

Before closing a batch:
1. Relevant tests pass.
2. Worklog entry appended.
3. Handoff state updated if session context changed.
4. No unapproved strategy drift introduced.
