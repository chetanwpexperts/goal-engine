# The reasoning core (plan, agreed 2026-10-24)

Goal: the brain does not store playbooks or forum answers and react when words match. It learns the
fundamentals from them and reasons its own way to a solution, so it can handle problems no single
playbook describes. No AI/LLM anywhere.

## What is learned from every real case (docs, Stack Overflow / DBA SE, incidents, our users)
Facts and ideas only, never copied text or code.

| Kind | Example |
|---|---|
| Fundamental (how it works) | "A lock wait happens when two transactions need the same rows; the second waits for the first" |
| Cause -> effect | "long-running transaction -> locks held longer -> other queries wait -> site slow" |
| Sign (how to see it) | "waiting rows in the running-transactions list; wait time keeps growing" |
| Action (fix + confirm) | "find the blocking transaction -> end it safely -> confirm waits are gone" |

## The knowledge map
Nodes: symptom, concept, cause, evidence, check, fix, verification, context (engine, app type, time
pattern). Edges with weights: suggests, causes, shows-as, found-by, fixed-by, verified-by, rules-out,
requires. Existing playbooks are converted into the map automatically; new knowledge is added as facts
and edges, not as whole playbooks.

## Reasoning loop
1. Pick out concepts from what the person says (existing ML understanding + the map's words).
2. Build hypotheses: causes linked to those concepts, scored by edge weights (Bayesian-style).
3. Plan the investigation: choose the read-only checks that best separate the remaining hypotheses
   (information gain), not a fixed list.
4. Run checks, read evidence, update scores; drop what's ruled out.
5. Still unclear: next most useful check, or one sharp question to the person.
6. Answer with the reasoning in plain words ("checked X because it separates A from B; X showed Y")
   + fix (approval + Undo) + how to confirm.
7. Nothing supported: say so, and report what is abnormal ("investigate when unknown").

## Learning every day
- Nightly runs and other contributors add facts/edges to the map.
- Real outcomes: "that fixed it" strengthens the path; a failed fix weakens it.
- Misses go to the learning list; the next night fills the gap.
- The generator injects problem COMBINATIONS no playbook describes; the brain must reason to the cause.

## Order
1. Merge PR #1 (diagnosis from evidence) after the real-database check.
2. Build the core for the database brain (map, converter, reasoning, investigation planning, outcome
   learning, generator family for unseen combinations).
3. Reuse the same core for the Microsoft brain, then PHP.
