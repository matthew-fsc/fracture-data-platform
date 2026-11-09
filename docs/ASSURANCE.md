# The assurance gate

The platform enforces an AI boundary over **data**: no model computes a figure
with financial consequence, held three ways that must agree — an `ai.proposal`
table, a database trigger on `lineage.ai_edge`, and the canonical writer.

That boundary says nothing about **code**. "Agent-assisted engineering with
human decision gates and continuous automated audit" is a claim about code, and
until this existed there was nothing behind it. A client's security review asks
about the second one.

## What it is

`fracture assure` runs nine policies over the repository tree and produces a
content-hashed report. Each policy is a pure function from a tree snapshot to a
list of violations — pure because the report has to be reproducible, or it is a
log line rather than evidence.

A policy earns its place only when it is **mechanical** (no judgement call),
**cheap** (runs on every commit without anyone noticing) and **load-bearing**
(its violation has already caused a wrong number, or would end an engagement).
Style preferences do not qualify; a linter does that job and nobody needs a
signed report about import order.

| | Policy | Why it is mechanical rather than reviewed |
| --- | --- | --- |
| P1 | Adapters cannot write to a source system | The promise has to survive a convenience method added at 11pm, so it is checked on the adapter's own AST |
| P2 | The chassis does not import a pack | One static import collapses the two layers and nothing else notices until the next engagement |
| P3 | Packs do not import each other | Two packs sharing a table is one badly named pack plus an unsequenceable migration |
| P4 | Mart models bind the pack's system time | A mart that reads the wall clock still renders; it is just not reproducible |
| P5 | No credential or issuable tax identifier in the tree | A key in git is a notifiable event, not a cleanup task |
| P6 | Every adapter ships its admission evidence | An adapter without empty, typical and pathological fixtures was tested against the one extract the engineer happened to have |
| P7 | Every Python file parses | A file that does not parse is a file no other policy inspected |
| P8 | The data-side AI boundary is intact in all three places | The redundancy is the point: any one site passes the happy path alone, so removing one is invisible to the suite |
| P9 | Published orderings are total | A tie across a limit changes which rows are published on some data and not other data |

### P5 and synthetic identifiers

The tax-identifier rule flags a value that is shaped like a US SSN **and in a
range the SSA actually issues**. Areas 000, 666 and 900–999 have never been
issued, so a value there cannot belong to a real person and is the correct
choice for a fixture.

Flagging only the plausible ones means the policy stays switched on inside
`tests/`, which is exactly where a pasted-in client extract would land. The
repository's own fixtures were moved into the 900 range when this policy was
written.

### P9, and why it exists

P9 was written because of a defect it then found, and the defect is the most
expensive thing in this repository's history to have diagnosed.

A pack section ordered `by abs(variance_amount) desc limit 12` has ties. Two
households at the cut-off could swap between builds, so the pack stopped
reissuing byte-identically — on roughly one synthetic estate in four, which is
the rate at which somebody stops believing the test and starts re-running it.

The same class was latent in fifteen `distinct on` orderings whose final term
was `recorded_at`. That column defaults to `now()`, which is one value per
statement, so **every row of a batch insert ties on it**. The pick was arbitrary
among the tied rows. All of them now carry `canon_id` as a final tiebreaker.

The check is a heuristic: the last ordering term must discriminate within the
group. A primary key always does; an id column does unless it is one of the
group keys, where it is constant and useless; a timestamp does not. It accepts
some non-total orderings and rejects some total ones, and is still worth having,
because the failure it prevents is silent and the remedy is one clause.

## The report

Two hashes, both needed:

- **`tree_hash`** covers the inspected source, path-ordered. Only the files the
  policies actually read, so an unrelated asset changing does not invalidate a
  report.
- **`content_hash`** covers the findings, canonically ordered by policy, path,
  line and detail — plus the policy set itself. That last part matters: a clean
  run against a shrunken policy set must not hash the same as a clean run
  against the full one, or the report cannot tell "we fixed it" from "we
  stopped checking", which is the only question an auditor is actually asking.

An unchanged tree with a changed report means the policy set moved, which is
the interesting case and the one a single hash hides.

## The change gate

A passing CI job proves the tree was clean. It does not prove a person decided
to ship it, and that difference is the entire content of "human decision
gates". So a gate run becomes a row in `control.change_gate`: the revision,
both hashes, the verdict, who wrote the change, whether it was an agent, and
which human approved it.

The rules are database constraints, not branches in the recorder:

- `agent_change_needs_human_approval` — an agent-authored change needs a named
  human approver.
- `approver_is_not_the_author` — an agent cannot approve its own work. The same
  rule as the data side, where a service account cannot confirm its own
  proposal.
- `failed_gate_is_not_approved` — recording a failing gate is expected and
  useful; marking it approved is impossible.
- `approval_is_complete` — the approver and the timestamp arrive together.

Enforced there rather than in `gate.py` because a rule that lives in one code
path lasts until somebody writes the second one.

```
fracture assure --record --authored-by claude-opus-5 --author-kind agent \
                --approved-by matthew@fracturesystems.com --summary "pack split"
fracture gates
fracture approve <gate-id> --approved-by matthew@fracturesystems.com
```

## Proving the gate can fail

A gate that cannot fail is theatre. `tests/test_assurance.py` plants a
violation of every policy and asserts it is caught, and plants correct code
that each policy must not flag — a policy that fires on correct code gets
switched off, which is the same outcome as not having it.

That suite immediately found a bug in the gate itself: the wall-clock pattern
never matched `now()`, because a word boundary after `)` sits between two
non-word characters and never matches. `current_date` matched, which is why it
went unnoticed. The corrected pattern caught `current_date` in the services
lockup mart on its first run.

## What is not covered

The gate reads the tree. It does not know what the code does, and it is not a
substitute for review or for the test suite — it is the floor beneath both.

Specifically out of scope today, and listed in `OPEN_DECISIONS.md`: no policy
checks that a new mart model has an assertion, that a dashboard view is
reachable from a department, or that a canonical column added to a pack's DDL
was added to its entity registry. Each is mechanical and none is written yet.
