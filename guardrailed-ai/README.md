# Guard-railed AI

Service line: intelligent automation inside a regulated workflow.

The implementation is `src/fracture/ai`. This page is the rule an engagement
follows. The tests that keep the rule true are `tests/test_ai_boundary.py`.

## What a model may do

- Propose a field mapping during a fold-in.
- Extract text from a document the client sent.
- Classify a row.
- Write the narrative around a pack.
- Triage an anomaly in words.

Every one of those lands as a row in `ai.proposal`, with the model name, a
hash of the prompt, and the inputs that were in front of the model. The prompt
itself is not stored: it may contain client data, and the audit question is
whether it was the same prompt.

## What a model may not do

Compute a number with financial consequence. Amounts, rates, hours, balances
and splits are in that set. Hours are included because an hour is multiplied
by a rate and invoiced.

Three checks have to agree, and removing any one of them fails the assurance
gate:

1. Nothing an AI produced enters except as a proposal.
2. A database trigger refuses a numeric canonical column populated from an
   unconfirmed proposal.
3. The canonical writer refuses the same write, earlier, with a clearer error.

A service account cannot confirm its own proposal. Confirmation is a person.

`extraction` is the only proposal kind that may carry a value destined for a
numeric column, and only after that confirmation. Every other kind reaching a
numeric column is a defect even when someone has confirmed it.

## What the engagement lead does

Cite the proposal id in the working papers. Do not paste the model's
arithmetic into the pack. If a figure is wrong, the correction is a source
record or a confirmed extraction, and the pack is reissued at a new system
time. The previous pack still reproduces.
