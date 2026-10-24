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

## Learning from any platform: how knowledge is trusted (applies to EVERY brain and every source)
Sources: official docs (MySQL, MariaDB, PostgreSQL, Microsoft Support/Learn, PHP, Laravel, WordPress
developer docs); Stack Overflow / DBA SE / Super User; Microsoft Community / Q&A; Reddit (r/excel, r/SQL,
r/PostgreSQL, r/PHP, r/laravel, r/Wordpress); MrExcel / Chandoo; WordPress.org support forums; Laracasts;
GitHub issues of the software itself; engineering blogs and post-mortems; our own users' outcomes.

No source is trusted on its own. Every fact passes all six filters before the brain may use it:
1. Signals are only a hint: accepted/votes/date/version. Old answers for old versions get low trust.
2. Official docs are the judge: a fact that contradicts the official documentation is dropped.
3. Lab test: anything testable is reproduced on the real test databases / generated files and must work.
4. Agreement: a fact needs support from more than one independent source.
5. Safety: anything that risks data loss or security is rejected, or kept only with backup + warning.
6. Real outcomes: trust rises when fixes work for users and falls when they fail.

Wrong-but-popular answers are kept as "common mistakes" (what people try first and why it fails), so the
brain recognises them and goes to the real cause. Every fact carries: source(s), version range, lab-tested
yes/no, trust score. The brain answers with certainty only from proven facts plus evidence from the
user's own data; otherwise it says what it found and the next step.
Ideas and facts only: never copy text or code from forums (licences); write our own.
