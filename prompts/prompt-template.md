# Phase Prompt Template

Copy this for each phase. One phase per prompt. Lean on steering + docs instead of re-explaining (saves credits).

```
# PHASE N: <name>
Context: assume the steering files + NEXUS-AML-Design-Document.md + DATA-SPEC.md + DECISIONS.md.
         Do NOT re-explain the project or schema.
Goal: <one sentence>
Preconditions: <what already exists / what Phase N-1 produced>
Tasks:
  1. ...
  2. ...
Out of scope (do NOT build): <explicit list — this matters as much as Tasks>
Constraints: <only the ones NOT already in steering>
Acceptance: <testable criteria>
When green: stop and report <specific output>. Do not proceed to the next phase.
```

## Rules of thumb
- Give decisions already made; don't ask the agent to "figure out X" (open-ended reasoning is expensive).
- Always include an "Out of scope" list to prevent over-building.
- Always end with "stop and report" so nothing runs unreviewed.
- After each phase: ask "did any durable decision change?" → if yes, update steering + DECISIONS.md.
- Commit + push after every green phase.
