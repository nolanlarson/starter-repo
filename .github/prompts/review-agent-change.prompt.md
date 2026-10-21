---
name: Review Agent Change
description: Review a code change for correctness, privacy, tool-boundary, and regression risks.
argument-hint: Optional files or risk areas to focus on
agent: ask
---

Review the current working-tree diff. Do not modify files.

Prioritize actionable bugs, behavior regressions, privacy/security risks, and missing tests. Pay particular attention to provider fallback behavior, prompt/history handling, native tool permissions, SSE event compatibility, and whether a check could consume live provider quota.

Report findings first, ordered by severity. For each finding, include a file reference, the concrete failure scenario, and a suggested fix. If there are no findings, say so and state the remaining verification gaps. Keep any summary brief.
