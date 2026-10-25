# Second brain — design

agent-os today answers *"may this agent do this?"*. The second brain adds *"what does the
crew know, and who is working on what?"*: a shared memory that compounds, plus
coordination so several agents can work from it without stepping on each other. It is
built **on top of** the existing guard, gate, feed and quarantine. None of them gets
weaker, and nothing gets a new way to touch the vault.

## Shape

```
workspace/                         ← the vault: the only source of truth
  Agents/<Name>.md                 charters (+ "## Validated insights", changed only by proposal)
  Agents/Logs/YYYY-MM-DD.md        activity feed (unchanged)
  Memory/<Agent>/<type>/<id>.md    each agent's own namespace: fact · decision · learning · entity · episode
  Memory/<Agent>/handoffs/<task>.md
  Memory/Shared/<type>/<id>.md     shared notes, changed only by approved proposal
  Tasks/<id>.md                    the board: one note per task, frontmatter + history lines
  Inbox/<Agent>.md                 append-only message bus, one file per agent
  Reviews/Consolidation/<date>.md  the weekly consolidation report
  Archive/...                      decayed and merged notes (moved, never deleted)
```

**Note** (frontmatter + markdown body): `id, type, title, tags, source, created, updated,
confidence (stated | observed | inferred), expires_at?` plus the body, whose `[[links]]`
form the graph. Decisions also carry `subject` and `stance` so contradictions can be
detected without NLP. **Task**: `id, title, owner, status (todo | claimed | doing |
review | done | parked), needs, depends_on, due, lease_until, handoff` plus history lines.

## Key decisions

1. **The vault is the database. Every index is disposable.** The search index, citation
   counts and link graph are rebuilt from the markdown on demand. Losing them loses
   nothing. Deleting a markdown file is the only way to lose knowledge, so nothing
   deletes: decay and merges *move* notes to `Archive/`.

2. **Memory writes are ordinary runtime writes, with no side door.** The memory store
   serializes a note, then calls `Runtime.write`, which runs gate → scope guard →
   budget → feed exactly as before. Namespaces are plain `write_scope` globs
   (`Memory/Researcher/**`). On top of that, the runtime gains **protected paths**
   (`Memory/Shared/**`, `Agents/*.md`): writing one needs an *approved, unused*
   proposal matching that exact path and content hash, even for an agent whose scope
   covers it. This only adds a check; nothing existing is relaxed.

3. **Provenance and confidence form a lattice, not a free-for-all.** A note with no
   `source` is rejected. Confidence ranks `stated > observed > inferred`, and a write
   may never replace a note with a lower-confidence version. So an agent's inference
   can't overwrite what a human stated, and the refusal is logged as BLOCKED.

4. **Retrieval is BM25 + one-hop links + recency, behind a `Retriever` interface.**
   Keyword scoring over title/tags/body (title and tags weighted up) covers what a
   small operation's vault needs, deterministically and with the standard library
   only. Notes linked from a hit join at a discount, and a recency half-life favours
   what's current. Every result cites `note id + line`. Embeddings can be plugged in
   behind the same interface later; none ship.

5. **Coordination is a single-writer state machine with leases.** Agents never edit
   `Tasks/` directly. They ask the board for transitions (claim, start, hand off,
   release), the board checks the rules, and the Captain's charter is the one
   persisting it, through the runtime. A claim is a lease with an expiry, so a crashed
   agent's work comes back, and two agents can't hold the same lease. That's tested
   with a fake clock, not with sleeps. This is honestly single-process; a
   multi-process deployment puts the board behind one work-queue service.

6. **Compounding is human-gated and deterministic.** Consolidation archives stale,
   never-cited episodes; merges duplicate learnings (normalized-token Jaccard ≥ 0.8,
   oldest wins, citations follow); proposes promoting any learning cited ≥ N times
   into its owner's charter; and flags two active decisions on the same `subject` with
   different `stance`. Charter changes always need a human. Shared-memory changes need
   a human or the designated reviewer, never the proposer. The same vault always
   produces the same report.

Messages, inbox captures and anything tagged external are **data, never
instructions**. They pass through `quarantine()` on the way in, show up in context packs
as quoted `[EXTERNAL DATA …]` blocks, and a flagged message can never become a task.

## A run, end to end

`claim task → build_context(agent, task, budget) [handoffs first, then cited hits,
quarantined where external] → model.act (deterministic mock by default; any model
behind the same interface: an OpenAI-compatible endpoint, a CLI, or Anthropic, chosen
by LLM_PROVIDER) → write episode +
learning → write handoff → retro (worked / failed / next) → release`. Every step is a
feed line.

## Out of scope

Embeddings, multi-process locking, a UI, and real model calls in tests or the demo.
Example data is fictional and generic: a made-up small bakery-supply business.
